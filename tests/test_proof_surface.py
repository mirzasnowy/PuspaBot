"""Tests for the trust / proof surface (B4).

Covers the module itself (``puspa.evidence.proof``): receipts
(hash matches the ledger event, chain position correct, a
tampered event fails verification), action classification
(money / send / other), evidence on answers (an answer without
evidence is flagged, never silently shown as proven), the B1
wiring (``record_outcome`` returns a proof block) and the B2
wiring (``outcome_with_proof``), and the audit-trail export
(only the requesting user's events, bounded and paginated).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN).
"""

from datetime import UTC, datetime

from puspa.db import connect as _db_connect
import pytest

from puspa.check_before_buy import (
    PriceObservation,
    RecordStore,
    SellerRecord,
    check_before_buy,
)
from puspa.check_before_buy.outcome import record_outcome
from puspa.evidence.outcomes import write_outcome
from puspa.evidence.proof import (
    MAX_PAGE_SIZE,
    Receipt,
    answer_proof,
    attach_proof,
    build_audit_trail_export,
    build_receipt,
    classify_receipt_action,
    describe_event,
    event_belongs_to_user,
    get_event,
    list_user_events,
    outcome_with_proof,
    verify_receipt,
)

LINK = "https://www.tokopedia.com/toko/galaxy-s24"


def _seeded_store() -> RecordStore:
    store = RecordStore()
    store.add_price_observation(
        PriceObservation(
            product_key=LINK,
            source="Tokopedia",
            price=12999000,
            source_url="https://www.tokopedia.com/toko/galaxy-s24",
            excerpt="[DATA: Galaxy S24 Rp12.999.000]",
        )
    )
    store.add_price_observation(
        PriceObservation(
            product_key=LINK,
            source="Shopee",
            price=12499000,
            source_url="https://shopee.co.id/galaxy-s24",
            excerpt="[DATA: Galaxy S24 Rp12.499.000]",
        )
    )
    store.add_seller_record(SellerRecord(name="Gadget Store", flagged=False, reports=12))
    return store


def _append(ledger, task_id, agent_id, event_type="task_created", content=None):
    return ledger.append_event(
        task_id=task_id,
        agent_id=agent_id,
        event_type=event_type,
        content=content or {"note": "test event"},
    )


# ---------------------------------------------------------------------------
# receipts
# ---------------------------------------------------------------------------


def test_receipt_matches_ledger_event(ledger, make_user):
    user = make_user()
    event = _append(ledger, "task-proof-1", user["agent_id"])
    receipt = build_receipt(ledger, event["id"])
    assert receipt is not None
    assert receipt.event_id == event["id"]
    assert receipt.event_hash == event["hash"]
    assert receipt.previous_hash == "GENESIS"
    assert receipt.chain_position == 1
    assert receipt.chain_length == 1
    assert receipt.chain_ok is True
    assert receipt.verified is True
    assert receipt.summary.startswith("Task created")
    assert receipt.action_kind == "other"
    data = receipt.to_dict()
    assert data["event_hash"] == event["hash"]
    assert data["chain_position"] == 1


def test_receipt_chain_position_counts_within_task(ledger, make_user):
    user = make_user()
    first = _append(ledger, "task-proof-2", user["agent_id"])
    second = _append(ledger, "task-proof-2", user["agent_id"], event_type="plan_created")
    _append(ledger, "task-proof-2", user["agent_id"], event_type="task_completed")
    receipt = build_receipt(ledger, second["id"])
    assert receipt.chain_position == 2
    assert receipt.chain_length == 3
    assert receipt.previous_hash == first["hash"]
    assert build_receipt(ledger, first["id"]).chain_position == 1


def test_receipt_unknown_event_is_none(ledger):
    assert build_receipt(ledger, "00000000-0000-0000-0000-000000000000") is None
    assert get_event(ledger.dsn, "not-a-uuid") is None


def test_tampered_event_fails_verification(ledger, make_user, test_dsn):
    user = make_user()
    event = _append(ledger, "task-proof-3", user["agent_id"])
    receipt = build_receipt(ledger, event["id"])
    assert receipt.verified is True
    ok, _reasons = verify_receipt(ledger, receipt)
    assert ok is True
    # Tamper with the stored event after the receipt was built.
    with _db_connect(test_dsn) as conn:
        conn.execute(
            "UPDATE events SET content = %s WHERE id = %s",
            ('{"note": "tampered"}', event["id"]),
        )
        conn.commit()
    stale = build_receipt(ledger, event["id"])
    assert stale.verified is False
    assert stale.chain_ok is False
    ok, reasons = verify_receipt(ledger, receipt)
    assert ok is False
    assert reasons, "a tampered event must fail with at least one reason"


