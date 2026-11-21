# Serve Modes — One PWA Codebase, Two Ways to Serve It (PWA serve modes (local operator vs hosted))

Decision locked by Nero, 18 Nov 2026 ~23:00 WIB (see
`docs/modes-decision.md`): **one web codebase, two serve modes.**
The PWA (`apps/pwa/`, React 19 + Vite, served by `puspa/pwa/app.py`)
plays both roles. What changes is the serve mode and which operator
panels are shown, not the app. Never two separate frontends.

## The two modes

| Mode | Who | What the PWA shows |
| --- | --- | --- |
| `local` | The operator: a self-hoster running their own bot on their own machine | The full product screens **plus** operator panels on the Office screen: pairing status, gateway status, live log |
| `hosted` | The end user: Free self-host and Plus/Pro/Team cloud users | The product screens only. The operator panels are hidden: the operator surface of a hosted deployment lives in the (private, not-yet-built) cloud backend, not in this repo |

Hosted serving itself is cloud work, not in this repo: the hosted
backend (`puspa-cloud`) is private and not built yet. What PWA serve modes (local operator vs hosted)
builds is the mode switch and the local operator panels.

## Configuration

`puspa/pwa/serve_modes.py`, function `resolve_serve_mode`. Sources,
in precedence order:

1. Explicit argument, or the `PUSPA_SERVE_MODE` environment variable
   (`"local"` or `"hosted"`, case-insensitive, surrounding whitespace
   ignored).
2. JSON config file: the `config_path` argument, or the
   `PUSPA_SERVE_MODE_FILE` environment variable, holding
   `{"serve_mode": "local"}` (or `"hosted"`).
3. Default: `"local"`.

### Fail-closed decisions (locked in this ticket)

- **Unset means `local`.** The operator owns the machine, so the
  operator surface is the safe default: no operator panel is ever
  hidden from the machine's owner by accident, and nothing about
  the hosted product is ever shown by accident.
- **An explicitly configured unknown value is rejected.**
  `resolve_serve_mode` raises `ServeModeError`, and `create_app`
  lets it propagate, so the server refuses to start rather than
  serve the wrong surface. A missing config file, a malformed file,
  or a file without a `serve_mode` key is rejected the same way.
- **An unknown mode never shows operator panels.**
  `operator_panels_visible` returns `False` for anything that is
  not `"local"` — including a mode string no code path produced.

## Live state: what the panels read

No mocks, no constants. The panels read the real objects the app
owns (wired in `puspa/pwa/app.py`, inside the marked PWA serve modes (local operator vs hosted) blocks):

- **Pairing status** — `PairingStore.status_snapshot()` (new read
  method on `puspa/hosting/pairing.py`): counts of outstanding
  pairing codes and sessions, the sessions themselves (user id,
  device label, expiry), and locked-out addresses. Pairing codes
  and session tokens are never exposed: only their SHA-256 hashes
  are stored, and the snapshot does not even carry those.
- **Gateway status** — `Gateway.policy_summary()` (new read method
  on `puspa/policy/gateway.py`): whether a policy is loaded, whether
  the snapshot went stale (fail-closed: a stale or never-loaded
  snapshot refuses everything), the dry-run flag, and the deny/allow
  rule sources. The last decision comes from `GatewayDecisionLog`
  (`puspa/pwa/serve_modes.py`), a bounded in-memory log (last 50)
  wired as the gateway's `audit` callable. `last_decision` is null
  until the gateway decides its first action — that is honest, not
  a placeholder (see limits below).
- **Live log** — the app's own event log (`app.state.event`): user
  messages, task events, approvals, and gateway decisions (the
  wiring mirrors each gateway audit record into the event log).
  The endpoint returns the newest 30 events as one-line summaries.

## Endpoint: `GET /api/operator/status`

Login required (session cookie, same as every other API route).
Response (`OperatorStatus` in `docs/openapi-pwa.yaml`):

