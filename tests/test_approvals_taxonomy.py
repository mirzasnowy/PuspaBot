"""Tests for the approval taxonomy (G4).

In-memory only; the Gate integration tests at the end use a real
token (Ed25519, ``ephemeral_keys`` fixture) to prove the taxonomy
layer does not break the gate's content-hash binding or its 24-hour
expiry — the gate itself is unchanged.
"""

import time

import pytest

from puspa.policy import Gate, GateDenied, SpendingLimit, issue_token
from puspa.policy.approvals import (
    ACTION_KINDS,
    ANSWER_TTL_SECONDS,
    ASKING_GUIDANCE_MARKER,
    GATE_KIND_FOR_ACTION,
    ApprovalPolicy,
    ApprovalRule,
    ApprovalStore,
    GuidanceState,
    auto_review,
    classify_action,
    classify_gate_kind,
    is_asking_guidance,
    is_credential_change,
    is_destructive,
    mark_asking_guidance,
    mark_destructive,
)
from puspa.policy.tokens import _issued, _revoked


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


# --- recognition: tool name first verb + button label, never content ---


def test_delete_recognized_from_tool_verb():
    assert classify_action("delete").kind == "delete"
    assert classify_action("remove_file").kind == "delete"
    assert classify_action("gmail.delete").kind == "delete"


def test_send_recognized_from_namespaced_tool():
    assert classify_action("gmail.send").kind == "send_to_other"
    assert classify_action("send").kind == "send_to_other"
    assert classify_action("gmail.kirim").kind == "send_to_other"


def test_share_and_post_recognized():
    assert classify_action("share").kind == "share"
    assert classify_action("publish_post").kind == "post"
    assert classify_action("post").kind == "post"


def test_button_label_recognizes_action():
    assert classify_action("do_it", button_label="Delete").kind == "delete"
    assert classify_action("do_it", button_label="Share").kind == "share"
    assert classify_action("unknown_tool", button_label="Send").kind == "send_to_other"


def test_unrecognized_action_has_no_kind():
    classification = classify_action("do_it")
    assert classification.kind is None
    assert classification.recognized is False


def test_page_payment_word_is_a_signal_but_other_content_is_not():
    # The word "payment" on the page IS a specified payment signal.
    assert classify_action("read_listing", page_text="pay now, payment due").kind == "pay"
    # Any other wording in the content is never read: a page that
    # says "delete" does not make a read action a delete action.
    assert classify_action("read_listing", page_text="please delete this file").kind is None
    assert classify_action("read_listing").kind is None


# --- payment recognition: every signal ---


@pytest.mark.parametrize(
    "tool_name",
    ["transfer", "payout", "order", "payroll", "subscription", "plan_upgrade", "upgrade_plan"],
)
def test_payment_recognized_from_tool_name(tool_name):
    assert classify_action(tool_name).kind == "pay"


def test_payment_recognized_from_word_payment_anywhere():
    assert classify_action("open_page", page_text="payment methods").kind == "pay"
    assert classify_action("payment_status").kind == "pay"
    assert classify_action("check", button_label="Payment options").kind == "pay"


@pytest.mark.parametrize(
    "label",
    ["Transfer", "Withdraw", "Pay", "Buy", "Place your order", "Subscribe"],
)
def test_payment_recognized_from_button_label(label):
    assert classify_action("do_it", button_label=label).kind == "pay"


def test_payment_button_labels_are_case_insensitive():
    assert classify_action("do_it", button_label="TRANSFER").kind == "pay"
    assert classify_action("do_it", button_label="place your order").kind == "pay"


def test_pay_maps_to_prepare_payment_gate_kind():
    assert classify_gate_kind("transfer") == "prepare_payment"
    assert classify_gate_kind("payout") == "prepare_payment"
    assert classify_gate_kind("delete") == "delete"
    assert classify_gate_kind("echo_read") is None
    assert classify_gate_kind("run_code") is None


# --- credential change: never approvable ---


