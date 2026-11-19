"""Verified outcome records (B2): one shared write path.

Covers the writer itself (accepts a fully-evidenced record,
rejects a record without evidence, ledger entry attached,
per-user and per-group queries) and the wiring of every
existing write path through it: B10 meeting decisions
(migrated), B11 split settlements (migrated), B6 board tasks,
B9 delegated tasks, B12 group price/seller checks and group
decisions, B13 draft approvals. For the migrated paths the
tests also prove equivalence: the pre-B2 event payload is
preserved verbatim under ``details``.

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers; the
meeting and drafting schemas by the autouse fixtures below.
"""

import json
from typing import Any

import pytest

from puspa.drafting import DraftingService
from puspa.drafting.schema import create_schema as create_drafting_schema
from puspa.drafting.schema import reset_tables as reset_drafting_tables
from puspa.evidence.evidence import record_evidence
from puspa.evidence.outcomes import (
    OUTCOME_EVENT_TYPE,
    OutcomeError,
    evidence_ref,
    get_outcome,
    is_outcome_event,
    list_outcomes_for_group,
    list_outcomes_for_user,
    write_outcome,
)
from puspa.meetings import MeetingMinutesStore
from puspa.meetings.schema import create_schema as create_meetings_schema
from puspa.meetings.schema import reset_tables as reset_meetings_tables
from puspa.memory.facts import MemoryFactStore
from puspa.pricing.readers import ResultRead
from puspa.rooms import DelegationService, RoomTaskStore, SplitService
from puspa.rooms.research import ResearchService, detect_source
from puspa.rooms.splitting import rupiah_to_cents
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]


@pytest.fixture(autouse=True)
def _b2_schemas(test_dsn, _rooms_schema):
    """Create the meeting + drafting schemas, then empty them."""
    create_meetings_schema(test_dsn)
    reset_meetings_tables(test_dsn)
    create_drafting_schema(test_dsn)
    reset_drafting_tables(test_dsn)
    yield
    reset_meetings_tables(test_dsn)
    reset_drafting_tables(test_dsn)


@pytest.fixture
def minutes_store(test_dsn):
    return MeetingMinutesStore(test_dsn)


@pytest.fixture
def task_store(test_dsn):
    return RoomTaskStore(test_dsn)


@pytest.fixture
def delegation_service(test_dsn):
    return DelegationService(test_dsn)


@pytest.fixture
def splits(test_dsn):
    return SplitService(test_dsn)


URL_TOPED = "https://www.tokopedia.com/toko/galaxy-s24"
URL_SHOPEE = "https://shopee.co.id/galaxy-s24"
PRICES = {
    URL_TOPED: (12_999_000.0, "Toko Galaxy", "Samsung Galaxy S24"),
    URL_SHOPEE: (12_499_000.0, "Shopee Mall Cell", "Samsung Galaxy S24"),
}


class FakeReader:
    """A reader that returns fixed prices per URL (no network)."""

    def __init__(self, fail_urls=()):
        self.fail_urls = set(fail_urls)

    def read(self, url):
        if url in self.fail_urls:
            return ResultRead(
                success=False,
                marketplace=detect_source(url),
                listing_url=url,
                reason="source_blocked_http_403",
            )
        price, seller, name = PRICES[url]
        return ResultRead(
            success=True,
            marketplace=detect_source(url),
            listing_url=url,
            price=price,
            shipping=15000,
            store_rating=4.9,
            name=name,
            seller=seller,
            text_excerpt=f"[DATA: {name} Rp{int(price)}]",
        )


def _fake_reread(url):
    price, _seller, name = PRICES[url]
    return {"value": price, "name": name}


@pytest.fixture
def research(test_dsn):
    return ResearchService(test_dsn, reader=FakeReader(), reread=_fake_reread, mode="fixture")


def _evidence_entry(kind="proof", **extra):
    entry = {"kind": kind}
    entry.update(extra)
    return entry


# --- the writer ---------------------------------------------------------


