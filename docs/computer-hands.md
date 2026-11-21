# Desktop hands (V2-31 core, V2-33 full input + windows, V2-35 tiers)

Puspa's hands on the local desktop: read the accessibility tree
as a JSON snapshot, click one element by ref, and verify the
result by re-reading the screen. Linux first (AT-SPI). Windows
and macOS are not built here; the backend seam raises for them.

V2-33 adds the rest of the input surface on the same loop:
typing, keyboard shortcuts, scroll, drag, clipboard read/write,
and window management (list windows, open app, focus window,
switch between apps). See "Full input (V2-33)" and "Windows
(V2-33)" below.

## The loop

The mechanism follows the agent-desktop pattern (Apache-2.0,
v1.2.0; attribution in `REUSE.md` / `NOTICE.md`), rewritten as
Puspa code, never verbatim. There is no Rust fork; everything is
Python.

1. **Snapshot.** `puspa computer snapshot` reads the tree. Every
   element gets a ref (`n1`, `n2`, ...) that is stable inside
   that one snapshot, plus a path of child indices from the
   desktop root so the backend can find the same live element
   again. Refs do NOT stay valid across snapshots; that is V2-34
   work.
2. **Skeleton first.** Snapshots are skeletons by default: the
   root plus two levels stay expanded, deeper subtrees are
   collapsed (they still report `child_count`). That is what
   keeps a huge Electron tree small enough to read. Drill into
   one node with a full snapshot (`--full`) or
   `DesktopHands.drill(ref)`.
3. **Click.** `puspa computer click <ref>` resolves the ref in a
   fresh snapshot, asks the user first (see Permission below),
   runs the element's own `click` action through AT-SPI, then
   re-reads the screen.
4. **Verify.** A successful click NEVER proves the UI changed,
   so `click` only sets `verified=true` when the re-read shows
   a change: the element changed name or state, matched the
   `--expect-*` given, or is gone when `--expect-absent` was
   given. A click that ran but left the screen unchanged comes
   back `performed=true, verified=false`. `verify` re-reads one
   ref without clicking anything.

Headless by default: the backend never moves the mouse and never
steals focus. A click runs the element's own action through
AT-SPI, the same way a screen reader would activate it.

## Permission

Every click is gated twice, under Puspa's existing permission
model; nothing is invented here.

- **Token check.** When a `Gate` and a token payload are
  supplied, `Gate.authorize_tool(payload, "computer.click")`
  must not be `deny`. Unknown tools are `deny` by default under
  `puspa/policy/tokens.py`, so a token that never mentions
  desktop hands cannot click.
- **User prompt.** The user is asked first, on the Claude Code
  model from `puspa/localexec`: approve once, approve for the
  session, or deny. A denial raises `PermissionDenied` and
  nothing touches the desktop. There is no `--yes` bypass.

Snapshots and verifies are reads and need no prompt. What they
return is screen text; screening that text is V2-36 work, not
this module's.

## Files

- `puspa/computer/tree.py`: `AccessibleNode`, `Snapshot`,
  refs, skeletonize, JSON shape.
- `puspa/computer/backend.py`: the `AccessibilityBackend`
  seam, `FakeBackend` (in-memory tree for tests and demos),
  `AtspiBackend` (real Linux backend: ctypes against the system
  `libatspi`, no pyatspi, no dbus-python), `make_backend`.
- `puspa/computer/hands.py`: `DesktopHands`: snapshot, drill,
  click, verify, plus the two permission gates.
- `puspa/computer/mcp.py`: MCP tool definitions
  (`computer_snapshot`, `computer_click`, `computer_verify`,
  plus the ten V2-33 tools) and `handle_tool` dispatch.
- `puspa/computer/cli.py`: the `puspa computer` command group.
- `puspa/computer/input.py` (V2-33): the `InputBackend` seam,
  `FakeInputBackend` (in-memory input over the shared fake
  tree), `XTestInputBackend` (real Linux: AT-SPI editable text
  for typing, XTest for keys/scroll/drag, xclip/xsel or
  wl-copy/wl-paste for the clipboard), `make_input_backend`.
