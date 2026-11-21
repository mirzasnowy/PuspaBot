# Phase 4 — Workflows + Custom modules (revision 7 §2.12–2.13)

Block library, triggers, chat-based workflow composer, derived
permission manifests, dry runs, workflow runner, 24-hour approval
expiry, custom modules with a manifest gate, and 3 built-in templates.
All in `build/fase-4/`; Phase 0/1/2/3 packages unchanged (used via
`sys.path`, the conftest pattern of phases 0/1/2/3).

## Structure

| Module | Contents |
| --- | --- |
| `puspa/workflow/blocks.py` | Library of 15 fixed blocks + schemas + MVP limits (10 steps, 2 branches, 20 items) |
| `puspa/workflow/triggers.py` | 4 triggers (schedule, due date, price drop, manual); email/webhook rejected |
| `puspa/workflow/composer.py` | Chat-based workflow composer (deterministic, library blocks only) + block requests |
| `puspa/workflow/manifest.py` | Derived permission manifest (computed by code) + subset check vs user permissions |
| `puspa/workflow/approvals.py` | Approval queue: content-bound, expires after 24 hours, then cancelled |
| `puspa/workflow/runner.py` | Workflow CRUD + versions/rollback, dry runs, workflow runs, scheduler tick |
| `puspa/workflow/templates.py` | 3 built-in templates; reference tasks 8 and 9 as workflows |
| `puspa/workflow/panel.py` | Render step cards + run results (HTML without JavaScript) |
| `puspa/workflow/metrics.py` | Workflow metrics §3.8 (active users, hands-free runs, block requests) |
| `puspa/modules/manifest.py` | Module manifest + manifest gate (5, by code) + CRUD/versions/rollback/delete |
| `puspa/modules/tables.py` | Module-owned tables (typed columns, per-user + per-module isolation) |
| `puspa/modules/views.py` | Render 7 fixed components (including SVG line chart, no JavaScript) |
| `puspa/modules/notifications.py` | Notification rules from module data |
| `puspa/modules/bills.py` | Built-in Bills module as a manifest |
| `infra/schema_workflow.sql` | workflows, workflow_runs, workflow_approvals, modules, module_tables, module_rows tables (+ RLS) |

## Running the tests

```bash
cd build/fase-4
NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
  ../fase-0/.venv/bin/python -m pytest tests -q
```

No new dependencies (see `requirements.txt`). DO NOT run pytest in
parallel (TRUNCATE collisions on the shared `puspa_test` DB).

## Test numbers (solo run, 17 Nov 2026)

| Suite | Result |
| --- | --- |
| Phase 4 (new) | 76 passed |
| Phase 0 | 248 passed, 4 skipped |
| Phase 1 | 58 passed |
| Phase 2 | 73 passed |
| Phase 3 (with browser) | 62 passed |

Detail, gates, and blockers: `status-phase-4.md`.
