"""Auto-curated knowledge wiki (COW-06).

The agent curates valuable information from conversations into a
topic-organized Markdown wiki with cross-references, plus a
read-only graph view of the same topics. CowAgent builds an
evolving knowledge graph with a visual browser; PuspaBot already
has the graph (``puspa.retrieval.knowledge_graph``) and a
second-brain view (B18, user-editable, post-beta, NOT this item).
What was missing was the auto-curation: turning what the agent
already consolidated into durable, browsable pages.

This module builds only on existing consolidation signals, never
a second memory store:

- consolidation summaries (``puspa.memory.consolidate``:
  ``list_summaries``) — one per task, each pointing at its source
  episodes through ``event_ids``;
- procedures (``puspa.memory.procedural``: ``list_procedures``) —
  successful task patterns, each with its ``source_event_id``;
- active profile facts (``puspa.memory.facts``) — user-confirmed
  facts, goals and commitments.

Every curated entry records its source (a summary id, a procedure
id, a fact key, and the episode event ids behind them), so no wiki
page is ever unattributed. Curation is deterministic and free: no
model call, no network. Topics come from the entities and concepts
already extracted by ``puspa.memory.episodic`` — the entity shared
by the most entries becomes the topic, so entries about the same
thing land on the same page. Cross-references link pages that
share an entity.

Pages live as Markdown files under the user's data dir:
``<puspa home>/wiki/<user_id>/<topic-slug>.md``, plus an
``index.md`` and a small ``_index.json`` manifest (title, entry
count, sources, entities per page) that the PWA reads without
parsing Markdown. A rebuild overwrites the generated files, so
the pages say so in their header; hand edits belong in the
second-brain graph view, not here.
"""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ..sessions.home import resolve_home
from .episodic import extract_concepts, extract_entities

__all__ = [
    "INDEX_FILENAME",
    "MANIFEST_FILENAME",
    "WIKI_DIR_NAME",
    "WikiEntry",
    "WikiPage",
    "assign_topics",
    "build_wiki",
    "collect_entries",
    "group_pages",
    "list_pages",
    "read_page",
    "render_index",
    "render_markdown_html",
    "render_page",
    "slugify",
    "wiki_dir",
    "write_wiki",
]

#: Subdirectory of the Puspa home holding every user's wiki.
WIKI_DIR_NAME = "wiki"
#: Index page listing every topic, written on each build.
INDEX_FILENAME = "index.md"
#: Machine-readable manifest beside the pages (the PWA list view).
MANIFEST_FILENAME = "_index.json"

#: Entry kinds, in the order their sections render on a page.
KIND_ORDER = ("decision", "fact", "procedure", "summary")
KIND_HEADINGS = {
    "decision": "Decisions",
    "fact": "Facts",
    "procedure": "Procedures",
    "summary": "Conversation summaries",
}

#: Fact keys that are decisions, not plain facts (goals the user
#: set and commitments they made).
_DECISION_KEY_PREFIXES = ("goal:", "commitment:")

#: A user id is one path segment: letters, digits, "-" or "_".
_USER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
#: A topic slug is one file stem: lowercase letters, digits, "-".
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """URL-safe slug of ``text`` (lowercase, dashes, max 60 chars)."""
    slug = _NON_SLUG_RE.sub("-", str(text or "").lower()).strip("-")
    return slug[:60].strip("-") or "general"


def _now() -> datetime:
    return datetime.now(UTC)


def _validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not _USER_ID_RE.match(user_id):
        raise ValueError(f"user_id must be a plain id, not a path: {user_id!r}")
    return user_id


def _validate_slug(slug: str) -> str:
    if not isinstance(slug, str) or not _SLUG_RE.match(slug):
        raise ValueError(f"topic slug must be lowercase letters, digits or '-': {slug!r}")
    return slug


