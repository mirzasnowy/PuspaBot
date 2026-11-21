"""Tests for group chat names (V2-27, issue #140).

The one shared name resolver (``puspa/rooms/names.py``) and the
own display-name API (``puspa/pwa/names_api.py``):

- A user sets their own display name; the edit is visible on
  the next read; nobody else may set it; empty and overlong
  names are refused.
- Two users whose bots are named differently in their persona
  files see their own bot's name (not one hardcoded name), and
  a bot with no name file falls back to "Puspa".
- A room member list shows display names with zero raw UUIDs:
  a member with no name is "Anggota N" by member-list position.
- Decisions render agreed-by user ids as names, never UUIDs.
- The HTTP routes require login and only ever act on the
  caller themself.

Seed data is real: users through ``make_user``, rooms through
``RoomStore``, decisions through ``RoomMemoryStore``, bot names
through real persona files in temporary homes.
"""

import re

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.rooms import memory as room_memory_module
from puspa.rooms.names import (
    DisplayNameStore,
    render_names_in_text,
    resolve_memory_authors,
    resolve_room_bot_names,
    resolve_room_members,
    resolve_room_names,
    resolve_user_names,
)
from puspa.rooms.names import (
    create_schema as create_names_schema,
)
from puspa.rooms.rooms import RoomStore

UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


@pytest.fixture(autouse=True)
def _names_schema(test_dsn):
    create_names_schema(test_dsn)


def _make_room(test_dsn, owner_id, name="Keluarga"):
    return RoomStore(test_dsn).create_room(name, owner_id)


def _bot_home(tmp_path, name, label):
    home = tmp_path / label
    home.mkdir()
    (home / "IDENTITY.md").write_text(f"# Identity\n\nName: {name}\n", encoding="utf-8")
    return home


def test_display_name_set_get_and_edit(make_user, test_dsn):
    user_id = make_user()["user_id"]
    store = DisplayNameStore(test_dsn)
    assert store.get_display_name(user_id) is None
    assert store.set_display_name(user_id, "  Rani  ") == "Rani"
    assert store.get_display_name(user_id) == "Rani"
    # An edit takes effect on the very next read.
    assert store.set_display_name(user_id, "Rani Wijaya") == "Rani Wijaya"
    assert store.get_display_name(user_id) == "Rani Wijaya"


def test_display_name_validation(make_user, test_dsn):
    user_id = make_user()["user_id"]
    store = DisplayNameStore(test_dsn)
    for bad in ("", "   ", "x" * 101):
        with pytest.raises(ValueError):
            store.set_display_name(user_id, bad)
    assert store.get_display_name(user_id) is None


def test_display_name_only_own_user_may_set_it(make_user, test_dsn):
    mine = make_user()["user_id"]
    other = make_user()["user_id"]
    store = DisplayNameStore(test_dsn)
    with pytest.raises(PermissionError):
        store.set_display_name(other, "Penyamar", edited_by=mine)
    assert store.get_display_name(other) is None
    with pytest.raises(ValueError):
        store.set_display_name("not-a-uuid", "Nama")


def test_two_bots_with_different_names_in_one_room(make_user, test_dsn, tmp_path):
    first = make_user()
    second = make_user()
    room = _make_room(test_dsn, first["user_id"])
    RoomStore(test_dsn).add_member(room.id, second["user_id"], added_by=first["user_id"])
    homes = {
        first["user_id"]: _bot_home(tmp_path, "Bimo", "home-first"),
        second["user_id"]: _bot_home(tmp_path, "Sari", "home-second"),
    }
    bot_names = resolve_room_bot_names(room.id, test_dsn, bot_homes=homes)
    assert bot_names[first["user_id"]] == "Bimo"
    assert bot_names[second["user_id"]] == "Sari"
    assert bot_names[first["user_id"]] != bot_names[second["user_id"]]


def test_bot_name_defaults_to_puspa_without_a_persona_file(make_user, test_dsn, tmp_path):
    user = make_user()
    room = _make_room(test_dsn, user["user_id"])
    empty_home = tmp_path / "home-empty"
    empty_home.mkdir()
    bot_names = resolve_room_bot_names(room.id, test_dsn, bot_homes={user["user_id"]: empty_home})
    assert bot_names[user["user_id"]] == "Puspa"