- `puspa/computer/windows.py` (V2-33): the `WindowBackend`
  seam, `FakeWindowBackend` (windows from the shared fake tree
  spec), `AtspiWindowBackend` (real Linux: list/focus through
  AT-SPI, open through the session launcher),
  `make_window_backend`, `WindowInfo` / `WindowResult`.
- `puspa/cli.py`: wires `computer` into the top-level parser.

No `puspa/computer/local.py` existed at this base, so the module
was built from scratch here instead of extending one. The local
runner it stands next to is `puspa/localexec` (subprocess
commands, not the desktop).

## CLI

```sh
puspa computer snapshot                  # skeleton JSON of the tree
puspa computer snapshot --full           # the whole tree (can be huge)
puspa computer snapshot --backend fake   # demo tree, no desktop needed
puspa computer click n4 --expect-state checked
puspa computer verify n4 --expect-state checked
puspa computer type n5 "hello" --expect-value-contains hello
puspa computer shortcut ctrl+a --ref n5 --expect-state selected
puspa computer scroll n5 --direction down --amount 2
puspa computer drag n7 n8
puspa computer clipboard write "hello"   # reports only the length
puspa computer clipboard read            # reports only the length
puspa computer windows                   # id, app, title, focused
puspa computer open-app gedit
puspa computer focus-window 0.1
puspa computer switch-window
puspa computer close-window 0.1          # destructive: always asks
puspa computer task steps.json           # multi-step task, see below
puspa computer allow                     # list allowlisted apps
puspa computer allow Notes               # allowlist one app
puspa computer allow --revoke Notes      # take it off the list
puspa computer stop                      # halt the hands now
puspa computer stop --status             # show the stop flag
puspa computer stop --clear              # let the hands run again
puspa computer audit --limit 20          # newest ledger entries
```

`--backend auto` (the default) uses the real AT-SPI desktop and
fails closed with a plain error when there is no desktop; it
never silently falls back to the fake tree. `click` and `verify`
exit non-zero when the result is not verified. The same goes
for every V2-33 action: an action that ran but left the screen
unchanged exits non-zero (`performed=true, verified=false`).

## Full input (V2-33)

Every input action follows the V2-31 loop: fresh snapshot,
resolve the ref, token check, user prompt, backend action,
fresh snapshot, verify. The permission model has one tool name
per action (`computer.type`, `computer.shortcut`,
`computer.scroll`, `computer.drag`, `computer.clipboard_read`,
`computer.clipboard_write`, `computer.open_app`,
`computer.focus_window`, `computer.switch_window`), so a token
can allow typing but not window switching. One approve-for-
session answer covers every computer action.

- **Type.** `type_text(ref, text)` types into an editable
  element. The re-read checks its `value` (the text the element
  holds, new in V2-33: `AccessibleNode.value`, read through the
  AT-SPI text interface on the real backend). Expect with
  `--expect-value` or `--expect-value-contains`. Typing into a
  non-editable element reports `performed=false`.
- **Shortcut.** `shortcut("ctrl+a", ref)` presses a chord
  (keys joined with `+`). Without a ref it targets the focused
  element (the deepest focused node in the tree). A chord with
  no effect on the target reports `performed=false`.
- **Scroll.** `scroll(ref, direction, amount)` scrolls
  up/down/left/right. A scroll is only `verified=true` when the
  re-read shows a visible change or matches the given expect:
  scrolling moves a viewport, and only the screen can say what
  it revealed, so a scroll that changes nothing visible is
  honestly reported as unverified.
- **Drag.** `drag(ref_from, ref_to)` drags one element onto
  another; the re-read checks the drop target. A source with no
  drag effect reports `performed=false`.
- **Clipboard.** `clipboard_read()` returns the content in
  memory only; `clipboard_write(text)` writes, then reads back
  and compares in memory to verify. Clipboard content is never
  written to a log, a file, or stdout, and never appears in a
  result dict: results report only the length. The permission
  prompt for a type or a clipboard write shows the length of
  the text, never the text itself. The MCP
  `computer_clipboard_read` result is the one place the content
  goes: to the calling agent, which is the point of the tool.
