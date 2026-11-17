# README — Phase 2 MVP Puspa (Team runtime + PWA + Connectors + Scheduler)

Definition: revision 7 (approved by Nero 17 Nov 2026 ~17:42 WIB).
Timeline ignored on Nero's order, 21:53 WIB. Full detail:
`status-phase-2.md`. DO NOT push to GitHub; DO NOT start Phase 3.

## Structure

```
build/fase-2/
  puspa/                 # Puspa namespace (extend_path, shared with Phase 0/1)
    runtime/             # agents, handoffs, budgets, subagent pool, task lifecycle
    evidence/            # evidence book, verifier, orphan-number renderer, projections
    router/              # tiered model router (on top of Phase 0 BYOK)
    connectors/          # Gmail/Drive/Calendar + per-agent grants
    scheduler/           # scheduler + triggers (daily backup, consolidation)
    push/                # Web Push VAPID (aes128gcm)
    cassette/            # cassette tests
    pwa/                 # FastAPI app (7 screens)
  apps/pwa/              # Jinja templates + static (service worker, manifest)
  infra/schema_runtime.sql # Phase 2 tables + RLS + trigger rejecting Puspa deletion
  cassettes/             # 10 reference cassettes (generator: create_reference_cassettes.py)
  tests/                 # Phase 2 suite (73 tests)
```

Old → new layout mapping (§7): see `status-phase-2.md` §9.

## Running the tests (SOLO — never in parallel with another pytest)

```bash
cd build/fase-2
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -m pytest tests -q
# 73 passed
```

Base re-verification (solo, in sequence):

```bash
cd ../fase-0 && ../fase-0/.venv/bin/python -m pytest tests -q  # 244 passed, 4 skipped
cd ../fase-1 && ../fase-0/.venv/bin/python -m pytest tests -q  # 58 passed
```

## Running the PWA locally

```bash
cd build/fase-2
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -c "
import sys; sys.path.insert(0, '../fase-0'); sys.path.insert(0, '../fase-1'); sys.path.insert(0, '.')
from puspa.pwa.app import jalankan
jalankan(host='127.0.0.1', port=8000)"
# Open http://127.0.0.1:8000/chat (needs Google login; demo mode:
# create_app(test_user_id='<user uuid>') — see status-phase-2.md)
```

## Language policy (Nero's order, 21:53 WIB)
English identifiers, Indonesian domain values (revision 7), Indonesian
prose. Do not invent new terms that are not in revision 7 and are not
standard technical terms; if one is needed, record it in `reuse.md`.

## New Phase 2 dependencies
- `fastapi==0.163.1` (MIT) — PWA. The rest from the Phase 0 venv.
