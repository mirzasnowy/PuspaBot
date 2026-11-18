# Multi-tenant hosting auth, pairing, link, and relay (G11)

Date: 18 Nov 2026. Package: `puspa/hosting/`. Pattern source: DuskBot
`server/request-auth.ts` + `server/card-seal.ts` + `server/sessions.ts` +
`apps/link-service/` + `apps/cloudflare/link-relay/` (PolyForm
Noncommercial — study only; the patterns were rewritten as Puspa
Python code, never copied verbatim). No new dependencies: everything
runs on `cryptography==50.0.2` (already pinned) and the standard
library.

Google login (`puspa.auth`) stays in front. This package is the
server-to-server path behind it: how a second machine, a headless
server, or an app that cannot reach the server directly proves who it
is, and how the two sides talk when the network is hostile. It is the
auth layer for the multi-tenant hosting in the 30-day-mature
definition — it is not a user-facing login screen.

## The six mechanisms

### 1. Loopback trust (`loopback.py`)

`loopback_trusted(host, origin, headers)` is true only when the Host
header AND the Origin header (when present) resolve to a loopback
address (`localhost`, `*.localhost`, `127.0.0.0/8`, `::1`; ports and
IPv6 brackets stripped first), AND the request is not from a browser.
A browser never gets loopback trust, even from loopback: a real
browser always sends `Sec-Fetch-Mode` and a `Mozilla/` user agent,
and either marker denies trust. The browser check is a heuristic and
is documented as one in the module — a non-browser client spoofing a
browser user agent is denied too (fail-closed direction), and the
real boundary is the loopback interface plus the session/service
rules, never the user-agent string alone.

### 2. Service mode (`service_allow.py`, `gateway.py`)

`authorize_request(...)` is the single front door. Decision order:

1. Valid Google session → allow (`via="google_session"`).
2. Valid pairing session → allow (`via="pairing_session"`).
3. Loopback-trusted request:
   - mode `"single_user"` (one user, local machine): allow.
   - mode `"service"` (shared server, the default): allow ONLY when
     (method, path) is on the explicit `ServiceAllowlist`
     (`via="service_allow"`); anything else is denied.
4. Anything else → deny.

The default allowlist is three read-only endpoints (`GET /health`,
`GET /healthz`, `GET /version`). Matching is `re.fullmatch`, so
`/health` never also matches `/health/secret`. The Google session
validator is an injected callable with the contract
`(token) -> user_id` (raising on invalid); the wiring is one line:

```python
from puspa.accounts.user_sessions import validate_session
from puspa.hosting import authorize_request

decision = authorize_request(
    method, path, headers,
    host=headers.get("host"),
    origin=headers.get("origin"),
    session_token=token,
    mode="service",
    session_validator=lambda t: validate_session(t, dsn=dsn),
)
```

When no validator is wired, the Google-session step is skipped
(never assumed valid). A validator that raises is treated as "no
valid session", never as a crash.

### 3. Pairing (`pairing.py`)

`PairingStore` issues a 6-digit code (5 minutes, single-use) that the
user reads off the logged-in app and types into the device. A correct
code mints a session token valid for 30 days. Only SHA-256 hashes of
codes and tokens are stored — the same pattern as
`puspa.accounts.user_sessions`. Five wrong codes from one address
lock that address for 10 minutes; while locked, verification refuses
immediately without touching any code, and a successful verification
clears the failure count. The store is in-memory: codes and sessions
die with the process (see limits).

### 4. Card seal (`identity.py`, `seal.py`)

- `load_or_create_identity_key(path)` loads (or creates, mode
  `0600`) the server's long-term Ed25519 identity key. An existing
  file more open than `0600` is refused. The public key's SHA-256
  fingerprint is what the app pins at linking.
  `IDENTITY_KEY_BACKUP_EXCLUDED = True`: the file must never enter a
  backup. `puspa.backup` only dumps Postgres and never walks the home
  directory, so no current backup path can pick it up; the exclusion
  is recorded so any future file-based backup honours it. Losing the
  key orphans every pinned app (they must re-link).
- `SealingKey.generate()` mints the per-start X25519 sealing keypair.
  The private half lives only in process memory — the class offers no
  serialisation of it and `__repr__` never leaks key material.
- `seal_card(plaintext, *, sealing_key, identity_key)` HPKE-encrypts
  to the sealing public key and signs `sealing_public_key ||
  ciphertext` with the identity key. `unseal_card(...)` checks, in
  order: the card's identity fingerprint equals the pinned
  fingerprint → the identity signature verifies → the sealing key is
  this server's current one → only then the HPKE ciphertext opens.
  A relay cannot swap in its own sealing key: the signature would not
  verify against the pinned identity key.

