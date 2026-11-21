"""File-size ratchet (G15, DuskBot pattern).

Rules, adapted from DuskBot's `scripts/testing/file-size-ratchet.test.ts`:

- No Puspa-owned file may exceed 2000 lines (hard cap).
- A file over 1000 lines may only shrink: its current line count must not
  exceed the recorded baseline below. Files over 1000 lines that are not
  in the baseline are rejected, so a new large file needs an explicit
  baseline entry (recorded here, with a reason) instead of slipping in.

Scope: Puspa-owned source and docs only. Excluded, with reasons:

- ``nanobot/`` — vendored third-party read reference (the nanobot-ai
  dependency, pinned in requirements.txt); its size is upstream's business.
- ``web-replica/`` — frozen re-themed snapshot of another site's front
  page; not edited Puspa source.
- ``backups/`` and ``cassettes/`` — snapshots and recorded fixtures, not
  source.
- ``.venv/``, ``__pycache__`` and ``.pytest_cache`` — generated.
- ``node_modules/`` and ``dist/`` — generated (npm install, build output).
- ``package-lock.json`` / ``yarn.lock`` / ``pnpm-lock.yaml`` — generated
  lockfiles; committed with the code they lock, but not hand-written
  source, so the ratchet does not count them.
- Files named ``generated-*`` — generated output (e.g. the TypeScript
  client types from the OpenAPI spec); the spec is the source of
  truth and it IS counted, the output is reproducible from it.

Line counting matches ``wc -l`` for files that end with a newline
(``bytes.splitlines()``); all four baseline files do.

This test uses only the standard library and touches no database, so it
also runs in CI's dependency-free ``checks`` lane with ``--noconftest``.
"""

from __future__ import annotations

from pathlib import Path

# Hard cap: no file may exceed this many lines.
HARD_MAX_LINES = 2000

# Soft cap: a file over this many lines must be recorded in BASELINE and
# may only shrink.
SOFT_MAX_LINES = 1000

