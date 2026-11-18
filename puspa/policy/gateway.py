"""Fail-closed action gateway with CEL-style rules (G5, maturity phase).

The gateway is the single action boundary for computer actions
(browser / desktop control), following the pattern in OpenBot's
``docs/architecture.md`` + ``app/src/lib/boundaries/rules.ts``
(rewritten as Puspa code from the documentation; the enforcing CEL
code is not in the OpenBot repo — its enforcement server image is
closed — so this evaluator is Puspa's own, written from the
documented semantics; see ``NOTICE.md`` and ``REUSE.md``):

1. Resolve the target from the server snapshot.
2. Evaluate the action policy as it stands right now.
3. Write an audit line for that decision.
4. Call the computer only when the decision forwards.
5. Write a second audit line when a forwarded action fails.

Rule language: a small, safe expression language (see
``puspa.policy.rules``) — never ``eval()``. It supports
``contains()`` and ``matches()`` (both case-insensitive) over the
attributes OpenBot documents: ``tool.name``, ``intent``,
``bot.id``, ``actor.id``, ``page.url``, ``page.host``,
``element.ref`` / ``element.role`` / ``element.name`` /
``element.type``, ``key``, ``command``, ``file.path`` /
``file.name`` / ``file.extension``, ``mcp.server`` / ``mcp.tool`` /
``mcp.effect``, ``initiator.kind`` / ``initiator.id``; plus
``and`` / ``or`` / ``not``, parentheses, ``==`` / ``!=`` against
string literals, and the literals ``true`` / ``false``.

Deny is evaluated BEFORE allow. Fail-closed throughout:

- Missing or empty policy -> nothing is allowed.
- A deny rule that fails to evaluate -> deny.
- An allow rule that fails to evaluate -> it does not allow.
- A malformed policy HALTS server startup: :func:`load_policy`
  (and :meth:`Gateway.start`) raise :class:`PolicyError` at load
  time, and there is a test for it.
- A snapshot that was never loaded -> everything is refused.
- A stale snapshot (a refresh failed) -> everything is refused.

Dry-run mode decides and records every action but still forwards.
The snapshot is kept fresh through an injected refresh hook, or
through Postgres ``LISTEN``/``NOTIFY`` (:class:`PostgresPolicyWatcher`
wakes the snapshot; the loader itself stays injected so the
snapshot never depends on a table Puspa does not own).

Every built-in rule template carries its ``cost`` — what else it
catches or misses — shown before the rule is added. Two traps from
OpenBot's own history are documented in the templates: "Never
submit a form" also stops Enter/Space anywhere, and
``contains(page.host, "x.com")`` once also stopped dropbox.com and
netflix.com.

Acknowledged limits (also OpenBot's): egress filtering does not
stop raw sockets (that is a Kubernetes NetworkPolicy job, not this
gateway's); the cloud metadata address (169.254.169.254 /
metadata.google.internal) is refused in every mode, including
dry-run.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

from .rules import (
    CompiledRule,
    PolicyError,
    RuleEvaluationError,
    evaluate_rule,
    parse_rule,
)

__all__ = [
    "BUILTIN_TEMPLATES",
    "DEFAULT_POLICY_DATA",
    "METADATA_HOSTS",
    "ActionContext",
    "CompiledRule",
    "Gateway",
    "GatewayDecision",
    "GatewayDenied",
    "Policy",
    "PolicyError",
    "PolicySnapshot",
    "PostgresPolicyWatcher",
    "RuleEvaluationError",
    "RuleTemplate",
    "describe_template",
    "evaluate_rule",
    "load_policy",
    "load_policy_from_file",
    "make_ledger_gateway_sink",
    "parse_rule",
    "template_cost",
]

#: Cloud metadata hosts: refused in every mode, including dry-run.
METADATA_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})


#: Explicit default at startup: deny nothing in particular, allow
#: everything (``deny: []``, ``allow: ["true"]``). The default is
#: written out, never implicit.
DEFAULT_POLICY_DATA = {"deny": [], "allow": ["true"]}


class GatewayDenied(Exception):
    """The gateway refused an action. Nothing was called."""


# ---------------------------------------------------------------------------
# Action context
# ---------------------------------------------------------------------------


@dataclass
class ActionContext:
    """Everything a rule may inspect about one action.

    Missing fields read as empty strings, so a rule that checks an
    attribute the action does not carry simply does not match it
    (it does not crash the evaluation).
    """

    tool_name: str = ""
    intent: str = ""
    bot_id: str = ""
    actor_id: str = ""
    page_url: str = ""
    page_host: str = ""
    element_ref: str = ""
    element_role: str = ""
    element_name: str = ""
    element_type: str = ""
    key: str = ""
    command: str = ""
    file_path: str = ""
    file_name: str = ""
    file_extension: str = ""
    mcp_server: str = ""
    mcp_tool: str = ""
    mcp_effect: str = ""
    initiator_kind: str = ""
    initiator_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Nested dict the evaluator walks (``tool.name`` etc.)."""
        return {
            "tool": {"name": self.tool_name},
            "intent": self.intent,
            "bot": {"id": self.bot_id},
            "actor": {"id": self.actor_id},
            "page": {"url": self.page_url, "host": self.page_host},
            "element": {
                "ref": self.element_ref,
                "role": self.element_role,
                "name": self.element_name,
                "type": self.element_type,
            },
            "key": self.key,
            "command": self.command,
            "file": {
                "path": self.file_path,
                "name": self.file_name,
                "extension": self.file_extension,
            },
            "mcp": {
                "server": self.mcp_server,
                "tool": self.mcp_tool,
                "effect": self.mcp_effect,
            },
            "initiator": {"kind": self.initiator_kind, "id": self.initiator_id},
        }


