"""Tests for the auto-curated knowledge wiki HTTP layer (COW-06,
``puspa/pwa/wiki_api.py``) against the real ``create_app``.

Covers login enforcement, the empty-wiki state, the build
trigger writing real pages from real consolidation signals,
page reads (Markdown + rendered HTML + sources), and the
read-only graph endpoint for a topic (nodes/edges from the same
KnowledgeGraph the second-brain view reads, no edit routes).
"""

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from puspa.memory.consolidate import consolidate_idle
from puspa.memory.episodic import record_episode
from puspa.memory.profile import add_fact
from puspa.pwa.app import create_app

EPISODE = "Samsung Galaxy S24 is a phone. Samsung Galaxy S24 sells Tokopedia."


def _client(user_id, test_dsn, tmp_path):
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    app.state.wiki_home = tmp_path
    return TestClient(app), app


def test_routes_exist_and_require_login(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/wiki/topics" in paths
    assert "/api/wiki/pages/{slug}" in paths
    assert "/api/wiki/build" in paths
    assert "/api/wiki/graph" in paths
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/wiki/topics").status_code == 401
        assert client.get("/api/wiki/pages/phone").status_code == 401
        assert client.post("/api/wiki/build").status_code == 401
        assert client.get("/api/wiki/graph?topic=phone").status_code == 401


def test_empty_wiki_lists_no_topics_and_unknown_page_404s(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    client, _ = _client(user_id, test_dsn, tmp_path)
    with client:
        assert client.get("/api/wiki/topics").json() == {"topics": []}
        assert client.get("/api/wiki/pages/phone").status_code == 404
        assert client.get("/api/wiki/graph?topic=phone").status_code == 404


def test_build_then_read_page_with_sources_and_html(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    add_fact(
        user_id,
        "preference:shopping",
        "User prefers Tokopedia for phone shopping",
        "user_stated",
        dsn=test_dsn,
    )
    client, _ = _client(user_id, test_dsn, tmp_path)
    with client:
        report = client.post("/api/wiki/build").json()
        assert report["entries"] == 1
        assert report["pages_written"] == 1
        assert report["topics"] == ["phone"]
        topics = client.get("/api/wiki/topics").json()["topics"]
        assert len(topics) == 1
        assert topics[0]["slug"] == "phone"
        assert topics[0]["title"] == "Phone"
        page = client.get("/api/wiki/pages/phone").json()
        assert page["slug"] == "phone"
        assert "# Phone" in page["markdown"]
        assert "User prefers Tokopedia for phone shopping" in page["markdown"]
        assert "<h1>Phone</h1>" in page["html"]
        assert page["sources"] == ["fact:preference:shopping"]
        # A rebuild is idempotent: same pages, same content.
        again = client.post("/api/wiki/build").json()
        assert again["pages_written"] == 1
        assert client.get("/api/wiki/pages/phone").json()["markdown"] == page["markdown"]


def test_graph_endpoint_is_read_only_and_shows_topic_nodes(make_user, test_dsn, tmp_path):
    user = make_user()
    user_id = user["user_id"]
    episode = record_episode(
        user_id,
        EPISODE,
        "user_message",
        task_id="11111111-1111-1111-1111-111111111111",
        event_id="33333333-3333-3333-3333-333333333333",
        dsn=test_dsn,
    )
    add_fact(
        user_id,
        "preference:shopping",
        "User prefers Tokopedia for phone shopping",
        "user_stated",
        dsn=test_dsn,
    )
    consolidate_idle(
        user_id,
        older_than_days=30,
        dsn=test_dsn,
        now=datetime.now(UTC) + timedelta(days=31),
    )
    client, _ = _client(user_id, test_dsn, tmp_path)
    with client:
        client.post("/api/wiki/build")
        graph = client.get("/api/wiki/graph?topic=phone").json()
        assert graph["topic"] == "phone"
        assert graph["title"] == "Phone"
        assert {n["name"] for n in graph["nodes"]} == {
            "samsung galaxy s24",
            "tokopedia",
        }
        assert graph["edges"] == [
            {
                "subject": "samsung galaxy s24",
                "relation": "sells",
                "object": "tokopedia",
                "sources": [f"{episode['id']}#p1"],
            }
        ]
        # The wiki exposes no edit route: the graph here is read-only.
        assert client.post("/api/wiki/pages/phone", json={}).status_code == 405


def test_wiki_screen_renders_without_dist(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    client, _ = _client(user_id, test_dsn, tmp_path)
    with client:
        resp = client.get("/wiki")
        assert resp.status_code == 200
        assert "Puspa" in resp.text