HPKE status (honest, verified): HPKE **is** available in
`cryptography==50.0.2` — no X25519+HKDF+AES-GCM fallback was needed.
Suite: `DHKEM(X25519)` + `HKDF-SHA256` + `AES-256-GCM`, base mode.
One API quirk of this version is recorded in the module docstring:
`Suite.encrypt` returns a single combined blob (encapsulated key
concatenated with ciphertext) and `Suite.decrypt` takes that combined
blob; there is no separate AAD parameter in base mode (context
binding lives in the `info` string `puspa-card-seal/v1`), and the
`Suite` object exposes no `kem` attribute (keypairs come from
`X25519PrivateKey.generate()`). Plaintext is capped at 64 KiB; bulk
data uses relay frames.

### 5. Link and relay (`link.py`, `relay.py`)

- `LinkService.create_link(user_id, *, address)` mints a 7-character
  code from an alphabet without `l`, `o`, `0`, `1` (32 symbols),
  valid 10 minutes, single-use, plus a 32-byte relay key held only by
  app + server (in process memory, returned once on redeem, never
  logged or persisted). Creation is rate-limited: 30/hour per address
  + 2,000/hour globally (sliding windows; both limits are
  constructor parameters with those defaults).
- `seal_frame(key, plaintext, *, aad)` / `open_frame(key, frame, *,
  aad)` are AES-256-GCM envelopes: 12-byte random nonce ||
  ciphertext (16-byte tag appended). Optional `aad` binds a frame to
  its link or sequence so a frame cannot be replayed onto another
  link. Frames are capped at 1 MiB; a wrong key, a tampered byte, or
  a wrong `aad` fails the tag check and raises `RelayError`.
- `RelayHub` is the in-memory relay: per-code FIFO queues of opaque
  frames (64 per code; a full queue refuses the publish instead of
  dropping silently). The hub holds no keys and never opens a frame —
  the test suite proves a hub operator with only the frame bytes
  cannot read them.

### 6. Fallback (`fallback.py`)

`FALLBACK_ORDER = ("direct", "tunnel", "relay")`, fixed. `pick_route`
returns the first available route; `connect_with_fallback(probes)`
runs injected probe callables in order, records every attempt, and
returns the first success or raises `AllRoutesFailedError` carrying
all attempts. The probes are the seam: a real direct HTTPS probe, a
real Cloudflare tunnel client check, and a real relay reachability
check do not exist in this repo yet (see limits).

## What is NOT built here (honest limits)

- **No HTTP route.** `authorize_request` is a pure function; no
  FastAPI/HTTP handler calls it yet. The PWA (`puspa/pwa/app.py`)
  is the natural call site — it already validates Google sessions
  the same way. Wiring it in is a follow-up once the hosting HTTP
  surface exists.
- **No Postgres-backed pairing store.** `PairingStore` and
  `LinkService` are in-memory and process-local; codes, sessions,
  relay keys, and rate-limit windows die on restart. The durable
  home for sessions is the Postgres `sessions` table owned by
  `puspa.accounts` (Google login sessions already live there);
  moving pairing sessions into it needs its own schema migration.
  Follow-up: add the migration + a `PostgresPairingStore` with the
  same interface, then switch the call site.
- **No live tunnel or networked relay.** The Cloudflare tunnel
  client and the networked relay endpoint (HTTP/WebSocket in front
  of `RelayHub`) do not exist yet. `connect_with_fallback` takes
  injected probes precisely so the ordering and accounting are real
  and tested while the transports are still missing. Follow-up:
  implement the tunnel client + the relay endpoint, then measure a
  real direct → tunnel → relay failover.
- **No ledger events.** Pairing/link/relay activity does not emit
  ledger event types (the core ledger stays at 31 types, as in G3).
  If auditability is wanted, add event types through the ledger
  owner, not from here.
- **Browser detection is a heuristic.** See section 1: a client
  that sends neither browser marker is treated as non-browser. The
  loopback interface + session/service rules carry the real trust,
  not the header check.

## Tests

- `tests/test_hosting_loopback.py` — loopback parsing, origin,
  browser detection, allowlist, gateway decisions.
- `tests/test_hosting_pairing.py` — code issue/verify, single use,
  5-wrong lockout, 10-minute lock, 5-minute code TTL, 30-day session
  TTL, revoke, hash-only storage, purge.
- `tests/test_hosting_seal.py` — identity key file (0600, reuse,
  too-open refusal), seal/unseal round trip, wrong pin, wrong
  identity, tampered ciphertext, swapped sealing key, rotation,
  serialisation, size cap.
- `tests/test_hosting_link_relay.py` — link code alphabet/uniqueness,
  create/redeem/expiry, per-address + global rate limits, frame
  crypto (wrong key, tamper, wrong aad, oversize), hub FIFO + cap +
  operator-cannot-read, fallback order + attempts + all-failed.
