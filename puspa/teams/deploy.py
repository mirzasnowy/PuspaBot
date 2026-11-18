"""Offline validation for the multi-tenant deploy files.

Pattern (hermes-agent ``Dockerfile`` + ``docker-compose.yml``,
rewritten as Puspa files): multi-stage image, s6-overlay as PID 1,
dashboard bound to 127.0.0.1 by default, remote access only through
an SSH tunnel or an authenticated reverse proxy, UID/GID remap so
home files stay owned by the host user, API server off unless its
key is uncommented.

Docker containers cannot run on this VM (the kernel rejects setns),
so a live ``docker build`` / ``docker compose up`` is impossible
here. This module is the validation that IS possible here: it parses
the Dockerfile (stages, instructions, entrypoint) and the compose
file (services, port bindings, environment) with pure Python and
reports every violation of the deploy rules as a plain string.
``validate_dockerfile`` / ``validate_compose`` return an empty list
when the file is clean — the tests assert exactly that for the
shipped files, plus negative cases on synthetic files. A live deploy
validation on a VPS is still required (see ``docs/teams.md``);
passing these checks proves the files are well-formed and follow
the rules, not that the image builds or runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "DockerfileInfo",
    "DockerfileStage",
    "parse_dockerfile",
    "validate_compose",
    "validate_dockerfile",
]

#: Dockerfile instructions the parser recognises (the rest is an error).
_KNOWN_INSTRUCTIONS = {
    "FROM",
    "RUN",
    "CMD",
    "LABEL",
    "EXPOSE",
    "ENV",
    "ADD",
    "COPY",
    "ENTRYPOINT",
    "VOLUME",
    "USER",
    "WORKDIR",
    "ARG",
    "ONBUILD",
    "STOPSIGNAL",
    "HEALTHCHECK",
    "SHELL",
}

#: The s6-overlay entrypoint: PID 1 must be /init.
_S6_ENTRYPOINT = "/init"

#: Dashboard port (the PWA app default, see puspa/pwa/app.py run()).
_DASHBOARD_PORT = "8000"


@dataclass
class DockerfileStage:
    """One FROM stage: base image + optional AS name."""

    base: str
    name: str | None = None


@dataclass
class DockerfileInfo:
    """Parsed Dockerfile: stages, instructions, structural errors."""

    stages: list[DockerfileStage] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _join_continuations(text: str) -> list[str]:
    """Join backslash-continued lines; drop blank lines, comments,
    and heredoc bodies (a ``RUN <<EOF`` block's body is script text,
    not Dockerfile instructions — ``mkdir``/``cat``/``chmod`` inside
    it must never be read as instructions)."""
    logical: list[str] = []
    current = ""
    heredoc_end: str | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if heredoc_end is not None:
            if line.strip() == heredoc_end:
                heredoc_end = None
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.endswith("\\"):
            current += line[:-1] + " "
            continue
        current += line
        match = re.search(r"<<-?['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?", current)
        if match:
            heredoc_end = match.group(1)
        logical.append(current.strip())
        current = ""
    if current.strip():
        logical.append(current.strip())
    return logical


def parse_dockerfile(text: str) -> DockerfileInfo:
    """Parse Dockerfile text into stages + instructions + errors.

    Structural errors only (no FROM at all, an instruction before
    the first FROM, an unknown instruction). Semantic rules live in
    ``validate_dockerfile``.
    """
    info = DockerfileInfo()
    for line in _join_continuations(text):
        parts = line.split(None, 1)
        instruction = parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""
        if instruction not in _KNOWN_INSTRUCTIONS:
            info.errors.append(f"unknown Dockerfile instruction: {instruction}")
            continue
        info.instructions.append(instruction)
        if instruction == "FROM":
            match = re.match(r"(\S+)(?:\s+AS\s+(\S+))?$", rest.strip(), re.IGNORECASE)
            if match is None:
                info.errors.append(f"malformed FROM line: {line!r}")
                continue
            info.stages.append(DockerfileStage(base=match.group(1), name=match.group(2)))
        elif not info.stages and instruction != "ARG":
            # ARG is the only instruction allowed before the first
            # FROM (it declares build args the FROM line may use).
            info.errors.append(f"instruction {instruction} appears before the first FROM")
    if not info.stages:
        info.errors.append("Dockerfile has no FROM stage")
    return info


def validate_dockerfile(path: str | Path) -> list[str]:
    """Validate the shipped Dockerfile against the deploy rules.

    Returns a list of issue strings (empty = clean):

    - parses without structural errors;
    - multi-stage (at least two FROM stages);
    - s6-overlay present (an ADD of an s6-overlay tarball) and
      ``/init`` as the ENTRYPOINT (s6-overlay as PID 1);
    - the app never runs as root: a ``USER`` directive is set, or
      the service script drops privileges with ``s6-setuidgid``
      (s6-overlay's /init itself must start as root, so a Dockerfile
      ``USER`` directive would break it — the drop happens in the
      service script instead);
    - UID/GID remap build args exist (``PUSPA_UID`` / ``PUSPA_GID``);
    - the dashboard host defaults to 127.0.0.1 (never 0.0.0.0);
    - no API key is baked into the image (no ``ENV PUSPA_API_KEY=``
      with a value);
    - a HEALTHCHECK exists (the liveness probe).
    """
    path = Path(path)
    if not path.exists():
        return [f"Dockerfile not found: {path}"]
    text = path.read_text(encoding="utf-8")
    info = parse_dockerfile(text)
    issues = list(info.errors)
    if len(info.stages) < 2:
        issues.append(f"Dockerfile must be multi-stage (found {len(info.stages)} stage(s))")
    if "s6-overlay" not in text:
        issues.append("Dockerfile does not install s6-overlay")
    entrypoints = [
        line for line in _join_continuations(text) if line.upper().startswith("ENTRYPOINT")
    ]
    if not any(_S6_ENTRYPOINT in line for line in entrypoints):
        issues.append("ENTRYPOINT must be /init (s6-overlay as PID 1)")
    if "USER" not in info.instructions and "s6-setuidgid" not in text:
        issues.append(
            "app must not run as root: set USER or drop privileges "
            "with s6-setuidgid in the service script"
        )
    if "PUSPA_UID" not in text or "PUSPA_GID" not in text:
        issues.append("Dockerfile must define PUSPA_UID / PUSPA_GID build args (UID/GID remap)")
    host_match = re.search(r"PUSPA_DASHBOARD_HOST\s*=\s*(\S+)", text)
    if host_match is None:
        issues.append("Dockerfile must set PUSPA_DASHBOARD_HOST (default 127.0.0.1)")
    elif host_match.group(1).strip("\"'") != "127.0.0.1":
        issues.append(f"dashboard must bind 127.0.0.1 by default, found {host_match.group(1)}")
    if re.search(r"ENV\s+PUSPA_API_KEY\s*=\s*\S+", text):
        issues.append("Dockerfile must not bake an API key into the image")
    if "HEALTHCHECK" not in info.instructions:
        issues.append("Dockerfile must define a HEALTHCHECK (liveness probe)")
    return issues


def validate_compose(path: str | Path) -> list[str]:
    """Validate the shipped docker-compose.yml against the deploy rules.

    Returns a list of issue strings (empty = clean):

    - valid YAML with ``services``;
    - ``gateway`` and ``dashboard`` services both exist;
    - every published port binds 127.0.0.1 (never 0.0.0.0 or a bare
      host port);
    - no service sets ``PUSPA_API_KEY`` to a value (the API server
      stays off unless the key is uncommented in the file);
    - the home volume is mounted (so home files survive restarts);
    - UID/GID build args are passed through (``PUSPA_UID`` /
      ``PUSPA_GID``).
    """
    path = Path(path)
    if not path.exists():
        return [f"docker-compose.yml not found: {path}"]
    try:
        import yaml
    except ImportError:
        return ["PyYAML is not installed; cannot validate docker-compose.yml"]
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # yaml.YAMLError and friends
        return [f"docker-compose.yml is not valid YAML: {exc}"]
    issues: list[str] = []
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        return ["docker-compose.yml has no services mapping"]
    services = data["services"]
    for required in ("gateway", "dashboard"):
        if required not in services:
            issues.append(f"docker-compose.yml is missing the {required!r} service")
    text = path.read_text(encoding="utf-8")
    for name, service in services.items():
        if not isinstance(service, dict):
            continue
        for port in service.get("ports") or []:
            binding = str(port)
            if binding.startswith("127.0.0.1:"):
                continue
            issues.append(
                f"service {name!r} publishes {binding!r}: "
                "ports must bind 127.0.0.1 (remote access only via SSH tunnel "
                "or an authenticated reverse proxy)"
            )
        env = service.get("environment") or {}
        if isinstance(env, dict) and env.get("PUSPA_API_KEY"):
            issues.append(
                f"service {name!r} sets PUSPA_API_KEY: the API server must stay "
                "off unless the key is deliberately uncommented"
            )
        if isinstance(env, list) and any(
            str(item).startswith("PUSPA_API_KEY=") and not str(item).endswith("=") for item in env
        ):
            issues.append(
                f"service {name!r} sets PUSPA_API_KEY: the API server must stay "
                "off unless the key is deliberately uncommented"
            )
    if "puspa-home" not in text and "puspa_home" not in text:
        issues.append("docker-compose.yml does not mount a puspa-home volume")
    if "PUSPA_UID" not in text or "PUSPA_GID" not in text:
        issues.append("docker-compose.yml must pass PUSPA_UID / PUSPA_GID build args")
    return issues