def test_member_list_shows_names_never_raw_uuids(make_user, test_dsn):
    owner = make_user()
    member = make_user()
    guest = make_user()
    store = DisplayNameStore(test_dsn)
    store.set_display_name(owner["user_id"], "Rani")
    room = _make_room(test_dsn, owner["user_id"])
    RoomStore(test_dsn).add_member(room.id, member["user_id"], added_by=owner["user_id"])
    RoomStore(test_dsn).add_member(
        room.id, guest["user_id"], added_by=owner["user_id"], role="guest"
    )
    named = resolve_room_members(room.id, test_dsn)
    assert [m.display_name for m in named] == ["Rani", "Anggota 2", "Anggota 3"]
    rendered = "\n".join(f"{m.display_name} ({m.role})" for m in named)
    assert not UUID_RE.search(rendered)
    assert owner["user_id"] not in rendered
    assert member["user_id"] not in rendered


def test_renamed_member_reflected_on_next_render(make_user, test_dsn):
    owner = make_user()
    member = make_user()
    store = DisplayNameStore(test_dsn)
    room = _make_room(test_dsn, owner["user_id"])
    RoomStore(test_dsn).add_member(room.id, member["user_id"], added_by=owner["user_id"])
    assert resolve_room_members(room.id, test_dsn)[1].display_name == "Anggota 2"
    store.set_display_name(member["user_id"], "Dimas")
    assert resolve_room_members(room.id, test_dsn)[1].display_name == "Dimas"


def test_room_names_resolve_members_and_bots_together(make_user, test_dsn, tmp_path):
    owner = make_user()
    member = make_user()
    DisplayNameStore(test_dsn).set_display_name(member["user_id"], "Dimas")
    room = _make_room(test_dsn, owner["user_id"])
    RoomStore(test_dsn).add_member(room.id, member["user_id"], added_by=owner["user_id"])
    resolved = resolve_room_names(
        room.id,
        test_dsn,
        bot_homes={owner["user_id"]: _bot_home(tmp_path, "Bimo", "home-owner")},
    )
    assert resolved.name_of(owner["user_id"]) == "Anggota 1"
    assert resolved.name_of(member["user_id"]) == "Dimas"
    assert resolved.name_of("00000000-0000-0000-0000-000000000000") == "Anggota"
    assert resolved.bot_names[owner["user_id"]] == "Bimo"


def test_decision_renders_agreed_by_ids_as_names(make_user, test_dsn):
    owner = make_user()
    member = make_user()
    store = DisplayNameStore(test_dsn)
    store.set_display_name(owner["user_id"], "Rani")
    store.set_display_name(member["user_id"], "Dimas")
    room = _make_room(test_dsn, owner["user_id"])
    RoomStore(test_dsn).add_member(room.id, member["user_id"], added_by=owner["user_id"])
    memory_store = room_memory_module.RoomMemoryStore(test_dsn)
    memory_store.record_decision(
        room.id,
        author_user_id=owner["user_id"],
        decision="Piknik hari Minggu.",
        agreed_by_user_ids=[owner["user_id"], member["user_id"]],
    )
    rows = memory_store.list_room_memory(room.id, viewer_user_id=owner["user_id"])
    assert len(rows) == 1
    named = resolve_memory_authors(rows, test_dsn)
    assert named[0].author_display_name == "Rani"
    assert named[0].content == "Piknik hari Minggu.\nAgreed by: Rani, Dimas"
    assert not UUID_RE.search(named[0].content)


def test_render_names_in_text_falls_back_for_unknown_ids():
    rendered = render_names_in_text(
        "Agreed by: 11111111-1111-1111-1111-111111111111",
        {"11111111-1111-1111-1111-111111111111": "Rani"},
    )
    assert rendered == "Agreed by: Rani"
    rendered = render_names_in_text("Agreed by: 22222222-2222-2222-2222-222222222222", {})
    assert rendered == "Agreed by: Anggota"
    assert render_names_in_text("No ids here.", {}) == "No ids here."


def test_resolve_user_names_never_returns_a_uuid(make_user, test_dsn):
    user = make_user()
    names = resolve_user_names([user["user_id"]], test_dsn)
    assert names[user["user_id"]] == "Anggota"
    assert not UUID_RE.search(names[user["user_id"]])


def test_display_name_api_requires_login_and_sets_own_name(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    assert "/api/profile/display-name" in {route.path for route in app.routes}
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/profile/display-name").status_code == 401
        assert (
            client.post("/api/profile/display-name", json={"display_name": "Rani"}).status_code
            == 401
        )
    with TestClient(app) as client:
        assert client.get("/api/profile/display-name").json() == {
            "user_id": user_id,
            "display_name": None,
        }
        saved = client.post("/api/profile/display-name", json={"display_name": "Rani"})
        assert saved.status_code == 200
        assert saved.json() == {"user_id": user_id, "display_name": "Rani"}
        assert client.get("/api/profile/display-name").json()["display_name"] == "Rani"
        assert (
            client.post("/api/profile/display-name", json={"display_name": ""}).status_code == 400
        )
