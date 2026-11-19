"""Shared splitting / patungan (B11).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers.

Money is integer sen everywhere (1 rupiah = 100 sen): the
helpers use ``rupiah_to_cents`` so the test amounts read as
Rupiah (Rp300.000 -> 30_000_000 sen).
"""

from datetime import UTC, datetime, timedelta

import pytest

from puspa.ledger import Ledger
from puspa.rooms import MoneyGate, SplitService
from puspa.rooms.errors import MoneyApprovalError, NotRoomMemberError, SplitError
from puspa.rooms.splitting import (
    cents_to_rupiah,
    equal_shares,
    format_rupiah,
    register_reminder_job,
    reminder_job_prompt,
    render_split,
    run_reminder_sweep,
    rupiah_to_cents,
    settlement_action_id,
    split_reminder_lines,
    weighted_shares,
)
from puspa.scheduler.cron import CronJobStore, cronjob_manage
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]


@pytest.fixture
def splits(test_dsn):
    return SplitService(test_dsn)


@pytest.fixture
def gate(test_dsn):
    return MoneyGate(test_dsn)


@pytest.fixture
def trio(make_room, add_room_member):
    """A room with owner + two members. Returns dict with ids."""
    made = make_room()
    room = made["room"]
    owner = made["owner"]["user_id"]
    second = add_room_member(room.id, owner)["user_id"]
    third = add_room_member(room.id, owner)["user_id"]
    return {"room": room, "owner": owner, "second": second, "third": third}


def _meal(splits, trio, total_rupiah=300_000, **kwargs):
    return splits.create_split(
        trio["room"].id,
        "Dinner together",
        rupiah_to_cents(total_rupiah),
        trio["owner"],
        [trio["owner"], trio["second"], trio["third"]],
        created_by=trio["owner"],
        kind="meal",
        **kwargs,
    )


# --- money helpers: integer sen, never floats -------------------------


def test_money_helpers_are_integer_sen_only():
    assert rupiah_to_cents(150_000) == 15_000_000
    assert cents_to_rupiah(15_000_000) == 150_000
    assert format_rupiah(15_000_000) == "Rp150.000"
    assert format_rupiah(15_000_050) == "Rp150.000,50"
    assert format_rupiah(0) == "Rp0"
    with pytest.raises(SplitError):
        cents_to_rupiah(15_000_001)  # not a whole number of rupiah
    for bad in (150_000.5, "150000", None, True):
        with pytest.raises(SplitError):
            rupiah_to_cents(bad)
        with pytest.raises(SplitError):
            format_rupiah(bad)


def test_equal_shares_distribute_leftover_sen_to_earliest():
    assert equal_shares(100_001, 3) == [33_334, 33_334, 33_333]
    assert equal_shares(10, 3) == [4, 3, 3]
    assert sum(equal_shares(999_999, 7)) == 999_999
    with pytest.raises(SplitError):
        equal_shares(0, 3)
    with pytest.raises(SplitError):
        equal_shares(100.5, 3)


def test_weighted_shares_use_largest_remainder():
    assert weighted_shares(100_000, [2, 1, 1]) == [50_000, 25_000, 25_000]
    # Floors [16, 33, 50], one sen left, largest remainder is the
    # first weight (100*1 % 6 == 4), so it gets the extra sen.
    assert weighted_shares(100, [1, 2, 3]) == [17, 33, 50]
    assert sum(weighted_shares(999, [3, 2, 1])) == 999
    with pytest.raises(SplitError):
        weighted_shares(100, [1, 0, 1])
    with pytest.raises(SplitError):
        weighted_shares(100, [])


# --- creating a split ---------------------------------------------------


def test_equal_split_payer_settled_others_owe(splits, trio):
    split = _meal(splits, trio)
    assert split.status == "open"
    assert split.kind == "meal"
    assert split.total_cents == 30_000_000
    shares = splits.list_shares(split.id, viewer_user_id=trio["owner"])
    assert [s.share_cents for s in shares] == [10_000_000] * 3
    assert [s.user_id for s in shares] == [trio["owner"], trio["second"], trio["third"]]
    payer = shares[0]
    assert payer.paid_cents == 10_000_000 and payer.remaining_cents == 0
    assert shares[1].paid_cents == 0 and shares[1].remaining_cents == 10_000_000
    debtors = splits.list_debtors(split.id, viewer_user_id=trio["owner"])
    assert [d.user_id for d in debtors] == [trio["second"], trio["third"]]


