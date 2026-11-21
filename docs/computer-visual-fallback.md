# Visual fallback: screenshot + OCR + web bridge (V2-32)

When the V2-31 accessibility tree is empty or too poor to act on,
the agent falls back to seeing the screen. This document describes
the fallback, the web bridge, and what is still fake on this VM.

## When the fallback takes over

`tree_is_poor(snapshot)` in `puspa/computer/vision.py` decides.
A tree is poor when it has fewer than 2 nodes (a bare root, or
nothing at all) or when no element anywhere offers an action, so
there is nothing to click by ref. A small tree with a clickable
element is NOT poor: that is a missing element, not a missing
tree, and `VisualFallback.complete_task` refuses it (use
`DesktopHands.click` by ref instead).

The pattern follows agent-desktop (Apache-2.0, v1.2.0;
attribution in `REUSE.md` / `NOTICE.md`), rewritten as Puspa
code, never verbatim. The visual part is the same observe-act-
verify loop as the core, only the eyes are different.

## The loop

1. **Screenshot.** `ScreenshotBackend.capture()` returns PNG
   bytes. The fake backend returns canned bytes (tests, demos).
   The real Linux backend reads the root window through libX11
   `XGetImage` via ctypes (no scrot, no gnome-screenshot, no
   ImageMagick: none is installed on this VM). It needs a running
   X server (`DISPLAY` set). It is verified live under Xvfb on
   this VM: a red rectangle drawn in a test window reads back as
   red pixels at the right place.
2. **OCR.** `OcrBackend.read(png)` returns words with boxes. The
   fake backend returns the words it was built with. The real
   backend shells out to the `tesseract` CLI (`tesseract stdin
   stdout --psm 11 tsv`) and parses the TSV. Tesseract is NOT
   installed on this VM, so the real OCR path fails closed with
   a plain error; only the TSV parsing is unit-tested here.
3. **Ground.** `ground_text(words, query)` finds the words that
   spell the query (a phrase across neighbouring words, an exact
   word, then a substring) and returns their union box as a
   `VisualElement` with a centre pixel. Case-insensitive;
   nothing is guessed when the text is not on screen.
4. **Click by coordinates (gated).** `VisualFallback.click_text`
   clicks the centre pixel, then re-reads the screen to verify.
   `expect` is `{"text": ...}` (this text must be on screen
   afterwards), `{"absent_text": ...}` (it must be gone), or
   None (any change in the screen words counts). An unchanged
   screen is `performed=true, verified=false`, exactly like a
   V2-31 ref click that changed nothing.
5. **Vision models.** `VisionModelBackend` is a seam for later,
   not a working model call: no vision model is wired up, and
   `UnavailableVisionBackend` says so plainly. OCR text
   grounding is the verifiable core of this item.

## Permission: coordinate clicks are riskier

A coordinate click goes through the same double gate as a ref
click (token check, then the user prompt), but with its own
token tool name, `computer.click_at`. A token that only grants
`computer.click` does NOT grant a coordinate click (unknown
tools deny by default), and the prompt shows the exact
coordinates. There is no bypass flag. The web bridge uses its
own name the same way, `computer.web_click`.

## Coordinate input: not built here

There is NO working real coordinate clicker on this VM. Linux
coordinate input needs libXtst (XTest) or xdotool, and neither
is installed, so `XTestClicker` fails closed with a plain error
naming what is missing. The visual path can see and locate live
(X11 screenshot works under Xvfb; tesseract works once
installed), but it cannot press a pixel until a real input path
exists. Installing `libxtst6` unblocks the `XTestClicker`
implementation, which is a small follow-up, not a redesign.

## The web bridge

`puspa/computer/web.py` routes a web target to browser
automation instead of the desktop. `is_web_target` accepts an
`http://` / `https://` URL or a `web:` prefix; anything else is
a desktop target.

- **Real backend:** `PlaywrightWebBackend`, Playwright plus
  headless Chromium. Playwright is already a Puspa dependency
  and the pricing reader (`puspa/pricing/readers.py`) already
  launches Chromium the same way, including the
  `PUSPA_CHROMIUM_EXECUTABLE` override. Text is found through
  the DOM first (exact bounding boxes, no OCR needed on the
  web); when the DOM cannot find it, the page is screenshotted
  and OCR-read through the OCR seam. Clicks go through the
  `computer.web_click` gate and verify by re-reading the page.
  Verified live on this VM against a local page (no network):
  open, find a button, click it, the re-read sees the button
  change.
- **Fake backend:** `FakeWebBackend`, an in-memory page with
  elements and `after_click` effects, for tests and demos.

## CLI

```console
puspa computer --backend fake visual locate Save
puspa computer --backend fake visual click Save --expect-text Saved
puspa computer --backend fake web open https://example.com
puspa computer --backend fake web find https://example.com Save
puspa computer --backend fake web click https://example.com Save --expect-text Saved
```

`--backend auto` (the default) uses the real screenshot, OCR,
and clicker backends and fails closed when any of them is
missing here; it never silently falls back to fake. The fake
visual demo is a "Save" button that turns into "Saved"; the
fake web demo is `https://example.com` with the same button.
Both verify by re-reading, so the demo click reports
`verified=true`.

## MCP tools

Four tools join the three V2-31 tools (seven in total):
`computer_visual_locate`, `computer_visual_click`,
`computer_web_open`, `computer_web_click`. They dispatch
through `handle_tool` with a `VisualFallback` / `WebBridge`
passed as `fallback` / `web`; called without one, they raise a
plain error instead of crashing. The `puspa mcp` server itself
still does not exist (V2-37 work).

## Honest limits

- No live desktop exists on this VM (no display, no AT-SPI
  bus), so the X11 screenshot backend was only run against a
  test window under Xvfb, never against a real application.
  Tesseract is not installed, so real OCR was never run here.
  libXtst is not installed, so no real coordinate click was
  ever pressed here.
- The vision-model path is a seam with nothing behind it.
- Wayland has no screenshot backend here (the X11 backend needs
  an X server); a portal-based backend is later work.
- Refs are still stable inside one snapshot only; cross-
  snapshot stability is V2-34 work. Typing, scrolling, window
  management, and the audit log are V2-33 and V2-35. Screen-
  text safety screening is V2-36.
