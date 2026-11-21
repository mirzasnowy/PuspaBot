"""Durable MEMORY.md: the human-readable long-term memory store.

The brain keeps five memory layers (working, episodic, semantic,
procedural, people). This module adds the durable layer on top of
them: one plain-language MEMORY.md file per user, rebuilt from the
lower layers, that the user can read and edit.

What it does, in plain words:

- ``collect_snapshot`` reads the lower layers for one user: the
  active profile facts (including user corrections and
  commitments), the episodic rows (the per-user ledger), the
  consolidation summaries, and the knowledge graph built from
  those episodes. Nothing is invented: every line in the file
  traces to a fact, an episode, a summary, or a graph node.
- ``render_memory_md`` turns a snapshot into the Markdown file,
  with sections for standing goals, commitments, corrections,
  preferences, facts, people, lessons, and a narrative journal.
  The file is capped at the shared narrative limits (200 lines /
  24,000 bytes, see ``puspa/memory/limits.py``).
- ``rebuild_memory`` runs the nightly rebuild: collect, render,
  and store the new document. The version number only grows when
  the text actually changes, so an unchanged rebuild is a no-op.
- ``apply_user_edits`` reads an edited MEMORY.md back. A user
  edit beats anything the agent inferred: changing a line records
  a user-confirmed fact that supersedes the old value, deleting
  a line expires the fact, and an edited guess replaces the
  unconfirmed inferred fact (the guess is rejected, never left
  standing next to the correction).
- The narrative journal (``build_daily_entry`` /
  ``append_journal_entry``) is the day-by-day story: one short
  plain-language entry per day, written from that day's summaries
  and episodes. The newest entries are rendered into the Journal
  section of MEMORY.md.

What it does NOT do: write-through (one ``commit_turn`` call that
writes the ledger and durable memory in the same turn) is a
separate item and lives elsewhere; this module only owns the
durable store, the rebuild, the journal, and reading edits back.

Storage: ``PostgresDurableStore`` is the production store over
migration 0029 (tables ``durable_memory`` and
``narrative_journal``); ``MemoryDurableStore`` is the in-memory
store with the same interface, used by tests and by callers that
need no database. Both implement the same methods, so logic is
tested without Postgres and the Postgres path is covered by the
database tests.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta

import psycopg
from psycopg.rows import dict_row

from . import facts as facts_module
from .limits import MEMORY_MAX_BYTES, MEMORY_MAX_LINES, truncate_narrative
from .schema import resolve_dsn

#: Section headings in the rendered file, in order.
SECTIONS = (
    "Standing Goals",
    "Commitments",
    "Corrections",
    "Preferences",
    "Facts",
    "People",
    "Lessons",
    "Unconfirmed Guesses",
    "Journal",
)

#: Snapshot keys, one per section (plus the graph summary line).
SNAPSHOT_KEYS = (
    "goals",
    "commitments",
    "corrections",
    "preferences",
    "facts",
    "people",
    "lessons",
    "guesses",
    "journal",
)

#: Section heading to snapshot key.
_SECTION_TO_KEY = {
    "Standing Goals": "goals",
    "Commitments": "commitments",
    "Corrections": "corrections",
    "Preferences": "preferences",
    "Facts": "facts",
    "People": "people",
    "Lessons": "lessons",
    "Unconfirmed Guesses": "guesses",
    "Journal": "journal",
}

#: Snapshot key to section heading.
_KEY_TO_SECTION = {value: key for key, value in _SECTION_TO_KEY.items()}

#: Fact-key prefix that forces a section, checked before source.
_PREFIX_TO_KEY = {
    "goal:": "goals",
    "standing_goal": "goals",
    "commitment:": "commitments",
    "person:": "people",
    "preference:": "preferences",
    "user_preference": "preferences",
    "lesson:": "lessons",
}

#: Prefix used when a user adds a plain bullet to a fact section.
_KEY_PREFIX = {
    "goals": "goal:",
    "commitments": "commitment:",
    "corrections": "correction:",
    "preferences": "preference:",
    "facts": "fact:",
    "people": "person:",
    "lessons": "lesson:",
    "guesses": "fact:",
}

#: Sections whose bullets are keyed facts (round-tripped by key).
_FACT_SECTIONS = ("goals", "commitments", "corrections", "preferences", "facts", "people")

#: Newest journal entries rendered into the file.
JOURNAL_MAX_ENTRIES = 14

#: Episodes read for one rebuild (graph + journal input).
EPISODE_READ_LIMIT = 200

#: Summaries read for one rebuild (lesson input).
SUMMARY_READ_LIMIT = 20

#: Marker shown after an unconfirmed inferred fact.
GUESS_SUFFIX = " (inferred, not yet confirmed)"

_KEY_RE = re.compile(r"^- \*\*(.+?)\*\*: (.*)$")
_BULLET_RE = re.compile(r"^- (.*)$")
_HEADING_RE = re.compile(r"^## (.+?)\s*$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now():
    return datetime.now(UTC)


def _slug(text):
    """Short URL-safe slug of ``text`` for a derived fact key."""
    slug = _SLUG_RE.sub("-", str(text or "").lower()).strip("-")
    return slug[:60] or "item"


def _fact_text(fact):
    """Content text of a fact record, either shape."""
    return str(fact.get("content") or fact.get("value") or "")


def _content_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_fact(fact):
    """Section key for one fact record.

    An explicit key prefix wins (``goal:``, ``commitment:``,
    ``person:``, ``preference:``, ``lesson:``). Otherwise a
    user-confirmed fact is a correction: the user stated it, so
    it outranks anything inferred. Anything else is a plain fact.
    Inferred facts never reach this function as active facts;
    they are rendered separately as guesses.
    """
    key = str(fact.get("key") or "")
    lowered = key.lower()
    for prefix, section in _PREFIX_TO_KEY.items():
        if lowered.startswith(prefix):
            return section
    if fact.get("source") == "user_confirmed":
        return "corrections"
    return "facts"


def _sort_items(items):
    """Sort snapshot items by key, then text, for a stable file."""
    return sorted(items, key=lambda item: (str(item.get("key") or ""), str(item.get("text") or "")))


def render_memory_md(snapshot, *, generated_at=None):
    """Render a snapshot dict into the MEMORY.md text.

    ``snapshot`` maps section keys to lists of items. A fact item
    is ``{"key", "text", "source"}``; a lesson is ``{"text"}``; a
    journal entry is ``{"date", "text"}``. The result is capped
    at the shared narrative limits, keeping the newest lines.
    """
    generated_at = generated_at or _now()
    stamp = generated_at.strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# MEMORY.md",
        "",
        "<!-- Generated by PuspaBot from the memory ledger and the knowledge graph. -->",
        f"<!-- Last rebuilt: {stamp}. Edit this file: your edits beat anything the agent inferred. -->",
        "",
    ]
    for heading in SECTIONS:
        key = _SECTION_TO_KEY[heading]
        items = snapshot.get(key) or []
        if key == "journal":
            items = sorted(items, key=lambda item: str(item.get("date") or ""), reverse=True)[
                :JOURNAL_MAX_ENTRIES
            ]
        elif key in _FACT_SECTIONS or key == "guesses":
            items = _sort_items(items)
        lines.append(f"## {heading}")
        lines.append("")
        if not items:
            lines.append("_Nothing recorded yet._")
        else:
            for item in items:
                if key == "journal":
                    lines.append(f"- **{item.get('date') or ''}**: {item.get('text') or ''}")
                elif key == "lessons":
                    lines.append(f"- {item.get('text') or ''}")
                elif key == "guesses":
                    lines.append(
                        f"- **{item.get('key') or ''}**: {item.get('text') or ''}{GUESS_SUFFIX}"
                    )
                else:
                    lines.append(f"- **{item.get('key') or ''}**: {item.get('text') or ''}")
        if key == "people" and snapshot.get("graph_names"):
            names = ", ".join(snapshot["graph_names"])
            lines.append(f"Names from recent conversations: {names}.")
        lines.append("")
    text = "\n".join(lines).rstrip("\n") + "\n"
    return truncate_narrative(text, max_lines=MEMORY_MAX_LINES, max_bytes=MEMORY_MAX_BYTES)


def parse_memory_md(text):
    """Parse MEMORY.md text back into sections and keyed facts.

    Returns ``{"sections": {section: [bullet texts]}, "facts":
    {section: [{"key", "value"}]}}``. Bullets written as
    ``- **key**: value`` yield a keyed fact; plain bullets yield
    text only. Headings outside the known sections are kept under
    their own name in ``sections`` and ignored by the edit
    reader. The header comment lines are skipped.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    sections = {}
    facts = {}
    current = None
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or line.startswith(("<!--", "# MEMORY")):
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            current = heading.group(1)
            sections.setdefault(current, [])
            continue
        if current is None:
            continue
        keyed = _KEY_RE.match(line)
        if keyed:
            key, value = keyed.group(1).strip(), keyed.group(2).strip()
            value = value.removesuffix(GUESS_SUFFIX)
            sections[current].append(line[2:])
            facts.setdefault(current, []).append({"key": key, "value": value})
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            sections[current].append(bullet.group(1).strip())
    return {"sections": sections, "facts": facts}


