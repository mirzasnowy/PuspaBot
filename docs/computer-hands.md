# Desktop hands (V2-31, core only)

Puspa's hands on the local desktop: read the accessibility tree
as a JSON snapshot, click one element by ref, and verify the
result by re-reading the screen. Linux first (AT-SPI). Windows
and macOS are not built here; the backend seam raises for them.

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
  (`computer_snapshot`, `computer_click`, `computer_verify`)
  and `handle_tool` dispatch.
- `puspa/computer/cli.py`: the `puspa computer` command group.
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
```

`--backend auto` (the default) uses the real AT-SPI desktop and
fails closed with a plain error when there is no desktop; it
never silently falls back to the fake tree. `click` and `verify`
exit non-zero when the result is not verified.

## MCP

`puspa/computer/mcp.py` defines three tools
(`computer_snapshot`, `computer_click`, `computer_verify`) with
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
- **Refs are snapshot-local.** A ref from one snapshot may point
  at a different element (or nothing) after the next snapshot.
  Always re-snapshot before acting; V2-34 owns cross-snapshot
  ref stability.
- **Click only.** Typing, scrolling, window management, and
  screenshots are separate follow-ups (V2-32, V2-33). An element
  with no `click` action reports `performed=false`.
- **No audit log or emergency stop yet.** Those are V2-35. The
  prompt is the stop for now: deny, and nothing runs.
- **Screen-text safety is V2-36.** Snapshots return raw screen
  text (window titles, labels) with no redaction.
- **Local machine only.** On the user's own machine this drives
  the user's apps; on hosted it drives the bot's own computer,
  never the user's desktop.
