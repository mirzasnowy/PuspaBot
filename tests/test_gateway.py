"""Tests for the fail-closed action gateway (G5).

In-memory only: no database, no network. The Postgres watcher is
tested with a fake connection factory, never a live connection.
"""

import pytest

from puspa.policy.gateway import (
    ActionContext,
    Gateway,
    GatewayDenied,
    PolicyError,
    PolicySnapshot,
    PostgresPolicyWatcher,
    RuleEvaluationError,
    evaluate_rule,
    load_policy,
    make_ledger_gateway_sink,
    parse_rule,
    template_cost,
)


def _ctx(**overrides):
    base = {
        "tool_name": "click",
        "page_url": "https://example.com/inbox",
        "page_host": "example.com",
        "bot_id": "puspa",
        "actor_id": "user-1",
    }
    base.update(overrides)
    return ActionContext(**base)


def _audit_sink():
    records = []

    def audit(record):
        records.append(record)

    audit.records = records
    return audit


# --- rule language ---


def test_contains_is_case_insensitive():
    assert evaluate_rule('contains(tool.name, "PAY")', _ctx(tool_name="pay_bill")) is True
    assert evaluate_rule('contains(tool.name, "pay")', _ctx(tool_name="click")) is False


def test_matches_is_case_insensitive_regex():
    assert evaluate_rule(r'matches(page.host, "(^|\.)x\.com$")', _ctx(page_host="x.com")) is True
    assert (
        evaluate_rule(r'matches(page.host, "(^|\.)x\.com$")', _ctx(page_host="dropbox.com"))
        is False
    )


def test_comparison_and_boolean_operators():
    ctx = _ctx(tool_name="click", key="Enter")
    assert evaluate_rule('tool.name == "click" and key == "Enter"', ctx) is True
    assert evaluate_rule('tool.name == "click" and key == "Space"', ctx) is False
    assert evaluate_rule('tool.name == "type" or key == "Enter"', ctx) is True
    assert evaluate_rule('not (key == "Enter")', ctx) is False
    assert evaluate_rule('key != "Space"', ctx) is True
    assert evaluate_rule("true", ctx) is True
    assert evaluate_rule("false", ctx) is False


def test_nested_attributes():
    ctx = _ctx(element_type="submit", file_extension="pdf", mcp_tool="read")
    assert evaluate_rule('element.type == "submit"', ctx) is True
    assert evaluate_rule('file.extension == "pdf"', ctx) is True
    assert evaluate_rule('contains(mcp.tool, "read")', ctx) is True
    assert evaluate_rule('initiator.kind == "person"', _ctx(initiator_kind="person")) is True


def test_unknown_attribute_is_a_load_error():
    with pytest.raises(PolicyError, match="unknown attribute"):
        parse_rule('contains(page.cookies, "x")')


def test_invalid_regex_is_a_load_error():
    with pytest.raises(PolicyError, match="invalid regex"):
        parse_rule('matches(page.host, "([")')


def test_malformed_rule_sources_raise():
    for bad in ["", "tool.name =", "contains(tool.name)", 'tool.name === "x"', "foo(bar)"]:
        with pytest.raises(PolicyError):
            parse_rule(bad)


def test_missing_attribute_fails_evaluation():
    # A raw context with no element at all: the rule cannot resolve
    # the attribute, so evaluation raises (the gateway treats this
    # as a deny for deny rules, and as "does not allow" for allow
    # rules). An ActionContext always carries every attribute as an
    # empty string, so this path only exists for raw dict contexts.
    ctx = {"tool": {"name": "click"}}
    with pytest.raises(RuleEvaluationError):
        evaluate_rule('element.type == "submit"', ctx)


# --- policy loading ---


def test_load_policy_rejects_malformed_shapes():
    with pytest.raises(PolicyError, match="must be a dict"):
        load_policy("not a dict")
    with pytest.raises(PolicyError, match="must be a list"):
        load_policy({"deny": "true"})
    with pytest.raises(PolicyError, match="must be strings"):
        load_policy({"allow": [True]})
    with pytest.raises(PolicyError, match="unknown policy sections"):
        load_policy({"deny": [], "allow": ["true"], "block": []})


def test_malformed_policy_halts_startup():
    with pytest.raises(PolicyError):
        Gateway.start({"deny": ["contains(tool.name)"], "allow": ["true"]})
    with pytest.raises(PolicyError):
        Gateway.start({"deny": [], "allow": ['matches(page.host, "([")']})


def test_default_policy_is_explicit():
    gateway = Gateway.start({"deny": [], "allow": ["true"]})
    assert gateway.decide(_ctx()).allowed is True


# --- deny before allow + fail-closed ---


