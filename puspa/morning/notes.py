"""Notes to actions (P-07, plan section 12.8).

A voice message, meeting, lecture, or study note (``pengajian``)
becomes a summary plus a list of actions with an owner. The
extraction is deterministic and rule-based — no model call, no
guessing:

- A line is an action when it starts with a bullet, contains an
  Indonesian action verb (``tolong``, ``harus``, ``bayar``,
  ``hubungi``, ...), says ``todo``, or names an owner with ``@``.
  Anything else is context for the summary, not an action.
- The owner comes from an ``@Name`` mention or an explicit
  ``PJ:`` / ``penanggung jawab:`` line; without one the action is
  ``unassigned`` and the screen says so, instead of inventing an
  owner.
- The due hint is a date word printed in the line (``besok``,
  ``Senin``, ``12 Desember``); it is kept as the printed words,
  never parsed into a date the note did not state.

Every action is stored as a tracked task in the ``tasks`` table
(the P-02 task store), so it shows up on the Tasks screen and in
the weekly report; the note itself (summary + actions with their
task ids) is stored in ``morning_notes`` (migration 0025).

The transcript behind a note comes from the P-03 voice pipeline
(transcriber still a stub — that honest limitation carries over:
a voice note's transcript is only as real as the configured
transcriber), or from text the user pastes. Nothing here claims
an ASR engine exists.
"""

from __future__ import annotations

import json
import re

import psycopg

from ..memory.schema import resolve_dsn
from ..runtime.agents import ensure_puspa_agent
from ..runtime.tasks import create_task_id

__all__ = [
    "NOTE_SOURCES",
    "create_schema",
    "extract_actions",
    "get_note",
    "list_notes",
    "save_note",
    "summarise_note_text",
]

#: Where a note came from. ``voice`` is a transcribed voice
#: message; ``text`` is pasted text; the rest name the meeting
#: kind the plan lists (rapat, kuliah, pengajian).
NOTE_SOURCES = ("voice", "text", "meeting", "lecture", "study")

#: Verbs (word-boundary matched) that mark a line as an action.
ACTION_VERBS = (
    "tolong",
    "harus",
    "perlu",
    "wajib",
    "jangan lupa",
    "cek",
    "periksa",
    "hubungi",
    "telepon",
    "bayar",
    "kirim",
    "kirimkan",
    "buat",
    "buatkan",
    "siapkan",
    "jadwalkan",
    "ingatkan",
    "beli",
    "belikan",
    "urus",
    "selesaikan",
    "kerjakan",
    "catat",
    "undang",
    "tindaklanjuti",
    "follow up",
    "bahas",
    "putuskan",
    "umumkan",
)

#: ``@Name`` mentions (one or two words) name the owner.
_OWNER_MENTION_RE = re.compile(r"@([A-Za-z][\w]*(?:\s+[A-Za-z][\w]*)?)")

#: ``PJ: Name`` / ``penanggung jawab: Name`` names the owner.
_OWNER_PREFIX_RE = re.compile(
    r"(?:^|\b)(?:pj|penanggung\s+jawab|owner)\s*:\s*([A-Za-z][\w]*(?:\s+[A-Za-z][\w]*)?)",
    re.IGNORECASE,
)

#: Date words kept verbatim as the due hint (plus a ``12 Desember``
#: style date, handled by _DUE_DATE_RE).
_DUE_WORDS = (
    "besok",
    "lusa",
    "hari ini",
    "minggu depan",
    "bulan depan",
    "akhir bulan",
    "awal bulan",
    "senin",
    "selasa",
    "rabu",
    "kamis",
    "jumat",
    "sabtu",
    "minggu",
)
_DUE_WORD_RE = re.compile(r"\b(" + "|".join(_DUE_WORDS) + r")\b", re.IGNORECASE)
_INDONESIAN_MONTHS = (
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
)
_DUE_DATE_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(_INDONESIAN_MONTHS) + r")(?:\s+(\d{4}))?",
    re.IGNORECASE,
)

#: ``tanggal 12`` / ``tgl 12`` as a weaker due hint.
_DUE_DAY_RE = re.compile(r"\bt(?:an)?ggal\s+(\d{1,2})\b", re.IGNORECASE)

#: Bullet prefixes stripped from an action's text.
_BULLET_RE = re.compile(r"^\s*(?:[-•*]\s+|\d+[.)]\s+)")

#: Sentence split for prose notes (keeps the line number of the
#: line the sentence came from).
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

#: Longest summary the note keeps (word-boundary truncated).
SUMMARY_MAX_CHARS = 280


def _find_owner(line: str) -> str | None:
    match = _OWNER_PREFIX_RE.search(line)
    if match is not None:
        return _strip_trailing_verbs(match.group(1).strip())
    match = _OWNER_MENTION_RE.search(line)
    if match is not None:
        return _strip_trailing_verbs(match.group(1).strip())
    return None


def _strip_trailing_verbs(name: str) -> str:
    """Drop action verbs a greedy two-word name match swallowed.

    ``@Budi tolong ...`` names Budi, not "Budi tolong": the mention
    regex takes up to two words, so a verb right after the name
    gets eaten. A real two-word name (``Budi Santoso``) has no verb
    at the end, so it survives untouched.
    """
    words = name.split()
    while words:
        last_two = " ".join(words[-2:]).lower() if len(words) >= 2 else ""
        if last_two in ACTION_VERBS:
            words = words[:-2]
        elif words[-1].lower() in ACTION_VERBS:
            words = words[:-1]
        else:
            break
    return " ".join(words) or name