# ---------------------------------------------------------------------------
# Policy (compiled) + loading
# ---------------------------------------------------------------------------


@dataclass
class Policy:
    """A compiled policy: deny rules, then allow rules."""

    deny: list[CompiledRule] = field(default_factory=list)
    allow: list[CompiledRule] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.deny and not self.allow


def load_policy(data: dict | None) -> Policy:
    """Compile a policy dict (``{"deny": [...], "allow": [...]}``).

    Raises :class:`PolicyError` on anything malformed: not a dict,
    deny/allow not lists of strings, an unparseable rule, an
    unknown attribute, or a bad regex. A missing or empty policy
    compiles to an empty policy, which the gateway treats as
    deny-everything (fail-closed) — it does not raise here, because
    "no policy" is a runtime state, not a syntax error.
    """
    if data is None:
        return Policy()
    if not isinstance(data, dict):
        raise PolicyError(f"policy must be a dict, got {type(data).__name__}")
    policy = Policy()
    for section in ("deny", "allow"):
        rules = data.get(section, [])
        if not isinstance(rules, list):
            raise PolicyError(f"policy {section!r} must be a list, got {type(rules).__name__}")
        compiled = []
        for source in rules:
            if not isinstance(source, str):
                raise PolicyError(f"policy {section!r} entries must be strings, got {source!r}")
            compiled.append(CompiledRule(source=source, ast=parse_rule(source)))
        if section == "deny":
            policy.deny = compiled
        else:
            policy.allow = compiled
    unknown = set(data) - {"deny", "allow"}
    if unknown:
        raise PolicyError(f"unknown policy sections: {sorted(unknown)}")
    return policy


def load_policy_from_file(path) -> Policy:
    """Load a policy from a JSON file. Raises PolicyError (and
    FileNotFoundError / JSON errors) at load time."""
    import json
    from pathlib import Path

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PolicyError(f"policy file {path} is not valid JSON: {exc}") from exc
    return load_policy(data)


# ---------------------------------------------------------------------------
# Snapshot (server state the gateway resolves targets from)
# ---------------------------------------------------------------------------


