"""Skill folder + manifest validation for the marketplace (G3).

Pattern rewritten from DuskBot ``server/skills.ts`` + ``skill-library.ts``
(PolyForm Noncommercial, study only, not copied): one skill is one
folder holding ``SKILL.md`` (``---`` frontmatter required at the very
start) plus a strictly validated ``manifest.json``.

Manifest rules (all enforced here, fail-closed):

- ``id`` must match the folder name and the pattern
  ``^[a-z0-9]+(-[a-z0-9]+)*$``.
- ``version`` must be semver.
- ``name`` / ``description`` must be non-empty strings.
- ``triggerTerms`` must be a non-empty list of strings.
- ``requiredCapabilities`` must be a list of strings (may be empty).
- ``tools`` must be a list of strings (may be empty).
- ``license`` must be a non-empty string; ``source`` a non-empty
  string; ``priceCredits`` an integer >= 0.
- ``origin`` is one of ``builtin`` / ``user`` / ``library``.

The validated manifest also carries the audit fields DuskBot stores:
``sha256`` (of ``SKILL.md``), ``importedAt``, ``compatibility``,
``warnings``, ``skippedFiles`` and ``referencesSha256`` (sha256 of
every file the skill references, so a later change is detectable).

Limits (DuskBot parity): at most 30 skills in the index, the index
JSON at most 4,000 bytes, at most 20 staged skills. The limits live
here so the registry and the tests share one definition.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import yaml

from .errors import ManifestError

__all__ = [
    "KNOWN_LICENSES",
    "MAX_INDEX_BYTES",
    "MAX_SKILLS",
    "MAX_STAGED",
    "SEMVER_PATTERN",
    "SKILL_ID_PATTERN",
    "SkillManifest",
    "parse_frontmatter",
    "sha256_bytes",
    "sha256_file",
    "validate_manifest",
    "validate_skill_folder",
]

#: Maximum skills in the marketplace index.
MAX_SKILLS = 30
#: Maximum serialised index size, bytes.
MAX_INDEX_BYTES = 4000
#: Maximum staged (imported but not yet installed) skills.
MAX_STAGED = 20

SKILL_ID_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

#: Licences the marketplace recognises. Anything else is stored
#: verbatim but scores zero on the licence trust signal and is
#: reported as a warning, never silently accepted as known.
KNOWN_LICENSES = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "MPL-2.0",
        "CC0-1.0",
        "PolyForm-Noncommercial-1.0.0",
    }
)

#: Skill origins. Builtin skills ship with Puspa, user skills are
#: authored by the user, library skills come from the marketplace.
ORIGINS = frozenset({"builtin", "user", "library"})

#: Frontmatter keys a skill may declare for setup-time config.
#: Each entry is ``{"name": ..., "prompt": ..., "required": bool,
#: "default": ...}``.
CONFIG_ENTRY_KEYS = frozenset({"name", "prompt", "required", "default"})


def sha256_bytes(data: bytes) -> str:
    """Hex sha256 of bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    """Hex sha256 of a file's bytes."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_frontmatter(text: str):
    """Split ``SKILL.md`` text into (frontmatter dict, body str).

    The frontmatter must start with ``---`` on the very first line
    and end with a ``---`` line; anything else raises
    :class:`ManifestError`. The frontmatter is YAML and must decode
    to a mapping.
    """
    if not text.startswith("---\n") and not text.startswith("---\r\n"):
        raise ManifestError("SKILL.md must start with '---' frontmatter")
    lines = text.splitlines()
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        raise ManifestError("SKILL.md frontmatter is not closed with '---'")
    raw = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ManifestError(f"SKILL.md frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ManifestError("SKILL.md frontmatter must be a mapping")
    body = "\n".join(lines[end + 1 :])
    return data, body


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _problems_for(data: dict, *, folder_name: str | None) -> list[str]:
    """Collect every manifest problem (empty = valid)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]
    skill_id = data.get("id")
    if not isinstance(skill_id, str) or not SKILL_ID_PATTERN.match(skill_id):
        problems.append("manifest 'id' must match ^[a-z0-9]+(-[a-z0-9]+)*$")
    elif folder_name is not None and skill_id != folder_name:
        problems.append(f"manifest 'id' {skill_id!r} does not match folder name {folder_name!r}")
    version = data.get("version")
    if not isinstance(version, str) or not SEMVER_PATTERN.match(version):
        problems.append("manifest 'version' must be semver (e.g. '1.2.3')")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        problems.append("manifest 'name' must be a non-empty string")
    if not isinstance(data.get("description"), str) or not data["description"].strip():
        problems.append("manifest 'description' must be a non-empty string")
    triggers = data.get("triggerTerms")
    if not _is_str_list(triggers) or not triggers:
        problems.append("manifest 'triggerTerms' must be a non-empty list of strings")
    caps = data.get("requiredCapabilities")
    if caps is not None and not _is_str_list(caps):
        problems.append("manifest 'requiredCapabilities' must be a list of strings")
    tools = data.get("tools")
    if tools is not None and not _is_str_list(tools):
        problems.append("manifest 'tools' must be a list of strings")
    if not isinstance(data.get("license"), str) or not data["license"].strip():
        problems.append("manifest 'license' must be a non-empty string")
    if not isinstance(data.get("source"), str) or not data["source"].strip():
        problems.append("manifest 'source' must be a non-empty string")
    price = data.get("priceCredits", 0)
    if not isinstance(price, int) or isinstance(price, bool) or price < 0:
        problems.append("manifest 'priceCredits' must be an integer >= 0")
    origin = data.get("origin", "library")
    if origin not in ORIGINS:
        problems.append(f"manifest 'origin' must be one of {sorted(ORIGINS)}")
    compat = data.get("compatibility")
    if compat is not None and not isinstance(compat, dict):
        problems.append("manifest 'compatibility' must be an object")
    for key in ("warnings", "skippedFiles"):
        if data.get(key) is not None and not _is_str_list(data[key]):
            problems.append(f"manifest '{key}' must be a list of strings")
    refs = data.get("referencesSha256")
    if refs is not None and (
        not isinstance(refs, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in refs.items())
    ):
        problems.append("manifest 'referencesSha256' must be an object of string to string")
    config = data.get("config")
    if config is not None:
        if not isinstance(config, list):
            problems.append("manifest 'config' must be a list")
        else:
            for entry in config:
                if not isinstance(entry, dict) or not entry.get("name"):
                    problems.append("manifest 'config' entries must be objects with 'name'")
                    break
                extra = set(entry) - CONFIG_ENTRY_KEYS
                if extra:
                    problems.append(
                        f"manifest 'config' entry {entry.get('name')!r} has "
                        f"unknown keys: {sorted(extra)}"
                    )
                    break
    return problems


