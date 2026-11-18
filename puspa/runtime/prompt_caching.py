"""Cache-breakpoint markers for a rendered prompt surface (G20).

Pattern studied, then rewritten as Puspa's own code (see docs/reuse.md):
hermes-agent splits its cache handling across ``agent/
prompt_caching.py`` (breakpoint markers) plus boundary/scope/clock
helpers. Only the PATTERN is taken: no hermes-agent code is copied.
hermes-agent is MIT. What Puspa needs is smaller than what
hermes-agent built, so this module keeps only the two markers the
providers Puspa actually drives support:

- Anthropic: the stable prefix becomes a system block carrying
  ``cache_control: {"type": "ephemeral"}``. The cache breakpoint
  is the end of that block, so the whole stable layer is one
  cacheable prefix. This is the provider's documented prompt
  caching mechanism: up to four cache breakpoints, the last block
  of the prefix carries the marker.
- OpenAI (and every OpenAI-compatible provider behind the router):
  prefix caching is automatic. No marker is sent; the stable
  prefix is cacheable exactly because it is byte-identical at the
  start of every request in the session. The marker here is
  therefore the absence of one, plus the invariant that the
  surface never moves the breakpoint.

The split is exact: ``system_prompt[:cache_breakpoint]`` is the
stable layer and ``system_prompt[cache_breakpoint:]`` is the rest,
so joining the Anthropic blocks back together reproduces the
system prompt byte for byte. ``puspa.router.translate`` proves
that: translating an Anthropic request built here back to OpenAI
returns the same system text (see ``tests/test_prompt_surface.py``).

No live call is made here. These functions only shape requests;
the router (``puspa.router.complete`` / ``puspa.router.translate``)
is what sends them.
"""

from __future__ import annotations

from .prompt_assembly import PromptSurface

__all__ = [
    "anthropic_request",
    "anthropic_system_blocks",
    "openai_messages",
    "openai_request",
    "stable_prefix",
]

#: Anthropic's default per-request token cap when the caller does
#: not give one. The router's own default is 4096 as well (see
#: ``puspa.router.translate.DEFAULT_ANTHROPIC_MAX_TOKENS``).
DEFAULT_ANTHROPIC_MAX_TOKENS = 4096


def stable_prefix(surface: PromptSurface) -> str:
    """The cacheable prefix: the frozen stable layer, nothing else."""
    return surface.system_prompt[: surface.cache_breakpoint]


def anthropic_system_blocks(surface: PromptSurface) -> list[dict]:
    """System prompt as Anthropic blocks, marker on the stable block.

    One block when there is no stable layer (nothing to cache) or
    when the whole system prompt is stable; two blocks otherwise:
    the stable prefix with ``cache_control`` and the rest without.
    The marker sits on the LAST block of the stable prefix, which
    is where Anthropic reads it.
    """
    system_prompt = surface.system_prompt
    breakpoint = surface.cache_breakpoint
    if breakpoint <= 0 or breakpoint >= len(system_prompt):
        block: dict = {"type": "text", "text": system_prompt}
        if breakpoint > 0:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]
    return [
        {
            "type": "text",
            "text": system_prompt[:breakpoint],
            "cache_control": {"type": "ephemeral"},
        },
        {"type": "text", "text": system_prompt[breakpoint:]},
    ]


def openai_messages(surface: PromptSurface) -> list[dict]:
    """OpenAI-shape transcript: system message first, then history.

    OpenAI caches the prompt prefix automatically, so no marker
    is added. The surface's only job for this format is to keep
    the stable layer byte-identical at the start of every turn.
    """
    return surface.as_messages()


def openai_request(surface: PromptSurface, *, model: str, tools: list | None = None) -> dict:
    """A chat-completions request dict for the OpenAI format.

    ``tools`` must already be OpenAI-format function tools; this
    function does not convert tool formats (that is
    ``puspa.router.translate.translate_request``). The returned
    dict is never the surface itself: mutating it cannot corrupt
    the frozen surface.
    """
    request: dict = {"model": model, "messages": openai_messages(surface)}
    if tools is not None:
        request["tools"] = [dict(tool) for tool in tools]
    return request


def anthropic_request(
    surface: PromptSurface,
    *,
    model: str,
    tools: list | None = None,
    max_tokens: int = DEFAULT_ANTHROPIC_MAX_TOKENS,
) -> dict:
    """A Messages API request dict for the Anthropic format.

    The system prompt travels as blocks (see
    :func:`anthropic_system_blocks`) instead of one string, so the
    cache marker survives. The history travels through
    ``puspa.router.translate.translate_request``: tool messages
    become ``tool_result`` blocks inside user turns and assistant
    ``tool_calls`` become ``tool_use`` blocks, which is the
    conversion the router already owns and tests. ``tools`` must
    already be Anthropic-format tools. Raises ``ValueError`` when
    the surface's messages carry a ``system`` role (Anthropic has
    no system-role messages, and silently relocating one into the
    system prompt would move prompt text) or when the history
    conversion drops anything: a truncated history must fail
    loud, never reach the provider silently shorter.
    """
    if any(
        isinstance(message, dict) and message.get("role") == "system"
        for message in surface.messages
    ):
        raise ValueError(
            "Anthropic requests cannot carry a 'system' message in "
            "messages; the system prompt travels as the top-level "
            "'system' blocks"
        )
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    # Local import: the router package is heavy (live completion
    # client); only this function needs the translator, and only
    # for shaping a request, never for a live call.
    from puspa.router.translate import translate_request

    converted = translate_request(
        {"model": model, "messages": list(surface.messages)},
        source="openai",
        target="anthropic",
    )
    if converted.report.dropped:
        dropped = ", ".join(str(drop) for drop in converted.report.dropped)
        raise ValueError(
            "history did not survive the OpenAI -> Anthropic "
            f"conversion, refusing a truncated request: {dropped}"
        )
    request: dict = {
        "model": model,
        "system": anthropic_system_blocks(surface),
        "messages": converted.payload["messages"],
        "max_tokens": max_tokens,
    }
    if tools is not None:
        request["tools"] = [dict(tool) for tool in tools]
    return request