class PolicySnapshot:
    """The server snapshot the gateway decides from.

    The snapshot holds the current compiled policy and, optionally,
    the set of targets the computer may act on. It is refreshed
    through an injected ``refresh_hook`` (a callable returning a
    policy dict, e.g. reading a file or a table the deployer owns).
    A failed refresh marks the snapshot stale and the gateway then
    refuses everything until a refresh succeeds.
    """

    def __init__(self, *, refresh_hook=None, targets: dict | None = None) -> None:
        self._refresh_hook = refresh_hook
        self._targets: dict = dict(targets or {})
        self._policy: Policy | None = None
        self._loaded = False
        self._stale = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def stale(self) -> bool:
        return self._stale

    @property
    def policy(self) -> Policy | None:
        return self._policy

    def refresh(self) -> Policy:
        """Re-read the policy through the hook and recompile it.

        A malformed policy raises PolicyError and marks the snapshot
        stale (fail-closed: the gateway refuses everything). A
        missing hook is a no-op that keeps the current snapshot.
        """
        if self._refresh_hook is None:
            return self._policy if self._policy is not None else Policy()
        data = self._refresh_hook()
        try:
            policy = load_policy(data)
        except PolicyError:
            self._stale = True
            raise
        self._policy = policy
        self._loaded = True
        self._stale = False
        return policy

    def resolve_target(self, context: ActionContext | dict) -> str | None:
        """Resolve the action's target from the snapshot.

        When the snapshot carries no target table, resolution is a
        no-op (the target is the action itself). When it does, the
        target must be listed or the action is refused.
        """
        if not self._targets:
            if isinstance(context, dict):
                page_url = (context.get("page") or {}).get("url", "")
                tool_name = (context.get("tool") or {}).get("name", "")
            else:
                page_url, tool_name = context.page_url, context.tool_name
            return page_url or tool_name or "action"
        if isinstance(context, dict):
            key = (context.get("page") or {}).get("url", "") or (context.get("tool") or {}).get(
                "name", ""
            )
        else:
            key = context.page_url or context.tool_name
        return self._targets.get(key)


class PostgresPolicyWatcher:
    """Postgres LISTEN/NOTIFY wake-up for snapshot refreshes.

    The watcher only signals *that* the policy changed; how the new
    policy is fetched stays with the injected loader (the snapshot's
    refresh hook), so this class never depends on a policy table
    Puspa does not own. ``connect`` is injectable for tests (a
    factory returning a connection-like object with ``cursor()`` /
    ``notifies()``); by default it connects with psycopg.
    """

    def __init__(
        self, dsn: str | None = None, *, channel: str = "puspa_policy_changed", connect=None
    ) -> None:
        self.dsn = dsn
        self.channel = channel
        self._connect = connect
        self._conn = None

    def _ensure_connected(self):
        if self._conn is not None:
            return self._conn
        if self._connect is not None:
            self._conn = self._connect()
        else:
            import psycopg

            self._conn = psycopg.connect(self.dsn or "", autocommit=True)
        with self._conn.cursor() as cur:
            cur.execute(f'LISTEN "{self.channel}"')
        return self._conn

    def wait_for_change(self, timeout: float = 30.0) -> bool:
        """Block up to ``timeout`` seconds for a notification.
        Returns True when one arrived, False on timeout."""
        import select

        conn = self._ensure_connected()
        if conn.notifies():
            return True
        ready, _, _ = select.select([conn], [], [], timeout)
        if not ready:
            return False
        conn.poll()
        return bool(conn.notifies())

    def close(self) -> None:
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.close()
            self._conn = None


# ---------------------------------------------------------------------------
# Built-in rule templates (each carries its cost)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuleTemplate:
    """One built-in rule template: the rule plus its cost.

    The cost is shown BEFORE the rule is added: what else the rule
    catches, or what it misses.
    """

    name: str
    rule: str
    cost: str
    description: str = ""