class SkillManifest:
    """A validated skill manifest (plain data, JSON-serialisable)."""

    def __init__(self, data: dict):
        self.data = dict(data)

    def __getitem__(self, key):
        return self.data[key]

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def skill_id(self) -> str:
        return self.data["id"]

    @property
    def version(self) -> str:
        return self.data["version"]

    @property
    def origin(self) -> str:
        return self.data.get("origin", "library")

    @property
    def price_credits(self) -> int:
        return int(self.data.get("priceCredits", 0))

    @property
    def license(self) -> str:
        return self.data.get("license", "")

    def to_dict(self) -> dict:
        """Serialisable copy."""
        return dict(self.data)

    def index_entry(self) -> dict:
        """The small entry the registry index stores for this skill.

        Deliberately compact — sha256, source and licence live in
        the store/manifest, not the index, so 30 skills still fit
        the 4,000-byte index cap.
        """
        return {
            "id": self.data["id"],
            "version": self.data["version"],
            "origin": self.origin,
            "priceCredits": self.price_credits,
        }


def validate_manifest(data: dict, *, folder_name: str | None = None):
    """Validate a manifest dict.

    Returns ``(SkillManifest, problems, warnings)``. ``problems``
    empty means valid. ``warnings`` are non-fatal (unknown licence,
    missing optional fields). Raises nothing.
    """
    problems = _problems_for(data, folder_name=folder_name)
    warnings: list[str] = []
    if not problems:
        if data.get("license") not in KNOWN_LICENSES:
            warnings.append(f"unknown licence {data.get('license')!r}")
        if not data.get("triggerTerms"):
            warnings.append("no trigger terms")
    if problems:
        return None, problems, warnings
    normalised = dict(data)
    normalised.setdefault("requiredCapabilities", [])
    normalised.setdefault("tools", [])
    normalised.setdefault("compatibility", {})
    normalised.setdefault("warnings", [])
    normalised.setdefault("skippedFiles", [])
    normalised.setdefault("referencesSha256", {})
    normalised.setdefault("priceCredits", 0)
    normalised.setdefault("origin", "library")
    normalised["warnings"] = list(normalised["warnings"]) + warnings
    return SkillManifest(normalised), [], warnings


def validate_skill_folder(path, *, origin: str | None = None):
    """Validate one skill folder on disk.

    Returns ``(SkillManifest, problems, frontmatter, body)``. The
    manifest's ``sha256`` is the sha256 of ``SKILL.md`` as stored,
    ``importedAt`` is stamped now, and ``referencesSha256`` is
    recomputed for every file named in the frontmatter ``references``
    list (a missing or changed file is a problem, not a warning).
    Only ``SKILL.md`` (and the manifest itself) is read from the
    folder at import time; other files are hashed, never executed.
    """
    folder = Path(path)
    folder_name = folder.name
    skill_md = folder / "SKILL.md"
    if not skill_md.is_file():
        return None, [f"SKILL.md not found in {folder}"], {}, ""
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"SKILL.md cannot be read: {exc}"], {}, ""
    try:
        frontmatter, body = parse_frontmatter(raw)
    except ManifestError as exc:
        return None, [str(exc)], {}, ""
    manifest_path = folder / "manifest.json"
    if not manifest_path.is_file():
        return None, ["manifest.json not found in skill folder"], frontmatter, body
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return None, [f"manifest.json cannot be read: {exc}"], frontmatter, body
    if not isinstance(data, dict):
        return None, ["manifest.json must be a JSON object"], frontmatter, body
    problems = _problems_for(data, folder_name=folder_name)
    if problems:
        return None, problems, frontmatter, body
    # Audit fields: sha256 of SKILL.md, import stamp, reference hashes.
    data = dict(data)
    data["sha256"] = sha256_bytes(raw.encode("utf-8"))
    data["importedAt"] = datetime.now(UTC).isoformat()
    if origin is not None:
        data["origin"] = origin
    refs = {}
    ref_problems = []
    for name in frontmatter.get("references") or []:
        if not isinstance(name, str) or not name or name.startswith(("/", "..")):
            ref_problems.append(f"reference {name!r} is not a plain relative path")
            continue
        ref_path = folder / name
        if not ref_path.is_file():
            ref_problems.append(f"referenced file {name!r} is missing")
            continue
        refs[name] = sha256_file(ref_path)
    if ref_problems:
        return None, ref_problems, frontmatter, body
    data["referencesSha256"] = refs
    manifest, _, _warnings = validate_manifest(data, folder_name=folder_name)
    return manifest, [], frontmatter, body