def test_write_outcome_accepts_fully_evidenced_record(make_user, ledger, test_dsn):
    user = make_user()
    record = write_outcome(
        ledger,
        task_id="task-1",
        agent_id=user["agent_id"],
        outcome_type="task_done",
        user_id=user["user_id"],
        room_id=None,
        subject_id="subject-1",
        summary="task done: buy the tickets",
        evidence=[_evidence_entry("board_task", task_id="subject-1")],
        receipts=[{"kind": "board_task", "task_id": "subject-1"}],
        sources=["https://www.tokopedia.com/toko/x"],
        details={"title": "buy the tickets"},
    )
    assert record.outcome_type == "task_done"
    assert record.user_id == user["user_id"]
    assert record.room_id is None
    assert record.subject_id == "subject-1"
    assert record.summary == "task done: buy the tickets"
    assert record.evidence[0]["kind"] == "board_task"
    assert record.receipts[0]["kind"] == "board_task"
    assert record.sources == ["https://www.tokopedia.com/toko/x"]
    assert record.details == {"title": "buy the tickets"}
    assert record.ledger_task_id == "task-1"
    assert record.event_hash
    # The record is a real ledger event, on the chain.
    events = ledger.events("task-1")
    assert len(events) == 1
    assert events[0]["type"] == OUTCOME_EVENT_TYPE
    assert events[0]["content"]["kind"] == "outcome_record"
    assert is_outcome_event(events[0])
    ok, broken = ledger.verify_chain("task-1")
    assert ok and broken == []
    # And it round-trips through the query API.
    assert get_outcome(test_dsn, record.outcome_id) == record
    assert json.dumps(record.to_dict())


def test_write_outcome_rejects_record_without_evidence(make_user, ledger):
    user = make_user()
    for bad_evidence in ([], None, [{}], [{"note": "no kind here"}], ["not a dict"]):
        with pytest.raises(OutcomeError):
            write_outcome(
                ledger,
                task_id="task-1",
                agent_id=user["agent_id"],
                outcome_type="task_done",
                user_id=user["user_id"],
                summary="task done",
                evidence=bad_evidence,
            )
    # Nothing was written for any of the rejected attempts.
    assert ledger.events("task-1") == []


def test_write_outcome_rejects_bad_fields(make_user, ledger):
    user = make_user()

    def _write(**overrides: Any):
        params: dict[str, Any] = {
            "task_id": "task-1",
            "agent_id": user["agent_id"],
            "outcome_type": "task_done",
            "user_id": user["user_id"],
            "summary": "task done",
            "evidence": [_evidence_entry()],
        }
        params.update(overrides)
        return write_outcome(
            ledger,
            task_id=params["task_id"],
            agent_id=params["agent_id"],
            outcome_type=params["outcome_type"],
            user_id=params["user_id"],
            room_id=params.get("room_id"),
            subject_id=params.get("subject_id"),
            summary=params["summary"],
            evidence=params["evidence"],
            receipts=params.get("receipts"),
            sources=params.get("sources"),
            details=params.get("details"),
        )

    for bad in ("", "   ", None, 42):
        with pytest.raises(OutcomeError):
            _write(task_id=bad)
        with pytest.raises(OutcomeError):
            _write(agent_id=bad)
        with pytest.raises(OutcomeError):
            _write(outcome_type=bad)
        with pytest.raises(OutcomeError):
            _write(user_id=bad)
        with pytest.raises(OutcomeError):
            _write(summary=bad)
    with pytest.raises(OutcomeError):
        _write(room_id="")
    with pytest.raises(OutcomeError):
        _write(receipts=[{"kind": "x"}, "not a dict"])
    with pytest.raises(OutcomeError):
        _write(receipts="not a list")
    with pytest.raises(OutcomeError):
        _write(sources=[""])
    with pytest.raises(OutcomeError):
        _write(sources="not a list")
    with pytest.raises(OutcomeError):
        _write(details=["not a dict"])
    with pytest.raises(OutcomeError):
        write_outcome(
            None,
            task_id="task-1",
            agent_id=user["agent_id"],
            outcome_type="task_done",
            user_id=user["user_id"],
            summary="task done",
            evidence=[_evidence_entry()],
        )


