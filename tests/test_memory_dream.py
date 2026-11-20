"""Tests for DREAM (G7). The LLM is always a stub; no live call."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from puspa.memory import dream
from puspa.memory.facts import MemoryFactStore


def _now():
    return datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _conv(cid, text, days_ago=0):
    return {
        "id": cid,
        "messages": [text],
        "created_at": _now() - timedelta(days=days_ago),
    }


def _stub_reply(payload):
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return json.dumps(payload)

    complete.calls = calls
    return complete


def test_dream_caps_and_confidence_floor():
    payload = {
        "learned": [
            {"key": f"topic{i}", "content": f"fact {i}", "confidence": 0.9} for i in range(8)
        ]
        + [{"key": "weak", "content": "shaky fact", "confidence": 0.5}],
        "expired": [
            {"key": f"old{i}", "content": f"old fact {i}", "confidence": 0.8} for i in range(7)
        ]
        + [{"key": "weakold", "content": "shaky old", "confidence": 0.69}],
        "ideas": [{"content": f"idea {i}", "confidence": 0.95} for i in range(5)],
    }
    stub = _stub_reply(payload)
    result = dream.run_dream(
        [_conv("a", "talk about coffee"), _conv("b", "talk about tea")],
        llm_complete=stub,
        now=_now(),
    )
    assert result["status"] == "ok"
    assert len(result["learned"]) == 6
    assert len(result["expired"]) == 5
    assert len(result["ideas"]) == 3
    assert all(item["confidence"] >= 0.7 for item in result["learned"])
    assert all(item["confidence"] >= 0.7 for item in result["expired"])
    assert all(item["confidence"] >= 0.7 for item in result["ideas"])
    assert "weak" not in [item["key"] for item in result["learned"]]
    assert result["conversations_used"] == 2
    assert len(stub.calls) == 1


def test_dream_too_few_conversations():
    stub = _stub_reply({"learned": [], "expired": [], "ideas": []})
    result = dream.run_dream([_conv("a", "only one")], llm_complete=stub, now=_now())
    assert result["status"] == "too_few"
    assert stub.calls == []


def test_dream_no_llm_is_skipped_not_faked():
    result = dream.run_dream([_conv("a", "one"), _conv("b", "two")], llm_complete=None, now=_now())
    assert result["status"] == "no_llm"
    assert result["learned"] == []


def test_dream_window_and_byte_budget():
    big = "x" * 30_000
    conversations = [
        _conv("old", "ancient talk", days_ago=10),
        _conv("big1", big),
        _conv("big2", big),
        _conv("small", "short talk"),
    ]
    stub = _stub_reply({"learned": [], "expired": [], "ideas": []})
    result = dream.run_dream(conversations, llm_complete=stub, now=_now())
    assert result["status"] == "ok"
    assert result["bytes_used"] <= dream.MAX_BYTES
    # The 10-day-old conversation is outside the window; the two
    # 30k conversations cannot both fit the 40k budget.
    assert result["conversations_used"] <= 2


def test_dream_max_conversations():
    conversations = [_conv(f"c{i}", f"talk {i}") for i in range(25)]
    stub = _stub_reply({"learned": [], "expired": [], "ideas": []})
    result = dream.run_dream(conversations, llm_complete=stub, now=_now())
    assert result["conversations_used"] <= dream.MAX_CONVERSATIONS


def test_dream_bad_reply_parses_to_empty():
    result = dream.run_dream(
        [_conv("a", "one"), _conv("b", "two")],
        llm_complete=lambda prompt: "not json at all",
        now=_now(),
    )
    assert result["status"] == "ok"
    assert result["learned"] == [] and result["expired"] == []
    assert result["ideas"] == []


class TestDreamJob:
    """Scheduler entry point over real episodic rows (Postgres).

    Integration: the autouse ``_schema`` fixture calls
    ``create_schema()``, which reads ``PUSPA_TEST_DSN`` from the
    environment — no fixture carries the DSN, so the marker split
    cannot see the database need (E-17).
    """

    @pytest.fixture(autouse=True)
    def _schema(self):
        from puspa.memory.schema import create_schema

        create_schema()
        yield

    @pytest.mark.integration
    def test_job_applies_learned_as_proposals_and_expires(self):
        from puspa.memory import episodic, facts

        user = str(uuid.uuid4())
        store = MemoryFactStore()
        old = facts.record_fact(user, "city", "Jakarta", store=store, trust_check=False, now=_now())
        assert old["status"] == "active"
        episodic.record_episode(user, "user moved to Bandung", "user_message", task_id=uuid.uuid4())
        episodic.record_episode(user, "Bandung is home now", "user_message", task_id=uuid.uuid4())
        payload = {
            "learned": [{"key": "city", "content": "user lives in Bandung", "confidence": 0.9}],
            "expired": [{"key": "city", "content": "user lives in Jakarta", "confidence": 0.8}],
            "ideas": [{"content": "visit Bandung cafes", "confidence": 0.9}],
        }
        result = dream.run_dream_job(
            user, llm_complete=_stub_reply(payload), store=store, now=_now()
        )
        assert result["status"] == "ok"
        assert result["applied"] == {"learned": 1, "expired": 1}
        assert store.get(old["id"], user)["status"] == "expired"
        proposals = store.list_user(user, statuses=("proposed",))
        assert len(proposals) == 1
        assert proposals[0]["content"] == "user lives in Bandung"
