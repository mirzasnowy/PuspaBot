"""Authoring standards for skills, with tests (G3).

Pattern rewritten from hermes-agent
``tests/skills/test_authoring_standards.py`` (MIT, adapted, not
copied): a skill that breaks the standards fails the check, so bad
authoring is caught before a skill reaches the marketplace.

Standards enforced by :func:`check_authoring`:

- ``description_too_long``: frontmatter ``description`` over 60
  characters.
- ``shell_util_reference``: prose refers to a shell utility instead
  of the native tool (``grep`` -> ``search_files``,
  ``cat`` -> ``read_file``, ``ls`` -> ``list_files``,
  ``sed`` -> ``edit_file``, ``awk`` -> ``search_files``,
  ``curl`` -> ``fetch_url``). Only prose is scanned; fenced code
  blocks are exempt, so a script may still show a shell example
  inside a code fence.
- ``platform_gating_unaudited``: the text gates on the platform
  (``sys.platform``, ``platform.system()``, ``os.name``) without
  frontmatter ``platform_gating_audited: true``.
- ``human_credit_missing``: no ``author`` in the frontmatter, or no
  credit line (``Author:`` / ``Credit:`` / ``By:``) in the body.
- ``section_order``: present sections must appear in the canonical
  order — Description, When to use, Tools, Configuration,
  Examples. Missing sections are fine; out-of-order ones fail.
- ``inline_logic_too_long``: a fenced code block over 10 lines —
  non-trivial logic belongs in a script file, not inline.
- ``config_undeclared``: the body uses a ``{{config.name}}``
  placeholder that the frontmatter ``config`` list does not declare.

Config declared in frontmatter is asked at setup time and injected
at load time:

- :func:`resolve_config` takes the declared list and either an
  ``answers`` dict or an ``asker`` callable
  (``asker(name, prompt, default) -> str``); required entries with
  no answer raise ``ValueError``.
- :func:`inject_config` substitutes ``{{config.name}}``
  placeholders in the skill text with the resolved values.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "CANONICAL_SECTIONS",
    "MAX_DESCRIPTION_CHARS",
    "MAX_INLINE_CODE_LINES",
    "SHELL_UTIL_TO_NATIVE_TOOL",
    "AuthoringViolation",
    "check_authoring",
    "inject_config",
    "resolve_config",
]

#: Frontmatter description limit, characters.
MAX_DESCRIPTION_CHARS = 60
#: Longest fenced code block allowed inline, lines.
MAX_INLINE_CODE_LINES = 10

#: Shell utilities and the native tool to use instead.
SHELL_UTIL_TO_NATIVE_TOOL = {
    "grep": "search_files",
    "cat": "read_file",
    "ls": "list_files",
    "sed": "edit_file",
    "awk": "search_files",
    "curl": "fetch_url",
}

#: Canonical section order (by heading text, case-insensitive).
CANONICAL_SECTIONS = (
    "description",
    "when to use",
    "tools",
    "configuration",
    "examples",
)

_PLATFORM_GATING = re.compile(r"sys\.platform|platform\.system\(\)|os\.name")
_CONFIG_PLACEHOLDER = re.compile(r"\{\{\s*config\.([A-Za-z0-9_]+)\s*\}\}")
_CREDIT_LINE = re.compile(r"^(author|credit|by)\s*:", re.IGNORECASE | re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_FENCE = re.compile(r"^```.*$", re.MULTILINE)


@dataclass
class AuthoringViolation:
    """One broken authoring standard."""

    code: str
    message: str
    detail: str = ""

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "detail": self.detail}


def _strip_code_fences(text: str) -> str:
    """Remove fenced code blocks (prose-only scan)."""
    out = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(line)
    return "\n".join(out)


def _code_blocks(text: str) -> list[list[str]]:
    """Fenced code blocks as lists of lines."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if current is None:
                current = []
            else:
                blocks.append(current)
                current = None
            continue
        if current is not None:
            current.append(line)
    return blocks