def wiki_dir(user_id: str, *, home=None) -> Path:
    """The wiki directory for one user: ``<home>/wiki/<user_id>``."""
    _validate_user_id(user_id)
    return resolve_home(home=home) / WIKI_DIR_NAME / user_id


@dataclass
class WikiEntry:
    """One curated item: what was learned, and where it came from.

    ``sources`` are stable ids, never prose: ``fact:<key>``,
    ``summary:<id>``, ``procedure:<id>`` and the episode event
    ids behind a summary or procedure as
    ``episode-event:<event id>``. ``topic`` is assigned by
    :func:`assign_topics` (empty until then).
    """

    kind: str
    text: str
    sources: list = field(default_factory=list)
    topic: str = ""


@dataclass
class WikiPage:
    """One topic page: its entries plus the topics it links to."""

    slug: str
    title: str
    entries: list = field(default_factory=list)
    related: list = field(default_factory=list)
    generated_at: datetime | None = None

    @property
    def sources(self) -> list:
        """Every source id behind this page, sorted, deduplicated."""
        seen: set[str] = set()
        for entry in self.entries:
            seen.update(str(s) for s in entry.sources)
        return sorted(seen)

    @property
    def entities(self) -> list:
        """Entities mentioned on this page, sorted, deduplicated."""
        seen: set[str] = set()
        for entry in self.entries:
            seen.update(extract_entities(entry.text))
            seen.update(extract_concepts(entry.text))
        return sorted(seen)


# -- collection ---------------------------------------------------------


def _fact_kind(fact: dict) -> str:
    key = str(fact.get("key") or "").lower()
    if key.startswith(_DECISION_KEY_PREFIXES):
        return "decision"
    return "fact"


def _fact_text(fact: dict) -> str:
    return " ".join(str(fact.get("content") or fact.get("value") or "").split())


def _summary_text(summary: dict) -> str:
    """One line per summary row, without the extractive markers.

    Consolidation summaries are extractive bullet lists
    ("- [task_completed] ..."); a wiki entry is prose, so the
    leading "- " and the "[type] " tag come off and the rows join
    with "; ".
    """
    raw = str(summary.get("summary") or "")
    rows = []
    for raw_line in raw.split("\n"):
        line = raw_line.strip()
        if line.startswith("- "):
            line = line[2:].strip()
        line = re.sub(r"^\[[^\]]+\]\s*", "", line)
        if line:
            rows.append(line)
    return " ".join("; ".join(rows).split())


def _procedure_text(procedure: dict) -> str:
    service = str(procedure.get("service") or "tasks").strip()
    steps = procedure.get("step") or []
    if isinstance(steps, str):
        steps = [steps]
    body = "; ".join(" ".join(str(s).split()) for s in steps if str(s).strip())
    text = f"{service}: {body}" if body else service
    return " ".join(text.split())


def collect_entries(user_id, *, dsn=None, facts=None, summaries=None, procedures=None):
    """Curatable entries from the existing consolidation signals.

    Each source is injectable (a list of plain dicts) so tests run
    without a database; when a source is None it is read from
    Postgres with ``dsn``. Entries with empty text are skipped,
    and identical ``(kind, text)`` pairs merge, keeping the union
    of their sources — a fact already stated in a summary is one
    entry, not two.
    """
    if facts is None:
        from .facts import PostgresFactStore

        facts = PostgresFactStore(dsn=dsn).list_user(user_id, statuses=("active",))
    if summaries is None:
        from . import consolidate

        summaries = consolidate.list_summaries(user_id, dsn=dsn)
    if procedures is None:
        from . import procedural

        procedures = procedural.list_procedures(user_id, dsn=dsn)
    merged: dict[tuple, WikiEntry] = {}
    for fact in facts or []:
        text = _fact_text(fact)
        if not text:
            continue
        key = (str(fact.get("key") or "").strip(), text)
        entry = merged.setdefault(
            (_fact_kind(fact), text),
            WikiEntry(kind=_fact_kind(fact), text=text, sources=[]),
        )
        source = f"fact:{key[0]}" if key[0] else "fact:?"
        if source not in entry.sources:
            entry.sources.append(source)
    for summary in summaries or []:
        text = _summary_text(summary)
        if not text:
            continue
        entry = merged.setdefault(("summary", text), WikiEntry(kind="summary", text=text))
        sources = [f"summary:{summary.get('id')}"] if summary.get("id") else []
        sources += [f"episode-event:{e}" for e in (summary.get("event_ids") or []) if e]
        for source in sources:
            if source not in entry.sources:
                entry.sources.append(source)
    for procedure in procedures or []:
        text = _procedure_text(procedure)
        if not text:
            continue
        entry = merged.setdefault(("procedure", text), WikiEntry(kind="procedure", text=text))
        sources = [f"procedure:{procedure.get('id')}"] if procedure.get("id") else []
        if procedure.get("source_event_id"):
            sources.append(f"episode-event:{procedure['source_event_id']}")
        for source in sources:
            if source not in entry.sources:
                entry.sources.append(source)
    return sorted(merged.values(), key=lambda e: (e.kind, e.text))


