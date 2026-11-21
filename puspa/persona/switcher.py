"""Multiple switchable personas, built on the persona files (V2-25).

The single-persona files (``SOUL.md``, ``IDENTITY.md``, ``USER.md`` in
the Puspa home; see ``puspa.persona.files``) stay exactly as they are:
they are the ``"default"`` persona, and a home that never created a
second persona behaves exactly as before. A second persona is the
same three files in its own folder, plus an optional ``meta.json``
holding its reply style:

- ``<home>/personas/<slug>/SOUL.md`` — the prompt: how the agent
  talks, what tone it uses.
- ``<home>/personas/<slug>/IDENTITY.md`` — the agent's name (the
  ``Name:`` line, same parser as the default persona) and identity.
- ``<home>/personas/<slug>/USER.md`` — what to call the user and
  what the agent should know about them in this role.
- ``<home>/personas/<slug>/meta.json`` — ``{"style": "santai"}``,
  one of the speaking styles from ``puspa.persona.persona``; it
  renders as a ``# Reply style`` section in the prompt block.
- ``<home>/personas/active.json`` — ``{"active": "<slug>"}``; missing
  or pointing at a deleted persona means the default persona.

The turn loop reads the active persona fresh on every turn through
:func:`load_active_persona_files`, so switching personas takes
effect on the next turn with no restart and no cache to clear. The
old persona's files stay on disk untouched: switching back restores
it exactly.

Pattern only (never copied code): AstrBot's persona settings +
persona manager — one persona per role, an explicit active choice,
per-persona prompt and style.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
import shutil
from pathlib import Path

from ..sessions.home import resolve_home
from .files import (
    DEFAULT_IDENTITY,
    DEFAULT_SOUL,
    DEFAULT_USER,
    PersonaFiles,
    load_persona_files,
)
from .persona import STYLES

__all__ = [
    "DEFAULT_SLUG",
    "create_persona",
    "delete_persona",
    "get_active_slug",
    "get_persona",
    "list_personas",
    "load_active_persona_files",
    "persona_dir",
    "set_active_persona",
    "slugify_name",
    "update_persona",
]

#: Slug of the root-files persona: the home's own SOUL.md /
#: IDENTITY.md / USER.md, i.e. what V2-25 already provides.
DEFAULT_SLUG = "default"

#: Folder under the home holding every extra persona + active.json.
PERSONAS_DIR_NAME = "personas"

#: File holding the active persona: {"active": "<slug>"}.
ACTIVE_FILE_NAME = "active.json"

#: Per-persona metadata file: {"style": "<one of STYLES>"}.
META_FILE_NAME = "meta.json"

#: Persona slugs: start alphanumeric, then letters, digits, "-" or "_".
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: A ``Name:`` line in IDENTITY.md, same rule as ``persona.files``.
_NAME_LINE_RE = re.compile(r"(?im)^name:\s*.*$")

#: First Markdown heading, used to place a missing ``Name:`` line.
_HEADING_RE = re.compile(r"(?m)^#\s+.*$")


def slugify_name(name: str) -> str:
    """Turn a persona name into its folder slug.

    Lowercase, runs of anything but letters/digits become one ``-``,
    leading/trailing dashes stripped. ``"Kerja Harian"`` becomes
    ``"kerja-harian"``. Raises :class:`ValueError` when nothing usable
    is left, or when the name would claim the reserved ``"default"``
    slug.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("nama persona harus string tak kosong")
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise ValueError(f"nama {name!r} tidak menghasilkan slug yang valid")
    if slug == DEFAULT_SLUG:
        raise ValueError(f'nama {name!r} bentrok dengan persona bawaan ("default")')
    if not _SLUG_RE.match(slug):
        raise ValueError(f"slug tidak valid: {slug!r}")
    return slug


def _validate_slug(slug: str) -> str:
    """A slug the caller passes in: valid shape, or ``"default"``."""
    if slug == DEFAULT_SLUG:
        return slug
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise ValueError(f"slug persona tidak valid: {slug!r}")
    return slug


def _validate_style(style: str | None) -> str | None:
    if style is None:
        return None
    if style not in STYLES:
        raise ValueError(f"gaya tidak dikenal: {style!r} (pilih: {sorted(STYLES)})")
    return style


def personas_root(home=None) -> Path:
    """The ``<home>/personas`` folder (created on first write)."""
    return resolve_home(home=home) / PERSONAS_DIR_NAME


def persona_dir(home, slug: str) -> Path:
    """Folder of one extra persona (``"default"`` lives at the root)."""
    return personas_root(home) / _validate_slug(slug)