#: Baseline recorded 18 Nov 2026 (line counts measured against the tree at
#: that date). A file may be removed from this table only when it drops to
#: 1000 lines or fewer; a count here may only ever go down — except for a
#: recorded, mechanical re-baseline (see the `ruff format` notes below,
#: 18 Nov 2026), which is a deliberate event, not silent growth.
BASELINE_LINES = {
    # World model (Puspa brain): prediction + planning over belief state.
    # Large because it holds the model, the planner hooks and the belief
    # update in one module; split only when a clean seam is proven.
    # Re-baselined 18 Nov 2026: the CI lint adoption's mypy fixes
    # restructured the belief handling (per-branch belief variables +
    # explicit None guards in _apply_observation, isinstance narrowing
    # in decay_stale_beliefs) and `ruff format` wrapped long lines:
    # +145 lines, no logic change. The ratchet resumes from 1605.
    # Split 19 Nov 2026 (K4 ratchet fix): the K4 causal read path
    # grew the file to 1,626, past this baseline, so the belief data
    # holders moved to puspa/world/beliefs.py (six dataclasses, the
    # price-key helper, three constants) and the causal read path to
    # the CausalExpectationMixin in puspa/world/causal.py. No logic
    # change, only the seam the comment above anticipated. The file
    # shrank 1,626 -> 1,397; the ratchet resumes from 1397.
    # Split 19 Nov 2026 (K5 ratchet fix): the K5 other-agent wiring
    # grew the file to 1,443, past this baseline, so the ingest hook
    # moved to AgentModelMixin.feed_task_result and the per-contact
    # state to AgentModelMixin.__init__ (both in puspa/world/agents),
    # and the Prediction dataclass moved to puspa/world/beliefs.
    # No logic change, only the same seam. The file shrank
    # 1,443 -> 1,395; the ratchet resumes from 1395.
    "puspa/world/model.py": 1395,
    # Other-agent goal models (Kosmos K5): the goal-model types,
    # the in-memory store, the keyword inferrer, and the mixin
    # that wires them into the world model, in one place. New
    # file, 19 Nov 2026, recorded at 1,047 lines with this reason
    # instead of slipping in: it crossed 1000 when the ingest
    # hook and the per-contact state moved here out of model.py
    # (the K5 ratchet fix). Split candidate when it grows again:
    # the inferrer (GoalInferrer + KeywordGoalInferrer) into a
    # goal_inference module, or the store into its own module.
    "puspa/world/agents.py": 1047,
    # Approval policy + store (E-10): the taxonomy, the narrowed
    # "always" bindings (target pattern + nominal cap, beta ban for
    # pay/send_to_other), and the fatigue tracker in one module.
    # Crossed 1000 lines in Wave 40 (E-10): +~240 lines for the
    # narrowing + tracker. Split candidate when it grows again:
    # the fatigue tracker into its own module, or the policy
    # store into a store module.
    # Wave 45 (E-16 follow-up): +4 lines for the beta ban on
    # broad "always" share grants.
    "puspa/policy/approvals.py": 1051,
    # Learning loop (E-15): experience recording, feedback,
    # quarantine, holdout evaluation, and skill activation in one
    # module. Crossed 1000 lines in Wave 43 (E-15): +~200 lines
    # for the guardrails (quarantine + holdout + weekly cap).
    # Split candidate when it grows again: the quarantine store
    # into its own module, or the holdout evaluation into one.
    "puspa/memory/learning_loop.py": 1215,
    # Turn loop (E-18): the turn runner, model calls, tool calls,
    # and the per-turn state machine in one module. Crossed 1000
    # lines in Wave 44 (E-18): +~80 lines for the observability
    # wiring (trace setup in run_turn, spans around the turn and
    # the model call, TTFT/cost/abandoned-cost metrics).
    # Split candidate when it grows again: the model-call step
    # into its own module, or the tool-call loop into one.
    # P-09 (live view + takeover, 20 Nov 2026): +82 lines — the
    # takeover_hold gate (constructor param, _check_takeover_hold
    # before every model call and every registry tool call, the
    # TurnInterrupted catch at both sites, the takeover reason
    # through _finish_interrupted). The ratchet resumes from 1163
    # (1162 + 1 line from `ruff format`).
    "puspa/turns/loop.py": 1163,
    # PWA app factory (G28 wiring + P-01/P-02/P-03): the FastAPI
    # app, the screen context + bootstrap, and every API module's
    # route registration in one place. Crossed 1000 lines in
    # Wave 46: P-01's memory service wiring, P-02's autonomy dial
    # + controls wiring, and P-03's voice/share wiring (the
    # transcriber seam, the share inbox, the manifest share_target)
    # together added ~66 lines to the 943-line pre-P-01 factory.
    # Split candidate when it grows again: the route registrations
    # into a routes module, or the screen context into its own
    # module.
    # P-04 (receipt photos, 20 Nov 2026): +19 lines — the
    # ocr_engine argument, the receipt pending/expense state, and
    # the receipt route registration. The ratchet resumes from 1028.
    # P-05 (shared rooms lite, 20 Nov 2026): +36 lines — the room-activity frame on /sse plus the membership import and route registration. Resumes from 1064.
    # P-05 fix (20 Nov 2026): +5 lines — the blind-except narrowing
    # of the room-activity SSE frame (the psycopg import plus the
    # narrowed handler with its failure-mode comment). The ratchet
    # resumes from 1069.
    # P-07 (morning summary + notes-to-actions, 20 Nov 2026): +19
    # lines — the "morning" screen in SCREENS, the two morning test
    # seams on create_app (connector factory + HTTP client), and
    # the morning route registration. Resumes from 1088.
    # P-09 (live view + takeover, 20 Nov 2026): +23 lines — the
    # "live" screen in SCREENS, its agents context (narrowed
    # except, no new blind handler), and the takeover store +
    # live-view route registration (with a blank separator line).
    # Resumes from 1111.
    # P-10 (replay timeline, 21 Nov 2026): +14 lines — the
    # "replay" screen in SCREENS, its agents context (same
    # narrowed except as live), and the replay route
    # registration. Resumes from 1125.
    # P-11 (visual approval overlay, 21 Nov 2026): +8 lines —
    # the approval_overlay_api import, the OverlayStore state,
    # and the overlay route registration. Resumes from 1133.
    # P-12 (rich cards in Chat, 21 Nov 2026): +5 lines — the
    # chat_cards_api import and route registration only (no new
    # screen, no new table). Resumes from 1138.
    # P-14 (interactive widgets in Chat, 21 Nov 2026): +6 lines —
    # the chat_widgets_api import and route registration plus
    # the comment (same seam as the cards: no new screen, no
    # new table). Resumes from 1144.
    # Broken-rooms fix (22 Nov 2026): +10 lines on top of the 1183 the
    # tree already carried — the "rooms" screen in SCREENS, the
    # rooms_api route registration, and the docstring lines that
    # name it. The ratchet resumes from 1193.
    # Artifacts + run-logs browser (22 Nov 2026): +11 lines — the
    # "artifacts" screen in SCREENS, its agents context (shared
    # with replay), and the artifacts route registration.
    # Resumes from 1204.
    "puspa/pwa/app.py": 1204,
    # PWA typed API client: every screen's fetch wrapper plus
    # the generated-types aliases in one file. Crossed 1000
    # lines in P-10 (21 Nov 2026): the replay fetchers (tasks,
    # timeline, single step, GIF URL) and their three type
    # aliases added 51 lines to the 982-line client. Split
    # candidate when it grows again: one fetch module per
    # feature area (live/replay, rooms, morning), keeping the
    # type aliases in client.ts. The ratchet resumes from 1033.
    # P-11 (visual approval overlay, 21 Nov 2026): +51 lines —
    # the overlay fetchers (list, approve, reject) and their
    # four type aliases. Resumes from 1084.
    # P-12 (rich cards in Chat, 21 Nov 2026): +40 lines — the
    # card fetchers (post card, draft card, insights chart) and
    # their nine type aliases. Resumes from 1124.
    # P-14 (interactive widgets in Chat, 21 Nov 2026): +62 lines
    # — the widget fetchers (post widget, compute, submit,
    # simulate, price-compare, simulation) and their thirteen
    # type aliases (five widget kinds plus request/result
    # types). Split candidate when it grows again: one fetch
    # module per feature area, keeping the type aliases in
    # client.ts. The ratchet resumes from 1186.
    # NOTE (22 Nov 2026): the file already measured 1,481 lines
    # at this item's base — the 1186 baseline above is stale
    # against merged work, not against this item. This item
    # adds +46 lines only: the ArtifactSummary interface (21)
    # plus the fetchArtifacts + artifactDownloadUrl fetchers
    # (25). The ratchet resumes from 1527.
    "apps/pwa/src/api/client.ts": 1527,
    # Reuse audit of third-party code (license + scope notes per repo).
    "docs/reuse.md": 1220,
    # Shared-room delegation (B6): the delegation board, task
    # lifecycle, and approval-gated steps in one module. Crossed
    # 1000 lines in Wave 33 (B2): +41 lines threading the shared
    # B2 outcome writer through complete_task and the delegation
    # close path (optional ledger + agent_id params, docstrings).
    # No logic change beyond the B2 wiring. Split candidate when
    # it grows again: the approval-gated step runner into its own
    # module, or the board queries into a store module.
    "puspa/rooms/delegation.py": 1039,
    # Shared splitting / patungan (B11): split maths, partial
    # payments, and settlement preparation in one module. Crossed
    # 1000 lines in Wave 33 (B2): +31 lines routing the settlement
    # preparation through the shared B2 outcome writer as a
    # payment_prepared record (optional ledger + agent_id params,
    # docstrings). No logic change beyond the B2 wiring. Split
    # candidate when it grows again: the settlement preparation
    # into its own module.
    "puspa/rooms/splitting.py": 1031,
    # Phase status log, append-only by design; shrinks only by archiving
    # old sections into a dated archive doc, never by deleting history.
    "docs/status-brain-deepening.md": 1134,
    # Agent self-scan (policy): reads the repo and reports policy drift.
    # Re-baselined 18 Nov 2026: the CI lint adoption ran `ruff format`
    # over the whole tree and this file grew by 18 lines from mechanical
    # line-wrapping (no logic added). The ratchet resumes from 1070.
    "puspa/policy/agent_scan.py": 1070,
    # Sigstore verifier tests: grew past 1000 lines in the maturity phase
    # (real staging fixtures embedded as base64: message-signature bundle
    # plus DSSE bundle with binding tests). Fixtures are data, not logic;
    # split only if a second verifier test module earns its own file.
    # Re-baselined 18 Nov 2026: `ruff format` grew it by 23 lines.
    "tests/test_sigstore_verify.py": 1124,
    # OAuth translation test matrix (router): many provider cases in one
    # module. Split only if a second test module earns its own file.
    "tests/test_router_oauth_translate.py": 1123,
    # Maturity phase status log, append-only by design (same rule as
    # status-brain-deepening.md); shrinks only by archiving old
    # sections into a dated archive doc, never by deleting history.
    # On 18 Nov 2026 the live log crossed the 2000-line hard cap
    # (2051 lines) and Waves 1-8 + the CI lanes adoption were moved
    # to the archive below; the live log is now 859 lines, under the
    # soft cap, so its baseline entry was removed (per
    # test_baseline_entries_are_still_large_or_gone).
    # Archived maturity log (Waves 1-8 + CI lanes, 18 Nov 2026):
    # frozen, never appended to; the living log continues in
    # docs/status-maturity.md (Wave 9 onwards).
    "docs/status-maturity-archive-2026-11-18.md": 1204,
    # OpenAPI contract for every PWA route (G1). The spec is the
    # source of truth and IS counted; the generated client
    # (generated-types.ts) is excluded as generated output. The
    # spec grows as routes land: G31 (operator status) + G33
    # (model settings) pushed it past 1000 lines in Wave 16
    # (19 Nov 2026). Split only if a second spec file earns its
    # own contract.
    # Re-baselined 19 Nov 2026 (Wave 17, G28 wiring): 9 new
    # endpoints (GET /api/insights, the 4-step onboarding flow,
    # hints read + seen) plus the InsightsReport,
    # OnboardingStatus, FirstTaskResult and Hint schemas: +610
    # lines, all new API surface ordered by the maturity work.
    # The ratchet resumes from 1707.
    # Split 19 Nov 2026 (Wave 30 ratchet fix): the B9 rooms
    # contract (5 endpoints + 5 schemas) grew the file to 2,152,
    # past the 2000-line hard cap, so the rooms paths + schemas
    # moved to docs/openapi-rooms.yaml (461 lines, its own
    # contract) and the main spec keeps $ref stubs to it; the
    # generated client (openapi-typescript 7.13.0) resolves the
    # external refs, verified by regenerating. The main spec is
    # now 1,727 lines; the ratchet resumes from 1727.
    # Wave 31 (B11): +12 lines for the Split $ref stubs (3 paths
    # + 3 schemas: Split, SplitShare, PreparedSettlement) so the
    # generated client carries the split types; the full contract
    # stays in docs/openapi-rooms.yaml. The ratchet resumes from
    # 1739.
    # Wave 32 (B13 + B18): +46 lines of $ref stubs in the main
    # spec — 6 draft paths + 3 draft schemas (B13, contract in
    # docs/openapi-rooms.yaml) and 5 graph paths + 9 graph schemas
    # (B18, contract split out to docs/openapi-graph.yaml, 592
    # lines, Wave 30 precedent: the inline graph contract would
    # have pushed the main spec past the 2000-line hard cap).
    # The ratchet resumes from 1785.
    # B7 (mockup editor): +28 lines of $ref stubs in the main
    # spec — 14 mockup paths (contract split out to
    # docs/openapi-mockup.yaml, 551 lines, Wave 30 precedent: the
    # inline mockup contract would have pushed the main spec
    # past the 2000-line hard cap). The ratchet resumes from 1813.
    # B8 (agent payments, phase 1): +24 lines of $ref stubs in
    # the main spec — 7 payment paths + 5 payment schemas
    # (contract split out to docs/openapi-payments.yaml, 398
    # lines, Wave 30 precedent). The ratchet resumes from 1837.
    # E-14 (embedding health): +83 lines — the GET /health path
    # plus the EmbeddingHealth, EmbeddingBanner and HealthStatus
    # schemas and the embedding fields on OperatorStatus. The
    # ratchet resumes from 1920.
    # Split 20 Nov 2026 (P-03 ratchet fix): the share-target +
    # voice contract (6 endpoints + 2 schemas) grew the file to
    # 2,214, past the 2000-line hard cap, so the paths + schemas
    # moved to docs/openapi-voice.yaml (291 lines, its own
    # contract) and the main spec keeps $ref stubs to it; the
    # generated client (openapi-typescript 7.13.0) resolves the
    # external refs, verified by regenerating. What stays inline
    # in the main spec: the Bootstrap.shared_text field and the
    # Manifest.share_target property. The ratchet resumes from 1954.
    # P-02 (stop-all, autonomy dial, weekly report): +26 lines —
    # the `controls` tag (2) plus $ref stubs in the main spec —
    # 4 control paths + 8 control schemas (contract split out
    # to docs/openapi-controls.yaml, 359 lines, same split
    # precedent as P-03's voice contract). The ratchet resumes
    # from 1980.
    # P-04 (receipt photos, 20 Nov 2026): +22 lines of $ref stubs
    # (5 receipt paths + 6 receipt schemas, contract split out to
    # docs/openapi-receipts.yaml, 393 lines) pushed the merged spec
    # to 2002, 2 over the 2000-line hard cap — so the 18 HTML page
    # routes moved to docs/openapi-pages.yaml (272 lines, its own
    # contract; Error/Manifest/HealthStatus referenced back), and
    # the main spec keeps $ref stubs. Regenerated client types are
    # byte-identical before/after the split. The ratchet resumes
    # from 1783.
    # P-05 (shared rooms lite, 20 Nov 2026): +4 lines — the room_activity variant on SseEvent plus its $ref to the rooms file. The membership contract lives in docs/openapi-rooms-membership.yaml, not here. Resumes from 1787.
    # P-07 (morning summary + notes-to-actions, 20 Nov 2026): +38
    # lines — $ref stubs for the 6 morning paths + 13 morning
    # schemas. The contract itself lives in
    # docs/openapi-morning.yaml (521 lines) so the main spec stays
    # under the 2000-line hard cap. Resumes from 1825.
    # P-09 (live view + takeover, 20 Nov 2026): +24 lines — the
    # `live-view` tag (2), $ref stubs for 4 live-view paths + 7
    # live-view schemas (contract split out to
    # docs/openapi-live-view.yaml, same split precedent), and
    # "live" in the Bootstrap screen enum. Main spec 1849, still
    # under the 2000-line hard cap. Resumes from 1849.
    # P-10 (replay timeline, 21 Nov 2026): +16 lines — the
    # `replay` tag (2), $ref stubs for 4 replay paths + 3 replay
    # schemas (contract split out to docs/openapi-replay.yaml,
    # same split precedent), and "replay" in the Bootstrap
    # screen enum. Main spec 1865, still under the 2000-line
    # hard cap. Resumes from 1865.
    # P-11 (visual approval overlay, 21 Nov 2026): +24 lines —
    # the `approval-overlay` tag (2), $ref stubs for 3 overlay
    # paths + 8 overlay schemas (contract split out to
    # docs/openapi-approval-overlay.yaml). Main spec 1889, still
    # under the 2000-line hard cap. Resumes from 1889.
    # P-13 (task progress rail, 21 Nov 2026): +5 lines — the
    # `agents` list property on OfficeProjection, documenting
    # the shape the server actually sends (no new endpoint;
    # the rail reads the existing SSE office frame). Main spec
    # 1894, still under the 2000-line hard cap. Resumes from 1894.
    # P-12 (rich cards in Chat, 21 Nov 2026): +13 lines — the
    # `chat-cards` tag (2), $ref stubs for 3 card paths + the
    # ChatCard schema, and the optional `card` property on
    # ChatMessage (contract split out to
    # docs/openapi-chat-cards.yaml). Main spec 1907, still under
    # the 2000-line hard cap (93 lines of headroom left).
    # Resumes from 1907.
    # P-14 (interactive widgets in Chat, 21 Nov 2026): +19 lines
    # — the `chat-widgets` tag (2), $ref stubs for 6 widget
    # paths + the ChatWidget schema, and the optional `widget`
    # property on ChatMessage (contract split out to
    # docs/openapi-chat-widgets.yaml). Main spec 1926, still
    # under the 2000-line hard cap (74 lines of headroom left).
    # Resumes from 1926.
    # NOTE (22 Nov 2026): the file already measured 2,295 lines
    # at this item's base — the 1926 baseline above is stale
    # against merged work (the spec is already past the
    # 2000-line hard cap before this item), not against this
    # item. This item adds +8 lines only: the `artifacts` tag
    # (2), $ref stubs for 2 artifact paths + the ArtifactSummary
    # schema (6), and "artifacts" in the Bootstrap screen enum
    # (same line). The contract itself lives in
    # docs/openapi-artifacts.yaml. The ratchet resumes from 2303.
    "docs/openapi-pwa.yaml": 2303,
    # Shared-rooms API contract: grew past the soft cap with the
    # B13 draft contract (6 paths + 3 schemas on top of rooms /
    # delegation / splits), now 1,247 lines. It stays one file:
    # splitting it again per feature would scatter one API
    # surface; the ratchet resumes from 1247.
    # P-05 (shared rooms lite, 20 Nov 2026): +94 lines — the create-room POST, $ref stubs for the membership paths/schemas, and the RoomActivity schema. The membership contract itself split out to docs/openapi-rooms-membership.yaml (913 lines) so this file stays under the cap. Resumes from 1341.
    "docs/openapi-rooms.yaml": 1341,
    # Maturity phase status log: re-added 19 Nov 2026 at 1055
    # lines (regrown past the soft cap with the Waves 15-16
    # entries) and archived again the same day — Waves 9-13
    # moved to docs/status-maturity-archive-2026-11-19.md
    # (580 lines, under the soft cap) when the live log hit
    # 1060 lines. The live log is now 819 lines (Wave 24
    # onwards; Waves 22-23 archived to
    # docs/status-maturity-archive-2026-11-19-waves-22-23.md
    # when it hit 1,051 lines with the Wave 28 K12 entry),
    # under the soft cap, so this entry is removed per
    # test_baseline_entries_are_still_large_or_gone.
    # Same rule as before: shrinks only by archiving old
    # sections into a dated archive doc, never by deleting history.
    # (No "docs/status-maturity.md" entry while it stays under
    # 1000 lines; re-add it with a reason if it ever crosses.)
    # 20 Nov 2026: the live log crossed the cap again (1026 lines
    # after the Wave 38 entry), so Waves 35-36 were archived to
    # docs/status-maturity-archive-2026-11-20-waves-35-36.md
    # (105 lines); the live log is now 925 lines, still under
    # the cap, so no entry is added.
    # 20 Nov 2026 (Wave 45): the live log neared the cap again
    # (964 lines), so Wave 28 was archived verbatim to
    # docs/status-maturity-archive-2026-11-20-waves-28.md
    # (280 lines); the live log is now 692 lines, still under
    # the cap, so no entry is added.
}

