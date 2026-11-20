"""Differential RLS tests (E-13, plan sections 6.9 and 12).

Two layers guard every read, and they can disagree in two ways:
the code layer can be too loose while RLS silently saves it (so
the looseness is never fixed), or RLS can be too strict so a
legitimate read fails silently. These tests run the same read
through each layer on its own, with the other layer disabled:

- Code layer alone: service calls run on connections opened as
  the table owner, which bypasses RLS by design (no FORCE, see
  ``docs/rls-differential.md``). A service that rejects user A
  asking for user B's data rejects on its own checks.
- RLS layer alone: raw SQL on a connection running as the
  ``puspa_app`` role with ``app.current_user_id`` set to A, with
  no Python check in the way. B's rows must be invisible.
- HTTP layer: the real PWA app, logged in as A, asking for B's
  resources by id. The handler binds the session user id, so a
  cross-user read must come back 403/404 with no leaked content.

A canary user owns one row in every covered table, each carrying
the marker ``CANARY_MARKER``. No response or row set returned to
another user may contain that marker anywhere; the marker is the
tripwire for a leak through either layer.

Coverage:

- Per-user tables (code layer = the caller passing the session
  user id; the stores themselves trust their ``user_id``
  argument — see ``test_code_layer_trusts_caller_supplied_user_id``):
  ``users``, ``sessions``, ``vault``, ``profile_facts``,
  ``episodic``, ``drafts``, ``prepared_payments``, ``mockups``.
- Shared rooms (isolation by membership, plan section 12):
  ``rooms``, ``room_members``, ``room_invites``,
  ``room_memories`` (including the private-visibility rule),
  ``room_tasks``, ``room_money_approvals``,
  ``room_channel_links``, ``delegated_tasks``,
  ``delegated_task_steps``, ``room_splits``, ``split_shares``,
  ``split_payments``, ``split_settlements``,
  ``room_research_links``, ``meeting_minutes``.

The mockup case is the regression test for migration 0021:
before it, the ``mockups_owner`` policy read the never-set
``app.user_id`` variable and the table had no grant to
``puspa_app``, so the RLS layer denied even the owner's reads.
"""

from __future__ import annotations

import contextlib
import json

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from puspa.accounts import rls
from puspa.accounts import vault as vault_store
from puspa.drafting import DraftingService, PostgresDraftStore
from puspa.drafting.errors import DraftNotFoundError
from puspa.drafting.schema import create_schema as create_drafting_schema
from puspa.drafting.schema import reset_tables as reset_drafting_tables
from puspa.meetings.schema import create_schema as create_meetings_schema
from puspa.meetings.schema import reset_tables as reset_meetings_tables
from puspa.meetings.store import MeetingMinutesStore
from puspa.memory import episodic, profile
from puspa.memory.facts import PostgresFactStore
from puspa.mockup.store import MockupStore
from puspa.mockup.store import create_schema as create_mockup_schema
from puspa.mockup.store import reset_tables as reset_mockup_tables
from puspa.mockup.templates import fill_template
from puspa.payments.prepare import PreparationService
from puspa.payments.schema import create_schema as create_payments_schema
from puspa.payments.schema import reset_tables as reset_payments_tables
from puspa.pwa.app import create_app
from puspa.rooms import (
    DelegationService,
    RoomMemoryStore,
    RoomStore,
    RoomTaskStore,
    SplitService,
)
from puspa.rooms.errors import NotRoomMemberError
from puspa.rooms.research import ResearchService
from puspa.rooms.schema import create_schema as create_rooms_schema
from puspa.rooms.schema import reset_tables as reset_rooms_tables

#: Marker planted in every canary row. It must never appear in a
#: response or row set handed to another user.
CANARY_MARKER = "rls-canary-e13"

