# Nightly live smoke lane (E-04, plan §4.4)

A separate scheduled CI lane — `.github/workflows/nightly-smoke.yml`,
once a day at 02:00 UTC — that probes the live providers with
low-capped keys and test accounts. It is not part of the per-push
CI lanes (`ci.yml`): it needs real keys, real accounts, and a
local Ollama server, none of which a push lane has.

## Probes

All probes live in `puspa/nightly/probes.py`; each returns a
`ProbeResult` (status, latency, cost, provider response shape).
The runner (`puspa/nightly/run.py`) appends one JSONL record per
probe to `nightly-smoke/runs.jsonl` and writes
`nightly-smoke/summary.md`.

| Probe | What it proves |
| --- | --- |
| `openrouter_turn` | One short turn through OpenRouter (router's cheapest reader, 32 output tokens max). |
| `openrouter_fallback` | Forced fallback: the first tier is called with a deliberately invalid key, so the provider must reject it with a 401 and the chain must move to the next tier. Passes only when the chain actually moved. |
| `ollama_turn` | Health check (`GET /api/tags`) before the first call, then one end-to-end turn through Ollama's OpenAI-compatible endpoint (`qwen2.5:0.5b`). A dead server fails with a clear message naming what to start, never a raw `CompletionError`. |
| `skill_discovery` | One skills.sh search plus one install into a temp dir, through the normal G3 gates (hash-checked, licence-checked). Runs only when `PUSPA_SKILL_DISCOVERY_ENABLED=1`; otherwise it skips. |
| `google_login` | Google login: with a fresh authorization code it runs the full callback path (exchange code, save session, fetch userinfo); without one it runs the refresh-only path and records that honestly. Either way it then refreshes the token once more — the token-expiry check (gate 15). |
| `telegram_roundtrip` | One message out (with an Approve/Reject inline keyboard) and one message in, via `getUpdates`. |
| `telegram_approval_button` | One approval via a button tap: a `callback_query` update is parsed and answered. Skips (does not fail) when no tap arrived in the poll window — the tap is a human action — but the skip is recorded in the time series. |

## Time series

Each record is one JSON line: `run_id`, `timestamp`, `probe`,
`status`, `latency_ms`, `cost_rp`, `provider`, `model`,
`response_shape`, `detail`, `error`. Latency, cost, and response
shape are stored, not just green/red, so a provider that gets
slower, pricier, or starts answering differently is visible.
`puspa/nightly/store.py` summarises the series per probe (pass
rate, latency p50/p95, total cost, whether the response shape
changed since the previous run).

History across nights: the workflow restores
`nightly-smoke/runs.jsonl` from the Actions cache before the run
and saves it back after, so each night appends to the series;
the per-run artifact (`nightly-smoke-<run id>`, 90 days) is the
backup if the cache is ever evicted.

## Secrets and setup checklist (names only)

A human with repo settings access creates these; no value ever
goes into the repo, the tests, or the time series.

| Secret / variable | Purpose |
| --- | --- |
| `OPENROUTER_NIGHTLY_KEY` | Low-capped OpenRouter key (set the spend cap in the OpenRouter dashboard; the lane itself only ever makes two short turns). |
| `PUSPA_GOOGLE_TEST_CLIENT_ID`, `PUSPA_GOOGLE_TEST_CLIENT_SECRET` | Google OAuth test client. |
| `PUSPA_GOOGLE_TEST_REFRESH_TOKEN` | Daily path for the Google leg. Note: a Google OAuth app in Testing mode issues refresh tokens that expire after 7 days — re-issue the test refresh token weekly. |
| `PUSPA_GOOGLE_TEST_AUTH_CODE`, `PUSPA_GOOGLE_TEST_CODE_VERIFIER` | Full callback path; a fresh authorization code each run (set these only on runs where the callback itself is being exercised). |
| `TELEGRAM_TEST_BOT_TOKEN`, `TELEGRAM_TEST_CHAT_ID` | Telegram test bot and the test chat id. |
| `PUSPA_SKILL_DISCOVERY_ENABLED` (variable, not secret) | `1` enables the skills leg. |

The Google test session (tokens) is saved to a 0600 file at
`PUSPA_NIGHTLY_SESSION_FILE` (runner temp dir in CI). It is
never uploaded as an artifact and never written into the time
series — only the email, subject, and expiry are recorded.

The Telegram legs need the operator to send one message to the
test bot (any text) and, for the approval leg, tap Approve on
the smoke message. Without an inbound message the roundtrip leg
fails loudly with instructions, by design: a smoke lane that
cannot see inbound traffic must fail, not silently skip.

## Running it locally

The probes take their HTTP transport as an argument, so the
harness is proven with fakes in `tests/test_nightly_smoke.py`
(no network, no keys). To run the real runner locally, set the
env vars above and run:

```sh
python -m puspa.nightly.run --only openrouter_turn,ollama_turn
```

`--only` takes a comma-separated probe list. Without secrets,
the Google and Telegram probes fail with a message naming the
missing secret — that is the intended behaviour, not a bug.

## Code map

- `puspa/nightly/probes.py` — the seven probes plus
  `ProbeResult` (whitelisted record fields) and `run_probes`
  (an exception in one probe becomes its failed result).
- `puspa/nightly/store.py` — the JSONL time-series store,
  `summarize` (p50/p95, cost totals, shape-change flag), and
  the Markdown summary writer.
- `puspa/nightly/run.py` — env-driven runner
  (`NightlyConfig.from_env`), exit 0 only when every required
  probe passed; the skills and Telegram-approval legs may skip.
- `puspa/channels/telegram.py` — the E-04 delta there:
  `fetch_updates` (raw `getUpdates`), `parse_callback_query`,
  `answer_callback_query`, and an optional `reply_markup` on
  `send_text`. The existing message path (`poll_once`,
  `parse_update`) is unchanged.
- `.github/workflows/nightly-smoke.yml` — the lane: Ollama in
  a container with `qwen2.5:0.5b` pulled, cache restore/save
  for the series, artifact upload for the night's records.

## What already existed vs what E-04 added

Already existed and is reused, not rebuilt: the fallback chain
and its executor (`puspa/router/fallback.py` — the forced
fallback leg calls `call_with_fallback` with a broken key on
tier one), the live completion path (`puspa/router/complete.py`
— OpenRouter turns and the Ollama OpenAI-compatible endpoint
via `DEFAULT_LOCAL_BASE_URL` / `LOCAL_DUMMY_KEY` from
`puspa/pwa/model_settings.py`), skills.sh search + install
(`puspa/marketplace/discovery.py` — the probe calls
`search_skills` and `install_discovered` with the normal G3
gates), the Google OAuth flow (`puspa/auth/google_oauth.py` —
`exchange_code`, `refresh_tokens`, `fetch_userinfo`), and the
Telegram adapter (`puspa/channels/telegram.py` — send, poll).

E-04 added: the `puspa/nightly/` package (probes, time-series
store, runner), the Telegram callback-query support above, the
nightly workflow, and this doc.

## Honest limits

- The lane has never run on GitHub yet. The workflow YAML is
  validated, every referenced command is checked, and the
  harness is proven with fakes — but the first live run happens
  in CI, after the secrets in the checklist exist. Until then,
  a missing secret fails its probe with a message naming it.
- The Google full-callback path needs a fresh authorization
  code per run; the daily path is refresh-only, and Testing-mode
  refresh tokens expire after 7 days (see the checklist).
- The Telegram approval leg needs a human tap in the poll
  window; a night without a tap records a skip, not a pass.
- The Ollama leg pulls `qwen2.5:0.5b` (small, CPU-friendly);
  first-pull time dominates the lane's runtime.
- Cost is measured only where the router catalog knows the
  rates (OpenRouter turns); the Ollama leg records no cost
  (local compute, not billed per token).
