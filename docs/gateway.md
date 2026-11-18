# Fail-closed gateway with CEL-style rules (G5)

The gateway is the single action boundary for computer actions
(browser / desktop control). The code lives in
`puspa/policy/gateway.py` (the boundary, the snapshot, the
templates) and `puspa/policy/rules.py` (the rule language).

Pattern source (studied, rewritten as Puspa code from the
documentation, never copied verbatim — see `NOTICE.md` and
`REUSE.md`): OpenBot `docs/architecture.md` +
`app/src/lib/boundaries/rules.ts` (MIT). Warning, carried over from
the comparison report: the enforcing CEL code is **not** in the
OpenBot repo (its enforcement server image is closed), so the
pattern below is from documentation, not verified code — and this
evaluator is Puspa's own, written from the documented semantics.

## The five steps

`Gateway.execute(context, computer_call, ...)`:

1. Resolve the target from the server snapshot.
2. Evaluate the action policy as it stands right now.
3. Write an audit line for that decision.
4. Call the computer only when the decision forwards.
5. Write a second audit line when a forwarded action fails (an
   exception, or a result dict with status `error` / `failed`).

`decide(context)` performs steps 1–3 without calling anything.

## The rule language

A small, safe expression language in `puspa/policy/rules.py` —
evaluated by a tokenizer + recursive-descent parser + evaluator,
never `eval()`:

```
expr       := or_expr
or_expr    := and_expr ("or" and_expr)*
and_expr   := not_expr ("and" not_expr)*
not_expr   := "not" not_expr | primary
primary    := "(" expr ")" | func | comparison | literal
func       := "contains" "(" attr "," string ")"
            | "matches" "(" attr "," string ")"
comparison := attr ("==" | "!=") string
literal    := "true" | "false"
```

`contains()` and `matches()` are case-insensitive. `matches()`
compiles its regex at load time, so a bad regex is a load error,
not a runtime surprise. Attributes a rule may inspect (OpenBot's
documented list):

`tool.name`, `intent`, `bot.id`, `actor.id`, `page.url`,
`page.host`, `element.ref`, `element.role`, `element.name`,
`element.type`, `key`, `command`, `file.path`, `file.name`,
`file.extension`, `mcp.server`, `mcp.tool`, `mcp.effect`,
`initiator.kind`, `initiator.id`.

An unknown attribute is a load error (`PolicyError`). A rule that
reads an attribute the action does not carry fails to evaluate
(`RuleEvaluationError`) — see fail-closed below.

`ActionContext` carries every attribute (empty string when the
action has none). A raw nested dict may omit attributes; that is
how the "broken rule" cases arise and are tested.

## Deny before allow, fail-closed

- **Deny is evaluated before allow.** The first matching deny rule
  decides; allow rules run only when no deny rule matched.
- **Missing or empty policy → nothing is allowed.** `Gateway`
  with no policy, or an empty policy dict, denies everything.
- **A deny rule that fails to evaluate → deny.** The decision
  records "failing closed".
- **An allow rule that fails to evaluate → it does not allow.**
  Evaluation continues with the next allow rule; if none matches,
  the decision is "no allow rule matched".
- **A malformed policy halts startup.** `load_policy` and
  `Gateway.start` raise `PolicyError` at load time: not a dict,
  deny/allow not lists of strings, an unparseable rule, an unknown
  attribute, a bad regex, or an unknown section. There is a test
  for the startup halt.
- **A snapshot that was never loaded → everything is refused.**
  Same for a stale snapshot (a refresh failed).
- **Explicit default at startup:** `deny: []`, `allow: ["true"]`
  — written out in `DEFAULT_POLICY_DATA`, never implicit.

## Dry-run

Dry-run mode decides and records every action but still forwards:
`GatewayDecision.outcome` is the policy verdict (`allow` /
`deny`), `allowed` is what actually happens — in dry-run they
differ on purpose. The audit line records the verdict and
`dry_run: true`. One exception: the cloud metadata block refuses
in every mode, dry-run included (below).

## Snapshot freshness