def test_custom_shares_must_sum_to_total(splits, trio):
    room_id = trio["room"].id
    split = splits.create_split(
        room_id,
        "Gift together",
        rupiah_to_cents(500_000),
        trio["owner"],
        [trio["owner"], trio["second"], trio["third"]],
        created_by=trio["owner"],
        kind="gift",
        shares={
            trio["owner"]: rupiah_to_cents(100_000),
            trio["second"]: rupiah_to_cents(150_000),
            trio["third"]: rupiah_to_cents(250_000),
        },
    )
    shares = splits.list_shares(split.id, viewer_user_id=trio["owner"])
    assert [s.share_cents for s in shares] == [
        rupiah_to_cents(100_000),
        rupiah_to_cents(150_000),
        rupiah_to_cents(250_000),
    ]
    with pytest.raises(SplitError):
        splits.create_split(
            room_id,
            "Bad gift",
            rupiah_to_cents(500_000),
            trio["owner"],
            [trio["owner"], trio["second"], trio["third"]],
            created_by=trio["owner"],
            shares={trio["owner"]: 1, trio["second"]: 1, trio["third"]: 1},
        )
    with pytest.raises(SplitError):  # shares must cover exactly the participants
        splits.create_split(
            room_id,
            "Bad gift",
            rupiah_to_cents(500_000),
            trio["owner"],
            [trio["owner"], trio["second"], trio["third"]],
            created_by=trio["owner"],
            shares={trio["owner"]: rupiah_to_cents(500_000)},
        )


def test_weighted_split(splits, trio):
    split = _meal(splits, trio, total_rupiah=400_000, weights=[2, 1, 1])
    shares = splits.list_shares(split.id, viewer_user_id=trio["owner"])
    assert [s.share_cents for s in shares] == [
        rupiah_to_cents(200_000),
        rupiah_to_cents(100_000),
        rupiah_to_cents(100_000),
    ]


def test_arisan_round_is_one_equal_split(splits, trio):
    # One arisan round: the member who collects the pot is the
    # payer; everyone else owes one equal share to them.
    split = splits.create_split(
        trio["room"].id,
        "Arisan round 3",
        rupiah_to_cents(300_000),
        trio["second"],
        [trio["owner"], trio["second"], trio["third"]],
        created_by=trio["owner"],
        kind="arisan",
    )
    shares = splits.list_shares(split.id, viewer_user_id=trio["owner"])
    assert [s.share_cents for s in shares] == [rupiah_to_cents(100_000)] * 3
    assert shares[1].remaining_cents == 0  # the collector's own share
    assert [d.user_id for d in splits.list_debtors(split.id, viewer_user_id=trio["owner"])] == [
        trio["owner"],
        trio["third"],
    ]


def test_create_split_validation(splits, trio, make_user, add_room_member):
    room_id = trio["room"].id
    outsider = make_user()["user_id"]
    guest = add_room_member(room_id, trio["owner"], role="guest")["user_id"]
    base = {
        "room_id": room_id,
        "title": "Dinner",
        "total_cents": rupiah_to_cents(300_000),
        "payer_user_id": trio["owner"],
        "participant_user_ids": [trio["owner"], trio["second"], trio["third"]],
        "created_by": trio["owner"],
    }
    with pytest.raises(SplitError):  # payer must be a participant
        splits.create_split(**{**base, "participant_user_ids": [trio["second"], trio["third"]]})
    with pytest.raises(SplitError):  # at least two participants
        splits.create_split(**{**base, "participant_user_ids": [trio["owner"]]})
    with pytest.raises(SplitError):  # duplicate participant
        splits.create_split(
            **{**base, "participant_user_ids": [trio["owner"], trio["second"], trio["second"]]}
        )
    with pytest.raises(SplitError):  # empty title
        splits.create_split(**{**base, "title": "  "})
    with pytest.raises(SplitError):  # unknown kind
        splits.create_split(**{**base, "kind": "lottery"})
    with pytest.raises(SplitError):  # non-positive / non-int total
        splits.create_split(**{**base, "total_cents": 0})
    with pytest.raises(SplitError):
        splits.create_split(**{**base, "total_cents": 100.5})
    with pytest.raises(SplitError):  # shares and weights together
        splits.create_split(**{**base, "shares": {}, "weights": [1, 1, 1]})
    with pytest.raises(NotRoomMemberError):  # non-member creator
        splits.create_split(**{**base, "created_by": outsider})
    with pytest.raises(NotRoomMemberError):  # guest may not front a split
        splits.create_split(
            **{**base, "payer_user_id": guest, "participant_user_ids": [guest, trio["second"]]}
        )
    with pytest.raises(NotRoomMemberError):  # guest may not take part
        splits.create_split(
            **{**base, "participant_user_ids": [trio["owner"], trio["second"], guest]}
        )


