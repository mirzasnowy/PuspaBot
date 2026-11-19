"""API tests for the B11 split endpoints (``puspa/pwa/rooms_api.py``).

Covers the HTTP layer only (the split maths live in
``tests/test_rooms_splitting_b11.py``): a logged-in member
creates a split and records payments, a non-member gets 403,
an anonymous caller gets 401, overpaying gets 400, and a
settlement preparation is 403 until the money gate approves
it (the room owner passes alone).

These tests run against the real ``puspa.pwa.app.create_app``:
the routes are mounted there with the app's own ``need_user``
login resolver, so the paths, login gate, status codes, and
JSON shapes tested here are the contract the live PWA serves.
The room schema is applied by the autouse fixture from
tests.rooms_helpers.
"""

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from tests.rooms_helpers import _rooms_schema, add_room_member, make_room

# The imported names are fixtures used by pytest injection (not
# by direct call); listing them keeps ruff from flagging the
# imports as unused or shadowed by the test parameters.
__all__ = ["_rooms_schema", "add_room_member", "make_room"]


def _client(user_id, test_dsn):
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    return TestClient(app)


@pytest.fixture
def trio(make_room, add_room_member):
    made = make_room()
    room = made["room"]
    owner = made["owner"]["user_id"]
    second = add_room_member(room.id, owner)["user_id"]
    third = add_room_member(room.id, owner)["user_id"]
    return {"room": room, "owner": owner, "second": second, "third": third}


def _create_split(client, room_id, payer, participants):
    return client.post(
        f"/api/rooms/{room_id}/splits",
        json={
            "title": "Dinner together",
            "total_cents": 30_000_000,
            "payer_user_id": payer,
            "participant_user_ids": participants,
            "kind": "meal",
        },
    )


def test_split_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/rooms/{room_id}/splits" in paths
    assert "/api/rooms/{room_id}/splits/{split_id}/payments" in paths
    assert "/api/rooms/{room_id}/splits/{split_id}/settlements/{debtor_user_id}/prepare" in paths
    with _client(user_id, test_dsn) as client:
        assert client.get("/api/rooms/00000000-0000-0000-0000-000000000000/splits").status_code in (
            403,
            404,
        )
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert (
            client.get("/api/rooms/00000000-0000-0000-0000-000000000000/splits").status_code == 401
        )


def test_create_and_list_splits(trio, test_dsn):
    room_id = trio["room"].id
    with _client(trio["owner"], test_dsn) as client:
        assert client.get(f"/api/rooms/{room_id}/splits").json() == {"splits": []}
        resp = _create_split(
            client, room_id, trio["owner"], [trio["owner"], trio["second"], trio["third"]]
        )
        assert resp.status_code == 200, resp.text
        split = resp.json()
        assert split["title"] == "Dinner together"
        assert split["status"] == "open"
        assert [s["share_cents"] for s in split["shares"]] == [10_000_000] * 3
        assert split["shares"][0]["is_paid"] is True
        assert split["shares"][1]["remaining_cents"] == 10_000_000
        listed = client.get(f"/api/rooms/{room_id}/splits").json()["splits"]
        assert [s["id"] for s in listed] == [split["id"]]


def test_splits_forbidden_for_non_member(trio, make_user, test_dsn):
    room_id = trio["room"].id
    outsider = make_user()["user_id"]
    with _client(outsider, test_dsn) as client:
        assert client.get(f"/api/rooms/{room_id}/splits").status_code == 403
        resp = _create_split(client, room_id, outsider, [outsider, trio["owner"], trio["second"]])
        assert resp.status_code == 403


def test_create_split_bad_input_is_400(trio, test_dsn):
    room_id = trio["room"].id
    with _client(trio["owner"], test_dsn) as client:
        resp = client.post(
            f"/api/rooms/{room_id}/splits",
            json={
                "title": "Bad split",
                "total_cents": 100.5,
                "payer_user_id": trio["owner"],
                "participant_user_ids": [trio["owner"], trio["second"]],
            },
        )
        assert resp.status_code == 400
        resp = client.post(
            f"/api/rooms/{room_id}/splits",
            json={
                "title": "Bad split",
                "total_cents": 30_000_000,
                "payer_user_id": trio["owner"],
                "participant_user_ids": [trio["owner"]],
            },
        )
        assert resp.status_code == 400


def test_record_payment_and_overpayment(trio, test_dsn):
    room_id = trio["room"].id
    with _client(trio["owner"], test_dsn) as client:
        split = _create_split(
            client, room_id, trio["owner"], [trio["owner"], trio["second"], trio["third"]]
        ).json()
        resp = client.post(
            f"/api/rooms/{room_id}/splits/{split['id']}/payments",
            json={"debtor_user_id": trio["second"], "amount_cents": 4_000_000},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["split"]["status"] == "partially_paid"
        assert resp.json()["split"]["shares"][1]["remaining_cents"] == 6_000_000
        over = client.post(
            f"/api/rooms/{room_id}/splits/{split['id']}/payments",
            json={"debtor_user_id": trio["second"], "amount_cents": 6_000_001},
        )
        assert over.status_code == 400
        rest = client.post(
            f"/api/rooms/{room_id}/splits/{split['id']}/payments",
            json={"debtor_user_id": trio["second"], "amount_cents": 6_000_000},
        )
        assert rest.status_code == 200
        assert rest.json()["split"]["shares"][1]["is_paid"] is True


def test_prepare_settlement_needs_gate_then_succeeds(trio, test_dsn):
    room_id = trio["room"].id
    with _client(trio["second"], test_dsn) as client:
        split = _create_split(
            client, room_id, trio["owner"], [trio["owner"], trio["second"], trio["third"]]
        ).json()
        # The second member is not the owner and has not been
        # approved by a second person: 403.
        resp = client.post(
            f"/api/rooms/{room_id}/splits/{split['id']}/settlements/{trio['second']}/prepare"
        )
        assert resp.status_code == 403
    with _client(trio["owner"], test_dsn) as client:
        # The room owner passes the money gate alone.
        resp = client.post(
            f"/api/rooms/{room_id}/splits/{split['id']}/settlements/{trio['second']}/prepare"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "prepared"
        assert body["recipient_user_id"] == trio["owner"]
        assert body["amount_cents"] == 10_000_000
        assert body["executed"] is False
