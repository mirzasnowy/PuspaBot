# Provider pool + quota-aware failover (V2-40)

One provider, several accounts. The pool sits in the router
(`puspa/router/pool.py`) between the model choice and the provider
call: when the account that would serve a request is spent, limited,
or failing, the request moves to the next account of the same
provider instead of failing — and the user sees each account's
quota as a bar in Settings, next to the model choice.

## Where the mechanism comes from

Studied from azrinaldi/gemini-stealth-bridge (MIT, Go program, 159
stars, verified 22 Nov 2026): multi-account pooling with `rotate` /
`lru` / `fill-first` strategies, 5-hour and 7-day quota tracking,
a quota preflight that skips spent accounts, failover on transport
or quota errors, per-account rate limits, and a web control plane
with live quota bars. Only the mechanism was taken, rewritten as
Puspa Python code — never verbatim, never a port line by line.

NOT taken, on purpose: gsb authenticates with Google session
cookies over a browser-mimicking uTLS transport against unofficial
Gemini Web endpoints. That breaks Google's platform rules and is out
of scope. This pool serves **official providers only**: a BYOK API
key stored in the vault, or an OAuth subscription login refreshed by
`puspa/router/oauth.py`. There is no cookie code and no uTLS code
anywhere in the implementation.

## Concepts

- **PoolAccount** — one credential of one provider. `account_id` is
  stable (`"<provider>:<slot>"`: `openrouter:1`, `openrouter:2`,
  `openrouter:oauth`). `kind` is `byok` or `oauth`. `label` is the
  display name for Settings. `auth_ref` is how the credential is
  resolved at call time: the vault entry name for BYOK
  (`byok:<provider>`, `byok:<provider>:2`, …), the provider name for
  OAuth. **No credential value is ever stored in a pool account**:
  the key/token is resolved at call time only, by
  `resolve_credential`, and never logged, returned, or snapshotted.
- **ProviderPool** — several accounts for ONE provider, one
  strategy, one shared `QuotaStore`, per-account rate limits.
- **Quota windows** — each account gets two windows in the same
  `QuotaStore` the tiered fallback uses: `<account>:5h` (18.000
  seconds) and `<account>:weekly` (604.800 seconds), the same two
  windows gsb tracks. Limits must come from the provider's actual
  plan via `register_quota`; an account with no registered quota
  counts as available (unknown is not exhaustion).
- **Preflight** — before every pick, an account must pass: not
  disabled, not cooling after a failure, no window exhausted, not
  over its per-account rate limit. A failed preflight is a skip
  with a reason, never a silent drop.
- **Failover** — `call_with_pool(pool, call_fn)` tries accounts in
  strategy order. Only retryable failures fall through: 429, quota
  exhaustion, 5xx, auth errors, transport errors. A deterministic
  failure (any other 4xx) raises immediately. Every attempt and
  every hop is recorded; when all accounts fail, `AllAccountsFailed`
  names each account and its reason.

## Strategies

| Strategy | Behaviour |
| --- | --- |
| `rotate` (default) | A sticky cursor. Consecutive picks stay on the same account while it can serve; the cursor only moves when the current account cannot. On a failure the next pick starts after the failed account. |
| `lru` | The ready account with the fewest failures, then the least recently used, then pool order. |
| `fill-first` | The first ready account in pool order; an account that just failed is demoted (cooling for 60 seconds by default) so the next pick skips it. |

A quota failure also marks the account's quota exhausted in the
store and starts an escalating cooldown (60s, 5m, 15m, 1h per
strike); a transport failure cools the account for one
fail-cooldown (60 seconds). A success resets the failure and strike
counters.

## Rate limits per account

`max_rpm` caps requests per minute per account (sliding 60-second
window) and `max_concurrent` caps simultaneous in-flight calls per
account. Both are 0 (unlimited) unless set. A rate-limited account
is skipped by preflight, never called. Selection alone (`select()`)
records no usage and consumes no RPM budget; admission happens in
`call_with_pool` just before the call.

## Discovery: where pool accounts come from

`discover_accounts(provider, user_id)` probes, in pool order:

1. the primary BYOK key (vault `byok:<provider>`) → slot 1;
2. numbered extra BYOK keys (`byok:<provider>:2` …
   `byok:<provider>:6`) → slots 2..6;
3. an OAuth subscription login (vault `byok:oauth:<provider>`,
   via `VaultTokenStore`) → the `oauth` slot.

Only existence is probed; no key or token value is read. A user
with no credential for the provider gets no pool (`None`), never an
invented account. `provider_pool_for_user` caches the pool per
(user, provider, strategy) in the process, so the quota state
Settings reads is the same state the router sees; `pool_quota_bars`
returns the Settings data (`{"provider", "strategy", "accounts"}`).

## Quota bars in Settings

`GET /api/model-settings/pool-quota?provider=openrouter` returns one
dict per account (OpenAPI: `PoolQuotaResponse` in
`docs/openapi-pwa.yaml`): label, availability, skip reason, and per
window the used fraction, remaining tokens, and reset countdown.
The PWA Settings screen (`SettingsScreen.tsx`, BYOK section) fetches
it when the BYOK provider changes and renders one bar per account
per window (`.quota-bar` in `styles.css`); an untracked window
shows "belum terlacak (limit belum didaftarkan)". No key material is
ever in a response.

## How it composes with the tiered fallback

The tiered fallback (`puspa/router/fallback.py`) falls through
MODELS when a tier fails; the pool falls through ACCOUNTS of one
provider when an account fails. The two compose: a pool call is what
a tier's `call_fn` can do, so one provider's several keys share the
load before the chain moves to the next tier. The per-task budget
ceiling is enforced the same way in both: a charge that would exceed
it raises `BudgetExhausted` and no other account (or tier) is tried
to dodge it.

## Live wiring (turn loop)

The pool is called from a live path, not only from Settings: the
turn loop's routed adapter
(`puspa/turns/turn_api.py::make_routed_model_call`, the adapter the
loop builds when it knows the user) routes a subscription-tier
call through the pool when the routed provider has at least two
accounts. Each attempt resolves its own account's credential at
call time (vault BYOK key or OAuth token) and calls the router's
live `complete_messages` with it, so a 429 on one key fails over
to the next account of the same provider. A non-retryable failure
(400) raises immediately without trying the next account, and
when every account fails the tier raises a retryable failure so
the chain falls through to the cheap/free safety net. With one
account the pool is never built and the direct call stays
bit-identical; a pool-build failure also falls back to the direct
call, so the pool can never break a completion that would have
worked. The scheduler does not call the pool: a scheduled job has
no user, so there is no per-user vault to discover accounts from.
Proven by `tests/test_provider_pool_wiring.py` (5 tests, test DB,
mock transport).

## Honest limits

- Quota state is in-memory (pool registry + `QuotaStore`) and does
  not survive a process restart; usage is not yet written to the
  ledger. Persisting quota snapshots to Postgres is a follow-up.
- The scheduler does not call the pool (no user on a scheduled
  job); the turn loop does, through the routed adapter above.
- Quota limits must be registered from the provider's actual plan.
  Until then an account is untracked and treated as available.
- The frontend types were added by hand to
  `apps/pwa/src/api/generated-types.ts` because `npm run generate`
  cannot run in this tree (pre-existing `openapi-voice.yaml` parse
  failure); re-run the generator when the specs are back.

## Tests

- `tests/test_provider_pool.py` (30 tests, in-memory, fake clock):
  rotation, quota failover, preflight in both windows, strategies,
  rate limits, snapshot bars, budget, validation.
- `tests/test_provider_pool_settings.py` (14 tests, test DB):
  discovery, credential resolution, quota bars, endpoint.
- `tests/test_provider_pool_wiring.py` (5 tests, test DB, mock
  transport): the turn loop's routed adapter fails over between
  two keys on a 429, never touches the pool with one key, never
  retries a 400, and falls through to the next tier when every
  account fails.
- `apps/pwa/src/api/modelSettings.test.ts` (2 new tests):
  `getPoolQuota` fetches the right URL and parses bars.