def test_split_reads_reject_non_members(splits, trio, make_user):
    split = _meal(splits, trio)
    outsider = make_user()["user_id"]
    with pytest.raises(NotRoomMemberError):
        splits.list_splits(trio["room"].id, viewer_user_id=outsider)
    with pytest.raises(NotRoomMemberError):
        splits.list_shares(split.id, viewer_user_id=outsider)
    assert splits.get_split("00000000-0000-0000-0000-000000000000") is None
    with pytest.raises(SplitError):
        splits.require_split("00000000-0000-0000-0000-000000000000")


# --- partial payments -----------------------------------------------------


def test_partial_payments_track_to_settled(splits, trio):
    split = _meal(splits, trio)
    assert split.status == "open"
    half = rupiah_to_cents(50_000)
    payment = splits.record_payment(split.id, trio["second"], half, recorded_by=trio["second"])
    assert payment.amount_cents == half
    assert payment.debtor_user_id == trio["second"]
    split = splits.get_split(split.id)
    assert split.status == "partially_paid"
    assert split.settled_at is None
    shares = splits.list_shares(split.id, viewer_user_id=trio["owner"])
    assert shares[1].paid_cents == half
    assert shares[1].remaining_cents == rupiah_to_cents(50_000)
    assert [d.user_id for d in splits.list_debtors(split.id, viewer_user_id=trio["owner"])] == [
        trio["second"],
        trio["third"],
    ]
    # Pay the rest of the second debtor, then the third in full.
    splits.record_payment(split.id, trio["second"], half, recorded_by=trio["owner"])
    splits.record_payment(
        split.id, trio["third"], rupiah_to_cents(100_000), recorded_by=trio["third"]
    )
    split = splits.get_split(split.id)
    assert split.status == "settled"
    assert split.settled_at is not None
    assert splits.list_debtors(split.id, viewer_user_id=trio["owner"]) == []
    assert len(splits.list_payments(split.id, viewer_user_id=trio["owner"])) == 3


def test_record_payment_validation(splits, trio, make_user, add_room_member):
    split = _meal(splits, trio)
    outsider = make_user()["user_id"]
    guest = add_room_member(trio["room"].id, trio["owner"], role="guest")["user_id"]
    with pytest.raises(SplitError):  # overpaying never creates credit
        splits.record_payment(
            split.id, trio["second"], rupiah_to_cents(100_001), recorded_by=trio["owner"]
        )
    with pytest.raises(SplitError):  # the payer cannot pay themselves
        splits.record_payment(
            split.id, trio["owner"], rupiah_to_cents(1), recorded_by=trio["owner"]
        )
    with pytest.raises(SplitError):  # not a participant
        splits.record_payment(split.id, outsider, rupiah_to_cents(1), recorded_by=trio["owner"])
    with pytest.raises(SplitError):  # non-positive / non-int amount
        splits.record_payment(split.id, trio["second"], 0, recorded_by=trio["owner"])
    with pytest.raises(SplitError):
        splits.record_payment(split.id, trio["second"], 10.5, recorded_by=trio["owner"])
    with pytest.raises(NotRoomMemberError):  # non-member recorder
        splits.record_payment(split.id, trio["second"], rupiah_to_cents(1), recorded_by=outsider)
    with pytest.raises(NotRoomMemberError):  # guest may not record
        splits.record_payment(split.id, trio["second"], rupiah_to_cents(1), recorded_by=guest)


