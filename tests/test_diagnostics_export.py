"""Tests for the redacted diagnostics export.

Unit tests (no database) prove the redaction: fake secrets
planted in log lines, span attributes, and ledger event
content must never survive into the bundle, while the
structure (event types, span names, counts, user id) stays
intact. Integration tests (live ``puspa_test_w1`` database)
prove the collection path — ledger events, message-trace
spans, the CLI, and the PWA download — end to end.
"""

import json

import pytest
from fastapi.testclient import TestClient

from puspa.diagnostics.export import (
    DIAGNOSTICS_FORMAT,
    DIAGNOSTICS_VERSION,
    MAX_LEDGER_EVENTS,
    MAX_LOG_LINES,
    MAX_SPANS,
    build_diagnostics_bundle,
    collect_diagnostics,
    export_diagnostics,
)
from puspa.diagnostics.redact import (
    REDACTED,
    redact_diagnostics_text,
    redact_diagnostics_value,
)
from puspa.observability.tracing import clear_spans
from puspa.observability.tracing import span as trace_span
from puspa.pwa.app import create_app

#: Fake secrets (never real credentials; each matches a
#: redaction pattern on purpose).
FAKE_OPENAI_KEY = "sk-testfakekey0123456789abcdef"
FAKE_BEARER = "Bearer abcdefghijklmnopqrstuvwx"
FAKE_GITHUB_TOKEN = "ghp_faketoken0123456789abcd"
FAKE_PASSWORD_LINE = "password=hunter2-secret"
FAKE_API_KEY_LINE = "api_key=AKIAIOSFODNN7EXAMPLE"
FAKE_DB_URL = "postgres://bob:s3cretpass@db.internal:5432/puspa"
FAKE_EMAIL = "someone@example.com"
FAKE_AUTH_HEADER = "Authorization: Bearer abcdefghijklmnopqrstuvwx"


@pytest.fixture
def clean_spans():
    clear_spans()
    yield
    clear_spans()


@pytest.fixture
def pwa_client(make_user):
    user = make_user()
    app = create_app(test_user_id=user["user_id"])
    with TestClient(app) as client:
        client.user_id = user["user_id"]
        client.agent_id = user["agent_id"]
        yield client


def _dump(document: dict) -> str:
    return json.dumps(document, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Redaction (unit, no database)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret",
    [
        FAKE_OPENAI_KEY,
        FAKE_BEARER,
        FAKE_GITHUB_TOKEN,
        FAKE_PASSWORD_LINE,
        FAKE_API_KEY_LINE,
        FAKE_DB_URL,
        FAKE_AUTH_HEADER,
    ],
)
def test_redact_text_catches_common_secret_shapes(secret):
    redacted = redact_diagnostics_text(f"call failed: {secret}")
    assert secret not in redacted
    assert REDACTED in redacted


def test_redact_text_catches_email_addresses():
    redacted = redact_diagnostics_text(f"user {FAKE_EMAIL} reported a bug")
    assert FAKE_EMAIL not in redacted
    assert REDACTED in redacted


def test_redact_text_keeps_ordinary_text():
    text = "tool read finished in 12ms, task tugas-abc123 done"
    assert redact_diagnostics_text(text) == text


def test_redact_value_redacts_nested_structures():
    value = {
        "headers": {"Authorization": FAKE_BEARER, "x-api-key": FAKE_OPENAI_KEY},
        "steps": [{"note": FAKE_PASSWORD_LINE}, ["plain", FAKE_EMAIL]],
    }
    redacted = redact_diagnostics_value(value)
    dumped = json.dumps(redacted)
    for secret in (FAKE_BEARER, FAKE_OPENAI_KEY, FAKE_PASSWORD_LINE, FAKE_EMAIL):
        assert secret not in dumped
    # Structure survives: keys and the plain string are still there.
    assert redacted["headers"]["Authorization"] == REDACTED
    assert redacted["steps"][1][0] == "plain"


def test_redact_value_rejects_non_string():
    with pytest.raises(ValueError):
        redact_diagnostics_text(123)


# ---------------------------------------------------------------------------
# Bundle builder (unit, no database)
# ---------------------------------------------------------------------------


