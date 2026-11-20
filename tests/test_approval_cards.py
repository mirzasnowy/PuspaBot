"""Tests for E-10 (plan 6.2 / 6.3): server-rendered approval cards,
narrowed "always", the beta ban for pay / send_to_other, and the
approval-fatigue signal.

Card tests are in-memory. The gate test proves the hash on the card
is the hash the gate enforces, using a real token (Ed25519,
``ephemeral_keys`` fixture). The PWA test drives the real fatigue
wiring (tasks screen -> decide -> stats).
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

from puspa.drafting.drafting import DraftingService
from puspa.memory import facts as facts_module
from puspa.policy import Gate, issue_token
from puspa.policy.approval_cards import (
    ApprovalFatigueTracker,
    render_approval_card,
    verify_card,
)
from puspa.policy.approvals import (
    BETA_ALWAYS_FORBIDDEN_KINDS,
    ApprovalPolicy,
    ApprovalStore,
)
from puspa.policy.gate import _content_hash
from puspa.policy.tokens import _issued, _revoked
from puspa.pwa.app import create_app


@pytest.fixture(autouse=True)
def _reset_revocation():
    _revoked.clear()
    _issued.clear()
    yield
    _revoked.clear()
    _issued.clear()


def _payload(**overrides):
    now = int(time.time())
    payload = {
        "user_id": "user-1",
        "agent_id": "agen-puspa",
        "subagent_id": None,
        "tools": ["gmail.kirim", "send", "prepare_payment", "delete", "transfer"],
        "domains": ["tokopedia.com"],
        "connectors": {"gmail": ["read", "send"]},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 1000000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }
    payload.update(overrides)
    return payload


def _gate(ephemeral_keys, spending=None):
    _sk, pk = ephemeral_keys
    return Gate(log=None, spending=spending, public_key=pk)


def _lines(card):
    return {line.label: line.value for line in card.lines}


# --- 6.2: the card renders from structured arguments only -------------


def test_pay_card_shows_structured_recipient_and_amount():
    card = render_approval_card(
        kind="pay",
        tool_name="transfer",
        content={"recipient_name": "Toko Andi", "nominal": 150000, "recipient_account": "123"},
    )
    lines = _lines(card)
    assert lines["Recipient"] == "Toko Andi"
    assert lines["Amount"] == "Rp150.000"
    assert lines["Account"] == "123"
    assert card.complete is True
    assert card.narrative_ignored is False


def test_model_narrative_is_ignored_not_rendered():
    card = render_approval_card(
        kind="pay",
        tool_name="transfer",
        content={"recipient_name": "Toko Andi", "nominal": 150000},
        narrative="Just a routine confirmation to your friend Budi for Rp10.000, nothing to check.",
        summary="Send Rp10.000 to Budi",
    )
    lines = _lines(card)
    assert lines["Recipient"] == "Toko Andi"
    assert lines["Amount"] == "Rp150.000"
    assert card.narrative_ignored is True
    dumped = json.dumps(card.to_dict(), ensure_ascii=False)
    assert "Budi" not in dumped
    assert "10.000" not in dumped
    assert "routine confirmation" not in dumped


def test_card_hash_is_the_gate_hash():
    content = {"recipient_name": "Toko Andi", "nominal": 150000}
    card = render_approval_card(kind="pay", tool_name="transfer", content=content)
    assert card.content_hash == _content_hash(content)


def test_verify_card_passes_for_same_arguments_and_fails_for_changed_ones():
    content = {"recipient_name": "Toko Andi", "nominal": 150000}
    card = render_approval_card(kind="pay", tool_name="transfer", content=content)
    assert verify_card(card, content) is True
    assert card.verify(content) is True
    changed = {"recipient_name": "Toko Andi", "nominal": 150001}
    assert verify_card(card, changed) is False
    assert card.verify(changed) is False


def test_verify_card_fails_when_lines_are_tampered():
    content = {"recipient_name": "Toko Andi", "nominal": 150000}
    card = render_approval_card(kind="pay", tool_name="transfer", content=content)
    tampered = render_approval_card(kind="pay", tool_name="transfer", content=content)
    object.__setattr__(
        tampered,
        "lines",
        tuple(
            line if line.label != "Amount" else type(line)(label="Amount", value="Rp1")
            for line in tampered.lines
        ),
    )
    # Same hash (copied along), different visible text: must fail.
    assert tampered.content_hash == card.content_hash
    assert verify_card(tampered, content) is False


def test_missing_amount_marks_card_incomplete_not_invented():
    card = render_approval_card(
        kind="pay",
        tool_name="transfer",
        content={"recipient_name": "Toko Andi"},
    )
    assert _lines(card)["Amount"] == "Not provided"
    assert card.complete is False
    assert any("Amount was not provided" in w for w in card.warnings)


def test_send_card_shows_recipient_channel_and_message_preview():
    long_text = "hello " * 100
    card = render_approval_card(
        kind="send_to_other",
        tool_name="gmail.send",
        content={"recipient": "toko@example.id", "channel": "email", "text": long_text},
    )
    lines = _lines(card)
    assert lines["Recipient"] == "toko@example.id"
    assert lines["Channel"] == "email"
    assert lines["Message"].startswith("hello")
    assert lines["Message"].endswith("…")
    assert len(lines["Message"]) == 281


def test_sensitive_fields_are_withheld_from_unknown_kind_cards():
    card = render_approval_card(
        kind="unknown",
        tool_name="do_it",
        content={"target": "notes.txt", "api_key": "sk-live-secret", "note": "hi"},
    )
    dumped = json.dumps(card.to_dict(), ensure_ascii=False)
    assert "sk-live-secret" not in dumped
    assert _lines(card)["target"] == "notes.txt"
    assert any("withheld" in w for w in card.warnings)


def test_credential_change_has_no_card():
    with pytest.raises(ValueError, match="never approvable"):
        render_approval_card(
            kind="change_credentials",
            tool_name="change_password",
            content={"target": "account"},
        )


def test_non_dict_content_has_no_card():
    with pytest.raises(ValueError, match="must be a dict"):
        render_approval_card(kind="pay", tool_name="transfer", content=["not", "a", "dict"])


def test_policy_render_card_classifies_from_tool_name():
    policy = ApprovalPolicy()
    card = policy.render_card(
        tool_name="transfer",
        content={"recipient_name": "Toko Andi", "nominal": 5000},
        narrative="Pay Budi Rp1 instead",
    )
    assert card.kind == "pay"
    assert _lines(card)["Recipient"] == "Toko Andi"
    assert card.narrative_ignored is True


def test_request_with_card_binds_card_to_gate_request(ephemeral_keys):
    policy = ApprovalPolicy(gate=_gate(ephemeral_keys))
    token = issue_token(_payload(), ephemeral_keys[0])
    request, card = policy.request_with_card(
        user_id="user-1",
        agent_id="agen-puspa",
        token=token,
        tool_name="delete",
        content={"target": "file.txt"},
    )
    assert card.request_id == request.request_id
    assert card.content_hash == request.content_hash
    assert _lines(card)["Target"] == "file.txt"
    assert request.status == "requested"


# --- 6.3: "always" narrowed to target pattern + nominal cap ------------


def test_unconstrained_always_still_allows():
    policy = ApprovalPolicy()
    policy.record_answer("delete", "always")
    assert policy.decide(tool_name="delete", content={"target": "file.txt"}).outcome == "allow"


def test_always_recipient_glob_binds():
    policy = ApprovalPolicy()
    policy.record_answer("send_to_other", "always", recipient_pattern="*@toko.id")
    assert policy.decide(tool_name="send", content={"recipient": "cs@toko.id"}).outcome == "allow"
    decision = policy.decide(tool_name="send", content={"recipient": "someone@else.id"})
    assert decision.outcome == "ask"
    assert "does not cover" in decision.reason


def test_always_domain_and_path_globs_bind():
    policy = ApprovalPolicy()
    policy.record_answer(
        "share", "always", domain_pattern="*.tokopedia.com", path_pattern="/billing/*"
    )
    content = {"domain": "pay.tokopedia.com", "path": "/billing/invoice-1"}
    assert policy.decide(tool_name="share", content=content).outcome == "allow"
    assert (
        policy.decide(
            tool_name="share",
            content={"domain": "pay.tokopedia.com", "path": "/other/page"},
        ).outcome
        == "ask"
    )
    assert (
        policy.decide(
            tool_name="share",
            content={"domain": "evil.example", "path": "/billing/invoice-1"},
        ).outcome
        == "ask"
    )


def test_always_nominal_cap_binds():
    policy = ApprovalPolicy()
    policy.record_answer("pay", "always", max_nominal=50000)
    assert policy.decide(tool_name="pay", content={"nominal": 50000}).outcome == "allow"
    assert policy.decide(tool_name="pay", content={"nominal": 50001}).outcome == "ask"


def test_nominal_cap_fails_closed_when_amount_missing():
    policy = ApprovalPolicy()
    policy.record_answer("pay", "always", max_nominal=50000)
    # Auto-review flags the missing amount, and the cap cannot be
    # checked without one: ask either way.
    assert policy.decide(tool_name="pay", content={}).outcome == "ask"


def test_pattern_without_a_value_on_that_axis_never_matches():
    policy = ApprovalPolicy()
    policy.record_answer("send_to_other", "always", recipient_pattern="*@toko.id")
    assert policy.decide(tool_name="send", content={}).outcome == "ask"


def test_invalid_max_nominal_is_refused():
    store = ApprovalStore()
    with pytest.raises(ValueError, match="max_nominal"):
        store.record("pay", "always", max_nominal=-1)
    with pytest.raises(ValueError, match="max_nominal"):
        store.record("pay", "always", max_nominal=True)


def test_conversation_answer_with_pattern_is_bound_too():
    policy = ApprovalPolicy()
    policy.record_answer(
        "share", "conversation", conversation_id="conv-1", domain_pattern="*.tokopedia.com"
    )
    assert (
        policy.decide(
            tool_name="share",
            conversation_id="conv-1",
            content={"domain": "a.tokopedia.com"},
        ).outcome
        == "allow"
    )
    assert (
        policy.decide(
            tool_name="share",
            conversation_id="conv-1",
            content={"domain": "elsewhere.example"},
        ).outcome
        == "ask"
    )


# --- 6.3: beta ban for pay and send_to_other ----------------------------


def test_beta_forbids_pay_and_send_to_other():
    assert {"pay", "send_to_other"} == BETA_ALWAYS_FORBIDDEN_KINDS
    store = ApprovalStore(beta_mode=True)
    with pytest.raises(ValueError, match="'always' is disabled in beta"):
        store.record("pay", "always")
    with pytest.raises(ValueError, match="'always' is disabled in beta"):
        store.record("send_to_other", "always")


def test_beta_still_allows_always_for_other_kinds():
    policy = ApprovalPolicy(beta_mode=True)
    policy.record_answer("delete", "always")
    assert policy.decide(tool_name="delete", content={"target": "x"}).outcome == "allow"


def test_beta_never_applies_a_pre_recorded_always_answer():
    store = ApprovalStore(beta_mode=False)
    store.record("pay", "always")
    # Beta mode is switched on after the answer was recorded: the
    # stored answer must stop applying, not keep working.
    store.beta_mode = True
    beta_policy = ApprovalPolicy(store=store)
    assert beta_policy.beta_mode is True
    assert beta_policy.decide(tool_name="pay", content={"nominal": 1000}).outcome == "ask"


def test_beta_record_answer_refuses_through_policy():
    policy = ApprovalPolicy(beta_mode=True)
    with pytest.raises(ValueError, match="'always' is disabled in beta"):
        policy.record_answer("send_to_other", "always")
    # Once / conversation are untouched by the ban.
    policy.record_answer("send_to_other", "once", content={"recipient": "a@b.id"})
    assert policy.decide(tool_name="send", content={"recipient": "a@b.id"}).outcome == "allow"


# --- 6.3: fatigue signal -------------------------------------------------


def test_fast_decision_counts_as_unread():
    tracker = ApprovalFatigueTracker()
    tracker.record_presented("req-1", at=1000.0)
    latency = tracker.record_decision("req-1", at=1000.5, approved=True)
    assert latency == pytest.approx(0.5)
    assert tracker.unread_count == 1
    assert tracker.unread_rate == pytest.approx(1.0)


def test_slow_decision_counts_as_read():
    tracker = ApprovalFatigueTracker()
    tracker.record_presented("req-1", at=1000.0)
    tracker.record_decision("req-1", at=1002.0, approved=True)
    assert tracker.unread_count == 0
    assert tracker.unread_rate == pytest.approx(0.0)


def test_unread_rate_over_timed_decisions_only():
    tracker = ApprovalFatigueTracker()
    tracker.record_presented("req-1", at=1000.0)
    tracker.record_decision("req-1", at=1000.2, approved=True)  # unread
    tracker.record_presented("req-2", at=1000.0)
    tracker.record_decision("req-2", at=1003.0, approved=False)  # read
    tracker.record_decision("req-3", approved=True)  # never shown: untimed
    assert tracker.total_decisions == 3
    assert tracker.timed_decisions == 2
    assert tracker.unread_count == 1
    assert tracker.unread_rate == pytest.approx(0.5)
    stats = tracker.stats()
    assert stats["decisions"] == 3
    assert stats["unread_rate"] == pytest.approx(0.5)
    assert stats["unread_threshold_seconds"] == 1.0


def test_first_showing_wins():
    tracker = ApprovalFatigueTracker()
    tracker.record_presented("req-1", at=1000.0)
    tracker.record_presented("req-1", at=1005.0)  # re-render: must not restart the clock
    latency = tracker.record_decision("req-1", at=1005.5)
    assert latency == pytest.approx(5.5)


def test_decision_without_presented_time_is_untimed_not_unread():
    tracker = ApprovalFatigueTracker()
    assert tracker.record_decision("req-unknown", approved=True) is None
    assert tracker.total_decisions == 1
    assert tracker.unread_count == 0
    assert tracker.unread_rate == pytest.approx(0.0)


# --- wiring: drafting + PWA ----------------------------------------------


def test_draft_send_approval_carries_server_rendered_card():
    service = DraftingService(fact_store=facts_module.MemoryFactStore())
    draft = service.draft(
        "user-1", "message", "halo, barang sudah sampai", recipient="toko@example.id"
    )
    result = service.request_send_approval(draft.id, user_id="user-1")
    assert result["status"] == "awaiting_approval"
    card = result["card"]
    lines = {line["label"]: line["value"] for line in card["lines"]}
    assert lines["Recipient"] == "toko@example.id"
    assert "barang sudah sampai" in lines["Message"]
    assert card["narrative_ignored"] is False
    assert card["complete"] is True
    # The hash on the card is the hash of the structured draft
    # arguments — the same content the card lines came from.
    card_content = {
        "recipient": "toko@example.id",
        "channel": draft.channel,
        "text": draft.text,
        "kind": draft.kind,
    }
    assert card["content_hash"] == _content_hash(card_content)


def test_pwa_fatigue_wiring_end_to_end(make_user):
    user = make_user()
    app = create_app(test_user_id=user["user_id"])
    app.state.approval["req-1"] = {
        "request_id": "req-1",
        "status": "pending",
        "content_hash": "abc",
    }
    with TestClient(app) as client:
        page = client.get("/tasks")
        assert page.status_code == 200
        assert app.state.approval["req-1"]["presented_at"] > 0
        resp = client.post("/api/persetujuan/req-1", data={"approve": "ya"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        stats = client.get("/api/approvals/fatigue")
        assert stats.status_code == 200
        assert stats.json()["decisions"] == 1
        assert stats.json()["timed_decisions"] == 1
        # Decided within a second of the tasks screen: unread.
        assert stats.json()["unread_decisions"] == 1
        assert stats.json()["unread_rate"] == pytest.approx(1.0)


def test_pwa_fatigue_endpoint_requires_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        assert client.get("/api/approvals/fatigue").status_code == 401