def test_write_outcome_accepts_open_outcome_type(make_user, ledger, test_dsn):
    user = make_user()
    record = write_outcome(
        ledger,
        task_id="task-1",
        agent_id=user["agent_id"],
        outcome_type="custom_check",
        user_id=user["user_id"],
        summary="a new feature names its own type",
        evidence=[_evidence_entry()],
    )
    assert record.outcome_type == "custom_check"
    assert [row.outcome_type for row in list_outcomes_for_user(test_dsn, user["user_id"])] == [
        "custom_check"
    ]


def test_evidence_ref_points_at_saved_row():
    evidence = record_evidence(
        task_id="task-1",
        claim="Galaxy S24",
        value=12_999_000,
        unit="Rp",
        source_url=URL_TOPED,
        text_excerpt="[DATA: Galaxy S24 Rp12999000]",
        reader_id="research-tokopedia",
    )
    ref = evidence_ref(evidence)
    assert ref["kind"] == "evidence_record"
    assert ref["evidence_id"] == evidence.evidence_id
    assert ref["source_url"] == URL_TOPED
    assert ref["verdict"] == "Uncertain"  # not yet verified
    assert "Rp12999000" in ref["excerpt"]


# --- per-user and per-group queries -------------------------------------


def test_list_outcomes_for_user_returns_only_that_user(make_user, ledger, test_dsn):
    ana = make_user()
    budi = make_user()
    write_outcome(
        ledger,
        task_id="task-a",
        agent_id=ana["agent_id"],
        outcome_type="task_done",
        user_id=ana["user_id"],
        summary="ana did one thing",
        evidence=[_evidence_entry()],
    )
    write_outcome(
        ledger,
        task_id="task-b",
        agent_id=budi["agent_id"],
        outcome_type="decision_recorded",
        user_id=budi["user_id"],
        summary="budi decided one thing",
        evidence=[_evidence_entry()],
    )
    write_outcome(
        ledger,
        task_id="task-a2",
        agent_id=ana["agent_id"],
        outcome_type="decision_recorded",
        user_id=ana["user_id"],
        summary="ana decided one thing",
        evidence=[_evidence_entry()],
    )
    ana_records = list_outcomes_for_user(test_dsn, ana["user_id"])
    assert [row.summary for row in ana_records] == [
        "ana did one thing",
        "ana decided one thing",
    ]
    assert all(row.user_id == ana["user_id"] for row in ana_records)
    budi_records = list_outcomes_for_user(test_dsn, budi["user_id"])
    assert [row.summary for row in budi_records] == ["budi decided one thing"]
    # The type filter narrows; the limit caps.
    assert [
        row.outcome_type
        for row in list_outcomes_for_user(test_dsn, ana["user_id"], outcome_type="task_done")
    ] == ["task_done"]
    assert len(list_outcomes_for_user(test_dsn, ana["user_id"], limit=1)) == 1
    # A user with no records gets an empty list, not an error.
    assert list_outcomes_for_user(test_dsn, make_user()["user_id"]) == []


def test_list_outcomes_for_group_returns_only_that_room(make_user, ledger, test_dsn):
    ana = make_user()
    room_a = "room-a"
    room_b = "room-b"
    write_outcome(
        ledger,
        task_id="task-a",
        agent_id=ana["agent_id"],
        outcome_type="decision_recorded",
        user_id=ana["user_id"],
        room_id=room_a,
        summary="room a decided",
        evidence=[_evidence_entry()],
    )
    write_outcome(
        ledger,
        task_id="task-b",
        agent_id=ana["agent_id"],
        outcome_type="decision_recorded",
        user_id=ana["user_id"],
        room_id=room_b,
        summary="room b decided",
        evidence=[_evidence_entry()],
    )
    write_outcome(
        ledger,
        task_id="task-c",
        agent_id=ana["agent_id"],
        outcome_type="task_done",
        user_id=ana["user_id"],
        room_id=None,
        summary="a single-user outcome, no room",
        evidence=[_evidence_entry()],
    )
    assert [row.summary for row in list_outcomes_for_group(test_dsn, room_a)] == ["room a decided"]
    assert [row.summary for row in list_outcomes_for_group(test_dsn, room_b)] == ["room b decided"]
    assert list_outcomes_for_group(test_dsn, "room-nobody") == []
    assert [
        row.outcome_type
        for row in list_outcomes_for_group(test_dsn, room_a, outcome_type="task_done")
    ] == []


