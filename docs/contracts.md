# SHARED CONTRACT — Puspa MVP Phase 0

Source of truth: `~/workspace/match-hunt/definisi-mvp-puspa-2026-11-17-revisi-7.md` (revision 7, approved by Nero 17 Nov 2026 ~17:42 WIB). This document is the contract between subagents so interfaces don't drift. If the contract conflicts with the definition, the definition wins; record the difference in docs/status-phase-0.md.

## Locations and tooling

- Build root: the repo root (previously `~/workspace/goals/hard-ai-engineering-portfolio-project/build/fase-0/`).
- Venv: `.venv` at the repo root (Python 3.12.3). Dependencies in `requirements.txt`: `psycopg[binary]`, `argon2-cffi`, `cryptography`, `pytest` + nanobot's declared dependencies (pydantic, pydantic-settings, loguru, pyyaml, json_repair, tiktoken, jinja2, regex, jsonschema, filelock, httpx, tzlocal, etc.) + nanobot itself as a git-pinned dependency (`nanobot-ai @ git+https://github.com/HKUDS/nanobot@e767174`, PyPI package name `nanobot-ai`, import still `nanobot`). The local clone `./nanobot` is read-only reference + `sys.path` fallback in `tests/conftest.py` if the directory actually exists.
- Run the tests from the repo root with `.venv/bin/python -m pytest tests -q`.
- `nanobot/` is a clone result, DO NOT modify it (ZERO lines of nanobot code changed in Phase 0). Remote: `origin` has been REMOVED (the mirzasnowy/nanobot fork is ignored, do not use it); what remains is `upstream` = https://github.com/HKUDS/nanobot as read-only reference. The Puspa repo is the STANDALONE repo **mirzasnowy/puspa-ai** (not a fork, not an org); the puspa-ai org is POSTPONED. Nero's no-fork rule applies to all reused code (see docs/status-phase-0.md).
- NEVER store secrets, tokens, passwords, or keys in files, logs, or chat. Keys are created at runtime / in tests. The vault master key comes only from env `PUSPA_VAULT_MASTER_KEY`.

## Package layout

```
repo root/
  nanobot/            # HKUDS/nanobot clone, do not modify
  puspa/
    __init__.py
    ledger/           # Ledger (2.3)
      __init__.py  schema.sql  ledger.py  errors.py
    policy/           # Permission tokens, skills, gate, spending (2.6)
      __init__.py  tokens.py  skill_signing.py  gate.py  spending.py
    accounts/         # Basic accounts (2.13) + RLS (2.15) + cross-user access (2.6)
      __init__.py  users.py  user_sessions.py  vault.py  rls.py  access.py
      schema.sql  schema_agent.sql
    quarantine/       # Quarantined reader + injection (2.6)
      __init__.py  reader.py  injection.py
    sandbox/          # Fail-closed sandbox (2.6)
      __init__.py  runner.py  allowlist.py
    chassis/          # Integration with nanobot as chassis (2.6)
      __init__.py  context.py  tools.py  registry.py  hooks.py
      skill_loader.py  agent_session.py  sandbox_tool.py  factory.py
  tests/
    conftest.py  test_ledger.py  test_ledger_event_types.py  test_policy.py
    test_gate.py  test_accounts.py  test_quarantine.py  test_sandbox.py
    test_attacks.py  test_integration.py  test_rls.py  test_vault.py
    test_access.py  test_byok_provider.py
  demo/
    byok_demo.py  byok_provider.py  openrouter_provider.py
    mock_provider_server.py  MATRIKS_BYOK.md  LAPORAN_DEMO.md  NOTES.md
    mock_logs/  data/
  infra/
    schema_core.sql
  requirements.txt  pytest.ini  README.md
  docs/contracts.md  docs/status-phase-0.md
  NOTICE.md  docs/nanobot-inventory.md
```

## Database (Postgres)