def test_verify_receipt_unknown_event_fails(ledger):
    receipt = Receipt(
        event_id="00000000-0000-0000-0000-000000000000",
        task_id="task-proof-x",
        event_type="task_created",
        summary="Task created",
        action_kind="other",
        created_at=datetime.now(UTC),
        valid_at=None,
        event_hash="0" * 64,
        previous_hash="GENESIS",
        chain_position=1,
        chain_length=1,
        chain_ok=True,
        verified=True,
    )
    ok, reasons = verify_receipt(ledger, receipt)
    assert ok is False
    assert reasons == ["event no longer exists in the ledger"]


# ---------------------------------------------------------------------------
# action classification
# ---------------------------------------------------------------------------


def test_money_and_send_classification():
    pay = {"type": "approval_granted", "content": {"action_kind": "pay"}}
    assert classify_receipt_action(pay) == "money"
    send = {"type": "approval_granted", "content": {"action_kind": "send_to_other"}}
    assert classify_receipt_action(send) == "send"
    gate = {"type": "approval_requested", "content": {"gate_kind": "prepare_payment"}}
    assert classify_receipt_action(gate) == "money"
    outcome = {
        "type": "evidence_recorded",
        "content": {"kind": "outcome_record", "outcome_type": "payment_prepared"},
    }
    assert classify_receipt_action(outcome) == "money"
    tool = {"type": "tool_called", "content": {"tool": "gmail.send"}}
    assert classify_receipt_action(tool) == "send"
    tool_pay = {"type": "tool_called", "content": {"tool": "transfer"}}
    assert classify_receipt_action(tool_pay) == "money"
    plain = {"type": "user_message", "content": {"text": "hello"}}
    assert classify_receipt_action(plain) == "other"
    assert classify_receipt_action({"type": "task_created", "content": None}) == "other"


def test_describe_event_falls_back_for_unknown_type():
    assert describe_event({"type": "nope", "content": {}}) == "Event recorded"
    event = {
        "type": "approval_granted",
        "content": {"summary": "payment of Rp50.000 approved"},
    }
    assert describe_event(event) == "Approval granted: payment of Rp50.000 approved"


# ---------------------------------------------------------------------------
# evidence on answers
# ---------------------------------------------------------------------------


def test_attach_proof_with_evidence_is_proven():
    answer = {"text": "fair price Rp12.499.000", "advice": "act"}
    evidence = [{"kind": "evidence_record", "evidence_id": "bukti-abc"}]
    result = attach_proof(answer, evidence)
    assert result["proof"]["proven"] is True
    assert result["proof"]["missing_evidence"] == []
    assert result["proof"]["evidence"] == evidence
    assert result["text"] == "fair price Rp12.499.000"


def test_attach_proof_without_evidence_is_flagged_not_proven():
    answer = {"text": "I could not verify this", "advice": "cannot_advise"}
    result = attach_proof(answer, [])
    assert result["proof"]["proven"] is False
    assert result["proof"]["missing_evidence"] == ["no evidence attached to this answer"]
    # A receipt alone does not make an answer proven: a hash proves
    # the event happened, not that the claim behind it is true.
    receipt = {
        "event_id": "00000000-0000-0000-0000-000000000001",
        "event_hash": "a" * 64,
    }
    result = attach_proof(answer, [], receipt)
    assert result["proof"]["proven"] is False
    assert result["proof"]["receipt"] == receipt


def test_attach_proof_rejects_bad_input():
    with pytest.raises(ValueError):
        attach_proof("not a dict", [])
    with pytest.raises(ValueError):
        attach_proof({}, ["not a dict"])
    with pytest.raises(ValueError):
        attach_proof({}, [], receipt="not a receipt")


def test_b1_record_outcome_returns_proof(ledger, make_user, test_dsn):
    user = make_user()
    answer = check_before_buy(f"{LINK} toko: Gadget Store", store=_seeded_store())
    assert answer.advice == "act"
    result = record_outcome(
        answer,
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        task_id="task-proof-b1",
        dsn=test_dsn,
    )
    proof = result["proof"]
    assert proof["proven"] is True
    assert len(proof["evidence"]) == 2
    assert proof["receipt"]["event_hash"] == result["ledger_event"]["hash"]
    assert proof["receipt"]["chain_position"] == 1
    assert proof["receipt"]["chain_length"] == 1
    assert proof["receipt"]["verified"] is True
    assert proof["missing_evidence"] == []


def test_b1_unverified_answer_is_flagged(make_user, test_dsn):
    user = make_user()
    answer = check_before_buy("some random text", store=RecordStore())
    assert answer.advice == "cannot_advise"
    result = record_outcome(
        answer,
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        task_id="task-proof-b1-unverified",
        dsn=test_dsn,
    )
    proof = result["proof"]
    assert proof["proven"] is False
    assert proof["evidence"] == []
    assert any("fair_price" in note for note in proof["missing_evidence"])
    assert any("seller" in note for note in proof["missing_evidence"])


