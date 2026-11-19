"""Nightly live smoke lane (E-04): the harness, proven with fakes.

Every probe is exercised through ``httpx.MockTransport`` — no
test touches the network, a real provider, or a real key. The
first live run happens in CI (see docs/nightly-smoke.md).

Covered: the OpenRouter turn and the forced fallback (the
chain must move off the broken-key tier), the Ollama health
check failing with a clear message instead of a raw
``CompletionError``, skill search + install into a temp dir,
the Google callback and refresh-only paths (plus the expired
refresh-token failure), the Telegram roundtrip and button
approval (plus the no-message / no-tap cases), the JSONL
time-series store and its summary, and the runner's exit code.
"""

from __future__ import annotations

import json

import httpx
import pytest

from puspa.channels.telegram import TelegramAdapter
from puspa.nightly.probes import (
    PROBE_NAMES,
    ProbeResult,
    SmokeError,
    probe_google_login,
    probe_ollama_turn,
    probe_openrouter_fallback,
    probe_openrouter_turn,
    probe_skill_discovery,
    probe_telegram_approval,
    probe_telegram_roundtrip,
    run_probes,
)
from puspa.nightly.run import NightlyConfig, main, run_nightly
from puspa.nightly.store import (
    append_record,
    append_records,
    load_records,
    summarize,
    write_summary_markdown,
)

FAKE_KEY = "fake-test-key"

OPENROUTER_REPLY = {
    "choices": [
        {
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
}

MIT_LICENSE = "MIT License\n\nPermission is hereby granted, free of charge."
SKILL_TEXT = """---
name: "Test Skill"
description: "A fake skill for the nightly smoke test."
---
# Test Skill

A fake skill for the nightly smoke test.
"""


# -- transports ----------------------------------------------------------


def _openrouter_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=OPENROUTER_REPLY)


def _openrouter_fallback_handler(request: httpx.Request) -> httpx.Response:
    """First call 401 (broken key), every later call 200."""
    _FALLBACK_CALLS["count"] += 1
    if _FALLBACK_CALLS["count"] == 1:
        return httpx.Response(401, json={"error": {"message": "invalid api key"}})
    return httpx.Response(200, json=OPENROUTER_REPLY)


_FALLBACK_CALLS = {"count": 0}


def _openrouter_always_401_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": {"message": "invalid api key"}})


def _ollama_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/tags":
        return httpx.Response(200, json={"models": [{"name": "qwen2.5:0.5b"}]})
    if request.url.path == "/v1/chat/completions":
        return httpx.Response(200, json=OPENROUTER_REPLY)
    return httpx.Response(404, json={})


def _ollama_down_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def _skills_handler(request: httpx.Request) -> httpx.Response:
    if "skills.sh" in request.url.host and request.url.path == "/api/search":
        return httpx.Response(
            200,
            json={
                "skills": [
                    {
                        "id": "test-skill",
                        "name": "Test Skill",
                        "installs": 5000,
                        "source": "vercel-labs/agent-skills",
                    }
                ]
            },
        )
    return httpx.Response(404, json={})


def _skills_empty_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"skills": []})


