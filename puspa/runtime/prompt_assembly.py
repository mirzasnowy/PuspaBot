"""Layered system-prompt assembly with a frozen stable prefix (G20).

Pattern studied, then rewritten as Puspa's own code (see docs/reuse.md):
hermes-agent assembles its system prompt in fixed layers (``agent/
system_prompt.py`` + ``agent/prompt_builder.py``) with a separate
cache module (``agent/prompt_caching.py``) and a snapshot test
(``test_prompt_surface.py``). Only the PATTERN is taken: no
hermes-agent code is copied. hermes-agent is MIT, so the pattern
itself is safe to reimplement; every line below is written for Puspa.

The three layers, always in this order:

1. ``stable`` -- identity, tool guidance, skills. Frozen for the
   whole session: the stable layer is the cache prefix, so changing
   it mid-session would invalidate the provider's prompt cache and
   multiply the cost of every following turn.
2. ``context`` -- context files (project notes, workspace files).
   Rebuilt every turn; changing it does not move the cache
   breakpoint because it sits after the stable layer.
3. ``volatile`` -- memory, profile, timestamp. Rebuilt every turn.

Invariants the builder enforces:

- Layer order is fixed: stable, then context, then volatile.
- The stable layer is byte-identical across every turn of one
  session. The first ``build()`` call freezes it; a later call
  that passes a different stable layer still renders the frozen
  text, and the attempted change is recorded as a deferred
  ``DeferredChange`` (kind ``"replace_stable"``) for the next
  session instead of silently taking effect.
- ``cache_breakpoint`` is the character offset where the stable
  layer ends inside ``system_prompt``. Everything before it is
  the cacheable prefix; see ``puspa.runtime.prompt_caching`` for
  the provider markers built on top of it.
- The builder never compresses. It consumes the message list the
  context compressor (G19) produced exactly as given: messages
  pass through untouched, and the only mid-session mutation the
  surface ever sees is compression operating on ``messages``,
  never on the stable prompt.
- ``snapshot_hash`` covers the rendered prompt surface plus the
  tool list, so a prompt or tool change fails the snapshot test
  (``tests/test_prompt_surface.py``) until the snapshot is
  regenerated and committed together with the change.

Prompt-state-changing commands (add/remove a skill, switch the
model, replace the stable layer or the tool list) are deferred to
the next session unless the caller opts in explicitly. That rule
lives in :class:`PromptSession` and :class:`DeferredChangeQueue`
below; the builder itself only freezes and records.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

__all__ = [
    "LAYER_SEPARATOR",
    "DeferredChange",
    "DeferredChangeQueue",
    "PromptBuilder",
    "PromptSession",
    "PromptSurface",
]

#: Separator between the three layers. The same separator nanobot
#: uses between the parts of its own system prompt, so a surface
#: rendered here looks the same as one rendered there.
LAYER_SEPARATOR = "\n\n---\n\n"

#: Message roles the builder accepts, in OpenAI chat-completions
#: shape. A tool message carries ``tool_call_id``; an assistant
#: message that calls tools carries ``tool_calls``.
MESSAGE_ROLES = ("system", "user", "assistant", "tool")

#: Kinds of prompt-state-changing command the deferral queue knows.
CHANGE_KINDS = (
    "add_skill",
    "remove_skill",
    "set_model",
    "replace_stable",
    "replace_tools",
)


def _normalize_layer(value: str | object | None) -> str:
    """Normalize one layer to its canonical text.

    Accepts a string, an iterable of strings, or None. Parts are
    stripped of surrounding blank lines, empty parts are dropped,
    and the rest are joined with a blank line. Normalization is
    deterministic: the same logical layer always renders the same
    bytes, so a trailing newline in the input can never look like
    a stable-layer change.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        parts = [value]
    elif isinstance(value, Iterable):
        parts = list(value)
    else:
        raise ValueError(
            "a prompt layer must be a string, an iterable of "
            f"strings, or None, not {type(value).__name__}"
        )
    texts = []
    for part in parts:
        if not isinstance(part, str):
            raise ValueError(
                "a prompt layer must be a string, an iterable of "
                f"strings, or None, not a part of type "
                f"{type(part).__name__}"
            )
        text = part.strip()
        if text:
            texts.append(text)
    return "\n\n".join(texts)


