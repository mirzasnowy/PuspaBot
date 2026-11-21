"""Tests for the anti-manipulation design rule (Kosmos K13).

Four layers, matching the wiring:

- Pure detector rules (in-memory, deterministic, no database):
  the five conflict kinds, the no-conflict cases, severity,
  and input validation.
- Store + gate (in-memory): the audit log, the finality of a
  user choice, the fail-closed ``require_resolved``, the
  ledger log mapping (existing event types only, no 32nd
  type), and the surface payload.
- Turn-loop wiring (fake model, no network, no database): the
  loop surfaces a seller-vs-user conflict instead of running
  the tool, records the decision, and runs the tool only after
  the user chose to proceed. A broken gate fails open.
- Postgres persistence (real Postgres, DSN from env
  ``PUSPA_TEST_DSN`` — never a hardcoded database name, so
  the file works in any worktree's test database): the
  migration is idempotent, a record round-trips through the
  table, a resolution persists, and each test cleans up its
  own rows. Each DB test skips itself when Postgres is not
  reachable.
- Docs checklist: ``docs/anti-manipulation.md`` carries a
  review row for every implemented K item (K1, K3, K4, K5,
  K6, K10, K11, K12).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import psycopg
import pytest

from puspa.ledger import EVENT_TYPES
from puspa.policy.conflicts import (
    CONFLICT_KINDS,
    STATUS_RESOLVED_ALTERNATIVE,
    STATUS_RESOLVED_DECLINED,
    STATUS_RESOLVED_PROCEED,
    STATUS_SURFACED,
    ConflictGate,
    ConflictSpec,
    ConflictStore,
    ProposedAction,
    UnresolvedConflictError,
    conflict_surface_payload,
    delete_conflicts,
    detect_conflict,
    load_conflicts,
    surface_text,
)
from puspa.policy.conflicts import (
    create_schema as create_conflict_schema,
)
from puspa.turns.config import TurnConfig
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply

REPO_ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")

#: The concrete scenario used through the file: Puspa's price
#: comparison recommends a product whose seller gains more
#: than the user saves. The user's reflective goal is to pay
#: the lowest price; the recommendation costs Rp25.000 more
#: than the cheapest option.
SELLER_SPEC = ConflictSpec(
    beneficiary="seller",
    user_goal="pay the lowest price",
    goal_alignment=0.3,
    other_benefit=0.8,
    user_cost="Rp25.000 more than the cheapest option",
    description="recommend the sponsored listing for this product",
)


def _seller_action() -> ProposedAction:
    return SELLER_SPEC.proposed_action("recommend_product")


def _dsn_available() -> bool:
    try:
        with psycopg.connect(DSN, connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Detector: the five kinds
# ---------------------------------------------------------------------------


def test_seller_vs_user_when_the_seller_gains_more_than_the_user():
    conflict = detect_conflict(_seller_action())
    assert conflict is not None
    assert conflict.kind == "seller_vs_user"
    assert conflict.beneficiary == "seller"
    assert conflict.tool_name == "recommend_product"
    assert conflict.status == STATUS_SURFACED
    # A money cost makes it high severity.
    assert conflict.severity == "high"
    assert "seller" in conflict.other_interest
    assert "lowest price" in conflict.user_interest


def test_provider_vs_user_for_a_paid_tier_push():
    conflict = detect_conflict(
        ProposedAction(
            tool_name="upgrade_plan",
            description="push the paid tier",
            beneficiary="provider",
            user_goal="keep costs down",
            goal_alignment=0.2,
            other_benefit=0.9,
        )
    )
    assert conflict is not None
    assert conflict.kind == "provider_vs_user"


def test_other_persons_gain_is_a_conflict_too():
    conflict = detect_conflict(
        ProposedAction(
            tool_name="suggest_reply",
            description="suggest a reply that flatters the other person",
            beneficiary="other_person",
            user_goal="be honest with friends",
            goal_alignment=0.2,
            other_benefit=0.7,
        )
    )
    assert conflict is not None
    assert conflict.kind == "seller_vs_user"
    assert conflict.beneficiary == "other_person"


def test_engagement_vs_goal_when_the_action_keeps_the_user_around():
    conflict = detect_conflict(
        ProposedAction(
            tool_name="suggest_more_videos",
            description="suggest another video to watch",
            beneficiary="user",
            user_goal="stop scrolling and sleep",
            goal_alignment=0.2,
            engagement_gain=0.9,
        )
    )
    assert conflict is not None
    assert conflict.kind == "engagement_vs_goal"
    assert conflict.severity == "low"


def test_retention_vs_independence_when_the_method_is_hidden():
    conflict = detect_conflict(
        ProposedAction(
            tool_name="write_report",
            description="write the report without showing how",
            beneficiary="user",
            user_goal="learn to write reports myself",
            goal_alignment=0.6,
            engagement_gain=0.6,
            hides_method=True,
        )
    )
    assert conflict is not None
    assert conflict.kind == "retention_vs_independence"


def test_attention_vs_benefit_for_a_proactive_ping_that_serves_the_system():
    conflict = detect_conflict(
        ProposedAction(
            tool_name="send_suggestion",
            description="ping the user with an unsolicited suggestion",
            beneficiary="system",
            user_goal="stay focused on work",
            goal_alignment=0.3,
            other_benefit=0.8,
            proactive=True,
        )
    )
    assert conflict is not None
    assert conflict.kind == "attention_vs_benefit"


def test_no_conflict_when_the_user_gains_most():
    assert (
        detect_conflict(
            ProposedAction(
                tool_name="recommend_product",
                description="recommend the cheapest option",
                beneficiary="seller",
                user_goal="pay the lowest price",
                goal_alignment=0.9,
                other_benefit=0.2,
            )
        )
        is None
    )


def test_no_conflict_for_a_plain_helpful_action():
    assert (
        detect_conflict(
            ProposedAction(
                tool_name="search_prices",
                description="search prices for the user",
                beneficiary="user",
                user_goal="pay the lowest price",
                goal_alignment=0.9,
            )
        )
        is None
    )


def test_low_severity_without_a_money_cost_or_a_wide_gap():
    conflict = detect_conflict(
        ProposedAction(
            tool_name="recommend_product",
            description="recommend a slightly sponsored listing",
            beneficiary="seller",
            user_goal="pay the lowest price",
            goal_alignment=0.4,
            other_benefit=0.6,
        )
    )
    assert conflict is not None
    assert conflict.severity == "low"


def test_detector_validates_its_inputs():
    with pytest.raises(ValueError, match="unknown beneficiary"):
        ProposedAction(tool_name="x", beneficiary="advertiser")
    with pytest.raises(ValueError, match="goal_alignment must be within"):
        ProposedAction(tool_name="x", goal_alignment=1.5)
    with pytest.raises(ValueError, match="other_benefit must be within"):
        ProposedAction(tool_name="x", other_benefit=-0.1)


def test_surface_text_names_both_sides_and_the_cost():
    conflict = detect_conflict(_seller_action())
    assert conflict is not None
    text = surface_text(conflict)
    assert "seller" in text
    assert "lowest price" in text
    assert "Rp25.000" in text
    assert "instead of deciding for you" in text
    assert "go ahead anyway" in text


# ---------------------------------------------------------------------------
# Store: audit log + finality
# ---------------------------------------------------------------------------


def test_store_records_and_lists_with_a_summary():
    store = ConflictStore()
    conflict = detect_conflict(_seller_action())
    assert conflict is not None
    store.record(conflict)
    assert store.get(conflict.conflict_id) is conflict
    assert store.unresolved() == [conflict]
    assert store.latest_for("recommend_product", "seller_vs_user") is conflict
    summary = store.summary()
    assert summary["total"] == 1
    assert summary["unresolved"] == 1
    assert summary["by_kind"] == {"seller_vs_user": 1}
    assert summary["by_status"] == {STATUS_SURFACED: 1}


def test_store_resolve_is_final_and_cannot_be_rewritten():
    store = ConflictStore()
    conflict = detect_conflict(_seller_action())
    assert conflict is not None
    store.record(conflict)
    resolved = store.resolve(conflict.conflict_id, "alternative")
    assert resolved.status == STATUS_RESOLVED_ALTERNATIVE
    assert resolved.user_choice == "alternative"
    assert resolved.resolved_at is not None
    assert resolved.resolved is True
    assert store.unresolved() == []
    with pytest.raises(ValueError, match="already resolved"):
        store.resolve(conflict.conflict_id, "proceed")


def test_store_rejects_bad_choices_unknown_ids_and_non_surfaced_records():
    store = ConflictStore()
    conflict = detect_conflict(_seller_action())
    assert conflict is not None
    store.record(conflict)
    with pytest.raises(ValueError, match="unknown user choice"):
        store.resolve(conflict.conflict_id, "maybe")
    with pytest.raises(KeyError, match="unknown conflict id"):
        store.resolve("no-such-id", "proceed")
    with pytest.raises(KeyError, match="unknown conflict id"):
        store.resolve("no-such-id", "decline")
    resolved = detect_conflict(_seller_action())
    assert resolved is not None
    resolved.status = STATUS_RESOLVED_PROCEED
    with pytest.raises(ValueError, match="only a surfaced conflict"):
        store.record(resolved)


# ---------------------------------------------------------------------------
# Gate: enforcement
# ---------------------------------------------------------------------------


def test_gate_proceeds_without_a_conflict_and_without_a_spec():
    gate = ConflictGate()
    assert gate.check(_seller_action()).outcome == "surface"
    assert gate.check_tool("search_prices").outcome == "proceed"
    assert (
        gate.check(ProposedAction(tool_name="search_prices", goal_alignment=0.9)).outcome
        == "proceed"
    )


def test_gate_surfaces_the_same_record_until_the_user_chooses():
    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC})
    first = gate.check_tool("recommend_product")
    second = gate.check_tool("recommend_product")
    assert first.outcome == "surface"
    assert second.outcome == "surface"
    assert first.conflict is not None and second.conflict is not None
    assert first.conflict.conflict_id == second.conflict.conflict_id
    assert len(gate.store.list_records()) == 1, "no duplicate record while unresolved"


def test_gate_proceeds_after_the_user_chose_to_proceed():
    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC})
    decision = gate.check_tool("recommend_product")
    assert decision.conflict is not None
    gate.record_user_choice(decision.conflict.conflict_id, "proceed")
    again = gate.check_tool("recommend_product")
    assert again.outcome == "proceed"
    assert again.conflict is None


def test_gate_surfaces_again_after_a_decline_instead_of_silently_allowing():
    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC})
    decision = gate.check_tool("recommend_product")
    assert decision.conflict is not None
    gate.record_user_choice(decision.conflict.conflict_id, "decline")
    again = gate.check_tool("recommend_product")
    assert again.outcome == "surface"
    assert again.conflict is not None
    assert again.conflict.conflict_id != decision.conflict.conflict_id
    assert len(gate.store.list_records()) == 2
    assert gate.store.summary()["by_status"][STATUS_RESOLVED_DECLINED] == 1


def test_require_resolved_is_fail_closed_until_the_user_chooses():
    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC})
    decision = gate.check_tool("recommend_product")
    assert decision.conflict is not None
    conflict_id = decision.conflict.conflict_id
    assert gate.may_run("recommend_product", "seller_vs_user") is False
    with pytest.raises(UnresolvedConflictError, match="has not chosen yet"):
        gate.require_resolved(conflict_id)
    gate.record_user_choice(conflict_id, "proceed")
    assert gate.require_resolved(conflict_id).status == STATUS_RESOLVED_PROCEED
    assert gate.may_run("recommend_product", "seller_vs_user") is True
    with pytest.raises(KeyError, match="unknown conflict id"):
        gate.require_resolved("no-such-id")


def test_gate_logs_through_the_existing_ledger_event_types_only():
    events: list[tuple[str, dict]] = []

    def log(event_type: str, content: dict) -> None:
        events.append((event_type, content))

    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC}, log=log)
    decision = gate.check_tool("recommend_product")
    assert decision.conflict is not None
    gate.record_user_choice(decision.conflict.conflict_id, "proceed")
    assert [event for event, _ in events] == ["approval_requested", "approval_granted"]
    for event, _ in events:
        assert event in EVENT_TYPES, f"{event} is not a closed ledger event type"
    assert events[0][1]["conflict_kind"] == "seller_vs_user"
    assert events[0][1]["tool"] == "recommend_product"

    events.clear()
    gate2 = ConflictGate(specs={"recommend_product": SELLER_SPEC}, log=log)
    decision2 = gate2.check_tool("recommend_product")
    assert decision2.conflict is not None
    gate2.record_user_choice(decision2.conflict.conflict_id, "decline")
    assert [event for event, _ in events] == ["approval_requested", "approval_rejected"]
    assert events[1][1]["user_choice"] == "decline"


def test_surface_payload_names_the_conflict_and_says_the_tool_did_not_run():
    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC})
    decision = gate.check_tool("recommend_product")
    payload = json.loads(conflict_surface_payload(decision, tool_name="recommend_product"))
    assert payload["conflict_decision"] == "surface"
    assert payload["tool"] == "recommend_product"
    assert payload["tool_was_run"] is False
    assert payload["conflict_kind"] == "seller_vs_user"
    assert payload["beneficiary"] == "seller"
    assert payload["severity"] == "high"
    assert "seller" in payload["surfaced_text"]
    assert payload["choices"] == ["proceed", "alternative", "decline"]
    assert decision.conflict is not None
    assert payload["conflict_id"] == decision.conflict.conflict_id


def test_surface_payload_rejects_a_proceed_decision():
    gate = ConflictGate()
    decision = gate.check_tool("search_prices")
    assert decision.outcome == "proceed"
    with pytest.raises(ValueError, match="only for a 'surface' decision"):
        conflict_surface_payload(decision, tool_name="search_prices")


def test_spec_validates_at_wiring_time():
    with pytest.raises(ValueError, match="unknown beneficiary"):
        ConflictSpec(beneficiary="advertiser")
    with pytest.raises(ValueError, match="other_benefit must be within"):
        ConflictSpec(other_benefit=2.0)


# ---------------------------------------------------------------------------
# Turn-loop wiring: real runs with a fake model
# ---------------------------------------------------------------------------


def _tool_reply(name, arguments=None):
    return ModelReply(
        text="",
        tool_calls=[{"id": "call-1", "name": name, "arguments": arguments or {}}],
        input_tokens=10,
        output_tokens=5,
    )


def _text_reply(text):
    return ModelReply(text=text, input_tokens=10, output_tokens=5)


def _run_loop(gate, tool_name, *, message="find me the cheapest laptop"):
    """Run one turn where the model calls ``tool_name`` once, then answers."""
    calls = {"count": 0, "registry": []}

    def model_call(entry, wire_messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return _tool_reply(tool_name)
        return _text_reply("done")

    def run_registry(name, arguments):
        calls["registry"].append((name, arguments))
        return {"ok": True}

    loop = TurnLoop(
        model_call=model_call,
        tool_call=run_registry,
        config=TurnConfig(max_iterations=10),
        conflict_gate=gate,
    )
    result = loop.run_turn([InternalMessage.user(message)])
    return result, calls, loop


def _tool_results(result):
    return [
        block.get("content", "")
        for message in result.messages
        for block in message.blocks
        if block.get("type") == "tool_result"
    ]


def test_loop_surfaces_the_seller_conflict_and_does_not_run_the_tool():
    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC})
    result, calls, loop = _run_loop(gate, "recommend_product")
    assert result.status == "completed"
    # The conflict scenario, end to end: the tool never ran.
    assert calls["registry"] == [], "a conflicted tool must not run before the user chooses"
    assert len(result.conflict_decisions) == 1
    decision = result.conflict_decisions[0]
    assert decision.outcome == "surface"
    assert decision.conflict is not None
    assert decision.conflict.kind == "seller_vs_user"
    payload = json.loads(_tool_results(result)[0])
    assert payload["conflict_decision"] == "surface"
    assert payload["tool_was_run"] is False
    assert "seller" in payload["surfaced_text"]
    assert "lowest price" in payload["surfaced_text"]
    events = [e for e in loop.event_memory() if e["type"] == "conflict_surfaced"]
    assert len(events) == 1
    assert events[0]["content"]["tool"] == "recommend_product"
    assert events[0]["content"]["conflict_kind"] == "seller_vs_user"
    assert gate.store.unresolved(), "the surfaced conflict waits on the user"


def test_loop_runs_the_tool_after_the_user_chose_to_proceed():
    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC})
    result, calls, _loop = _run_loop(gate, "recommend_product")
    assert calls["registry"] == []
    assert result.conflict_decisions[0].conflict is not None
    conflict_id = result.conflict_decisions[0].conflict.conflict_id
    gate.record_user_choice(conflict_id, "proceed")
    result2, calls2, _loop2 = _run_loop(gate, "recommend_product")
    assert result2.status == "completed"
    assert calls2["registry"] == [("recommend_product", {})]
    assert result2.conflict_decisions[0].outcome == "proceed"


def test_loop_runs_a_tool_with_no_conflict_spec_unchanged():
    gate = ConflictGate(specs={"recommend_product": SELLER_SPEC})
    result, calls, _loop = _run_loop(gate, "search_prices")
    assert result.status == "completed"
    assert calls["registry"] == [("search_prices", {})]
    assert result.conflict_decisions[0].outcome == "proceed"


def test_loop_without_a_gate_behaves_exactly_as_before():
    result, calls, _loop = _run_loop(None, "recommend_product")
    assert result.status == "completed"
    assert calls["registry"] == [("recommend_product", {})]
    assert result.conflict_decisions == []


def test_loop_fails_open_when_the_gate_breaks():
    class BrokenGate:
        def check_tool(self, tool_name, **kwargs):
            raise RuntimeError("gate is down")

    result, calls, _loop = _run_loop(BrokenGate(), "recommend_product")
    assert result.status == "completed"
    assert calls["registry"] == [("recommend_product", {})]


def test_loop_surfaces_even_when_the_dial_would_run_silently():
    """The gate runs after the dial: a do-silently area never
    silently runs a conflicted action."""
    from puspa.autonomy.dial import AutonomyDial

    dial = AutonomyDial()
    for _ in range(24):
        dial.record_outcome("scheduling", True)
    assert dial.stage("scheduling") == "do_silently"
    gate = ConflictGate(
        specs={
            "schedule_reminder": ConflictSpec(
                beneficiary="system",
                user_goal="stay focused on work",
                goal_alignment=0.3,
                other_benefit=0.8,
                engagement_gain=0.9,
                proactive=True,
            )
        }
    )
    calls = {"count": 0, "registry": []}

    def model_call(entry, wire_messages, tools):
        calls["count"] += 1
        if calls["count"] == 1:
            return _tool_reply("schedule_reminder")
        return _text_reply("done")

    def run_registry(name, arguments):
        calls["registry"].append((name, arguments))
        return {"ok": True}

    loop = TurnLoop(
        model_call=model_call,
        tool_call=run_registry,
        config=TurnConfig(max_iterations=10),
        autonomy_dial=dial,
        conflict_gate=gate,
    )
    result = loop.run_turn([InternalMessage.user("remind me")])
    assert result.status == "completed"
    assert result.dial_decisions[0].stage == "do_silently"
    assert calls["registry"] == [], "a conflict beats do-silently"
    assert result.conflict_decisions[0].outcome == "surface"


# ---------------------------------------------------------------------------
# Postgres persistence
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _dsn_available(), reason="Postgres is not reachable")
def test_migration_is_idempotent_and_round_trips_a_record(make_user):
    create_conflict_schema(DSN)
    create_conflict_schema(DSN)
    user = make_user()
    try:
        store = ConflictStore(dsn=DSN)
        conflict = detect_conflict(_seller_action())
        assert conflict is not None
        store.record(conflict, user_id=user["user_id"], agent_id=user["agent_id"])
        loaded = load_conflicts(user["user_id"], user["agent_id"], dsn=DSN)
        assert len(loaded) == 1
        assert loaded[0].conflict_id == conflict.conflict_id
        assert loaded[0].kind == "seller_vs_user"
        assert loaded[0].tool_name == "recommend_product"
        assert loaded[0].status == STATUS_SURFACED
        assert loaded[0].severity == "high"
        assert "seller" in loaded[0].other_interest
        store.resolve(conflict.conflict_id, "alternative")
        loaded = load_conflicts(
            user["user_id"], user["agent_id"], dsn=DSN, status=STATUS_RESOLVED_ALTERNATIVE
        )
        assert len(loaded) == 1
        assert loaded[0].user_choice == "alternative"
        assert loaded[0].resolved_at is not None
        assert (
            load_conflicts(user["user_id"], user["agent_id"], dsn=DSN, status=STATUS_SURFACED) == []
        )
    finally:
        delete_conflicts(user["user_id"], user["agent_id"], dsn=DSN)


@pytest.mark.skipif(not _dsn_available(), reason="Postgres is not reachable")
def test_persisting_without_user_or_agent_ids_raises(make_user):
    create_conflict_schema(DSN)
    user = make_user()
    try:
        store = ConflictStore(dsn=DSN)
        conflict = detect_conflict(_seller_action())
        assert conflict is not None
        with pytest.raises(ValueError, match="needs user_id and agent_id"):
            store.record(conflict)
        assert store.list_records() == []
    finally:
        delete_conflicts(user["user_id"], user["agent_id"], dsn=DSN)


# ---------------------------------------------------------------------------
# Docs: the review checklist (acceptance criterion 4)
# ---------------------------------------------------------------------------


def test_docs_checklist_covers_every_implemented_k_item():
    doc = (REPO_ROOT / "docs" / "anti-manipulation.md").read_text(encoding="utf-8")
    assert "## Review checklist" in doc
    checklist = doc.split("## Review checklist", 1)[1]
    for item in (
        "intent engine",
        "act-or-ask rule",
        "bitemporal state",
        "causal ledger",
        "other-agent goal models",
        "ignorance map",
        "interruption budget",
        "autonomy dial",
        "kill switch and export",
    ):
        assert item in checklist, f"checklist is missing a review row for {item}"
    for kind in CONFLICT_KINDS:
        assert kind in doc, f"docs never name conflict kind {kind}"
