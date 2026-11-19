"""Meeting notes and follow-ups (B10).

Tests run against the Postgres test database named by the
``test_dsn`` fixture (env PUSPA_TEST_DSN). The room schema is
applied by the autouse fixture from tests.rooms_helpers; the
meeting schema is applied by the autouse fixture below.
"""

from datetime import UTC, date, datetime, timedelta

import pytest

from puspa.meetings import (
    MeetingMinutesStore,
    build_reminder_digest,
    due_meeting_actions,
    list_minutes,
    overdue_meeting_actions,
    parse_transcript,
    polish_minutes,
    register_reminder_job,
    reminders_for_owner,
    render_minutes,
    run_reminder_sweep,
)
from puspa.meetings.schema import create_schema as create_meetings_schema
from puspa.meetings.schema import reset_tables as reset_meetings_tables
from puspa.rooms import RoomTaskStore, list_room_memory, list_tasks
from puspa.rooms.errors import NotRoomMemberError
from puspa.scheduler.cron import CronJobStore, cronjob_manage
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room, room_store

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room", "room_store"]


@pytest.fixture(autouse=True)
def _meetings_schema(test_dsn, _rooms_schema):
    """Create the meeting schema, then empty the table before and
    after each test. Depends on ``_rooms_schema`` so the rooms
    tables exist first (the meeting schema references them)."""
    create_meetings_schema(test_dsn)
    reset_meetings_tables(test_dsn)
    yield
    reset_meetings_tables(test_dsn)


@pytest.fixture
def minutes_store(test_dsn):
    return MeetingMinutesStore(test_dsn)


REF_THURSDAY = date(2026, 11, 19)  # a Thursday, for weekday tests

TRANSCRIPT = """Title: Weekly shop sync
Attendees: Ana, Budi
Decision: buy from seller A.
Decision: keep the price cap at Rp500.000.
Action: @Budi send the price list by 2026-11-20
TODO: @Ana confirm the seller by Friday
Action: book the venue by tomorrow
Note: the seller replies fastest on WhatsApp.
Just chatting here, nothing to record.
"""


# --- deterministic extraction (pure functions) ---


def test_parse_transcript_decisions_actions_owners_deadlines():
    minutes = parse_transcript(TRANSCRIPT, ref_date=REF_THURSDAY)
    assert minutes.title == "Weekly shop sync"
    assert minutes.attendees == ("Ana", "Budi")
    assert [d.text for d in minutes.decisions] == [
        "buy from seller A.",
        "keep the price cap at Rp500.000.",
    ]
    assert [a.title for a in minutes.actions] == [
        "send the price list",
        "confirm the seller",
        "book the venue",
    ]
    assert minutes.actions[0].owner_name == "Budi"
    assert minutes.actions[0].due_date == date(2026, 11, 20)
    assert minutes.actions[1].owner_name == "Ana"
    # Friday after Thursday 19 Nov 2026 is 20 Nov 2026.
    assert minutes.actions[1].due_date == date(2026, 11, 20)
    assert minutes.actions[2].owner_name is None
    assert minutes.actions[2].due_date == date(2026, 11, 20)
    assert minutes.notes == ("the seller replies fastest on WhatsApp.",)


def test_parse_transcript_weekday_names_and_next():
    text = (
        "Action: one by Friday\nAction: two by next Friday\n"
        "Action: three by Jumat\nAction: four by besok\nAction: five by today"
    )
    minutes = parse_transcript(text, ref_date=REF_THURSDAY)
    assert [a.due_date for a in minutes.actions] == [
        date(2026, 11, 20),
        date(2026, 11, 27),
        date(2026, 11, 20),
        date(2026, 11, 20),
        date(2026, 11, 19),
    ]


def test_parse_transcript_unmarked_talk_is_ignored():
    minutes = parse_transcript("hello everyone\nwe should maybe buy it\n", ref_date=REF_THURSDAY)
    assert minutes.decisions == ()
    assert minutes.actions == ()
    assert "none stated" in render_minutes(minutes).lower()


def test_render_minutes_lists_decisions_and_actions():
    minutes = parse_transcript(TRANSCRIPT, ref_date=REF_THURSDAY)
    text = render_minutes(minutes)
    assert "buy from seller A." in text
    assert "send the price list — owner: Budi — due: 2026-11-20" in text
    assert "book the venue — owner: unassigned — due: 2026-11-20" in text


def test_polish_minutes_without_llm_returns_deterministic():
    minutes = parse_transcript(TRANSCRIPT, ref_date=REF_THURSDAY)
    assert polish_minutes(minutes) == render_minutes(minutes)