def check_authoring(skill_text: str, frontmatter: dict | None = None):
    """Check a skill against the authoring standards.

    Returns a list of :class:`AuthoringViolation` (empty = clean).
    ``frontmatter`` is the parsed ``SKILL.md`` frontmatter dict.
    """
    frontmatter = frontmatter or {}
    violations: list[AuthoringViolation] = []
    prose = _strip_code_fences(skill_text)

    # Description length (frontmatter).
    description = frontmatter.get("description", "")
    if isinstance(description, str) and len(description) > MAX_DESCRIPTION_CHARS:
        violations.append(
            AuthoringViolation(
                code="description_too_long",
                message=f"description is {len(description)} characters "
                f"(max {MAX_DESCRIPTION_CHARS})",
                detail=description,
            )
        )

    # Shell utilities in prose -> native tools.
    for util, native in SHELL_UTIL_TO_NATIVE_TOOL.items():
        if re.search(rf"\b{re.escape(util)}\b", prose):
            violations.append(
                AuthoringViolation(
                    code="shell_util_reference",
                    message=f"refers to shell utility {util!r}; "
                    f"use the native tool {native!r} instead",
                    detail=util,
                )
            )

    # Platform gating without an audit flag.
    if (
        _PLATFORM_GATING.search(skill_text)
        and frontmatter.get("platform_gating_audited") is not True
    ):
        violations.append(
            AuthoringViolation(
                code="platform_gating_unaudited",
                message="platform gating is present but not marked audited "
                "(set frontmatter platform_gating_audited: true "
                "after review)",
            )
        )

    # Human credit first: author in frontmatter + a credit line.
    if not frontmatter.get("author"):
        violations.append(
            AuthoringViolation(
                code="human_credit_missing",
                message="frontmatter 'author' is missing; credit the human author",
            )
        )
    elif not _CREDIT_LINE.search(prose):
        violations.append(
            AuthoringViolation(
                code="human_credit_missing",
                message="no credit line (Author:/Credit:/By:) in the skill body",
            )
        )

    # Canonical section order.
    headings = [h.strip().lower() for h in _HEADING.findall(prose)]
    positions = []
    for section in CANONICAL_SECTIONS:
        for i, heading in enumerate(headings):
            if heading == section or heading.startswith(section + " "):
                positions.append(i)
                break
    if positions != sorted(positions):
        violations.append(
            AuthoringViolation(
                code="section_order",
                message="sections are out of the canonical order: "
                + " > ".join(CANONICAL_SECTIONS),
                detail=str(headings),
            )
        )

    # Non-trivial logic inline instead of in a script.
    for block in _code_blocks(skill_text):
        if len(block) > MAX_INLINE_CODE_LINES:
            violations.append(
                AuthoringViolation(
                    code="inline_logic_too_long",
                    message=f"inline code block has {len(block)} lines "
                    f"(max {MAX_INLINE_CODE_LINES}); move non-trivial "
                    "logic to a script file",
                    detail=f"{len(block)} lines",
                )
            )
            break

    # Config placeholders must be declared in frontmatter.
    declared = {
        entry.get("name") for entry in frontmatter.get("config") or [] if isinstance(entry, dict)
    }
    for name in sorted(set(_CONFIG_PLACEHOLDER.findall(skill_text))):
        if name not in declared:
            violations.append(
                AuthoringViolation(
                    code="config_undeclared",
                    message=f"placeholder {{{{config.{name}}}}} is used but "
                    "not declared in frontmatter 'config'",
                    detail=name,
                )
            )

    return violations


def resolve_config(declared: list | None, *, answers: dict | None = None, asker=None) -> dict:
    """Resolve setup-time config for a skill.

    ``declared`` is the frontmatter ``config`` list of
    ``{"name", "prompt", "required", "default"}``. Values come from
    ``answers`` first, then the ``asker`` callable
    (``asker(name, prompt, default)``), then the declared default.
    A required entry with no value raises ``ValueError``.
    """
    resolved: dict = {}
    answers = answers or {}
    for entry in declared or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        name = entry["name"]
        if name in answers and answers[name] not in (None, ""):
            resolved[name] = answers[name]
            continue
        if asker is not None:
            value = asker(name, entry.get("prompt", name), entry.get("default"))
            if value not in (None, ""):
                resolved[name] = value
                continue
        if "default" in entry and entry["default"] is not None:
            resolved[name] = entry["default"]
            continue
        if entry.get("required"):
            raise ValueError(f"required config {name!r} was not provided at setup")
    return resolved


def inject_config(skill_text: str, config: dict) -> str:
    """Substitute ``{{config.name}}`` placeholders with resolved values.

    Placeholders with no resolved value are left untouched (the
    authoring check flags them as undeclared instead of this
    function failing the load).
    """

    def _replace(match):
        name = match.group(1)
        return str(config[name]) if name in config else match.group(0)

    return _CONFIG_PLACEHOLDER.sub(_replace, skill_text)