def _active_path(home=None) -> Path:
    return personas_root(home) / ACTIVE_FILE_NAME


def get_active_slug(home=None) -> str:
    """Slug of the active persona; ``"default"`` when none is set.

    A missing ``active.json``, an unreadable one, or one pointing at
    a persona that no longer exists all fall back to the default —
    switching must never break the next turn.
    """
    try:
        data = json.loads(_active_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return DEFAULT_SLUG
    slug = data.get("active") if isinstance(data, dict) else None
    if slug == DEFAULT_SLUG:
        return DEFAULT_SLUG
    if isinstance(slug, str) and _SLUG_RE.match(slug) and persona_dir(home, slug).is_dir():
        return slug
    return DEFAULT_SLUG


def set_active_persona(home, slug: str) -> str:
    """Make ``slug`` the active persona (``"default"`` = root files).

    Raises :class:`KeyError` for an unknown slug. The write goes
    through a temp file + rename, so a crash mid-write never leaves
    a half-written ``active.json`` behind.
    """
    slug = _validate_slug(slug)
    if slug != DEFAULT_SLUG and not persona_dir(home, slug).is_dir():
        raise KeyError(f"persona tidak dikenal: {slug!r}")
    root = personas_root(home)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{ACTIVE_FILE_NAME}.tmp"
    tmp.write_text(json.dumps({"active": slug}), encoding="utf-8")
    tmp.replace(_active_path(home))
    return slug


def _read_meta(directory: Path) -> dict:
    """A persona folder's ``meta.json``; ``{}`` when missing/broken."""
    try:
        data = json.loads((directory / META_FILE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_meta(directory: Path, style: str | None) -> None:
    if style is None:
        return
    (directory / META_FILE_NAME).write_text(json.dumps({"style": style}), encoding="utf-8")


def _identity_with_name(text: str, name: str) -> str:
    """``text`` with its ``Name:`` line set to ``name``.

    Replaces the existing line when there is one; otherwise inserts
    it after the first heading, or prepends it when there is no
    heading at all. The rest of the file is left untouched.
    """
    line = f"Name: {name}"
    if _NAME_LINE_RE.search(text):
        return _NAME_LINE_RE.sub(line, text, count=1)
    match = _HEADING_RE.search(text)
    if match:
        end = match.end()
        return text[:end] + f"\n\n{line}" + text[end:]
    return f"{line}\n\n{text}"


def _record(home, slug: str, *, active: str) -> dict:
    """Full record of one persona: files read fresh from disk."""
    if slug == DEFAULT_SLUG:
        files = load_persona_files(home=home)
        return {
            "slug": DEFAULT_SLUG,
            "name": files.name,
            "soul": files.soul,
            "identity": files.identity,
            "user": files.user,
            "style": None,
            "is_active": active == DEFAULT_SLUG,
            "is_default": True,
        }
    directory = persona_dir(home, slug)
    files = load_persona_files(home=directory)
    style = _read_meta(directory).get("style")
    if style not in STYLES:
        style = None
    return {
        "slug": slug,
        "name": files.name,
        "soul": files.soul,
        "identity": files.identity,
        "user": files.user,
        "style": style,
        "is_active": active == slug,
        "is_default": False,
    }


def list_personas(home=None) -> list[dict]:
    """Every persona: the default first, then the rest by name.

    Entries carry ``slug``, ``name``, ``style``, ``is_active`` and
    ``is_default`` (no file text — use :func:`get_persona` for that).
    """
    active = get_active_slug(home)
    default = _record(home, DEFAULT_SLUG, active=active)
    found = []
    root = personas_root(home)
    if root.is_dir():
        for directory in root.iterdir():
            if directory.is_dir() and _SLUG_RE.match(directory.name):
                found.append(_record(home, directory.name, active=active))
    found.sort(key=lambda item: item["name"].casefold())
    return [
        {
            "slug": default["slug"],
            "name": default["name"],
            "style": default["style"],
            "is_active": default["is_active"],
            "is_default": True,
        },
        *[
            {
                "slug": item["slug"],
                "name": item["name"],
                "style": item["style"],
                "is_active": item["is_active"],
                "is_default": False,
            }
            for item in found
        ],
    ]


def get_persona(home, slug: str) -> dict:
    """Full record of one persona, file text included.

    Raises :class:`KeyError` for an unknown slug.
    """
    slug = _validate_slug(slug)
    if slug != DEFAULT_SLUG and not persona_dir(home, slug).is_dir():
        raise KeyError(f"persona tidak dikenal: {slug!r}")
    return _record(home, slug, active=get_active_slug(home))


def create_persona(
    home,
    name: str,
    *,
    soul: str | None = None,
    identity: str | None = None,
    user: str | None = None,
    style: str | None = None,
) -> dict:
    """Create a persona from its name, prompt, and reply style.

    The folder slug comes from the name (``"Kerja"`` -> ``"kerja"``);
    a name whose slug already exists raises :class:`ValueError`.
    Omitted file text starts from the defaults, with the ``Name:``
    line set to the new name. Returns the full record.
    """
    style = _validate_style(style)
    slug = slugify_name(name)
    directory = persona_dir(home, slug)
    if directory.exists():
        raise ValueError(f"persona sudah ada: {slug!r}")
    directory.mkdir(parents=True)
    identity_text = _identity_with_name(
        identity if identity is not None else DEFAULT_IDENTITY, name.strip()
    )
    (directory / "SOUL.md").write_text(soul if soul is not None else DEFAULT_SOUL, encoding="utf-8")
    (directory / "IDENTITY.md").write_text(identity_text, encoding="utf-8")
    (directory / "USER.md").write_text(user if user is not None else DEFAULT_USER, encoding="utf-8")
    _write_meta(directory, style)
    return _record(home, slug, active=get_active_slug(home))


def update_persona(
    home,
    slug: str,
    *,
    name: str | None = None,
    soul: str | None = None,
    identity: str | None = None,
    user: str | None = None,
    style: str | None = None,
) -> dict:
    """Edit a persona; only the given fields change.

    A new ``name`` rewrites the ``Name:`` line of IDENTITY.md (the
    folder slug never moves: it is the stable id other records
    point at). ``style`` must be one of the known speaking styles.
    Raises :class:`KeyError` for an unknown slug. Returns the full
    record. The default persona can be edited the same way — it
    writes the root files.
    """
    style = _validate_style(style)
    slug = _validate_slug(slug)
    directory = resolve_home(home=home) if slug == DEFAULT_SLUG else persona_dir(home, slug)
    if slug != DEFAULT_SLUG and not directory.is_dir():
        raise KeyError(f"persona tidak dikenal: {slug!r}")
    if identity is not None:
        text = identity
        if name is not None:
            text = _identity_with_name(text, name.strip())
        (directory / "IDENTITY.md").write_text(text, encoding="utf-8")
    elif name is not None:
        current = (
            (directory / "IDENTITY.md").read_text(encoding="utf-8")
            if (directory / "IDENTITY.md").exists()
            else DEFAULT_IDENTITY
        )
        (directory / "IDENTITY.md").write_text(
            _identity_with_name(current, name.strip()), encoding="utf-8"
        )
    if soul is not None:
        (directory / "SOUL.md").write_text(soul, encoding="utf-8")
    if user is not None:
        (directory / "USER.md").write_text(user, encoding="utf-8")
    if style is not None:
        _write_meta(directory, style)
    return _record(home, slug, active=get_active_slug(home))


def delete_persona(home, slug: str) -> str:
    """Delete a persona's folder; the default cannot be deleted.

    Deleting the active persona falls back to the default on the
    next turn (the ``active.json`` pointer is removed). Returns the
    deleted slug. Raises :class:`ValueError` for the default and
    :class:`KeyError` for an unknown slug.
    """
    slug = _validate_slug(slug)
    if slug == DEFAULT_SLUG:
        raise ValueError("persona bawaan tidak bisa dihapus")
    directory = persona_dir(home, slug)
    if not directory.is_dir():
        raise KeyError(f"persona tidak dikenal: {slug!r}")
    if get_active_slug(home) == slug:
        with contextlib.suppress(OSError):
            _active_path(home).unlink()
    shutil.rmtree(directory)
    return slug


def load_active_persona_files(home=None, *, warn=None) -> PersonaFiles:
    """The active persona's files, read fresh from disk.

    The default persona loads from the home root; any other active
    persona loads from its folder, with its reply style attached
    (``PersonaFiles.style``) so the prompt block carries it. The
    same fallbacks as :func:`puspa.persona.files.load_persona_files`
    apply: a missing or malformed file falls back to its default.
    """
    slug = get_active_slug(home)
    if slug == DEFAULT_SLUG:
        return load_persona_files(home=home, warn=warn)
    directory = persona_dir(home, slug)
    files = load_persona_files(home=directory, warn=warn)
    style = _read_meta(directory).get("style")
    if style not in STYLES:
        return files
    return dataclasses.replace(files, style=style)
