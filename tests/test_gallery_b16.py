"""Store + API tests for the B16 community gallery (``puspa/gallery`` + ``puspa/pwa/gallery_api.py``).

Covers the HTTP layer against the real ``puspa.pwa.app.create_app``:
publish flow from a B7 mockup, the restricted-customary-use refusal,
the aggregate always shown together with its rating count, one
rating per user per work (a second rating updates the first), the
rating-spam rate limits (hourly cap + per-work cooldown), take-down
removing the work from the listing, the SVG render of the published
snapshot, cross-user isolation, and the 401 gate for anonymous
callers. The gallery schema is applied by the autouse fixture below
(conftest does NOT apply it).
"""

import pytest
from fastapi.testclient import TestClient

from puspa.gallery.store import GalleryError, GalleryStore, RateLimitedError, create_schema
from puspa.gallery.store import reset_tables as reset_gallery_tables
from puspa.mockup.store import MockupStore
from puspa.mockup.store import create_schema as create_mockup_schema
from puspa.mockup.store import reset_tables as reset_mockup_tables
from puspa.mockup.templates import fill_template
from puspa.pwa.app import create_app


@pytest.fixture(autouse=True)
def _gallery_schema(test_dsn):
    create_mockup_schema(test_dsn)
    create_schema(test_dsn)
    reset_mockup_tables(test_dsn)
    reset_gallery_tables(test_dsn)
    yield
    reset_gallery_tables(test_dsn)
    reset_mockup_tables(test_dsn)


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


def _mockup_doc(user_id, test_dsn, title="Promo Warung Kopi"):
    store = MockupStore(dsn=test_dsn)
    doc = fill_template("promo_banner", {"shop": "Warung Sari"})
    doc["title"] = title
    return store.create(user_id, doc)