def diff_memory_md(previous_text, edited_text):
    """Diff two MEMORY.md texts, keyed by (section, key).

    Returns ``{"changed": [...], "added": [...], "removed":
    [...]}`` where each entry is ``{"section", "key", "old",
    "new"}`` (``old`` is None for added, ``new`` is None for
    removed). Plain bullets without a key compare by text, with
    ``key`` None. Journal bullets are compared but never applied
    (history is not rewritten); see ``apply_user_edits``.
    """
    previous = parse_memory_md(previous_text)
    edited = parse_memory_md(edited_text)

    def keyed_map(parsed):
        out = {}
        for section, items in parsed["facts"].items():
            for item in items:
                out[(section, item["key"])] = item["value"]
        return out

    def plain_set(parsed):
        out = set()
        keyed_lines = set()
        for _section, items in parsed["facts"].items():
            for item in items:
                keyed_lines.add(f"**{item['key']}**: {item['value']}")
                keyed_lines.add(f"**{item['key']}**: {item['value']}{GUESS_SUFFIX}")
        for section, bullets in parsed["sections"].items():
            for bullet in bullets:
                if bullet not in keyed_lines:
                    out.add((section, bullet))
        return out

    old_map = keyed_map(previous)
    new_map = keyed_map(edited)
    changed, added, removed = [], [], []
    for location, old_value in sorted(old_map.items()):
        if location not in new_map:
            removed.append(
                {"section": location[0], "key": location[1], "old": old_value, "new": None}
            )
        elif new_map[location] != old_value:
            changed.append(
                {
                    "section": location[0],
                    "key": location[1],
                    "old": old_value,
                    "new": new_map[location],
                }
            )
    for location, new_value in sorted(new_map.items()):
        if location not in old_map:
            added.append(
                {"section": location[0], "key": location[1], "old": None, "new": new_value}
            )
    old_plain = plain_set(previous)
    new_plain = plain_set(edited)
    for section, bullet in sorted(old_plain - new_plain):
        removed.append({"section": section, "key": None, "old": bullet, "new": None})
    for section, bullet in sorted(new_plain - old_plain):
        added.append({"section": section, "key": None, "old": None, "new": bullet})
    return {"changed": changed, "added": added, "removed": removed}