def test_answer_proof_without_ledger_has_unknown_position(make_user, test_dsn):
    user = make_user()
    answer = check_before_buy(f"{LINK} toko: Gadget Store", store=_seeded_store())
    result = record_outcome(
        answer,
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        task_id="task-proof-b1-noleger",
        dsn=test_dsn,
    )
    proof = answer_proof(answer, result["ledger_event"])
    assert proof["receipt"]["chain_position"] is None
    assert proof["receipt"]["verified"] is True


def test_b2_outcome_with_proof(ledger, make_user):
    user = make_user()
    record = write_outcome(
        ledger,
        task_id="task-proof-b2",
        agent_id=user["agent_id"],
        outcome_type="price_checked",
        user_id=user["user_id"],
        summary="checked the fair price",
        evidence=[{"kind": "evidence_record", "evidence_id": "bukti-xyz"}],
        sources=["https://www.tokopedia.com/toko/galaxy-s24"],
    )
    result = outcome_with_proof(record, ledger)
    assert result["proof"]["proven"] is True
    assert result["proof"]["receipt"]["event_hash"] == record.event_hash
    assert result["proof"]["receipt"]["event_id"] == record.outcome_id
    assert result["proof"]["receipt"]["verified"] is True


# ---------------------------------------------------------------------------
# audit-trail export
# ---------------------------------------------------------------------------


def _seed_events(ledger, user, task_prefix, count):
    for index in range(count):
        _append(ledger, f"{task_prefix}-{index}", user["agent_id"])


def test_export_contains_only_requesting_user_events(ledger, make_user, test_dsn):
    alice = make_user()
    bob = make_user()
    _seed_events(ledger, alice, "task-alice", 3)
    _seed_events(ledger, bob, "task-bob", 2)
    document = build_audit_trail_export(alice["user_id"], dsn=test_dsn)
    assert document["format"] == "puspa-audit-trail"
    assert document["version"] == 1
    assert document["pagination"]["total"] == 3
    assert document["counts"]["total_events"] == 3
    assert len(document["events"]) == 3
    for entry in document["events"]:
        assert entry["event"]["agent_id"] == alice["agent_id"]
        assert entry["receipt"]["event_hash"] == entry["event"]["hash"]
        assert entry["receipt"]["verified"] is True
    assert document["excluded"], "the export must name what it leaves out"


def test_export_paginates(ledger, make_user, test_dsn):
    user = make_user()
    _seed_events(ledger, user, "task-page", 5)
    first = build_audit_trail_export(user["user_id"], dsn=test_dsn, limit=2, offset=0)
    assert first["pagination"] == {
        "total": 5,
        "limit": 2,
        "offset": 0,
        "returned": 2,
    }
    second = build_audit_trail_export(user["user_id"], dsn=test_dsn, limit=2, offset=2)
    assert second["pagination"]["returned"] == 2
    third = build_audit_trail_export(user["user_id"], dsn=test_dsn, limit=2, offset=4)
    assert third["pagination"]["returned"] == 1
    ids = (
        [entry["event"]["id"] for entry in first["events"]]
        + [entry["event"]["id"] for entry in second["events"]]
        + [entry["event"]["id"] for entry in third["events"]]
    )
    assert len(set(ids)) == 5, "pages must not repeat or drop events"


def test_export_limit_clamps_to_max(ledger, make_user, test_dsn):
    user = make_user()
    _seed_events(ledger, user, "task-clamp", 2)
    document = build_audit_trail_export(user["user_id"], dsn=test_dsn, limit=10_000_000)
    assert document["pagination"]["limit"] == MAX_PAGE_SIZE
    assert document["pagination"]["returned"] == 2


def test_export_rejects_unknown_user_and_bad_paging(test_dsn):
    with pytest.raises(ValueError):
        build_audit_trail_export("no-such-user", dsn=test_dsn)
    with pytest.raises(ValueError):
        build_audit_trail_export("", dsn=test_dsn)
    user_id = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(ValueError):
        build_audit_trail_export(user_id, dsn=test_dsn, limit=0)
    with pytest.raises(ValueError):
        build_audit_trail_export(user_id, dsn=test_dsn, offset=-1)


def test_list_user_events_counts_and_ownership(ledger, make_user, test_dsn):
    alice = make_user()
    bob = make_user()
    event = _append(ledger, "task-own", alice["agent_id"])
    _append(ledger, "task-own-bob", bob["agent_id"])
    events, total = list_user_events(alice["user_id"], dsn=test_dsn)
    assert total == 1
    assert [item["id"] for item in events] == [event["id"]]
    assert event_belongs_to_user(event, alice["user_id"], dsn=test_dsn) is True
    assert event_belongs_to_user(event, bob["user_id"], dsn=test_dsn) is False
