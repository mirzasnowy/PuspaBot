# Status log archive — Waves 35-36 (moved from docs/status-maturity.md, 20 Nov 2026)

The live log crossed the 1000-line soft cap (1026 lines after the Wave 38 entry), so the two oldest sections (Waves 35 and 36) were moved here verbatim. History is archived, never deleted.

## Wave 36 — B8 agent payments phase 1 (1 worker, 20 Nov 2026)

B8 agent payments, phase 1: prepare only (commit
805409a9). New puspa/payments package: models (verified
Recipient, integer-Rupiah amounts, B5 rail resolution with
COD refused, evidence-must-name-recipient rule) and
prepare (PreparationService: prepare / approve / reject /
list / list-payable / get / mark-paid-by-user, plus the
execute_payment guard — always raises ExecutionRefused
after recording the refusal; EXECUTION_LIMIT_RUPIAH = 0
as a module constant, no executor, gateway client, or
payment credential anywhere). Every preparation writes a
payment_prepared outcome record through the shared B2
writer plus an approval_requested ledger event (G4 kind
prepare_payment); rows start awaiting_approval and become
payable only after owner approval; idempotency key returns
the same preparation, same key with different content is
refused; expiry defaults to 24h and is enforced on
approve/mark-paid/read; mark_paid_by_user is a user
assertion (payment_paid_by_user outcome, evidence kind
user_assertion), never an execution. PWA payments API (8
routes, execute always 403) registered in app.py;
infra/schema_payments.sql (owner-only RLS, per-user
idempotency key); docs/agent-payments.md +
docs/openapi-payments.yaml (main spec gains 24 lines of
$ref stubs, baseline re-recorded 1813 to 1837 with
reason).

Coordinator verification fixes (in the B8 commit, not the
worker's): the worker's API never applied its schema — a
server that only ran schema_core.sql would fail on the
missing table. Fixed with the B7 mockup pattern (lazy
create_schema on first request, once per app) plus a test
that drops the table and proves the first request
recreates it; and the worker shipped no OpenAPI contract,
so docs/openapi-payments.yaml + stubs were added
following the B7/B13/B18 split precedent.

Central suite Wave 36: 3,899 passed, 23 skipped, 1
warning, EXIT 0, 34m24s (3,864 + 27 B8 module + 8 API,
exact). B8 pushed: 805409a9. Notion B8 row Done / Suite
green. Honest limits: no live gateway, no real rails
integration, no execution; paid-by-user is an assertion,
not a verified payment; recipient verification is against
the evidence supplied, not a bank/wallet registry; no
account-reference well-formedness check; phase 2 is not
ticketed.

## Wave 35 — B5 + B7 (2 workers parallel, pipeline rule, 20 Nov 2026)

B5 local Indonesian data layer (commit e7e71067) and B7
prompt to visual mockup, drag and drop (commit 70aa4866).
B5: new puspa/local_data package (rates: seeded shipping
rates for 10 cities x 8 couriers from Jakarta with COD
flags, fail-loud on unknown city/courier, cheapest + COD
lookup, JSON refresh seam; rails: QRIS, virtual account,
bank transfer, GoPay, OVO, DANA, COD naming only; language:
everyday Indonesian + mixed ID-EN normalisation plus
city/courier extraction); wired into B1's answer as
shipping + payment_rail sections (information only, the
verdict is unchanged); docs/local-data-layer.md. B7: new
puspa/mockup engine (declarative JSON design doc, numbered
ops with undo/redo, 5 Indonesian templates, deterministic
prompt-to-mockup with 2-3 variants + deterministic
self-critique, word-change patches in Indonesian, LLM patch
seam via router complete(); deterministic SVG/safe-HTML
render with strict CSP, PNG export via Pillow, stdlib PDF
export, Postgres store with RLS); PWA Mockup screen (10th
screen, drag-drop round-trips as move_node ops); API +
Jinja fallback template; docs/mockup-editor.md +
docs/openapi-mockup.yaml (main spec gains 28 lines of $ref
stubs, baseline re-recorded 1785 to 1813 with reason);
Pillow==13.0.0 pinned in requirements.txt.

VM replaced mid-wave (~00:30 WIB 20 Nov, uptime 0 min):
the first Wave 35 central suite was killed at 87% and the
wait loop got SIGTERM; Postgres packages were GONE
entirely. Full reinstall: apt-get update + install
postgresql-16 + postgresql-16-pgvector 0.6.0, cluster
started manually (policy-rc.d denies the service start).
Fresh-cluster gotcha: role root does not exist yet, so
schema_core.sql cannot connect as root — create it first
via su postgres, then run the schema; w1/w2 recreated +
ledger/accounts schemas applied; RLS true on
users/sessions/vault in all three. The killed run has no
SUITE_EXIT and was re-run from scratch. Recovery note in
the pipeline doc.

Central suite Wave 35: 3,864 passed, 23 skipped, 1
warning, EXIT 0, 27m15s. Arithmetic: 3,786 + 46 B5 + 31 B7
= 3,863, plus 1 — the webui_lite template-coverage test
gains a case because B7 added apps/pwa/templates/
mockup.html (verified by per-file collect counts in w1 vs
w2). Full vitest in build/: 122 passed. Both items pushed:
B5 e7e71067, B7 70aa4866. Honest limits: B5 rates are
seeded samples (labelled, 1 kg default, JSON refresh seam
unfilled); B7 vision self-critique is a deterministic
geometry/contrast seam (no vision model yet), PNG uses
Pillow's default bitmap font, PDF is flat single-page,
undo/redo stack is in process memory, no module binding,
no Figma import, no publish/rate.