class MemoryDurableStore:
    """In-memory durable store: same interface as the Postgres store."""

    def __init__(self):
        self._documents = {}
        self._journal = {}

    def save_document(self, user_id, content, content_hash, generated_at, source_counts):
        user_id = str(user_id)
        previous = self._documents.get(user_id)
        version = 1 if previous is None else previous["version"]
        if previous is not None and previous["content_hash"] == content_hash:
            return dict(previous)
        if previous is not None:
            version = previous["version"] + 1
        record = {
            "user_id": user_id,
            "content": content,
            "content_hash": content_hash,
            "version": version,
            "generated_at": generated_at,
            "source_counts": dict(source_counts),
        }
        self._documents[user_id] = record
        return dict(record)

    def get_document(self, user_id):
        record = self._documents.get(str(user_id))
        return dict(record) if record is not None else None

    def save_journal_entry(self, user_id, entry_date, text, source_event_ids):
        user_id = str(user_id)
        entry_date = entry_date.isoformat() if hasattr(entry_date, "isoformat") else str(entry_date)
        entry = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "entry_date": entry_date,
            "text": text,
            "source_event_ids": [str(event_id) for event_id in (source_event_ids or [])],
        }
        self._journal[(user_id, entry_date)] = entry
        return dict(entry)

    def get_journal_entry(self, user_id, entry_date):
        entry_date = entry_date.isoformat() if hasattr(entry_date, "isoformat") else str(entry_date)
        entry = self._journal.get((str(user_id), entry_date))
        return dict(entry) if entry is not None else None

    def list_journal_entries(self, user_id, limit=JOURNAL_MAX_ENTRIES):
        rows = [entry for (owner, _day), entry in self._journal.items() if owner == str(user_id)]
        rows.sort(key=lambda entry: entry["entry_date"], reverse=True)
        return [dict(entry) for entry in rows[:limit]]