#: Per-user tables in the differential sweep: (table, user id column).
PER_USER_TABLES = (
    ("users", "id"),
    ("sessions", "user_id"),
    ("vault", "user_id"),
    ("profile_facts", "user_id"),
    ("episodic", "user_id"),
    ("drafts", "user_id"),
    ("prepared_payments", "user_id"),
    ("mockups", "user_id"),
)

#: Room tables in the differential sweep: (table, rows a fellow
#: member (not the owner) may see). The outsider sees zero rows
#: in every one of them.
ROOM_TABLES_MEMBER_VISIBLE = (
    ("rooms", 1),
    ("room_members", 1),
    ("room_invites", 1),
    ("room_memories", 1),
    ("room_tasks", 1),
    ("room_money_approvals", 1),
    ("room_channel_links", 1),
    ("delegated_tasks", 1),
    ("delegated_task_steps", 1),
    ("room_splits", 1),
    ("split_shares", 2),
    ("split_payments", 1),
    ("split_settlements", 1),
    ("room_research_links", 1),
    ("meeting_minutes", 1),
)


@pytest.fixture(autouse=True)
def _differential_schemas(test_dsn):
    """Apply + empty the schemas this module reads (conftest does
    not apply the room/drafting/payments/mockup/meeting schemas)."""
    create_rooms_schema(test_dsn)
    create_drafting_schema(test_dsn)
    create_payments_schema(test_dsn)
    create_mockup_schema(test_dsn)
    create_meetings_schema(test_dsn)
    reset_rooms_tables(test_dsn)
    reset_drafting_tables(test_dsn)
    reset_payments_tables(test_dsn)
    reset_mockup_tables(test_dsn)
    reset_meetings_tables(test_dsn)
    yield
    reset_rooms_tables(test_dsn)
    reset_drafting_tables(test_dsn)
    reset_payments_tables(test_dsn)
    reset_mockup_tables(test_dsn)
    reset_meetings_tables(test_dsn)


@contextlib.contextmanager
def _as_app(test_dsn):
    """A connection as the application role (RLS applies).

    This is the RLS-layer-only fixture: raw SQL, no Python
    check in the way. The caller sets ``app.current_user_id``
    with :func:`puspa.accounts.rls.as_user` per transaction.
    """
    conn = psycopg.connect(test_dsn)
    try:
        conn.execute("SET ROLE puspa_app")
        conn.commit()
        yield conn
    finally:
        conn.close()


def _rows_visible_as(conn, user_id, table):
    """All rows of ``table`` visible to ``user_id`` as puspa_app."""
    with conn.transaction(), rls.as_user(conn, user_id), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT * FROM {table}")
        return cur.fetchall()


def _assert_no_canary(payload) -> None:
    """The canary marker appears nowhere in this payload."""
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    assert CANARY_MARKER not in text, f"canary marker leaked into: {text[:400]}"


def _drafting_service(test_dsn) -> DraftingService:
    return DraftingService(
        test_dsn,
        draft_store=PostgresDraftStore(test_dsn),
        fact_store=PostgresFactStore(test_dsn),
    )


def _prepare_payment(service, user_id):
    """One prepared payment for ``user_id`` (no ledger: the
    preparation maths never depend on it)."""
    return service.prepare_payment(
        user_id=user_id,
        recipient={
            "name": f"Recipient {user_id[:8]}",
            "account_ref": "1234567890",
            "verified": True,
            "verified_by": "test",
        },
        amount_rupiah=10_000,
        rail="qris",
        evidence=[
            {
                "kind": "seller_page",
                "source_url": "https://example.invalid/shop",
                "recipient_name": f"Recipient {user_id[:8]}",
                "account_ref": "1234567890",
            }
        ],
    )


def _mockup_doc(title):
    doc = fill_template("promo_banner", {"shop": "Canary Shop"})
    doc["title"] = title
    return doc


