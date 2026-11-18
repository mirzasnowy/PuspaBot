"""G31: PWA serve modes (local operator vs hosted).

Run standalone (no database): ``pytest tests/test_pwa_serve_modes.py
--noconftest``. The endpoint tests use ``create_app(test_user_id=...)``
so no session lookup touches Postgres.
"""

import json

import pytest
from fastapi.testclient import TestClient

from puspa.hosting.pairing import PairingStore
from puspa.policy.gateway import DEFAULT_POLICY_DATA, ActionContext, Gateway
from puspa.pwa.app import create_app
from puspa.pwa.serve_modes import (
    GatewayDecisionLog,
    ServeMode,
    ServeModeError,
    describe_serve_mode,
    gateway_status,
    operator_panels_visible,
    operator_status,
    pairing_status,
    resolve_serve_mode,
)


@pytest.fixture(autouse=True)
def _clean_serve_mode_env(monkeypatch):
    monkeypatch.delenv("PUSPA_SERVE_MODE", raising=False)
    monkeypatch.delenv("PUSPA_SERVE_MODE_FILE", raising=False)


# ---------------------------------------------------------------------------
# Mode resolution: env, file, default, invalid rejected
# ---------------------------------------------------------------------------


def test_default_is_local_when_nothing_configured():
    assert resolve_serve_mode(environ={}) == ServeMode.LOCAL
    assert resolve_serve_mode() == ServeMode.LOCAL


def test_env_local_and_hosted():
    assert resolve_serve_mode(environ={"PUSPA_SERVE_MODE": "local"}) == ServeMode.LOCAL
    assert resolve_serve_mode(environ={"PUSPA_SERVE_MODE": "hosted"}) == ServeMode.HOSTED


def test_env_is_case_insensitive_and_trims_whitespace():
    assert resolve_serve_mode(environ={"PUSPA_SERVE_MODE": "  Hosted "}) == ServeMode.HOSTED


def test_env_empty_counts_as_unset():
    assert resolve_serve_mode(environ={"PUSPA_SERVE_MODE": "   "}) == ServeMode.LOCAL


def test_env_invalid_is_rejected_fail_closed():
    with pytest.raises(ServeModeError, match="unknown serve mode"):
        resolve_serve_mode(environ={"PUSPA_SERVE_MODE": "trial"})
    with pytest.raises(ServeModeError, match="unknown serve mode"):
        resolve_serve_mode(environ={"PUSPA_SERVE_MODE": "operator"})


def test_explicit_argument_beats_environment(monkeypatch):
    monkeypatch.setenv("PUSPA_SERVE_MODE", "hosted")
    assert resolve_serve_mode("local") == ServeMode.LOCAL


def test_file_config_hosted(tmp_path):
    path = tmp_path / "serve.json"
    path.write_text(json.dumps({"serve_mode": "hosted"}), encoding="utf-8")
    assert resolve_serve_mode(environ={}, config_path=str(path)) == ServeMode.HOSTED


def test_file_config_via_env_var(tmp_path, monkeypatch):
    path = tmp_path / "serve.json"
    path.write_text(json.dumps({"serve_mode": "hosted"}), encoding="utf-8")
    monkeypatch.setenv("PUSPA_SERVE_MODE_FILE", str(path))
    assert resolve_serve_mode() == ServeMode.HOSTED


def test_env_beats_file(tmp_path):
    path = tmp_path / "serve.json"
    path.write_text(json.dumps({"serve_mode": "hosted"}), encoding="utf-8")
    assert (
        resolve_serve_mode(environ={"PUSPA_SERVE_MODE": "local"}, config_path=str(path))
        == ServeMode.LOCAL
    )


def test_file_invalid_value_is_rejected(tmp_path):
    path = tmp_path / "serve.json"
    path.write_text(json.dumps({"serve_mode": "lite"}), encoding="utf-8")
    with pytest.raises(ServeModeError, match="unknown serve mode"):
        resolve_serve_mode(environ={}, config_path=str(path))


def test_file_missing_key_malformed_json_and_missing_file_rejected(tmp_path):
    no_key = tmp_path / "no-key.json"
    no_key.write_text(json.dumps({"mode": "hosted"}), encoding="utf-8")
    with pytest.raises(ServeModeError, match="no 'serve_mode' key"):
        resolve_serve_mode(environ={}, config_path=str(no_key))
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    with pytest.raises(ServeModeError, match="unreadable"):
        resolve_serve_mode(environ={}, config_path=str(bad_json))
    with pytest.raises(ServeModeError, match="not found"):
        resolve_serve_mode(environ={}, config_path=str(tmp_path / "absent.json"))


