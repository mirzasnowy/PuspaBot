"""Per-run skill narrowing + tool overflow (G3).

Pattern rewritten from OpenBot (MIT, adapted, not copied): before a
run, pick the skills that fit the message; the agent is built with
only those skills' tools plus the granted tools (a model picks
reliably from ~10 tools, not from 30).

- **Fail-open**: when narrowing fails — no skill matches, or the
  LLM selector errors — the whole catalog is offered. A
  fail-closed narrowing would silently remove capabilities.
- **Overflow at 50 tools**: the first 50 tools stay directly
  available and the rest wait behind a single ``search_tools``
  tool (:func:`search_tools` searches the overflow list).
- **Hard refusal at 128 tools**: the run is refused and an audit
  record ``agent.tool_limit_refused`` is produced
  (:class:`ToolLimitError`).

Narrowing takes the tool list as input — a list of dicts
``{"name", "description", "skill"}`` — and never depends on the
G27 plugin loader. Skill matching is deterministic keyword
overlap on ``triggerTerms`` by default; an injected
``llm_complete`` callable (``complete(prompt)`` from
``puspa.router.complete``) may select skill ids instead, and any
failure of that call falls back to deterministic matching, then
to fail-open.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .errors import ToolLimitError

__all__ = [
    "MAX_DIRECT_TOOLS",
    "MAX_TOOLS",
    "SEARCH_TOOLS_NAME",
    "TOOL_LIMIT_REFUSED_EVENT",
    "NarrowingResult",
    "narrow_tools",
    "search_tools",
]

#: Above this many tools, the rest wait behind ``search_tools``.
MAX_DIRECT_TOOLS = 50
#: Above this many tools, the run is refused.
MAX_TOOLS = 128
#: The overflow search tool's name.
SEARCH_TOOLS_NAME = "search_tools"
#: Audit event for a refused run.
TOOL_LIMIT_REFUSED_EVENT = "agent.tool_limit_refused"

_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class NarrowingResult:
    """Outcome of one narrowing pass."""

    tools: list = field(default_factory=list)
    overflow_tools: list = field(default_factory=list)
    selected_skill_ids: list = field(default_factory=list)
    narrowed: bool = False
    refused: bool = False
    audit: list = field(default_factory=list)

    @property
    def search_tool(self) -> dict | None:
        """The ``search_tools`` tool, when overflow exists."""
        for tool in self.tools:
            if tool.get("name") == SEARCH_TOOLS_NAME:
                return tool
        return None

    def to_dict(self) -> dict:
        return {
            "tools": [t.get("name") for t in self.tools],
            "overflow_tools": [t.get("name") for t in self.overflow_tools],
            "selected_skill_ids": list(self.selected_skill_ids),
            "narrowed": self.narrowed,
            "refused": self.refused,
            "audit": list(self.audit),
        }


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _match_scores(message: str, skills: list) -> dict[str, int]:
    """Deterministic trigger-term overlap per skill id."""
    words = _tokens(message)
    scores: dict[str, int] = {}
    for skill in skills or []:
        skill_id = skill.get("id")
        if not skill_id:
            continue
        triggers = skill.get("triggerTerms") or []
        hits = 0
        for term in triggers:
            term_words = _tokens(str(term))
            if (term_words and term_words & words) or str(term).lower() in message.lower():
                hits += 1
        if hits:
            scores[skill_id] = hits
    return scores


def _llm_select(message: str, skills: list, llm_complete) -> list[str] | None:
    """Ask the LLM which skills fit. ``None`` on any failure."""
    try:
        catalog = [
            {
                "id": s.get("id"),
                "description": s.get("description", ""),
                "triggerTerms": s.get("triggerTerms") or [],
            }
            for s in skills or []
        ]
        prompt = (
            "Pick the skill ids that fit this user message. "
            'Reply with JSON only: {"skills": ["id", ...]}.\n'
            f"Message: {message}\n"
            f"Skills: {json.dumps(catalog, ensure_ascii=False)}"
        )
        raw = llm_complete(prompt)
        data = json.loads(raw)
        picked = data.get("skills")
        if not isinstance(picked, list):
            return None
        known = {s.get("id") for s in skills or []}
        return [p for p in picked if p in known]
    except Exception:
        return None


def search_tools(query: str, overflow_tools: list) -> list[dict]:
    """Search the overflow tools by name/description keyword match.

    Pure function: the ``search_tools`` tool the agent calls when
    the direct tool list overflowed past 50.
    """
    words = _tokens(query)
    if not words:
        return list(overflow_tools)
    ranked = []
    for tool in overflow_tools:
        haystack = _tokens(f"{tool.get('name', '')} {tool.get('description', '')}")
        hits = len(words & haystack)
        if hits:
            ranked.append((hits, tool))
    ranked.sort(key=lambda item: -item[0])
    return [tool for _hits, tool in ranked]


def narrow_tools(
    message: str,
    skills: list,
    tools: list,
    *,
    llm_complete=None,
    granted_tools: list | None = None,
    audit_sink=None,
) -> NarrowingResult:
    """Narrow the tool list for one run.

    ``skills`` is the catalog (manifest dicts with ``id``,
    ``triggerTerms``, ``description``). ``tools`` is the full tool
    list (``{"name", "description", "skill"}``; ``skill`` may be
    ``None`` for granted/ungrouped tools). ``granted_tools`` are
    always kept, whatever the narrowing picks.

    Returns a :class:`NarrowingResult`. Raises
    :class:`ToolLimitError` (and records the
    ``agent.tool_limit_refused`` audit event) when the final tool
    count exceeds 128.
    """
    tools = list(tools or [])
    granted = list(granted_tools or [])
    {t.get("name") for t in granted}
    audit: list[dict] = []

    def _audit(event: str, detail: dict) -> None:
        record = {"event": event, **detail}
        audit.append(record)
        if audit_sink is not None:
            audit_sink(record)

    if (
        len(tools)
        + len([g for g in granted if g.get("name") not in {t.get("name") for t in tools}])
        > MAX_TOOLS
    ):
        _audit(
            TOOL_LIMIT_REFUSED_EVENT,
            {
                "tool_count": len(tools),
                "limit": MAX_TOOLS,
                "message": message[:200],
            },
        )
        raise ToolLimitError(f"run refused: {len(tools)} tools exceed the {MAX_TOOLS} limit")

    selected: list[str] | None = None
    if llm_complete is not None:
        selected = _llm_select(message, skills, llm_complete)
    if selected is None:
        scores = _match_scores(message, skills)
        selected = sorted(scores, key=lambda sid: -scores[sid]) if scores else None

    narrowed = selected is not None and len(selected) > 0
    if selected:
        chosen = [t for t in tools if t.get("skill") in set(selected)]
    else:
        # Fail-open: narrowing found nothing -> offer the whole catalog.
        chosen = list(tools)
        _audit(
            "narrowing_fail_open",
            {
                "message": message[:200],
                "tool_count": len(chosen),
            },
        )

    # Granted tools are always present.
    names = {t.get("name") for t in chosen}
    for tool in granted:
        if tool.get("name") not in names:
            chosen.append(tool)
            names.add(tool.get("name"))

    overflow: list[dict] = []
    if len(chosen) > MAX_DIRECT_TOOLS:
        direct = chosen[:MAX_DIRECT_TOOLS]
        overflow = chosen[MAX_DIRECT_TOOLS:]
        direct.append(
            {
                "name": SEARCH_TOOLS_NAME,
                "description": (
                    "Search the remaining tools by keyword. "
                    f"{len(overflow)} more tools are available through this tool."
                ),
                "skill": None,
            }
        )
        chosen = direct
        _audit(
            "tool_overflow_search",
            {
                "direct_tools": MAX_DIRECT_TOOLS,
                "overflow_tools": len(overflow),
            },
        )

    return NarrowingResult(
        tools=chosen,
        overflow_tools=overflow,
        selected_skill_ids=selected or [],
        narrowed=narrowed,
        audit=audit,
    )