def test_get_outcome_returns_none_for_unknown_or_non_outcome(make_user, ledger, test_dsn):
    user = make_user()
    assert get_outcome(test_dsn, "00000000-0000-0000-0000-000000000000") is None
    assert get_outcome(test_dsn, "not a uuid at all") is None
    # A plain ledger event is not an outcome record.
    event = ledger.append_event(
        task_id="task-1",
        agent_id=user["agent_id"],
        event_type="task_created",
        content={"kind": "something_else"},
    )
    assert get_outcome(test_dsn, event["id"]) is None


def test_non_outcome_events_are_not_listed(make_user, ledger, test_dsn):
    user = make_user()
    ledger.append_event(
        task_id="task-1",
        agent_id=user["agent_id"],
        event_type="evidence_recorded",
        content={"kind": "meeting_decision", "decision": "the old shape"},
    )
    assert list_outcomes_for_user(test_dsn, user["user_id"]) == []


# --- B10 meeting decisions (migrated) -----------------------------------


def test_meeting_decision_is_decision_recorded_outcome(minutes_store, make_room, ledger, test_dsn):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    record = minutes_store.write_minutes(
        room.id,
        author_user_id=owner["user_id"],
        transcript="Decision: buy from seller A.",
        ledger=ledger,
        agent_id=owner["agent_id"],
    )
    outcomes = list_outcomes_for_user(test_dsn, owner["user_id"])
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_type == "decision_recorded"
    assert outcome.room_id == room.id
    assert outcome.subject_id == record.id
    assert outcome.summary == "buy from seller A."
    assert outcome.evidence == [
        {"kind": "meeting_minutes", "meeting_id": record.id, "decision": "buy from seller A."}
    ]
    # Equivalence: the pre-B2 payload is preserved verbatim.
    assert outcome.details == {
        "kind": "meeting_decision",
        "meeting_id": record.id,
        "room_id": room.id,
        "decision": "buy from seller A.",
    }
    assert list_outcomes_for_group(test_dsn, room.id) == outcomes
    # The chain for the meeting still verifies.
    ok, broken = ledger.verify_chain(record.id)
    assert ok and broken == []


def test_meeting_action_item_is_not_an_outcome(minutes_store, make_room, ledger, test_dsn):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    minutes_store.write_minutes(
        room.id,
        author_user_id=owner["user_id"],
        transcript="Action: book the venue by tomorrow",
        ledger=ledger,
        agent_id=owner["agent_id"],
    )
    # Creating a task is not an outcome: no record at all.
    assert list_outcomes_for_user(test_dsn, owner["user_id"]) == []


# --- B11 split settlements (migrated) -----------------------------------


def _meal_split(splits, trio, total_rupiah=300_000):
    return splits.create_split(
        trio["room"].id,
        "Dinner together",
        rupiah_to_cents(total_rupiah),
        trio["owner"],
        [trio["owner"], trio["second"], trio["third"]],
        created_by=trio["owner"],
        kind="meal",
    )


@pytest.fixture
def trio(make_room, add_room_member):
    made = make_room()
    room = made["room"]
    owner = made["owner"]["user_id"]
    second = add_room_member(room.id, owner)["user_id"]
    third = add_room_member(room.id, owner)["user_id"]
    return {"room": room, "owner": owner, "second": second, "third": third}


def test_settlement_preparation_is_payment_prepared_outcome(splits, trio, ledger, test_dsn):
    split = _meal_split(splits, trio)
    splits.prepare_settlement(
        split.id, trio["second"], requested_by=trio["owner"], ledger=ledger, agent_id=trio["owner"]
    )
    outcomes = list_outcomes_for_group(test_dsn, trio["room"].id)
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_type == "payment_prepared"
    assert outcome.user_id == trio["owner"]
    assert outcome.subject_id == split.id
    assert outcome.evidence[0]["kind"] == "money_approval"
    assert outcome.evidence[0]["amount_cents"] == rupiah_to_cents(100_000)
    assert outcome.receipts[0]["kind"] == "split_settlement"
    assert outcome.receipts[0]["amount_cents"] == rupiah_to_cents(100_000)
    # Equivalence: the pre-B2 payload is preserved verbatim.
    assert outcome.details["kind"] == "split_settlement_prepared"
    assert outcome.details["executed"] is False
    assert outcome.details["amount_cents"] == rupiah_to_cents(100_000)
    assert outcome.details["recipient_user_id"] == trio["owner"]
    assert list_outcomes_for_user(test_dsn, trio["owner"]) == outcomes