def _find_due_hint(line: str) -> str | None:
    match = _DUE_DATE_RE.search(line)
    if match is not None:
        day, month, year = match.groups()
        hint = f"{int(day)} {month.capitalize()}"
        return f"{hint} {year}" if year else hint
    match = _DUE_DAY_RE.search(line)
    if match is not None:
        return f"tanggal {int(match.group(1))}"
    match = _DUE_WORD_RE.search(line)
    if match is not None:
        return match.group(1).lower()
    return None


def _is_action(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _BULLET_RE.match(stripped):
        return True
    lowered = f" {stripped.lower()} "
    if "todo" in lowered:
        return True
    if _OWNER_MENTION_RE.search(stripped) or _OWNER_PREFIX_RE.search(stripped):
        return True
    return any(f" {verb} " in lowered or lowered.startswith(f"{verb} ") for verb in ACTION_VERBS)


def _split_statements(text: str) -> list[tuple[int, str]]:
    """(line number, statement) pairs, splitting prose into sentences."""
    statements: list[tuple[int, str]] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for raw_piece in _SENTENCE_RE.split(line.strip()):
            piece = raw_piece.strip()
            if piece:
                statements.append((line_no, piece))
    return statements


def extract_actions(text: str, *, default_owner: str | None = None) -> list[dict]:
    """Action items from a note's text, in the order they appear.

    Each action is ``{text, owner, due_hint, source_line}`` where
    ``owner`` is a name or None (unassigned — never invented) and
    ``due_hint`` is the date words printed in the line or None.
    The action text keeps the ``@Name`` mention (it is information);
    only the bullet prefix is stripped.
    """
    actions = []
    for line_no, statement in _split_statements(text or ""):
        if not _is_action(statement):
            continue
        clean = _BULLET_RE.sub("", statement).strip()
        if not clean:
            continue
        actions.append(
            {
                "text": clean,
                "owner": _find_owner(statement) or default_owner,
                "due_hint": _find_due_hint(statement),
                "source_line": line_no,
            }
        )
    return actions


def summarise_note_text(text: str, *, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """A short summary: the first two statements, word-truncated.

    Deterministic extractive summary — the note's own words, never
    a paraphrase a model invented. Returns "" for an empty note.
    """
    statements = [statement for _, statement in _split_statements(text or "")]
    if not statements:
        return ""
    summary = " ".join(statements[:2])
    if len(summary) <= max_chars:
        return summary
    cut = summary.rfind(" ", 0, max_chars)
    return summary[: cut if cut > 0 else max_chars].rstrip() + "…"


def create_schema(dsn=None):
    """Create the ``morning_notes`` table by applying migration 0025
    through the migration runner (table DDL lives only in migrations
    — see tests/test_migrations.py::test_no_table_ddl_in_python_code).
    Idempotent; safe to call before every test.
    """
    from puspa.migrations import apply_migrations

    apply_migrations(resolve_dsn(dsn), target="0025")


def save_note(
    user_id: str,
    *,
    source: str,
    title: str = "",
    raw_text: str,
    dsn=None,
) -> dict:
    """Extract a note's actions and store the note + its tasks.

    Returns ``{note_id, summary, actions}`` where each action
    carries its ``task_id`` in the ``tasks`` table. Raises
    ``ValueError`` for an unknown source or empty text.
    """
    if source not in NOTE_SOURCES:
        raise ValueError(f"unknown note source {source!r}; expected one of {NOTE_SOURCES}")
    if not (raw_text or "").strip():
        raise ValueError("note text must not be empty")
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    actions = extract_actions(raw_text)
    summary = summarise_note_text(raw_text)
    agent_id = ensure_puspa_agent(user_id, dsn=dsn)
    stored_actions = []
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        with conn.cursor() as cur:
            for action in actions:
                task_id = create_task_id()
                cur.execute(
                    "INSERT INTO tasks (id, user_id, agent_id, purpose, status) "
                    "VALUES (%s, %s, %s, %s, 'accepted')",
                    (task_id, user_id, agent_id, action["text"]),
                )
                stored_actions.append({**action, "task_id": task_id})
            cur.execute(
                "INSERT INTO morning_notes (user_id, source, title, raw_text, summary, actions) "
                "VALUES (%s, %s, %s, %s, %s, %s::jsonb) RETURNING id",
                (
                    user_id,
                    source,
                    title.strip(),
                    raw_text,
                    summary,
                    json.dumps(stored_actions),
                ),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("morning note insert returned no id")
            note_id = str(row[0])
        conn.commit()
    return {"note_id": note_id, "summary": summary, "actions": stored_actions}


def list_notes(user_id: str, *, dsn=None, limit: int = 20) -> list[dict]:
    """The user's notes, newest first (actions embedded)."""
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        rows = conn.execute(
            "SELECT id, source, title, summary, actions, created_at FROM morning_notes "
            "WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, max(1, limit)),
        ).fetchall()
    return [
        {
            "note_id": str(row[0]),
            "source": row[1],
            "title": row[2],
            "summary": row[3],
            "actions": row[4] if isinstance(row[4], list) else json.loads(row[4]),
            "created_at": row[5].isoformat() if row[5] is not None else None,
        }
        for row in rows
    ]


def get_note(user_id: str, note_id: str, *, dsn=None) -> dict | None:
    """One note with its full text, or None when missing/foreign."""
    if not user_id or not note_id:
        return None
    with psycopg.connect(resolve_dsn(dsn)) as conn:
        row = conn.execute(
            "SELECT id, source, title, raw_text, summary, actions, created_at "
            "FROM morning_notes WHERE id = %s AND user_id = %s",
            (note_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return {
        "note_id": str(row[0]),
        "source": row[1],
        "title": row[2],
        "raw_text": row[3],
        "summary": row[4],
        "actions": row[5] if isinstance(row[5], list) else json.loads(row[5]),
        "created_at": row[6].isoformat() if row[6] is not None else None,
    }