`PolicySnapshot` holds the current compiled policy and, optionally,
the set of targets the computer may act on. It refreshes through an
injected `refresh_hook` (a callable returning a policy dict — for
example reading a file or a table the deployer owns). A failed
refresh (malformed policy) marks the snapshot stale and the gateway
refuses everything until a refresh succeeds.

Postgres `LISTEN`/`NOTIFY` is the wake-up signal:
`PostgresPolicyWatcher` listens on the `puspa_policy_changed`
channel and `wait_for_change(timeout)` reports when a notification
arrived. It only signals *that* the policy changed; how the new
policy is fetched stays with the injected loader, so the watcher
never depends on a policy table Puspa does not own. The connection
factory is injectable for tests (a fake connection is used; no live
Postgres is needed).

Target resolution: when the snapshot carries no target table,
resolution is a no-op (the target is the action itself). When it
does, the target must be listed or the action is refused.

## Built-in rule templates (each carries its cost)

`list_templates()` / `describe_template(name)` /
`template_cost(name)` — the cost is shown **before** the rule is
added:

| Template | Rule | Cost |
| --- | --- | --- |
| `never_submit_form` | `not (key == "Enter" or key == "Space" or element.type == "submit")` | Also blocks pressing Enter and Space anywhere, including plain text fields and chat boxes — not only form submits. |
| `block_host_substring` | `not contains(page.host, "x.com")` | `contains()` is a substring match: this also blocks dropbox.com and netflix.com, which both contain `x.com`. Prefer `matches(page.host, "(^\|\\.)x\\.com$")`. |
| `block_payment_tools` | `not contains(tool.name, "pay")` | Catches pay, payment and payroll tools, but misses money-moving tools named transfer, order or subscription. Pair with the G4 payment taxonomy. |
| `deny_credential_changes` | `not (contains(tool.name, "password") or contains(tool.name, "credential"))` | Misses credential changes named `rotate secret`, `update login` or `change api key`. The G4 taxonomy is the backstop, not this rule. |
| `allow_own_bot_only` | `bot.id == "puspa"` | Only allows actions from the bot id `puspa`; replace the id with the deployment's real bot id. |

Every template rule parses (tested). Templates are documentation +
data, not installed rules: the caller adds a template's rule to a
policy explicitly after reading its cost.

## Audit wiring

`Gateway` takes an `audit` callable receiving one record dict per
decision / failure. `make_ledger_gateway_sink(log, task_id,
agent_id, initiator=None)` adapts it to a ledger `log` callable
(`log(event_type, content)`, the shape `Gate` and `PuspaTool`
use): decision lines become `tool_called` events, forwarded-action
failures become `tool_completed` events with status `error`. No new
ledger event type is used. The initiator attribute (G6) rides in
the content when given.

## Acknowledged limits (also OpenBot's)

- **Egress filtering does not stop raw sockets.** That is a
  Kubernetes NetworkPolicy job, not this gateway's. The gateway
  only sees actions that come through it; a raw socket opened
  behind its back is outside its view.
- **Cloud metadata address refused in all modes:**
  `169.254.169.254` and `metadata.google.internal` (host or URL)
  are denied even in dry-run. Other link-local or internal
  addresses are not special-cased — a policy must block them if
  the deployment needs it.
- **No live computer is wired yet.** The gateway guards the
  `computer_call` the caller injects. Puspa has no
  browser/desktop-driving call site today, so the gateway is the
  boundary waiting for that seam (the ledger sink and the decision
  audit are wired; the computer call itself is the caller's). A
  caller that drives the computer directly bypasses the gateway.
- **The rule language is deliberately small.** No numbers, no
  arithmetic, no lists, no `in` operator, no string functions
  beyond `contains` / `matches`. Anything richer belongs in a
  policy reload, not in a rule.
- **`ActionContext` defaults hide missing attributes.** A rule
  that reads an attribute an `ActionContext` does not carry sees
  an empty string, not an error — the fail-closed "broken rule"
  path only fires for raw dict contexts that omit the key.
- **Snapshot refresh is pull-based.** Nothing pushes a policy
  change into the gateway except the refresh hook / watcher the
  deployer wires; an unwired gateway keeps deciding on the policy
  it started with.
