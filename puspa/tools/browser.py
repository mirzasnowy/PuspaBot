"""Browser and web tools for the agent (COW-07).

Three capabilities, wired as registry tools the turn loop can
call (:func:`register_browser_tools`):

- ``browser_open`` / ``browser_click`` / ``browser_fill`` — drive
  a real browser: open a URL and read its text, click text on the
  page, fill a form field. All three run through the existing web
  bridge (:mod:`puspa.computer.web`), which already drives
  headless Chromium through Playwright; this module invents no
  second browser driver. Clicks and fills are permission-gated
  exactly like the bridge's own tools (token check + user prompt,
  tools ``computer.web_click`` / ``computer.web_fill``). One
  :class:`BrowserTools` keeps one bridge, so open, click and fill
  share the same page, the way an agent session expects.
- ``web_search`` — search the web through the DuckDuckGo HTML
  endpoint, parsed with the standard library. No API key, no new
  dependency, no paid service. The endpoint is overridable
  (``search_base_url``) so tests point it at a local fixture
  server instead of the live web.
- ``web_fetch`` — plain HTTP fetch of one URL (httpx, the same
  client the pricing readers use), with the page reduced to
  readable text. A page that needs JavaScript to show anything
  fails with a clear error that names ``browser_open`` as the
  tool to use instead; a non-text response and a non-200 status
  fail the same way, visibly, never as an empty success.

Pattern adapted from CowAgent's built-in browser and web-search
tools (MIT, rewritten as PuspaBot code, never copied verbatim):
one tool per action, plain JSON results, the browser kept behind
a small seam (a fake backend for tests, the real Chromium for
live runs). Split note (D-08): this is the LOCAL side only. The
browser runs on the user's own machine; hosted execution of
these tools lives in the private repo and is not built here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from puspa.computer.vision import VisualFallbackError
from puspa.computer.web import WebBridge
from puspa.tools.registry import ToolRegistry, default_registry, register

__all__ = [
    "BROWSER_TOOL_NAMES",
    "BrowserTools",
    "register_browser_tools",
]

#: Tool names this module registers, in registration order.
BROWSER_TOOL_NAMES: tuple[str, ...] = (
    "browser_open",
    "browser_click",
    "browser_fill",
    "web_search",
    "web_fetch",
)

#: Default search endpoint: DuckDuckGo's HTML results page. No
#: key, no account, plain HTML the standard library can parse.
DEFAULT_SEARCH_BASE_URL = "https://html.duckduckgo.com/html/"

#: Default cap on the text a tool returns, in characters. Long
#: pages are cut with a ``truncated`` flag so the model knows
#: there is more, instead of silently dropping the tail.
DEFAULT_MAX_CHARS = 8000

#: Default number of search results returned.
DEFAULT_MAX_RESULTS = 5

#: A fetch larger than this is cut before parsing: a multi-MB
#: page is never useful as tool output.
MAX_BODY_BYTES = 1_000_000

#: Phrases that mean "this page shows nothing without
#: JavaScript". Checked against the visible text first.
_JS_REQUIRED_PHRASES = (
    "requires javascript",
    "require javascript",
    "javascript is required",
    "javascript required",
    "enable javascript",
    "please enable javascript",
    "turn on javascript",
    "this site requires javascript",
)

#: Mount nodes a JavaScript app renders into. A page with one of
#: these, scripts, and almost no visible text is a JS shell.
_JS_MOUNT_IDS = ("root", "app", "__next")


def _require_http_url(url: str) -> str:
    """Validate ``url`` as an http(s) URL with a host, or raise
    ValueError with a plain message the agent can act on."""
    text = (url or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(
            f"not a usable web address: {url!r} (need http:// or https:// with a host)"
        )
    return text


def _truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """Cut ``text`` to ``max_chars`` characters. Returns the
    (possibly cut) text and whether it was cut."""
    if max_chars <= 0:
        raise ValueError(f"max_chars must be positive, got {max_chars}")
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


class _TextExtractor(HTMLParser):
    """Reduce an HTML page to its visible text.

    Script and style content is dropped; everything else is kept
    in document order. The ``<title>`` is captured separately so
    a fetch can report it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._in_title = False
        self._chunks: list[str] = []
        self._title_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self._title_chunks.append(data)
        else:
            self._chunks.append(data)

    @property
    def text(self) -> str:
        """The visible text, whitespace collapsed to single spaces."""
        return " ".join(" ".join(self._chunks).split())

    @property
    def title(self) -> str:
        """The page title, whitespace collapsed."""
        return " ".join(" ".join(self._title_chunks).split())