def test_settled_split_refuses_further_payment(splits, trio):
    split = _meal(splits, trio)
    for debtor in (trio["second"], trio["third"]):
        splits.record_payment(split.id, debtor, rupiah_to_cents(100_000), recorded_by=debtor)
    assert splits.get_split(split.id).status == "settled"
    with pytest.raises(SplitError):
        splits.record_payment(split.id, trio["second"], rupiah_to_cents(1), recorded_by=debtor)


# --- reminders: debtors only, never the payer or the paid -----------------


def test_reminders_go_to_debtors_only(splits, trio):
    split = _meal(splits, trio, due_at=datetime.now(UTC) + timedelta(days=2))
    # Second debtor pays in full; third pays half.
    splits.record_payment(
        split.id, trio["second"], rupiah_to_cents(100_000), recorded_by=trio["second"]
    )
    splits.record_payment(
        split.id, trio["third"], rupiah_to_cents(50_000), recorded_by=trio["third"]
    )
    shares = splits.list_shares(split.id, viewer_user_id=trio["owner"])
    lines = split_reminder_lines(split, shares)
    assert len(lines) == 1
    assert trio["third"][:8] in lines[0]
    assert "Rp50.000" in lines[0]
    assert trio["owner"][:8] not in lines[0]
    assert trio["second"][:8] not in lines[0]
    sweep = run_reminder_sweep(trio["room"].id, viewer_user_id=trio["owner"])
    assert len(sweep) == 1
    assert sweep[0]["debtor_user_id"] == trio["third"]
    assert sweep[0]["remaining_cents"] == rupiah_to_cents(50_000)
    assert sweep[0]["urgency"] == "not_yet"
    assert sweep[0]["split_id"] == split.id


def test_reminder_sweep_skips_settled_splits_and_groups_by_debtor(splits, trio):
    first = _meal(splits, trio)
    second = splits.create_split(
        trio["room"].id,
        "Gift together",
        rupiah_to_cents(90_000),
        trio["owner"],
        [trio["owner"], trio["second"], trio["third"]],
        created_by=trio["owner"],
        kind="gift",
    )
    splits.record_payment(
        first.id, trio["second"], rupiah_to_cents(100_000), recorded_by=trio["second"]
    )
    splits.record_payment(
        first.id, trio["third"], rupiah_to_cents(100_000), recorded_by=trio["third"]
    )
    assert splits.get_split(first.id).status == "settled"
    sweep = run_reminder_sweep(trio["room"].id, viewer_user_id=trio["owner"])
    assert {entry["split_id"] for entry in sweep} == {second.id}
    assert {entry["debtor_user_id"] for entry in sweep} == {trio["second"], trio["third"]}
    from puspa.rooms.splitting import build_reminder_digest

    digest = build_reminder_digest(trio["room"].id, viewer_user_id=trio["owner"])
    assert {entry["debtor_user_id"] for entry in digest} == {trio["second"], trio["third"]}
    assert all(len(entry["reminders"]) == 1 for entry in digest)


def test_render_split_shows_balances(splits, trio):
    split = _meal(splits, trio)
    shares = splits.list_shares(split.id, viewer_user_id=trio["owner"])
    text = render_split(split, shares)
    assert "Dinner together" in text
    assert "Rp300.000" in text
    assert "Rp100.000" in text
    assert "status open" in text


def test_reminder_job_prompt_and_registration(make_room, tmp_path):
    made = make_room()
    room = made["room"]
    prompt = reminder_job_prompt(room.id)
    assert room.id in prompt
    assert "never remind the payer" in prompt
    store = CronJobStore(tmp_path / "cron.json")
    result = register_reminder_job(store, room.id, deliver_to="room:" + room.id)
    assert result["action"] == "create"
    assert room.id in result["job"]["prompt"]
    assert result["job"]["schedule"]["seconds"] == 12 * 3600
    assert len(cronjob_manage(store, "list")["jobs"]) == 1


# --- settlement: prepare-only, money-gated, never executed -----------------