def _canonical_json(value: object) -> str:
    """Canonical JSON for hashing: sorted keys, compact, stable."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=repr,
    )


def _validate_messages(messages: list) -> None:
    """Check the shared message contract; raise ValueError if broken.

    The check is deliberately light: the builder must accept any
    message list the context compressor produces, so it validates
    only the shape the downstream formats need (role, content,
    tool linkage) and never rewrites a message.
    """
    if not isinstance(messages, list):
        raise ValueError(f"messages must be a list, not {type(messages).__name__}")
    for index, message in enumerate(messages):
        where = f"messages[{index}]"
        if not isinstance(message, dict):
            raise ValueError(f"{where} must be a dict, not {type(message).__name__}")
        role = message.get("role")
        if role not in MESSAGE_ROLES:
            raise ValueError(f"{where}.role must be one of {MESSAGE_ROLES}, not {role!r}")
        if "content" not in message:
            if not (role == "assistant" and message.get("tool_calls")):
                raise ValueError(f"{where} is missing 'content'")
        elif message["content"] is not None and not isinstance(message["content"], (str, list)):
            raise ValueError(
                f"{where}.content must be a string, a list of blocks, "
                f"or None, not {type(message['content']).__name__}"
            )
        if role == "tool" and not message.get("tool_call_id"):
            raise ValueError(f"{where} (tool) is missing 'tool_call_id'")
        tool_calls = message.get("tool_calls")
        if tool_calls is not None and not isinstance(tool_calls, list):
            raise ValueError(f"{where}.tool_calls must be a list")


def _text_of_content(content: object) -> str:
    """Best-effort text of a message content, for rendering only."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text", "")))
        return "".join(texts)
    return str(content)


@dataclass(frozen=True)
class PromptSurface:
    """One rendered prompt surface: system prompt + messages.

    ``messages`` is the input list used as-is (the builder never
    compresses); ``cache_breakpoint`` is the character offset where
    the stable layer ends inside ``system_prompt``; ``snapshot_hash``
    covers the system prompt, the messages, and the tool list.
    """

    system_prompt: str
    messages: list
    cache_breakpoint: int
    snapshot_hash: str

    @property
    def stable_prefix(self) -> str:
        """The frozen stable layer: the cacheable prefix."""
        return self.system_prompt[: self.cache_breakpoint]

    def as_messages(self) -> list:
        """OpenAI-shape transcript: system message first, then history.

        Returns a new list holding the same message dicts; the
        builder never copies or rewrites a message.
        """
        return [{"role": "system", "content": self.system_prompt}, *self.messages]

    def to_prompt_text(self) -> str:
        """Flatten the surface to one string for ``complete(prompt)``.

        The router's ``complete()`` takes a single prompt string
        sent as one user message, so a surface that has to travel
        that path is rendered as labeled text: the system prompt,
        then the transcript with one line per turn. Tool calls and
        tool results are labeled with their ids so the flattened
        text stays unambiguous. This is a rendering bridge, not a
        second source of truth: the surface itself is authoritative.
        """
        lines = [self.system_prompt, "", "--- Transcript ---"]
        for message in self.messages:
            role = message.get("role", "user")
            content = _text_of_content(message.get("content"))
            if role == "assistant" and message.get("tool_calls"):
                calls = message["tool_calls"]
                names = []
                for call in calls:
                    if isinstance(call, dict):
                        function = call.get("function") or {}
                        names.append(str(function.get("name", "?")))
                    else:
                        names.append("?")
                label = f"Assistant (calls {', '.join(names)}):"
                lines.append(f"{label} {content}".rstrip())
            elif role == "tool":
                call_id = message.get("tool_call_id", "?")
                lines.append(f"Tool result ({call_id}): {content}".rstrip())
            elif role == "system":
                lines.append(f"System: {content}".rstrip())
            elif role == "assistant":
                lines.append(f"Assistant: {content}".rstrip())
            else:
                lines.append(f"User: {content}".rstrip())
        return "\n".join(lines)


@dataclass(frozen=True)
class DeferredChange:
    """One prompt-state-changing command held for the next session.

    ``kind`` is one of :data:`CHANGE_KINDS`; ``detail`` carries the
    JSON-able payload (for example ``{"skill": "billing", "text":
    "..."}`` for ``add_skill`` or ``{"model": "x/y"}`` for
    ``set_model``). ``seq`` is the queue-assigned order number,
    starting at 1; a change built by hand has seq 0 and is stamped
    when it enters a queue.
    """

    kind: str
    detail: dict = field(default_factory=dict)
    seq: int = 0

    def __post_init__(self) -> None:
        if self.kind not in CHANGE_KINDS:
            raise ValueError(
                f"unknown deferred-change kind {self.kind!r}; expected one of {CHANGE_KINDS}"
            )
        if not isinstance(self.detail, dict):
            raise ValueError("DeferredChange.detail must be a dict")


