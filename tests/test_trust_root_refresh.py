"""Tests for scheduled Sigstore trust-root refresh (``puspa.policy.trust_root``).

All network is faked: refreshes go through an injectable
``updater_factory`` whose fake updater hands back chosen bytes, and
the clock is an injectable ``now`` (file mtimes are set with
``os.utime``). No test here touches the network or the database, so
this file runs with ``--noconftest``.

Covered: refresh writes a new cached root; a fresh cache skips the
network entirely; a due cache refreshes; an identical download
reports ``unchanged`` and leaves the file alone; stale and missing
roots are detected and recorded through the sink; a bad downloaded
root (not JSON, wrong media type, oversized) is rejected and never
overwrites the good cache; a missing sigstore TUF client reports
``unavailable``; the scheduler registers the daily
``trust_root_refresh`` trigger and dispatches it to the job; and the
verify path, pointed at the cached root, verifies a real staging
bundle with sockets disabled — verification itself never touches the
network.

The staging bundle fixture is reused from ``test_sigstore_verify``
(the Sigstore project's public test assets) instead of duplicating
its ~100 lines of base64 here.
"""

import os
import socket
from datetime import datetime, timedelta, timezone
from importlib.resources import as_file, files
from pathlib import Path

import pytest

from puspa.policy import trust_root
from puspa.policy.sigstore_verify import verify_sigstore_bundle
from puspa.policy.trust_root import (
    TrustRootError,
    cached_root_age_days,
    check_staleness,
    default_cache_path,
    refresh_trust_root,
    resolve_trust_root_for_verify,
    run_trust_root_refresh_job,
)
from test_sigstore_verify import IDENTITY, ISSUER, _artifact, _bundle_bytes

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)

_BUNDLED_ROOTS = {
    "production": "https%3A%2F%2Ftuf-repo-cdn.sigstore.dev/trusted_root.json",
    "staging": "https%3A%2F%2Ftuf-repo-cdn.sigstage.dev/trusted_root.json",
}


def _bundled_root_bytes(environment="production") -> bytes:
    resource = files("sigstore._store").joinpath(_BUNDLED_ROOTS[environment])
    with as_file(resource) as path:
        return Path(path).read_bytes()


def _set_mtime(path: Path, age_days: float) -> None:
    moment = NOW - timedelta(days=age_days)
    os.utime(path, (moment.timestamp(), moment.timestamp()))