def test_polish_minutes_keeps_facts_or_falls_back():
    minutes = parse_transcript(TRANSCRIPT, ref_date=REF_THURSDAY)
    deterministic = render_minutes(minutes)

    def faithful(prompt: str) -> str:
        return deterministic + "\nPolished intro line."

    assert polish_minutes(minutes, llm_complete=faithful).endswith("Polished intro line.")

    def forgetful(prompt: str) -> str:
        return "A lovely meeting happened. Everyone agreed on things."

    assert polish_minutes(minutes, llm_complete=forgetful) == deterministic


# --- store: minutes, board tasks, room decisions, ledger ---


def test_write_minutes_creates_board_tasks_and_decisions(
    minutes_store, make_room, add_room_member, test_dsn
):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    ana = add_room_member(room.id, added_by=owner["user_id"])
    budi = add_room_member(room.id, added_by=owner["user_id"])
    record = minutes_store.write_minutes(
        room.id,
        author_user_id=owner["user_id"],
        transcript=TRANSCRIPT,
        held_at=datetime(2026, 11, 19, 10, 0, tzinfo=UTC),
        members_by_name={"Ana": ana["user_id"], "Budi": budi["user_id"]},
    )
    assert record.title == "Weekly shop sync"
    assert "buy from seller A." in record.minutes_text
    assert record.decisions == ["buy from seller A.", "keep the price cap at Rp500.000."]
    assert len(record.action_items) == 3
    assert record.action_items[0]["owner_user_id"] == budi["user_id"]
    assert record.action_items[0]["due_at"] == "2026-11-20"
    assert record.action_items[1]["owner_user_id"] == ana["user_id"]
    assert record.action_items[2]["owner_user_id"] is None  # unassigned, never guessed
    tasks = list_tasks(room.id, viewer_user_id=owner["user_id"])
    assert [task.title for task in tasks] == [
        "send the price list",
        "confirm the seller",
        "book the venue",
    ]
    assert tasks[0].owner_user_id == budi["user_id"]
    assert tasks[0].due_at == datetime(2026, 11, 20, 23, 59, tzinfo=UTC)
    decisions = list_room_memory(room.id, viewer_user_id=owner["user_id"], kind="decision")
    assert {row.content.split("\n")[0] for row in decisions} == {
        "buy from seller A.",
        "keep the price cap at Rp500.000.",
    }
    listed = list_minutes(room.id, viewer_user_id=ana["user_id"])
    assert [row.id for row in listed] == [record.id]


def test_write_minutes_unresolved_owner_stays_unassigned(minutes_store, make_room, test_dsn):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    record = minutes_store.write_minutes(
        room.id,
        author_user_id=owner["user_id"],
        transcript="Action: @Nobody send the list by 2026-11-20",
        held_at=datetime(2026, 11, 19, 10, 0, tzinfo=UTC),
    )
    assert record.action_items[0]["owner_name"] == "Nobody"
    assert record.action_items[0]["owner_user_id"] is None
    tasks = list_tasks(room.id, viewer_user_id=owner["user_id"])
    assert tasks[0].owner_user_id is None


def test_write_minutes_records_ledger_events(
    minutes_store, make_room, add_room_member, ledger, test_dsn
):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    budi = add_room_member(room.id, added_by=owner["user_id"])
    record = minutes_store.write_minutes(
        room.id,
        author_user_id=owner["user_id"],
        transcript=(
            "Decision: buy from seller A.\nAction: @Budi send the price list by 2026-11-20"
        ),
        held_at=datetime(2026, 11, 19, 10, 0, tzinfo=UTC),
        members_by_name={"Budi": budi["user_id"]},
        ledger=ledger,
        agent_id=owner["agent_id"],
    )
    events = ledger.events(record.id)
    kinds = [(event["type"], event["content"]["kind"]) for event in events]
    assert kinds == [
        ("evidence_recorded", "meeting_decision"),
        ("task_created", "meeting_action_item"),
    ]
    assert events[0]["content"]["decision"] == "buy from seller A."
    assert events[1]["content"]["room_task_id"] == record.action_items[0]["task_id"]
    assert events[1]["content"]["owner_user_id"] == budi["user_id"]
    ok, broken = ledger.verify_chain(record.id)
    assert ok and broken == []