def test_settlement_needs_money_gate_approval(splits, gate, trio):
    split = _meal(splits, trio)
    action_id = settlement_action_id(split.id, trio["second"])
    assert action_id.startswith("split-settlement:")
    # A non-owner requester with no approvals is refused.
    with pytest.raises(MoneyApprovalError):
        splits.prepare_settlement(split.id, trio["second"], requested_by=trio["second"])
    # The requester approves, plus one other owner/member: passes.
    gate.approve_money_action(split.room_id, action_id, approver_user_id=trio["second"])
    with pytest.raises(MoneyApprovalError):
        splits.prepare_settlement(split.id, trio["second"], requested_by=trio["second"])
    gate.approve_money_action(split.room_id, action_id, approver_user_id=trio["third"])
    settlement = splits.prepare_settlement(split.id, trio["second"], requested_by=trio["second"])
    assert settlement.status == "prepared"
    assert settlement.recipient_user_id == trio["owner"]
    assert settlement.amount_cents == rupiah_to_cents(100_000)
    assert settlement.action_id == action_id
    assert settlement.debtor_user_id == trio["second"]


def test_settlement_owner_alone_may_prepare(splits, trio):
    split = _meal(splits, trio)
    settlement = splits.prepare_settlement(split.id, trio["third"], requested_by=trio["owner"])
    assert settlement.status == "prepared"
    assert settlement.amount_cents == rupiah_to_cents(100_000)
    assert settlement.recipient_user_id == trio["owner"]


def test_settlement_amount_follows_partial_payments_and_supersedes(splits, trio):
    split = _meal(splits, trio)
    splits.record_payment(
        split.id, trio["second"], rupiah_to_cents(40_000), recorded_by=trio["second"]
    )
    first = splits.prepare_settlement(split.id, trio["second"], requested_by=trio["owner"])
    assert first.amount_cents == rupiah_to_cents(60_000)
    splits.record_payment(
        split.id, trio["second"], rupiah_to_cents(10_000), recorded_by=trio["second"]
    )
    second = splits.prepare_settlement(split.id, trio["second"], requested_by=trio["owner"])
    assert second.amount_cents == rupiah_to_cents(50_000)
    settlements = splits.list_settlements(split.id, viewer_user_id=trio["owner"])
    by_id = {s.id: s for s in settlements}
    assert by_id[first.id].status == "superseded"
    assert by_id[second.id].status == "prepared"


def test_settlement_validation(splits, trio):
    split = _meal(splits, trio)
    with pytest.raises(SplitError):  # the payer has nothing to settle
        splits.prepare_settlement(split.id, trio["owner"], requested_by=trio["owner"])
    splits.record_payment(
        split.id, trio["second"], rupiah_to_cents(100_000), recorded_by=trio["second"]
    )
    with pytest.raises(SplitError):  # nothing left to settle
        splits.prepare_settlement(split.id, trio["second"], requested_by=trio["owner"])
    # There is deliberately no execution path: settlement is
    # prepare-only (B8 phase 1). The user pays in their own
    # bank/e-wallet app and records it as a payment.
    assert not hasattr(splits, "execute_settlement")
    assert not hasattr(SplitService, "execute_settlement")


def test_settlement_preparation_is_ledger_recorded(splits, trio, test_dsn):
    split = _meal(splits, trio)
    ledger = Ledger(test_dsn)
    agent_id = trio["owner"]  # any non-empty agent id works for the ledger
    splits.prepare_settlement(
        split.id, trio["second"], requested_by=trio["owner"], ledger=ledger, agent_id=agent_id
    )
    events = ledger.events(split.id, event_type="evidence_recorded")
    assert len(events) == 1
    content = events[0]["content"]
    # B2: the preparation is a payment_prepared outcome
    # record; the pre-B2 payload is preserved under details.
    assert content["kind"] == "outcome_record"
    assert content["outcome_type"] == "payment_prepared"
    assert content["receipts"][0]["kind"] == "split_settlement"
    assert content["receipts"][0]["amount_cents"] == rupiah_to_cents(100_000)
    details = content["details"]
    assert details["kind"] == "split_settlement_prepared"
    assert details["executed"] is False
    assert details["amount_cents"] == rupiah_to_cents(100_000)
    assert details["recipient_user_id"] == trio["owner"]
    with pytest.raises(SplitError):  # ledger without an agent id
        splits.prepare_settlement(
            split.id, trio["third"], requested_by=trio["owner"], ledger=ledger
        )