class DeferredChangeQueue:
    """A small FIFO queue for prompt-state-changing commands.

    Commands wait here until the next session unless the caller
    opts in with ``apply_now=True``. The queue itself never
    applies anything: it only records the order and hands the
    commands back, so the decision of *when* to apply stays with
    the session that owns it.
    """

    def __init__(self) -> None:
        self._pending: list[DeferredChange] = []
        self._next_seq = 1

    def request(self, change: DeferredChange, *, apply_now: bool = False) -> str:
        """File a command; return ``"applied"`` or ``"deferred"``.

        With ``apply_now=True`` the command is stamped and handed
        straight back as applied: the caller applies it to the
        current session right away. Otherwise it is queued for the
        next session.
        """
        stamped = replace(change, seq=self._next_seq)
        self._next_seq += 1
        if apply_now:
            return "applied"
        self._pending.append(stamped)
        return "deferred"

    def pending(self) -> list[DeferredChange]:
        """Queued commands, oldest first (a copy)."""
        return list(self._pending)

    def drain(self) -> list[DeferredChange]:
        """Take every queued command, oldest first; the queue empties."""
        drained, self._pending = self._pending, []
        return drained

    def __len__(self) -> int:
        return len(self._pending)

    def __bool__(self) -> bool:
        return bool(self._pending)


class PromptBuilder:
    """Assemble one prompt surface from three layers plus messages.

    The builder is session-scoped: construct one per session (or
    take it from :class:`PromptSession`), pass the same ``stable``
    layer every turn, and let the builder freeze it on the first
    build. ``tools`` are frozen at construction because the
    snapshot hash covers them; use :meth:`with_tools` for a new
    tool list, which is a next-session change.
    """

    def __init__(self, tools: list | None = None) -> None:
        self._tools: list = [dict(tool) for tool in (tools or [])]
        self._frozen_stable: str | None = None
        self._deferred: list[DeferredChange] = []

    @property
    def tools(self) -> list:
        """The frozen tool list (a copy; mutating it changes nothing)."""
        return [dict(tool) for tool in self._tools]

    @property
    def frozen_stable(self) -> str | None:
        """The stable layer frozen by the first build, or None yet."""
        return self._frozen_stable

    @property
    def pending_changes(self) -> list[DeferredChange]:
        """Stable-layer changes this builder refused mid-session."""
        return list(self._deferred)

    def with_tools(self, tools: list | None) -> PromptBuilder:
        """A fresh builder with a different tool list (next session)."""
        return PromptBuilder(tools=tools)

    def new_session(self) -> PromptBuilder:
        """A fresh builder with the same tools and no frozen stable."""
        return PromptBuilder(tools=self._tools)

    def build(
        self,
        messages: list,
        *,
        stable: str | object | None = None,
        context: str | object | None = None,
        volatile: str | object | None = None,
    ) -> PromptSurface:
        """Render the surface: three layers, then the messages as-is.

        The stable layer freezes on the first call. A later call
        that passes a different stable layer still renders the
        frozen text, and the attempt is recorded in
        :attr:`pending_changes` as a deferred ``"replace_stable"``
        change instead of taking effect mid-session. Context and
        volatile layers are rebuilt freely every turn.
        """
        _validate_messages(messages)
        stable_text = _normalize_layer(stable)
        if self._frozen_stable is None:
            self._frozen_stable = stable_text
        elif stable_text != self._frozen_stable:
            self._deferred.append(
                DeferredChange(
                    kind="replace_stable",
                    detail={
                        "attempted": stable_text,
                        "frozen": self._frozen_stable,
                    },
                )
            )
            stable_text = self._frozen_stable
        context_text = _normalize_layer(context)
        volatile_text = _normalize_layer(volatile)
        layers = [text for text in (stable_text, context_text, volatile_text) if text]
        system_prompt = LAYER_SEPARATOR.join(layers)
        cache_breakpoint = len(stable_text) if stable_text else 0
        snapshot_hash = hashlib.sha256(
            _canonical_json(
                {
                    "system_prompt": system_prompt,
                    "messages": messages,
                    "tools": self._tools,
                }
            ).encode("utf-8")
        ).hexdigest()
        return PromptSurface(
            system_prompt=system_prompt,
            messages=list(messages),
            cache_breakpoint=cache_breakpoint,
            snapshot_hash=snapshot_hash,
        )