def _html_to_text(html: str) -> tuple[str, str]:
    """Return (visible text, title) of an HTML document."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text, extractor.title


def _javascript_required(html: str, text: str) -> bool:
    """True when the page shows nothing without JavaScript.

    Two signals, both on short visible text only (a long article
    that merely mentions JavaScript is not a JS gate): the visible
    text says so outright, or the page is a shell (a JS mount
    node plus scripts, almost no visible text). A plain page with
    little text and no mount node is not flagged: short is not
    the same as empty.
    """
    visible = " ".join(text.split()).lower()
    if len(visible) < 500 and any(phrase in visible for phrase in _JS_REQUIRED_PHRASES):
        return True
    if len(visible) >= 120:
        return False
    low_html = html.lower()
    if "<script" not in low_html:
        return False
    return any(
        re.search(rf'<div[^>]+id=["\']{mount}["\']', html, re.IGNORECASE) for mount in _JS_MOUNT_IDS
    )


class _SearchParser(HTMLParser):
    """Parse DuckDuckGo HTML results.

    A result is an ``<a class="result__a">`` (title + link)
    followed by an ``<a class="result__snippet">``. DuckDuckGo
    wraps outbound links in a redirect (``/l/?uddg=...``); the
    real destination is unwrapped when present. Links are
    resolved against the search page URL.
    """

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._title_chunks: list[str] = []

    def _unwrap(self, href: str) -> str:
        absolute = urljoin(self._base_url, href)
        parsed = urlparse(absolute)
        if parsed.path == "/l/":
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                return target
        return absolute

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag != "a":
            return
        classes = dict(attrs).get("class", "")
        if "result__a" in classes:
            href = dict(attrs).get("href", "")
            self.results.append({"title": "", "url": self._unwrap(href), "snippet": ""})
            self._in_title = True
            self._title_chunks = []
        elif "result__snippet" in classes and self.results:
            self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag != "a":
            return
        if self._in_title and self.results:
            self.results[-1]["title"] = " ".join(" ".join(self._title_chunks).split())
            self._in_title = False
        self._in_snippet = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_chunks.append(data)
        elif self._in_snippet and self.results:
            self.results[-1]["snippet"] += data

    @property
    def cleaned(self) -> list[dict[str, str]]:
        """Results with whitespace collapsed, in page order."""
        cleaned = []
        for result in self.results:
            cleaned.append(
                {
                    "title": " ".join(result["title"].split()),
                    "url": result["url"],
                    "snippet": " ".join(result["snippet"].split()),
                }
            )
        return cleaned


def _parse_search_html(html: str, base_url: str) -> list[dict[str, str]]:
    """Parse one search-results page into title/url/snippet rows."""
    parser = _SearchParser(base_url)
    parser.feed(html)
    parser.close()
    return parser.cleaned


class BrowserTools:
    """The browser + web tools, sharing one bridge and two HTTP
    clients.

    ``bridge`` is a ready :class:`~puspa.computer.web.WebBridge`
    (tests pass one over a fake backend); ``bridge_factory`` is
    called lazily on first use instead, so constructing this
    class never launches a browser. The HTTP clients are lazy
    the same way: pass ready ``httpx.Client`` instances in
    tests (a MockTransport or a client pointed at a fixture
    server), otherwise one client per job is built on first use.
    ``prompt`` / ``gate`` / ``token_payload`` are forwarded to
    the bridge for the gated click and fill.
    """

    def __init__(
        self,
        *,
        bridge: WebBridge | None = None,
        bridge_factory=None,
        search_client: httpx.Client | None = None,
        fetch_client: httpx.Client | None = None,
        search_base_url: str = DEFAULT_SEARCH_BASE_URL,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_results: int = DEFAULT_MAX_RESULTS,
        timeout_s: float = 15.0,
        prompt=None,
        gate=None,
        token_payload: dict | None = None,
    ) -> None:
        self._bridge = bridge
        self._bridge_factory = bridge_factory
        self._search_client = search_client
        self._fetch_client = fetch_client
        self._owns_search_client = search_client is None
        self._owns_fetch_client = fetch_client is None
        self.search_base_url = search_base_url
        self.max_chars = max_chars
        self.max_results = max_results
        self.timeout_s = timeout_s
        self._prompt = prompt
        self._gate = gate
        self._token_payload = token_payload

    # -- seams ----------------------------------------------------------

    @property
    def bridge(self) -> WebBridge:
        """The shared web bridge, built on first use."""
        if self._bridge is None:
            if self._bridge_factory is not None:
                self._bridge = self._bridge_factory()
            else:
                self._bridge = WebBridge(
                    prompt=self._prompt,
                    gate=self._gate,
                    token_payload=self._token_payload,
                )
        return self._bridge

    def _get_search_client(self) -> httpx.Client:
        if self._search_client is None:
            self._search_client = httpx.Client(
                timeout=self.timeout_s,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) PuspaBot/0.1"},
            )
        return self._search_client

    def _get_fetch_client(self) -> httpx.Client:
        if self._fetch_client is None:
            self._fetch_client = httpx.Client(
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) PuspaBot/0.1"},
            )
        return self._fetch_client

    def close(self) -> None:
        """Close the browser and any HTTP clients this instance
        built. Safe to call twice; clients passed in are left
        alone (their owner closes them)."""
        if self._bridge is not None:
            self._bridge.close()
            self._bridge = None
        if self._owns_search_client and self._search_client is not None:
            self._search_client.close()
            self._search_client = None
        if self._owns_fetch_client and self._fetch_client is not None:
            self._fetch_client.close()
            self._fetch_client = None

    # -- browser tools --------------------------------------------------

    def browser_open(self, url: str, max_chars: int | None = None) -> str:
        """Open ``url`` in the real browser and return its title
        plus its visible text."""
        target = _require_http_url(url)
        limit = self.max_chars if max_chars is None else max_chars
        try:
            page = self.bridge.open(target)
            text = self.bridge.read_text()
        except VisualFallbackError as exc:
            raise ValueError(f"browser could not open {target!r}: {exc}") from exc
        cut, truncated = _truncate(text, limit)
        return json.dumps(
            {
                "status": "ok",
                "tool": "browser_open",
                "url": page.url,
                "title": page.title,
                "text": cut,
                "truncated": truncated,
            }
        )

    def browser_click(
        self,
        text: str,
        expect_text: str | None = None,
        expect_absent_text: str | None = None,
    ) -> str:
        """Click ``text`` on the open page (permission-gated),
        then re-read the page to verify. Pass ``expect_text``
        (or ``expect_absent_text``) to say what must be true
        afterwards."""
        if not (text or "").strip():
            raise ValueError("browser_click needs the text to click")
        expect: dict | None = None
        if expect_text is not None:
            expect = {"text": expect_text}
        elif expect_absent_text is not None:
            expect = {"absent_text": expect_absent_text}
        try:
            result = self.bridge.click_text(text, expect=expect)
        except VisualFallbackError as exc:
            raise ValueError(f"browser click on {text!r} failed: {exc}") from exc
        payload = {"status": "ok", "tool": "browser_click"}
        payload.update(result.to_dict())
        return json.dumps(payload)

    def browser_fill(self, selector: str, value: str) -> str:
        """Type ``value`` into the form field ``selector`` names
        on the open page (permission-gated), then read the field
        back to verify. ``selector`` is a CSS selector, or the
        field's label on the fake backend."""
        if not (selector or "").strip():
            raise ValueError("browser_fill needs a field selector")
        try:
            result = self.bridge.fill(selector, value)
        except VisualFallbackError as exc:
            raise ValueError(f"browser fill of {selector!r} failed: {exc}") from exc
        payload = {"status": "ok", "tool": "browser_fill"}
        payload.update(result.to_dict())
        return json.dumps(payload)

    # -- web tools ------------------------------------------------------

    def web_search(self, query: str, max_results: int | None = None) -> str:
        """Search the web for ``query`` and return the top
        results (title, URL, snippet)."""
        text = (query or "").strip()
        if not text:
            raise ValueError("web_search needs a query")
        limit = self.max_results if max_results is None else max_results
        if limit <= 0:
            raise ValueError(f"max_results must be positive, got {limit}")
        try:
            response = self._get_search_client().get(self.search_base_url, params={"q": text})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ValueError(f"web search for {text!r} failed: {exc}") from exc
        results = _parse_search_html(response.text, self.search_base_url)
        return json.dumps(
            {
                "status": "ok",
                "tool": "web_search",
                "query": text,
                "results": results[:limit],
            }
        )

    def web_fetch(self, url: str, max_chars: int | None = None) -> str:
        """Fetch ``url`` over plain HTTP and return its readable
        text. Fails with a clear error when the page needs
        JavaScript (use ``browser_open`` for those), when it is
        not a text page, or when the server answers an error."""
        target = _require_http_url(url)
        limit = self.max_chars if max_chars is None else max_chars
        try:
            response = self._get_fetch_client().get(target)
        except httpx.HTTPError as exc:
            raise ValueError(f"web fetch of {target!r} failed: {exc}") from exc
        if response.status_code != 200:
            raise ValueError(
                f"web fetch of {target!r} failed: the server answered HTTP {response.status_code}"
            )
        content_type = response.headers.get("content-type", "")
        main_type = content_type.split(";")[0].strip().lower()
        if main_type and not (
            main_type.startswith("text/")
            or main_type.endswith(("+json", "+xml"))
            or main_type in ("application/json", "application/xml")
        ):
            raise ValueError(
                f"web fetch of {target!r} failed: not a text page "
                f"(Content-Type: {content_type or 'unknown'})"
            )
        raw = response.content[:MAX_BODY_BYTES]
        html = raw.decode("utf-8", errors="replace")
        if main_type in ("", "text/html"):
            text, title = _html_to_text(html)
        else:
            text, title = " ".join(html.split()), ""
        if _javascript_required(html, text):
            raise ValueError(
                f"web fetch of {target!r} failed: the page needs JavaScript "
                "to show its content, and a plain fetch cannot run it; "
                "use browser_open to read it in a real browser instead"
            )
        cut, truncated = _truncate(text, limit)
        return json.dumps(
            {
                "status": "ok",
                "tool": "web_fetch",
                "url": target,
                "final_url": str(response.url),
                "title": title,
                "text": cut,
                "truncated": truncated,
            }
        )


