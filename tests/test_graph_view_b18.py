"""Tests for the B18 second-brain graph view (``puspa/pwa/graph_api.py``).

Covers the service and the HTTP layer against the real
``puspa.pwa.app.create_app``: bounded reads (cap, pagination,
kind and recency filters), node inspect (facts + provenance),
all four edit flows end to end (rename, add/delete node,
add/change/delete edge, correct a fact), an edit changing a
later retrieval answer over the same graph, a ledger entry for
every edit, edits surviving a fresh app through ledger replay,
and code-graph nodes being read-only through this endpoint.

Seed data is real episodic memory: two episodes recorded with
``record_episode`` seed the graph through the deterministic
extractor, so the view renders from the real KnowledgeGraph,
never mock data.
"""

from fastapi.testclient import TestClient

from puspa.ledger import Ledger
from puspa.memory.episodic import record_episode
from puspa.pwa.app import create_app
from puspa.pwa.graph_api import GraphViewService, correction_task_id
from puspa.retrieval.code_graph import CodeGraph

EPISODE_A = "Samsung Galaxy S24 is a phone. Samsung Galaxy S24 sells Tokopedia."
EPISODE_B = "Xiaomi Redmi Note is a phone. Xiaomi Redmi Note sells Shopee."


def _seed_episodes(user_id, test_dsn, agent_id=None):
    first = record_episode(user_id, EPISODE_A, "user_message", agent_id=agent_id, dsn=test_dsn)
    second = record_episode(user_id, EPISODE_B, "user_message", agent_id=agent_id, dsn=test_dsn)
    return first, second


def _client(user_id, test_dsn):
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    return TestClient(app), app


def _corrections(test_dsn, user_id):
    events = Ledger(test_dsn).events(correction_task_id(user_id), event_type="evidence_recorded")
    return [e for e in events if (e["content"] or {}).get("kind") == "user_correction"]