# ---------------------------------------------------------------------------
# Panel visibility + description
# ---------------------------------------------------------------------------


def test_operator_panels_only_in_local_mode():
    assert operator_panels_visible("local") is True
    assert operator_panels_visible(ServeMode.LOCAL) is True
    assert operator_panels_visible("hosted") is False
    assert operator_panels_visible(ServeMode.HOSTED) is False
    # Unknown mode: fail-closed, panels hidden, never shown.
    assert operator_panels_visible("trial") is False


def test_describe_serve_mode():
    assert "operator" in describe_serve_mode("local").lower()
    assert "end user" in describe_serve_mode("hosted").lower()
    assert "Unknown" in describe_serve_mode("trial")


# ---------------------------------------------------------------------------
# Pairing status reads live store state (never codes or tokens)
# ---------------------------------------------------------------------------


def test_pairing_status_empty_store():
    snapshot = pairing_status(PairingStore(clock=lambda: 1000.0))
    assert snapshot == {
        "active_codes": 0,
        "active_sessions": 0,
        "sessions": [],
        "locked_addresses": 0,
        "locks": [],
    }


def test_pairing_status_counts_live_code_then_live_session():
    now = [1000.0]
    store = PairingStore(clock=lambda: now[0])
    issued = store.issue("user-1", "laptop")
    assert pairing_status(store)["active_codes"] == 1
    assert pairing_status(store)["active_sessions"] == 0
    # The code itself never appears in the snapshot.
    assert issued["code"] not in json.dumps(pairing_status(store))
    verified = store.verify(issued["code"], address="10.0.0.1")
    snapshot = pairing_status(store)
    assert snapshot["active_codes"] == 0
    assert snapshot["active_sessions"] == 1
    assert snapshot["sessions"] == [
        {
            "user_id": "user-1",
            "device_label": "laptop",
            "expires_at": verified["expires_at"],
        }
    ]
    # The session token never appears in the snapshot.
    assert verified["session_token"] not in json.dumps(snapshot)


def test_pairing_status_expired_code_not_counted_and_lock_listed():
    now = [1000.0]
    store = PairingStore(clock=lambda: now[0])
    store.issue("user-1", "laptop")
    now[0] = 1000.0 + 301  # past the 5-minute code TTL
    assert pairing_status(store)["active_codes"] == 0
    for _ in range(5):
        with pytest.raises(Exception, match="unknown, expired, or already used"):
            store.verify("000000", address="10.0.0.9")
    snapshot = pairing_status(store)
    assert snapshot["locked_addresses"] == 1
    assert snapshot["locks"][0]["address"] == "10.0.0.9"
    assert snapshot["locks"][0]["locked_until"] == 1000.0 + 301 + 600


# ---------------------------------------------------------------------------
# Gateway decision log + gateway status read live decisions
# ---------------------------------------------------------------------------


def test_decision_log_records_last_and_recent_newest_first():
    log = GatewayDecisionLog(clock=lambda: 42.0)
    assert log.last() is None
    assert log.count == 0
    log.record({"allowed": True, "outcome": "allow", "rule": "true", "reason": "ok"})
    log.record({"allowed": False, "outcome": "deny", "rule": None, "reason": "no allow"})
    assert log.count == 2
    assert log.last()["outcome"] == "deny"
    assert [entry["outcome"] for entry in log.recent(2)] == ["deny", "allow"]
    assert log.last()["recorded_at"] == 42.0


def test_decision_log_is_bounded():
    log = GatewayDecisionLog(maxlen=3)
    for index in range(5):
        log.record({"allowed": True, "outcome": "allow", "reason": f"decision {index}"})
    assert log.count == 3
    assert log.last()["reason"] == "decision 4"


def test_gateway_policy_summary_default_policy():
    gateway = Gateway.start(DEFAULT_POLICY_DATA)
    summary = gateway.policy_summary()
    assert summary["loaded"] is True
    assert summary["stale"] is False
    assert summary["dry_run"] is False
    assert summary["deny_rules"] == []
    assert summary["allow_rules"] == ["true"]


def test_gateway_policy_summary_never_loaded_snapshot_is_fail_closed():
    from puspa.policy.gateway import PolicySnapshot

    gateway = Gateway(snapshot=PolicySnapshot())
    summary = gateway.policy_summary()
    assert summary["loaded"] is False
    assert summary["deny_rules"] == []
    assert summary["allow_rules"] == []


