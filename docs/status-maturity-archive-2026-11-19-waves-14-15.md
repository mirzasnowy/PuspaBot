# Status archive: repo maturity phase, Waves 14-15 (18-19 Nov 2026)

Archived from `docs/status-maturity.md` on 19 Nov 2026, when the live
log approached the 1,000-line soft cap of the file-size ratchet (975
lines). Frozen: never appended to; the living log continues in
`docs/status-maturity.md` (Wave 16 onwards).
History is never deleted, only archived.

## Wave 14: G4+G5+G6 (approval taxonomy + fail-closed gateway + audit trail, DONE 18 Nov 2026)

New files `puspa/policy/approvals.py` (773 lines, G4) +
`puspa/policy/gateway.py` (791 lines, G5) +
`puspa/policy/rules.py` (301 lines, G5) +
`puspa/ledger/audit.py` (571 lines, G6) + 3 test files
(1,111 lines) + `docs/approvals.md` (171) + `docs/gateway.md`
(176) + `docs/audit-trail.md` (137) + G4+G5+G6 sections in
REUSE.md + NOTICE.md (OpenBot MIT pattern attribution, from
documentation — the enforcing CEL code is not in the OpenBot
repo, so the evaluator is Puspa's own; no code copied).
**117 tests passed** (verified by the coordinator), ruff check +
format clean, mypy clean (8 source files). Contents: G4 — six
kinds that always ask (delete, send_to_other, share, post, pay,
change_credentials); tiered answers once / conversation /
always (24h expiry, "once" bound to content hash);
strictest-wins (credential → members_self_serve → ask_every_time
→ extra vigilant → check-in → auto-review → stored answer →
default ask); auto-review (unknown action, pay without amount,
delete without target, send without recipient); extra-vigilant
switch (default off); recognition from the first verb of the
tool name + button labels, never content (pay via the word
`payment` anywhere in action/page, tool names
transfer/payout/order/payroll/plan upgrade/subscription matched
whole-word so `recorder` does not catch `order`, buttons
Transfer/Withdraw/Pay/Buy/Place your order/Subscribe);
credential changes can never be approved by any answer
(escalate to a human); `ASKING_GUIDANCE` marker +
`GuidanceState` anti-double-approval; destructive tools marked
in their description; wired to the existing `Gate` (hash binding
+ 24h expiry still enforced, proven by tests) and to
`kind_for_tool` (`puspa/chassis/tools.py` falls back to the G4
classifier for tool names outside the gate's table). G5 —
`Gateway.execute` in 5 steps (resolve target from snapshot →
evaluate policy → audit the decision → call the computer only
when forwarded → second audit line when a forwarded action
fails); own rule language (tokenizer + recursive-descent parser
+ evaluator, no `eval()`: `contains()` / `matches()`
case-insensitive over the 20 documented attributes, plus
`and`/`or`/`not`, `==`/`!=`, `true`/`false`); deny before
allow; fail-closed (missing/empty policy → refuse everything,
broken deny → deny, broken allow → no allow, snapshot never
loaded / stale → refuse everything, malformed policy →
`PolicyError` at load and `Gateway.start` halts startup);
dry-run (verdict recorded, still forwarded — except the cloud
metadata address, refused in all modes including dry-run); 5
built-in templates each with its `cost` (the Enter/Space trap
and the `contains(page.host, "x.com")` trap that also blocked
dropbox.com/netflix.com recorded explicitly); snapshot kept
fresh via an injected refresh hook + `PostgresPolicyWatcher`
LISTEN/NOTIFY as a wake-up signal;
`make_ledger_gateway_sink` maps gateway audit lines to existing
ledger event types (`tool_called` / `tool_completed`) only. G6
— `initiator` field as an attribute in event content (person,
deployment, routine, responsibility, memory, handoff); event
types stay 31 (proven by test); "Nobody watching" filter =
routine+handoff+responsibility+memory; handover
(help_requested / control_taken / control_released) audited via
the existing handoff event types + `control` attribute; while a
human controls the browser, bot actions are refused
(`ControlRejected`), not queued; CSV export max 5,000 rows +
truncation marker header + `'` prefix on cells starting
`= + - @` (formula-injection guard) + secrets redacted + saved
files contribute path + size only; every export recorded in a
separate `AuditExportLog` (JSONL hash-chained, outside the core
ledger — documented choice, not a new event type and not
squeezed into an unrelated type); retention via
`AUDIT_RETENTION_DAYS` (unset = forever); pruning touches only
the export log, never the core ledger. No new dependency.
`puspa/policy/gate.py` untouched. Coordinator fix during
verification (worker test, not worker report): the new G4 test
`test_policy_request_executes_payment_through_gate` set a
spending limit for `user-1` in the module-global `_limits` dict
and never cleaned it, so the older
`test_spending_limit_default_zero_blocks_payment` (which expects
the fresh default 0) failed when the suite ran both files —
the test now uses its own user `user-g4-payment`
(SpendingLimit, token payload, request, approve). First full
run: 1 failed, 2,811 passed; after the fix: **2,812 passed, 9
skipped, 1 warning, 19m34s, EXIT 0** (2,695 Wave 13 + 117
G4+G5+G6, exact). Fix commit d9a4e8c3. Honest limits (from the
three docs): no live computer call site is guarded by the
gateway yet (Puspa has no browser/desktop driver) — a caller
that drives the computer directly can still bypass it;
`ApprovalStore` + `ControlState` are in-memory (lost on
restart); no admin UI for rules (rules are added in code, and
"admin" is an owner string, not a role check); the rule language
is deliberately small (no numbers/arithmetic/lists); an
`ActionContext` that carries no attribute sees an empty string,
not an error — the fail-closed "broken rule" path only lives
for raw context dicts; the export log is a JSONL file without a
lock (two processes can interleave appends); pruning cuts the
head of the hash chain (verification anchors on the oldest
surviving record); redaction only catches secret-assignment
patterns, not bare secrets in free sentences. Also checked: the
Indonesian aliases `kirim`/`hapus`/`bayar` in the classifier
are interop, not new naming — `gmail.kirim` is a real
pre-existing tool name in Puspa's registry (`gate.py`,
`runtime/hierarchy.py`, `agent_scan.py`); recognition has to
read the names that exist.

## Wave 15: G29 (code graph extraction + live LLM triple extractor, DONE 18 Nov 2026)

New file `puspa/retrieval/code_graph.py` (832 lines) +
`tests/test_retrieval_code_graph.py` (334 lines, 13 tests) +
`docs/code-graph.md` (166 lines); changed
`puspa/retrieval/knowledge_graph.py` (+~30 lines, live
extractor wiring) + `puspa/retrieval/__init__.py` + G29 sections
in REUSE.md + NOTICE.md (Graphify Apache-2.0 pattern
attribution, no code copied). **22 tests passed** (13 new + 9
old retrieval-graph tests, verified by the coordinator), ruff
check + format clean, mypy clean (3 source files). Contents:
`CodeExtractor` (AST 2-pass — definitions collected first, so
calls to functions defined later in the file still resolve);
entities file/function/class/module/SQL table/config + edges
defines/imports/calls/inherits/reads_table/writes_table/
reads_config, all into the existing shared `KnowledgeGraph`
(not a second graph — prose and code nodes live in one graph,
proven by test); queries from the graph without re-reading
files (`callers_of`/`callees_of` one hop, `reachable_from` BFS
+ hop counts, `reaches`, `call_path` shortest path; a bare name
resolves when unique, ambiguous raises `ValueError` naming the
candidates, unknown returns empty); `CodeGraph` incremental
indexing (`add/update/remove_file`, exact detach of the old
extraction, shared nodes survive, unchanged text = no-op, no
duplicates); `JsonTripleExtractor` now defaults to the router's
live completion (`resolve_llm_complete("default")` →
`default_llm_complete` on `complete_messages`, the same seam
the turn loop's `turn_api` calls) — building the callable
needs no key and makes no network call, a missing key raises
hard on the first call, kwargs (`model`/`transport`/
`key_resolver`) pass through so tests bind a fake transport;
old callables and non-callable values behave as before
(backward compatible). No new dependency. Honest limits (from
`docs/code-graph.md`): Python only (`ast`); dynamic calls
invisible (`getattr`, `eval`, calls through unknown objects);
names normalized lowercase like the shared graph, so `Foo` vs
`foo` collide; `from . import x` skipped; SQL table/config
detection is a string-literal heuristic (false positives from
prose/docstrings, misses dynamically built queries); no live
provider call proven in the suite (tests use fake
callable/transport, real extraction still needs a key via
BYOK). Wave 15 central SOLO suite: **2,825 passed, 9 skipped, 1
warning, 17m29s, EXIT 0** (2,812 Wave 14 + 13 new G29 tests,
exact). The first run was killed by a VM replacement at ~7 min
(SIGTERM, empty log; third replacement that day), and the
recovery waiter was killed by a fourth replacement ~4 min
later; recovery succeeded on the second attempt, and the
re-run went green on the first try.

## Wave 15: G30 (skill discovery, vercel-labs find-skills pattern, DONE 18 Nov 2026)

Worker 3333ca47 + coordinator follow-up (G4 `install` kind).
New files `puspa/marketplace/discovery.py` (815 lines) +
`tests/test_marketplace_discovery.py` (586 lines, 38 tests) +
`tests/test_marketplace_discovery_approval.py` (8 tests) +
`skills/find-skills/SKILL.md` (202 lines) +
`skills/find-skills/manifest.json` +
`docs/skill-discovery.md` (168 lines); changed
`puspa/marketplace/errors.py` (4 new errors) +
`puspa/marketplace/__init__.py` (exports) +
`puspa/policy/approvals.py` (new `install` kind, coordinator
follow-up) + `docs/approvals.md` (seven kinds) + G30 sections
in REUSE.md + NOTICE.md (vercel-labs/skills MIT pattern
attribution, no code copied). **54 tests cover G30 (38
discovery + 8 bridge + 8 taxonomy install-kind), all passing**
(verified by the coordinator), ruff check + format clean,
mypy clean. Contents: `SkillsShClient` (GET
`{base}/api/search?q=...`, fail-soft to empty on transport
failure / non-200 / malformed payload); word-drop fallback
(exact < 10 + 2-6 terms → drop one term per subquery, merge
dedup by slug, cap 25, exact never displaced); rubric
(recommended = 1K+ installs + trusted source or 100+ stars;
suspect = < 100 installs or < 100 stars; unknown stars
reported unknown, never assumed good); `DiscoveryCache`
(local JSON, 24h TTL, stale served when live fails);
`install_discovered` (6 fail-closed gates: valid slug →
fetch SKILL.md → sha256 vs expected → explicit per-skill
approval → licence detected + allowlist MIT/Apache/BSD →
import DISABLED → enable with `explicit_approval=True`;
G24 `record_use` when `skills_root` given); built-in
find-skills skill (own wording, trigger terms, imports
through the registry). Coordinator follow-up (the worker's
open decision, closed here): `install` is now a first-class
G4 kind — always asks, tiered answers in the `ApprovalStore`
with 24h expiry, content-bound to `{"skill_slug",
"sha256"}`; recognition from tool verb `install` and button
label "Install"; no Gate kind (local enablement, never
outbound — `ApprovalPolicy.request` refuses it, same pattern
as `change_credentials`); bridge `approval_from_store`
(builds `InstallApproval` fresh from the store per install)
+ `consume_install_approval` (spends "once" answers only).
Honest limits (from `docs/skill-discovery.md`): no live
call to skills.sh or GitHub yet (API shape from public
`find.ts` + SKILL.md; first live search unverified);
registry returns no description / version / licence / stars
(imports as 1.0.0, licence from the repo LICENSE file, stars
caller-supplied); SKILL.md only from conventional paths
(`skills/<slug>/SKILL.md` or `<slug>/SKILL.md` on
main/master); built-in find-skills skill not wired into
turn-loop skill activation yet; `InstallApproval` is
evidence, not a capability — build fresh per install,
never cache across installs. Wave 15 central SOLO suite:
**2,879 passed, 9 skipped, 1 warning, 19m51s, EXIT 0**
(2,825 Wave 15 G29 + 54 new G30 tests, exact).

