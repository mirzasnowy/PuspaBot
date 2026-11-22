"""Tests for the browser + web tools (COW-07).

Every network call here goes to a local fixture server on
127.0.0.1 (stdlib http.server, ephemeral port): no live network,
no API key, no external service. The browser half runs twice:
once against the fake web backend (fast, hermetic) and once
against real headless Chromium pointed at the same fixture
server (the live half, like test_computer_web.py's Playwright
tests). HTTP clients in the fixture tests are built with
``trust_env=False`` so an ambient egress proxy can never
redirect a localhost request.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from puspa.computer.web import FakeWebBackend, WebBridge
from puspa.localexec import PermissionDecision, PermissionDenied
from puspa.tools.browser import (
    BROWSER_TOOL_NAMES,
    BrowserTools,
    _html_to_text,
    _javascript_required,
    _parse_search_html,
    register_browser_tools,
)
from puspa.tools.registry import ToolRegistry, handle_function_call
from puspa.tools.toolsets import default_toolsets
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import ModelReply

pytestmark = pytest.mark.unit

APPROVE_ONCE = PermissionDecision.APPROVE_ONCE
DENY = PermissionDecision.DENY


class FakePrompt:
    """A prompt that answers from a queue and records every ask."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.asked = []

    def ask(self, command, cwd):
        self.asked.append(command)
        return self.decisions.pop(0)


# ----------------------------------------------------------------------
# Fixture server
# ----------------------------------------------------------------------

PAGE_HTML = """<html><head><title>Fixture Page</title></head><body>
<h1>Hello Fixture</h1>
<p>The quick brown fox jumps over the lazy dog.</p>
<a href="/page">A link to this page</a>
<form>
<label>Name <input id="name" name="name" value=""></label>
<label>Email <input id="email" name="email" type="email" value=""></label>
</form>
<button id="save" onclick="document.getElementById('status').textContent='Saved!'">Save</button>
<div id="status">Not saved</div>
</body></html>"""

SEARCH_HTML = """<html><body>
<div class="results">
<a class="result__a" href="https://example.com/first">  First Result </a>
<a class="result__snippet" href="https://example.com/first">First snippet
with a line break.</a>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Freal&amp;rut=abc">Second Result</a>
<a class="result__snippet" href="//duckduckgo.com/l/?uddg=x">Second snippet.</a>
</div>
</body></html>"""

JS_SHELL_HTML = """<html><head><title>App</title></head><body>
<div id="root"></div>
<noscript>This site requires JavaScript.</noscript>
<script src="/app.js"></script>
</body></html>"""

PLAIN_TEXT = "Just plain text here.\nSecond line."