# --- B6 board tasks -----------------------------------------------------


def test_board_task_completion_writes_task_done(make_room, task_store, ledger, test_dsn):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    task = task_store.add_task(room.id, title="Buy the tickets", created_by=owner["user_id"])
    done = task_store.complete_task(
        task.id, completed_by=owner["user_id"], ledger=ledger, agent_id=owner["agent_id"]
    )
    assert done is not None and done.status == "done"
    outcomes = list_outcomes_for_user(test_dsn, owner["user_id"])
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_type == "task_done"
    assert outcome.room_id == room.id
    assert outcome.subject_id == task.id
    assert outcome.summary == "task done: Buy the tickets"
    assert outcome.evidence[0]["kind"] == "board_task"
    # Completing again is a no-op: no second record.
    task_store.complete_task(
        task.id, completed_by=owner["user_id"], ledger=ledger, agent_id=owner["agent_id"]
    )
    assert len(list_outcomes_for_user(test_dsn, owner["user_id"])) == 1
    ok, broken = ledger.verify_chain(task.id)
    assert ok and broken == []


def test_board_task_completion_without_ledger_writes_nothing(
    make_room, task_store, ledger, test_dsn
):
    made = make_room()
    owner = made["owner"]
    task = task_store.add_task(
        made["room"].id, title="Buy the tickets", created_by=owner["user_id"]
    )
    task_store.complete_task(task.id, completed_by=owner["user_id"])
    assert list_outcomes_for_user(test_dsn, owner["user_id"]) == []
    with pytest.raises(ValueError):
        task_store.complete_task(task.id, completed_by=owner["user_id"], ledger=ledger)


# --- B9 delegated tasks -------------------------------------------------


def test_delegated_task_completion_writes_one_task_done_with_step_evidence(
    delegation_service, make_room, ledger, test_dsn
):
    made = make_room()
    owner = made["owner"]
    task = delegation_service.tasks.add_task(
        made["room"].id, title="Compare prices", created_by=owner["user_id"]
    )
    delegation_service.delegate_to_agent(
        task.id, delegated_by=owner["user_id"], subagent_ref="sub-ok"
    )
    step = delegation_service.propose_step(
        task.id, by="sub-ok", description="read the listing", tool_name="read_listing"
    )
    delegation_service.execute_step(step.id, by="sub-ok", executor=lambda c: "read ok")
    done = delegation_service.complete_task(
        task.id, completed_by=owner["user_id"], ledger=ledger, agent_id=owner["agent_id"]
    )
    assert done is not None and done.status == "done"
    outcomes = list_outcomes_for_user(test_dsn, owner["user_id"])
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_type == "task_done"
    assert outcome.subject_id == task.id
    kinds = [entry["kind"] for entry in outcome.evidence]
    assert kinds == ["board_task", "delegated_step"]
    assert outcome.evidence[1]["step_id"] == step.id
    assert outcome.evidence[1]["status"] == "executed"
    assert outcome.evidence[1]["result"] == "read ok"


# --- B12 group price / seller checks and decisions ----------------------


