# Sentinel — self-observability (G12)

Puspa watches itself: a periodic scanner over its own records
(the ledger + the learning loop's inputs), a decision log, usage
traces, anonymous analytics, a batch trajectory runner, and a
session-insights engine. The code lives in `puspa/sentinel/`.

Pattern sources (studied, rewritten as Puspa code, never copied
verbatim — see `NOTICE.md` and `REUSE.md`): DuskBot
`server/sentinel/` + `server/decision-log.ts` +
`server/usage-trace/` (PolyForm Noncommercial, study only) and
hermes-agent `hermes_logging.py` + `hermes_startup_watchdog.py` +
`agent/usage_*.py` + `agent/insights.py` + `batch_runner.py` +
`agent/trajectory.py` + `trajectory-format.md` (MIT).

## What the sentinel scans

The scanner reads ledger events through `Ledger.recent_events`
(a read-only window query, added in this item — no new ledger
event types) and runs five detectors (`puspa/sentinel/detectors.py`,
pure functions over event dicts):

| Detector | Fires when | Key |
| --- | --- | --- |
| `tool_error` | a tool fails `tool_error_min` (3) times in the lookback | `tool_error:<tool>` |
| `turn_stop` | a `task_stopped` event, or a task stalled (created, no terminal event, silent > `stalled_hours`, 24h) | `turn_stop:<task>` / `turn_stall:<task>` |
| `queue_wait` | a `trigger_missed` event, or a trigger whose task started later than `queue_wait_minutes` (5m) after `trigger_fired`, or never started | `queue_wait:<trigger>` |
| `cost` | one task's summed `content.cost_rp` reaches `cost_usd` ($5) in the lookback | `cost:<task>` |
| `retry_loop` | the same tool called `retry_min` (3) times in one task with no success in between | `retry_loop:<task>:<tool>` |

Defaults live in `puspa/sentinel/thresholds.py`
(`SentinelThresholds`): queue 5 minutes, cost $5, retry min. 3,
tool-error min. 3, lookback 7 days, rescan 30 minutes, stall
24 hours. `idr_per_usd` (default 16,000) converts the Rupiah
costs the ledger records into the dollars the cost threshold
uses — it is a configuration value, not a market rate.

Notes on shapes the detectors rely on (all already in the repo):
`tool_completed` content is `{"tool", "status": "ok"|"error"|...}`
(`puspa/chassis/tools.py`); `trigger_fired` / `trigger_missed`
content carries `trigger_id` + `task_id`
(`puspa/scheduler/scheduler.py`); `task_stopped` with
`content.deleted == True` is a delete tombstone
(`docs/contracts.md`), not a stopped turn, and is excluded.

## Scan flow and the independent verifier (K8)

`puspa/sentinel/scanner.py` (`Sentinel.scan`):

1. Rescan gate: a scan sooner than `rescan_minutes` after
   `state["last_scan"]` is a no-op (`status: "skipped"`) unless
   forced.
2. Detect: all five detectors over the lookback window.
3. Verify (K8): each candidate goes through the independent
   verifier (`puspa/sentinel/verifier.py`), which re-reads fresh
   events and re-runs the single detector for that issue's scope.
   The verifier module is separate from the detector module so
   the two can evolve independently. Verdicts: `confirmed`
   (condition still present), `stale` (gone), `duplicate`
   (already acknowledged/resolved in state), `unverifiable`
   (anything failed — fail-closed, never confirmed).
4. Merge: confirmed issues are upserted into the state file
   (`puspa/sentinel/state.py`); an open issue whose condition is
   now stale is auto-resolved (`resolution: "auto: condition
   cleared"`); acknowledged issues are never auto-resolved.
5. Prune + write: `prune_seen` drops open issues unseen for
   longer than the lookback and resolved issues older than twice
   it; acknowledged issues are never pruned. Writes are atomic
   (temp file + `os.replace`).

State lives in `sentinel-state.json` under the sentinel home
(`PUSPA_SENTINEL_HOME` or `~/.puspa/sentinel`), with a
human-readable `sentinel-state-issues.json` snapshot of open
issues next to it.

## Scheduling

`puspa/sentinel/scanner.py::run_sentinel_scan` is the scheduler
entry point: `schedule_sentinel_scan(user_id, hour=3)` registers
a daily `sentinel_scan` trigger and `run_scheduled_job`
dispatches it (same pattern as `trust_root_refresh` / `dream`).
The job never raises — failures return
`{"job": "sentinel_scan", "status": "error", ...}`.

Honest limit: the scheduler moves a trigger one day forward on
each run (`run_trigger`), so the trigger gives a daily scan at
most. The 30-minute rescan interval is enforced inside the
scanner state. A 30-minute cadence needs a caller that invokes
`run_sentinel_scan` every 30 minutes — for example a G8 cron job
(`every 30m`) — not this daily trigger.

## Decision log

`puspa/sentinel/decision_log.py` (`DailyDecisionLog`): every
decision is recorded — actor, source, via — into daily files
`decisions-YYYY-MM-DD.jsonl` (one JSON object per line, with an
`entry_hash`: SHA-256 of the canonical JSON of
`{actor, source, via, decision, details}`, timestamp excluded so
the same decision always hashes the same). Retention 180 days
by default (`prune` deletes older daily files; nothing else in
the directory is touched).

This is the durable, file-based counterpart of G9's in-memory
`puspa.decider.log.DecisionLog` — it does not write to the
ledger, so it needs no new ledger event type.

## Usage traces

`puspa/sentinel/traces.py` (`trace_from_events`): one task's
ledger events become a span tree — a task span, tool spans
(`tool_called` paired with the next `tool_completed` for the
same tool), message spans as leaves. Span ids reuse ledger event
ids, so a span always traces back to the hash-chained record.
An unpaired call is still a span with `ended_at: None` (visible
as unfinished, not dropped). `Trace.to_json()` / `flatten()` for
export.

## Anonymous analytics + crash reports

`puspa/sentinel/analytics.py` (`Analytics`):

- Off means off: `enabled=False` or env
  `PUSPA_ANALYTICS_ENABLED=0/false/no/off` makes every method a
  no-op (default is enabled; the switch is the env var plus
  runtime `enable()` / `disable()`).
- Anonymous by construction: counters are keyed by a fixed
  snake_case event name only — anything else raises, so no user
  id, task id, or free text can become a counter key. Only
  totals are recorded.
- Crash reports carry the exception type, its module, a
  fingerprint (SHA-256 of `module:type`), and the time. The
  exception message is never included (it may contain user text
  or secrets).
- No network, ever: there is no endpoint and no sender.
  `flush()` writes local JSONL (or hands the payload to an
  injected `sink`). A remote endpoint, if ever wanted, is a
  separate decision with its own review.

## Batch trajectory runner

`puspa/sentinel/trajectory.py` (`BatchTrajectoryRunner`): runs
tasks through the existing eval harness
(`puspa/evidence/eval_harness.py`) and records one trajectory
per task. `trajectories.jsonl` is the checkpoint — each finished
task is appended, so a killed run resumes with `resume=True`
instead of starting over. `record_prompt_run` records one agent
run over a bare prompt (no cassette) from the agent's own
`{"messages", "tool_calls", "final_state", "passed", "score"}`
result. `aggregate_tool_usage` totals tool calls per tool.
Redaction before saving: step content passes through G7's
`puspa.memory.trust.redact_secrets`; the raw trajectory is never
written to disk. The format is documented in
`docs/trajectory-format.md` (JSON + ShareGPT export).

## Session insights

`puspa/sentinel/insights.py`: tokens, cost estimate, tool/skill
usage, activity, model/platform breakdown — from ledger events
(`insights_from_events`) or from a G26 session database
(`insights_from_state_db`, read-only SQLite `mode=ro`).

- Tokens reuse `puspa.router.compression.count_tokens` (the G19
  counter); cost uses the router catalog (cheapest reader model)
  or the `cost_rp` the ledger already recorded.
- Sub-cent amounts are labelled at 4 decimals (`$0.0023`),
  never `~$0.00`; $0.01 and above use 2 decimals. The estimate
  is always labelled with its model and counter.
- Nothing is invented: a message with no text contributes no
  tokens, an event with no cost contributes no cost, and the
  state.db table stores no tool name, so tool-role messages are
  counted as `"<tool messages>"` rather than attributed to a
  tool.

## Verification discipline

The sentinel's own checks follow `docs/verification.md` (see the
"Sentinel self-checks" section there): isolated fixtures,
synthetic events, never verify a mutation against live user
data. The sentinel only reads the ledger; its writes go to its
own home directory.

## Honest limits

- The scan is only as complete as the ledger: events outside
  the 7-day lookback are invisible, and a task whose events
  never reached the ledger cannot be seen at all.
- `run_sentinel_scan` reads the whole ledger window for one
  user id argument that is only recorded in the report — the
  ledger has no per-user partition (see `puspa/ledger/ledger.py`),
  so per-user scoping is a follow-up, not a claim.
- The 30-minute cadence is not scheduled yet: the daily trigger
  is registered, but a 30-minute caller (G8 cron job) still
  needs wiring.
- The scanner is not wired to any alerting channel: issues sit
  in the state file + snapshot until something reads them. The
  dashboard/notification call site is a follow-up.
- Insights cost is an estimate from the router catalog (or
  recorded `cost_rp` when present); it is not a bill. The
  `idr_per_usd` rate is config, not a market rate.
- Trajectories from harness runs record cassette replays
  (deterministic), not live agent runs; the live-agent path is
  `record_prompt_run` with an injected agent result.