class _FixtureHandler(BaseHTTPRequestHandler):
    """Serves the canned pages the browser + web tools fetch."""

    def log_message(self, *args):  # keep test output clean
        pass

    def _send(self, status, body, content_type):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from urllib.parse import urlsplit

        path = urlsplit(self.path).path
        if path == "/page":
            self._send(200, PAGE_HTML, "text/html; charset=utf-8")
        elif path == "/search":
            self._send(200, SEARCH_HTML, "text/html; charset=utf-8")
        elif path == "/js-shell":
            self._send(200, JS_SHELL_HTML, "text/html; charset=utf-8")
        elif path == "/plain":
            self._send(200, PLAIN_TEXT, "text/plain; charset=utf-8")
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/page")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/binary":
            self._send(200, b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")
        else:
            self._send(404, "<html><body>Not here.</body></html>", "text/html")


@pytest.fixture(scope="module")
def fixture_base():
    """Base URL of the local fixture server (one server per module)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture()
def tools(fixture_base):
    """BrowserTools pointed at the fixture server, with a fake
    browser backend. HTTP clients bypass any ambient proxy."""
    owned = BrowserTools(
        bridge=WebBridge(FakeWebBackend(FAKE_PAGES), prompt=FakePrompt((APPROVE_ONCE,))),
        search_client=httpx.Client(trust_env=False),
        fetch_client=httpx.Client(trust_env=False, follow_redirects=True),
        search_base_url=f"{fixture_base}/search",
    )
    yield owned
    owned.close()


FAKE_PAGES = {
    "https://example.com/form": {
        "title": "Form Page",
        "elements": [
            {
                "label": "Save",
                "x": 10,
                "y": 10,
                "width": 60,
                "height": 24,
                "after_click": {"label": "Saved"},
            },
            {"label": "Cancel", "x": 100, "y": 10, "width": 60, "height": 24},
        ],
        "fields": [
            {"selector": "#name", "label": "Name", "value": ""},
            {"selector": "#email", "label": "Email", "value": ""},
        ],
    }
}


def _payload(text):
    return json.loads(text)


# ----------------------------------------------------------------------
# Pure helpers: HTML text, JS detection, search parsing
# ----------------------------------------------------------------------


def test_html_to_text_drops_scripts_and_keeps_title():
    html = (
        "<html><head><title> My Title </title>"
        "<style>.x{display:none}</style></head>"
        "<body><script>var x = 1;</script><p>Hello <b>world</b></p></body></html>"
    )
    text, title = _html_to_text(html)
    assert title == "My Title"
    assert text == "Hello world"
    assert "var x" not in text
    assert "display:none" not in text


def test_javascript_required_phrase_and_shell():
    assert _javascript_required(JS_SHELL_HTML, "This site requires JavaScript.")
    shell_no_words = (
        '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    )
    assert _javascript_required(shell_no_words, "")
    assert not _javascript_required(
        '<html><body><p>Short page.</p><script src="/x.js"></script></body></html>',
        "Short page.",
    )
    long_article = "How to enable JavaScript in your browser. " + ("word " * 200)
    assert not _javascript_required("<html><body></body></html>", long_article)


def test_parse_search_html_unwraps_redirect_and_pairs_snippets():
    results = _parse_search_html(SEARCH_HTML, "https://html.duckduckgo.com/html/")
    assert len(results) == 2
    assert results[0] == {
        "title": "First Result",
        "url": "https://example.com/first",
        "snippet": "First snippet with a line break.",
    }
    assert results[1]["title"] == "Second Result"
    assert results[1]["url"] == "https://example.com/real"
    assert results[1]["snippet"] == "Second snippet."


def test_parse_search_html_empty_page():
    assert _parse_search_html("<html><body>No results.</body></html>", "https://x/") == []


# ----------------------------------------------------------------------
# web_fetch against the fixture server
# ----------------------------------------------------------------------


def test_web_fetch_plain_page(tools, fixture_base):
    payload = _payload(tools.web_fetch(f"{fixture_base}/page"))
    assert payload["status"] == "ok"
    assert payload["title"] == "Fixture Page"
    assert payload["final_url"] == f"{fixture_base}/page"
    assert "Hello Fixture" in payload["text"]
    assert "The quick brown fox" in payload["text"]
    assert payload["truncated"] is False


def test_web_fetch_follows_redirect(tools, fixture_base):
    payload = _payload(tools.web_fetch(f"{fixture_base}/redirect"))
    assert payload["status"] == "ok"
    assert payload["final_url"] == f"{fixture_base}/page"
    assert "Hello Fixture" in payload["text"]


def test_web_fetch_plain_text_page(tools, fixture_base):
    payload = _payload(tools.web_fetch(f"{fixture_base}/plain"))
    assert payload["status"] == "ok"
    assert "Just plain text here." in payload["text"]


def test_web_fetch_truncates_long_text(tools, fixture_base):
    payload = _payload(tools.web_fetch(f"{fixture_base}/page", max_chars=10))
    assert payload["status"] == "ok"
    assert payload["truncated"] is True
    assert len(payload["text"]) == 10


def test_web_fetch_404_is_a_clear_error(tools, fixture_base):
    with pytest.raises(ValueError, match="HTTP 404"):
        tools.web_fetch(f"{fixture_base}/notfound")


def test_web_fetch_binary_is_a_clear_error(tools, fixture_base):
    with pytest.raises(ValueError, match="not a text page"):
        tools.web_fetch(f"{fixture_base}/binary")


def test_web_fetch_js_shell_names_browser_open(tools, fixture_base):
    with pytest.raises(ValueError) as excinfo:
        tools.web_fetch(f"{fixture_base}/js-shell")
    message = str(excinfo.value)
    assert "JavaScript" in message
    assert "browser_open" in message


def test_web_fetch_rejects_non_http_url(tools):
    with pytest.raises(ValueError, match="not a usable web address"):
        tools.web_fetch("ftp://example.com/file.txt")


def test_web_fetch_bad_max_chars(tools, fixture_base):
    with pytest.raises(ValueError, match="max_chars must be positive"):
        tools.web_fetch(f"{fixture_base}/page", max_chars=0)


# ----------------------------------------------------------------------
# web_search against the fixture server
# ----------------------------------------------------------------------


def test_web_search_returns_parsed_results(tools):
    payload = _payload(tools.web_search("fixture query"))
    assert payload["status"] == "ok"
    assert payload["query"] == "fixture query"
    assert len(payload["results"]) == 2
    assert payload["results"][0]["title"] == "First Result"
    assert payload["results"][1]["url"] == "https://example.com/real"


def test_web_search_respects_max_results(tools):
    payload = _payload(tools.web_search("fixture query", max_results=1))
    assert len(payload["results"]) == 1


def test_web_search_empty_query_is_an_error(tools):
    with pytest.raises(ValueError, match="needs a query"):
        tools.web_search("   ")


def test_web_search_http_error_is_an_error(fixture_base):
    broken = BrowserTools(
        search_client=httpx.Client(trust_env=False),
        search_base_url=f"{fixture_base}/notfound",
    )
    try:
        with pytest.raises(ValueError, match="web search"):
            broken.web_search("anything")
    finally:
        broken.close()


# ----------------------------------------------------------------------
# browser_open / click / fill against the fake backend
# ----------------------------------------------------------------------


def test_browser_open_reads_fake_page_text(tools):
    payload = _payload(tools.browser_open("https://example.com/form"))
    assert payload["status"] == "ok"
    assert payload["url"] == "https://example.com/form"
    assert payload["title"] == "Form Page"
    assert "Save" in payload["text"]
    assert "Cancel" in payload["text"]


def test_browser_open_rejects_non_http_url(tools):
    with pytest.raises(ValueError, match="not a usable web address"):
        tools.browser_open("notes://local")


def test_browser_click_verifies_by_reread(tools):
    tools.browser_open("https://example.com/form")
    payload = _payload(tools.browser_click("Save", expect_text="Saved"))
    assert payload["status"] == "ok"
    assert payload["performed"] is True
    assert payload["verified"] is True
    assert payload["label"] == "Save"


def test_browser_click_missing_text_is_an_error(tools):
    tools.browser_open("https://example.com/form")
    with pytest.raises(ValueError, match="cannot locate"):
        tools.browser_click("Delete")


def test_browser_click_denied_is_an_error():
    denied = BrowserTools(
        bridge=WebBridge(FakeWebBackend(FAKE_PAGES), prompt=FakePrompt((DENY,))),
    )
    try:
        denied.browser_open("https://example.com/form")
        with pytest.raises(PermissionDenied):
            denied.browser_click("Save")
    finally:
        denied.close()


def test_browser_fill_sets_field_and_verifies(tools):
    tools.browser_open("https://example.com/form")
    payload = _payload(tools.browser_fill("#name", "Nero"))
    assert payload["status"] == "ok"
    assert payload["performed"] is True
    assert payload["verified"] is True
    assert payload["selector"] == "#name"
    assert payload["value"] == "Nero"


def test_browser_fill_by_label(tools):
    tools.browser_open("https://example.com/form")
    payload = _payload(tools.browser_fill("Email", "nero@example.com"))
    assert payload["verified"] is True


def test_browser_fill_missing_field_is_an_error(tools):
    tools.browser_open("https://example.com/form")
    with pytest.raises(ValueError, match="no form field"):
        tools.browser_fill("#missing", "x")


def test_browser_fill_empty_selector_is_an_error(tools):
    with pytest.raises(ValueError, match="needs a field selector"):
        tools.browser_fill("  ", "x")


# ----------------------------------------------------------------------
# Registry wiring + toolsets
# ----------------------------------------------------------------------


def test_register_browser_tools_idempotent():
    registry = ToolRegistry()
    first = register_browser_tools(registry)
    second = register_browser_tools(registry, tools=first)
    assert second is first
    assert registry.names() == sorted(BROWSER_TOOL_NAMES)
    for name in BROWSER_TOOL_NAMES:
        schema = registry.get(name).to_schema()["function"]
        assert schema["name"] == name
        assert schema["description"]
    first.close()


def test_register_browser_tools_dispatches_through_registry(tools, fixture_base):
    registry = ToolRegistry()
    register_browser_tools(registry, tools=tools)
    payload = _payload(
        handle_function_call("web_fetch", {"url": f"{fixture_base}/page"}, registry=registry)
    )
    assert payload["status"] == "ok"
    assert "Hello Fixture" in payload["text"]
    missing = _payload(handle_function_call("web_fetch", {}, registry=registry))
    assert missing["status"] == "error"
    assert "missing required arguments" in missing["error"]


def test_web_toolset_exposes_all_five():
    registry = ToolRegistry()
    register_browser_tools(registry)
    toolsets = default_toolsets()
    exposed = toolsets.exposed_names("web", registry=registry)
    assert exposed == ["browser_click", "browser_fill", "browser_open", "web_fetch", "web_search"]
    # The default toolset must not gain the browser tools: they are
    # opt-in through the web preset (the click/fill act on live pages).
    assert "browser_click" not in toolsets.exposed_names("default", registry=registry)
    assert "web_search" not in toolsets.exposed_names("default", registry=registry)


# ----------------------------------------------------------------------
# Turn loop: a real run calls web_fetch through the registry
# ----------------------------------------------------------------------


def test_turn_loop_calls_web_fetch(tools, fixture_base):
    registry = ToolRegistry()
    register_browser_tools(registry, tools=tools)
    calls = []

    def recording_tool_call(name, arguments):
        calls.append((name, arguments))
        return registry.handle_function_call(name, arguments)

    replies = [
        ModelReply(
            text="",
            tool_calls=[
                {
                    "id": "call-1",
                    "name": "web_fetch",
                    "arguments": {"url": f"{fixture_base}/page"},
                }
            ],
            input_tokens=10,
            output_tokens=5,
        ),
        ModelReply(text="The page says hello.", input_tokens=10, output_tokens=5),
    ]

    def fake_model(entry, wire_messages, offered_tools):
        return replies.pop(0)

    loop = TurnLoop(
        model_call=fake_model,
        tool_call=recording_tool_call,
        tool_catalog=[registry.get(name) for name in registry.names()],
    )
    result = loop.run_turn([InternalMessage.user("fetch the fixture page")])
    assert result.status == "completed"
    assert result.text == "The page says hello."
    assert calls == [("web_fetch", {"url": f"{fixture_base}/page"})]
    tool_results = [event for event in loop.event_memory() if event["type"] == "tool_called"]
    assert [event["content"]["tool"] for event in tool_results] == ["web_fetch"]


# ----------------------------------------------------------------------
# Real headless Chromium against the fixture server
# ----------------------------------------------------------------------


def _headless_chromium_available() -> bool:
    """True when Playwright can launch a headless Chromium binary.

    The CI runner has no Chromium binary installed, so the live tests
    below would fail there for an environment reason, not a code
    reason (the same missing-binary gap behind the two old Playwright
    failures in the full suite). Only the missing-executable case
    skips; any other launch failure still raises, so a broken
    browser is never hidden.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            browser.close()
    except Exception as exc:
        if "Executable doesn't exist" in str(exc):
            return False
        raise
    return True


@pytest.fixture(scope="module")
def live_tools(fixture_base):
    """BrowserTools over the real Playwright backend (one browser
    for the module). HTTP clients bypass any ambient proxy."""
    if not _headless_chromium_available():
        pytest.skip("headless Chromium is not installed on this runner")
    owned = BrowserTools(
        prompt=FakePrompt((APPROVE_ONCE, APPROVE_ONCE, APPROVE_ONCE)),
        fetch_client=httpx.Client(trust_env=False, follow_redirects=True),
    )
    yield owned
    owned.close()


def test_live_browser_open_reads_fixture_page(live_tools, fixture_base):
    payload = _payload(live_tools.browser_open(f"{fixture_base}/page"))
    assert payload["status"] == "ok"
    assert payload["title"] == "Fixture Page"
    assert "Hello Fixture" in payload["text"]
    assert "The quick brown fox" in payload["text"]


def test_live_browser_fill_and_click(live_tools, fixture_base):
    live_tools.browser_open(f"{fixture_base}/page")
    filled = _payload(live_tools.browser_fill("#name", "Nero"))
    assert filled["performed"] is True
    assert filled["verified"] is True
    clicked = _payload(live_tools.browser_click("Save", expect_text="Saved!"))
    assert clicked["performed"] is True
    assert clicked["verified"] is True


def test_live_browser_open_missing_page_is_an_error(live_tools, fixture_base):
    # The browser opens the URL (no HTTP error at the browser
    # level), but the page has no readable text of interest: the
    # tool still returns what the browser shows, it does not fake
    # a fetch error. This pins the split: HTTP status errors
    # belong to web_fetch, the browser reports what it renders.
    payload = _payload(live_tools.browser_open(f"{fixture_base}/notfound"))
    assert payload["status"] == "ok"
    assert "Not here" in payload["text"]