@pytest.fixture
def seeded(test_dsn, make_user):
    """Users A (outsider), B, C (room member), and the canary user,
    each with one row per per-user table, plus a canary room with
    one row per room table. All seeding runs as the table owner,
    so setup never depends on the layers under test."""
    a = make_user()
    b = make_user()
    c = make_user()
    canary = make_user(email=f"{CANARY_MARKER}@example.invalid")
    owner = psycopg.connect(test_dsn)
    try:
        with owner.cursor() as cur:
            for user in (a, b):
                cur.execute(
                    "INSERT INTO vault (user_id, name, ciphertext) VALUES (%s, %s, %s)",
                    (user["user_id"], "gmail_token", f"ct-{user['user_id'][:8]}"),
                )
                cur.execute(
                    "INSERT INTO sessions (user_id, token_hash, device_label) VALUES (%s, %s, %s)",
                    (user["user_id"], f"hash-{user['user_id'][:8]}", "laptop"),
                )
        owner.commit()
    finally:
        owner.close()
    profile.add_fact(a["user_id"], "nickname", "Alice", "user_stated", dsn=test_dsn)
    profile.add_fact(b["user_id"], "nickname", "Bob", "user_stated", dsn=test_dsn)
    episodic.record_episode(a["user_id"], "episode A", "user_message", dsn=test_dsn)
    episodic.record_episode(b["user_id"], "episode B", "user_message", dsn=test_dsn)

    drafts = _drafting_service(test_dsn)
    draft_a = drafts.draft(a["user_id"], "message", "brief A")
    draft_b = drafts.draft(b["user_id"], "message", "brief B")
    draft_canary = drafts.draft(canary["user_id"], "message", CANARY_MARKER)

    payments = PreparationService(test_dsn)
    payment_a = _prepare_payment(payments, a["user_id"])
    payment_b = _prepare_payment(payments, b["user_id"])
    payment_canary = payments.prepare_payment(
        user_id=canary["user_id"],
        recipient={
            "name": CANARY_MARKER,
            "account_ref": "1234567890",
            "verified": True,
            "verified_by": "test",
        },
        amount_rupiah=10_000,
        rail="qris",
        evidence=[
            {
                "kind": "seller_page",
                "source_url": "https://example.invalid/shop",
                "recipient_name": CANARY_MARKER,
                "account_ref": "1234567890",
            }
        ],
    )

    mockups = MockupStore(test_dsn)
    mockup_a = mockups.create(a["user_id"], _mockup_doc("Mockup A"))
    mockup_b = mockups.create(b["user_id"], _mockup_doc("Mockup B"))
    mockup_canary = mockups.create(canary["user_id"], _mockup_doc(CANARY_MARKER))

    # The canary user also owns one row per per-user table, each
    # carrying the marker, so any leak is recognisable.
    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO vault (user_id, name, ciphertext) VALUES (%s, %s, %s)",
            (canary["user_id"], CANARY_MARKER, "canary-secret"),
        )
        cur.execute(
            "INSERT INTO sessions (user_id, token_hash, device_label) VALUES (%s, %s, %s)",
            (canary["user_id"], "canary-token-hash", CANARY_MARKER),
        )
        conn.commit()
    profile.add_fact(canary["user_id"], "nickname", CANARY_MARKER, "user_stated", dsn=test_dsn)
    episodic.record_episode(canary["user_id"], CANARY_MARKER, "user_message", dsn=test_dsn)

    # The canary room: owner = canary, member = C, outsider = A.
    rooms = RoomStore(test_dsn)
    room = rooms.create_room(f"{CANARY_MARKER} room", canary["user_id"])
    rooms.add_member(room.id, c["user_id"], added_by=canary["user_id"], role="member")
    tasks = RoomTaskStore(test_dsn)
    task = tasks.add_task(
        room.id, title=CANARY_MARKER, created_by=canary["user_id"], owner_user_id=c["user_id"]
    )
    memories = RoomMemoryStore(test_dsn)
    shared_memory = memories.record_memory(
        room.id, author_user_id=canary["user_id"], content=f"{CANARY_MARKER} shared"
    )
    private_memory = memories.record_memory(
        room.id,
        author_user_id=canary["user_id"],
        content=f"{CANARY_MARKER} private",
        visibility="private",
    )
    splits = SplitService(test_dsn)
    split = splits.create_split(
        room.id,
        title=CANARY_MARKER,
        total_cents=10_000,
        payer_user_id=canary["user_id"],
        participant_user_ids=[canary["user_id"], c["user_id"]],
        created_by=canary["user_id"],
        kind="meal",
    )
    splits.record_payment(
        split.id, debtor_user_id=c["user_id"], amount_cents=1000, recorded_by=canary["user_id"]
    )
    research = ResearchService(test_dsn)
    link = research.add_link(
        room.id,
        added_by_user_id=canary["user_id"],
        url="https://tokopedia.com/rls-canary-e13",
        label=CANARY_MARKER,
    )
    delegation = DelegationService(test_dsn)
    delegation.delegate_to_agent(task.id, delegated_by=canary["user_id"], subagent_ref="canary")
    step = delegation.propose_step(
        task.id, by=canary["user_id"], description=f"{CANARY_MARKER} step", tool_name="read"
    )
    minutes = MeetingMinutesStore(test_dsn).write_minutes(
        room.id,
        author_user_id=canary["user_id"],
        transcript="We agreed to launch on Friday. Canary will prepare the report.",
        title=CANARY_MARKER,
    )
    with psycopg.connect(test_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO room_invites (room_id, inviter_user_id, kind, role, token_hash, "
            "status, expires_at) VALUES (%s, %s, 'link', 'member', %s, 'pending', "
            "now() + interval '1 day')",
            (room.id, canary["user_id"], f"{CANARY_MARKER}-token"),
        )
        cur.execute(
            "INSERT INTO room_money_approvals (action_id, room_id, approver_user_id) "
            "VALUES (%s, %s, %s)",
            (f"{CANARY_MARKER}-action", room.id, canary["user_id"]),
        )
        cur.execute(
            "INSERT INTO room_channel_links (platform, chat_id, room_id, linked_by) "
            "VALUES ('telegram', %s, %s, %s)",
            (f"{CANARY_MARKER}-chat", room.id, canary["user_id"]),
        )
        cur.execute(
            "INSERT INTO split_settlements (split_id, debtor_user_id, recipient_user_id, "
            "amount_cents, action_id, status, prepared_by) VALUES (%s, %s, %s, 1000, %s, "
            "'prepared', %s)",
            (
                split.id,
                c["user_id"],
                canary["user_id"],
                f"{CANARY_MARKER}-settlement",
                canary["user_id"],
            ),
        )
        conn.commit()
    return {
        "a": a,
        "b": b,
        "c": c,
        "canary": canary,
        "room": room,
        "task": task,
        "shared_memory": shared_memory,
        "private_memory": private_memory,
        "split": split,
        "link": link,
        "step": step,
        "minutes": minutes,
        "draft_a": draft_a,
        "draft_b": draft_b,
        "draft_canary": draft_canary,
        "payment_a": payment_a,
        "payment_b": payment_b,
        "payment_canary": payment_canary,
        "mockup_a": mockup_a,
        "mockup_b": mockup_b,
        "mockup_canary": mockup_canary,
    }


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


# --- RLS layer alone: raw SQL as puspa_app, no Python check -------------


@pytest.mark.parametrize("table,user_column", PER_USER_TABLES)
def test_rls_per_user_table_shows_only_own_rows(seeded, test_dsn, table, user_column):
    """As A, a per-user table shows only A's rows: B's row and the
    canary row are invisible (and the marker never appears)."""
    a_id = seeded["a"]["user_id"]
    b_id = seeded["b"]["user_id"]
    canary_id = seeded["canary"]["user_id"]
    with _as_app(test_dsn) as conn:
        rows = _rows_visible_as(conn, a_id, table)
    owners = {str(row[user_column]) for row in rows}
    assert owners == {a_id}, f"{table}: visible owners as A: {owners}"
    assert len(rows) == 1, f"{table}: expected exactly A's row, got {len(rows)}"
    _assert_no_canary(rows)
    with _as_app(test_dsn) as conn:
        rows_b = _rows_visible_as(conn, b_id, table)
    assert {str(row[user_column]) for row in rows_b} == {b_id}
    _assert_no_canary(rows_b)
    with _as_app(test_dsn) as conn:
        rows_canary = _rows_visible_as(conn, canary_id, table)
    assert len(rows_canary) == 1
    assert CANARY_MARKER in json.dumps(rows_canary, default=str)


@pytest.mark.parametrize("table,member_visible", ROOM_TABLES_MEMBER_VISIBLE)
def test_rls_room_table_hides_everything_from_outsider(seeded, test_dsn, table, member_visible):
    """As outsider A, every room table is empty; as fellow member
    C, the expected rows are visible (and the marker never leaks
    to A)."""
    a_id = seeded["a"]["user_id"]
    c_id = seeded["c"]["user_id"]
    with _as_app(test_dsn) as conn:
        rows_a = _rows_visible_as(conn, a_id, table)
    assert rows_a == [], f"{table}: outsider A sees {len(rows_a)} rows"
    _assert_no_canary(rows_a)
    with _as_app(test_dsn) as conn:
        rows_c = _rows_visible_as(conn, c_id, table)
    assert len(rows_c) == member_visible, (
        f"{table}: member C sees {len(rows_c)} rows, expected {member_visible}"
    )


def test_rls_private_room_memory_hidden_from_fellow_member(seeded, test_dsn):
    """A fellow member sees the room-visible memory but never
    another member's private memory, through RLS alone."""
    c_id = seeded["c"]["user_id"]
    canary_id = seeded["canary"]["user_id"]
    with _as_app(test_dsn) as conn:
        rows_c = _rows_visible_as(conn, c_id, "room_memories")
    contents = [row["content"] for row in rows_c]
    assert contents == [f"{CANARY_MARKER} shared"]
    with _as_app(test_dsn) as conn:
        rows_owner = _rows_visible_as(conn, canary_id, "room_memories")
    assert {row["content"] for row in rows_owner} == {
        f"{CANARY_MARKER} shared",
        f"{CANARY_MARKER} private",
    }


def test_rls_removed_member_loses_access_immediately(seeded, test_dsn):
    """After leaving, the member sees nothing of the room through
    RLS alone (plan section 12.1: removal is immediate)."""
    c_id = seeded["c"]["user_id"]
    room_id = seeded["room"].id
    RoomStore(test_dsn).leave_room(room_id, user_id=c_id)
    with _as_app(test_dsn) as conn:
        assert _rows_visible_as(conn, c_id, "rooms") == []
        assert _rows_visible_as(conn, c_id, "room_tasks") == []
        assert _rows_visible_as(conn, c_id, "room_memories") == []
        assert _rows_visible_as(conn, c_id, "room_splits") == []


def test_rls_without_current_user_sees_nothing(seeded, test_dsn):
    """Fail-closed: with no user set, the app role sees nothing
    in any covered table."""
    with _as_app(test_dsn) as conn, conn.transaction():
        for table, _ in PER_USER_TABLES:
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == []
        for table, _ in ROOM_TABLES_MEMBER_VISIBLE:
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == []


def test_rls_owner_sees_own_mockup(seeded, test_dsn):
    """Regression test for migration 0021: the owner's own mockup
    is visible through RLS (before the fix the app role got
    ``permission denied`` and the policy read a never-set
    variable, so even the owner saw nothing)."""
    a_id = seeded["a"]["user_id"]
    b_id = seeded["b"]["user_id"]
    with _as_app(test_dsn) as conn:
        rows_a = _rows_visible_as(conn, a_id, "mockups")
    assert [row["title"] for row in rows_a] == ["Mockup A"]
    with _as_app(test_dsn) as conn:
        rows_b = _rows_visible_as(conn, b_id, "mockups")
    assert [row["title"] for row in rows_b] == ["Mockup B"]


def test_canary_fixture_actually_seeds_every_table(seeded, test_dsn):
    """Guard against a vacuous fixture: as the table owner, every
    covered table holds at least one canary row."""
    canary_id = seeded["canary"]["user_id"]
    with psycopg.connect(test_dsn) as conn:
        for table, column in PER_USER_TABLES:
            (count,) = conn.execute(
                f"SELECT count(*) FROM {table} WHERE {column} = %s",
                (canary_id,),
            ).fetchone()
            assert count >= 1, f"{table}: no canary row"
        for table, _ in ROOM_TABLES_MEMBER_VISIBLE:
            (count,) = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert count >= 1, f"{table}: no canary row"


# --- Code layer alone: service calls (owner connections, RLS off) --------


@pytest.mark.parametrize(
    "reader",
    [
        lambda test_dsn, room_id, viewer: RoomTaskStore(test_dsn).list_tasks(
            room_id, viewer_user_id=viewer
        ),
        lambda test_dsn, room_id, viewer: RoomMemoryStore(test_dsn).list_room_memory(
            room_id, viewer_user_id=viewer
        ),
        lambda test_dsn, room_id, viewer: SplitService(test_dsn).list_splits(
            room_id, viewer_user_id=viewer
        ),
        lambda test_dsn, room_id, viewer: DelegationService(test_dsn).board(
            room_id, viewer_user_id=viewer
        ),
        lambda test_dsn, room_id, viewer: ResearchService(test_dsn).list_links(
            room_id, viewer_user_id=viewer
        ),
        lambda test_dsn, room_id, viewer: MeetingMinutesStore(test_dsn).list_minutes(
            room_id, viewer_user_id=viewer
        ),
    ],
)
def test_code_room_readers_reject_outsider(seeded, test_dsn, reader):
    """Each room reader rejects an outsider on its own Python
    membership check, with RLS disabled (owner connection)."""
    with pytest.raises(NotRoomMemberError):
        reader(test_dsn, seeded["room"].id, seeded["a"]["user_id"])


def test_code_room_readers_accept_member(seeded, test_dsn):
    """The same readers accept a fellow member and return the
    room's rows (code layer allows legitimate reads)."""
    room_id = seeded["room"].id
    viewer = seeded["c"]["user_id"]
    assert len(RoomTaskStore(test_dsn).list_tasks(room_id, viewer_user_id=viewer)) == 1
    assert len(RoomMemoryStore(test_dsn).list_room_memory(room_id, viewer_user_id=viewer)) == 1
    assert len(SplitService(test_dsn).list_splits(room_id, viewer_user_id=viewer)) == 1
    assert len(DelegationService(test_dsn).board(room_id, viewer_user_id=viewer)) == 1
    assert len(ResearchService(test_dsn).list_links(room_id, viewer_user_id=viewer)) == 1
    assert len(MeetingMinutesStore(test_dsn).list_minutes(room_id, viewer_user_id=viewer)) == 1


def test_code_private_room_memory_hidden_from_fellow_member(seeded, test_dsn):
    """A fellow member's code-layer list holds the room-visible
    memory only, never another member's private memory."""
    memories = RoomMemoryStore(test_dsn).list_room_memory(
        seeded["room"].id, viewer_user_id=seeded["c"]["user_id"]
    )
    assert [memory.content for memory in memories] == [f"{CANARY_MARKER} shared"]


def test_code_split_shares_and_payments_reject_outsider(seeded, test_dsn):
    """Share/payment readers check membership through the parent
    split, so an outsider is rejected there too."""
    split_id = seeded["split"].id
    outsider = seeded["a"]["user_id"]
    service = SplitService(test_dsn)
    with pytest.raises(NotRoomMemberError):
        service.list_shares(split_id, viewer_user_id=outsider)
    with pytest.raises(NotRoomMemberError):
        service.list_payments(split_id, viewer_user_id=outsider)
    member = seeded["c"]["user_id"]
    assert len(service.list_shares(split_id, viewer_user_id=member)) == 2
    assert len(service.list_payments(split_id, viewer_user_id=member)) == 1


def test_code_minutes_get_rejects_outsider(seeded, test_dsn):
    """Reading one minutes row by id rejects an outsider."""
    with pytest.raises(NotRoomMemberError):
        MeetingMinutesStore(test_dsn).get_minutes(
            seeded["minutes"].id, viewer_user_id=seeded["a"]["user_id"]
        )
    record = MeetingMinutesStore(test_dsn).get_minutes(
        seeded["minutes"].id, viewer_user_id=seeded["c"]["user_id"]
    )
    assert record is not None and record.title == CANARY_MARKER


def test_code_draft_payment_mockup_reject_other_user(seeded, test_dsn):
    """Single-object reads reject another user on the code layer
    alone: a missing draft, no payment, an unknown mockup."""
    outsider = seeded["a"]["user_id"]
    with pytest.raises(DraftNotFoundError):
        _drafting_service(test_dsn).get_draft(seeded["draft_canary"].id, user_id=outsider)
    assert (
        PreparationService(test_dsn).get_prepared_payment(
            seeded["payment_canary"].id, user_id=outsider
        )
        is None
    )
    with pytest.raises(KeyError):
        MockupStore(test_dsn).get(outsider, seeded["mockup_canary"]["id"])
    # And the owner's own reads still work through the code layer.
    assert (
        _drafting_service(test_dsn)
        .get_draft(seeded["draft_canary"].id, user_id=seeded["canary"]["user_id"])
        .id
        == seeded["draft_canary"].id
    )
    assert (
        PreparationService(test_dsn)
        .get_prepared_payment(seeded["payment_canary"].id, user_id=seeded["canary"]["user_id"])
        .id
        == seeded["payment_canary"].id
    )
    assert (
        MockupStore(test_dsn).get(seeded["canary"]["user_id"], seeded["mockup_canary"]["id"])[
            "title"
        ]
        == CANARY_MARKER
    )


def test_code_room_list_excludes_foreign_rooms(seeded, test_dsn):
    """A user's room list holds only rooms they belong to."""
    a_rooms = RoomStore(test_dsn).list_rooms_for_user(seeded["a"]["user_id"])
    assert a_rooms == []
    c_rooms = RoomStore(test_dsn).list_rooms_for_user(seeded["c"]["user_id"])
    assert [room.id for room in c_rooms] == [seeded["room"].id]


def test_code_removed_member_loses_access_immediately(seeded, test_dsn):
    """After leaving, the code layer rejects the ex-member at
    every room reader (plan section 12.1)."""
    c_id = seeded["c"]["user_id"]
    room_id = seeded["room"].id
    RoomStore(test_dsn).leave_room(room_id, user_id=c_id)
    with pytest.raises(NotRoomMemberError):
        RoomTaskStore(test_dsn).list_tasks(room_id, viewer_user_id=c_id)
    with pytest.raises(NotRoomMemberError):
        DelegationService(test_dsn).board(room_id, viewer_user_id=c_id)
    with pytest.raises(NotRoomMemberError):
        SplitService(test_dsn).list_splits(room_id, viewer_user_id=c_id)


def test_code_layer_trusts_caller_supplied_user_id(seeded, test_dsn, vault_master_key):
    """DOCUMENTED GAP, not a bug in this module: per-user stores
    trust the ``user_id`` they are handed. Called with B's id,
    they return B's data — there is no viewer parameter and no
    rejection. The protections for these reads are (1) the HTTP
    handler binding the session user id (tested below) and (2)
    the RLS layer (tested above), which is exactly why the RLS
    layer must stay correct: it is the only layer that rejects a
    caller that passes the wrong id. If a store ever gains a
    viewer check, this test must change with it."""
    b_id = seeded["b"]["user_id"]
    assert vault_store.list_names(b_id, dsn=test_dsn) == ["gmail_token"]
    assert [fact["value"] for fact in profile.list_facts(b_id, dsn=test_dsn)] == ["Bob"]
    assert [mockup["title"] for mockup in MockupStore(test_dsn).list(b_id)] == ["Mockup B"]
    assert [
        room.id for room in RoomStore(test_dsn).list_rooms_for_user(seeded["canary"]["user_id"])
    ] == [seeded["room"].id]


# --- HTTP layer: the real PWA app, logged in as A --------------------------


def test_http_cross_user_reads_are_rejected(seeded, test_dsn):
    """Logged in as outsider A, every cross-user read is 403/404
    and the body never carries the canary marker."""
    room_id = seeded["room"].id
    with _client(seeded["a"]["user_id"], test_dsn) as client:
        for path, status in (
            (f"/api/rooms/{room_id}/tasks", 403),
            (f"/api/rooms/{room_id}/splits", 403),
            (f"/api/drafts/{seeded['draft_canary'].id}", 404),
            (f"/api/payments/{seeded['payment_canary'].id}", 404),
            (f"/api/mockups/{seeded['mockup_canary']['id']}", 404),
        ):
            response = client.get(path)
            assert response.status_code == status, f"{path}: {response.status_code}"
            _assert_no_canary(response.text)


def test_http_list_endpoints_never_leak_canary(seeded, test_dsn):
    """Logged in as A, every list endpoint returns only A's rows;
    the canary marker appears in none of them."""
    with _client(seeded["a"]["user_id"], test_dsn) as client:
        for path in (
            "/api/rooms",
            "/api/drafts",
            "/api/payments",
            "/api/mockups",
            "/api/voice/profile",
        ):
            response = client.get(path)
            assert response.status_code == 200, f"{path}: {response.status_code}"
            _assert_no_canary(response.text)


def test_http_owner_reads_return_own_rows(seeded, test_dsn):
    """Logged in as the canary user, the same endpoints return the
    canary rows (the code layer allows legitimate reads)."""
    canary_id = seeded["canary"]["user_id"]
    with _client(canary_id, test_dsn) as client:
        rooms = client.get("/api/rooms").json()["rooms"]
        assert [room["id"] for room in rooms] == [seeded["room"].id]
        board = client.get(f"/api/rooms/{seeded['room'].id}/tasks").json()
        assert [task["title"] for task in board["tasks"]] == [CANARY_MARKER]
        splits = client.get(f"/api/rooms/{seeded['room'].id}/splits").json()["splits"]
        assert [split["title"] for split in splits] == [CANARY_MARKER]
        drafts = client.get("/api/drafts").json()["drafts"]
        assert [draft["brief"] for draft in drafts] == [CANARY_MARKER]
        payments = client.get("/api/payments").json()["payments"]
        assert len(payments) == 1 and CANARY_MARKER in json.dumps(payments)
        mockups = client.get("/api/mockups").json()["mockups"]
        assert [mockup["title"] for mockup in mockups] == [CANARY_MARKER]


def test_http_removed_member_gets_403(seeded, test_dsn):
    """After leaving, the ex-member's board and splits reads are
    403 through the HTTP layer too."""
    c_id = seeded["c"]["user_id"]
    room_id = seeded["room"].id
    RoomStore(test_dsn).leave_room(room_id, user_id=c_id)
    with _client(c_id, test_dsn) as client:
        assert client.get(f"/api/rooms/{room_id}/tasks").status_code == 403
        assert client.get(f"/api/rooms/{room_id}/splits").status_code == 403
        assert client.get("/api/rooms").json()["rooms"] == []