# ----------------------------------------------------------------------
# Registry wiring
# ----------------------------------------------------------------------

_TOOL_SCHEMAS: dict[str, tuple[str, dict, bool]] = {
    "browser_open": (
        (
            "Open a URL in a real browser (headless Chromium) and return the page title "
            "plus its visible text. Read-only: never clicks anything. Use it for pages "
            "a plain fetch cannot read, such as pages that need JavaScript."
        ),
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to open, e.g. 'https://example.com'.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of page text to return (default 8000).",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        True,
    ),
    "browser_click": (
        (
            "Click text on the page the browser has open. The user is asked first, then "
            "the page is re-read to verify the click did something. Pass expect_text "
            "(or expect_absent_text) to say what must be true afterwards."
        ),
        {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to click on the open page, e.g. 'Save'.",
                },
                "expect_text": {
                    "type": "string",
                    "description": "Text that must be on the page after the click.",
                },
                "expect_absent_text": {
                    "type": "string",
                    "description": "Text that must be gone from the page after the click.",
                },
            },
            "required": ["text"],
            "additionalProperties": False,
        },
        False,
    ),
    "browser_fill": (
        (
            "Type a value into a form field on the page the browser has open. The user "
            "is asked first, then the field is read back to verify the value stuck. "
            "The selector is a CSS selector, e.g. '#email' or 'input[name=q]'."
        ),
        {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the form field, e.g. '#email'.",
                },
                "value": {
                    "type": "string",
                    "description": "Value to type into the field.",
                },
            },
            "required": ["selector", "value"],
            "additionalProperties": False,
        },
        False,
    ),
    "web_search": (
        (
            "Search the web and return the top results (title, URL, snippet). Read-only. "
            "Use it to find pages before fetching or opening them."
        ),
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query, e.g. 'PuspaBot price comparison'.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum results to return (default 5).",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        True,
    ),
    "web_fetch": (
        (
            "Fetch one URL over plain HTTP and return its readable text. Read-only. "
            "Fails with a clear error when the page needs JavaScript (use browser_open "
            "for those), when it is not a text page, or when the server answers an error."
        ),
        {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch, e.g. 'https://example.com/article'.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters of page text to return (default 8000).",
                },
            },
            "required": ["url"],
            "additionalProperties": False,
        },
        True,
    ),
}


def register_browser_tools(
    registry: ToolRegistry | None = None,
    *,
    tools: BrowserTools | None = None,
) -> BrowserTools:
    """Register the browser + web tools on a registry.

    Idempotent: a tool already on the registry is left alone, so
    calling this twice (or after ``discover``) is safe. Returns
    the :class:`BrowserTools` behind the handlers, so the caller
    can close it (browser + HTTP clients) when done.
    """
    target = registry if registry is not None else default_registry()
    owned = tools if tools is not None else BrowserTools()
    handlers: dict[str, Callable[..., str]] = {
        "browser_open": owned.browser_open,
        "browser_click": owned.browser_click,
        "browser_fill": owned.browser_fill,
        "web_search": owned.web_search,
        "web_fetch": owned.web_fetch,
    }
    for name in BROWSER_TOOL_NAMES:
        if name in target:
            continue
        description, parameters, read_only = _TOOL_SCHEMAS[name]
        register(
            name,
            description,
            parameters,
            handlers[name],
            read_only=read_only,
            registry=target,
        )
    return owned