- **Multi-step tasks.** `DesktopHands.run_task(steps)` runs a
  list of steps in order and stops at the first step that did
  not perform or did not verify, returning
  `{"steps": [...], "completed": bool}`. Step actions: `type`,
  `shortcut`, `scroll`, `drag`, `click`, `verify`,
  `clipboard_read`, `clipboard_write`, `open_app`,
  `focus_window`, `switch_window`, `list_windows`, `snapshot`.
  Each step carries that action's arguments plus an optional
  `expect`. Refs in a task are resolved fresh at each step, so
  a ref must still be valid when its step runs; steps that add
  or remove windows can shift refs (V2-34 owns ref stability).

## Windows (V2-33)

- **List.** `list_windows()` returns `WindowInfo` values (id,
  app, title, focused). A window id is its frame's path in the
  tree (`"0.1"`: first application, second frame). Listing is a
  read, like snapshot: no prompt, no token check.
- **Open app.** `open_app(name)` launches by executable name,
  falling back to `gtk-launch` for desktop entries, then
  re-reads the tree to verify a window for it appeared.
- **Focus.** `focus_window(id)` grabs focus on the live frame
  through AT-SPI and re-reads the window list to verify it is
  the focused one.
- **Switch.** `switch_window()` focuses the next window after
  the focused one, cycling. It reports `performed=false` when
  only one window is open.

## Tiers, allowlist, stop, audit (V2-35)

V2-31 asked the user before every action. V2-35 replaces that
with three tiers (`puspa/computer/tiers.py`):

- **READ, free.** Snapshot, verify, list windows, locate text
  by OCR, open a web page, find text on a page. They change
  nothing, so they never ask.
- **NORMAL, free inside an allowlisted app.** Clicks, typing,
  shortcuts, scroll, focus/switch window, open app. The user
  allowlists an app once (`puspa computer allow Notes`, the PWA
  panel on the Settings screen, or `computer_allowlist`) and the
  agent clicks freely inside it; outside one it still asks.
  The list is one JSON file
  (`~/.local/share/puspa/computer/allowlist.json`), so the CLI,
  the PWA server, and the MCP host read the same list.
  Clipboard read is NORMAL too: it returns the user's own data,
  so it asks.
- **DESTRUCTIVE, always asks.** Clipboard write, drag, typing
  into a field that submits (`type --submits`, or a `type` step
  with `"submits": true`), a shortcut holding Enter/Return, and
  closing a window (`puspa computer close-window 0.1`). The
  allowlist never covers these.

The token check from V2-31 still applies on top: a token
without the tool permission is refused whatever the tier says.

**Emergency stop.** `puspa computer stop` sets a flag file
(`~/.local/share/puspa/computer/stop.json`); every action
re-reads it before it runs, and a running `run_task` stops at
the next step boundary at the latest, reporting
`"stopped": true`. `puspa computer stop --clear` (or the PWA
panel, or `computer_stop`) lets the hands run again. The stop
needs no database and no desktop session: the flag must flip
even when everything else is down. It is computer-scoped, not
the global agent kill switch (which stops the whole agent and
is operator-only).

**Audit.** Every action is one `tool_called` event on the
`computer` task stream in the Puspa ledger (no second ledger):
what action, which target (a ref, window id, or app name),
which tier and decision, and the allow/deny/stop outcome, with
the event's own timestamp. Typed text and clipboard content
never appear, only their lengths. `puspa computer audit`
lists the newest entries; `computer_audit` is the MCP tool.
Stop transitions use the kill-switch precedent (`task_stopped`
for a request, `user_message` for a clear). Writing is
best-effort: when no database is reachable the action still
runs and the write is skipped.

## MCP