def test_gateway_status_shows_last_live_decision():
    log = GatewayDecisionLog()
    gateway = Gateway.start(
        {"deny": ['contains(tool.name, "pay")'], "allow": ["true"]}, audit=log.record
    )
    assert gateway_status(gateway, log)["last_decision"] is None
    assert gateway_status(gateway, log)["decisions"] == 0
    gateway.decide(ActionContext(tool_name="browse"))
    gateway.decide(ActionContext(tool_name="pay invoice"))
    status = gateway_status(gateway, log)
    assert status["decisions"] == 2
    assert status["last_decision"]["outcome"] == "deny"
    assert status["last_decision"]["allowed"] is False
    assert "pay" in status["last_decision"]["rule"]
    assert status["policy"]["deny_rules"] == ['contains(tool.name, "pay")']


# ---------------------------------------------------------------------------
# operator_status payload
# ---------------------------------------------------------------------------


def test_operator_status_hosted_hides_panels():
    payload = operator_status("hosted", pairing_store=PairingStore(), gateway=Gateway())
    assert payload == {
        "mode": "hosted",
        "operator_panels": False,
        "pairing": None,
        "gateway": None,
        "log": [],
    }


def test_operator_status_local_reads_live_state():
    log = GatewayDecisionLog()
    gateway = Gateway.start(DEFAULT_POLICY_DATA, audit=log.record)
    gateway.decide(ActionContext(tool_name="browse"))
    events = [
        {"type": "user_message", "created_at": 10.0, "content": {"text": "halo Puspa"}},
        {"type": "task_started", "created_at": 11.0, "content": {"purpose": "riset harga"}},
    ]
    payload = operator_status(
        "local",
        pairing_store=PairingStore(clock=lambda: 1000.0),
        gateway=gateway,
        decision_log=log,
        recent_events=events,
    )
    assert payload["mode"] == "local"
    assert payload["operator_panels"] is True
    assert payload["pairing"]["active_sessions"] == 0
    assert payload["gateway"]["last_decision"]["outcome"] == "allow"
    assert payload["gateway"]["decisions"] == 1
    assert [line["summary"] for line in payload["log"]] == [
        "user_message: halo Puspa",
        "task_started: riset harga",
    ]


def test_operator_status_local_without_objects_is_empty_not_invented():
    payload = operator_status("local")
    assert payload["operator_panels"] is True
    assert payload["pairing"] is None
    assert payload["gateway"] is None
    assert payload["log"] == []


# ---------------------------------------------------------------------------
# Endpoint: GET /api/operator/status (live app wiring)
# ---------------------------------------------------------------------------


@pytest.fixture
def app_client():
    app = create_app(test_user_id="user-1")
    with TestClient(app) as client:
        yield client, app


def test_endpoint_requires_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/api/operator/status")
        assert resp.status_code == 401


def test_endpoint_local_default_shows_live_pairing_gateway_and_log(app_client):
    client, app = app_client
    resp = client.get("/api/operator/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "local"
    assert body["operator_panels"] is True
    assert body["pairing"]["active_codes"] == 0
    assert body["gateway"]["policy"]["allow_rules"] == ["true"]
    assert body["gateway"]["last_decision"] is None
    assert body["log"] == []

    # A pairing code issued against the live store shows up.
    issued = app.state.pairing_store.issue("user-1", "laptop")
    resp = client.get("/api/operator/status")
    assert resp.json()["pairing"]["active_codes"] == 1

    # A gateway decision made through the live gateway shows up,
    # and is mirrored into the live log lines.
    app.state.gateway.decide(ActionContext(tool_name="browse"))
    body = client.get("/api/operator/status").json()
    assert body["gateway"]["decisions"] == 1
    assert body["gateway"]["last_decision"]["outcome"] == "allow"
    assert body["gateway"]["last_decision"]["allowed"] is True
    assert any(line["type"] == "gateway_decision" for line in body["log"])

    # A chat message posted through the live app flows into the log.
    client.post("/api/chat", data={"text": "cek harga laptop"})
    body = client.get("/api/operator/status").json()
    assert any("cek harga laptop" in line["summary"] for line in body["log"])
    # No secret material ever leaks into the payload.
    assert issued["code"] not in json.dumps(body)


def test_endpoint_hosted_hides_panels(monkeypatch):
    monkeypatch.setenv("PUSPA_SERVE_MODE", "hosted")
    app = create_app(test_user_id="user-1")
    with TestClient(app) as client:
        body = client.get("/api/operator/status").json()
        assert body["mode"] == "hosted"
        assert body["operator_panels"] is False
        assert body["pairing"] is None
        assert body["gateway"] is None
        assert body["log"] == []


def test_create_app_rejects_invalid_mode_at_startup(monkeypatch):
    monkeypatch.setenv("PUSPA_SERVE_MODE", "trial")
    with pytest.raises(ServeModeError):
        create_app(test_user_id="user-1")