BUILTIN_TEMPLATES: tuple[RuleTemplate, ...] = (
    RuleTemplate(
        name="never_submit_form",
        rule='not (key == "Enter" or key == "Space" or element.type == "submit")',
        cost=(
            "Also blocks pressing Enter and Space anywhere, including plain text "
            "fields and chat boxes — not only form submits. A narrower rule "
            'targets element.type == "submit" only.'
        ),
        description="Never submit a form (deny rule).",
    ),
    RuleTemplate(
        name="block_host_substring",
        rule='not contains(page.host, "x.com")',
        cost=(
            "contains() is a substring match: this also blocks dropbox.com and "
            "netflix.com, which both contain 'x.com'. Prefer "
            'matches(page.host, "(^|\\.)x\\.com$") for an exact host.'
        ),
        description="Block a host by substring (deny rule).",
    ),
    RuleTemplate(
        name="block_payment_tools",
        rule='not contains(tool.name, "pay")',
        cost=(
            "Catches pay, payment and payroll tools, but misses money-moving "
            "tools named transfer, order or subscription that never use the "
            "word 'pay'. Pair with the G4 payment taxonomy."
        ),
        description="Block payment tools by name (deny rule).",
    ),
    RuleTemplate(
        name="deny_credential_changes",
        rule='not (contains(tool.name, "password") or contains(tool.name, "credential"))',
        cost=(
            "Misses credential changes named 'rotate secret', 'update login' or "
            "'change api key'. The G4 taxonomy (credential changes are never "
            "approvable) is the backstop, not this rule."
        ),
        description="Block credential-change tools by name (deny rule).",
    ),
    RuleTemplate(
        name="allow_own_bot_only",
        rule='bot.id == "puspa"',
        cost=(
            "Only allows actions from the bot id 'puspa'. Replace the id with "
            "the deployment's real bot id; every other bot is refused, "
            "including helpers and subagents, until they are listed."
        ),
        description="Allow only one bot id (allow rule).",
    ),
)


def list_templates() -> list[RuleTemplate]:
    return list(BUILTIN_TEMPLATES)


def describe_template(name: str) -> RuleTemplate:
    """Return a template (with its cost) to show before adding it."""
    for template in BUILTIN_TEMPLATES:
        if template.name == name:
            return template
    raise KeyError(f"unknown rule template: {name!r}")


def template_cost(name: str) -> str:
    return describe_template(name).cost


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatewayDecision:
    """One gateway verdict.

    ``outcome`` is what the policy says ("allow" | "deny");
    ``allowed`` is what actually happens. In dry-run mode the two
    differ on purpose: the policy verdict is recorded, but the
    action is still forwarded (except the cloud metadata block,
    which refuses in every mode).
    """

    allowed: bool
    outcome: str  # "allow" | "deny"
    rule: str | None  # matched rule source, or None
    reason: str
    dry_run: bool = False
    forwarded: bool = False