def _seeded_bundle():
    return build_diagnostics_bundle(
        user_id="user-1",
        log_lines=[
            f"[trace_id=tugas-abc] tool read failed, key {FAKE_OPENAI_KEY}",
            f"login as {FAKE_EMAIL}",
            "plain line, nothing secret",
        ],
        spans=[
            {
                "name": "turn",
                "kind": "turn",
                "trace_id": "tugas-abc",
                "started_at": 1793000000.0,
                "duration_ms": 12.5,
                "attributes": {"tool": "read", "auth": FAKE_AUTH_HEADER},
                "status": "ok",
                "error": None,
            }
        ],
        ledger_events=[
            {
                "id": 1,
                "created_at": "2026-11-22T06:00:00+00:00",
                "task_id": "tugas-abc",
                "agent_id": "agen-1",
                "type": "tool_called",
                "content": {"tool": "read", "note": FAKE_PASSWORD_LINE},
            }
        ],
    )


def test_bundle_has_no_secrets_but_keeps_structure():
    document = _seeded_bundle()
    dumped = _dump(document)
    for secret in (
        FAKE_OPENAI_KEY,
        FAKE_EMAIL,
        FAKE_AUTH_HEADER,
        FAKE_PASSWORD_LINE,
    ):
        assert secret not in dumped
    # Structure intact: format, user id, counts, event type,
    # span name, and the plain log line are all still there.
    assert document["format"] == DIAGNOSTICS_FORMAT
    assert document["version"] == DIAGNOSTICS_VERSION
    assert document["user"] == {"id": "user-1"}
    assert document["counts"] == {"logs": 3, "message_trace": 1, "ledger_events": 1}
    assert document["sections"]["ledger_events"][0]["type"] == "tool_called"
    assert document["sections"]["message_trace"][0]["name"] == "turn"
    assert document["sections"]["logs"][2] == "plain line, nothing secret"
    assert document["redactions_applied"] >= 4


def test_bundle_carries_no_email_address():
    document = _seeded_bundle()
    assert "email" not in document["user"]
    assert FAKE_EMAIL not in _dump(document)


def test_bundle_caps_each_section_and_marks_truncation():
    document = build_diagnostics_bundle(
        user_id="user-1",
        log_lines=[f"line {n}" for n in range(MAX_LOG_LINES + 100)],
        spans=[
            {
                "name": f"s{n}",
                "kind": "turn",
                "trace_id": "t",
                "started_at": 1.0,
                "duration_ms": 1.0,
                "attributes": {},
                "status": "ok",
                "error": None,
            }
            for n in range(MAX_SPANS + 50)
        ],
        ledger_events=[{"id": n, "type": "heartbeat"} for n in range(MAX_LEDGER_EVENTS + 10)],
    )
    assert document["counts"] == {
        "logs": MAX_LOG_LINES,
        "message_trace": MAX_SPANS,
        "ledger_events": MAX_LEDGER_EVENTS,
    }
    assert document["truncated"] == {
        "logs": True,
        "message_trace": True,
        "ledger_events": True,
    }
    # The most recent rows are kept.
    assert document["sections"]["logs"][0] == f"line {100}"
    assert document["sections"]["logs"][-1] == f"line {MAX_LOG_LINES + 99}"


def test_bundle_rejects_empty_user_id():
    with pytest.raises(ValueError):
        build_diagnostics_bundle(user_id="")


def test_bundle_lists_what_is_excluded():
    document = build_diagnostics_bundle(user_id="user-1")
    assert document["excluded"]
    assert any("secret" in line.lower() for line in document["excluded"])


# ---------------------------------------------------------------------------
# Collection (live database)
# ---------------------------------------------------------------------------


def test_collect_includes_ledger_event_redacted(make_user, ledger, test_dsn):
    user = make_user()
    ledger.append_event(
        task_id="tugas-diag-1",
        agent_id=user["agent_id"],
        event_type="tool_called",
        content={"tool": "read", "note": f"key was {FAKE_OPENAI_KEY}"},
    )
    document = collect_diagnostics(user["user_id"], dsn=test_dsn)
    dumped = _dump(document)
    assert FAKE_OPENAI_KEY not in dumped
    events = document["sections"]["ledger_events"]
    assert len(events) == 1
    assert events[0]["type"] == "tool_called"
    assert events[0]["task_id"] == "tugas-diag-1"
    assert document["counts"]["ledger_events"] == 1