def _publish(client, mockup_id, **extra):
    response = client.post("/api/gallery/publish", json={"mockup_id": mockup_id, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def test_routes_exist_and_require_login(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/gallery/publish" in paths
    assert "/api/gallery" in paths
    assert "/api/gallery/{work_id}" in paths
    assert "/api/gallery/{work_id}/render.svg" in paths
    assert "/api/gallery/{work_id}/rate" in paths
    assert "/api/gallery/{work_id}/unpublish" in paths
    anon = TestClient(create_app(test_user_id=None, dsn=test_dsn))
    with anon:
        assert anon.get("/api/gallery").status_code == 401
        assert anon.post("/api/gallery/publish", json={"mockup_id": "doc-x"}).status_code == 401
        assert anon.get("/api/gallery/gal-x").status_code == 401
        assert anon.post("/api/gallery/gal-x/rate", json={"rating": 5}).status_code == 401
        assert anon.post("/api/gallery/gal-x/unpublish").status_code == 401


def test_publish_flow_from_b7_mockup(make_user, test_dsn):
    owner = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    client = _client(owner, test_dsn)
    with client:
        work = _publish(client, doc["id"])
        assert work["owner_id"] == owner
        assert work["mockup_id"] == doc["id"]
        assert work["title"] == "Promo Warung Kopi"
        assert work["template"] == "promo_banner"
        assert work["version"] == doc["version"]
        assert work["status"] == "published"
        # Aggregate is always a pair: no score without its count.
        assert work["average_rating"] is None
        assert work["rating_count"] == 0
        listing = client.get("/api/gallery").json()["works"]
        assert [w["id"] for w in listing] == [work["id"]]
        detail = client.get(f"/api/gallery/{work['id']}").json()
        assert detail["reviews"] == []
        # The published snapshot renders as SVG.
        svg = client.get(f"/api/gallery/{work['id']}/render.svg")
        assert svg.status_code == 200
        assert svg.headers["content-type"].startswith("image/svg+xml")
        assert "<svg" in svg.text


def test_publish_twice_is_refused(make_user, test_dsn):
    owner = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    client = _client(owner, test_dsn)
    with client:
        _publish(client, doc["id"])
        again = client.post("/api/gallery/publish", json={"mockup_id": doc["id"]})
        assert again.status_code == 400
        assert "already published" in again.json()["error"]


def test_publish_someone_elses_mockup_is_404(make_user, test_dsn):
    owner = make_user()["user_id"]
    other = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    client = _client(other, test_dsn)
    with client:
        response = client.post("/api/gallery/publish", json={"mockup_id": doc["id"]})
        assert response.status_code == 404


def test_restricted_customary_use_cannot_be_published(make_user, test_dsn):
    owner = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    client = _client(owner, test_dsn)
    with client:
        response = client.post(
            "/api/gallery/publish",
            json={"mockup_id": doc["id"], "restricted_customary_use": True},
        )
        assert response.status_code == 400
        assert "restricted customary use" in response.json()["error"]
        assert client.get("/api/gallery").json()["works"] == []
    # The store refuses it too, and nothing is stored.
    store = GalleryStore(dsn=test_dsn)
    with pytest.raises(GalleryError, match="restricted customary use"):
        store.publish(owner, doc, restricted_customary_use=True)
    assert store.list_published() == []


def test_rate_and_review_updates_aggregate(make_user, test_dsn):
    owner = make_user()["user_id"]
    rater = make_user()["user_id"]
    second = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    owner_client = _client(owner, test_dsn)
    with owner_client:
        work = _publish(owner_client, doc["id"])
    rater_client = _client(rater, test_dsn)
    with rater_client:
        saved = rater_client.post(
            f"/api/gallery/{work['id']}/rate",
            json={"rating": 4, "review": "Bagus, warnanya hidup."},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["rating"] == 4
        assert saved.json()["work_version"] == doc["version"]
        assert saved.json()["average_rating"] == 4.0
        assert saved.json()["rating_count"] == 1
    second_client = _client(second, test_dsn)
    with second_client:
        saved = second_client.post(
            f"/api/gallery/{work['id']}/rate", json={"rating": 5, "review": "Suka sekali."}
        )
        assert saved.json()["average_rating"] == 4.5
        assert saved.json()["rating_count"] == 2
    # The aggregate is never shown without its count.
    with rater_client:
        listing = rater_client.get("/api/gallery").json()["works"]
        assert listing[0]["average_rating"] == 4.5
        assert listing[0]["rating_count"] == 2
        detail = rater_client.get(f"/api/gallery/{work['id']}").json()
        assert detail["average_rating"] == 4.5
        assert detail["rating_count"] == 2
        assert {r["user_id"] for r in detail["reviews"]} == {rater, second}
        assert {r["rating"] for r in detail["reviews"]} == {4, 5}


def test_second_rating_updates_instead_of_adding(make_user, test_dsn):
    owner = make_user()["user_id"]
    rater = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    store = GalleryStore(dsn=test_dsn)
    work = store.publish(owner, doc)
    first = store.rate(rater, work["id"], 2, "Kurang.", per_work_cooldown_seconds=0)
    assert first["average_rating"] == 2.0
    assert first["rating_count"] == 1
    second = store.rate(rater, work["id"], 5, "Setelah revisi, bagus.", per_work_cooldown_seconds=0)
    assert second["rating"] == 5
    assert second["review"] == "Setelah revisi, bagus."
    assert second["rating_count"] == 1
    assert second["average_rating"] == 5.0
    assert len(store.get(work["id"], rater)["reviews"]) == 1


def test_rate_validation_and_self_rating(make_user, test_dsn):
    owner = make_user()["user_id"]
    rater = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    store = GalleryStore(dsn=test_dsn)
    work = store.publish(owner, doc)
    for bad in (0, 6, "5", 4.5, True):
        with pytest.raises(GalleryError, match="1 to 5"):
            store.rate(rater, work["id"], bad)
    with pytest.raises(GalleryError, match="at most 500"):
        store.rate(rater, work["id"], 5, "x" * 501)
    with pytest.raises(GalleryError, match="your own work"):
        store.rate(owner, work["id"], 5, "Karyaku sendiri.")
    with pytest.raises(KeyError):
        store.rate(rater, "gal-nope", 5)
    # Same guards through the API.
    client = _client(rater, test_dsn)
    with client:
        assert client.post(f"/api/gallery/{work['id']}/rate", json={"rating": 0}).status_code == 400
        assert client.post("/api/gallery/gal-nope/rate", json={"rating": 5}).status_code == 404
    owner_client = _client(owner, test_dsn)
    with owner_client:
        assert (
            owner_client.post(f"/api/gallery/{work['id']}/rate", json={"rating": 5}).status_code
            == 400
        )


def test_rating_spam_rate_limits(make_user, test_dsn):
    owner = make_user()["user_id"]
    rater = make_user()["user_id"]
    store = GalleryStore(dsn=test_dsn)
    works = [store.publish(owner, _mockup_doc(owner, test_dsn, f"Promo {i}")) for i in range(3)]
    # Hourly cap: 2 ratings per hour, the third is refused.
    store.rate(rater, works[0]["id"], 5, per_work_cooldown_seconds=0, hourly_limit=2)
    store.rate(rater, works[1]["id"], 4, per_work_cooldown_seconds=0, hourly_limit=2)
    with pytest.raises(RateLimitedError, match="last hour"):
        store.rate(rater, works[2]["id"], 5, per_work_cooldown_seconds=0, hourly_limit=2)
    # Per-work cooldown: rating the same work again too soon is refused.
    other = make_user()["user_id"]
    store.rate(other, works[0]["id"], 5, per_work_cooldown_seconds=3600, hourly_limit=100)
    with pytest.raises(RateLimitedError, match="just rated"):
        store.rate(other, works[0]["id"], 4, per_work_cooldown_seconds=3600, hourly_limit=100)


def test_rate_cooldown_through_api(make_user, test_dsn):
    owner = make_user()["user_id"]
    rater = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    owner_client = _client(owner, test_dsn)
    with owner_client:
        work = _publish(owner_client, doc["id"])
    client = _client(rater, test_dsn)
    with client:
        assert client.post(f"/api/gallery/{work['id']}/rate", json={"rating": 5}).status_code == 200
        # The default 60-second per-work cooldown trips immediately.
        again = client.post(f"/api/gallery/{work['id']}/rate", json={"rating": 4})
        assert again.status_code == 429
        assert again.json()["retry_after_seconds"] == 60


def test_unpublish_removes_from_gallery(make_user, test_dsn):
    owner = make_user()["user_id"]
    rater = make_user()["user_id"]
    doc = _mockup_doc(owner, test_dsn)
    owner_client = _client(owner, test_dsn)
    with owner_client:
        work = _publish(owner_client, doc["id"])
    rater_client = _client(rater, test_dsn)
    with rater_client:
        assert (
            rater_client.post(f"/api/gallery/{work['id']}/rate", json={"rating": 5}).status_code
            == 200
        )
        assert len(rater_client.get("/api/gallery").json()["works"]) == 1
        # Only the owner can take it down.
        assert rater_client.post(f"/api/gallery/{work['id']}/unpublish").status_code == 404
        assert len(rater_client.get("/api/gallery").json()["works"]) == 1
    with owner_client:
        assert owner_client.post(f"/api/gallery/{work['id']}/unpublish").status_code == 200
        assert owner_client.get("/api/gallery").json()["works"] == []
        # Taken down: strangers see a 404, the owner still sees it.
        assert rater_client.get(f"/api/gallery/{work['id']}").status_code == 404
        assert owner_client.get(f"/api/gallery/{work['id']}").status_code == 200
        assert owner_client.get(f"/api/gallery/{work['id']}/render.svg").status_code == 404
        # Re-publishing the same mockup restores it with its rating history.
        republished = _publish(owner_client, doc["id"])
        assert republished["id"] == work["id"]
        assert republished["rating_count"] == 1
        assert republished["average_rating"] == 5.0


def test_gallery_page_renders_from_jinja_without_dist(make_user, test_dsn, tmp_path, monkeypatch):
    """The CI lesson: the Jinja template is what gets served when
    ``dist/`` was never built — hide it and the page must still
    render with the gallery list and the rating form."""
    import puspa.pwa.app as app_module

    user = make_user()
    app = create_app(test_user_id=user["user_id"], dsn=test_dsn)
    monkeypatch.setattr(app_module, "DIST_DIR", tmp_path / "no-dist-here")
    with TestClient(app) as client:
        page = client.get("/gallery")
        assert page.status_code == 200
        assert "Galeri" in page.text
        assert "/api/gallery" in page.text
        assert "Kirim nilai" in page.text