class Gateway:
    """The single action boundary for computer actions.

    ``audit`` is a callable taking one audit record dict (see
    :func:`make_ledger_gateway_sink` for the ledger adapter).
    ``snapshot`` supplies the policy when given; otherwise the
    policy passed to the constructor is used.
    """

    def __init__(
        self,
        policy: Policy | dict | None = None,
        *,
        snapshot: PolicySnapshot | None = None,
        dry_run: bool = False,
        audit=None,
    ) -> None:
        self.snapshot = snapshot
        if isinstance(policy, Policy):
            self._policy = policy
        elif isinstance(policy, dict):
            self._policy = load_policy(policy)
        elif policy is None:
            self._policy = Policy()
        else:
            raise PolicyError(
                f"policy must be a Policy, a dict, or None, got {type(policy).__name__}"
            )
        self.dry_run = dry_run
        self.audit = audit

    @classmethod
    def start(cls, policy_data: dict | None, *, dry_run: bool = False, audit=None) -> Gateway:
        """Build the gateway at server startup. A malformed policy
        raises PolicyError here, halting startup."""
        return cls(load_policy(policy_data), dry_run=dry_run, audit=audit)

    # -- policy resolution ----------------------------------------------
    def policy_summary(self) -> dict:
        """Read-only operator view of the active policy (G31).

        Whether a policy is loaded, whether the snapshot went stale
        (fail-closed: a stale or never-loaded snapshot refuses
        everything), the dry-run flag, and the deny/allow rule
        sources. A snapshot that was never loaded reports no rules:
        the panel shows "no policy loaded" rather than inventing
        one.
        """
        if self.snapshot is not None:
            policy = self.snapshot.policy
            return {
                "loaded": self.snapshot.loaded,
                "stale": self.snapshot.stale,
                "dry_run": self.dry_run,
                "deny_rules": [rule.source for rule in policy.deny] if policy else [],
                "allow_rules": [rule.source for rule in policy.allow] if policy else [],
            }
        return {
            "loaded": True,
            "stale": False,
            "dry_run": self.dry_run,
            "deny_rules": [rule.source for rule in self._policy.deny],
            "allow_rules": [rule.source for rule in self._policy.allow],
        }

    def _current_policy(self) -> Policy | None:
        if self.snapshot is not None:
            if not self.snapshot.loaded:
                return None
            if self.snapshot.stale:
                return None
            return self.snapshot.policy
        return self._policy

    # -- audit ----------------------------------------------------------
    def _audit(self, record: dict) -> None:
        if self.audit is None:
            return
        try:
            self.audit(record)
        except TypeError:
            self.audit({"type": record.get("event", "gateway"), "content": record})

    def _audit_decision(self, decision: GatewayDecision, nested: dict) -> None:
        tool = (nested.get("tool") or {}).get("name", "")
        actor = (nested.get("actor") or {}).get("id", "")
        host = (nested.get("page") or {}).get("host", "")
        self._audit(
            {
                "event": "gateway_decision",
                "allowed": decision.allowed,
                "outcome": decision.outcome,
                "rule": decision.rule,
                "reason": decision.reason,
                "dry_run": decision.dry_run,
                "forwarded": decision.forwarded,
                "tool": tool,
                "actor": actor,
                "page_host": host,
            }
        )

    @staticmethod
    def _normalize_context(context: ActionContext | dict) -> dict:
        """Action context as the nested dict the evaluator walks.

        An ``ActionContext`` always carries every attribute (empty
        string when the action has none). A raw nested dict may omit
        attributes: a rule that reads one then fails to evaluate,
        which the gateway treats as fail-closed. A flat dict
        (``{"tool_name": ...}``) is converted through ActionContext.
        """
        if isinstance(context, ActionContext):
            return context.to_dict()
        if isinstance(context, dict):
            if any(isinstance(v, dict) for v in context.values()):
                return context
            fields = ActionContext.__dataclass_fields__
            return ActionContext(**{k: v for k, v in context.items() if k in fields}).to_dict()
        raise PolicyError(
            f"action context must be ActionContext or dict, got {type(context).__name__}"
        )

    @staticmethod
    def _nested_str(nested: dict, *path: str) -> str:
        node: Any = nested
        for part in path:
            if not isinstance(node, dict):
                return ""
            node = node.get(part, "")
        return node if isinstance(node, str) else ""

    # -- decision -------------------------------------------------------
    def decide(self, context: ActionContext | dict) -> GatewayDecision:
        """Decide one action. Deny rules run before allow rules."""
        nested = self._normalize_context(context)
        policy = self._current_policy()
        if policy is None:
            decision = GatewayDecision(
                allowed=False,
                outcome="deny",
                rule=None,
                reason="policy snapshot never loaded or stale: refusing everything",
            )
            self._audit_decision(decision, nested)
            return decision
        if self.snapshot is not None and self.snapshot.resolve_target(nested) is None:
            decision = GatewayDecision(
                allowed=False,
                outcome="deny",
                rule=None,
                reason="action target is not in the server snapshot",
            )
            self._audit_decision(decision, nested)
            return decision
        # Cloud metadata address: refused in every mode, dry-run included.
        host = self._nested_str(nested, "page", "host").lower()
        url = self._nested_str(nested, "page", "url").lower()
        if host in METADATA_HOSTS or any(meta in url for meta in METADATA_HOSTS):
            decision = GatewayDecision(
                allowed=False,
                outcome="deny",
                rule=None,
                reason="cloud metadata address is blocked in all modes",
            )
            self._audit_decision(decision, nested)
            return decision
        if policy.is_empty:
            decision = GatewayDecision(
                allowed=False,
                outcome="deny",
                rule=None,
                reason="no policy loaded: refusing everything",
            )
            self._audit_decision(decision, nested)
            return decision
        # Deny first. A deny rule that fails to evaluate denies.
        for rule in policy.deny:
            try:
                matched = evaluate_rule(rule.ast, nested)
            except RuleEvaluationError as exc:
                return self._deny_or_dry_run(
                    nested,
                    rule=rule.source,
                    reason=f"deny rule failed to evaluate, failing closed: {exc}",
                )
            if matched:
                return self._deny_or_dry_run(
                    nested,
                    rule=rule.source,
                    reason=f"deny rule matched: {rule.source}",
                )
        # Then allow. An allow rule that fails to evaluate does not allow.
        for rule in policy.allow:
            try:
                matched = evaluate_rule(rule.ast, nested)
            except RuleEvaluationError:
                continue
            if matched:
                decision = GatewayDecision(
                    allowed=True,
                    outcome="allow",
                    rule=rule.source,
                    reason=f"allow rule matched: {rule.source}",
                    dry_run=self.dry_run,
                    forwarded=True,
                )
                self._audit_decision(decision, nested)
                return decision
        return self._deny_or_dry_run(nested, rule=None, reason="no allow rule matched")

    def _deny_or_dry_run(self, nested: dict, *, rule: str | None, reason: str) -> GatewayDecision:
        """Build a deny decision, honoring dry-run: in dry-run the
        policy verdict ("deny") is recorded but the action is still
        forwarded, so the decision is observable without enforcing
        it. The metadata block never reaches this helper."""
        if self.dry_run:
            decision = GatewayDecision(
                allowed=True,
                outcome="deny",
                rule=rule,
                reason=f"dry-run: would deny ({reason}), forwarding anyway",
                dry_run=True,
                forwarded=True,
            )
        else:
            decision = GatewayDecision(
                allowed=False,
                outcome="deny",
                rule=rule,
                reason=reason,
            )
        self._audit_decision(decision, nested)
        return decision

    # -- execution (the five steps) -------------------------------------
    def execute(self, context: ActionContext | dict, computer_call, *args, **kwargs):
        """Run one computer action through the boundary:

        1. resolve the target from the snapshot (inside decide),
        2. evaluate the policy (decide),
        3. audit the decision (decide),
        4. call the computer only when the decision forwards,
        5. audit a second line when a forwarded action fails.

        Dry-run still forwards (but the decision record says so).
        Returns the computer call's result. Raises GatewayDenied
        when the decision refuses, and re-raises the computer
        call's own exception after auditing the failure.
        """
        decision = self.decide(context)
        if not decision.allowed:
            raise GatewayDenied(decision.reason)
        nested = self._normalize_context(context)
        tool = self._nested_str(nested, "tool", "name")
        actor = self._nested_str(nested, "actor", "id")
        try:
            result = computer_call(*args, **kwargs)
        except Exception as exc:
            self._audit(
                {
                    "event": "gateway_action_failed",
                    "allowed": True,
                    "rule": decision.rule,
                    "reason": f"forwarded action failed: {exc}",
                    "dry_run": self.dry_run,
                    "tool": tool,
                    "actor": actor,
                }
            )
            raise
        if isinstance(result, dict) and str(result.get("status", "")).lower() in (
            "error",
            "failed",
        ):
            self._audit(
                {
                    "event": "gateway_action_failed",
                    "allowed": True,
                    "rule": decision.rule,
                    "reason": f"forwarded action returned {result.get('status')}: {result.get('reason', '')}",
                    "dry_run": self.dry_run,
                    "tool": tool,
                    "actor": actor,
                }
            )
        return result