#: Puspa-owned top-level directories covered by the ratchet.
SCOPED_DIRS = ("puspa", "tests", "apps", "cli", "web", "docs", "infra", "demo")

#: File extensions counted (text source and docs only; binaries excluded).
COUNTED_SUFFIXES = {".py", ".md", ".sql", ".yml", ".yaml", ".json", ".ts", ".tsx", ".js"}

#: Directory names never descended into.
SKIPPED_DIRS = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "nanobot",
    "web-replica",
    "backups",
    "cassettes",
    "node_modules",
    "dist",
}

#: Generated lockfiles: committed with the code they lock, but not
#: hand-written source, so the ratchet does not count their lines.
SKIPPED_FILES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    # Committed schema snapshot (E-08): `pg_dump -s` output, regenerated
    # from the migrations with one command (see its header). Generated
    # output, not hand-written source — it is 6000+ lines by nature.
    "schema_snapshot.sql",
}

#: Generated output (e.g. TypeScript types from the OpenAPI spec):
#: reproducible from a counted source file, so not counted itself.
GENERATED_PREFIX = "generated-"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _line_count(path: Path) -> int:
    return len(path.read_bytes().splitlines())


def _scoped_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for top in SCOPED_DIRS:
        top_dir = root / top
        if not top_dir.is_dir():
            continue
        for path in sorted(top_dir.rglob("*")):
            if not path.is_file():
                continue
            if any(part in SKIPPED_DIRS for part in path.relative_to(root).parts):
                continue
            if path.name in SKIPPED_FILES:
                continue
            if path.name.startswith(GENERATED_PREFIX):
                continue
            if path.suffix not in COUNTED_SUFFIXES:
                continue
            files.append(path)
    # Root-level text files (README, NOTICE, helper scripts).
    for path in sorted(root.iterdir()):
        if (
            path.is_file()
            and path.name not in SKIPPED_FILES
            and not path.name.startswith(GENERATED_PREFIX)
            and path.suffix in COUNTED_SUFFIXES
        ):
            files.append(path)
    return files