# -- topics -------------------------------------------------------------


def _entry_terms(entry: WikiEntry) -> list[str]:
    """Topic candidates in one entry: entities plus concepts.

    Entities (names, model codes) are the specific terms; concepts
    (content keywords) are the abstract ones — the same two levels
    episodic retrieval ranks on. The union is what groups entries
    into topics and what links pages to each other.
    """
    return sorted(set(extract_entities(entry.text)) | set(extract_concepts(entry.text)))


def assign_topics(entries):
    """Assign each entry the topic its terms point at, in place.

    The topic of an entry is the term it carries that appears in
    the most entries overall (ties break alphabetically, so the
    assignment is deterministic). Entries with no term at all go
    to the "general" topic. Returns the entries, for chaining.
    """
    counts: dict[str, int] = {}
    per_entry: list[list[str]] = []
    for entry in entries or []:
        terms = sorted(set(_entry_terms(entry)))
        per_entry.append(terms)
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
    for entry, terms in zip(entries or [], per_entry):
        if not terms:
            entry.topic = "general"
            continue
        best = min(terms, key=lambda t: (-counts[t], t))
        entry.topic = best
    return entries


def group_pages(entries, *, now=None):
    """Group assigned entries into topic pages, with cross-references.

    One page per topic slug, entries in kind order then text
    order, pages sorted by slug. A page links to another page
    when they share an entity other than the page's own topic
    term — that shared entity is what the cross-reference means.
    Entries without an assigned topic are treated as "general".
    """
    moment = now or _now()
    by_slug: dict[str, list[WikiEntry]] = {}
    for entry in entries or []:
        slug = slugify(entry.topic or "general")
        by_slug.setdefault(slug, []).append(entry)
    page_terms: dict[str, set[str]] = {}
    pages: list[WikiPage] = []
    for slug in sorted(by_slug):
        group = sorted(
            by_slug[slug],
            key=lambda e: (KIND_ORDER.index(e.kind) if e.kind in KIND_ORDER else 99, e.text),
        )
        title = (group[0].topic or slug).strip().title() if group else slug
        page = WikiPage(slug=slug, title=title, entries=group, generated_at=moment)
        pages.append(page)
        terms: set[str] = set()
        for entry in group:
            terms.update(_entry_terms(entry))
        page_terms[slug] = terms
    for page in pages:
        own = {page.title.lower()}
        related = set()
        for other in pages:
            if other.slug == page.slug:
                continue
            if (page_terms[page.slug] & page_terms[other.slug]) - own:
                related.add(other.slug)
        page.related = sorted(related)
    return pages


# -- rendering ----------------------------------------------------------


def _page_filename(slug: str) -> str:
    return f"{slug}.md"


