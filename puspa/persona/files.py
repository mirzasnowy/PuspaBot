"""User-editable persona files (V2-25, issue #133).

Like Muse's SOUL.md: the user owns three plain Markdown files in
the Puspa home, edits them with any text editor, and the agent
reads them fresh on every turn. The files live in the user's data
directory, never in the repo; the repo holds only this loader, the
defaults below, and ``docs/persona-files.md``.

The three files, one concern each:

- ``SOUL.md`` -- the agent's persona and vibe: how it talks, what
  tone it uses, what it never does.
- ``IDENTITY.md`` -- the agent's name and identity: who it is, what
  it calls itself. A ``Name: ...`` line sets the name the agent
  introduces itself with.
- ``USER.md`` -- the user's own profile and preferences: what to
  call them, what they care about, how they like answers.

Reading rules (the turn must never crash on these files):

- A missing file falls back to its default, silently: a fresh
  home simply behaves like the default agent until the user edits.
- A file that cannot be read (not valid UTF-8, a directory in
  place of the file, over the size cap) also falls back to its
  default, but the fallback is reported: the warning is appended
  to ``PersonaFiles.warnings`` and handed to the optional ``warn``
  callback, so the turn path can record it as a ledger warning.
- An empty file is a deliberate blank: that section renders as
  nothing, and no warning is raised.
- Nothing is cached. ``load_persona_files`` reads the files from
  disk on every call, so an edit takes effect on the next turn.

The loader never touches the database and never raises for a
bad file: every read failure is caught inside ``_read_one`` and
reported as a warning instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..sessions.home import resolve_home

__all__ = [
    "DEFAULT_IDENTITY",
    "DEFAULT_NAME",
    "DEFAULT_SOUL",
    "DEFAULT_USER",
    "MAX_FILE_BYTES",
    "PERSONA_FILES",
    "PersonaFiles",
    "ensure_persona_files",
    "load_persona_files",
    "persona_file_paths",
    "render_persona_block",
]

#: The agent's name when no file says otherwise.
DEFAULT_NAME = "Puspa"

#: Files larger than this are treated as malformed: a persona file
#: is prompt text, and an unbounded file would bloat every turn.
MAX_FILE_BYTES = 64 * 1024

#: The three user-editable files, in render order.
PERSONA_FILES = ("SOUL.md", "IDENTITY.md", "USER.md")

#: Default persona: warm, plain, and to the point.
DEFAULT_SOUL = """# Soul

You are Puspa, a personal AI agent. Be warm, direct, and plain:
short sentences, everyday words, no jargon unless the user asks
for it. Say what you did and what is left, with real counts,
never a guessed percentage. If you do not know something, say so
instead of guessing.
"""

#: Default identity. The ``Name:`` line is what the agent calls
#: itself; change it to rename the agent.
DEFAULT_IDENTITY = """# Identity

Name: Puspa

You are Puspa, a personal AI agent the user runs themselves.
"""

#: Default user profile: a template the user fills in.
DEFAULT_USER = """# About the user

