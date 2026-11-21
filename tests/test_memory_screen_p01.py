"""Tests for the P-01 memory screen (``puspa/pwa/memory_api.py``).

Covers the service and the HTTP layer against the real
``puspa.pwa.app.create_app``: the facts list with source labels
and confidence (stored, user-stated, and inferred cases),
pagination and the limit cap, single-fact read with history,
correct (edit) and forget flows with their ledger events,
proposal confirm/reject, export download, consent status and
withdrawal, guest-mode toggle, room memory kept separate from
personal memory (membership-gated, private rows hidden from
other members), and guest mode refusing writes at the two real
write funnels (memory provider + turn-loop intercepts).

Seed data is real: facts recorded through ``record_fact`` /
``profile.add_fact`` and episodes through ``record_episode``,
so the screen renders from the real stores, never mock data.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from puspa.ledger import Ledger
from puspa.memory import episodic
from puspa.memory import facts as facts_module
from puspa.memory import profile as profile_module
from puspa.memory.provider import DefaultPgVectorProvider
from puspa.pwa.app import create_app
from puspa.pwa.memory_api import memory_rights_task_id
from puspa.rooms import memory as room_memory_module
from puspa.rooms.rooms import RoomStore
from puspa.turns.intercepts import InterceptedTools


def _client(user_id, test_dsn):
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    return TestClient(app), app


def _seed_fact(user_id, test_dsn, key="hometown", content="Karawang", **kwargs):
    return profile_module.add_fact(user_id, key, content, "user_stated", dsn=test_dsn, **kwargs)


def _rights_events(test_dsn, user_id):
    events = Ledger(test_dsn).events(memory_rights_task_id(user_id), event_type="evidence_recorded")
    return [e for e in events if (e["content"] or {}).get("kind") == "memory_right"]


def _make_room(test_dsn, owner_id):
    store = RoomStore(test_dsn)
    return store.create_room("family", owner_id)


def test_routes_exist_and_require_login(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/memory" in paths
    assert "/api/memory/facts/{fact_id}" in paths
    assert "/api/memory/facts/{fact_id}/forget" in paths
    assert "/api/memory/export" in paths
    assert "/api/memory/consent/withdraw" in paths
    assert "/api/memory/guest" in paths
    assert "/api/memory/rooms/{room_id}" in paths
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/memory").status_code == 401
        assert client.get("/api/memory/export").status_code == 401
        assert client.post("/api/memory/guest", json={"guest": True}).status_code == 401


def test_screen_lists_facts_with_source_label_and_confidence(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    stored = profile_module.add_fact(
        user_id,
        "favorite_food",
        "nasi padang",
        "user_stated",
        dsn=test_dsn,
        confidence=0.8,
        source_detail="from the chat on 3 November 2026",
    )
    stated = _seed_fact(user_id, test_dsn)
    inferred = profile_module.add_fact(user_id, "likes_spicy", "yes", "inferred", dsn=test_dsn)
    client, _ = _client(user_id, test_dsn)
    with client:
        screen = client.get("/api/memory").json()
        by_id = {fact["id"]: fact for fact in screen["facts"]}
        assert screen["total_facts"] == 3
        assert screen["consent"]["status"] == "none"
        assert screen["guest_mode"] is False
        assert by_id[stored["id"]]["source_label"] == "from the chat on 3 November 2026"
        assert by_id[stored["id"]]["confidence"] == 0.8
        assert by_id[stored["id"]]["confidence_stored"] is True
        # A user-stated fact with no stored score is certain by definition.
        assert by_id[stated["id"]]["confidence"] == 1.0
        assert by_id[stated["id"]]["confidence_stored"] is False
        assert by_id[stated["id"]]["source_label"].startswith("you told Puspa on ")
        # An inferred proposal with no stored score is an unverified guess.
        assert by_id[inferred["id"]]["confidence"] == 0.5
        assert "waiting for your confirmation" in by_id[inferred["id"]]["source_label"]
        assert len(screen["proposals"]) == 1
        assert screen["proposals"][0]["id"] == inferred["id"]


def test_facts_pagination_caps_limit_and_filters_history(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    first = _seed_fact(user_id, test_dsn, key="city", content="Karawang")
    second = _seed_fact(user_id, test_dsn, key="city", content="Jakarta")
    assert first["id"] != second["id"]
    assert second.get("conflicts_with") == first["id"]
    _seed_fact(user_id, test_dsn, key="hobby", content="chess")
    client, _ = _client(user_id, test_dsn)
    with client:
        capped = client.get("/api/memory?limit=500").json()
        assert capped["limit"] == 100
        assert capped["total_facts"] == 2
        page_one = client.get("/api/memory?limit=1&offset=0").json()
        page_two = client.get("/api/memory?limit=1&offset=1").json()
        assert page_one["facts"][0]["id"] != page_two["facts"][0]["id"]
        history = client.get("/api/memory?status=history").json()
        assert {fact["id"] for fact in history["facts"]} == {first["id"]}
        assert history["facts"][0]["status"] == "replaced"
        bad = client.get("/api/memory?limit=abc")
        assert bad.status_code == 400
        unknown = client.get("/api/memory?status=nope")
        assert unknown.status_code == 400


def test_fact_read_shows_history_and_unknown_is_404(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    old = _seed_fact(user_id, test_dsn, key="city", content="Karawang")
    new = _seed_fact(user_id, test_dsn, key="city", content="Jakarta")
    client, _ = _client(user_id, test_dsn)
    with client:
        detail = client.get(f"/api/memory/facts/{new['id']}").json()
        assert detail["fact"]["content"] == "Jakarta"
        assert [row["id"] for row in detail["history"]] == [old["id"], new["id"]]
        missing = client.get(f"/api/memory/facts/{uuid.uuid4()}")
        assert missing.status_code == 404


def test_correct_fact_supersedes_and_audits_without_content(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    fact = _seed_fact(user_id, test_dsn)
    client, _ = _client(user_id, test_dsn)
    with client:
        corrected = client.patch(
            f"/api/memory/facts/{fact['id']}", json={"content": "Bandung"}
        ).json()
        assert corrected["fact"]["content"] == "Bandung"
        assert corrected["fact"]["source"] == "user_confirmed"
        assert corrected["previous_id"] == fact["id"]
        screen = client.get("/api/memory").json()
        assert screen["facts"][0]["content"] == "Bandung"
        history = client.get("/api/memory?status=history").json()
        assert [row["id"] for row in history["facts"]] == [fact["id"]]
        empty = client.patch(f"/api/memory/facts/{fact['id']}", json={"content": "  "})
        assert empty.status_code == 400
    events = _rights_events(test_dsn, user_id)
    assert len(events) == 1
    assert events[0]["content"]["operation"] == "correct_fact"
    assert events[0]["content"]["fact_id"] == fact["id"]
    assert "Bandung" not in str(events[0]["content"])
    assert "Karawang" not in str(events[0]["content"])


def test_forget_fact_deletes_fact_and_versions_without_content(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    old = _seed_fact(user_id, test_dsn, key="city", content="Karawang")
    new = _seed_fact(user_id, test_dsn, key="city", content="Jakarta")
    other = _seed_fact(user_id, test_dsn, key="hobby", content="chess")
    client, _ = _client(user_id, test_dsn)
    with client:
        forgotten = client.post(f"/api/memory/facts/{new['id']}/forget").json()
        assert forgotten["forgotten"] == 2
        assert set(forgotten["forgotten_ids"]) == {old["id"], new["id"]}
        screen = client.get("/api/memory").json()
        assert [fact["id"] for fact in screen["facts"]] == [other["id"]]
        assert client.get(f"/api/memory/facts/{old['id']}").status_code == 404
        missing = client.post(f"/api/memory/facts/{uuid.uuid4()}/forget")
        assert missing.status_code == 404
    events = _rights_events(test_dsn, user_id)
    assert len(events) == 1
    assert events[0]["content"]["operation"] == "forget_fact"
    assert "Karawang" not in str(events[0]["content"])
    assert "Jakarta" not in str(events[0]["content"])


def test_delete_endpoint_is_the_same_forget(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    fact = _seed_fact(user_id, test_dsn)
    client, _ = _client(user_id, test_dsn)
    with client:
        deleted = client.delete(f"/api/memory/facts/{fact['id']}").json()
        assert deleted["forgotten"] == 1
        assert client.get("/api/memory").json()["total_facts"] == 0


def test_proposal_confirm_and_reject(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    inferred = profile_module.add_fact(
        user_id, "likes_spicy", "yes", "inferred", dsn=test_dsn, confidence=0.7
    )
    rejected = profile_module.add_fact(user_id, "likes_sweet", "no", "inferred", dsn=test_dsn)
    client, _ = _client(user_id, test_dsn)
    with client:
        confirmed = client.post(f"/api/memory/facts/{inferred['id']}/confirm").json()
        assert confirmed["fact"]["status"] == "active"
        assert confirmed["fact"]["source"] == "user_confirmed"
        assert confirmed["fact"]["confidence"] == 0.7
        refused = client.post(f"/api/memory/facts/{rejected['id']}/reject").json()
        assert refused["fact"]["status"] == "rejected"
        screen = client.get("/api/memory").json()
        assert screen["total_facts"] == 1
        assert screen["proposals"] == []
        missing = client.post(f"/api/memory/facts/{uuid.uuid4()}/confirm")
        assert missing.status_code == 404
    operations = [e["content"]["operation"] for e in _rights_events(test_dsn, user_id)]
    assert operations == ["confirm_proposal", "reject_proposal"]


def test_export_download_contains_facts_and_episodes(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    fact = _seed_fact(user_id, test_dsn)
    episode = episodic.record_episode(user_id, "hello puspa", "user_message", dsn=test_dsn)
    client, _ = _client(user_id, test_dsn)
    with client:
        response = client.get("/api/memory/export")
        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]
        assert "puspa-memory-export.json" in response.headers["content-disposition"]
        document = response.json()
        assert document["format"] == "puspa-user-export"
        assert document["user"]["id"] == user_id
        assert {row["id"] for row in document["sections"]["profile_facts"]} == {fact["id"]}
        assert {row["id"] for row in document["sections"]["episodic"]} == {episode["id"]}
        assert document["sections"]["profile_facts"][0]["confidence"] is None


def test_consent_status_and_withdrawal(make_user, test_dsn):
    from puspa.consent.consent import record_consent

    user = make_user()
    user_id = user["user_id"]
    client, _ = _client(user_id, test_dsn)
    with client:
        assert client.get("/api/memory/consent").json()["status"] == "none"
        missing = client.post("/api/memory/consent/withdraw")
        assert missing.status_code == 404
        record_consent(user_id, dsn=test_dsn)
        assert client.get("/api/memory/consent").json()["status"] == "granted"
        withdrawn = client.post("/api/memory/consent/withdraw").json()
        assert withdrawn["status"] == "withdrawn"
        assert withdrawn["withdrawn_at"] is not None
        # Withdrawing twice is idempotent, not an error.
        again = client.post("/api/memory/consent/withdraw").json()
        assert again["status"] == "withdrawn"
        assert client.get("/api/memory").json()["consent"]["status"] == "withdrawn"
    operations = [e["content"]["operation"] for e in _rights_events(test_dsn, user_id)]
    assert operations == ["withdraw_consent"]


def test_guest_mode_toggle_and_state(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    client, _ = _client(user_id, test_dsn)
    with client:
        assert client.get("/api/memory").json()["guest_mode"] is False
        on = client.post("/api/memory/guest", json={"guest": True}).json()
        assert on["guest_mode"] is True
        assert client.get("/api/memory").json()["guest_mode"] is True
        bad = client.post("/api/memory/guest", json={"guest": "yes"})
        assert bad.status_code == 400
        off = client.post("/api/memory/guest", json={"guest": False}).json()
        assert off["guest_mode"] is False


def test_guest_mode_refuses_provider_writes_but_not_reads(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    provider = DefaultPgVectorProvider(config={"dsn": test_dsn})
    assert provider.guest_mode is False
    provider.set_guest_mode(True)
    result = provider.store(user_id, {"content": "guest episode", "layer": "episodic"})
    assert result["stored"] is False
    assert result["guest_mode"] is True
    assert episodic.list_episodes(user_id, dsn=test_dsn) == []
    provider.set_guest_mode(False)
    stored = provider.store(user_id, {"content": "normal episode", "layer": "episodic"})
    assert stored["content"] == "normal episode"
    assert len(episodic.list_episodes(user_id, dsn=test_dsn)) == 1


def test_guest_mode_refuses_intercepted_memory_records(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    store = facts_module.MemoryFactStore()
    tools = InterceptedTools(user_id=user_id, memory_store=store, guest_mode=True)
    result = tools.try_intercept(
        "memory", {"action": "record", "key": "city", "content": "Jakarta"}
    )
    assert result["result"]["recorded"]["stored"] is False
    assert result["result"]["recorded"]["guest_mode"] is True
    assert facts_module.list_active_facts(user_id, store=store) == []
    tools.guest_mode = False
    recorded = tools.try_intercept(
        "memory", {"action": "record", "key": "city", "content": "Jakarta"}
    )
    assert recorded["result"]["recorded"]["stored"] is True
    assert len(facts_module.list_active_facts(user_id, store=store)) == 1


def test_room_memory_is_separate_and_membership_gated(make_user, test_dsn):
    owner = make_user()
    member = make_user()
    stranger = make_user()
    room = _make_room(test_dsn, owner["user_id"])
    RoomStore(test_dsn).add_member(
        room.id, member["user_id"], added_by=owner["user_id"], role="member"
    )
    shared = room_memory_module.record_memory(
        room.id,
        author_user_id=owner["user_id"],
        content="family decision: reunion in June",
        kind="decision",
        dsn=test_dsn,
    )
    private = room_memory_module.record_memory(
        room.id,
        author_user_id=member["user_id"],
        content="my private note",
        kind="note",
        visibility="private",
        dsn=test_dsn,
    )
    # Personal memory of the owner must never leak into room memory.
    _seed_fact(owner["user_id"], test_dsn)
    owner_client, _ = _client(owner["user_id"], test_dsn)
    member_client, _ = _client(member["user_id"], test_dsn)
    stranger_client, _ = _client(stranger["user_id"], test_dsn)
    with owner_client:
        seen = owner_client.get(f"/api/memory/rooms/{room.id}").json()
        assert {row["id"] for row in seen["memories"]} == {shared.id}
        # The owner never sees another member's private row.
        assert all(row["content"] != "my private note" for row in seen["memories"])
        assert all(row["content"] != "Karawang" for row in seen["memories"])
        posted = owner_client.post(
            f"/api/memory/rooms/{room.id}/memories",
            json={"content": "shared note", "kind": "note", "visibility": "room"},
        ).json()
        assert posted["memory"]["content"] == "shared note"
        deleted = owner_client.delete(
            f"/api/memory/rooms/{room.id}/memories/{posted['memory']['id']}"
        ).json()
        assert deleted["deleted"] is True
    with member_client:
        seen = member_client.get(f"/api/memory/rooms/{room.id}").json()
        assert {row["id"] for row in seen["memories"]} == {shared.id, private.id}
    with stranger_client:
        forbidden = stranger_client.get(f"/api/memory/rooms/{room.id}")
        assert forbidden.status_code == 403
        forbidden_post = stranger_client.post(
            f"/api/memory/rooms/{room.id}/memories",
            json={"content": "intruder", "kind": "note"},
        )
        assert forbidden_post.status_code == 403


def test_memory_page_renders_facts(make_user, test_dsn):
    """The /memory page serves the SPA shell with the facts in its
    bootstrap payload when the dist is built; the Jinja template
    (served when no dist is built, as in CI) renders the facts
    server-side instead and carries the actions, checked on disk."""
    from pathlib import Path

    from puspa.pwa.app import DIST_DIR

    user = make_user()
    user_id = user["user_id"]
    _seed_fact(user_id, test_dsn)
    client, _ = _client(user_id, test_dsn)
    with client:
        page = client.get("/memory")
        assert page.status_code == 200
        assert "Karawang" in page.text
        if (DIST_DIR / "index.html").exists():
            assert "__PUSPA_BOOTSTRAP__" in page.text
    template = Path("apps/pwa/templates/memory.html").read_text(encoding="utf-8")
    assert "Forget this" in template
    assert "Guest mode" in template
    assert "Export all my data" in template
    assert "Withdraw data consent" in template


def test_record_fact_persists_confidence_and_source_detail():
    store = facts_module.MemoryFactStore()
    recorded = facts_module.record_fact(
        "user-1",
        "hometown",
        "Karawang",
        source="inferred",
        store=store,
        confidence=0.7,
        source_detail="from the chat on 3 November 2026",
    )
    assert recorded["confidence"] == 0.7
    assert recorded["source_detail"] == "from the chat on 3 November 2026"
    assert recorded["status"] == "proposed"
    with pytest.raises(ValueError):
        facts_module.record_fact("user-1", "hometown", "Karawang", store=store, confidence=1.5)


def test_forget_fact_on_unknown_id_raises():
    store = facts_module.MemoryFactStore()
    with pytest.raises(KeyError):
        facts_module.forget_fact("user-1", str(uuid.uuid4()), store=store)
