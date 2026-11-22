# Browser and web tools (COW-07)

The agent can drive a real browser and search the web, through five
registry tools in `puspa/tools/browser.py`. Pattern adapted from
CowAgent's built-in browser and web-search tools (MIT, rewritten as
PuspaBot code, never copied verbatim).

## The tools

| Tool | What it does | Gated? |
| --- | --- | --- |
| `browser_open` | Open a URL in headless Chromium, return title + visible text | No (read) |
| `browser_click` | Click text on the open page, re-read to verify | Yes (asks first) |
| `browser_fill` | Type a value into a form field, read it back to verify | Yes (asks first) |
| `web_search` | Search the web, return title / URL / snippet rows | No (read) |
| `web_fetch` | Fetch one URL over plain HTTP, return readable text | No (read) |

## Browser: the existing bridge, not a second driver

The three `browser_*` tools run through the computer web bridge
(`puspa/computer/web.py`, V2-32), which already drives headless
Chromium through Playwright. One `BrowserTools` keeps one bridge,
so open, click and fill share the same page. Clicks and fills go
through the bridge's own double gate (token check + user prompt,
tools `computer.web_click` / `computer.web_fill`); the typed value
is never written to the audit log, only its length.

The bridge gained two methods for this item: `read_text()` (the
open page's visible text) and `fill(selector, value)` (type into a
form field, return the value read back). Both backends implement
them: Playwright through the DOM, the fake backend through its
page spec (`fields` with `selector` / `label` / `value`).

## Web search: no key, no new dependency

`web_search` queries the DuckDuckGo HTML endpoint
(`https://html.duckduckgo.com/html/`) with httpx and parses the
results with the standard library. No API key, no account, no paid
service. The endpoint is overridable (`search_base_url`) so tests
point it at a local fixture server.

## Web fetch: plain HTTP, honest failures

`web_fetch` is a plain httpx GET (redirects followed, 15s timeout,
1 MB body cap) reduced to readable text. It fails visibly, never
with an empty success, when:

- the server answers a non-200 status;
- the response is not a text page (e.g. an image);
- the page needs JavaScript to show its content. The error names
  `browser_open` as the tool to use instead.

## Wiring

- `register_browser_tools(registry)` registers all five tools
  (idempotent). `register_builtin_tools()` calls it, so `discover`
  picks them up like the other builtin tools.
- Exposure is opt-in through the `web` toolset preset
  (`puspa/tools/toolsets.py`), which inherits the default toolset.
  The default toolset does not gain them: `browser_click` and
  `browser_fill` act on live pages.
- The turn loop calls them like any registry tool: pass the
  registry's `handle_function_call` as `tool_call` and the
  definitions as `tool_catalog` (see `tests/test_browser_tools.py`
  for a full run through `TurnLoop`).

## Split note (D-08)

This is the LOCAL side only. The browser runs on the user's own
machine. Hosted execution of these tools (running the browser on
Puspa's servers for hosted users) lives in the private repo and is
not built here.