def test_deny_is_evaluated_before_allow():
    gateway = Gateway.start({"deny": ['contains(tool.name, "pay")'], "allow": ["true"]})
    decision = gateway.decide(_ctx(tool_name="pay_bill"))
    assert decision.allowed is False
    assert decision.outcome == "deny"
    assert 'contains(tool.name, "pay")' in (decision.rule or "")
    assert gateway.decide(_ctx(tool_name="click")).allowed is True


def test_missing_policy_allows_nothing():
    gateway = Gateway(policy=None)
    assert gateway.decide(_ctx()).allowed is False
    assert "no policy" in gateway.decide(_ctx()).reason


def test_empty_policy_dict_allows_nothing():
    gateway = Gateway(policy={})
    assert gateway.decide(_ctx()).allowed is False


def test_broken_deny_rule_denies():
    # A deny rule that cannot evaluate (the raw context carries no
    # element at all) denies.
    gateway = Gateway.start({"deny": ['element.type == "submit"'], "allow": ["true"]})
    ctx = {"tool": {"name": "click"}}  # no element carried
    decision = gateway.decide(ctx)
    assert decision.allowed is False
    assert "failing closed" in decision.reason


def test_broken_allow_rule_does_not_allow():
    # An allow rule that cannot evaluate simply does not allow; the
    # decision falls through to "no allow rule matched".
    gateway = Gateway.start({"deny": [], "allow": ['element.type == "submit"', "false"]})
    ctx = {"tool": {"name": "click"}}
    decision = gateway.decide(ctx)
    assert decision.allowed is False
    assert decision.reason == "no allow rule matched"


def test_no_allow_rule_matched_denies():
    gateway = Gateway.start({"deny": [], "allow": ['bot.id == "other-bot"']})
    assert gateway.decide(_ctx()).allowed is False


# --- metadata address: refused in every mode ---


@pytest.mark.parametrize("host", ["169.254.169.254", "metadata.google.internal"])
def test_metadata_address_blocked_in_normal_mode(host):
    gateway = Gateway.start({"deny": [], "allow": ["true"]})
    decision = gateway.decide(_ctx(page_host=host, page_url=f"http://{host}/"))
    assert decision.allowed is False
    assert "metadata" in decision.reason


@pytest.mark.parametrize("host", ["169.254.169.254", "metadata.google.internal"])
def test_metadata_address_blocked_in_dry_run_too(host):
    gateway = Gateway.start({"deny": [], "allow": ["true"]}, dry_run=True)
    decision = gateway.decide(_ctx(page_host=host, page_url=f"http://{host}/"))
    assert decision.allowed is False
    assert decision.dry_run is False


# --- dry-run: decide + record, but still forward ---


def test_dry_run_forwards_a_denied_action_but_records_the_verdict():
    audit = _audit_sink()
    gateway = Gateway.start(
        {"deny": ['contains(tool.name, "pay")'], "allow": ["true"]},
        dry_run=True,
        audit=audit,
    )
    decision = gateway.decide(_ctx(tool_name="pay_bill"))
    assert decision.outcome == "deny"  # the verdict is recorded
    assert decision.allowed is True  # ...but the action forwards
    assert decision.forwarded is True
    assert decision.dry_run is True
    assert audit.records and audit.records[0]["outcome"] == "deny"

    calls = []
    result = gateway.execute(_ctx(tool_name="pay_bill"), lambda: calls.append(1) or "done")
    assert result == "done"
    assert calls == [1]


def test_dry_run_forwards_when_no_allow_rule_matches():
    gateway = Gateway.start({"deny": [], "allow": ['bot.id == "other-bot"']}, dry_run=True)
    decision = gateway.decide(_ctx())
    assert decision.outcome == "deny"
    assert decision.allowed is True


# --- execute: the five steps ---


def test_execute_refuses_without_calling_the_computer():
    gateway = Gateway.start({"deny": ['contains(tool.name, "pay")'], "allow": ["true"]})
    calls = []
    with pytest.raises(GatewayDenied):
        gateway.execute(_ctx(tool_name="pay_bill"), lambda: calls.append(1))
    assert calls == []


def test_execute_audits_the_decision_and_calls_the_computer():
    audit = _audit_sink()
    gateway = Gateway.start({"deny": [], "allow": ["true"]}, audit=audit)
    result = gateway.execute(_ctx(), lambda: "ok")
    assert result == "ok"
    assert len(audit.records) == 1
    assert audit.records[0]["event"] == "gateway_decision"
    assert audit.records[0]["allowed"] is True


def test_execute_audits_a_second_line_when_a_forwarded_action_fails():
    audit = _audit_sink()
    gateway = Gateway.start({"deny": [], "allow": ["true"]}, audit=audit)

    def boom():
        raise RuntimeError("computer exploded")

    with pytest.raises(RuntimeError, match="computer exploded"):
        gateway.execute(_ctx(), boom)
    events = [r["event"] for r in audit.records]
    assert events == ["gateway_decision", "gateway_action_failed"]