@pytest.mark.parametrize(
    "tool_name",
    [
        "change_password",
        "update_password",
        "reset_password",
        "change_credentials",
        "rotate_secret",
        "update_api_key",
        "change_login",
    ],
)
def test_credential_change_recognized(tool_name):
    assert is_credential_change(tool_name) is True
    assert classify_action(tool_name).kind == "change_credentials"


def test_credential_change_in_button_label():
    assert is_credential_change("do_it", button_label="Change password") is True


def test_credential_change_never_approvable_for_any_answer():
    policy = ApprovalPolicy()
    policy.record_answer("change_credentials", "always")
    decision = policy.decide(tool_name="change_password")
    assert decision.outcome == "escalate_to_human"
    # Even an admin "ask every time" rule does not downgrade it to ask.
    policy.add_rule(ApprovalRule(kind="change_credentials", owner="admin", mode="ask_every_time"))
    assert policy.decide(tool_name="change_password").outcome == "escalate_to_human"


def test_credential_change_request_raises_at_policy(ephemeral_keys):
    policy = ApprovalPolicy(gate=_gate(ephemeral_keys))
    token = issue_token(_payload(), ephemeral_keys[0])
    with pytest.raises(GateDenied, match="human must do it"):
        policy.request(
            user_id="user-1",
            agent_id="agen-puspa",
            token=token,
            tool_name="change_password",
            content={"target": "account"},
        )


# --- tiered answers + 24h expiry ---


def test_default_decision_is_ask():
    policy = ApprovalPolicy()
    decision = policy.decide(tool_name="delete", content={"target": "file.txt"})
    assert decision.outcome == "ask"


def test_always_answer_allows():
    policy = ApprovalPolicy()
    policy.record_answer("delete", "always")
    decision = policy.decide(tool_name="delete", content={"target": "file.txt"})
    assert decision.outcome == "allow"
    assert decision.answer_scope == "always"


def test_once_answer_binds_to_exact_content():
    policy = ApprovalPolicy()
    content = {"target": "file.txt"}
    policy.record_answer("delete", "once", content=content)
    assert policy.decide(tool_name="delete", content=content).outcome == "allow"
    # Same kind, different content: the "once" answer does not apply.
    assert policy.decide(tool_name="delete", content={"target": "other.txt"}).outcome == "ask"


def test_conversation_answer_scoped_to_conversation():
    policy = ApprovalPolicy()
    policy.record_answer("share", "conversation", conversation_id="conv-1")
    assert policy.decide(tool_name="share", conversation_id="conv-1").outcome == "allow"
    assert policy.decide(tool_name="share", conversation_id="conv-2").outcome == "ask"
    assert policy.decide(tool_name="share").outcome == "ask"


def test_answers_expire_after_24_hours():
    store = ApprovalStore()
    old = time.time() - ANSWER_TTL_SECONDS - 1
    store.record("delete", "always", granted_at=old)
    policy = ApprovalPolicy(store=store)
    assert policy.decide(tool_name="delete", content={"target": "x"}).outcome == "ask"
    assert store.purge_expired() == 1


def test_once_answer_consumed_after_use(ephemeral_keys):
    policy = ApprovalPolicy(gate=_gate(ephemeral_keys))
    token = issue_token(_payload(), ephemeral_keys[0])
    content = {"target": "file.txt"}
    policy.record_answer("delete", "once", content=content)
    first = policy.request(
        user_id="user-1",
        agent_id="agen-puspa",
        token=token,
        tool_name="delete",
        content=content,
    )
    assert first.status == "approved"  # approved at once from the stored answer
    # Second identical request: the "once" answer is spent, so it asks.
    decision = policy.decide(tool_name="delete", content=content)
    assert decision.outcome == "ask"


# --- strictest wins ---


def test_admin_ask_every_time_beats_always_answer():
    policy = ApprovalPolicy()
    policy.record_answer("delete", "always")
    policy.add_rule(ApprovalRule(kind="delete", owner="admin", mode="ask_every_time"))
    decision = policy.decide(tool_name="delete", content={"target": "x"})
    assert decision.outcome == "ask"
    assert decision.matched_rule is not None
    assert decision.matched_rule.owner == "admin"


