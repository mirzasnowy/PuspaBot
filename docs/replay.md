# Replay timeline (replay timeline, plan section 12.10)

## What it is

One screen (`/replay` in the PWA): a task's steps played back
like a video.

1. **Timeline** — every ledger event of one task, oldest
   first. Each step shows what the bot did (event type, tool),
   *why* (derived from the event itself), and *what came of
   it* (the result the event recorded, when there is one).
2. **Play + scrub** — Play advances one step per 800 ms (the
   same length as one GIF frame); the slider and the step
   buttons jump to any step, forward or back. Play stops at
   the last step; the button then offers "Putar ulang" from
   step 0.
3. **GIF export** — `GET /api/replay/{agent_id}/{task_id}/export.gif`
   downloads the whole timeline as an animated GIF, one frame
   per step, looping. The export is deterministic: the same
   timeline always exports the same bytes.
4. **Chain check** — the timeline verifies the task's hash
   chain before playing and reports `chain_ok` plus the broken
   event ids. A replay of a tampered task still plays, but the
   break is shown, not hidden.

## What it is not (honest limits)

- **No real screenshot.** PuspaBot has no desktop/browser/CDP to
  watch, so the `screenshot` of every step stays `null` and
  the GIF frames are *rendered* from the step's own data (step
  number, event type, time, tool, reason, result) on a fixed
  480x270 canvas with Pillow. The screen and every GIF frame
  say so in plain words instead of faking a desktop recording.
  When a backend that can produce frames lands, it implements
  the `FrameSource` capture seam (`puspa.pwa.live_view`) and
  the steps gain real screenshots without changing this API
  shape.
- **No invented reasons.** Reason and result come from the
  stored event content only. A `tool_called` step has no
  result yet — the result arrives in the later
  `tool_completed` step — and a step whose event recorded no
  reason says so plainly.
- **One task per replay, 500 steps max.** Longer tasks are
  refused with 400 (the count is named in the error) instead
  of silently truncating a replay and breaking its chain.

## How it works

- `puspa/pwa/replay.py` — `build_replay` reads the task's
  ledger events (oldest first), derives each step's reason
  and result, verifies the hash chain, and derives the task
  status from the last event (`completed` / `failed` /
  `stopped`, else `running`). `list_tasks_for_agent` is the
  task picker: one row per task with step count and times,
  newest first. `render_step_frame` draws one step as a PIL
  image; `export_replay_gif` encodes the frames as an
  animated GIF (800 ms per frame, looping).
- `puspa/pwa/replay_api.py` — the four endpoints, mounted in
  `create_app` next to the live-view routes, with the app's own
  login resolver. Route order matters: `/tasks` is registered
  before the bare `/{task_id}` route, so the literal segment
  wins over the task-id parameter.
- `apps/pwa/src/screens/ReplayScreen.tsx` — agent picker,
  task picker, play/pause, slider, step buttons, per-step
  panel, GIF download link. The screen is the 13th PWA
  screen (`tests/test_pwa.py` pins the count on purpose).
- `docs/openapi-replay.yaml` — the contract (4 endpoints + 3
  schemas), split out of `docs/openapi-pwa.yaml` like the
  live-view contract; the main spec keeps `$ref` stubs.

## Ownership

Every function takes the requesting `user_id` and refuses
with 403 when the agent is not theirs. A task whose events
belong to another agent reads as 404, never as someone
else's steps — the replay never leaks across agents.