def test_execute_audits_error_results_too():
    audit = _audit_sink()
    gateway = Gateway.start({"deny": [], "allow": ["true"]}, audit=audit)
    result = gateway.execute(_ctx(), lambda: {"status": "error", "reason": "nope"})
    assert result["status"] == "error"
    events = [r["event"] for r in audit.records]
    assert events == ["gateway_decision", "gateway_action_failed"]


def test_ledger_sink_maps_decisions_and_failures_to_existing_types():
    seen = []

    def log(event_type, content):
        seen.append((event_type, content))

    sink = make_ledger_gateway_sink(log, task_id="task-1", agent_id="agent-1")
    gateway = Gateway.start({"deny": [], "allow": ["true"]}, audit=sink)
    gateway.execute(_ctx(tool_name="click"), lambda: "ok")
    assert seen[0][0] == "tool_called"
    assert seen[0][1]["status"] == "forwarded"

    def boom():
        raise RuntimeError("x")

    with pytest.raises(RuntimeError):
        gateway.execute(_ctx(tool_name="click"), boom)
    assert seen[-1][0] == "tool_completed"
    assert seen[-1][1]["status"] == "error"


# --- snapshot: never loaded / stale / refresh hook ---


def test_snapshot_never_loaded_refuses_everything():
    snapshot = PolicySnapshot(refresh_hook=lambda: {"deny": [], "allow": ["true"]})
    gateway = Gateway(snapshot=snapshot)
    decision = gateway.decide(_ctx())
    assert decision.allowed is False
    assert "never loaded" in decision.reason
    snapshot.refresh()
    assert gateway.decide(_ctx()).allowed is True


def test_snapshot_refresh_failure_marks_stale_and_refuses():
    calls = {"n": 0}

    def hook():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"deny": [], "allow": ["true"]}
        return {"deny": ["contains(tool.name)"]}  # malformed

    snapshot = PolicySnapshot(refresh_hook=hook)
    snapshot.refresh()
    gateway = Gateway(snapshot=snapshot)
    assert gateway.decide(_ctx()).allowed is True
    with pytest.raises(PolicyError):
        snapshot.refresh()
    assert snapshot.stale is True
    assert gateway.decide(_ctx()).allowed is False


def test_snapshot_target_resolution():
    snapshot = PolicySnapshot(
        refresh_hook=lambda: {"deny": [], "allow": ["true"]},
        targets={"https://example.com/inbox": "inbox"},
    )
    snapshot.refresh()
    gateway = Gateway(snapshot=snapshot)
    assert gateway.decide(_ctx()).allowed is True
    decision = gateway.decide(_ctx(page_url="https://other.example/"))
    assert decision.allowed is False
    assert "not in the server snapshot" in decision.reason


def test_postgres_watcher_uses_listen_notify_via_fake_connection():
    import os

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            assert "LISTEN" in sql

    class FakeConnection:
        def __init__(self):
            self._pending = []
            # A real fd select() can watch; never written to, so it
            # is never ready (the poll path below is what fires).
            self._read_fd, self._write_fd = os.pipe()

        def cursor(self):
            return FakeCursor()

        def notifies(self):
            pending, self._pending = self._pending, []
            return pending

        def poll(self):
            self._pending = ["puspa_policy_changed"]

        def fileno(self):
            return self._read_fd

        def close(self):
            os.close(self._read_fd)
            os.close(self._write_fd)

    fake = FakeConnection()
    watcher = PostgresPolicyWatcher(dsn="dbname=x", connect=lambda: fake)
    # No notification yet: a zero timeout returns False.
    assert watcher.wait_for_change(timeout=0) is False
    # Simulate the server notifying: poll() picks it up.
    fake.poll()
    assert watcher.wait_for_change(timeout=0) is True
    watcher.close()


# --- templates carry their cost ---


def test_every_template_has_a_cost():
    from puspa.policy.gateway import list_templates

    templates = list_templates()
    assert len(templates) >= 4
    for template in templates:
        assert template.cost, f"template {template.name} has no cost"
        assert template.rule
        # Every template rule must parse.
        parse_rule(template.rule)


def test_template_cost_warns_about_the_x_com_trap():
    cost = template_cost("block_host_substring")
    assert "dropbox.com" in cost
    assert "netflix.com" in cost


def test_template_cost_warns_about_enter_space():
    cost = template_cost("never_submit_form")
    assert "Enter" in cost and "Space" in cost


def test_unknown_template_raises():
    with pytest.raises(KeyError):
        template_cost("no_such_template")