def test_members_self_serve_denies_even_with_always_answer():
    policy = ApprovalPolicy()
    policy.record_answer("post", "always")
    policy.add_rule(ApprovalRule(kind="post", owner="admin", mode="members_self_serve"))
    decision = policy.decide(tool_name="post")
    assert decision.outcome == "deny"
    assert "members do it themselves" in decision.reason


def test_conversation_rule_cannot_loosen_admin_rule():
    policy = ApprovalPolicy()
    policy.record_answer("pay", "always")
    policy.add_rule(ApprovalRule(kind="pay", owner="admin", mode="ask_every_time"))
    policy.add_rule(ApprovalRule(kind="pay", owner="conversation", mode="ask_every_time"))
    assert policy.decide(tool_name="pay", content={"nominal": 1000}).outcome == "ask"


def test_extra_vigilant_forces_ask():
    policy = ApprovalPolicy(extra_vigilant=True)
    policy.record_answer("delete", "always")
    assert policy.decide(tool_name="delete", content={"target": "x"}).outcome == "ask"
    policy.extra_vigilant = False
    assert policy.decide(tool_name="delete", content={"target": "x"}).outcome == "allow"


def test_checkin_task_asks_before_every_change():
    policy = ApprovalPolicy()
    policy.record_answer("share", "always")
    decision = policy.decide(tool_name="share", from_checkin=True)
    assert decision.outcome == "ask"
    assert "check-in" in decision.reason


# --- auto-review ---


def test_auto_review_flags_unrecognized_action():
    classification = classify_action("do_it")
    findings = auto_review(classification, {})
    assert [f.code for f in findings] == ["unrecognized_action"]


def test_auto_review_flags_payment_without_amount():
    classification = classify_action("transfer")
    findings = auto_review(classification, {})
    assert any(f.code == "payment_without_amount" for f in findings)
    decision = ApprovalPolicy().decide(tool_name="transfer", content={})
    assert decision.outcome == "ask"
    assert "payment without a nominal amount" in decision.reason


def test_auto_review_passes_payment_with_amount_but_still_asks_by_default():
    classification = classify_action("transfer")
    assert auto_review(classification, {"nominal": 5000}) == []
    # No finding, no answer: the kind always asks by default.
    assert ApprovalPolicy().decide(tool_name="transfer", content={"nominal": 5000}).outcome == "ask"


# --- anti-double-approval ---


def test_asking_guidance_marker():
    message = mark_asking_guidance("Approve this payment?")
    assert ASKING_GUIDANCE_MARKER in message
    assert is_asking_guidance(message) is True
    assert is_asking_guidance("Approve this payment?") is False
    assert mark_asking_guidance(message) == message  # idempotent


def test_guidance_state_blocks_own_approval_card():
    state = GuidanceState()
    assert state.should_place_approval_card() is True
    state.set_asking()
    assert state.should_place_approval_card() is False
    state.clear()
    assert state.should_place_approval_card() is True


def test_destructive_tool_marked_in_description():
    description = mark_destructive("Deletes a file.")
    assert is_destructive(description) is True
    assert is_destructive("Deletes a file.") is False
    assert mark_destructive(description) == description  # idempotent


# --- gate integration: hash binding + 24h expiry still hold ---


def test_policy_request_files_pending_when_asking(ephemeral_keys):
    policy = ApprovalPolicy(gate=_gate(ephemeral_keys))
    token = issue_token(_payload(), ephemeral_keys[0])
    request = policy.request(
        user_id="user-1",
        agent_id="agen-puspa",
        token=token,
        tool_name="delete",
        content={"target": "file.txt"},
    )
    assert request.status == "requested"