def make_ledger_gateway_sink(log, *, task_id: str, agent_id: str, initiator: dict | None = None):
    """Adapt a gateway audit callable to a ledger ``log`` callable
    (``log(event_type, content)``, the shape ``Gate`` and
    ``PuspaTool`` use). Decision lines become ``tool_called``
    events; forwarded-action failures become ``tool_completed``
    events with status "error". No new ledger event type is used.
    The initiator attribute (G6) rides in the content when given.
    """

    def _content(extra: dict) -> dict:
        content: dict[str, Any] = {"task_id": task_id, "agent_id": agent_id}
        content.update(extra)
        if initiator is not None:
            content["initiator"] = dict(initiator)
        return content

    def sink(record: dict) -> None:
        event = record.get("event", "")
        if event == "gateway_action_failed":
            log(
                "tool_completed",
                _content(
                    {
                        "tool": record.get("tool", ""),
                        "status": "error",
                        "gateway": "failed_after_forward",
                        "reason": record.get("reason", ""),
                    }
                ),
            )
        else:
            log(
                "tool_called",
                _content(
                    {
                        "tool": record.get("tool", ""),
                        "status": "forwarded" if record.get("allowed") else "rejected",
                        "gateway_rule": record.get("rule"),
                        "reason": record.get("reason", ""),
                        "dry_run": bool(record.get("dry_run")),
                    }
                ),
            )

    return sink