def test_collect_scopes_to_one_task(make_user, ledger, test_dsn):
    user = make_user()
    for task_id in ("tugas-diag-1", "tugas-diag-2"):
        ledger.append_event(
            task_id=task_id,
            agent_id=user["agent_id"],
            event_type="heartbeat",
            content={},
        )
    document = collect_diagnostics(user["user_id"], dsn=test_dsn, task_id="tugas-diag-2")
    events = document["sections"]["ledger_events"]
    assert [event["task_id"] for event in events] == ["tugas-diag-2"]
    assert document["task_id"] == "tugas-diag-2"


def test_collect_includes_spans_for_user_tasks(make_user, ledger, test_dsn, clean_spans):
    user = make_user()
    ledger.append_event(
        task_id="tugas-diag-1",
        agent_id=user["agent_id"],
        event_type="task_created",
        content={},
    )
    with trace_span("turn", task_id="tugas-diag-1", attributes={"note": FAKE_AUTH_HEADER}):
        pass
    with trace_span("turn", task_id="tugas-other", attributes={}):
        pass
    document = collect_diagnostics(user["user_id"], dsn=test_dsn)
    dumped = _dump(document)
    assert FAKE_AUTH_HEADER not in dumped
    spans = document["sections"]["message_trace"]
    assert [span["trace_id"] for span in spans] == ["tugas-diag-1"]


def test_collect_unknown_user_raises(test_dsn):
    import uuid

    with pytest.raises(ValueError, match="unknown user"):
        collect_diagnostics(str(uuid.uuid4()), dsn=test_dsn)


def test_collect_since_filters_old_events(make_user, ledger, test_dsn):
    user = make_user()
    ledger.append_event(
        task_id="tugas-diag-1",
        agent_id=user["agent_id"],
        event_type="heartbeat",
        content={},
    )
    document = collect_diagnostics(user["user_id"], dsn=test_dsn, since="2999-01-01")
    assert document["counts"]["ledger_events"] == 0


def test_collect_rejects_bad_since(make_user, test_dsn):
    user = make_user()
    with pytest.raises(ValueError, match="since must be an ISO"):
        collect_diagnostics(user["user_id"], dsn=test_dsn, since="not-a-date")


# ---------------------------------------------------------------------------
# File export + audit record
# ---------------------------------------------------------------------------


def test_export_writes_file_and_records_audit_log(make_user, test_dsn, tmp_path):
    user = make_user()
    out = tmp_path / "diag.json"
    audit_dir = tmp_path / "audit"
    result = export_diagnostics(
        user["user_id"], out, dsn=test_dsn, log_lines=["plain line"], audit_dir=audit_dir
    )
    assert out.exists()
    assert not list(tmp_path.glob("*.tmp"))
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["format"] == DIAGNOSTICS_FORMAT
    assert result.total_rows == 1
    assert result.path == out
    from puspa.ledger.audit import AuditExportLog

    records = AuditExportLog(audit_dir).read_all()
    assert len(records) == 1
    assert records[0]["exporter"] == user["user_id"]
    assert records[0]["filters"]["kind"] == "diagnostics"


def test_export_reads_log_file(make_user, test_dsn, tmp_path):
    user = make_user()
    log_file = tmp_path / "puspa.log"
    log_file.write_text(f"boom {FAKE_GITHUB_TOKEN}\nplain\n", encoding="utf-8")
    out = tmp_path / "diag.json"
    export_diagnostics(user["user_id"], out, dsn=test_dsn, log_file=log_file)
    document = json.loads(out.read_text(encoding="utf-8"))
    assert FAKE_GITHUB_TOKEN not in _dump(document)
    assert document["sections"]["logs"] == ["boom «redacted»", "plain"]