def test_gate_hash_binding_still_voids_changed_content(ephemeral_keys):
    gate = _gate(ephemeral_keys)
    token = issue_token(_payload(), ephemeral_keys[0])
    request = gate.request_approval(
        user_id="user-1",
        agent_id="agen-puspa",
        token=token,
        kind="delete",
        content={"target": "file.txt"},
    )
    gate.approve(request.request_id, "user-1")
    request.content["target"] = "other.txt"  # tampered after approval
    with pytest.raises(GateDenied, match="content changed after approval"):
        gate.execute(request.request_id, executor=lambda content: None)


def test_gate_24h_expiry_still_denies(ephemeral_keys):
    gate = _gate(ephemeral_keys)
    token = issue_token(_payload(exp=int(time.time()) - 10), ephemeral_keys[0])
    with pytest.raises(GateDenied, match="kedaluwarsa"):
        gate.request_approval(
            user_id="user-1",
            agent_id="agen-puspa",
            token=token,
            kind="send",
            content={"recipient": "a@example.id"},
        )


def test_policy_request_executes_payment_through_gate(ephemeral_keys):
    spending = SpendingLimit("user-g4-payment")
    spending.set_limit(100000)
    gate = _gate(ephemeral_keys, spending=spending)
    policy = ApprovalPolicy(gate=gate)
    token = issue_token(_payload(user_id="user-g4-payment"), ephemeral_keys[0])
    request = policy.request(
        user_id="user-g4-payment",
        agent_id="agen-puspa",
        token=token,
        tool_name="transfer",
        content={"nominal": 5000},
    )
    assert request.kind == "prepare_payment"
    gate.approve(request.request_id, "user-g4-payment")
    calls = []
    result = gate.execute(request.request_id, executor=calls.append)
    assert result is None
    assert calls == [{"nominal": 5000}]


# --- install kind (G30: skill install through the G4 store) ---


def test_install_is_a_first_class_kind():
    assert "install" in ACTION_KINDS
    # A local enablement, never an outbound action: no Gate kind,
    # the same pattern as change_credentials.
    assert GATE_KIND_FOR_ACTION["install"] is None


def test_install_recognized_from_tool_verb():
    found = classify_action("marketplace.install")
    assert found.kind == "install"
    assert found.recognized is True


def test_install_recognized_from_button_label():
    found = classify_action("do_it", button_label="Install")
    assert found.kind == "install"
    assert found.recognized is True


def test_install_has_no_gate_kind():
    assert classify_gate_kind("marketplace.install") is None


def test_install_decision_is_ask_by_default():
    policy = ApprovalPolicy()
    decision = policy.decide(
        tool_name="marketplace.install",
        content={"skill_slug": "react-best-practices", "sha256": "abc123"},
    )
    assert decision.outcome == "ask"
    assert decision.kind == "install"


def test_install_once_answer_binds_to_exact_content():
    policy = ApprovalPolicy()
    content = {"skill_slug": "react-best-practices", "sha256": "abc123"}
    policy.record_answer("install", "once", content=content)
    assert policy.decide(tool_name="marketplace.install", content=content).outcome == "allow"
    # Same skill, different content hash: the "once" answer does not apply.
    changed = {"skill_slug": "react-best-practices", "sha256": "def456"}
    assert policy.decide(tool_name="marketplace.install", content=changed).outcome == "ask"


def test_install_always_answer_allows():
    policy = ApprovalPolicy()
    policy.record_answer("install", "always")
    decision = policy.decide(
        tool_name="marketplace.install",
        content={"skill_slug": "react-best-practices", "sha256": "abc123"},
    )
    assert decision.outcome == "allow"
    assert decision.answer_scope == "always"


def test_install_request_raises_at_policy(ephemeral_keys):
    # Install never reaches the outbound Gate: the policy request
    # path files with the gate, and there is no gate kind for it.
    policy = ApprovalPolicy(gate=_gate(ephemeral_keys))
    token = issue_token(_payload(), ephemeral_keys[0])
    with pytest.raises(GateDenied, match="no gate kind"):
        policy.request(
            user_id="user-1",
            agent_id="agen-puspa",
            token=token,
            tool_name="marketplace.install",
            content={"skill_slug": "react-best-practices", "sha256": "abc123"},
        )