def test_no_file_over_hard_cap() -> None:
    """No Puspa-owned file exceeds 2000 lines."""
    root = _repo_root()
    violations = []
    for path in _scoped_files(root):
        count = _line_count(path)
        if count > HARD_MAX_LINES:
            violations.append(f"{path.relative_to(root)}: {count} lines")
    assert not violations, (
        "Files over the 2000-line hard cap (split the file, do not raise "
        "the cap without a recorded decision):\n" + "\n".join(violations)
    )


def test_files_over_soft_cap_only_shrink() -> None:
    """Files over 1000 lines are in BASELINE and never grow past it."""
    root = _repo_root()
    violations = []
    for path in _scoped_files(root):
        rel = str(path.relative_to(root))
        count = _line_count(path)
        if count <= SOFT_MAX_LINES:
            continue
        if rel not in BASELINE_LINES:
            violations.append(
                f"{rel}: {count} lines, not in BASELINE_LINES — record it "
                "with a reason (or split the file) instead of growing it silently"
            )
        elif count > BASELINE_LINES[rel]:
            violations.append(
                f"{rel}: {count} lines, above its baseline of "
                f"{BASELINE_LINES[rel]} — files over 1000 lines may only shrink"
            )
    assert not violations, "\n".join(violations)


def test_baseline_entries_are_still_large_or_gone() -> None:
    """Baseline entries that dropped to 1000 lines or fewer must be removed.

    A stale baseline entry hides the fact that the file is small again and
    lets a later regrowth pass unnoticed.
    """
    root = _repo_root()
    stale = []
    for rel, baseline in BASELINE_LINES.items():
        path = root / rel
        if not path.is_file():
            continue
        if _line_count(path) <= SOFT_MAX_LINES:
            stale.append(
                f"{rel}: now {_line_count(path)} lines (baseline {baseline}) — "
                "remove it from BASELINE_LINES"
            )
    assert not stale, "\n".join(stale)
