"""Service tests for the per-room job queue (P-05, plan 12.1).

Covers the queue contract only: enqueue validation and roles,
FIFO order, the two built-in handlers (summarize records a room
summary, note records a room note), failure marking, and the
serialisation guarantee — two workers running jobs for the same
room never overlap, while the oldest job always runs first.
The HTTP layer for the queue lives in
``tests/test_pwa_rooms_p05_api.py``.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from puspa.rooms import RoomJobQueue
from puspa.rooms.errors import GuestReadOnlyError, NotRoomMemberError
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]


@pytest.fixture(autouse=True)
def _queue_schema(test_dsn):
    """Apply migration 0024 (the queue table). The room fixture
    applies only 0010; the test database already records 0023
    (the memory loader runs in conftest), so only 0024 lands."""
    from puspa.rooms.job_queue import create_schema

    create_schema(test_dsn)


@pytest.fixture
def queue(test_dsn):
    """A RoomJobQueue pointed at the test database."""
    return RoomJobQueue(test_dsn)


def test_enqueue_and_list_fifo(make_room, queue):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    first = queue.enqueue(room_id, "note", {"content": "first"}, requested_by=owner)
    second = queue.enqueue(room_id, "note", {"content": "second"}, requested_by=owner)
    assert first.status == "queued"
    assert first.kind == "note"
    assert [job.id for job in queue.list_jobs(room_id, viewer_user_id=owner)] == [
        first.id,
        second.id,
    ]


def test_enqueue_rejects_bad_kind_and_payload(make_room, queue):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    with pytest.raises(ValueError):
        queue.enqueue(room_id, "pay", {}, requested_by=owner)
    with pytest.raises(ValueError):
        queue.enqueue(room_id, "note", {"content": "  "}, requested_by=owner)
    with pytest.raises(ValueError):
        queue.enqueue(room_id, "summarize", {"messages": "not a list"}, requested_by=owner)
    with pytest.raises(ValueError):
        queue.enqueue(room_id, "note", ["not", "a", "dict"], requested_by=owner)


def test_enqueue_rejects_guest_and_outsider(make_room, add_room_member, make_user, queue):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    guest = add_room_member(room_id, owner, role="guest")
    outsider = make_user()["user_id"]
    with pytest.raises(GuestReadOnlyError):
        queue.enqueue(room_id, "note", {"content": "x"}, requested_by=guest["user_id"])
    with pytest.raises(NotRoomMemberError):
        queue.enqueue(room_id, "note", {"content": "x"}, requested_by=outsider)


def test_process_next_summarize_records_room_summary(make_room, queue, test_dsn):
    from puspa.rooms import RoomMemoryStore

    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    queue.enqueue(
        room_id,
        "summarize",
        {
            "messages": [
                {"author": "A", "text": "Decision: ship Friday"},
                {"author": "B", "text": "TODO: book venue"},
            ]
        },
        requested_by=owner,
    )
    finished = queue.process_next(room_id, actor_user_id=owner)
    assert finished is not None
    assert finished.status == "done"
    assert "ship Friday" in (finished.result or "")
    memories = RoomMemoryStore(test_dsn).list_room_memory(room_id, viewer_user_id=owner)
    assert len(memories) == 1
    assert memories[0].kind == "summary"
    assert "ship Friday" in memories[0].content
    # The queue is empty now.
    assert queue.process_next(room_id, actor_user_id=owner) is None


def test_process_next_note_records_room_note(make_room, queue, test_dsn):
    from puspa.rooms import RoomMemoryStore

    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    queue.enqueue(room_id, "note", {"content": "buy rice"}, requested_by=owner)
    finished = queue.process_next(room_id, actor_user_id=owner)
    assert finished.status == "done"
    memories = RoomMemoryStore(test_dsn).list_room_memory(room_id, viewer_user_id=owner)
    assert [m.content for m in memories] == ["buy rice"]


def test_private_note_job_visible_only_to_author(make_room, add_room_member, queue, test_dsn):
    from puspa.rooms import RoomMemoryStore

    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    member = add_room_member(room_id, owner)
    queue.enqueue(
        room_id, "note", {"content": "secret", "visibility": "private"}, requested_by=owner
    )
    queue.process_next(room_id, actor_user_id=owner)
    store = RoomMemoryStore(test_dsn)
    assert [m.content for m in store.list_room_memory(room_id, viewer_user_id=owner)] == ["secret"]
    assert store.list_room_memory(room_id, viewer_user_id=member["user_id"]) == []


def test_failed_job_is_marked_and_queue_continues(make_room, queue):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id

    def boom(job):
        raise RuntimeError("handler broke")

    queue.enqueue(room_id, "note", {"content": "bad"}, requested_by=owner)
    queue.enqueue(room_id, "note", {"content": "good"}, requested_by=owner)
    failed = queue.process_next(room_id, actor_user_id=owner, handler=boom)
    assert failed.status == "failed"
    assert "handler broke" in (failed.error or "")
    good = queue.process_next(room_id, actor_user_id=owner)
    assert good.status == "done"
    assert queue.process_next(room_id, actor_user_id=owner) is None


def test_run_job_runs_one_specific_job(make_room, queue):
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    first = queue.enqueue(room_id, "note", {"content": "first"}, requested_by=owner)
    queue.enqueue(room_id, "note", {"content": "second"}, requested_by=owner)
    finished = queue.run_job(first.id, actor_user_id=owner)
    assert finished.status == "done"
    assert finished.id == first.id
    # Running it again is refused: it is no longer queued.
    with pytest.raises(ValueError):
        queue.run_job(first.id, actor_user_id=owner)
    with pytest.raises(ValueError):
        queue.run_job("00000000-0000-0000-0000-000000000000", actor_user_id=owner)


def test_two_workers_never_overlap_for_one_room(make_room, queue):
    """Two threads running jobs for the same room serialise: the
    second handler starts only after the first one finished, and
    the oldest job runs first."""
    made = make_room()
    owner = made["owner"]["user_id"]
    room_id = made["room"].id
    first = queue.enqueue(room_id, "note", {"content": "first"}, requested_by=owner)
    second = queue.enqueue(room_id, "note", {"content": "second"}, requested_by=owner)
    spans: list[tuple[str, float, float]] = []
    lock = threading.Lock()

    def slow_handler(job):
        start = time.monotonic()
        time.sleep(0.3)
        with lock:
            spans.append((job.id, start, time.monotonic()))
        return job.id

    results: list = []

    def work():
        return queue.process_next(room_id, actor_user_id=owner, handler=slow_handler)

    # The futures re-raise a worker failure in this thread, so no
    # broad handler is needed to collect it: a worker that dies
    # fails the test with the real traceback instead of silently.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(work) for _ in range(2)]
        for future in as_completed(futures):
            results.append(future.result())
    assert [job.id for job in results if job is not None] == [first.id, second.id]
    assert len(spans) == 2
    ordered = sorted(spans, key=lambda span: span[1])
    assert ordered[0][2] <= ordered[1][1], "the two handlers overlapped"
    assert ordered[0][0] == first.id, "the oldest job did not run first"
    assert all(job.status == "done" for job in results if job is not None)