- Name: (fill in what to call you)
- Language: (which language you prefer for answers)
- Notes: (anything the agent should always remember about you)
"""

_DEFAULTS = {
    "SOUL.md": DEFAULT_SOUL,
    "IDENTITY.md": DEFAULT_IDENTITY,
    "USER.md": DEFAULT_USER,
}

#: A ``Name:`` line, case-insensitive, anywhere in IDENTITY.md.
_NAME_RE = re.compile(r"(?im)^name:\s*(.+?)\s*$")

#: First level-1 Markdown heading, used when no ``Name:`` line exists.
_HEADING_RE = re.compile(r"(?m)^#\s+(.+?)\s*$")


@dataclass
class PersonaFiles:
    """One fresh read of the three persona files.

    ``soul``, ``identity``, and ``user`` hold the resolved text for
    each file: the file's content, or its default when the file is
    missing or malformed. ``sources`` says which it was per file
    (``"file"`` or ``"default"``). ``warnings`` lists every
    fallback caused by a malformed file, so the caller can surface
    them; a missing file is not a warning.
    """

    soul: str
    identity: str
    user: str
    name: str
    home: Path
    warnings: list[str] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)


def persona_file_paths(home=None) -> dict[str, Path]:
    """Resolved path of each persona file under the Puspa home."""
    root = resolve_home(home=home)
    return {name: root / name for name in PERSONA_FILES}


def _parse_name(identity_text: str) -> str:
    """The agent's name from IDENTITY.md, or the default.

    A ``Name:`` line wins; otherwise the first level-1 heading;
    otherwise :data:`DEFAULT_NAME`. The value is plain text, so a
    heading like ``# Bimo`` names the agent Bimo.
    """
    match = _NAME_RE.search(identity_text)
    if match:
        name = match.group(1).strip().strip("\"'`")
        if name:
            return name
    match = _HEADING_RE.search(identity_text)
    if match:
        name = match.group(1).strip()
        if name and name.lower() != "identity":
            return name
    return DEFAULT_NAME


def _read_one(path: Path, default: str, *, warn=None) -> tuple[str, str]:
    """Read one persona file, falling back to its default.

    Returns ``(text, source)``. A missing file is a silent
    fallback to the default. An unreadable, non-UTF-8, or
    oversize file falls back too, and the reason is reported
    through ``warn`` when one is given.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return default, "default"
    if size > MAX_FILE_BYTES:
        message = (
            f"{path.name} is {size} bytes, over the "
            f"{MAX_FILE_BYTES} byte cap; using the default instead."
        )
        if warn is not None:
            warn(message)
        return default, "default"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        message = f"{path.name} could not be read ({exc}); using the default instead."
        if warn is not None:
            warn(message)
        return default, "default"
    return text, "file"


def load_persona_files(home=None, *, warn=None) -> PersonaFiles:
    """Read the three persona files fresh from disk.

    Call this on every turn: there is no cache, so an edit to
    any file is visible to the very next turn. ``warn`` is an
    optional callback receiving one warning string per malformed
    file; the loader itself never raises for a bad file.
    """
    root = resolve_home(home=home)
    paths = persona_file_paths(home=root)
    warnings: list[str] = []
    texts: dict[str, str] = {}
    sources: dict[str, str] = {}
    for name in PERSONA_FILES:
        text, source = _read_one(paths[name], _DEFAULTS[name], warn=warnings.append)
        texts[name] = text
        sources[name] = source
    if warn is not None:
        for warning in list(warnings):
            warn(warning)
    return PersonaFiles(
        soul=texts["SOUL.md"],
        identity=texts["IDENTITY.md"],
        user=texts["USER.md"],
        name=_parse_name(texts["IDENTITY.md"]),
        home=root,
        warnings=warnings,
        sources=sources,
    )


def render_persona_block(persona: PersonaFiles) -> str:
    """Render the persona files as one block of prompt text.

    Each non-empty file becomes a labeled section, in file order.
    An all-empty read renders as ``""`` (no block to inject).
    The block is plain text: the model reads it as instructions
    from the user, not as tool output or retrieved data.
    """
    sections = []
    if persona.soul.strip():
        sections.append(f"# Agent persona\n\n{persona.soul.strip()}")
    if persona.identity.strip():
        sections.append(f"# Agent identity\n\n{persona.identity.strip()}")
    if persona.user.strip():
        sections.append(f"# About the user\n\n{persona.user.strip()}")
    return "\n\n".join(sections)


def ensure_persona_files(home=None) -> list[Path]:
    """Create the persona files with defaults where they are missing.

    Only missing files are written; a file the user already edited
    is never overwritten. Returns the paths that were created.
    A path that exists as a directory is left alone: the loader
    reports it as malformed on the next read instead.
    """
    root = resolve_home(home=home)
    root.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for name in PERSONA_FILES:
        path = root / name
        if path.exists():
            continue
        path.write_text(_DEFAULTS[name], encoding="utf-8")
        created.append(path)
    return created