def _write_cache(path: Path, data: bytes, age_days: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _set_mtime(path, age_days)


class _FakeUpdater:
    """Stand-in for sigstore's TrustUpdater: returns chosen bytes."""

    def __init__(self, download_dir: Path, data: bytes):
        self._path = download_dir / "downloaded-trusted_root.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_bytes(data)

    def get_trusted_root_path(self) -> str:
        return str(self._path)


class _CountingFactory:
    """Fake updater factory that records whether it was ever called."""

    def __init__(self, download_dir: Path, data: bytes):
        self.download_dir = download_dir
        self.data = data
        self.calls: list[str] = []

    def __call__(self, tuf_url: str):
        self.calls.append(tuf_url)
        return _FakeUpdater(self.download_dir, self.data)


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "trust_root" / "trusted_root.json"


@pytest.fixture
def sink():
    events: list[dict] = []

    def _sink(event_type, content):
        events.append({"type": event_type, "content": content})

    _sink.events = events
    return _sink


@pytest.fixture
def download_dir(tmp_path):
    return tmp_path / "tuf-download"


def test_refresh_writes_new_cached_root(cache_path, download_dir, sink):
    data = _bundled_root_bytes("production")
    outcome = refresh_trust_root(
        cache_path, sink=sink, now=NOW,
        updater_factory=_CountingFactory(download_dir, data))
    assert outcome["status"] == "refreshed"
    assert outcome["path"] == str(cache_path)
    assert cache_path.read_bytes() == data
    assert outcome["age_days"] == pytest.approx(0.0, abs=1e-6)
    assert [e["type"] for e in sink.events] == ["trust_root_refreshed"]


def test_fresh_cache_skips_network(cache_path, download_dir, sink):
    _write_cache(cache_path, _bundled_root_bytes("production"), age_days=1)
    factory = _CountingFactory(download_dir, _bundled_root_bytes("production"))
    outcome = refresh_trust_root(
        cache_path, sink=sink, now=NOW, updater_factory=factory)
    assert outcome["status"] == "fresh"
    assert factory.calls == []
    assert sink.events == []


def test_due_cache_refreshes(cache_path, download_dir, sink):
    old = _bundled_root_bytes("staging")
    _write_cache(cache_path, old, age_days=8)
    new = _bundled_root_bytes("production")
    outcome = refresh_trust_root(
        cache_path, sink=sink, now=NOW,
        updater_factory=_CountingFactory(download_dir, new))
    assert outcome["status"] == "refreshed"
    assert cache_path.read_bytes() == new


def test_force_refreshes_fresh_cache(cache_path, download_dir):
    _write_cache(cache_path, _bundled_root_bytes("production"), age_days=1)
    factory = _CountingFactory(
        download_dir, _bundled_root_bytes("production"))
    outcome = refresh_trust_root(
        cache_path, force=True, now=NOW, updater_factory=factory)
    # Same bytes as the cache: nothing to install, but the network
    # path was taken because force was asked for.
    assert outcome["status"] == "unchanged"
    assert len(factory.calls) == 1


def test_identical_download_leaves_cache_untouched(cache_path, download_dir):
    data = _bundled_root_bytes("production")
    _write_cache(cache_path, data, age_days=8)
    before = cache_path.stat().st_mtime_ns
    outcome = refresh_trust_root(
        cache_path, now=NOW,
        updater_factory=_CountingFactory(download_dir, data))
    assert outcome["status"] == "unchanged"
    assert cache_path.stat().st_mtime_ns == before
    assert cache_path.read_bytes() == data


def test_stale_root_detected_and_recorded(cache_path, sink):
    _write_cache(cache_path, _bundled_root_bytes("production"), age_days=31)
    result = check_staleness(cache_path, sink=sink, now=NOW)
    assert result["stale"] is True
    assert result["age_days"] == pytest.approx(31.0, abs=1e-6)
    assert "31.0 days old" in result["reason"]
    assert [e["type"] for e in sink.events] == ["trust_root_stale"]
    assert sink.events[0]["content"]["age_days"] == pytest.approx(31.0, abs=1e-6)


def test_missing_root_is_stale(cache_path, sink):
    result = check_staleness(cache_path, sink=sink, now=NOW)
    assert result == {"stale": True, "age_days": None,
                      "path": str(cache_path),
                      "reason": "no cached trust root"}
    assert [e["type"] for e in sink.events] == ["trust_root_stale"]


def test_fresh_root_is_not_stale(cache_path, sink):
    _write_cache(cache_path, _bundled_root_bytes("production"), age_days=5)
    result = check_staleness(cache_path, sink=sink, now=NOW)
    assert result["stale"] is False
    assert sink.events == []


def test_bad_download_never_overwrites_good_cache(cache_path, download_dir, sink):
    good = _bundled_root_bytes("production")
    _write_cache(cache_path, good, age_days=40)
    before_mtime = cache_path.stat().st_mtime_ns
    outcome = refresh_trust_root(
        cache_path, sink=sink, now=NOW,
        updater_factory=_CountingFactory(download_dir, b"not json at all"))
    assert outcome["status"] == "failed"
    assert "rejected" in outcome["reason"]
    assert cache_path.read_bytes() == good
    assert cache_path.stat().st_mtime_ns == before_mtime
    assert [e["type"] for e in sink.events] == ["trust_root_refresh_failed"]


@pytest.mark.parametrize("bad", [
    b'{"mediaType": "text/plain"}',
    b"",
    b"{" ,
])
def test_malformed_downloads_rejected(cache_path, download_dir, bad):
    outcome = refresh_trust_root(
        cache_path, now=NOW,
        updater_factory=_CountingFactory(download_dir, bad))
    assert outcome["status"] == "failed"
    assert not cache_path.exists()


def test_oversized_download_rejected(cache_path, download_dir):
    big = b"x" * (trust_root.MAX_TRUSTED_ROOT_BYTES + 1)
    outcome = refresh_trust_root(
        cache_path, now=NOW,
        updater_factory=_CountingFactory(download_dir, big))
    assert outcome["status"] == "failed"
    assert "over the" in outcome["reason"]
    assert not cache_path.exists()


def test_download_failure_keeps_old_cache(cache_path, download_dir, sink):
    good = _bundled_root_bytes("production")
    _write_cache(cache_path, good, age_days=40)

    def _failing_factory(tuf_url):
        raise ConnectionError("tuf-repo-cdn.sigstore.dev unreachable")

    outcome = refresh_trust_root(
        cache_path, sink=sink, now=NOW, updater_factory=_failing_factory)
    assert outcome["status"] == "failed"
    assert cache_path.read_bytes() == good
    assert [e["type"] for e in sink.events] == ["trust_root_refresh_failed"]


def test_missing_tuf_client_reports_unavailable(cache_path, sink):
    def _no_client(tuf_url):
        raise TrustRootError("sigstore TUF client is not available: no tuf")

    outcome = refresh_trust_root(
        cache_path, sink=sink, now=NOW, updater_factory=_no_client)
    assert outcome["status"] == "unavailable"
    assert not cache_path.exists()
    assert [e["type"] for e in sink.events] == ["trust_root_refresh_failed"]


def test_resolve_returns_cached_path_and_records_staleness(cache_path, sink):
    _write_cache(cache_path, _bundled_root_bytes("production"), age_days=40)
    path, status = resolve_trust_root_for_verify(
        cache_path, sink=sink, now=NOW)
    # A stale cached root is still the freshest TUF-verified copy, so
    # it is returned — but only after the staleness is recorded.
    assert path == str(cache_path)
    assert status["stale"] is True
    assert [e["type"] for e in sink.events] == ["trust_root_stale"]


def test_resolve_without_cache_falls_back_to_bundled(cache_path, sink):
    path, status = resolve_trust_root_for_verify(
        cache_path, sink=sink, now=NOW)
    assert path is None
    assert status["stale"] is True
    assert status["reason"] == "no cached trust root"


def test_refresh_job_records_staleness_and_refresh(
        cache_path, download_dir, sink):
    outcome = run_trust_root_refresh_job(
        sink=sink, cache_path=cache_path, now=NOW,
        updater_factory=_CountingFactory(
            download_dir, _bundled_root_bytes("production")))
    assert outcome["status"] == "refreshed"
    assert outcome["staleness"]["stale"] is True  # no cache existed yet
    assert outcome["staleness"]["reason"] == "no cached trust root"
    assert [e["type"] for e in sink.events] == [
        "trust_root_stale", "trust_root_refreshed"]
    assert cache_path.read_bytes() == _bundled_root_bytes("production")


def test_refresh_job_never_raises(cache_path, download_dir, sink):
    def _boom(tuf_url):
        raise RuntimeError("boom")

    outcome = run_trust_root_refresh_job(
        sink=sink, cache_path=cache_path, now=NOW, updater_factory=_boom)
    assert outcome["status"] == "failed"
    assert outcome["staleness"]["stale"] is True


def test_default_cache_path_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom.json"
    monkeypatch.setenv("PUSPA_TRUST_ROOT_CACHE", str(custom))
    assert default_cache_path() == custom


def test_default_cache_path_without_env(monkeypatch):
    monkeypatch.delenv("PUSPA_TRUST_ROOT_CACHE", raising=False)
    path = default_cache_path()
    assert path.name == "trusted_root.json"
    assert path.parent.name == "trust_root"


def test_cached_root_age_days_missing(cache_path):
    assert cached_root_age_days(cache_path, now=NOW) is None


def test_verify_with_cached_root_uses_no_network(cache_path, monkeypatch):
    # Point the verifier at the cached staging root and disable
    # sockets entirely: verification must still succeed, proving the
    # verify path never touches the network.
    _write_cache(cache_path, _bundled_root_bytes("staging"), age_days=40)
    path, status = resolve_trust_root_for_verify(cache_path, now=NOW)
    assert status["stale"] is True  # recorded; the root is still used

    def _no_socket(*args, **kwargs):
        raise OSError("network disabled in test")

    monkeypatch.setattr(socket, "socket", _no_socket)
    ok, reason = verify_sigstore_bundle(
        _bundle_bytes(), _artifact(), IDENTITY, ISSUER, trust_root=path)
    assert (ok, reason) == (True, "ok")


def test_scheduler_registers_daily_refresh_trigger(monkeypatch):
    from puspa.scheduler import scheduler

    seen = {}

    def _fake_create_trigger(user_id, event_type, next_trigger_at,
                            schedule="", workflow="", dsn=None):
        seen.update({"user_id": user_id, "event_type": event_type,
                     "next_trigger_at": next_trigger_at,
                     "schedule": schedule})
        return scheduler.Trigger(
            id="pemicu-test", user_id=user_id, event_type=event_type,
            schedule=schedule, next_trigger_at=next_trigger_at)

    monkeypatch.setattr(scheduler, "create_trigger", _fake_create_trigger)
    trigger = scheduler.schedule_trust_root_refresh("user-1")
    assert trigger.event_type == "trust_root_refresh"
    assert seen["event_type"] == "trust_root_refresh"
    assert seen["schedule"] == "daily 03:00"
    assert seen["next_trigger_at"] > NOW
    assert trigger.event_type in scheduler.TRIGGER_TYPES


def test_scheduler_dispatches_refresh_job(monkeypatch):
    from puspa.scheduler import scheduler

    class _FakeCursor:
        def __init__(self, row):
            self._row = row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, *args, **kwargs):
            return None

        def fetchone(self):
            return self._row

    class _FakeConn:
        def __init__(self, row):
            self._row = row

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self, *args, **kwargs):
            return _FakeCursor(self._row)

    monkeypatch.setattr(
        scheduler, "_connect",
        lambda dsn=None: _FakeConn(("trust_root_refresh", "user-1")))
    called = {}

    def _fake_job(**kwargs):
        called.update(kwargs)
        return {"status": "fresh", "path": "/cache/trusted_root.json",
                "age_days": 1.0, "reason": "fresh",
                "staleness": {"stale": False}}

    monkeypatch.setattr(trust_root, "run_trust_root_refresh_job", _fake_job)
    result = scheduler.run_scheduled_job(
        {"trigger_id": "pemicu-test", "task_id": "tugas-test"})
    assert result["job"] == "trust_root_refresh"
    assert result["result"]["status"] == "fresh"