`puspa/computer/mcp.py` defines twenty-two tools
(`computer_snapshot`, `computer_click`, `computer_verify`,
`computer_type`, `computer_shortcut`, `computer_scroll`,
`computer_drag`, `computer_clipboard_read`,
`computer_clipboard_write`, `computer_list_windows`,
`computer_open_app`, `computer_focus_window`,
`computer_switch_window`, `computer_run_task`, plus the four
V2-32 tools `computer_visual_locate`, `computer_visual_click`,
`computer_web_open`, `computer_web_click`, plus the four V2-35
tools `computer_close_window`, `computer_allowlist`,
`computer_stop`, `computer_audit`) with
`name` / `description` / `inputSchema`, plus `handle_tool` and a
validated server definition (`mcp_server_definition`, id
`puspa-computer`). The `puspa mcp` server itself does NOT exist
yet: that is V2-37. These definitions are ready for it and
nothing here claims a working server.

## Honest limits

- **No live desktop was available on the build VM.** There is
  no AT-SPI bus, no display, no pyatspi, and no dbus-python
  here. The real AT-SPI backend was never run against a real
  application: its tree walk and click path are written and
  type-checked, and only the fail-closed half is tested live
  (unavailable without a bus, refuses instead of guessing).
  The first live check needs a Linux desktop session. `libatspi`
  itself IS installed on the VM, and the ctypes symbol binding
  against it is tested.
- **What the VM actually has (checked 22 Nov 2026).**
  `libatspi.so.0` present, including the text and editable-text
  symbols the typing path binds (`atspi_accessible_get_text`,
  `atspi_text_get_text`, `atspi_editable_text_insert_text`,
  `atspi_component_grab_focus`); `libX11.so.6` present
  (`XOpenDisplay`, `XKeysymToKeycode`, `XStringToKeysym`);
  `Xvfb` present (`/usr/bin/Xvfb`); `libXtst.so.6` MISSING, so
  the XTest key/scroll/drag path cannot even load here and
  fails closed with a plain "XTest libraries not found" error;
  no clipboard tool at all (`xdotool`, `ydotool`, `xclip`,
  `xsel`, `wl-copy`, `wmctrl` all absent), so the real
  clipboard backend fails closed too. The real input and window
  backends therefore have the same status as the V2-31 AT-SPI
  backend: written and type-checked, fail-closed half tested
  live, live half unverified. The first live check needs a
  Linux desktop session with libXtst and xclip (or wl-clipboard
  on Wayland) installed, plus an app to type into.
- **Refs are snapshot-local.** A ref from one snapshot may point
  at a different element (or nothing) after the next snapshot.
  Always re-snapshot before acting; V2-34 owns cross-snapshot
  ref stability.
- **Typing is AT-SPI editable text, not keystrokes.** On the
  real backend `type_text` inserts through the element's own
  editable-text interface, so it works on editable fields and
  refuses (returns False) on anything without one. It does not
  simulate typing key by key, and it cannot type into apps
  that do not expose editable text. Keys, scroll, and drag use
  XTest instead, which needs an X display and libXtst.
- **Scroll verification is honest, not generous.** A scroll
  that leaves the tree unchanged reports unverified, even
  though the scroll itself ran: only an expected change (or a
  visible one) can confirm a scroll did anything.
- **Screenshots are V2-32, not this item.** The multi-step
  demo in the tests runs end to end on the fake backends
  (type + shortcut + scroll + window switch + open app), and
  the same steps run through `puspa computer task` on the fake
  backend; nothing here claims a live desktop run.
- **Audit log and emergency stop are V2-35, done.** Every
  action is a `tool_called` ledger event (lengths only, never
  typed text or clipboard content); the stop flag halts the
  hands mid-task and is clearable from the CLI, the PWA, or
  MCP. The live half is still unverified: the real backends
  have never run against a real desktop on this VM.
- **Screen-text safety is V2-36.** Snapshots return raw screen
  text (window titles, labels, typed values) with no redaction.
  Clipboard content is the one exception by design: it never
  reaches a log, a file, or stdout, only the calling agent
  through `computer_clipboard_read`.
- **Local machine only.** On the user's own machine this drives
  the user's apps; on hosted it drives the bot's own computer,
  never the user's desktop.