def _google_handler(request: httpx.Request) -> httpx.Response:
    if request.url.host == "oauth2.googleapis.com":
        body = request.read().decode()
        if "grant_type=authorization_code" in body:
            return httpx.Response(
                200,
                json={
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(
            200,
            json={
                "access_token": "access-2",
                "refresh_token": "refresh-1",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )
    if request.url.host == "openidconnect.googleapis.com":
        return httpx.Response(200, json={"sub": "111", "email": "smoke-test@example.com"})
    return httpx.Response(404, json={})


def _google_rejected_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json={"error": "invalid_grant"})


TELEGRAM_MESSAGE_UPDATE = {
    "update_id": 1,
    "message": {
        "message_id": 10,
        "date": 1700000000,
        "chat": {"id": 123, "type": "private"},
        "from": {"id": 999, "is_bot": False},
        "text": "smoke ping",
    },
}

TELEGRAM_CALLBACK_UPDATE = {
    "update_id": 2,
    "callback_query": {
        "id": "cq-1",
        "from": {"id": 999, "is_bot": False},
        "message": {"message_id": 7, "chat": {"id": 123, "type": "private"}},
        "data": "smoke:approve",
    },
}


def _telegram_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    _TELEGRAM_SEEN.append(path)
    if path.endswith("/getUpdates"):
        return httpx.Response(
            200,
            json={"ok": True, "result": [TELEGRAM_MESSAGE_UPDATE, TELEGRAM_CALLBACK_UPDATE]},
        )
    if path.endswith("/sendMessage"):
        return httpx.Response(
            200, json={"ok": True, "result": {"message_id": 7, "chat": {"id": 123}}}
        )
    if path.endswith("/answerCallbackQuery"):
        return httpx.Response(200, json={"ok": True, "result": True})
    return httpx.Response(404, json={"ok": False, "description": "not found"})


_TELEGRAM_SEEN: list[str] = []


def _telegram_no_inbound_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/getUpdates"):
        return httpx.Response(200, json={"ok": True, "result": []})
    return _telegram_handler(request)


def _telegram_no_tap_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/getUpdates"):
        return httpx.Response(200, json={"ok": True, "result": [TELEGRAM_MESSAGE_UPDATE]})
    return _telegram_handler(request)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- OpenRouter ----------------------------------------------------------


def test_openrouter_turn_passes_with_cost_and_shape():
    result = probe_openrouter_turn(
        transport=httpx.MockTransport(_openrouter_handler),
        key_resolver=lambda _provider: FAKE_KEY,
    )
    assert result.status == "pass"
    assert result.provider == "openrouter"
    assert result.model
    assert result.cost_rp is not None and result.cost_rp >= 0
    assert result.response_shape["finish_reason"] == "stop"
    assert result.response_shape["input_tokens"] == 10
    assert result.response_shape["output_tokens"] == 3
    assert result.latency_ms >= 0


def test_openrouter_turn_transport_error_is_a_failed_result():
    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    results = run_probes(
        [
            (
                "openrouter_turn",
                lambda: probe_openrouter_turn(
                    transport=httpx.MockTransport(_boom),
                    key_resolver=lambda _provider: FAKE_KEY,
                ),
            )
        ]
    )
    assert results[0].status == "fail"
    assert "openrouter" in results[0].error.lower() or "ConnectError" in results[0].error


def test_openrouter_fallback_moves_off_the_broken_key_tier():
    _FALLBACK_CALLS["count"] = 0
    result = probe_openrouter_fallback(
        transport=httpx.MockTransport(_openrouter_fallback_handler),
        key_resolver=lambda _provider: FAKE_KEY,
    )
    assert result.status == "pass"
    assert result.response_shape["tiers_tried"][0] != result.response_shape["tiers_tried"][-1]
    assert result.response_shape["hops"] == 1
    assert "broken key" in result.detail
    assert result.cost_rp is not None


def test_openrouter_fallback_fails_when_no_tier_answers():
    result = probe_openrouter_fallback(
        transport=httpx.MockTransport(_openrouter_always_401_handler),
        key_resolver=lambda _provider: FAKE_KEY,
    )
    assert result.status == "fail"
    assert "did not move" in result.error


# -- Ollama ---------------------------------------------------------------


def test_ollama_turn_passes_after_health_check():
    result = probe_ollama_turn(transport=httpx.MockTransport(_ollama_handler))
    assert result.status == "pass"
    assert result.provider == "ollama"
    assert result.model == "qwen2.5:0.5b"
    assert result.cost_rp is None  # local compute: no per-token rate
    assert "health check ok" in result.detail


def test_ollama_down_fails_with_a_clear_message_not_a_raw_completion_error():
    with pytest.raises(SmokeError) as excinfo:
        probe_ollama_turn(transport=httpx.MockTransport(_ollama_down_handler))
    assert "Ollama is not reachable" in str(excinfo.value)
    assert "ollama serve" in str(excinfo.value)
    assert "ollama pull qwen2.5:0.5b" in str(excinfo.value)
    assert "CompletionError" not in str(excinfo.value)


def test_ollama_down_becomes_a_failed_result_through_run_probes():
    results = run_probes(
        [
            (
                "ollama_turn",
                lambda: probe_ollama_turn(transport=httpx.MockTransport(_ollama_down_handler)),
            )
        ]
    )
    assert results[0].status == "fail"
    assert "Ollama is not reachable" in results[0].error


def test_ollama_non_200_health_check_fails_clearly():
    def _unhealthy(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={})

    with pytest.raises(SmokeError, match="health check failed"):
        probe_ollama_turn(transport=httpx.MockTransport(_unhealthy))


# -- Skill discovery -------------------------------------------------------


def test_skill_discovery_skips_when_disabled(tmp_path):
    result = probe_skill_discovery(enabled=False, skills_root=tmp_path)
    assert result.status == "skip"
    assert "disabled" in result.detail


def test_skill_discovery_searches_and_installs_to_a_temp_dir(tmp_path):
    result = probe_skill_discovery(
        enabled=True,
        http_client=_client(_skills_handler),
        skills_root=tmp_path / "skills",
        skill_text=SKILL_TEXT,
        license_text=MIT_LICENSE,
    )
    assert result.status == "pass"
    assert result.provider == "skills.sh"
    assert result.model == "test-skill"
    assert result.response_shape["quality"] == "recommended"
    assert (tmp_path / "skills" / "test-skill" / "SKILL.md").read_text() == SKILL_TEXT


def test_skill_discovery_fails_when_the_registry_returns_nothing(tmp_path):
    result = probe_skill_discovery(
        enabled=True,
        http_client=_client(_skills_empty_handler),
        skills_root=tmp_path,
    )
    assert result.status == "fail"
    assert "no skills" in result.error


# -- Google login ----------------------------------------------------------


def test_google_login_full_callback_path_saves_and_refreshes():
    saved: list[dict] = []
    result = probe_google_login(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uri="http://localhost:8000/auth/google/callback",
        auth_code="code-1",
        code_verifier="v" * 50,
        http_client=_client(_google_handler),
        token_store=lambda _user, tokens: saved.append(tokens),
    )
    assert result.status == "pass"
    assert result.response_shape["path"] == "callback"
    assert result.response_shape["expires_in"] == 3600
    assert "smoke-test@example.com" in result.detail
    assert len(saved) == 2  # exchange, then refresh
    assert saved[0]["access_token"] == "access-1"
    assert saved[1]["access_token"] == "access-2"


def test_google_login_refresh_only_path():
    saved: list[dict] = []
    result = probe_google_login(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uri="http://localhost:8000/auth/google/callback",
        refresh_token="refresh-0",
        http_client=_client(_google_handler),
        token_store=lambda _user, tokens: saved.append(tokens),
    )
    assert result.status == "pass"
    assert result.response_shape["path"] == "refresh-only"
    assert len(saved) == 2


def test_google_login_without_credentials_names_the_secret():
    result = probe_google_login(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uri="http://localhost:8000/auth/google/callback",
        http_client=_client(_google_handler),
    )
    assert result.status == "fail"
    assert "PUSPA_GOOGLE_TEST_REFRESH_TOKEN" in result.error


def test_google_login_rejected_refresh_token_points_at_expiry():
    result = probe_google_login(
        client_id="test-client",
        client_secret="test-secret",
        redirect_uri="http://localhost:8000/auth/google/callback",
        refresh_token="refresh-0",
        http_client=_client(_google_rejected_handler),
    )
    assert result.status == "fail"
    assert "7 days" in result.error


# -- Telegram ---------------------------------------------------------------


def test_telegram_adapter_parses_and_answers_a_callback_query():
    _TELEGRAM_SEEN.clear()
    adapter = TelegramAdapter(
        "fake-token", http_client=_client(_telegram_handler), sleep=lambda _s: None
    )
    assert adapter.parse_callback_query(TELEGRAM_MESSAGE_UPDATE) is None
    parsed = adapter.parse_callback_query(TELEGRAM_CALLBACK_UPDATE)
    assert parsed == {
        "callback_query_id": "cq-1",
        "chat_id": "123",
        "sender_id": "999",
        "data": "smoke:approve",
    }
    assert adapter.answer_callback_query("cq-1", text="received: smoke:approve") is True
    assert any(path.endswith("/answerCallbackQuery") for path in _TELEGRAM_SEEN)


def test_telegram_roundtrip_sends_with_keyboard_and_reads_inbound():
    _TELEGRAM_SEEN.clear()
    result = probe_telegram_roundtrip(
        bot_token="fake-token",
        chat_id="123",
        http_client=_client(_telegram_handler),
    )
    assert result.status == "pass"
    assert result.response_shape["sent_message_id"] == 7
    assert result.response_shape["inbound_message_id"] == "10"
    assert any(path.endswith("/sendMessage") for path in _TELEGRAM_SEEN)


def test_telegram_roundtrip_fails_loudly_with_no_inbound_message():
    result = probe_telegram_roundtrip(
        bot_token="fake-token",
        chat_id="123",
        http_client=_client(_telegram_no_inbound_handler),
    )
    assert result.status == "fail"
    assert "send one message to the test bot" in result.error


def test_telegram_approval_answers_the_tap():
    _TELEGRAM_SEEN.clear()
    result = probe_telegram_approval(
        bot_token="fake-token",
        chat_id="123",
        http_client=_client(_telegram_handler),
    )
    assert result.status == "pass"
    assert result.response_shape["callback_data"] == "smoke:approve"
    assert any(path.endswith("/answerCallbackQuery") for path in _TELEGRAM_SEEN)


def test_telegram_approval_skips_when_no_tap_arrived():
    result = probe_telegram_approval(
        bot_token="fake-token",
        chat_id="123",
        http_client=_client(_telegram_no_tap_handler),
    )
    assert result.status == "skip"
    assert "tap Approve" in result.detail


# -- Records and the time-series store -------------------------------------


def test_probe_record_carries_only_whitelisted_fields_and_no_token():
    record = ProbeResult(
        name="google_login",
        status="pass",
        detail="session saved for smoke-test@example.com",
        response_shape={"path": "refresh-only"},
    ).to_record("run-1", "2026-11-20T02:00:00Z")
    assert set(record) == {
        "run_id",
        "timestamp",
        "probe",
        "status",
        "latency_ms",
        "cost_rp",
        "provider",
        "model",
        "response_shape",
        "detail",
        "error",
    }
    assert "token" not in json.dumps(record).lower()


def test_run_probes_turns_a_crash_into_a_failed_result():
    def _boom() -> ProbeResult:
        raise RuntimeError("probe blew up")

    results = run_probes(
        [
            ("openrouter_turn", _boom),
            ("ollama_turn", lambda: ProbeResult(name="ollama_turn", status="pass")),
        ]
    )
    assert results[0].status == "fail"
    assert "RuntimeError: probe blew up" in results[0].error
    assert results[1].status == "pass"


def test_store_roundtrip_skips_a_malformed_line(tmp_path):
    store = tmp_path / "runs.jsonl"
    append_records(
        store,
        [
            {"run_id": "r1", "probe": "openrouter_turn", "status": "pass", "latency_ms": 10},
            {"run_id": "r1", "probe": "ollama_turn", "status": "pass", "latency_ms": 20},
        ],
    )
    with store.open("a", encoding="utf-8") as handle:
        handle.write("{not json\n")
    records = load_records(store)
    assert len(records) == 2
    assert load_records(tmp_path / "missing.jsonl") == []


def test_summarize_reports_p50_p95_cost_and_shape_change(tmp_path):
    store = tmp_path / "runs.jsonl"
    shapes = [
        {"finish_reason": "stop", "input_tokens": 10},
        {"finish_reason": "stop", "input_tokens": 10},
        {"finish_reason": "stop", "input_tokens": 12},
    ]
    for index, (latency, cost, shape) in enumerate(
        [(10, 5, shapes[0]), (20, 6, shapes[1]), (30, 7, shapes[2])]
    ):
        append_record(
            store,
            {
                "run_id": f"r{index}",
                "timestamp": "2026-11-20T02:00:00Z",
                "probe": "openrouter_turn",
                "status": "pass",
                "latency_ms": latency,
                "cost_rp": cost,
                "response_shape": shape,
            },
        )
    append_record(
        store,
        {
            "run_id": "r0",
            "timestamp": "2026-11-20T02:00:00Z",
            "probe": "ollama_turn",
            "status": "pass",
            "latency_ms": 500,
            "cost_rp": None,
            "response_shape": {"finish_reason": "stop"},
        },
    )
    summary = summarize(load_records(store))
    assert summary["runs"] == 3
    probe = summary["probes"]["openrouter_turn"]
    assert probe["runs"] == 3
    assert probe["passes"] == 3
    assert probe["latency_p50_ms"] == 20
    assert probe["latency_p95_ms"] == 30
    assert probe["total_cost_rp"] == 18
    assert probe["shape_changed"] is True  # input_tokens 10 -> 12
    assert summary["probes"]["ollama_turn"]["shape_changed"] is False


def test_summary_markdown_names_the_probe_and_its_p50(tmp_path):
    summary = summarize(
        [
            {
                "run_id": "r1",
                "probe": "openrouter_turn",
                "status": "pass",
                "latency_ms": 20,
                "cost_rp": 5,
                "response_shape": {"finish_reason": "stop"},
            }
        ]
    )
    out = tmp_path / "summary.md"
    write_summary_markdown(out, summary, "r1")
    text = out.read_text()
    assert "openrouter_turn" in text
    assert "20" in text
    assert "Run `r1`" in text


# -- Runner -----------------------------------------------------------------


def test_runner_exits_zero_when_the_only_enabled_probe_skips(tmp_path, monkeypatch):
    monkeypatch.setenv("PUSPA_SKILL_DISCOVERY_ENABLED", "")
    config = NightlyConfig(
        store_path=str(tmp_path / "runs.jsonl"),
        summary_path=str(tmp_path / "summary.md"),
        only=("skill_discovery",),
    )
    report = run_nightly(config)
    assert report.exit_code == 0
    assert report.results[0].status == "skip"
    assert len(load_records(config.store_path)) == 1
    assert (tmp_path / "summary.md").is_file()


def test_runner_exits_one_when_a_required_probe_fails(tmp_path):
    config = NightlyConfig(
        store_path=str(tmp_path / "runs.jsonl"),
        summary_path=str(tmp_path / "summary.md"),
        only=("google_login",),
    )
    report = run_nightly(config)
    assert report.exit_code == 1
    assert report.results[0].status == "fail"


def test_main_rejects_an_unknown_probe_name(tmp_path, capsys):
    code = main(
        [
            "--only",
            "no_such_probe",
            "--store",
            str(tmp_path / "runs.jsonl"),
            "--summary",
            str(tmp_path / "summary.md"),
        ]
    )
    assert code == 2
    assert "unknown probe" in capsys.readouterr().err


def test_probe_names_cover_the_register():
    assert set(PROBE_NAMES) == {
        "openrouter_turn",
        "openrouter_fallback",
        "ollama_turn",
        "skill_discovery",
        "google_login",
        "telegram_roundtrip",
        "telegram_approval_button",
    }