def render_page(page: WikiPage) -> str:
    """One topic page as Markdown.

    Entries group by kind; every entry names its sources on the
    line below it, so nothing on the page is unattributed. The
    header says the file is generated and overwritten on rebuild.
    """
    stamp = (page.generated_at or _now()).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# {page.title}",
        "",
        (
            "<!-- Generated by PuspaBot's auto-curated wiki from "
            "consolidation signals. Do not edit by hand: the next "
            "build overwrites this file. -->"
        ),
        "",
        f"_Curated from conversations, {stamp}. Every entry names its source._",
        "",
    ]
    for kind in KIND_ORDER:
        group = [e for e in page.entries if e.kind == kind]
        if not group:
            continue
        lines.append(f"## {KIND_HEADINGS[kind]}")
        lines.append("")
        for entry in group:
            lines.append(f"- {entry.text}")
            if entry.sources:
                lines.append(f"  - Sources: {', '.join(sorted(entry.sources))}")
        lines.append("")
    if page.related:
        lines.append("## Related topics")
        lines.append("")
        for slug in page.related:
            lines.append(f"- [{slug}]({slug}.md)")
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_index(pages, *, now=None) -> str:
    """The wiki index: every topic with its entry count."""
    stamp = (now or _now()).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Wiki",
        "",
        (
            "<!-- Generated by PuspaBot's auto-curated wiki. Do not edit "
            "by hand: the next build overwrites this file. -->"
        ),
        "",
        f"_Topic-organized notes curated from conversations, {stamp}._",
        "",
        "## Topics",
        "",
    ]
    if not pages:
        lines.append(
            "_Nothing curated yet. Run a build after the agent has consolidated conversations._"
        )
        lines.append("")
    for page in pages:
        count = len(page.entries)
        noun = "entry" if count == 1 else "entries"
        lines.append(f"- [{page.title}]({_page_filename(page.slug)}) — {count} {noun}")
    lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$")


def _inline_html(text: str) -> str:
    """Inline Markdown (bold, code, links) over escaped text."""
    escaped = html.escape(text)
    escaped = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = _BOLD_RE.sub(lambda m: f"<strong>{m.group(1)}</strong>", escaped)

    def _link(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(("http://", "https://")) or (
            target.endswith(".md") and "/" not in target and ".." not in target
        ):
            href = html.escape(target, quote=True)
            return f'<a href="{href}">{label}</a>'
        return label

    return _LINK_RE.sub(_link, escaped)


def render_markdown_html(text: str) -> str:
    """A tiny, safe Markdown-to-HTML renderer (subset, no dependency).

    Handles ``#``/``##``/``###`` headings, ``- `` bullet lists,
    paragraphs, ``**bold**``, ```code``` and ``[label](target)``
    links. All other markup is escaped, never passed through, so
    a hostile wiki entry cannot inject HTML into the PWA.
    """
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    out: list[str] = []
    in_list = False

    def _close_list() -> None:
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            _close_list()
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            _close_list()
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline_html(heading.group(2))}</h{level}>")
            continue
        if line.startswith("<!--") and line.endswith("-->"):
            continue
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline_html(line[2:].strip())}</li>")
            continue
        _close_list()
        out.append(f"<p>{_inline_html(line)}</p>")
    _close_list()
    return "\n".join(out)


# -- storage ------------------------------------------------------------


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _manifest(pages, *, generated_at: datetime) -> dict:
    return {
        "generated_at": generated_at.isoformat(),
        "pages": [
            {
                "slug": page.slug,
                "title": page.title,
                "entries": len(page.entries),
                "sources": page.sources,
                "entities": page.entities,
                "updated_at": (page.generated_at or generated_at).isoformat(),
            }
            for page in pages
        ],
    }


