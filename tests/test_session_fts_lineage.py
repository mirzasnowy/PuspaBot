"""FTS5 recall, compression lineage, and the G19 seam (G26).

FTS5 search reuses G7's safe MATCH query builder; the index itself
lives in ``state.db`` and is kept in sync by triggers. Lineage is
recorded when a compression births a child session.
"""

from __future__ import annotations

from puspa.runtime.context_compression import SUMMARY_MARKER
from puspa.sessions import open_persistence
from puspa.sessions.lineage import (
    adopt_compression_result,
    find_summary_message,
    record_compression,
)


def _seed(gateway, session_id):
    gateway.append_messages(
        session_id,
        [
            ("user", "how do I pay my PLN electricity bill this month"),
            ("assistant", "open the bill page and check the PLN meter number"),
            ("user", "what is the cheapest laptop for programming"),
            ("assistant", "compare prices across Tokopedia and Shopee first"),
        ],
    )


def test_fts_finds_message_by_keyword(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("bills")
        _seed(gateway, session["id"])
        hits = gateway.search_messages("PLN electricity bill")
        assert hits
        assert hits[0]["content"].startswith("how do I pay my PLN")
        assert all("PLN" in hit["content"] or "bill" in hit["content"] for hit in hits)


def test_fts_ranks_best_match_first(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("rank")
        gateway.append_message(session["id"], "user", "laptop laptop laptop")
        gateway.append_message(session["id"], "user", "a laptop mention")
        hits = gateway.search_messages("laptop")
        assert [h["content"] for h in hits] == [
            "laptop laptop laptop",
            "a laptop mention",
        ]
        assert hits[0]["bm25"] <= hits[1]["bm25"]


def test_fts_no_match_returns_empty(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("empty")
        _seed(gateway, session["id"])
        assert gateway.search_messages("zebra quantum") == []
        assert gateway.search_messages("!!!") == []


def test_fts_query_punctuation_cannot_break_syntax(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("punct")
        _seed(gateway, session["id"])
        # Unbalanced quotes / FTS5 operators must not raise.
        hits = gateway.search_messages('"PLN OR "electricity" *')
        assert isinstance(hits, list)


def test_fts_search_can_be_scoped_to_one_session(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        first = gateway.create_session("first")
        second = gateway.create_session("second")
        gateway.append_message(first["id"], "user", "PLN bill here")
        gateway.append_message(second["id"], "user", "PLN bill there")
        scoped = gateway.search_messages("PLN", session_id=second["id"])
        assert len(scoped) == 1
        assert scoped[0]["session_id"] == second["id"]


def test_fts_survives_reopen_because_it_lives_in_state_db(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("persist")
        gateway.append_message(session["id"], "user", "PLN bill reminder")
    with open_persistence(home=tmp_path) as gateway:
        hits = gateway.search_messages("PLN bill")
        assert len(hits) == 1


def test_fts_verify_and_rebuild(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        session = gateway.create_session("drift")
        gateway.append_message(session["id"], "user", "PLN bill reminder")
        assert gateway.store.verify_fts()["ok"]
        # Simulate drift: delete one index row by hand.
        gateway.store._conn.execute("DELETE FROM messages_fts WHERE rowid = 1")
        gateway.store._conn.commit()
        report = gateway.store.verify_fts()
        assert not report["ok"]
        assert report["messages"] == 1 and report["indexed"] == 0
        gateway.store.rebuild_fts()
        assert gateway.store.verify_fts()["ok"]
        assert len(gateway.search_messages("PLN")) == 1


def test_compression_lineage_parent_child(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        parent = gateway.create_session("parent")
        gateway.append_message(parent["id"], "user", "long conversation here")
        child = record_compression(
            gateway,
            parent_session_id=parent["id"],
            summary="summary of the long conversation",
            reason="context limit reached",
        )
        assert child["parent_id"] == parent["id"]
        assert child["messages"][0]["role"] == "assistant"
        assert "summary of the long conversation" in child["messages"][0]["content"]
        assert gateway.children(parent["id"])[0]["child_session_id"] == child["id"]
        chain = gateway.lineage_chain(child["id"])
        assert [s["id"] for s in chain] == [child["id"], parent["id"]]


def test_lineage_chain_walks_grandparents(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        first = gateway.create_session("first")
        second = record_compression(gateway, parent_session_id=first["id"], summary="s1")
        third = record_compression(gateway, parent_session_id=second["id"], summary="s2")
        chain = gateway.lineage_chain(third["id"])
        assert [s["id"] for s in chain] == [third["id"], second["id"], first["id"]]


def test_adopt_compression_result_only_when_compressed(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        parent = gateway.create_session("parent")

        class FakeResult:
            def __init__(self, messages, compressed):
                self.messages = messages
                self.stats = {"compressed": compressed}

        not_compressed = FakeResult([{"role": "user", "content": "hi"}], False)
        assert (
            adopt_compression_result(gateway, parent_session_id=parent["id"], result=not_compressed)
            is None
        )
        assert gateway.children(parent["id"]) == []

        compressed = FakeResult(
            [
                {"role": "assistant", "content": f"{SUMMARY_MARKER}\nthe summary"},
                {"role": "user", "content": "later question"},
            ],
            True,
        )
        child = adopt_compression_result(gateway, parent_session_id=parent["id"], result=compressed)
        assert child is not None
        assert child["parent_id"] == parent["id"]
        assert child["messages"][0]["content"].startswith(SUMMARY_MARKER)


def test_adopt_compression_result_accepts_a_mapping(tmp_path):
    with open_persistence(home=tmp_path) as gateway:
        parent = gateway.create_session("parent")
        child = adopt_compression_result(
            gateway,
            parent_session_id=parent["id"],
            result={
                "messages": [
                    {"role": "assistant", "content": f"{SUMMARY_MARKER}\nsummary"},
                ],
                "stats": {"compressed": True},
            },
        )
        assert child is not None


def test_lineage_marker_matches_g19_compressor():
    from puspa.sessions.lineage import SUMMARY_MARKER as SESSION_MARKER

    assert SESSION_MARKER == SUMMARY_MARKER
    assert (
        find_summary_message([{"role": "assistant", "content": f"{SUMMARY_MARKER}\ntext"}])
        == f"{SUMMARY_MARKER}\ntext"
    )
    assert find_summary_message([{"role": "user", "content": "no marker"}]) is None