- Local Postgres server. Test DB `puspa_test`. DSN from env `PUSPA_TEST_DSN`, default `dbname=puspa_test user=root host=/var/run/postgresql` (peer auth, no password).
- Role `root` created with LOGIN; DB `puspa_test` owned by `root`.
- Every application table has a `user_id` column, except `users` (its PK is
  `id`, with no `user_id` column) and `events` (which uses `agent_id` +
  `task_id`; user isolation in `events` goes through `agent_id`, which is
  bound to the owning agent's `user_id` — record this mapping in ledger.py).

## Ledger — `puspa/ledger` (definition 2.3)

- Table `events`, append-only. Columns: `id UUID PK DEFAULT gen_random_uuid()`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `task_id TEXT NOT NULL`, `agent_id TEXT NOT NULL`, `subagent_id TEXT`, `type TEXT NOT NULL CHECK (type IN (...31 types...))`, `content JSONB NOT NULL DEFAULT '{}'`, `parent_id UUID REFERENCES events(id)`, `previous_hash TEXT NOT NULL`, `hash TEXT NOT NULL UNIQUE`. Indexes: `(task_id, id)`, `type`, `created_at`.
- Closed type set (31, revision 7: 22 old + 9 new): task_created, plan_created, subagent_created, handoff_proposed, handoff_accepted, handoff_handed_over, handoff_rejected, handoff_expired, heartbeat, tool_called, tool_completed, evidence_recorded, verification_completed, approval_requested, approval_granted, approval_rejected, user_message, agent_message, budget_exhausted, task_completed, task_failed, task_stopped, workflow_proposed, workflow_activated, workflow_paused, trigger_fired, trigger_missed, module_proposed, module_activated, false_report, data_consent.
- Type CHECK migration (idempotent, in `puspa/ledger/schema.sql`): `CREATE TABLE IF NOT EXISTS` does not change the constraint of a table that already exists (e.g. one created when the set still had 22 types), so the migration block at the end of the schema DROPs the old constraint and ADDs a new constraint with the revision 7 list of 31 types, under the same constraint name (`event_jenis_check`).
- DB permissions: the application role has only INSERT + SELECT on `events`. No UPDATE/DELETE (REVOKE).
- API:
  - `Ledger(dsn)`; `create_schema()` — runs `schema.sql` (CREATE TABLE IF NOT EXISTS + REVOKE).
  - `append_event(*, task_id, agent_id, subagent_id=None, event_type, content: dict, parent_id=None) -> dict` — validates `event_type`; `previous_hash` = hash of the previous event in the same `task_id` (ordered by `(created_at, id)`), or the string `"GENESIS"` for the first event; `hash = sha256(canonical_json({task_id, agent_id, subagent_id, event_type, content, parent_id, previous_hash, time_iso}))`; `created_at` comes from the DB (`now()`), read back after insert.
  - `verify_chain(task_id) -> (bool, list[str])` — recompute the hash of each event in sequence; return (True, []) or (False, [ids of the events where the chain breaks]).
  - `events(task_id, event_type=None) -> list[dict]`.
- Tombstone (recorded decision): deleting a task does NOT use UPDATE. Delete = append `task_stopped` with `content={"deleted": true, ...}`; projections hide that task. Reason: append-only is a stronger invariant than "content replaced" in definition 2.3; if Nero disagrees, this is a question for him (see docs/status-phase-0.md).
- Chain-ordering deviation (decision 5 in docs/status-phase-0.md, needs Nero's confirmation): the contract above writes the chain as ordered by `id`, but `id` uses `gen_random_uuid()` so it is not ordered by insertion time — the implementation orders insertions by `(created_at, id)`. Alternative: UUIDv7 ids.

## Permission tokens — `puspa/policy/tokens.py` (definition 2.6)

- Ed25519 (`cryptography`). Token = `base64url(canonical_payload_json).base64url(signature)`; signature over the canonical payload bytes. Keys are created at runtime / in tests, never stored in files.
- Payload: `user_id, agent_id, subagent_id (null for an agent token), tools: [str], domains: [str], connectors: {name: [scope]}, anggaran: {step_max, token_max, rupiah_max}, exp (unix), iat (unix), parent (hash of the parent token or null)`.
- `issue_token(payload, signing_key) -> str`; `verify_token(token, public_key) -> dict` (raises `TokenError` if the signature is wrong, `exp` has passed, or a field is missing).
- `is_subset(child, parent) -> bool`: `tools` ⊆, `domains` ⊆, each connector scope ⊆, each budget ≤, `exp` ≤, same `user_id`, same `agent_id`; `subagent_id` may only be filled if the parent is an agent token.
- `issue_child(parent_token, requested_payload, signing_key, public_key) -> str` — verify the parent, check the subset, sign the child. On failure → `TokenError`, no token is issued.
- `revoke(token_id)` / `is_revoked(token_id)` — in-memory revocation set (persistence in phase 2). `revoke_all_for_task(task_id)` for Stop.

## Single gate — `puspa/policy/gate.py` (definition 2.6)

- `Gate(log=None, spending=None, public_key=None)` — `log` is an event-recording callable (to avoid a circular import with the ledger; tests may use a recording stub). `public_key` (optional beyond the original contract, see docs/status-phase-0.md decision 6): if a token is given as a string, its signature MUST be verified with this `public_key`; without a `public_key`, a string token is rejected. A token may also be given as a payload dict already verified by the caller.
- `request_approval(*, user_id, agent_id, token, kind, content: dict) -> ApprovalRequest` — `kind` ∈ {send, create_event, prepare_payment, delete}. Compute `content_hash = sha256(canonical_json(content))`. Check the token allows the kind + target. Record `approval_requested` via `log` if present.
- `approve(request_id, user_id)` — only the owning user. Record `approval_granted`.
- `execute(request_id, *, executor)` — re-check EVERYTHING: already approved, `content_hash` still matches the current content, token still valid (not `exp`, not revoked), spending limit OK. Only then call `executor(content)` exactly once (idempotency key = `request_id`; if already executed → return the same result without calling again). If any check fails → raise `GateDenied`, NO execution, record `approval_rejected` with the reason.
- `spending.py`: `SpendingLimit(user_id)` — default Rp0. `set_limit(user_id, rupiah)`; `spent_in_period(user_id)` (from the ledger if available, otherwise from memory); `check(user_id, nominal) -> bool` (nominal + spent ≤ limit). MVP rule: approval AND limit are two independent checks; the default Rp0 limit means any payment stays blocked until the user raises the limit in Settings (the `set_limit` API already exists; Settings UI in phase 2).

## Signed skills — `puspa/policy/skill_signing.py` (definition 2.6)

- A skill = a directory containing `SKILL.md` + `manifest.json` {name, version, tools[], domains[], connectors{}, author, signature}.
- `sign_skill(dirpath, signing_key)` — sign the canonical manifest (without the `signature` field).
- `verify_skill(dirpath, public_key) -> bool`; `load_skill(dirpath, public_key) -> dict` — raise `SkillRejected` if there is no signature, the signature is wrong, or a tool outside the manifest is used.
- `check_tool(skill, tool, public_key=None)` — **contract fixed 17 Nov 2026 (gap closed, see docs/status-phase-0.md decision 7):** `public_key` is REQUIRED (None -> `SkillRejected`, fail-closed). For a dirpath: read the manifest from disk then verify the signature; for a dict: verify the `signature` field against the unsigned manifest; only after that passes, check tool membership. A manifest changed after signing is REJECTED. Caller rule: NEVER call `check_tool` with a raw manifest without a `public_key`.
- Puspa's keys for built-in skills are created at runtime; registered community keys are separate (phase 6).

## Basic accounts — `puspa/accounts` (definition 2.13)

- `users.py`: `create_user(email, password) -> user_id` (email lowercased/stripped, unique; Argon2id hash via argon2-cffi, time_cost=2, memory_cost=65536, parallelism=1); `authenticate(email, password) -> user_id` (raises `AuthError` if wrong); `delete_user(user_id)` — delete the user row + revoke all sessions + delete the vault; events in the ledger are NOT deleted (append-only audit). Revision 7 NOTE: this password login is replaced by Google login in phase 1; sessions + vault are reused (docs/status-phase-0.md decision 20).
- `user_sessions.py`: `create_session(user_id, device_label) -> token` (`secrets.token_urlsafe(32)`, only its sha256 is stored); `validate_session(token) -> user_id` (updates `last_seen_at`); `owner_of_token(token) -> user_id` (owner lookup WITHOUT side effects — used by `resolve_session_as`, so that a rejection does not change someone else's session `last_seen_at`); `revoke_session(token)`; `revoke_all_sessions(user_id)`; `list_sessions(user_id)`.
- `vault.py`: per-user key = `derive_user_key(master_key, user_id)` — HKDF-SHA256(master_key, info=str(user_id)), length 32, salt=None — then Fernet. `put(user_id, name, value)`, `get(user_id, name)`, `delete(user_id, name)`, `list_names(user_id)`. Master key from env `PUSPA_VAULT_MASTER_KEY` (base64 urlsafe 32 bytes); if the env is missing, raise — never create a default key. **`derive_user_key` parameters are FROZEN** (docs/status-phase-0.md decision 16): changing them makes all old ciphertext undecryptable.
- Tables (`schema.sql`): `users(id UUID PK, email TEXT UNIQUE, password_hash TEXT, created_at TIMESTAMPTZ DEFAULT now())`, `sessions(id UUID PK, user_id UUID FK, token_hash TEXT UNIQUE, device_label TEXT, created_at, last_seen_at, revoked BOOL DEFAULT false)`, `vault(user_id UUID, name TEXT, ciphertext TEXT, updated_at TIMESTAMPTZ, PRIMARY KEY (user_id, name))`. All queries filter by `user_id`.

## Row Level Security — `puspa/accounts/rls.py` + `schema.sql` (definition 2.15)

- RLS active on tables `users`, `sessions`, `vault` — WITHOUT `FORCE ROW LEVEL SECURITY` (docs/status-phase-0.md decision 15): the table owner / superuser bypasses RLS (a Postgres property); real enforcement only applies to connections as the `puspa_app` role (NOLOGIN; connections run as the owner then `SET ROLE puspa_app` — evidence in `tests/test_rls.py`).
- 6 policies (counted from `schema.sql`): `users` — SELECT own, INSERT register-or-own, UPDATE own, DELETE own; `sessions` — FOR ALL own; `vault` — FOR ALL own. Policies read `app.current_user_id` (`coalesce(current_setting(..., true), '')`).
- Registration INSERT exception: the `users` INSERT policy allows an empty `app.current_user_id` (before a session is validated, the user_id is not yet known). Login / registration / session validation run as the table owner; once the user_id is known, all subsequent queries go through the RLS helper as `puspa_app`.
- Helper `rls.py` (the only place application code sets the current user): `set_current_user(conn, user_id)` / `clear_current_user(conn)` / `current_user_id(conn)` / `require_current_user(conn)` (raises `RlsError` if not yet set — fail-closed) / `as_user(conn, user_id)` (context manager, `SET LOCAL` — the value disappears on its own when the transaction ends). `user_id` is validated as a UUID before being set.
- Setup + recovery: `infra/schema_core.sql` (idempotent) creates roles `root` + `puspa_app` + DB `puspa_test` then applies the ledger + accounts schemas. Postgres on this VM does not survive restarts — recover with that file (docs/status-phase-0.md decision 14, README section "Database setup").
- RLS on the `events` table does NOT exist yet (phase 2): ledger isolation currently goes through the agent registry + `require_owner` in the owned readers (see the Access section below).

## Cross-user access — `puspa/accounts/access.py` (definition 2.6, attack path 8)

- `require_owner(requesting_user_id, owner_user_id)` — the ONLY place ownership is checked; every read API MUST call it BEFORE querying content / decrypting. Fail-closed: reject (`AccessDeniedError`) if either id is None / empty / not a string, or if the two differ. Does NOT touch the database.
- Owned read functions (scoped readers) (same pattern: determine the owner -> `require_owner` -> then read): `read_vault_entry(requesting_user_id, owner_user_id, name)` (rejected BEFORE decryption; correct owner + missing name -> `KeyError`); `resolve_session_as(requesting_user_id, token)` (the token itself is the ownership credential; uses side-effect-free `owner_of_token`; unknown/revoked token -> `SessionError`); `read_ledger_event(requesting_user_id, event_id)` (check existence first -> owner from the registry -> `require_owner` -> then read content; unregistered agent -> `AccessDeniedError`); `read_ledger_events_for_task(requesting_user_id, task_id)` (all agents in the task are checked first; a single one belonging to someone else -> the whole task is rejected).
- Agent registry (`schema_agent.sql`, table `agents`: `agent_id TEXT PK`, `user_id UUID FK -> users(id) ON DELETE CASCADE`): `create_agent_schema()`, `register_agent(agent_id, user_id)` (idempotent to the same owner; to a different owner -> `AccessDeniedError`), `owner_of(agent_id)` (None if not yet registered = rejected fail-closed). The `agents` table is created on demand by `create_agent_schema()`, not by `infra/schema_core.sql`.
- Workflows/modules do NOT yet have owned read functions (their tables only arrive in phase 4): when their tables exist, use the same `require_owner` with the `owner_user_id` from the workflow/module row.
- Tests: `tests/test_access.py` (23 tests) + 3 path-8 tests in `tests/test_attacks.py` (26 path-8 tests total).

## Quarantined reader + injection — `puspa/quarantine` (definition 2.6)

- `reader.py`: `QuarantinedReader(allowed_domains)`; `read_text(source, text) -> dict` with a strict schema `{source, title (max 200 characters, marked as data), number: [{value, unit, context_max_200}], excerpt (max 200 characters)}`. It has NO write tools / connectors. It does NOT return raw text as instructions; all free text is marked `data`. Phase 0: the reader accepts text already fetched; network fetching lives in sandbox/runner.
- `injection.py`: `scan(text) -> list[Finding{span, pattern, level}]` — detects instruction patterns (ID/EN imperatives: "kirim", "bayar", "abaikan instruksi", "kamu harus", "ignore previous", etc. + URLs); `mark(text) -> str` — wraps suspicious spans as `[DATA: ...]`. This is an extra layer; the main defence remains structural quarantine.

## Fail-closed sandbox — `puspa/sandbox` (definition 2.6)

- `runner.py`: `Sandbox(allowlist)`; `run(code: str, *, timeout_s=30) -> dict` — Docker-backed when available; when Docker is unavailable, raise `SandboxUnavailable` (fail-closed; NEVER fall back to host execution). `check_available() -> bool`.
- `allowlist.py`: default allowlist — model APIs, connector APIs, marketplace domains (tokopedia.com, shopee.co.id, blibli.com), bill-provider domains (pln.co.id + PDAM sites). Localhost only for test doubles via the explicit `allow_test_localhost=True` flag, never on the prod path.
- Docker installation was attempted in this phase; if it fails, record it as a blocker in docs/status-phase-0.md and the tests assert fail-closed behaviour.

## Chassis integration — `puspa/chassis` (definitions 2.6, 2.3, 2.13)

Principle: ZERO lines of nanobot code changed. All hooks use nanobot's official extension points (`AgentLoop` / `Nanobot.run` constructor parameters, `AgentHook` / `ToolRegistry` / `SkillsLoader` / `Tool` subclasses).

- `context.py`: `token_context(payload)` (ContextVar; only an ALREADY-verified payload dict — a raw string is ignored), `resolve_token_payload()` (ContextVar first, then `RequestContext.attributes["puspa_token"]`), `bind_token(payload)` / `reset_token(token)`, `make_ledger_sink(ledger, task_id, agent_id)` -> callable `log(event_type, content)` (best-effort: write exceptions are swallowed so the turn doesn't fall over).
- `tools.py`: `PuspaTool(Tool)` wraps one nanobot tool (following nanobot's `_LegacyErrorPrefixTool` pattern). `execute` order: (1) token present, otherwise -> reject fail-closed; (2) tool name in `payload["tools"]`, otherwise -> reject; (3) if the tool has an attached skill manifest, `check_tool(manifest, name, public_key)` (signature required, see the Skills section); (4) if the tool maps to an outbound-action kind (`KIND_TOOLS`: send/create_event/prepare_payment/delete) -> `Gate.request_approval`; without a gate -> reject; if the gate creates a request, return `ToolResult.error("Menunggu persetujuan...")` WITHOUT executing; (5) execute the original tool; the string result is scanned with `puspa.quarantine.scan` — findings are recorded in `tool_completed` (detection only; blocking by the Verifier is a phase 2 task). Audit: `tool_called` + `tool_completed` on every call; content NEVER contains tool parameters (they may hold sensitive data).
- `registry.py`: `PuspaRegistry(ToolRegistry)` — `register` wraps every tool once (anti-double-wrap via `isinstance`); the `prepare_call` override is a backstop (a tool that slipped through unwrapped still gets its token checked; rejected + recorded as `tool_called`/`tool_completed` with rejected status). `get_definitions` is NOT checked (the schema the model sees is not execution).
- `hooks.py`: `PuspaHook(AgentHook)` — observation only (nanobot hooks cannot veto): `before_run` -> `task_created`; `after_run` -> `task_completed` + `agent_message` (final content, truncated to 2000 characters); `on_error` -> `task_failed`. `make_puspa_hook_factory(task_id, agent_id, log)` -> `AgentTurnHookFactory` for `build_agent_turn_hook`.
- `skill_loader.py`: `PuspaSkillsLoader(SkillsLoader)` overrides `_load_skill` — skills from `workspace`/`plugin` sources MUST pass `verify_skill` (without a `public_key` -> all rejected); `builtin` sources are trusted as part of the chassis. Failed verification -> `None` (the skill is invisible, following nanobot semantics).
- `agent_session.py`: `bind_turn(*, session_token, signing_key, public_key, agent_id, tools, domains=None, connector=None, anggaran=None, exp_seconds=3600, task_id=None, dsn=None) -> (user_id, token_string, payload_dict)` — validate the session then issue an agent permission token (default budget {step_max 25, token_max 100000, rupiah_max 0}). `payload_from_token(token, public_key)` = `verify_token`.
- `sandbox_tool.py`: `SandboxTool(Tool)` — name `run_code`, parameters {code (required), timeout_s (default 30)}, `read_only=False`, `concurrency_safe=True`. Execution is delegated to `puspa.sandbox.Sandbox`; `SandboxUnavailable` -> `ToolResult.error` (never executes on the host). nanobot's built-in shell tool is NOT on the Puspa token's list.
- `factory.py`: `PuspaChassis` (dataclass config: task_id, agent_id, ledger, gate, public_key, sandbox, skill_manifests, log) + `build_registry(chassis)` (register `SandboxTool` first, before `AgentLoop` is created) + `build_hook_factory(chassis)` + `assemble(chassis, config, bus=None)` -> `AgentLoop.from_config(config, bus=bus, tool_registry=registry, hooks=[...], hook_factories=[...])`.
- Subagent tokens: nanobot's `SubagentManager.spawn` has NO token slot; the mechanism is a Puspa ContextVar set around `spawn()` (visible in the subagent because `asyncio.create_task` copies the context — tested in `test_integration.py`).

NOT yet in phase 0 (phase 2): an end-to-end turn test with the real `AgentLoop` + model provider; closing the approval -> async execution flow; blocking of poisoned results by the Verifier; ledger policy when Postgres is down; `Sandbox.run` in an executor.

## Tests

- pytest. `tests/conftest.py`: fixtures `test_dsn` (env `PUSPA_TEST_DSN` or peer-auth default), `ephemeral_keys` (fresh Ed25519 keys per test session), `tmp_skill_dir`, `vault_master_key` (random master key per test, only in the test process env). Autouse fixture: apply the accounts schema then `TRUNCATE users, sessions, vault CASCADE` before and after each test — TRUNCATE CASCADE also empties the `agents` table (FK to users), so agent rows don't leak between tests. The `sys.path` fallback to the local `./nanobot` clone is only used if the directory actually exists (guarded by `Path.exists()`); from a fresh clone, tests use nanobot from the git-pinned dependency.
- Each test module has a header comment mapping it to a definition section + gate.
- `test_attacks.py` (8 paths, definition 2.6) was written AFTER the packages landed, by a separate subagent, based on the interfaces in this contract.
- Don't claim a pass before `pytest` is green. Include the test output in the final report.