def write_wiki(user_id, pages, *, home=None, now=None):
    """Write pages + index + manifest to the user's wiki dir.

    Writes are atomic (temp file + replace) and stale topic files
    from an earlier build are removed, so a renamed topic does not
    leave a ghost page behind. Returns the build report.
    """
    moment = now or _now()
    target = wiki_dir(user_id, home=home)
    target.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for page in pages:
        page.generated_at = page.generated_at or moment
        path = target / _page_filename(page.slug)
        _atomic_write(path, render_page(page))
        written.append(_page_filename(page.slug))
    _atomic_write(target / INDEX_FILENAME, render_index(pages, now=moment))
    _atomic_write(
        target / MANIFEST_FILENAME,
        json.dumps(_manifest(pages, generated_at=moment), indent=2, ensure_ascii=False) + "\n",
    )
    keep = {_page_filename(p.slug) for p in pages} | {INDEX_FILENAME, MANIFEST_FILENAME}
    for stale in target.glob("*.md"):
        if stale.name not in keep:
            stale.unlink()
    return {
        "user_id": user_id,
        "wiki_dir": str(target),
        "pages_written": len(pages),
        "files": sorted(written),
        "generated_at": moment.isoformat(),
    }


def list_pages(user_id, *, home=None):
    """The user's wiki pages, from the manifest (or a disk scan).

    Returns ``[]`` when the wiki has never been built. Each item
    is ``{"slug", "title", "entries", "sources", "entities",
    "updated_at"}``. A missing manifest falls back to scanning
    the ``*.md`` files, so hand-written pages still list.
    """
    target = wiki_dir(user_id, home=home)
    manifest_path = target / MANIFEST_FILENAME
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("pages"), list):
                return [dict(p) for p in data["pages"]]
        except (ValueError, OSError):
            pass
    pages = []
    if not target.is_dir():
        return pages
    for path in sorted(target.glob("*.md")):
        if path.name == INDEX_FILENAME:
            continue
        try:
            first = path.read_text(encoding="utf-8").split("\n", 1)[0].strip()
        except OSError:
            continue
        title = first[2:].strip() if first.startswith("# ") else path.stem
        pages.append(
            {
                "slug": path.stem,
                "title": title,
                "entries": 0,
                "sources": [],
                "entities": [],
                "updated_at": None,
            }
        )
    return pages


def read_page(user_id, slug, *, home=None):
    """One wiki page: its Markdown, rendered HTML, and sources.

    Raises :class:`KeyError` when the topic does not exist.
    """
    _validate_slug(slug)
    target = wiki_dir(user_id, home=home)
    path = target / _page_filename(slug)
    if not path.exists():
        raise KeyError(f"unknown wiki topic: {slug!r}")
    markdown = path.read_text(encoding="utf-8")
    first = markdown.split("\n", 1)[0].strip()
    title = first[2:].strip() if first.startswith("# ") else slug
    meta = {p["slug"]: p for p in list_pages(user_id, home=home)}
    info = meta.get(slug, {})
    return {
        "slug": slug,
        "title": title,
        "markdown": markdown,
        "html": render_markdown_html(markdown),
        "sources": info.get("sources", []),
        "entities": info.get("entities", []),
        "updated_at": info.get("updated_at"),
    }


def build_wiki(
    user_id, *, dsn=None, home=None, now=None, facts=None, summaries=None, procedures=None
):
    """One full curation pass: collect, group, write, report.

    Reads the consolidation signals (or the injected lists in
    tests), assigns topics, groups pages, writes them, and
    returns the report: pages written, entries curated, topics,
    and the wiki directory. An empty signal set still writes an
    empty index, so the screen can say "nothing yet" honestly.
    """
    moment = now or _now()
    _validate_user_id(user_id)
    entries = collect_entries(
        user_id, dsn=dsn, facts=facts, summaries=summaries, procedures=procedures
    )
    assign_topics(entries)
    pages = group_pages(entries, now=moment)
    report = write_wiki(user_id, pages, home=home, now=moment)
    report["entries"] = len(entries)
    report["topics"] = [page.slug for page in pages]
    return report