class PostgresDurableStore:
    """Postgres durable store over migration 0029 tables."""

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def save_document(self, user_id, content, content_hash, generated_at, source_counts):
        import json

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content_hash, version FROM durable_memory WHERE user_id = %s",
                    (str(user_id),),
                )
                previous = cur.fetchone()
                if previous is not None and previous["content_hash"] == content_hash:
                    cur.execute("SELECT * FROM durable_memory WHERE user_id = %s", (str(user_id),))
                    return _public_document(cur.fetchone())
                version = 1 if previous is None else int(previous["version"]) + 1
                cur.execute(
                    "INSERT INTO durable_memory "
                    "(user_id, content, content_hash, version, generated_at, source_counts) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (user_id) DO UPDATE SET content = EXCLUDED.content, "
                    "content_hash = EXCLUDED.content_hash, version = EXCLUDED.version, "
                    "generated_at = EXCLUDED.generated_at, "
                    "source_counts = EXCLUDED.source_counts RETURNING *",
                    (
                        str(user_id),
                        content,
                        content_hash,
                        version,
                        generated_at,
                        json.dumps(source_counts),
                    ),
                )
                row = cur.fetchone()
            conn.commit()
        return _public_document(row)

    def get_document(self, user_id):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM durable_memory WHERE user_id = %s", (str(user_id),))
            row = cur.fetchone()
        return _public_document(row) if row is not None else None

    def save_journal_entry(self, user_id, entry_date, text, source_event_ids):
        day = entry_date.isoformat() if hasattr(entry_date, "isoformat") else str(entry_date)
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO narrative_journal "
                    "(user_id, entry_date, text, source_event_ids) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (user_id, entry_date) DO UPDATE SET text = EXCLUDED.text, "
                    "source_event_ids = EXCLUDED.source_event_ids, "
                    "updated_at = now() RETURNING *",
                    (str(user_id), day, text, [str(e) for e in (source_event_ids or [])]),
                )
                row = cur.fetchone()
            conn.commit()
        return _public_journal_entry(row)

    def get_journal_entry(self, user_id, entry_date):
        day = entry_date.isoformat() if hasattr(entry_date, "isoformat") else str(entry_date)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM narrative_journal WHERE user_id = %s AND entry_date = %s",
                (str(user_id), day),
            )
            row = cur.fetchone()
        return _public_journal_entry(row) if row is not None else None

    def list_journal_entries(self, user_id, limit=JOURNAL_MAX_ENTRIES):
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM narrative_journal WHERE user_id = %s "
                "ORDER BY entry_date DESC LIMIT %s",
                (str(user_id), limit),
            )
            return [_public_journal_entry(row) for row in cur.fetchall()]


def _public_document(row):
    import json

    counts = row["source_counts"]
    if isinstance(counts, str):
        counts = json.loads(counts)
    return {
        "user_id": str(row["user_id"]),
        "content": row["content"],
        "content_hash": row["content_hash"],
        "version": int(row["version"]),
        "generated_at": row["generated_at"],
        "source_counts": dict(counts or {}),
    }


def _public_journal_entry(row):
    day = row["entry_date"]
    if hasattr(day, "isoformat"):
        day = day.isoformat()
    return {
        "id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "entry_date": day,
        "text": row["text"],
        "source_event_ids": [str(e) for e in (row["source_event_ids"] or [])],
    }


def default_store(dsn=None):
    """Production store (Postgres)."""
    return PostgresDurableStore(dsn=dsn)


