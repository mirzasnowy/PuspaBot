"""Tests for E-18 observability (plan section 9.2).

Tracing (trace id == task id end to end), every metric,
every alarm at its threshold, redacted logging with bounded
retention, the /health + operator block, and the wiring into
turn / router / tool / sandbox / renderer / embedding /
scheduler. No live provider, no network: model calls and the
container runtime are fakes. The scheduler tick test uses the
shared Postgres test database (``test_dsn`` fixture).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from puspa.evidence.renderer import present
from puspa.memory.embeddings import HashEmbedding
from puspa.observability import alarms as alarms_mod
from puspa.observability import metrics as metrics_mod
from puspa.observability import tracing as tracing_mod
from puspa.observability.health import observability_block
from puspa.observability.logging import (
    RedactingFormatter,
    get_observability_logger,
    install_redacting_handler,
)
from puspa.pwa.health import build_health_payload
from puspa.pwa.serve_modes import operator_status
from puspa.retrieval.embedding_health import evaluate_startup
from puspa.router.fallback import (
    CallResult,
    ChainEntry,
    ProviderError,
    call_with_fallback,
)
from puspa.router.router import get_valid_oauth_token
from puspa.sandbox.warmpool import ContainerRuntime, WarmPool
from puspa.turns.config import TurnConfig
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply


@pytest.fixture(autouse=True)
def _fresh_observability():
    metrics_mod.reset_metrics()
    alarms_mod.reset_alarms()
    tracing_mod.clear_spans()
    tracing_mod.configure_tracing()
    yield
    metrics_mod.reset_metrics()
    alarms_mod.reset_alarms()
    tracing_mod.clear_spans()


def _metrics():
    return metrics_mod.get_metrics()


def _snapshot():
    return _metrics().snapshot()


def _text_reply(text="Hello from the turn.", *, input_tokens=10, output_tokens=5):
    return ModelReply(text=text, input_tokens=input_tokens, output_tokens=output_tokens)


def _chain_entry(tier, provider="openrouter", model="m"):
    return ChainEntry(
        tier=tier,
        provider=provider,
        model=model,
        family="test",
        rp_per_1k_input=1,
        rp_per_1k_output=2,
        vision=False,
    )


# ---------------------------------------------------------------------------
# Tracing
# ---------------------------------------------------------------------------


def test_start_trace_sets_current_trace_id_and_end_resets():
    token = tracing_mod.start_trace("tugas-abc123")
    assert tracing_mod.current_trace_id() == "tugas-abc123"
    tracing_mod.end_trace(token)
    assert tracing_mod.current_trace_id() is None


def test_span_records_locally_with_task_trace_id():
    with tracing_mod.span("turn", kind="turn", task_id="tugas-abc123"):
        pass
    spans = tracing_mod.finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span["trace_id"] == "tugas-abc123"
    assert span["attributes"]["puspa.task_id"] == "tugas-abc123"
    assert span["kind"] == "turn"
    assert span["status"] == "ok"
    assert span["duration_ms"] >= 0


def test_span_marks_error_on_exception():
    with (
        pytest.raises(ValueError),
        tracing_mod.span("tool:boom", kind="tool", task_id="tugas-err"),
    ):
        raise ValueError("boom")
    span = tracing_mod.finished_spans()[0]
    assert span["status"] == "error"
    assert "ValueError" in (span["error"] or "")


def test_spans_for_trace_filters_by_trace_id():
    with tracing_mod.span("turn", kind="turn", task_id="tugas-one"):
        pass
    with tracing_mod.span("turn", kind="turn", task_id="tugas-two"):
        pass
    assert len(tracing_mod.spans_for_trace("tugas-one")) == 1
    assert len(tracing_mod.spans_for_trace("tugas-two")) == 1
    assert tracing_mod.spans_for_trace("tugas-missing") == []


def test_otel_trace_id_is_deterministic():
    first = tracing_mod.otel_trace_id("tugas-abc123")
    assert first == tracing_mod.otel_trace_id("tugas-abc123")
    assert first != tracing_mod.otel_trace_id("tugas-abc124")
    assert 0 < first < 2**128


def test_configure_tracing_records_otel_spans_locally():
    exporter = tracing_mod.installed_exporter()
    assert exporter is not None
    exporter.clear()
    with tracing_mod.span("turn", kind="turn", task_id="tugas-otel"):
        pass
    exported = exporter.get_finished_spans()
    assert len(exported) == 1
    assert exported[0].context.trace_id == tracing_mod.otel_trace_id("tugas-otel")
    assert exported[0].attributes["puspa.trace_id"] == "tugas-otel"


# ---------------------------------------------------------------------------
# Metrics: every metric is emitted
# ---------------------------------------------------------------------------


def test_turn_latency_ttft_and_status_emitted():
    _metrics().record_turn_latency(0.5, status="completed")
    _metrics().record_turn_latency(1.5, status="failed")
    _metrics().record_ttft(0.2)
    turns = _snapshot()["turns"]
    assert turns["count"] == 2
    assert turns["by_status"] == {"completed": 1, "failed": 1}
    assert turns["latency_ms"]["count"] == 2
    assert turns["latency_ms"]["mean"] == 1000.0
    assert turns["latency_ms"]["max"] == 1500.0
    assert turns["ttft_ms"]["count"] == 1
    assert turns["ttft_ms"]["mean"] == 200.0


def test_fallback_rate_per_chain():
    _metrics().record_fallback("subscription->cheap", hops=1)
    _metrics().record_fallback("subscription->cheap", hops=0)
    _metrics().record_fallback("cheap->free", hops=2)
    chains = _snapshot()["fallback"]["chains"]
    assert chains["subscription->cheap"]["runs"] == 2
    assert chains["subscription->cheap"]["with_fallback"] == 1
    assert chains["subscription->cheap"]["rate"] == 0.5
    assert chains["cheap->free"]["rate"] == 1.0
    assert _metrics().fallback_rate("missing-chain") is None


def test_credential_refresh_failures_per_provider():
    _metrics().record_credential_refresh_failure("xai")
    _metrics().record_credential_refresh_failure("xai")
    _metrics().record_credential_refresh_failure("github_copilot")
    creds = _snapshot()["credentials"]
    assert creds["refresh_failures"] == 3
    assert creds["by_provider"] == {"xai": 2, "github_copilot": 1}


def test_cost_per_task_and_per_user():
    _metrics().record_cost("tugas-1", "user-a", 1500)
    _metrics().record_cost("tugas-1", "user-a", 600)
    _metrics().record_cost("tugas-2", "user-b", 300)
    cost = _snapshot()["cost"]
    assert cost["total_rp"] == 2400
    assert cost["tasks"] == 2
    assert cost["max_cost_per_task_rp"] == 2100
    assert cost["per_user_rp"] == {"user-a": 2100, "user-b": 300}


def test_abandoned_cost_tracked_separately():
    _metrics().record_abandoned_cost("tugas-9", "user-a", 450)
    cost = _snapshot()["cost"]
    assert cost["abandoned_rp"] == 450
    assert cost["abandoned_tasks"] == 1
    assert cost["total_rp"] == 0


def test_held_orphan_numbers():
    _metrics().record_held_orphan_numbers(3)
    _metrics().record_held_orphan_numbers(1)
    orphans = _snapshot()["orphan_numbers"]
    assert orphans["held_total"] == 4
    assert orphans["events"] == 2
    assert orphans["last_held"] == 1


def test_embedding_fallback_ratio():
    assert _metrics().embedding_fallback_ratio() is None
    _metrics().record_embedding_fallback(False)
    _metrics().record_embedding_fallback(True)
    _metrics().record_embedding_fallback(True)
    _metrics().record_embedding_fallback(False)
    embedding = _snapshot()["embedding"]
    assert embedding["total"] == 4
    assert embedding["fallback_total"] == 2
    assert embedding["fallback_ratio"] == 0.5


def test_approval_latency():
    _metrics().record_approval_latency(2.5, request_id="r1", approved=True)
    approvals = _snapshot()["approvals"]
    assert approvals["decisions"] == 1
    assert approvals["latency_ms"]["count"] == 1
    assert approvals["latency_ms"]["mean"] == 2500.0


def test_sandbox_start_p95():
    for seconds in [0.1 * n for n in range(1, 21)]:
        _metrics().record_sandbox_start(seconds)
    start = _snapshot()["sandbox"]["start_ms"]
    assert start["count"] == 20
    assert start["p95"] == 1900.0
    assert _metrics().sandbox_start_p95_ms() == 1900.0


def test_queue_age_tracks_max():
    _metrics().record_queue_age(12.5)
    _metrics().record_queue_age(3.0)
    queue = _snapshot()["queue"]
    assert queue["max_age_s"] == 12.5
    assert queue["age_s"]["count"] == 2


def test_quota_remaining_reported():
    _metrics().report_quota("openrouter:m", 0.05)
    assert _snapshot()["quota"]["providers"] == {"openrouter:m": 0.05}


def test_snapshot_is_json_safe():
    _metrics().record_turn_latency(0.1, status="completed")
    _metrics().record_cost("tugas-1", "user-a", 100)
    json.dumps(_snapshot())


# ---------------------------------------------------------------------------
# Alarms: every rule fires at its threshold
# ---------------------------------------------------------------------------


def test_cost_alarm_fires_above_2000_not_at_2000():
    _metrics().record_cost("tugas-1", "user-a", 2000)
    assert alarms_mod.evaluate_alarms(_snapshot()) == []
    _metrics().record_cost("tugas-2", "user-a", 2001)
    active = alarms_mod.evaluate_alarms(_snapshot())
    assert [alarm["name"] for alarm in active] == ["cost_per_task"]
    assert active[0]["severity"] == "critical"
    assert active[0]["value"] == 2001
    assert active[0]["threshold"] == 2000


def test_embedding_alarm_only_in_production():
    _metrics().record_embedding_fallback(True)
    assert alarms_mod.evaluate_alarms(_snapshot(), environment="development") == []
    active = alarms_mod.evaluate_alarms(_snapshot(), environment="production")
    assert [alarm["name"] for alarm in active] == ["embedding_fallback"]
    assert active[0]["value"] == 1


def test_sandbox_p95_alarm_fires_above_3s():
    for _ in range(20):
        _metrics().record_sandbox_start(3.5)
    active = alarms_mod.evaluate_alarms(_snapshot())
    assert [alarm["name"] for alarm in active] == ["sandbox_start_p95"]
    assert active[0]["value"] == 3500.0
    assert active[0]["threshold"] == 3000


def test_quota_alarm_fires_below_10_percent():
    _metrics().report_quota("openrouter:m", 0.5)
    assert alarms_mod.evaluate_alarms(_snapshot()) == []
    _metrics().report_quota("openrouter:m", 0.09)
    active = alarms_mod.evaluate_alarms(_snapshot())
    assert [alarm["name"] for alarm in active] == ["provider_quota"]
    assert active[0]["detail"]["providers"] == {"openrouter:m": 0.09}


def test_kill_drill_alarm_on_failure_and_clears_on_success():
    alarms_mod.report_kill_drill(False, 1.0)
    active = alarms_mod.get_alarms().active_alarms()
    assert [alarm["name"] for alarm in active] == ["kill_drill_failed"]
    alarms_mod.report_kill_drill(True, 1.0)
    assert alarms_mod.get_alarms().active_alarms() == []


def test_kill_drill_alarm_on_slow_success():
    alarms_mod.report_kill_drill(True, 6.0)
    active = alarms_mod.get_alarms().active_alarms()
    assert [alarm["name"] for alarm in active] == ["kill_drill_failed"]
    assert active[0]["value"] == 6.0


def test_run_kill_drill_success_failure_and_slow():
    ok = alarms_mod.run_kill_drill(lambda: 3)
    assert ok["success"] is True
    assert ok["stopped"] == 3
    assert ok["error"] is None
    assert ok["alarms"] == []

    def _boom():
        raise RuntimeError("stop broke")

    failed = alarms_mod.run_kill_drill(_boom)
    assert failed["success"] is False
    assert "RuntimeError" in (failed["error"] or "")
    assert [alarm["name"] for alarm in failed["alarms"]] == ["kill_drill_failed"]

    slow = alarms_mod.run_kill_drill(lambda: time.sleep(0.01), deadline_s=0.001)
    assert slow["success"] is False
    assert slow["duration_s"] >= 0.01


def test_alarms_clear_when_condition_lapses():
    _metrics().record_cost("tugas-1", "user-a", 5000)
    assert alarms_mod.evaluate_alarms(_snapshot())
    metrics_mod.reset_metrics()
    assert alarms_mod.evaluate_alarms(_snapshot()) == []


# ---------------------------------------------------------------------------
# Logging: redaction survives, retention is bounded
# ---------------------------------------------------------------------------

_SECRET_LOG_LINES = (
    "calling with api_key=sk-abcdefghij0123456789 and token=ghp_abcdefghij0123456789",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
    "dsn postgres://user:s3cretpass@db.internal:5432/puspa",
)


def test_redacting_formatter_scrubs_secrets():
    formatter = RedactingFormatter("%(levelname)s %(message)s")
    for line in _SECRET_LOG_LINES:
        record = logging.LogRecord("test", logging.INFO, __file__, 1, line, (), None)
        formatted = formatter.format(record)
        assert "sk-abcdefghij" not in formatted
        assert "ghp_abcdefghij" not in formatted
        assert "s3cretpass" not in formatted
        assert "eyJhbGciOi" not in formatted
        assert "«redacted»" in formatted


def test_redacting_formatter_prefixes_trace_id():
    token = tracing_mod.start_trace("tugas-log-1")
    try:
        formatter = RedactingFormatter("%(message)s")
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
        assert formatter.format(record).startswith("[trace_id=tugas-log-1] hello")
    finally:
        tracing_mod.end_trace(token)


def test_rotating_file_handler_bounds_retention(tmp_path):
    log_path = tmp_path / "puspa.log"
    logger = logging.getLogger("puspa.e18.retention.test")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    handler = install_redacting_handler(logger, log_path=log_path, max_bytes=200, backup_count=2)
    assert handler is not None
    assert install_redacting_handler(logger, log_path=log_path) is None
    for line in list(_SECRET_LOG_LINES) * 20:
        logger.info(line)
    for handle in list(logger.handlers):
        handle.flush()
        handle.close()
        logger.removeHandler(handle)
    files = sorted(tmp_path.glob("puspa.log*"))
    assert 1 <= len(files) <= 3
    total_bytes = sum(path.stat().st_size for path in files)
    assert total_bytes <= 3 * 200 + 1000
    blob = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in files)
    assert "s3cretpass" not in blob
    assert "sk-abcdefghij" not in blob


def test_get_observability_logger_is_redacted():
    logger = get_observability_logger("puspa.observability.e18.test")
    assert logger.name == "puspa.observability.e18.test"
    parent = logging.getLogger("puspa")
    assert getattr(parent, "_puspa_redacting_installed", False) is True


# ---------------------------------------------------------------------------
# Health + operator block
# ---------------------------------------------------------------------------


def test_observability_block_shape():
    _metrics().record_turn_latency(0.1, status="completed")
    block = observability_block()
    assert set(block) == {"metrics", "alarms", "tracing"}
    assert block["metrics"]["turns"]["count"] == 1
    assert block["alarms"] == []
    assert block["tracing"]["spans_recorded"] == 0


def test_health_payload_degraded_on_critical_alarm_only():
    _metrics().record_cost("tugas-1", "user-a", 5000)
    block = observability_block()
    payload = build_health_payload(None, observability=block)
    assert payload["status"] == "degraded"
    assert payload["observability"]["alarms"]

    metrics_mod.reset_metrics()
    alarms_mod.reset_alarms()
    for _ in range(20):
        _metrics().record_sandbox_start(3.5)
    payload = build_health_payload(None, observability=observability_block())
    assert payload["status"] == "ok"
    assert payload["observability"]["alarms"]


def test_operator_status_carries_observability():
    block = observability_block()
    payload = operator_status("local", observability=block)
    assert payload["operator_panels"] is True
    assert payload["observability"] == block
    hosted = operator_status("hosted", observability=block)
    assert hosted["operator_panels"] is False
    assert "observability" not in hosted


# ---------------------------------------------------------------------------
# Wiring: turn / router / tool / sandbox / renderer / embedding / scheduler
# ---------------------------------------------------------------------------


def test_turn_loop_records_latency_ttft_cost_and_spans():
    def model_call(entry, wire_messages, tools):
        return _text_reply(input_tokens=1_000_000, output_tokens=500_000)

    loop = TurnLoop(model_call=model_call, config=TurnConfig(max_iterations=3))
    result = loop.run_turn(
        [InternalMessage.user("hello")], user_id="user-a", task_id="tugas-turn-1"
    )
    assert result.status == "completed"
    snapshot = _snapshot()
    assert snapshot["turns"]["count"] == 1
    assert snapshot["turns"]["latency_ms"]["count"] == 1
    assert snapshot["turns"]["ttft_ms"]["count"] == 1
    assert snapshot["cost"]["tasks"] == 1
    assert snapshot["cost"]["max_cost_per_task_rp"] > 0
    assert snapshot["cost"]["per_user_rp"] == {"user-a": snapshot["cost"]["max_cost_per_task_rp"]}
    spans = tracing_mod.spans_for_trace("tugas-turn-1")
    kinds = {span["kind"] for span in spans}
    assert "turn" in kinds
    assert "router" in kinds
    assert all(span["trace_id"] == "tugas-turn-1" for span in spans)


def test_turn_loop_tool_span_and_interrupted_abandoned_cost():
    calls = {"model": 0}

    def model_call(entry, wire_messages, tools):
        calls["model"] += 1
        if calls["model"] == 1:
            return ModelReply(
                text="",
                tool_calls=[{"id": "call-1", "name": "search", "arguments": {}}],
                input_tokens=1_000_000,
                output_tokens=500_000,
            )
        return _text_reply()

    interrupt = threading.Event()

    def tool_call(name, arguments):
        interrupt.set()
        return {"ok": True}

    loop = TurnLoop(model_call=model_call, tool_call=tool_call, config=TurnConfig(max_iterations=5))
    result = loop.run_turn(
        [InternalMessage.user("search for x")],
        user_id="user-a",
        task_id="tugas-turn-2",
        interrupt_event=interrupt,
    )
    assert result.status == "interrupted"
    assert result.cost_rp > 0
    snapshot = _snapshot()
    assert snapshot["cost"]["abandoned_rp"] == result.cost_rp
    assert snapshot["cost"]["abandoned_tasks"] == 1
    spans = tracing_mod.spans_for_trace("tugas-turn-2")
    assert any(span["kind"] == "tool" and span["name"] == "tool:search" for span in spans)


def test_fallback_records_rate_and_router_spans():
    chain = [_chain_entry("subscription"), _chain_entry("cheap")]

    def flaky(entry):
        if entry.tier == "subscription":
            raise ProviderError("boom", status=500)
        return CallResult(value="ok", input_tokens=100, output_tokens=50)

    result = call_with_fallback(chain, flaky)
    assert result.value == "ok"
    assert result.tier_used.tier == "cheap"
    chains = _snapshot()["fallback"]["chains"]
    assert chains["subscription->cheap"]["runs"] == 1
    assert chains["subscription->cheap"]["rate"] == 1.0
    spans = tracing_mod.finished_spans()
    router_spans = [span for span in spans if span["kind"] == "router"]
    assert {span["name"] for span in router_spans} == {"router:subscription", "router:cheap"}
    assert router_spans[0]["status"] == "error"
    assert router_spans[1]["status"] == "ok"


def test_oauth_refresh_failure_recorded():
    with pytest.raises(Exception, match="not registered"):
        get_valid_oauth_token("definitely-not-registered")
    creds = _snapshot()["credentials"]
    assert creds["refresh_failures"] == 1
    assert creds["by_provider"] == {"definitely-not-registered": 1}


class _FakeRuntime(ContainerRuntime):
    def __init__(self):
        self.created: list[str] = []

    def create(self, image, flags, command):
        container_id = f"fake-{len(self.created)}"
        self.created.append(container_id)
        return container_id

    def start(self, container_id, *, input, timeout_s):
        return {"ok": True, "stdout": "", "stderr": "", "exit_code": 0, "timed_out": False}

    def remove(self, container_id):
        return None

    def exists(self, container_id):
        return container_id in self.created


def test_warmpool_acquire_records_sandbox_start():
    pool = WarmPool(_FakeRuntime(), image="img", flags_builder=list, size=1)
    slot = pool.acquire("user-a")
    assert slot.container_id == "fake-0"
    start = _snapshot()["sandbox"]["start_ms"]
    assert start["count"] == 2  # one cold create + one warm checkout
    assert start["max"] is not None and start["max"] >= 0


def test_renderer_present_records_held_orphans():
    result = present("Harganya Rp9.999.000.", {})
    assert result["held"] is True
    orphans = _snapshot()["orphan_numbers"]
    assert orphans["held_total"] == 1
    assert orphans["events"] == 1


def test_evaluate_startup_records_embedding_fallback():
    health = evaluate_startup(HashEmbedding(), environment="development")
    assert health.fallback_count == 1
    embedding = _snapshot()["embedding"]
    assert embedding["total"] == 1
    assert embedding["fallback_total"] == 1
    assert embedding["fallback_ratio"] == 1.0


def test_scheduler_tick_records_queue_age(test_dsn, make_user):
    from puspa.scheduler.scheduler import create_trigger, tick

    user_id = make_user()["user_id"]
    now = datetime.now(UTC)
    create_trigger(user_id, "manual", now - timedelta(minutes=10), dsn=test_dsn)
    tick(dsn=test_dsn, now=now)
    queue = _snapshot()["queue"]
    assert queue["age_s"]["count"] == 1
    assert queue["max_age_s"] >= 600.0