class PromptSession:
    """One session's prompt state: frozen stable layer + deferral.

    Owns a :class:`PromptBuilder` and a :class:`DeferredChangeQueue`.
    The stable layer is kept as tagged parts (a base part plus one
    part per skill), so ``add_skill`` / ``remove_skill`` can apply
    cleanly at the next session instead of by string surgery.
    ``model`` is session state too: a model switch is a
    prompt-state-changing command, so it is deferred the same way.
    """

    def __init__(
        self,
        *,
        stable: str | object | None = None,
        tools: list | None = None,
        model: str | None = None,
    ) -> None:
        base = _normalize_layer(stable)
        self._parts: list[tuple[str, str]] = [("base", base)] if base else []
        self._tools: list = [dict(tool) for tool in (tools or [])]
        self.model = model
        self.changes = DeferredChangeQueue()
        self.turn_count = 0
        self._builder = PromptBuilder(tools=self._tools)

    @property
    def stable_text(self) -> str:
        """The frozen stable layer for this session."""
        return "\n\n".join(text for _, text in self._parts)

    @property
    def tools(self) -> list:
        """The session's tool list (a copy)."""
        return [dict(tool) for tool in self._tools]

    @property
    def pending_changes(self) -> list[DeferredChange]:
        """Commands queued for the next session, oldest first."""
        return self.changes.pending()

    def build(
        self,
        messages: list,
        *,
        context: str | object | None = None,
        volatile: str | object | None = None,
    ) -> PromptSurface:
        """Render this turn's surface from the frozen stable layer."""
        surface = self._builder.build(
            messages,
            stable=self.stable_text,
            context=context,
            volatile=volatile,
        )
        self.turn_count += 1
        return surface

    def request_change(
        self, kind: str, detail: dict | None = None, *, apply_now: bool = False
    ) -> str:
        """File a prompt-state-changing command.

        Returns ``"deferred"`` (queued for the next session) or
        ``"applied"`` (opt-in: applied to this session right away).
        """
        change = DeferredChange(kind=kind, detail=dict(detail or {}))
        outcome = self.changes.request(change, apply_now=apply_now)
        if outcome == "applied":
            self._apply(change)
        return outcome

    def next_session(self) -> PromptSession:
        """Start the next session with every queued change applied.

        The queue drains in order; the new session freezes the
        updated stable layer, tool list, and model on its first
        build. This session is untouched: its builder keeps the
        old frozen stable layer until it is dropped.
        """
        queued = self.changes.drain()
        nxt = PromptSession(tools=self._tools, model=self.model)
        # Carry the tagged parts over (not the collapsed text), so a
        # queued remove_skill still finds its skill part to drop.
        nxt._parts = list(self._parts)
        for change in queued:
            nxt._apply(change)
        return nxt

    def _apply(self, change: DeferredChange) -> None:
        """Apply one change to this session's state, immediately."""
        if change.kind == "add_skill":
            name = str(change.detail.get("skill", "")).strip()
            text = str(change.detail.get("text", "")).strip()
            if not name:
                raise ValueError("add_skill needs detail['skill']")
            self._parts = [(tag, text_) for tag, text_ in self._parts if tag != f"skill:{name}"]
            if text:
                self._parts.append((f"skill:{name}", f"# Skill: {name}\n\n{text}"))
            self._builder = PromptBuilder(tools=self._tools)
        elif change.kind == "remove_skill":
            name = str(change.detail.get("skill", "")).strip()
            if not name:
                raise ValueError("remove_skill needs detail['skill']")
            self._parts = [(tag, text) for tag, text in self._parts if tag != f"skill:{name}"]
            self._builder = PromptBuilder(tools=self._tools)
        elif change.kind == "set_model":
            model = str(change.detail.get("model", "")).strip()
            if not model:
                raise ValueError("set_model needs detail['model']")
            self.model = model
        elif change.kind == "replace_stable":
            # "text" is the session API; "attempted" is what the
            # builder records when it refuses a mid-session change.
            raw = change.detail.get("text", change.detail.get("attempted"))
            text = _normalize_layer(raw)
            self._parts = [("base", text)] if text else []
            self._builder = PromptBuilder(tools=self._tools)
        elif change.kind == "replace_tools":
            tools = change.detail.get("tools")
            if not isinstance(tools, list):
                raise ValueError("replace_tools needs detail['tools'] as a list")
            self._tools = [dict(tool) for tool in tools]
            self._builder = PromptBuilder(tools=self._tools)
        else:  # pragma: no cover - DeferredChange already validates kind
            raise ValueError(f"unknown deferred-change kind {change.kind!r}")