def test_routes_exist_and_require_login(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/graph" in paths
    assert "/api/graph/nodes/{name:path}" in paths
    assert "/api/graph/edges" in paths
    assert "/api/graph/search" in paths
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/graph").status_code == 401
        assert client.post("/api/graph/nodes", json={"name": "x"}).status_code == 401


def test_bounded_read_cap_pagination_and_filters(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    _seed_episodes(user_id, test_dsn, agent_id=user["agent_id"])
    client, _ = _client(user_id, test_dsn)
    with client:
        page = client.get("/api/graph").json()
        assert page["total_nodes"] == 4
        assert {n["name"] for n in page["nodes"]} == {
            "samsung galaxy s24",
            "tokopedia",
            "xiaomi redmi note",
            "shopee",
        }
        assert page["total_edges"] == 2
        assert len(page["edges"]) == 2

        capped = client.get("/api/graph?limit=500").json()
        assert capped["limit"] == 100

        first = client.get("/api/graph?limit=1&offset=0").json()
        second = client.get("/api/graph?limit=1&offset=1").json()
        assert len(first["nodes"]) == 1 and len(second["nodes"]) == 1
        assert first["nodes"][0]["name"] != second["nodes"][0]["name"]

        phones = client.get("/api/graph?kind=phone").json()
        assert {n["name"] for n in phones["nodes"]} == {
            "samsung galaxy s24",
            "xiaomi redmi note",
        }
        assert phones["total_nodes"] == 2

        future = client.get("/api/graph?since=2999-01-01T00:00:00Z").json()
        assert future["total_nodes"] == 0
        past = client.get("/api/graph?since=2000-01-01T00:00:00Z").json()
        assert past["total_nodes"] == 4

        assert client.get("/api/graph?limit=0").status_code == 400
        assert client.get("/api/graph?limit=abc").status_code == 400
        assert client.get("/api/graph?since=not-a-date").status_code == 400


def test_inspect_node_shows_facts_and_provenance(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    _seed_episodes(user_id, test_dsn, agent_id=user["agent_id"])
    client, _ = _client(user_id, test_dsn)
    with client:
        node = client.get("/api/graph/nodes/samsung%20galaxy%20s24").json()
        assert node["name"] == "samsung galaxy s24"
        assert node["kind"] == "phone"
        assert node["is_code_node"] is False
        assert {(f["subject"], f["relation"], f["object"]) for f in node["facts"]} == {
            ("samsung galaxy s24", "sells", "tokopedia")
        }
        assert any("Samsung Galaxy S24 sells Tokopedia" in p["text"] for p in node["provenance"])
        assert any(p["passage_id"].endswith("#p1") for p in node["provenance"])
        assert client.get("/api/graph/nodes/no%20such%20node").status_code == 404


def test_rename_node_end_to_end_with_ledger_entry(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    _seed_episodes(user_id, test_dsn, agent_id=user["agent_id"])
    client, _ = _client(user_id, test_dsn)
    with client:
        renamed = client.patch(
            "/api/graph/nodes/samsung%20galaxy%20s24", json={"name": "Pixel 9 Pro"}
        ).json()
        assert renamed["name"] == "pixel 9 pro"
        assert renamed["kind"] == "phone"
        page = client.get("/api/graph").json()
        assert "samsung galaxy s24" not in {n["name"] for n in page["nodes"]}
        assert "pixel 9 pro" in {n["name"] for n in page["nodes"]}
        node = client.get("/api/graph/nodes/pixel%209%20pro").json()
        assert ("pixel 9 pro", "sells", "tokopedia") in {
            (f["subject"], f["relation"], f["object"]) for f in node["facts"]
        }
        assert client.get("/api/graph/nodes/samsung%20galaxy%20s24").status_code == 404
        assert client.patch("/api/graph/nodes/nope", json={"name": "other"}).status_code == 404
    entries = _corrections(test_dsn, user_id)
    assert len(entries) == 1
    assert entries[0]["content"]["operation"] == "rename_node"
    assert entries[0]["content"]["detail"] == {
        "old_name": "samsung galaxy s24",
        "new_name": "Pixel 9 Pro",
    }


def test_add_and_delete_node_end_to_end(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    _seed_episodes(user_id, test_dsn, agent_id=user["agent_id"])
    client, _ = _client(user_id, test_dsn)
    with client:
        added = client.post(
            "/api/graph/nodes",
            json={"name": "Oppo Reno", "kind": "phone", "description": "user-added"},
        ).json()
        assert added["name"] == "oppo reno"
        assert added["kind"] == "phone"
        assert "user_correction" in added["sources"]
        assert client.post("/api/graph/nodes", json={"name": "Oppo Reno"}).status_code == 400
        assert (
            client.post("/api/graph/nodes", json={"name": "Kernel", "kind": "function"}).status_code
            == 403
        )
        deleted = client.delete("/api/graph/nodes/oppo%20reno").json()
        assert deleted == {"deleted": "oppo reno"}
        assert client.get("/api/graph/nodes/oppo%20reno").status_code == 404
        assert client.delete("/api/graph/nodes/oppo%20reno").status_code == 404
    entries = _corrections(test_dsn, user_id)
    assert [e["content"]["operation"] for e in entries] == ["add_node", "delete_node"]


def test_add_change_delete_edge_and_correct_fact(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    _seed_episodes(user_id, test_dsn, agent_id=user["agent_id"])
    client, _ = _client(user_id, test_dsn)
    with client:
        edge = client.post(
            "/api/graph/edges",
            json={
                "subject": "xiaomi redmi note",
                "relation": "sells",
                "object": "tokopedia",
            },
        ).json()
        assert edge == {
            "subject": "xiaomi redmi note",
            "relation": "sells",
            "object": "tokopedia",
            "sources": ["user_correction"],
        }
        assert (
            client.post(
                "/api/graph/edges",
                json={
                    "subject": "xiaomi redmi note",
                    "relation": "sells",
                    "object": "tokopedia",
                },
            ).status_code
            == 400
        )
        corrected = client.patch(
            "/api/graph/edges",
            json={
                "subject": "samsung galaxy s24",
                "relation": "sells",
                "object": "tokopedia",
                "new_object": "Shopee",
                "operation": "correct_fact",
            },
        ).json()
        assert corrected["object"] == "shopee"
        assert corrected["subject"] == "samsung galaxy s24"
        assert corrected["relation"] == "sells"
        node = client.get("/api/graph/nodes/samsung%20galaxy%20s24").json()
        assert ("samsung galaxy s24", "sells", "shopee") in {
            (f["subject"], f["relation"], f["object"]) for f in node["facts"]
        }
        assert ("samsung galaxy s24", "sells", "tokopedia") not in {
            (f["subject"], f["relation"], f["object"]) for f in node["facts"]
        }
        removed = client.delete(
            "/api/graph/edges?subject=xiaomi%20redmi%20note&relation=sells&object=tokopedia"
        ).json()
        assert removed["deleted"] == {
            "subject": "xiaomi redmi note",
            "relation": "sells",
            "object": "tokopedia",
        }
        assert (
            client.delete(
                "/api/graph/edges?subject=xiaomi%20redmi%20note&relation=sells&object=tokopedia"
            ).status_code
            == 404
        )
    entries = _corrections(test_dsn, user_id)
    assert [e["content"]["operation"] for e in entries] == [
        "add_edge",
        "correct_fact",
        "delete_edge",
    ]


def test_edit_changes_later_retrieval_answer(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    _seed_episodes(user_id, test_dsn, agent_id=user["agent_id"])
    service = GraphViewService(dsn=test_dsn)
    before = service.retrieve(user_id, "Pixel 9 Pro", agent_id=user["agent_id"])
    assert before, "retrieval over the seeded graph returns passages"
    assert all(result["seed_nodes"] == [] for result in before)
    assert all(result["graph_score"] == 0.0 for result in before)
    service.rename_node(user_id, "samsung galaxy s24", "Pixel 9 Pro", agent_id=user["agent_id"])
    after = service.retrieve(user_id, "Pixel 9 Pro", agent_id=user["agent_id"])
    assert after, "retrieval after the rename still returns passages"
    top = after[0]
    assert "Samsung Galaxy S24 sells Tokopedia" in top["text"]
    assert "pixel 9 pro" in top["seed_nodes"]
    assert top["graph_score"] > 0.0


def test_edits_survive_fresh_app_through_ledger_replay(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    _seed_episodes(user_id, test_dsn, agent_id=user["agent_id"])
    client, _ = _client(user_id, test_dsn)
    with client:
        assert (
            client.patch(
                "/api/graph/nodes/samsung%20galaxy%20s24", json={"name": "Pixel 9 Pro"}
            ).status_code
            == 200
        )
        assert (
            client.post("/api/graph/nodes", json={"name": "Oppo Reno", "kind": "phone"}).status_code
            == 200
        )
    fresh_client, _ = _client(user_id, test_dsn)
    with fresh_client:
        page = fresh_client.get("/api/graph").json()
        names = {n["name"] for n in page["nodes"]}
        assert "pixel 9 pro" in names
        assert "samsung galaxy s24" not in names
        assert "oppo reno" in names
        node = fresh_client.get("/api/graph/nodes/pixel%209%20pro").json()
        assert ("pixel 9 pro", "sells", "tokopedia") in {
            (f["subject"], f["relation"], f["object"]) for f in node["facts"]
        }


def test_code_graph_nodes_are_not_editable(make_user, test_dsn):
    user = make_user()
    user_id = user["user_id"]
    _seed_episodes(user_id, test_dsn, agent_id=user["agent_id"])
    client, app = _client(user_id, test_dsn)
    service = app.state.graph_views
    graph = service.graph_for(user_id, agent_id=user["agent_id"])
    CodeGraph(graph).add_file("puspa/billing.py", "def charge(amount):\n    return amount\n")
    with client:
        page = client.get("/api/graph").json()
        assert "puspa/billing.py" in {n["name"] for n in page["nodes"]}
        file_node = client.get("/api/graph/nodes/puspa%2Fbilling.py").json()
        assert file_node["is_code_node"] is True
        assert (
            client.patch(
                "/api/graph/nodes/puspa%2Fbilling.py", json={"name": "other.py"}
            ).status_code
            == 403
        )
        assert client.delete("/api/graph/nodes/puspa%2Fbilling.py").status_code == 403
        assert (
            client.post(
                "/api/graph/edges",
                json={
                    "subject": "puspa/billing.py",
                    "relation": "calls",
                    "object": "tokopedia",
                },
            ).status_code
            == 403
        )
        assert (
            client.patch(
                "/api/graph/edges",
                json={
                    "subject": "puspa/billing.py",
                    "relation": "defines",
                    "object": "puspa.billing.charge",
                    "new_object": "tokopedia",
                },
            ).status_code
            == 403
        )
    assert _corrections(test_dsn, user_id) == []


def test_obsidian_export_is_markdown_with_wikilinks(make_user, test_dsn):
    user_id = make_user()["user_id"]
    _seed_episodes(user_id, test_dsn)
    client, _ = _client(user_id, test_dsn)
    with client:
        resp = client.get("/api/graph/export/obsidian")
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert "puspa-graph.md" in resp.headers["content-disposition"]
    text = resp.text
    assert text.startswith("# Puspa knowledge graph")
    # One section per node, with the other end as a wikilink.
    assert "## [[samsung galaxy s24]]" in text
    assert "## [[tokopedia]]" in text
    assert "- samsung galaxy s24 — sells — [[tokopedia]]" in text
    assert "- [[samsung galaxy s24]] — sells — tokopedia" in text
    assert "## [[xiaomi redmi note]]" in text
    assert "- xiaomi redmi note — sells — [[shopee]]" in text


def test_obsidian_export_empty_graph(make_user, test_dsn):
    user_id = make_user()["user_id"]
    client, _ = _client(user_id, test_dsn)
    with client:
        resp = client.get("/api/graph/export/obsidian")
    assert resp.status_code == 200
    assert "Graf masih kosong." in resp.text


def test_obsidian_export_requires_login(make_user, test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/graph/export/obsidian").status_code == 401
