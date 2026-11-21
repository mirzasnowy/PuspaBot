# Phase 1 MVP PuspaBot — Persona + Memory + Onboarding

Definition: revision 7 (approved by Nero 17 Nov 2026 ~17:42 WIB).
Work period: 17 Nov 2026 (the 17-week timeline ignored on Nero's
order, 21:53 WIB — dates are only ordering, not deadlines).

## What was built

| Package | Contents |
| --- | --- |
| `puspa/memory/` | 3-layer memory: Profile, Episodic (pgvector), Procedural; memory slices; idle-time consolidation |
| `puspa/persona/` | Speaking style from examples + corrections; blind-test harness for gate 3 |
| `puspa/onboarding/` | 4-step onboarding, 5-minute target, measured |
| `puspa/auth/` | Google login (OAuth 2.0 + PKCE) + backup email link |
| `puspa/backup/` | Encrypted Postgres backup + restore |
| `puspa/consent/` | Data consent + account export/delete |

Phase 0 base reused unchanged: `puspa/ledger` (ledger),
`puspa/accounts` (users/sessions/vault/rls/access), `puspa/policy`,
`puspa/quarantine`, `puspa/sandbox`, `puspa/chassis`. Base imports via
`extend_path` in `puspa/__init__.py` (see `tests/conftest.py`).

## How to run the tests (SOLO — never two pytest runs at once)

```bash
cd build/fase-1
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -m pytest tests -q
```

No new dependencies (see `requirements.txt`): everything Phase 1
uses was already in the Phase 0 venv (psycopg, cryptography,
argon2-cffi, httpx, pytest). pgvector 0.6.0 installed via apt
(`postgresql-16-pgvector`).

## Numbers

- Phase 1 suite: **58 passed** (17 Nov 2026, after Postgres recovery).
- Phase 0 suite (re-verification): **244 passed, 4 skipped**
  (4 skips = live-container Docker tests, the same environment
  block as Phase 0).
- Onboarding (system side, 4 steps): **0.29 seconds** (5-minute target).
- Backup + restore to a clean DB: **0.3 seconds**, 14 tables recovered,
  ledger hash chain intact.

## What is pending (needs Nero / a human — not a code blocker)

1. **Publish the Google OAuth app** to "In production" (needs
   Nero's browser; Testing status = refresh tokens expire after 7 days).
2. **Gate 3 blind test**: harness + 20 example items done; none of
   the 5 friend judges has answered yet.
3. **Legal review** of the consent text (decision 20) before users
   outside the close circle.
4. **Decision 5** (PLN/PDAM/phone customer numbers) — needed for Phase 2.

Full detail + definition-vs-build conflicts: `status-phase-1.md`.
Cross-phase reuse log: `reuse.md`.
