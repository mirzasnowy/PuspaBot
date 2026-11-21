"""Store tests for the artifacts browser (COW-12).

Covers the durable record only (the HTTP layer lives in
``tests/test_pwa_artifacts_api.py``): one run's artifact is
recorded and listed with its name, kind, size, and time, and
downloaded back byte for byte; another user's agent is 403,
an unknown artifact is 404, and bad records (empty name,
unknown kind, oversize content) are 400. The store applies
its own migration, like the takeover store: the shared
fixtures only apply migration 0001.
"""

from __future__ import annotations

import pytest

from puspa.pwa.artifacts import (
    MAX_ARTIFACT_BYTES,
    ArtifactError,
    ArtifactStore,
)


@pytest.fixture
def store(test_dsn):
    store = ArtifactStore(test_dsn)
    store.create_schema()
    return store


def _record(store, user, *, name="laporan.md", kind="markdown", content=b"# hasil\n"):
    return store.record_artifact(
        user_id=user["user_id"],
        agent_id=user["agent_id"],
        run_id="tugas-1",
        name=name,
        kind=kind,
        media_type="text/markdown",
        content=content,
    )


def test_record_and_list_returns_name_kind_size_and_time(store, make_user):
    user = make_user()
    artifact = _record(store, user, content=b"# hasil\n")
    listed = store.list_artifacts(user["user_id"], user["agent_id"])
    assert len(listed) == 1
    assert listed[0]["artifact_id"] == artifact.artifact_id
    assert listed[0]["name"] == "laporan.md"
    assert listed[0]["kind"] == "markdown"
    assert listed[0]["size_bytes"] == 8
    assert listed[0]["run_id"] == "tugas-1"
    assert listed[0]["created_at"]
    assert "content" not in listed[0]


def test_list_newest_first(store, make_user):
    user = make_user()
    first = _record(store, user, name="a.md")
    second = _record(store, user, name="b.md")
    listed = store.list_artifacts(user["user_id"], user["agent_id"])
    assert [row["name"] for row in listed] == ["b.md", "a.md"]
    assert listed[0]["artifact_id"] == second.artifact_id
    assert listed[1]["artifact_id"] == first.artifact_id


def test_get_returns_the_same_bytes(store, make_user):
    user = make_user()
    artifact = _record(store, user, content=b"isi persis")
    found = store.get_artifact(user["user_id"], artifact.artifact_id)
    assert found.content == b"isi persis"
    assert found.name == "laporan.md"
    assert found.media_type == "text/markdown"


def test_list_for_other_users_agent_is_403(store, make_user):
    owner = make_user()
    stranger = make_user()
    _record(store, owner)
    with pytest.raises(ArtifactError) as caught:
        store.list_artifacts(stranger["user_id"], owner["agent_id"])
    assert caught.value.status == 403


def test_get_unknown_artifact_is_404(store, make_user):
    user = make_user()
    with pytest.raises(ArtifactError) as caught:
        store.get_artifact(user["user_id"], "00000000-0000-0000-0000-000000000000")
    assert caught.value.status == 404


def test_get_other_users_artifact_is_404_not_403(store, make_user):
    owner = make_user()
    stranger = make_user()
    artifact = _record(store, owner)
    with pytest.raises(ArtifactError) as caught:
        store.get_artifact(stranger["user_id"], artifact.artifact_id)
    assert caught.value.status == 404


def test_record_validates_name_kind_and_size(store, make_user):
    user = make_user()
    with pytest.raises(ArtifactError) as caught:
        _record(store, user, name="   ")
    assert caught.value.status == 400
    with pytest.raises(ArtifactError) as caught:
        _record(store, user, kind="video")
    assert caught.value.status == 400
    with pytest.raises(ArtifactError) as caught:
        _record(store, user, content=b"x" * (MAX_ARTIFACT_BYTES + 1))
    assert caught.value.status == 400


def test_create_schema_is_idempotent(store):
    store.create_schema()
    store.create_schema()