```json
{
  "mode": "local",
  "operator_panels": true,
  "pairing": {
    "active_codes": 0,
    "active_sessions": 1,
    "sessions": [{"user_id": "user-1", "device_label": "laptop", "expires_at": 1730000000}],
    "locked_addresses": 0,
    "locks": []
  },
  "gateway": {
    "policy": {"loaded": true, "stale": false, "dry_run": false, "deny_rules": [], "allow_rules": ["true"]},
    "dry_run": false,
    "decisions": 1,
    "last_decision": {"allowed": true, "outcome": "allow", "rule": "true", "reason": "allow rule matched", "forwarded": true}
  },
  "log": [{"type": "user_message", "created_at": 1730000000, "summary": "user_message: halo PuspaBot"}]
}
```

In hosted mode the same endpoint returns `{"mode": "hosted",
"operator_panels": false, "pairing": null, "gateway": null, "log":
[]}`. The Office screen hides the panels on the
`operator_panels` flag alone.

## Frontend

- `apps/pwa/src/api/client.ts`: `fetchOperatorStatus()` (GET
  `/api/operator/status`) plus the generated `OperatorStatus` /
  `PairingStatus` / `GatewayStatus` / `GatewayDecision` /
  `OperatorLogLine` types (regenerated from `openapi-pwa.yaml`
  with `npm run generate`).
- `apps/pwa/src/state/operator.ts`: pure helpers — panel
  visibility, one-line pairing/gateway summaries, last-decision
  label, `HH:MM:SS` clock formatting, log-line formatting.
- `apps/pwa/src/components/OperatorPanel.tsx`: the three panels
  (pairing, gateway, live log). Loading shows a loading note, a
  failed fetch shows the error — never a silent hide. Hosted mode
  renders nothing.
- `apps/pwa/src/screens/OfficeScreen.tsx`: fetches the status on
  mount and polls every 5 seconds (`OPERATOR_POLL_MS`). The
  existing SSE stream still carries the office projection; the
  operator panels poll because SSE carries no operator events.

## Tests

- Python: `tests/test_pwa_serve_modes.py` (28 tests, run with
  `--noconftest`, no database): mode resolution (env, file,
  precedence, default, invalid rejected), panel visibility,
  pairing snapshot (live code then live session, no codes/tokens
  leaked, expired code uncounted, lockout listed), decision log
  (last/recent/bounded), gateway policy summary + live allow/deny
  decisions, the endpoint via `TestClient` (401 without login,
  live pairing/gateway/log in local mode, panels hidden in hosted
  mode, invalid mode rejected at startup).
- TypeScript: `apps/pwa/src/state/operator.test.ts` (helpers),
  `apps/pwa/src/components/OperatorPanel.test.tsx` (renders from
  live-shaped state, empty state, locked address, loading, error,
  hosted renders nothing),
  `apps/pwa/src/api/client.test.ts` (`fetchOperatorStatus`),
  `apps/pwa/src/screens/OfficeScreen.test.tsx` (panel appears in
  local mode, hidden in hosted mode, error shown on fetch failure).

## Honest limits

- **The gateway panel is idle until an action flows through the
  gateway.** The PWA itself takes no computer actions, so in
  production the decision count stays 0 and "last decision" stays
  "none yet" until some action path calls `gateway.decide()`. The
  panel reports that honestly; wiring a real action path through
  the gateway is a follow-up, not something this ticket invents.
- **The pairing store is in-memory and process-local.** Sessions
  and codes die with the process (documented in
  `puspa/hosting/pairing.py`); moving pairing sessions into the
  Postgres `sessions` table is a follow-up with its own migration.
  The panel therefore shows the current process's pairing state,
  not a durable history.
- **The operator panels poll; they are not pushed.** The live log
  refreshes every 5 seconds via `GET /api/operator/status`. The
  SSE stream carries the office projection only. Folding operator
  events into the SSE stream is a follow-up.
- **Hosted mode hides the panels; it does not serve them.**
  Verifying hosted serving end to end needs the cloud backend,
  which is not in this repo.
