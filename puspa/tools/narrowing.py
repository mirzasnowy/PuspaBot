"""Per-run skill narrowing + ``search_tools`` over the tool registry (G17).

Pattern rewritten from OpenBot (``app/src/lib/skills/`` +
``docs/architecture.md``, adapted, never copied verbatim): before a
run, pick the skills that fit the user message and build the bot
with only those skills' tools plus the granted tools. A model picks
reliably from ~10 tools and unreliably from 30, so the offered set
is capped small; when the catalog is big anyway, the rest waits
behind one ``search_tools`` tool; past the hard limit the run is
refused.

This is the registry-level twin of the marketplace narrowing in
:mod:`puspa.marketplace.narrowing` (G3). That module narrows
marketplace skill manifests (plain dicts); this module narrows
:class:`~puspa.tools.registry.ToolDefinition` objects from the
registry, so it is what the turn loop actually runs. The two share
the thresholds (50 / 128), the event name
``agent.tool_limit_refused``, and the fail-open rule — but they do
not share code: the marketplace module works on dicts and must
never depend on the registry, and this module must never depend on
the marketplace package (importing it would drag the whole
marketplace ``__init__`` into the tools layer). The exception
:class:`ToolLimitError` here is likewise a tools-layer twin of the
marketplace one, same name and same audit event.

Rules:

- **Narrowing.** The default selector is deterministic keyword /
  description overlap between the user message and each skill
  (trigger terms count double, description tokens count once).
  No LLM call, no cost, fully testable. The selector is a
  pluggable :class:`ToolSelector` protocol: pass any object with a
  ``select(message, skills)`` method. An LLM-backed selector is
  NOT built here — it plugs in later through the router's
  ``llm_complete`` seam (see ``docs/skill-narrowing.md``).
- **Fail-open.** When narrowing finds nothing — no skill matches,
  the selector raises, or the selector names only unknown skills —
  the whole catalog is offered, and the fall-back is recorded in
  the audit list. A fail-closed narrowing would silently remove
  capabilities the model might need.
- **Overflow at 50 tools.** When the offered set is over
  ``max_direct_tools`` (default 50), only the first 50 stay
  directly offered; the rest become hidden tools, reachable
  through one ``search_tools`` tool. Narrowed-away tools are
  hidden the same way, so the model can always recover a tool
  narrowing missed by calling ``search_tools``.
- **Refusal at 128 tools.** When the run's catalog (plus granted
  tools not already in it) is over ``max_tools`` (default 128),
  :func:`prepare_run_tools` raises :class:`ToolLimitError` and
  records the ``agent.tool_limit_refused`` audit event through the
  ``audit_sink`` callable. The sink has the G4/G5/G6 ledger shape
  ``sink(event_type, content)`` — the same shape
  :class:`puspa.turns.loop.TurnLoop` and ``make_ledger_sink`` use;
  no new audit path is invented. Note the ledger itself keeps a
  closed set of 31 event types (revision 7, section 2.3), so a raw
  Ledger sink drops this name silently (best-effort, same as the
  existing ``world_prediction`` precedent); the record is kept by
  the turn sink, and the turn's failure record carries the refusal
  message. Making the refusal durable in the ledger needs a
  decision about the closed set — see ``docs/skill-narrowing.md``.

``search_tools`` is a real registered tool: its handler searches
the run's hidden tools (bound per run through a :mod:`contextvars`
binding, the same pattern as the token binding in
``puspa.chassis.context``) and falls back to searching the whole
registry when no run has bound hidden tools. Register it with
:func:`register_search_tools`; ``puspa.tools.builtin`` calls that
during discovery so the tool exists on the default registry.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .registry import ToolDefinition, ToolRegistry, default_registry

__all__ = [
    "DEFAULT_MAX_DIRECT_TOOLS",
    "DEFAULT_MAX_OFFERED_TOOLS",
    "DEFAULT_MAX_TOOLS",
    "DEFAULT_SEARCH_RESULT_LIMIT",
    "SEARCH_TOOLS_NAME",
    "TOOL_LIMIT_REFUSED_EVENT",
    "KeywordSelector",
    "NarrowingConfig",
    "NarrowingResult",
    "Skill",
    "ToolLimitError",
    "ToolSelector",
    "current_hidden_tools",
    "hidden_tools_context",
    "prepare_run_tools",
    "register_search_tools",
    "search_tools",
    "search_tools_definition",
]

#: Above this many directly offered tools, the rest wait behind
#: ``search_tools``.
DEFAULT_MAX_DIRECT_TOOLS = 50
#: Above this many tools in the run's catalog, the run is refused.
DEFAULT_MAX_TOOLS = 128
#: The narrowing cap: at most this many catalog tools are offered
#: directly after a successful narrowing pass.
DEFAULT_MAX_OFFERED_TOOLS = 10
#: Default number of results one ``search_tools`` call returns.
DEFAULT_SEARCH_RESULT_LIMIT = 10
#: The overflow search tool's name.
SEARCH_TOOLS_NAME = "search_tools"
#: Audit event for a refused run.
TOOL_LIMIT_REFUSED_EVENT = "agent.tool_limit_refused"

#: Hard cap on one ``search_tools`` call's ``limit`` argument: a
#: caller asking for more gets this many, never an unbounded list.
MAX_SEARCH_LIMIT = 50

_WORD = re.compile(r"[a-z0-9]+")


class ToolLimitError(Exception):
    """A run was refused because the tool count exceeds the hard
    limit (default 128). The audit event is
    ``agent.tool_limit_refused``.

    Tools-layer twin of ``puspa.marketplace.errors.ToolLimitError``
    (same name, same event); kept separate so this module never
    imports the marketplace package.
    """


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Skill:
    """One skill: a group of tools plus the words that select it.

    ``trigger_terms`` are the keywords that match a user message
    (``"price"``, ``"cheapest"``, ``"pln"``); ``description`` is
    matched by token overlap too, so a skill needs no exhaustive
    trigger list. ``tools`` names the registry tools the skill
    owns. A skill is built by the caller — from a marketplace
    manifest, a module manifest, or a hand-written list — never
    guessed from tool names.
    """

    id: str
    description: str = ""
    trigger_terms: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("skill id must be a non-empty string")
        for name in self.tools:
            if not isinstance(name, str) or not name:
                raise ValueError(f"skill {self.id!r} names an invalid tool: {name!r}")


@dataclass
class NarrowingConfig:
    """Thresholds for one narrowing pass. Defaults are the OpenBot
    starting points from the comparison report (50 / 128)."""

    max_direct_tools: int = DEFAULT_MAX_DIRECT_TOOLS
    max_tools: int = DEFAULT_MAX_TOOLS
    max_offered_tools: int = DEFAULT_MAX_OFFERED_TOOLS
    search_result_limit: int = DEFAULT_SEARCH_RESULT_LIMIT

    def __post_init__(self) -> None:
        if self.max_direct_tools < 1:
            raise ValueError("max_direct_tools must be at least 1")
        if self.max_tools <= self.max_direct_tools:
            raise ValueError(
                f"max_tools ({self.max_tools}) must be larger than "
                f"max_direct_tools ({self.max_direct_tools})"
            )
        if self.max_offered_tools < 1:
            raise ValueError("max_offered_tools must be at least 1")
        if self.max_offered_tools > self.max_direct_tools:
            raise ValueError(
                f"max_offered_tools ({self.max_offered_tools}) must not exceed "
                f"max_direct_tools ({self.max_direct_tools})"
            )
        if self.search_result_limit < 1:
            raise ValueError("search_result_limit must be at least 1")


# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------


@runtime_checkable
class ToolSelector(Protocol):
    """Pick the skill ids that fit a user message.

    Return a list of skill ids (possibly empty), or ``None`` when
    the selector cannot decide. An empty list and ``None`` both
    mean "no skill picked" and lead to fail-open (the whole
    catalog is offered). Raising is also fail-open: the caller
    falls back to the keyword selector, then to the whole catalog.
    An LLM-backed selector implements this same protocol by
    calling the router's ``llm_complete(prompt)`` callable — see
    ``docs/skill-narrowing.md`` for the seam; it is not built here.
    """

    def select(self, message: str, skills: Sequence[Skill]) -> list[str] | None: ...


class KeywordSelector:
    """Deterministic selector: trigger-term + description overlap.

    Score per skill: 2 points per trigger term that matches the
    message (whole-word token overlap, or the term appearing as a
    substring), plus 1 point per description token shared with the
    message. Only skills with a positive score are picked, best
    score first, ties broken by skill id so the result is fully
    deterministic. No LLM call, no cost, no network.
    """

    def select(self, message: str, skills: Sequence[Skill]) -> list[str] | None:
        words = _tokens(message)
        lowered = message.lower()
        scored: list[tuple[int, str]] = []
        for skill in skills:
            score = 0
            for term in skill.trigger_terms:
                term_words = _tokens(str(term))
                if (term_words and term_words & words) or str(term).lower() in lowered:
                    score += 2
            score += len(_tokens(skill.description) & words)
            if score > 0:
                scored.append((score, skill.id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [skill_id for _score, skill_id in scored]


def _tool_score(message_words: set[str], tool: ToolDefinition) -> int:
    """Token overlap between the message and one tool's name +
    description, for ranking tools under the offered cap."""
    haystack = _tokens(f"{tool.name} {tool.description}")
    return len(message_words & haystack)


# ---------------------------------------------------------------------------
# search_tools: pure search + registered tool
# ---------------------------------------------------------------------------

#: The hidden tools of the current run, bound by
#: :func:`hidden_tools_context` while a turn runs. ``None`` means
#: no run bound hidden tools; the handler then searches the whole
#: registry instead.
_HIDDEN_TOOLS: ContextVar[tuple[ToolDefinition, ...] | None] = ContextVar(
    "puspa_hidden_tools", default=None
)


def current_hidden_tools() -> tuple[ToolDefinition, ...] | None:
    """The hidden tools bound for this run, or None."""
    return _HIDDEN_TOOLS.get()


class hidden_tools_context:
    """Bind the run's hidden tools for the ``search_tools`` handler.

    A context manager (not ``contextlib``: it must also work as a
    plain ``try``/``finally`` pair via :meth:`__enter__` /
    :meth:`__exit__`). The previous binding is restored on exit,
    so nested runs do not leak hidden tools into each other.
    """

    def __init__(self, tools: Sequence[ToolDefinition]) -> None:
        self._tools = tuple(tools)
        self._token: Token | None = None

    def __enter__(self) -> tuple[ToolDefinition, ...]:
        self._token = _HIDDEN_TOOLS.set(self._tools)
        return self._tools

    def __exit__(self, *exc: Any) -> None:
        if self._token is not None:
            _HIDDEN_TOOLS.reset(self._token)
            self._token = None


def search_tools(
    query: str, tools: Sequence[ToolDefinition], *, limit: int = DEFAULT_SEARCH_RESULT_LIMIT
) -> list[dict[str, Any]]:
    """Search tools by name/description keyword overlap.

    Pure function: the work behind the ``search_tools`` tool.
    Ranking is token overlap between the query and each tool's
    name + description, best first, ties broken by tool name.
    An empty query returns the tools in name order. Results are
    capped at ``limit`` (1..50) and carry name, description, and
    parameters — enough for the model to call a found tool.
    """
    if not isinstance(query, str):
        raise ValueError("query must be a string")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    limit = min(limit, MAX_SEARCH_LIMIT)
    words = _tokens(query)
    ranked: list[tuple[int, str, ToolDefinition]] = []
    for tool in tools:
        if not words:
            ranked.append((0, tool.name, tool))
            continue
        hits = len(words & _tokens(f"{tool.name} {tool.description}"))
        if hits:
            ranked.append((hits, tool.name, tool))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return [
        {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
        for _hits, _name, tool in ranked[:limit]
    ]


def _search_tools_handler_for(registry: ToolRegistry):
    """Build the ``search_tools`` handler bound to one registry.

    The handler searches the run's hidden tools (bound through
    the context) when there are any; otherwise it searches the
    registry the tool was registered on — the hidden tools are
    registered there too, so they stay findable, and the run
    binding only narrows the scope.
    """

    def _handler(query: str, limit: int = DEFAULT_SEARCH_RESULT_LIMIT) -> str:
        if not isinstance(query, str) or not query.strip():
            return json.dumps(
                {
                    "status": "error",
                    "tool": SEARCH_TOOLS_NAME,
                    "error": "query must be a non-empty string",
                }
            )
        try:
            wanted = int(limit)
        except (TypeError, ValueError):
            return json.dumps(
                {"status": "error", "tool": SEARCH_TOOLS_NAME, "error": "limit must be an integer"}
            )
        hidden = current_hidden_tools()
        if hidden is not None:
            scope = "hidden"
            pool = list(hidden)
        else:
            scope = "registry"
            pool = [
                tool
                for tool in (registry.get(name) for name in registry.names())
                if tool is not None and tool.name != SEARCH_TOOLS_NAME
            ]
        try:
            results = search_tools(query, pool, limit=wanted)
        except ValueError as exc:
            return json.dumps({"status": "error", "tool": SEARCH_TOOLS_NAME, "error": str(exc)})
        return json.dumps(
            {
                "status": "ok",
                "tool": SEARCH_TOOLS_NAME,
                "scope": scope,
                "query": query,
                "results": results,
                "count": len(results),
            }
        )

    return _handler


def search_tools_definition(
    hidden_count: int = 0, registry: ToolRegistry | None = None
) -> ToolDefinition:
    """The ``search_tools`` definition offered on one run.

    A fresh definition per run so the description can say how
    many hidden tools wait behind it. The handler is shared:
    it reads the run's hidden tools from the context binding,
    and otherwise searches ``registry`` (the default registry
    when none is given).
    """
    if hidden_count > 0:
        description = (
            "Search the tools that are not offered directly in this run. "
            f"{hidden_count} more tool(s) are available through this tool: "
            "call it with keywords for what you need, then call the tool it returns."
        )
    else:
        description = (
            "Search the tools that are not offered directly in this run. "
            "Call it with keywords for what you need, then call the tool it returns."
        )
    return ToolDefinition(
        name=SEARCH_TOOLS_NAME,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords for the tool you need, e.g. 'check electricity bill'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum tools to return (default 10, at most 50).",
                    "default": DEFAULT_SEARCH_RESULT_LIMIT,
                },
            },
            "required": ["query"],
        },
        handler=_search_tools_handler_for(registry or default_registry()),
        read_only=True,
    )


def register_search_tools(registry: ToolRegistry | None = None) -> ToolDefinition:
    """Register ``search_tools`` on a registry. Idempotent: when
    the tool is already registered, the existing definition is
    returned untouched."""
    target = registry if registry is not None else default_registry()
    existing = target.get(SEARCH_TOOLS_NAME)
    if existing is not None:
        return existing
    definition = search_tools_definition(registry=target)
    target.register_definition(definition)
    return definition


# ---------------------------------------------------------------------------
# Run preparation
# ---------------------------------------------------------------------------


@dataclass
class NarrowingResult:
    """Outcome of one narrowing pass for a run."""

    tools: list[ToolDefinition] = field(default_factory=list)
    hidden_tools: list[ToolDefinition] = field(default_factory=list)
    selected_skill_ids: list[str] = field(default_factory=list)
    narrowed: bool = False
    fail_open: bool = False
    refused: bool = False
    audit: list[dict] = field(default_factory=list)

    @property
    def offered_names(self) -> list[str]:
        """Names of the directly offered tools, in offered order."""
        return [tool.name for tool in self.tools]

    @property
    def hidden_names(self) -> list[str]:
        """Names of the hidden tools, in catalog order."""
        return [tool.name for tool in self.hidden_tools]

    @property
    def search_tool_included(self) -> bool:
        """True when the offered set carries ``search_tools``."""
        return SEARCH_TOOLS_NAME in self.offered_names

    def schemas(self) -> list[dict[str, Any]]:
        """OpenAI-style schemas for the offered tools, in order."""
        return [tool.to_schema() for tool in self.tools]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tools": list(self.offered_names),
            "hidden_tools": list(self.hidden_names),
            "selected_skill_ids": list(self.selected_skill_ids),
            "narrowed": self.narrowed,
            "fail_open": self.fail_open,
            "refused": self.refused,
            "audit": list(self.audit),
        }


def _resolve_granted(
    granted: Sequence[str | ToolDefinition],
    catalog: Sequence[ToolDefinition],
) -> list[ToolDefinition]:
    """Granted tools as definitions: names resolve against the
    catalog, definitions pass through. Unknown names are ignored
    (a granted name with no definition cannot be offered)."""
    by_name = {tool.name: tool for tool in catalog}
    resolved: list[ToolDefinition] = []
    seen: set[str] = set()
    for item in granted:
        if isinstance(item, str):
            tool = by_name.get(item)
        elif isinstance(item, ToolDefinition):
            tool = item
        else:
            continue
        if tool is not None and tool.name not in seen:
            resolved.append(tool)
            seen.add(tool.name)
    return resolved


def prepare_run_tools(
    message: str,
    tools: Sequence[ToolDefinition],
    *,
    skills: Sequence[Skill] = (),
    granted: Sequence[str | ToolDefinition] = (),
    selector: ToolSelector | None = None,
    config: NarrowingConfig | None = None,
    audit_sink: Callable[[str, dict], Any] | None = None,
) -> NarrowingResult:
    """Narrow a run's tool catalog to the set the model is offered.

    ``tools`` is the run's catalog — already filtered by toolset,
    allowlist, and availability by the caller; narrowing only ever
    shrinks it, never widens it. ``skills`` groups catalog tools
    by skill; ``granted`` tools (names or definitions) are always
    offered, whatever narrowing picks. ``selector`` picks the
    skills; None means the deterministic :class:`KeywordSelector`.
    ``audit_sink`` is the G4/G5/G6 ledger-shape callable
    ``sink(event_type, content)``; narrowing records go through
    it AND into the returned result's ``audit`` list.

    Raises :class:`ToolLimitError` (after recording the
    ``agent.tool_limit_refused`` audit event) when the catalog
    plus granted tools exceeds ``config.max_tools``.
    """
    if not isinstance(message, str):
        raise ValueError("message must be a string")
    cfg = config if config is not None else NarrowingConfig()
    catalog = list(tools)
    granted_tools = _resolve_granted(granted, catalog)
    audit: list[dict] = []

    def _audit(event: str, content: dict) -> None:
        record = {"event": event, **content}
        audit.append(record)
        if audit_sink is not None:
            audit_sink(event, content)

    catalog_names = {tool.name for tool in catalog}
    total = len(catalog_names | {tool.name for tool in granted_tools})
    if total > cfg.max_tools:
        _audit(
            TOOL_LIMIT_REFUSED_EVENT,
            {"tool_count": total, "limit": cfg.max_tools, "message": message[:200]},
        )
        raise ToolLimitError(f"run refused: {total} tools exceed the {cfg.max_tools} tool limit")

    # -- select skills ------------------------------------------------
    active_selector = selector if selector is not None else KeywordSelector()
    selected: list[str] | None = None
    try:
        selected = active_selector.select(message, list(skills))
    except Exception:
        _audit(
            "narrowing_selector_failed",
            {"selector": type(active_selector).__name__, "message": message[:200]},
        )
        selected = None
    if selected is None:
        # A selector that cannot decide falls back to keywords.
        try:
            selected = KeywordSelector().select(message, list(skills))
        except Exception:
            selected = None
    known = {skill.id for skill in skills}
    selected = [skill_id for skill_id in (selected or []) if skill_id in known]

    message_words = _tokens(message)
    hidden: list[ToolDefinition] = []
    if selected:
        owned = {name for skill in skills if skill.id in set(selected) for name in skill.tools}
        chosen = [tool for tool in catalog if tool.name in owned]
        hidden = [tool for tool in catalog if tool.name not in owned]
        narrowed = True
        fail_open = False
        _audit(
            "narrowing_selected",
            {"skills": list(selected), "tool_count": len(chosen)},
        )
    else:
        # Fail-open: nothing matched -> offer the whole catalog.
        chosen = list(catalog)
        narrowed = False
        fail_open = True
        _audit(
            "narrowing_fail_open",
            {"message": message[:200], "tool_count": len(chosen)},
        )

    # -- cap the offered set ------------------------------------------
    if narrowed and len(chosen) > cfg.max_offered_tools:
        ranked = sorted(
            chosen,
            key=lambda tool: (-_tool_score(message_words, tool), tool.name),
        )
        offered = ranked[: cfg.max_offered_tools]
        offered_ids = {id(tool) for tool in offered}
        hidden = [*hidden, *(tool for tool in chosen if id(tool) not in offered_ids)]
        chosen = offered

    # -- granted tools are always present ------------------------------
    offered_names = {tool.name for tool in chosen}
    for tool in granted_tools:
        if tool.name not in offered_names:
            chosen.append(tool)
            offered_names.add(tool.name)

    # -- overflow behind search_tools ----------------------------------
    if len(chosen) > cfg.max_direct_tools:
        direct = chosen[: cfg.max_direct_tools]
        overflow = chosen[cfg.max_direct_tools :]
        hidden = [*hidden, *overflow]
        direct.append(search_tools_definition(len(hidden)))
        chosen = direct
        _audit(
            "tool_overflow_search",
            {"direct_tools": cfg.max_direct_tools, "overflow_tools": len(overflow)},
        )
    elif hidden:
        # Narrowed-away tools stay reachable through search_tools.
        chosen = [*chosen, search_tools_definition(len(hidden))]

    return NarrowingResult(
        tools=chosen,
        hidden_tools=hidden,
        selected_skill_ids=selected,
        narrowed=narrowed,
        fail_open=fail_open,
        audit=audit,
    )