def _graph_people_and_counts(episodes):
    """Knowledge graph built from episodes: person nodes + counts.

    The graph is built in memory from the user's episodic rows
    (see ``puspa/memory/episodic.py``): each episode is one
    document, so every node traces to an episode. Returns
    ``{"people": [names], "names": [all node names], "nodes": n,
    "edges": n}``. ``people`` holds nodes the extractor typed as
    a person; ``names`` holds every node name, rendered in the
    file as an informational line, not as asserted facts.
    """
    from .episodic import episodes_to_graph

    if not episodes:
        return {"people": [], "names": [], "nodes": 0, "edges": 0}
    graph = episodes_to_graph(episodes)
    people = sorted(
        name for name, node in graph.nodes.items() if getattr(node, "kind", None) == "person"
    )
    return {
        "people": people,
        "names": sorted(graph.nodes),
        "nodes": graph.node_count,
        "edges": graph.edge_count,
    }


def collect_snapshot(
    user_id,
    *,
    dsn=None,
    fact_store=None,
    facts=None,
    summaries=None,
    episodes=None,
    journal_entries=None,
    now=None,
):
    """Collect one user's snapshot from the lower memory layers.

    Every source can be injected (lists of plain dicts) for tests;
    when a source is None it is read from Postgres with ``dsn``.
    ``facts`` are shared-shape records (see
    ``puspa/memory/facts.py``): active facts are filed into their
    section, inferred facts (status ``"proposed"``) are filed as
    unconfirmed guesses. ``summaries`` are consolidation rows;
    their text and the causal lessons they carry become Lessons.
    ``episodes`` are the per-user ledger rows; the knowledge graph
    is built from them and its person nodes become People.
    """
    now = now or _now()
    if facts is None:
        store = fact_store or facts_module.PostgresFactStore(dsn=dsn)
        facts = store.list_user(user_id, statuses=("active", "proposed"))
    if summaries is None:
        from . import consolidate

        summaries = consolidate.list_summaries(user_id, dsn=dsn)[:SUMMARY_READ_LIMIT]
    if episodes is None:
        from . import episodic

        episodes = episodic.list_episodes(user_id, limit=EPISODE_READ_LIMIT, dsn=dsn)
    if journal_entries is None:
        journal_entries = default_store(dsn=dsn).list_journal_entries(user_id)

    snapshot = {key: [] for key in SNAPSHOT_KEYS}
    for fact in facts or []:
        text = _fact_text(fact)
        if not text:
            continue
        if fact.get("status") == "proposed":
            snapshot["guesses"].append(
                {"key": str(fact.get("key") or ""), "text": text, "source": "inferred"}
            )
            continue
        if fact.get("status") != "active":
            continue
        section = classify_fact(fact)
        snapshot[section].append(
            {"key": str(fact.get("key") or ""), "text": text, "source": fact.get("source")}
        )
    graph_info = _graph_people_and_counts(episodes or [])
    known_people = {item["key"][len("person:") :] for item in snapshot["people"]}
    for name in graph_info["people"]:
        if name not in known_people:
            snapshot["people"].append({"key": f"person:{name}", "text": name, "source": "graph"})
    snapshot["graph_names"] = graph_info["names"]
    for summary in summaries or []:
        text = " ".join(str(summary.get("summary") or "").split())
        if text:
            snapshot["lessons"].append({"text": text, "source": "summary"})
    for entry in journal_entries or []:
        snapshot["journal"].append(
            {"date": str(entry.get("entry_date") or ""), "text": str(entry.get("text") or "")}
        )
    counts = {key: len(snapshot[key]) for key in SNAPSHOT_KEYS}
    counts["graph_nodes"] = graph_info["nodes"]
    counts["graph_edges"] = graph_info["edges"]
    counts["episodes"] = len(episodes or [])
    counts["summaries"] = len(summaries or [])
    return {"snapshot": snapshot, "counts": counts, "generated_at": now}


def rebuild_memory(user_id, *, dsn=None, store=None, fact_store=None, now=None, **collect_kwargs):
    """Nightly rebuild: collect, render, store the new document.

    Returns ``{"user_id", "version", "content", "content_hash",
    "generated_at", "changed", "counts"}``. ``changed`` is False
    and the version is untouched when the rebuilt text is
    identical to the stored one, so a quiet night is a no-op.
    """
    now = now or _now()
    collected = collect_snapshot(user_id, dsn=dsn, fact_store=fact_store, now=now, **collect_kwargs)
    content = render_memory_md(collected["snapshot"], generated_at=now)
    store = store or default_store(dsn=dsn)
    previous = store.get_document(user_id)
    record = store.save_document(user_id, content, _content_hash(content), now, collected["counts"])
    return {
        "user_id": str(user_id),
        "version": record["version"],
        "content": content,
        "content_hash": record["content_hash"],
        "generated_at": now,
        "changed": previous is None or previous["content_hash"] != record["content_hash"],
        "counts": collected["counts"],
    }