def test_compare_writes_price_and_seller_outcomes(make_room, research, ledger, test_dsn):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    research.add_link(room.id, added_by_user_id=owner["user_id"], url=URL_TOPED, label="Option A")
    research.add_link(room.id, added_by_user_id=owner["user_id"], url=URL_SHOPEE, label="Option B")
    comparison = research.compare(
        room.id,
        requested_by_user_id=owner["user_id"],
        ledger=ledger,
        agent_id=owner["agent_id"],
    )
    assert len(comparison.items) == 2
    assert all(item.checked for item in comparison.items)
    outcomes = list_outcomes_for_group(test_dsn, room.id)
    by_type = {}
    for outcome in outcomes:
        by_type.setdefault(outcome.outcome_type, []).append(outcome)
    assert sorted(by_type) == ["price_checked", "seller_checked"]
    assert len(by_type["price_checked"]) == 2
    assert len(by_type["seller_checked"]) == 2
    price = by_type["price_checked"][0]
    assert price.evidence[0]["kind"] == "evidence_record"
    assert price.evidence[0]["evidence_id"] == comparison.items[0].evidence_id
    assert price.evidence[0]["verdict"] == "Verified"
    assert price.sources == [URL_TOPED]
    assert price.details["price"] == 12_999_000.0
    assert price.details["seller"] == "Toko Galaxy"
    seller = by_type["seller_checked"][0]
    assert seller.details["seller"] == "Toko Galaxy"
    assert seller.details["store_rating"] == 4.9
    assert list_outcomes_for_user(test_dsn, owner["user_id"]) == outcomes


def test_compare_without_ledger_writes_nothing(make_room, research, ledger, test_dsn):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    research.add_link(room.id, added_by_user_id=owner["user_id"], url=URL_TOPED, label="Option A")
    research.compare(room.id, requested_by_user_id=owner["user_id"])
    assert list_outcomes_for_group(test_dsn, room.id) == []
    with pytest.raises(ValueError):
        research.compare(room.id, requested_by_user_id=owner["user_id"], ledger=ledger)


def test_compare_unchecked_link_writes_no_outcome(make_room, research, ledger, test_dsn):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    failing = ResearchService(
        research.dsn,
        reader=FakeReader(fail_urls={URL_TOPED}),
        reread=_fake_reread,
        mode="fixture",
    )
    failing.add_link(room.id, added_by_user_id=owner["user_id"], url=URL_TOPED, label="Option A")
    comparison = failing.compare(
        room.id,
        requested_by_user_id=owner["user_id"],
        ledger=ledger,
        agent_id=owner["agent_id"],
    )
    assert comparison.items[0].checked is False
    assert list_outcomes_for_group(test_dsn, room.id) == []


def test_group_decision_writes_decision_recorded_outcome(
    make_room, add_room_member, research, ledger, test_dsn
):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    budi = add_room_member(room.id, added_by=owner["user_id"])
    memory = research.record_group_decision(
        room.id,
        author_user_id=owner["user_id"],
        decision="buy the cheaper one",
        agreed_by_user_ids=[budi["user_id"]],
        ledger=ledger,
        agent_id=owner["agent_id"],
    )
    outcomes = list_outcomes_for_group(test_dsn, room.id)
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_type == "decision_recorded"
    assert outcome.subject_id == memory.id
    assert outcome.summary == "buy the cheaper one"
    assert outcome.evidence[0]["kind"] == "group_decision"
    assert outcome.evidence[0]["agreed_by_user_ids"] == [budi["user_id"]]


# --- B13 draft approvals ------------------------------------------------


def test_draft_approval_writes_decision_recorded_outcome(ledger, test_dsn):
    service = DraftingService(fact_store=MemoryFactStore())
    user_id = "user-1"
    draft = service.draft(user_id, "message", "please confirm the order by Friday")
    approval = service.approve_draft(draft.id, user_id=user_id, ledger=ledger, agent_id="agent-1")
    outcomes = list_outcomes_for_user(test_dsn, user_id)
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.outcome_type == "decision_recorded"
    assert outcome.subject_id == draft.id
    assert outcome.evidence[0]["kind"] == "draft_approval"
    assert outcome.evidence[0]["approval_id"] == approval.id
    assert outcome.evidence[0]["content_hash"] == approval.content_hash
    assert outcome.details["kind"] == "message"
    ok, broken = ledger.verify_chain(draft.id)
    assert ok and broken == []


def test_draft_approval_without_ledger_writes_nothing(ledger, test_dsn):
    service = DraftingService(fact_store=MemoryFactStore())
    user_id = "user-1"
    draft = service.draft(user_id, "message", "please confirm the order by Friday")
    service.approve_draft(draft.id, user_id=user_id)
    assert list_outcomes_for_user(test_dsn, user_id) == []
    with pytest.raises(ValueError):
        service.approve_draft(draft.id, user_id=user_id, ledger=ledger)
