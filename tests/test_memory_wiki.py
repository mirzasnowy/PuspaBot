"""Tests for the auto-curated knowledge wiki (COW-06,
``puspa/memory/wiki.py``).

Covers the pure curation (collection from consolidation signals,
deterministic topic assignment, page grouping with
cross-references, Markdown rendering, the safe Markdown-to-HTML
subset), the file storage (atomic writes, stale-page cleanup,
manifest round-trip, path-traversal refusal), and the end-to-end
build against the real Postgres signals (a consolidation summary,
a procedure, and an active profile fact become attributed wiki
pages on disk).
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.memory import wiki
from puspa.memory.consolidate import consolidate_idle
from puspa.memory.episodic import record_episode
from puspa.memory.procedural import record_successful_task
from puspa.memory.profile import add_fact

FACTS = [
    {
        "key": "preference:shopping",
        "content": "User prefers Tokopedia for phone shopping",
        "status": "active",
    },
    {
        "key": "goal:savings",
        "content": "Save Rp1 billion by age 25 with disciplined spending",
        "status": "active",
    },
]
SUMMARIES = [
    {
        "id": "sum-1",
        "summary": "- [task_completed] Tokopedia: phone order successful",
        "event_ids": ["evt-1", "evt-2"],
    }
]
PROCEDURES = [
    {
        "id": "proc-1",
        "service": "Tokopedia",
        "step": ["search phone", "compare prices", "checkout"],
        "source_event_id": "evt-3",
    }
]


def test_collect_entries_merges_sources_and_classifies_kinds():
    entries = wiki.collect_entries("u1", facts=FACTS, summaries=SUMMARIES, procedures=PROCEDURES)
    by_kind = {entry.kind: entry for entry in entries}
    assert by_kind["fact"].text == "User prefers Tokopedia for phone shopping"
    assert by_kind["fact"].sources == ["fact:preference:shopping"]
    assert by_kind["decision"].text.startswith("Save Rp1 billion")
    assert by_kind["decision"].sources == ["fact:goal:savings"]
    assert by_kind["summary"].text == "Tokopedia: phone order successful"
    assert by_kind["summary"].sources == [
        "summary:sum-1",
        "episode-event:evt-1",
        "episode-event:evt-2",
    ]
    assert by_kind["procedure"].text == "Tokopedia: search phone; compare prices; checkout"
    assert by_kind["procedure"].sources == ["procedure:proc-1", "episode-event:evt-3"]


def test_collect_entries_skips_empty_and_dedupes_identical_text():
    entries = wiki.collect_entries(
        "u1",
        facts=[{"key": "k", "content": "  ", "status": "active"}],
        summaries=[
            {"id": "a", "summary": "Tokopedia: order done", "event_ids": []},
            {"id": "b", "summary": "Tokopedia: order done", "event_ids": []},
        ],
        procedures=[],
    )
    assert len(entries) == 1
    assert entries[0].kind == "summary"
    assert entries[0].sources == ["summary:a", "summary:b"]


def test_assign_topics_groups_shared_entities_and_falls_back():
    entries = wiki.collect_entries("u1", facts=FACTS, summaries=SUMMARIES, procedures=PROCEDURES)
    wiki.assign_topics(entries)
    by_text = {entry.text: entry.topic for entry in entries}
    assert by_text["User prefers Tokopedia for phone shopping"] == "phone"
    assert by_text["Tokopedia: search phone; compare prices; checkout"] == "phone"
    assert by_text["Tokopedia: phone order successful"] == "phone"
    lonely = wiki.WikiEntry(kind="fact", text="a b")
    wiki.assign_topics([lonely])
    assert lonely.topic == "general"


def test_group_pages_cross_references_shared_entities():
    a = wiki.WikiEntry(kind="fact", text="Samsung Galaxy S24 is a phone sold by Tokopedia")
    b = wiki.WikiEntry(kind="fact", text="Tokopedia offers a promo for Samsung Galaxy S24")
    c = wiki.WikiEntry(kind="fact", text="Xiaomi Redmi Note is a phone sold by Shopee")
    a.topic = "phone"
    b.topic = "phone"
    c.topic = "xiaomi"
    pages = wiki.group_pages([a, b, c])
    assert [p.slug for p in pages] == ["phone", "xiaomi"]
    phone_page = pages[0]
    assert phone_page.title == "Phone"
    # The xiaomi page shares the entity "phone" with the phone page.
    assert phone_page.related == ["xiaomi"]
    assert pages[1].related == ["phone"]


def test_render_page_names_every_source_and_links_related():
    entry = wiki.WikiEntry(
        kind="fact", text="User prefers Tokopedia", sources=["fact:k"], topic="tokopedia"
    )
    page = wiki.WikiPage(
        slug="tokopedia",
        title="Tokopedia",
        entries=[entry],
        related=["phone"],
        generated_at=datetime(2026, 11, 22, tzinfo=UTC),
    )
    text = wiki.render_page(page)
    assert text.startswith("# Tokopedia\n")
    assert "- User prefers Tokopedia\n  - Sources: fact:k" in text
    assert "- [phone](phone.md)" in text
    assert "Do not edit by hand" in text


def test_render_index_lists_topics_with_counts():
    pages = [
        wiki.WikiPage(slug="phone", title="Phone", entries=[wiki.WikiEntry("fact", "x")]),
        wiki.WikiPage(slug="age", title="Age", entries=[]),
    ]
    text = wiki.render_index(pages, now=datetime(2026, 11, 22, tzinfo=UTC))
    assert "- [Phone](phone.md) — 1 entry" in text
    assert "- [Age](age.md) — 0 entries" in text


def test_render_markdown_html_escapes_hostile_markup():
    html = wiki.render_markdown_html(
        "# Title\n\n<script>alert(1)</script>\n\n- **bold** and `code`\n- [Phone](phone.md) and [evil](javascript:alert(1))\n"
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html
    assert '<a href="phone.md">Phone</a>' in html
    assert "javascript:" not in html


def test_write_and_list_round_trip_with_manifest(tmp_path):
    entries = wiki.collect_entries("u1", facts=FACTS, summaries=SUMMARIES, procedures=PROCEDURES)
    wiki.assign_topics(entries)
    pages = wiki.group_pages(entries, now=datetime(2026, 11, 22, tzinfo=UTC))
    report = wiki.write_wiki("u1", pages, home=tmp_path)
    assert report["pages_written"] == 2
    assert set(report["files"]) == {"age.md", "phone.md"}
    listed = wiki.list_pages("u1", home=tmp_path)
    assert {p["slug"] for p in listed} == {"age", "phone"}
    phone = wiki.read_page("u1", "phone", home=tmp_path)
    assert phone["title"] == "Phone"
    assert "fact:preference:shopping" in phone["sources"]
    assert "<h1>Phone</h1>" in phone["html"]
    assert "phone" in phone["entities"]
    with pytest.raises(KeyError):
        wiki.read_page("u1", "nope", home=tmp_path)


def test_rebuild_removes_stale_topic_files(tmp_path):
    first = wiki.group_pages(
        [wiki.WikiEntry(kind="fact", text="Tokopedia sells phones", topic="tokopedia")]
    )
    wiki.write_wiki("u1", first, home=tmp_path)
    second = wiki.group_pages(
        [wiki.WikiEntry(kind="fact", text="Shopee sells phones", topic="shopee")]
    )
    wiki.write_wiki("u1", second, home=tmp_path)
    names = {p.name for p in (tmp_path / "wiki" / "u1").iterdir()}
    assert "tokopedia.md" not in names
    assert names == {"shopee.md", "index.md", "_index.json"}


def test_wiki_dir_refuses_path_traversal(tmp_path):
    with pytest.raises(ValueError):
        wiki.wiki_dir("../evil", home=tmp_path)
    with pytest.raises(ValueError):
        wiki.read_page("u1", "../evil", home=tmp_path)
    assert wiki.list_pages("nobody", home=tmp_path) == []


def test_build_wiki_end_to_end_on_real_signals(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    record_episode(
        user_id,
        "Samsung Galaxy S24 is a phone. Samsung Galaxy S24 sells Tokopedia.",
        "user_message",
        dsn=test_dsn,
    )
    record_episode(
        user_id,
        "Tokopedia: phone order successful",
        "task_completed",
        task_id="11111111-1111-1111-1111-111111111111",
        event_id="22222222-2222-2222-2222-222222222222",
        dsn=test_dsn,
    )
    add_fact(
        user_id,
        "preference:shopping",
        "User prefers Tokopedia for phone shopping",
        "user_stated",
        dsn=test_dsn,
    )
    record_successful_task(
        user_id,
        "Tokopedia",
        ["search phone", "compare prices", "checkout"],
        dsn=test_dsn,
    )
    consolidate_idle(
        user_id,
        older_than_days=30,
        dsn=test_dsn,
        now=datetime.now(UTC) + timedelta(days=31),
    )
    report = wiki.build_wiki(user_id, dsn=test_dsn, home=tmp_path)
    assert report["entries"] >= 3
    assert report["pages_written"] >= 1
    assert "phone" in report["topics"]
    page = wiki.read_page(user_id, "phone", home=tmp_path)
    assert "fact:preference:shopping" in page["sources"]
    assert "procedure:" in " ".join(page["sources"])
    assert "summary:" in " ".join(page["sources"])
    assert "Tokopedia" in page["markdown"]


def test_build_wiki_with_no_signals_writes_empty_index(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    report = wiki.build_wiki(user_id, dsn=test_dsn, home=tmp_path)
    assert report["entries"] == 0
    assert report["pages_written"] == 0
    assert wiki.list_pages(user_id, home=tmp_path) == []
    index = (tmp_path / "wiki" / user_id / "index.md").read_text(encoding="utf-8")
    assert "Nothing curated yet" in index


def test_cli_wiki_build_and_list(make_user, test_dsn, tmp_path, monkeypatch, capsys):
    from puspa.cli import main

    user_id = make_user()["user_id"]
    add_fact(
        user_id,
        "preference:shopping",
        "User prefers Tokopedia for phone shopping",
        "user_stated",
        dsn=test_dsn,
    )
    monkeypatch.setenv("PUSPA_TEST_DSN", test_dsn)
    monkeypatch.setenv("PUSPA_HOME", str(tmp_path))
    assert main(["wiki", "build", "--user", user_id]) == 0
    out = capsys.readouterr().out
    assert "Wiki built: 1 page(s), 1 entry" in out
    assert main(["wiki", "list", "--user", user_id]) == 0
    assert "phone — Phone (1 entries)" in capsys.readouterr().out
    assert main(["wiki", "build", "--user", "not-a-uuid"]) == 1