def read_memory(user_id, *, dsn=None, store=None):
    """Load the stored MEMORY.md document for a user, or None."""
    store = store or default_store(dsn=dsn)
    return store.get_document(user_id)


def pinned_items(snapshot):
    """Items that are never dropped from recall context.

    Corrections, commitments, and standing goals, in that order:
    what the user corrected, what they committed to, and what
    they aim at. Recall-first loads these before anything else.
    """
    pinned = []
    for key in ("corrections", "commitments", "goals"):
        for item in snapshot.get(key) or []:
            pinned.append(
                {"section": _KEY_TO_SECTION[key], "key": item.get("key"), "text": item.get("text")}
            )
    return pinned


def build_daily_entry(user_id, entry_day, *, dsn=None, summaries=None, episodes=None):
    """Build one day's narrative journal entry (deterministic).

    ``entry_day`` is a ``date``. The entry is plain-language
    extractive text from that day's summaries and completed
    episodes: one short sentence per summary, plus one line for
    the day's completed tasks. Returns ``{"text",
    "source_event_ids"}`` or None when the day has nothing to
    record. No model call: the entry is reproducible from the
    same rows, so a rebuild of the same day writes the same text.
    """
    if summaries is None:
        from . import consolidate

        summaries = consolidate.list_summaries(user_id, dsn=dsn)
    if episodes is None:
        from . import episodic

        episodes = episodic.list_episodes(user_id, limit=EPISODE_READ_LIMIT, dsn=dsn)
    day_start = datetime(entry_day.year, entry_day.month, entry_day.day, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    event_ids = []

    def in_day(stamp):
        if stamp is None:
            return False
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        return day_start <= stamp < day_end

    sentences = []
    for summary in summaries or []:
        stamp = summary.get("created_at") or summary.get("period_end")
        if not in_day(stamp):
            continue
        text = " ".join(str(summary.get("summary") or "").split())
        if text:
            sentences.append(text)
            event_ids.extend(str(e) for e in (summary.get("event_ids") or []))
    completed = []
    for episode in episodes or []:
        if episode.get("type") != "task_completed" or not in_day(episode.get("created_at")):
            continue
        content = " ".join(str(episode.get("content") or "").split())
        if content:
            completed.append(content)
            if episode.get("event_id"):
                event_ids.append(str(episode["event_id"]))
    if completed:
        shown = "; ".join(content[:160] for content in completed[:3])
        sentences.append(f"Completed: {shown}.")
    if not sentences:
        return None
    text = " ".join(sentences)
    if len(text) > 2000:
        text = text[:1997] + "..."
    return {"text": text, "source_event_ids": sorted(set(event_ids))}


def append_journal_entry(user_id, entry_day, text, *, dsn=None, store=None, source_event_ids=None):
    """Store (or replace) one day's narrative journal entry."""
    store = store or default_store(dsn=dsn)
    return store.save_journal_entry(user_id, entry_day, text, source_event_ids or [])


def _reject_proposals_for_key(user_id, key, fact_store, now):
    """Reject unconfirmed inferred facts with ``key``.

    Returns the number rejected. A user edit that touches a key
    the agent only guessed at replaces the guess: the guess must
    not survive next to the correction.
    """
    rejected = 0
    for fact in fact_store.list_for_key(user_id, key):
        if fact.get("status") == "proposed":
            fact_store.update(
                fact["id"], user_id, status="rejected", valid_until=now, updated_at=now
            )
            rejected += 1
    return rejected


def _expire_active_for_key(user_id, key, fact_store, now):
    """Expire the active fact with ``key`` (a deleted line).

    Returns True when a fact was expired. Expiry is a tombstone:
    the row stays in history, it just stops being current.
    """
    expired = False
    for fact in fact_store.list_for_key(user_id, key):
        if fact.get("status") == "active":
            fact_store.update(
                fact["id"], user_id, status="expired", valid_until=now, updated_at=now
            )
            expired = True
    return expired


def apply_user_edits(
    user_id,
    edited_text,
    *,
    previous_text,
    fact_store=None,
    journal=None,
    dsn=None,
    now=None,
):
    """Read a user-edited MEMORY.md back into the memory layers.

    ``previous_text`` is the generated file the user started
    from; ``edited_text`` is what they saved. Changed and added
    fact bullets become user-confirmed facts (source
    ``"user_confirmed"``), which supersede the old value and beat
    any inferred guess with the same key. A deleted fact bullet
    expires the fact. Added plain bullets in the Lessons section
    become ``lesson:`` facts so the next rebuild keeps them;
    deleted lesson bullets expire the matching fact. Journal
    bullets are history and are never rewritten: edits there are
    reported as ignored, not applied.

    Returns ``{"applied": [...], "expired": [...],
    "rejected_proposals": n, "ignored": [...]}``.
    """
    now = now or _now()
    fact_store = fact_store or facts_module.PostgresFactStore(dsn=dsn)
    diff = diff_memory_md(previous_text, edited_text)
    applied, expired, ignored = [], [], []
    rejected = 0
    for change in diff["changed"] + diff["added"]:
        section = change["section"]
        key = change["key"]
        value = change["new"]
        if section == "Journal":
            ignored.append(
                {"section": section, "key": key, "reason": "journal entries are history"}
            )
            continue
        if section not in _SECTION_TO_KEY:
            ignored.append({"section": section, "key": key, "reason": "unknown section"})
            continue
        snapshot_key = _SECTION_TO_KEY[section]
        if key is None:
            if snapshot_key == "lessons":
                key = f"lesson:{_slug(value)}"
            elif snapshot_key == "guesses":
                key = f"fact:{_slug(value)}"
            elif snapshot_key in _KEY_PREFIX:
                key = f"{_KEY_PREFIX[snapshot_key]}{_slug(value)}"
            else:
                ignored.append({"section": section, "key": key, "reason": "no key for this line"})
                continue
        recorded = facts_module.record_fact(
            user_id,
            key,
            value,
            source="user_confirmed",
            store=fact_store,
            journal=journal,
            now=now,
        )
        if recorded.get("stored"):
            applied.append(
                {
                    "section": section,
                    "key": key,
                    "old": change["old"],
                    "new": value,
                    "fact_id": recorded["id"],
                }
            )
            rejected += _reject_proposals_for_key(user_id, key, fact_store, now)
    for removal in diff["removed"]:
        section = removal["section"]
        key = removal["key"]
        value = removal["old"]
        if section == "Journal":
            ignored.append(
                {"section": section, "key": key, "reason": "journal entries are history"}
            )
            continue
        if section not in _SECTION_TO_KEY:
            ignored.append({"section": section, "key": key, "reason": "unknown section"})
            continue
        snapshot_key = _SECTION_TO_KEY[section]
        if key is None:
            if snapshot_key == "lessons":
                key = f"lesson:{_slug(value)}"
            else:
                ignored.append({"section": section, "key": key, "reason": "no key for this line"})
                continue
        if _expire_active_for_key(user_id, key, fact_store, now):
            expired.append({"section": section, "key": key, "old": value})
        else:
            rejected += _reject_proposals_for_key(user_id, key, fact_store, now)
            if rejected:
                expired.append({"section": section, "key": key, "old": value})
    return {
        "applied": applied,
        "expired": expired,
        "rejected_proposals": rejected,
        "ignored": ignored,
    }


def run_nightly_rebuild(user_id, *, dsn=None, store=None, fact_store=None, now=None):
    """Nightly consolidation hook: journal entry plus rebuild.

    Writes today's narrative journal entry from today's
    summaries and episodes (when the day has anything to record),
    then rebuilds MEMORY.md from the lower layers. Returns the
    rebuild result plus ``"journal_entry"`` (the stored entry or
    None when the day was empty).
    """
    now = now or _now()
    store = store or default_store(dsn=dsn)
    entry = build_daily_entry(user_id, now.date(), dsn=dsn)
    journal_entry = None
    if entry is not None:
        journal_entry = store.save_journal_entry(
            user_id, now.date(), entry["text"], entry["source_event_ids"]
        )
    result = rebuild_memory(user_id, dsn=dsn, store=store, fact_store=fact_store, now=now)
    result["journal_entry"] = journal_entry
    return result
