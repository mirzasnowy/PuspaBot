# Visual approval overlay (P-11, plan section 12.10)

The visual layer over the G4 approval gate: before the bot clicks
or acts, the element it is about to act on is highlighted in the
live view, and the user approves or rejects that one action.
Approved actions run; rejected ones are cancelled and recorded.
This is a layer over the gate, not a new gate: the taxonomy
(`puspa/policy/approvals.py`), the tiered answers, and the gate's
content-hash binding all stay where they are.

## What it is

- **Propose.** When the bot is about to take an action that needs
  approval, the action is proposed with its target element: the
  button it is about to click (`button_label`) or the tool it is
  about to run (`tool_name`), plus the server-rendered approval
  card (structured arguments only — never a model narrative).
  The proposal is recorded in the ledger as `approval_requested`.
- **Highlight.** The overlay resolves a highlight against the
  P-09 live frame: the frame's last tool call (when it is this
  action's tool), the open task (when it is this action's task),
  or the matching `approval_requested` ledger event — or `none`
  when the target is not visible in the current frame yet. The
  Live screen marks exactly that frame row with an outline, and
  the overlay card shows the target element in a highlighted box.
- **Decide, per action.** Approve records `approval_granted` in
  the ledger; reject records `approval_rejected` with the reason.
  A decided overlay cannot be decided again (409).
- **Cancellation is real.** When the store is constructed with
  the turn loop's `Gate`, a rejection marks the gate request
  rejected (`Gate.reject`, added by P-11 as the complement of
  `Gate.approve`), so `Gate.execute` can never run it afterwards
  — and a rejected request cannot be approved later either.
  The same call path is covered by tests with a real gate.

## What it is not (honest limits)

- **No screenshot, no pixel highlight.** Puspa has no real
  desktop to watch (P-09): the frame is built from ledger
  events, and the overlay highlights a *row* of that frame, not
  a position on a screen image. `screenshot` stays null. A real
  desktop/backend highlight needs the `FrameSource` capture seam
  P-09 left open, and is out of scope here.
- **The PWA holds no gate.** The PWA process has no `Gate`
  instance (same as `app.state.approval`), so a rejection made
  in the PWA is the ledger event plus the overlay status; the
  gate-level cancellation lands when the store is constructed
  with the turn loop's gate. The two surfaces (the tasks-screen
  card and the live overlay) are independent today.
- **Overlay state is in-memory.** Like the gate's requests and
  `app.state.approval`, pending overlays do not survive a
  process restart. The ledger events remain, and a pending
  approval whose process died must be re-proposed. Durability
  lives in the ledger, not in the store.
- **No migration.** The ledger event types used
  (`approval_requested`, `approval_granted`,
  `approval_rejected`) already exist in the closed set, so P-11
  adds no migration — and no numbering collision with the
  parallel P-10 work.

## Files

- `puspa/pwa/approval_overlay.py` — the overlay model + store
  (`OverlayStore`, `TargetElement`, `OverlayHighlight`,
  `ApprovalOverlay`, `highlight_for`).
- `puspa/pwa/approval_overlay_api.py` — the four endpoints under
  `/api/live-view/{agent_id}/approvals` (list, propose, approve,
  reject), mounted by `puspa/pwa/app.py` next to the live-view
  routes.
- `puspa/policy/gate.py` — `Gate.reject` only (the approve
  complement); nothing else in the gate changed.
- `docs/openapi-approval-overlay.yaml` — the contract; the main
  spec keeps `$ref` stubs (`docs/openapi-pwa.yaml`).
- `apps/pwa/src/screens/ApprovalOverlay.tsx` — the overlay card;
  `LiveViewScreen.tsx` renders it over the live frame and marks
  the highlighted row.
- Tests: `tests/test_approval_overlay.py`,
  `tests/test_approval_overlay_api.py`,
  `apps/pwa/src/screens/ApprovalOverlay.test.tsx`,
  plus the overlay cases in `LiveViewScreen.test.tsx` and
  `apps/pwa/src/api/client.test.ts`.