def test_write_minutes_rejects_non_member_and_empty(
    minutes_store, make_room, make_user, ledger, test_dsn
):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        minutes_store.write_minutes(
            room.id, author_user_id=outsider["user_id"], transcript=TRANSCRIPT
        )
    with pytest.raises(ValueError):
        minutes_store.write_minutes(room.id, author_user_id=owner["user_id"], transcript="   ")
    with pytest.raises(ValueError):
        minutes_store.write_minutes(
            room.id,
            author_user_id=owner["user_id"],
            transcript=TRANSCRIPT,
            ledger=ledger,
        )


def test_get_minutes_non_member_cannot_read(minutes_store, make_room, make_user, test_dsn):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    record = minutes_store.write_minutes(
        room.id,
        author_user_id=owner["user_id"],
        transcript="Decision: buy from seller A.",
        held_at=datetime(2026, 11, 19, 10, 0, tzinfo=UTC),
    )
    assert (
        minutes_store.get_minutes(
            "00000000-0000-0000-0000-000000000000", viewer_user_id=owner["user_id"]
        )
        is None
    )
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        minutes_store.get_minutes(record.id, viewer_user_id=outsider["user_id"])


# --- reminders: queries + scheduler hook ---


def _write_dated_minutes(minutes_store, room_id, owner_id, member_id):
    """Minutes with one item due tomorrow and one overdue yesterday."""
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    yesterday = (now - timedelta(days=1)).date().isoformat()
    return minutes_store.write_minutes(
        room_id,
        author_user_id=owner_id,
        transcript=(
            f"Action: @Budi send the price list by {tomorrow}\n"
            f"Action: @Budi pay the deposit by {yesterday}"
        ),
        held_at=now,
        members_by_name={"Budi": member_id},
    )


def test_meeting_reminders_due_and_overdue_only(
    minutes_store, make_room, add_room_member, test_dsn
):
    made = make_room()
    room = made["room"]
    owner = made["owner"]
    budi = add_room_member(room.id, added_by=owner["user_id"])
    record = _write_dated_minutes(minutes_store, room.id, owner["user_id"], budi["user_id"])
    # A plain board task due tomorrow is NOT a meeting action item.
    RoomTaskStore(test_dsn).add_task(
        room.id,
        title="buy snacks",
        created_by=owner["user_id"],
        owner_user_id=budi["user_id"],
        due_at=datetime.now(UTC) + timedelta(days=1),
    )
    due = due_meeting_actions(room.id, viewer_user_id=owner["user_id"], within=timedelta(days=3))
    # B6 due_soon has no lower bound, so the overdue item is due too.
    assert [task.title for task in due] == ["pay the deposit", "send the price list"]
    assert record.action_items[0]["task_id"] in {task.id for task in due}
    overdue = overdue_meeting_actions(room.id, viewer_user_id=owner["user_id"])
    assert [task.title for task in overdue] == ["pay the deposit"]
    mine = reminders_for_owner(
        room.id,
        owner_user_id=budi["user_id"],
        viewer_user_id=owner["user_id"],
        within=timedelta(days=3),
    )
    assert [task.title for task in mine] == ["pay the deposit", "send the price list"]
    digest = build_reminder_digest(
        room.id, viewer_user_id=owner["user_id"], within=timedelta(days=3)
    )
    assert len(digest) == 1
    assert digest[0]["owner_user_id"] == budi["user_id"]
    assert [item["title"] for item in digest[0]["tasks"]] == [
        "pay the deposit",
        "send the price list",
    ]
    assert digest[0]["tasks"][0]["status"] == "overdue"
    assert "overdue" in digest[0]["tasks"][0]["text"]
    assert "due" in digest[0]["tasks"][1]["text"]
    sweep = run_reminder_sweep(room.id, viewer_user_id=owner["user_id"], within=timedelta(days=3))
    assert len(sweep) == 2
    assert {entry["owner_user_id"] for entry in sweep} == {budi["user_id"]}


def test_meeting_reminders_reject_non_member(minutes_store, make_room, make_user, test_dsn):
    made = make_room()
    room = made["room"]
    outsider = make_user()
    with pytest.raises(NotRoomMemberError):
        due_meeting_actions(room.id, viewer_user_id=outsider["user_id"])


def test_register_reminder_job_uses_g8_cron(make_room, tmp_path):
    made = make_room()
    room = made["room"]
    store = CronJobStore(tmp_path / "cron.json")
    result = register_reminder_job(store, room.id, deliver_to="room:" + room.id)
    assert result["action"] == "create"
    job = result["job"]
    assert room.id in job["prompt"]
    assert job["schedule"]["type"] == "interval"
    assert job["schedule"]["seconds"] == 12 * 3600
    listed = cronjob_manage(store, "list")
    assert len(listed["jobs"]) == 1
