"""Tests for the durable MEMORY.md store (V2-05 / COW-05).

Pure tests are marked ``unit`` (no database). Database tests use
the ``test_dsn`` fixture and the shared test database; they clean
up only their own rows, and the global fixture truncates the
durable tables before and after each test.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from puspa.memory import durable
from puspa.memory.facts import MemoryFactStore, PostgresFactStore, record_fact

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def _fact(fact_id, key, value, source="user_stated", status="active"):
    return {
        "id": fact_id,
        "user_id": "user-1",
        "key": key,
        "content": value,
        "status": status,
        "valid_from": NOW,
        "valid_until": None,
        "superseded_by": None,
        "source": source,
        "confidence": None,
        "source_detail": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _snapshot():
    return {
        "goals": [
            {
                "key": "goal:remote-work",
                "text": "Land remote work paying USD 3000 a month",
                "source": "user_stated",
            }
        ],
        "commitments": [
            {
                "key": "commitment:beta",
                "text": "Ship the Puspa beta to 10 users",
                "source": "user_stated",
            }
        ],
        "corrections": [
            {"key": "city", "text": "Lives in Karawang, not Tangerang", "source": "user_confirmed"}
        ],
        "preferences": [
            {
                "key": "preference:language",
                "text": "Prefers plain Indonesian",
                "source": "user_stated",
            }
        ],
        "facts": [{"key": "city", "text": "Karawang", "source": "user_stated"}],
        "people": [{"key": "person:budi", "text": "Budi is a friend", "source": "user_stated"}],
        "lessons": [{"text": "A repeated deep task becomes a reflex skill.", "source": "summary"}],
        "guesses": [{"key": "hobby", "text": "Plays chess", "source": "inferred"}],
        "journal": [{"date": "2026-11-21", "text": "Shipped the first durable memory rebuild."}],
    }


@pytest.mark.unit
def test_render_has_all_sections_in_order():
    text = durable.render_memory_md(_snapshot(), generated_at=NOW)
    positions = [text.index(f"## {heading}") for heading in durable.SECTIONS]
    assert positions == sorted(positions)
    assert "Land remote work paying USD 3000 a month" in text
    assert "Plays chess (inferred, not yet confirmed)" in text
    assert "Last rebuilt: 2026-11-21 12:00 UTC" in text


@pytest.mark.unit
def test_render_respects_size_caps():
    snapshot = {key: [] for key in durable.SNAPSHOT_KEYS}
    snapshot["facts"] = [
        {"key": f"fact:{n}", "text": "x" * 100, "source": "user_stated"} for n in range(500)
    ]
    text = durable.render_memory_md(snapshot, generated_at=NOW)
    from puspa.memory.limits import check_narrative

    result = check_narrative(text)
    assert result["ok"], result["violations"]


@pytest.mark.unit
def test_parse_round_trip():
    text = durable.render_memory_md(_snapshot(), generated_at=NOW)
    parsed = durable.parse_memory_md(text)
    facts = parsed["facts"]["Facts"]
    assert {"key": "city", "value": "Karawang"} in facts
    guesses = parsed["facts"]["Unconfirmed Guesses"]
    assert {"key": "hobby", "value": "Plays chess"} in guesses
    assert "Shipped the first durable memory rebuild." in parsed["sections"]["Journal"][0]


@pytest.mark.unit
def test_diff_detects_change_add_remove():
    before = durable.render_memory_md(_snapshot(), generated_at=NOW)
    edited = before.replace("**city**: Karawang", "**city**: Bandung")
    edited += "\n"
    diff = durable.diff_memory_md(before, edited)
    assert len(diff["changed"]) == 1
    assert diff["changed"][0]["key"] == "city"
    assert diff["changed"][0]["old"] == "Karawang"
    assert diff["changed"][0]["new"] == "Bandung"
    assert diff["added"] == []
    assert diff["removed"] == []


@pytest.mark.unit
def test_diff_of_identical_texts_is_empty():
    text = durable.render_memory_md(_snapshot(), generated_at=NOW)
    assert durable.diff_memory_md(text, text) == {"changed": [], "added": [], "removed": []}


@pytest.mark.unit
def test_user_edit_beats_inferred_fact():
    """A user edit to MEMORY.md beats the inferred guess.

    The guess was only proposed (never active). After the edit
    the corrected value is the active fact and the guess is
    rejected, so the next rebuild shows the correction, not the
    guess.
    """
    store = MemoryFactStore()
    record_fact("user-1", "hobby", "Plays chess", source="inferred", store=store, now=NOW)
    snapshot = {key: [] for key in durable.SNAPSHOT_KEYS}
    snapshot["guesses"] = [{"key": "hobby", "text": "Plays chess", "source": "inferred"}]
    previous = durable.render_memory_md(snapshot, generated_at=NOW)
    edited = previous.replace("Plays chess", "Plays go, not chess")
    result = durable.apply_user_edits(
        "user-1", edited, previous_text=previous, fact_store=store, now=NOW
    )
    assert len(result["applied"]) == 1
    assert result["applied"][0]["key"] == "hobby"
    assert result["applied"][0]["new"] == "Plays go, not chess"
    assert result["rejected_proposals"] == 1
    history = store.list_for_key("user-1", "hobby")
    active = [fact for fact in history if fact["status"] == "active"]
    assert len(active) == 1
    assert active[0]["content"] == "Plays go, not chess"
    assert active[0]["source"] == "user_confirmed"
    assert all(fact["status"] != "proposed" for fact in history)
    rebuilt = durable.collect_snapshot(
        "user-1", fact_store=store, facts=None, summaries=[], episodes=[], journal_entries=[]
    )
    assert rebuilt["snapshot"]["guesses"] == []
    assert rebuilt["snapshot"]["corrections"][0]["text"] == "Plays go, not chess"


@pytest.mark.unit
def test_user_edit_supersedes_active_fact():
    store = MemoryFactStore()
    record_fact("user-1", "city", "Tangerang", source="user_stated", store=store, now=NOW)
    snapshot = {key: [] for key in durable.SNAPSHOT_KEYS}
    snapshot["facts"] = [{"key": "city", "text": "Tangerang", "source": "user_stated"}]
    previous = durable.render_memory_md(snapshot, generated_at=NOW)
    edited = previous.replace("Tangerang", "Karawang")
    result = durable.apply_user_edits(
        "user-1", edited, previous_text=previous, fact_store=store, now=NOW
    )
    assert len(result["applied"]) == 1
    history = store.list_for_key("user-1", "city")
    active = [fact for fact in history if fact["status"] == "active"]
    assert [fact["content"] for fact in active] == ["Karawang"]
    assert any(fact["status"] == "superseded" for fact in history)


@pytest.mark.unit
def test_deleted_line_expires_fact():
    store = MemoryFactStore()
    record_fact("user-1", "city", "Tangerang", source="user_stated", store=store, now=NOW)
    snapshot = {key: [] for key in durable.SNAPSHOT_KEYS}
    snapshot["facts"] = [{"key": "city", "text": "Tangerang", "source": "user_stated"}]
    previous = durable.render_memory_md(snapshot, generated_at=NOW)
    edited = "\n".join(line for line in previous.split("\n") if "**city**: Tangerang" not in line)
    result = durable.apply_user_edits(
        "user-1", edited, previous_text=previous, fact_store=store, now=NOW
    )
    assert len(result["expired"]) == 1
    assert result["expired"][0]["key"] == "city"
    history = store.list_for_key("user-1", "city")
    assert all(fact["status"] != "active" for fact in history)


@pytest.mark.unit
def test_added_lesson_survives_next_rebuild():
    store = MemoryFactStore()
    doc_store = durable.MemoryDurableStore()
    snapshot = {key: [] for key in durable.SNAPSHOT_KEYS}
    previous = durable.render_memory_md(snapshot, generated_at=NOW)
    edited = previous.replace(
        "## Lessons\n\n_Nothing recorded yet._",
        "## Lessons\n\n- Always verify against the live result.",
    )
    result = durable.apply_user_edits(
        "user-1", edited, previous_text=previous, fact_store=store, now=NOW
    )
    assert len(result["applied"]) == 1
    assert result["applied"][0]["key"].startswith("lesson:")
    rebuilt = durable.rebuild_memory(
        "user-1",
        store=doc_store,
        fact_store=store,
        summaries=[],
        episodes=[],
        journal_entries=[],
        now=NOW,
    )
    assert "Always verify against the live result." in rebuilt["content"]


@pytest.mark.unit
def test_journal_edit_is_ignored_not_applied():
    store = MemoryFactStore()
    snapshot = {key: [] for key in durable.SNAPSHOT_KEYS}
    snapshot["journal"] = [{"date": "2026-11-21", "text": "Shipped the first rebuild."}]
    previous = durable.render_memory_md(snapshot, generated_at=NOW)
    edited = previous.replace("Shipped the first rebuild.", "Shipped everything ever.")
    result = durable.apply_user_edits(
        "user-1", edited, previous_text=previous, fact_store=store, now=NOW
    )
    assert result["applied"] == []
    assert len(result["ignored"]) == 1
    assert result["ignored"][0]["section"] == "Journal"


@pytest.mark.unit
def test_rebuild_reproduces_file_from_ledger_and_graph():
    """Same facts, episodes, and summaries rebuild the same file.

    The second rebuild with identical inputs is byte-identical
    and does not bump the version: the rebuild is a pure function
    of the lower layers, not of the clock.
    """
    store = MemoryFactStore()
    doc_store = durable.MemoryDurableStore()
    record_fact(
        "user-1", "goal:remote-work", "Land remote work", source="user_stated", store=store, now=NOW
    )
    record_fact("user-1", "city", "Karawang", source="user_stated", store=store, now=NOW)
    episodes = [
        {
            "id": "ep-1",
            "type": "user_message",
            "content": "Budi is a person. Budi likes remote work.",
            "event_id": "ev-1",
            "created_at": NOW,
        }
    ]
    summaries = [
        {
            "summary": "- [task_completed] puspa: successful run.",
            "event_ids": ["ev-1"],
            "created_at": NOW,
        }
    ]
    first = durable.rebuild_memory(
        "user-1",
        store=doc_store,
        fact_store=store,
        summaries=summaries,
        episodes=episodes,
        journal_entries=[],
        now=NOW,
    )
    second = durable.rebuild_memory(
        "user-1",
        store=doc_store,
        fact_store=store,
        summaries=summaries,
        episodes=episodes,
        journal_entries=[],
        now=NOW,
    )
    assert first["content"] == second["content"]
    assert first["version"] == 1
    assert second["version"] == 1
    assert second["changed"] is False
    assert "Land remote work" in first["content"]
    assert "Karawang" in first["content"]
    assert "budi" in first["content"]
    assert first["counts"]["graph_nodes"] >= 1


@pytest.mark.unit
def test_rebuild_version_bumps_only_when_content_changes():
    store = MemoryFactStore()
    doc_store = durable.MemoryDurableStore()
    record_fact("user-1", "city", "Karawang", source="user_stated", store=store, now=NOW)
    first = durable.rebuild_memory(
        "user-1",
        store=doc_store,
        fact_store=store,
        summaries=[],
        episodes=[],
        journal_entries=[],
        now=NOW,
    )
    record_fact("user-1", "city", "Bandung", source="user_stated", store=store, now=NOW)
    second = durable.rebuild_memory(
        "user-1",
        store=doc_store,
        fact_store=store,
        summaries=[],
        episodes=[],
        journal_entries=[],
        now=NOW,
    )
    assert first["version"] == 1
    assert second["version"] == 2
    assert second["changed"] is True
    assert "Bandung" in second["content"]


@pytest.mark.unit
def test_collect_snapshot_classifies_facts():
    facts = [
        _fact("f1", "goal:remote-work", "Land remote work"),
        _fact("f2", "commitment:beta", "Ship the beta"),
        _fact("f3", "city", "Karawang", source="user_confirmed"),
        _fact("f4", "preference:language", "Plain Indonesian"),
        _fact("f5", "person:budi", "Budi is a friend"),
        _fact("f6", "hobby", "Plays chess", source="inferred", status="proposed"),
        _fact("f7", "timezone", "Asia/Jakarta"),
    ]
    collected = durable.collect_snapshot(
        "user-1", facts=facts, summaries=[], episodes=[], journal_entries=[]
    )
    snapshot = collected["snapshot"]
    assert [item["key"] for item in snapshot["goals"]] == ["goal:remote-work"]
    assert [item["key"] for item in snapshot["commitments"]] == ["commitment:beta"]
    assert [item["key"] for item in snapshot["corrections"]] == ["city"]
    assert [item["key"] for item in snapshot["preferences"]] == ["preference:language"]
    assert [item["key"] for item in snapshot["people"]] == ["person:budi"]
    assert [item["key"] for item in snapshot["guesses"]] == ["hobby"]
    assert [item["key"] for item in snapshot["facts"]] == ["timezone"]


@pytest.mark.unit
def test_build_daily_entry_from_summaries_and_episodes():
    summaries = [
        {
            "summary": "- [task_completed] puspa: successful run.",
            "event_ids": ["ev-1"],
            "created_at": NOW,
        }
    ]
    episodes = [
        {
            "id": "ep-1",
            "type": "task_completed",
            "content": "puspa: successful run of the nightly job",
            "event_id": "ev-2",
            "created_at": NOW,
        },
        {
            "id": "ep-2",
            "type": "user_message",
            "content": "hello",
            "event_id": "ev-3",
            "created_at": NOW - timedelta(days=5),
        },
    ]
    entry = durable.build_daily_entry("user-1", NOW.date(), summaries=summaries, episodes=episodes)
    assert entry is not None
    assert "successful run" in entry["text"]
    assert "Completed:" in entry["text"]
    assert set(entry["source_event_ids"]) == {"ev-1", "ev-2"}


@pytest.mark.unit
def test_build_daily_entry_empty_day_returns_none():
    assert durable.build_daily_entry("user-1", NOW.date(), summaries=[], episodes=[]) is None


@pytest.mark.unit
def test_pinned_items_never_drop_corrections_commitments_goals():
    pinned = durable.pinned_items(_snapshot())
    sections = [item["section"] for item in pinned]
    assert sections == ["Corrections", "Commitments", "Standing Goals"]
    assert pinned[0]["text"] == "Lives in Karawang, not Tangerang"


@pytest.mark.unit
def test_memory_store_keeps_version_when_content_unchanged():
    store = durable.MemoryDurableStore()
    first = store.save_document("user-1", "# MEMORY.md\n", "hash-a", NOW, {})
    second = store.save_document("user-1", "# MEMORY.md\n", "hash-a", NOW, {})
    assert first["version"] == 1
    assert second["version"] == 1
    third = store.save_document("user-1", "# MEMORY.md\nchanged\n", "hash-b", NOW, {})
    assert third["version"] == 2


@pytest.mark.unit
def test_journal_entry_round_trip_in_memory_store():
    store = durable.MemoryDurableStore()
    entry = store.save_journal_entry("user-1", NOW.date(), "A good day.", ["ev-1"])
    assert store.get_journal_entry("user-1", NOW.date())["text"] == "A good day."
    assert store.list_journal_entries("user-1")[0]["id"] == entry["id"]


def _insert_summary(test_dsn, user_id, text, event_ids):
    with psycopg.connect(test_dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO summaries (user_id, period_start, period_end, summary, event_ids, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (user_id, NOW, NOW, text, event_ids, NOW),
            )
        conn.commit()


def test_postgres_store_round_trip(test_dsn):
    store = durable.PostgresDurableStore(dsn=test_dsn)
    user_id = str(uuid.uuid4())
    record = store.save_document(user_id, "# MEMORY.md\n", "hash-a", NOW, {"facts": 2})
    assert record["version"] == 1
    assert record["source_counts"] == {"facts": 2}
    assert store.get_document(user_id)["content"] == "# MEMORY.md\n"
    entry = store.save_journal_entry(user_id, NOW.date(), "A good day.", ["ev-1"])
    assert entry["entry_date"] == "2026-11-21"
    assert store.list_journal_entries(user_id)[0]["text"] == "A good day."


def test_rebuild_reads_real_facts_from_postgres(test_dsn):
    user_id = str(uuid.uuid4())
    fact_store = PostgresFactStore(dsn=test_dsn)
    record_fact(
        user_id,
        "goal:remote-work",
        "Land remote work",
        source="user_stated",
        store=fact_store,
        now=NOW,
    )
    record_fact(user_id, "city", "Karawang", source="user_stated", store=fact_store, now=NOW)
    record_fact(user_id, "hobby", "Plays chess", source="inferred", store=fact_store, now=NOW)
    first = durable.rebuild_memory(user_id, dsn=test_dsn, fact_store=fact_store, now=NOW)
    assert first["version"] == 1
    assert "Land remote work" in first["content"]
    assert "Karawang" in first["content"]
    assert "Plays chess (inferred, not yet confirmed)" in first["content"]
    assert first["counts"]["goals"] == 1
    assert first["counts"]["guesses"] == 1
    second = durable.rebuild_memory(user_id, dsn=test_dsn, fact_store=fact_store, now=NOW)
    assert second["content"] == first["content"]
    assert second["changed"] is False
    stored = durable.read_memory(user_id, dsn=test_dsn)
    assert stored["version"] == 1
    assert stored["content"] == first["content"]


def test_nightly_rebuild_writes_journal_and_document(test_dsn):
    user_id = str(uuid.uuid4())
    _insert_summary(
        test_dsn, user_id, "- [task_completed] puspa: successful run.", [str(uuid.uuid4())]
    )
    result = durable.run_nightly_rebuild(user_id, dsn=test_dsn, now=NOW)
    assert result["journal_entry"] is not None
    assert "successful run" in result["journal_entry"]["text"]
    assert "successful run" in result["content"]
    assert "## Journal" in result["content"]
    assert result["version"] == 1


def test_apply_edits_persists_to_postgres(test_dsn):
    user_id = str(uuid.uuid4())
    fact_store = PostgresFactStore(dsn=test_dsn)
    record_fact(user_id, "city", "Tangerang", source="user_stated", store=fact_store, now=NOW)
    built = durable.rebuild_memory(user_id, dsn=test_dsn, fact_store=fact_store, now=NOW)
    edited = built["content"].replace("Tangerang", "Karawang")
    result = durable.apply_user_edits(
        user_id, edited, previous_text=built["content"], fact_store=fact_store, now=NOW
    )
    assert len(result["applied"]) == 1
    history = fact_store.list_for_key(user_id, "city")
    active = [fact for fact in history if fact["status"] == "active"]
    assert [fact["content"] for fact in active] == ["Karawang"]
    rebuilt = durable.rebuild_memory(user_id, dsn=test_dsn, fact_store=fact_store, now=NOW)
    assert "Karawang" in rebuilt["content"]
    assert "Tangerang" not in rebuilt["content"]