def test_export_missing_log_file_raises(make_user, test_dsn, tmp_path):
    user = make_user()
    with pytest.raises(ValueError, match="log file not found"):
        export_diagnostics(
            user["user_id"], tmp_path / "diag.json", dsn=test_dsn, log_file="/nope.log"
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_export_end_to_end(make_user, test_dsn, tmp_path, capsys):
    from puspa.cli import main

    user = make_user()
    out = tmp_path / "diag.json"
    code = main(
        [
            "diagnostics",
            "export",
            "--user",
            user["user_id"],
            "--out",
            str(out),
            "--audit-dir",
            str(tmp_path / "audit"),
        ]
    )
    assert code == 0
    assert out.exists()
    printed = capsys.readouterr().out
    assert str(out) in printed
    assert '"total_rows": 0' in printed


def test_cli_export_with_log_file_redacts(make_user, test_dsn, tmp_path):
    from puspa.cli import main

    user = make_user()
    log_file = tmp_path / "puspa.log"
    log_file.write_text(f"Authorization: {FAKE_BEARER}\n", encoding="utf-8")
    out = tmp_path / "diag.json"
    code = main(
        [
            "diagnostics",
            "export",
            "--user",
            user["user_id"],
            "--out",
            str(out),
            "--log-file",
            str(log_file),
            "--audit-dir",
            str(tmp_path / "audit"),
        ]
    )
    assert code == 0
    assert FAKE_BEARER not in out.read_text(encoding="utf-8")


def test_cli_unknown_user_fails(test_dsn, tmp_path, capsys):
    from puspa.cli import main

    code = main(
        [
            "diagnostics",
            "export",
            "--user",
            "no-such-user",
            "--out",
            str(tmp_path / "diag.json"),
            "--audit-dir",
            str(tmp_path / "audit"),
        ]
    )
    assert code == 1
    assert "failed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# PWA page + download
# ---------------------------------------------------------------------------


def test_diagnostics_page_renders(pwa_client):
    resp = pwa_client.get("/diagnostics")
    assert resp.status_code == 200
    assert "Puspa" in resp.text


def test_diagnostics_page_jinja_template_without_dist(pwa_client, monkeypatch, tmp_path):
    """The Jinja template must exist: CI runs without a dist/ build,
    so the page falls back to apps/pwa/templates/diagnostics.html.
    A missing template would 500 here while the local dist/ build
    hides it."""
    import puspa.pwa.app as app_module

    monkeypatch.setattr(app_module, "DIST_DIR", tmp_path / "no-dist-here")
    resp = pwa_client.get("/diagnostics")
    assert resp.status_code == 200
    assert "Diagnostik" in resp.text
    assert "/api/diagnostics/export" in resp.text
    assert "redacted" in resp.text


def test_api_export_download_is_redacted(pwa_client, ledger, clean_spans):
    user_id = pwa_client.user_id
    ledger.append_event(
        task_id="tugas-diag-1",
        agent_id=pwa_client.agent_id,
        event_type="heartbeat",
        content={},
    )
    # The app event log is the PWA log source: a secret planted
    # there must not survive the download.
    app = pwa_client.app
    app.state.event.append({"type": "note", "content": f"key {FAKE_OPENAI_KEY}"})
    resp = pwa_client.get("/api/diagnostics/export")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.headers["content-disposition"].endswith('.json"')
    document = resp.json()
    assert document["format"] == DIAGNOSTICS_FORMAT
    assert document["user"] == {"id": user_id}
    assert FAKE_OPENAI_KEY not in resp.text


def test_api_export_requires_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as client:
        resp = client.get("/api/diagnostics/export")
    assert resp.status_code == 401


def test_api_export_scopes_to_task(pwa_client, ledger):
    ledger.append_event(
        task_id="tugas-diag-1",
        agent_id=pwa_client.agent_id,
        event_type="heartbeat",
        content={},
    )
    ledger.append_event(
        task_id="tugas-diag-2",
        agent_id=pwa_client.agent_id,
        event_type="heartbeat",
        content={},
    )
    resp = pwa_client.get("/api/diagnostics/export", params={"task_id": "tugas-diag-2"})
    assert resp.status_code == 200
    events = resp.json()["sections"]["ledger_events"]
    assert [event["task_id"] for event in events] == ["tugas-diag-2"]
