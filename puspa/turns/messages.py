"""One internal message format for all three provider API modes (G21).

Pattern studied, then rewritten as Puspa's own code (see
``docs/reuse.md``): hermes-agent meets three API modes (chat
completions, Responses, Anthropic Messages) in one internal
message format, so the rest of the system never cares which
provider is behind the call. Only the PATTERN is taken: no
hermes-agent code is copied.

The internal format is :class:`InternalMessage`: a role plus a
list of content blocks. Block types:

- ``{"type": "text", "text": str}``
- ``{"type": "image", "source": {"type": "url", "url": str}}`` or
  ``{"type": "image", "source": {"type": "base64", "media_type",
  "data": str}}``
- ``{"type": "tool_call", "id": str, "name": str, "arguments":
  dict}``
- ``{"type": "tool_result", "tool_call_id": str, "content": str,
  "is_error": bool}``

Every converter returns a :class:`MessageConversion` (value plus
the report of what did not cross), following the same discipline
as ``puspa.router.translate``: a field with no equivalent on the
other side is REPORTED, never silently dropped. A structurally
invalid payload raises ``ValueError`` — that is a broken payload,
not an unmapped field.

Wire conversions between OpenAI chat-completions and Anthropic
Messages reuse ``puspa.router.translate`` (the existing wire
translator) instead of re-implementing the mapping: internal ->
chat-completions -> translate -> Anthropic, and back. The
Responses API has no translator yet, so its converters are
written directly here.

Alternation (``enforce_alternation`` / ``validate_alternation``)
is enforced in code, never trusted from the model: before any
call, history must alternate user / assistant / user /
assistant, system messages live only at the start, and a tool
result must follow the assistant turn that called it. Repair is
deterministic — consecutive same-role messages are merged,
stray system messages become user messages, orphan tool results
become user messages — and every repair is reported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

__all__ = [
    "ROLES",
    "InternalMessage",
    "MessageConversion",
    "enforce_alternation",
    "from_anthropic",
    "from_chat_completions",
    "from_responses",
    "to_anthropic",
    "to_chat_completions",
    "to_responses",
    "validate_alternation",
]

ROLES = ("system", "user", "assistant", "tool")

#: OpenAI Responses API item types this module knows how to read.
#: ``reasoning`` items are dropped with a report entry: the turn
#: loop keeps no reasoning trace in history.
_RESPONSES_KNOWN_OUTPUT_TYPES = ("message", "function_call", "reasoning")


@dataclass
class MessageConversion[T]:
    """A converted payload plus what did not cross."""

    value: T
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class InternalMessage:
    """One message in Puspa's internal format.

    ``blocks`` are the content blocks listed in the module
    docstring. ``role`` is one of ``system`` / ``user`` /
    ``assistant`` / ``tool``: a ``tool`` message carries exactly
    one ``tool_result`` block.
    """

    role: str
    blocks: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role: {self.role!r} (want one of {ROLES})")

    @classmethod
    def system(cls, text: str) -> InternalMessage:
        return cls(role="system", blocks=[{"type": "text", "text": text}] if text else [])

    @classmethod
    def user(cls, text: str, *, images: list[dict] | None = None) -> InternalMessage:
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for image in images or []:
            blocks.append({"type": "image", "source": dict(image)})
        return cls(role="user", blocks=blocks)

    @classmethod
    def assistant(cls, text: str = "", *, tool_calls: list[dict] | None = None) -> InternalMessage:
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        for call in tool_calls or []:
            blocks.append(
                {
                    "type": "tool_call",
                    "id": call.get("id") or "",
                    "name": call.get("name", ""),
                    "arguments": dict(call.get("arguments") or {}),
                }
            )
        return cls(role="assistant", blocks=blocks)

    @classmethod
    def tool_result(
        cls, tool_call_id: str, content: str, *, is_error: bool = False
    ) -> InternalMessage:
        return cls(
            role="tool",
            blocks=[
                {
                    "type": "tool_result",
                    "tool_call_id": tool_call_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        )

    @property
    def text(self) -> str:
        """Plain text of the message.

        Text blocks joined; a tool message has no text blocks,
        so its tool-result content is its text.
        """
        joined = "".join(
            block.get("text", "")
            for block in self.blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if joined:
            return joined
        result = self.tool_result_block
        if result is not None:
            return str(result.get("content") or "")
        return ""

    @property
    def tool_calls(self) -> list[dict]:
        """Tool-call blocks (assistant messages only)."""
        return [
            block
            for block in self.blocks
            if isinstance(block, dict) and block.get("type") == "tool_call"
        ]

    @property
    def tool_result_block(self) -> dict | None:
        """The tool-result block (tool messages only), or None."""
        for block in self.blocks:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return block
        return None

    def to_dict(self) -> dict:
        return {"role": self.role, "blocks": [dict(block) for block in self.blocks]}

    @classmethod
    def from_dict(cls, data: dict) -> InternalMessage:
        if not isinstance(data, dict):
            raise ValueError(f"message must be a dict, got {type(data).__name__}")
        return cls(role=data.get("role", ""), blocks=[dict(b) for b in (data.get("blocks") or [])])

    def char_count(self) -> int:
        """Rough size of the message, for overflow checks.

        Summed per block (not via ``text`` — a tool message's
        text IS its tool-result content, so counting both would
        double-count it).
        """
        total = 0
        for block in self.blocks:
            if block.get("type") == "text":
                total += len(block.get("text") or "")
            elif block.get("type") == "tool_call":
                total += len(json.dumps(block.get("arguments") or {}, ensure_ascii=False))
            elif block.get("type") == "tool_result":
                total += len(str(block.get("content") or ""))
            elif block.get("type") == "image":
                total += 1000  # an image is not text; count a flat budget
        return total


# ---------------------------------------------------------------------------
# Chat completions (OpenAI wire format)
# ---------------------------------------------------------------------------


def to_chat_completions(messages: list[InternalMessage]) -> MessageConversion[list[dict]]:
    """Internal messages -> OpenAI chat-completions message list.

    Tool calls ride on the assistant message (``tool_calls``);
    tool results become ``role: "tool"`` messages with
    ``tool_call_id``. A tool message with no matching assistant
    call id is still sent (the provider rejects a dangling id,
    which surfaces as a loud API error, never a silent fix-up
    here — the alternation enforcer upstream prevents it).
    """
    dropped: list[str] = []
    wire: list[dict] = []
    for index, message in enumerate(messages):
        if message.role == "system":
            wire.append({"role": "system", "content": message.text})
            continue
        if message.role == "user":
            parts: list[dict] = []
            for block in message.blocks:
                if block.get("type") == "text":
                    parts.append({"type": "text", "text": block.get("text", "")})
                elif block.get("type") == "image":
                    source = block.get("source") or {}
                    if source.get("type") == "base64":
                        url = (
                            f"data:{source.get('media_type', 'image/png')};base64,"
                            f"{source.get('data', '')}"
                        )
                    else:
                        url = source.get("url", "")
                    if not url:
                        dropped.append(f"messages[{index}]: image block with no usable source")
                        continue
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                else:
                    dropped.append(
                        f"messages[{index}]: {block.get('type')!r} block has "
                        "no chat-completions equivalent"
                    )
            wire.append(
                {
                    "role": "user",
                    "content": parts
                    if len(parts) != 1 or parts[0].get("type") != "text"
                    else parts[0]["text"],
                }
            )
            continue
        if message.role == "assistant":
            out: dict = {"role": "assistant", "content": message.text or None}
            calls = message.tool_calls
            if calls:
                out["tool_calls"] = [
                    {
                        "id": call.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": call.get("name", ""),
                            "arguments": json.dumps(
                                call.get("arguments") or {}, ensure_ascii=False
                            ),
                        },
                    }
                    for call in calls
                ]
            wire.append(out)
            continue
        if message.role == "tool":
            result = message.tool_result_block
            if result is None:
                dropped.append(f"messages[{index}]: tool message with no tool_result block")
                continue
            wire.append(
                {
                    "role": "tool",
                    "tool_call_id": result.get("tool_call_id") or "",
                    "content": result.get("content", ""),
                }
            )
            continue
        dropped.append(f"messages[{index}]: role {message.role!r} has no wire equivalent")
    return MessageConversion(value=wire, dropped=dropped)


def from_chat_completions(wire: list[dict]) -> MessageConversion[list[InternalMessage]]:
    """OpenAI chat-completions message list -> internal messages."""
    if not isinstance(wire, list):
        raise ValueError(f"chat-completions messages must be a list, got {type(wire).__name__}")
    dropped: list[str] = []
    messages: list[InternalMessage] = []
    for index, item in enumerate(wire):
        if not isinstance(item, dict):
            dropped.append(f"messages[{index}]: message is not an object")
            continue
        role = item.get("role")
        if role == "system":
            messages.append(InternalMessage.system(_wire_text(item.get("content"))))
            continue
        if role == "user":
            messages.append(_wire_user_to_internal(item, index, dropped))
            continue
        if role == "assistant":
            calls: list[dict] = []
            for call_index, call in enumerate(item.get("tool_calls") or []):
                if not isinstance(call, dict):
                    dropped.append(
                        f"messages[{index}].tool_calls[{call_index}]: call is not an object"
                    )
                    continue
                function = call.get("function") or {}
                arguments = function.get("arguments", "{}")
                if isinstance(arguments, dict):
                    arguments_dict = arguments
                elif isinstance(arguments, str):
                    try:
                        parsed = json.loads(arguments) if arguments.strip() else {}
                    except ValueError:
                        parsed = None
                    if isinstance(parsed, dict):
                        arguments_dict = parsed
                    else:
                        dropped.append(
                            f"messages[{index}].tool_calls[{call_index}]: "
                            "arguments are not a JSON object; sent as an empty input"
                        )
                        arguments_dict = {}
                else:
                    dropped.append(
                        f"messages[{index}].tool_calls[{call_index}]: "
                        "arguments are not a JSON object; sent as an empty input"
                    )
                    arguments_dict = {}
                calls.append(
                    {
                        "id": call.get("id") or "",
                        "name": function.get("name", ""),
                        "arguments": arguments_dict,
                    }
                )
            messages.append(
                InternalMessage.assistant(_wire_text(item.get("content")), tool_calls=calls)
            )
            continue
        if role == "tool":
            messages.append(
                InternalMessage.tool_result(
                    item.get("tool_call_id") or "", _wire_text(item.get("content"))
                )
            )
            continue
        dropped.append(f"messages[{index}]: role {role!r} has no internal equivalent")
    return MessageConversion(value=messages, dropped=dropped)


def _wire_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return "" if content is None else str(content)


def _wire_user_to_internal(item: dict, index: int, dropped: list[str]) -> InternalMessage:
    content = item.get("content")
    if isinstance(content, str):
        return InternalMessage.user(content)
    if not isinstance(content, list):
        return InternalMessage.user(_wire_text(content))
    text_parts: list[str] = []
    images: list[dict] = []
    for part_index, part in enumerate(content):
        if not isinstance(part, dict):
            text_parts.append(str(part))
            continue
        kind = part.get("type")
        if kind == "text":
            text_parts.append(part.get("text", ""))
        elif kind == "image_url":
            image_url = part.get("image_url") or {}
            url = image_url.get("url", "") if isinstance(image_url, dict) else ""
            if url.startswith("data:"):
                try:
                    header, data = url[5:].split(",", 1)
                    media_type = header.split(";")[0] or "image/png"
                except ValueError:
                    dropped.append(f"messages[{index}].content[{part_index}]: malformed data URL")
                    continue
                images.append({"type": "base64", "media_type": media_type, "data": data})
            elif url:
                images.append({"type": "url", "url": url})
            else:
                dropped.append(f"messages[{index}].content[{part_index}]: empty image URL")
        elif kind == "input_audio":
            dropped.append(
                f"messages[{index}].content[{part_index}]: input_audio has no internal equivalent"
            )
        else:
            dropped.append(
                f"messages[{index}].content[{part_index}]: type {kind!r} has no internal equivalent"
            )
    return InternalMessage.user("".join(text_parts), images=images)


# ---------------------------------------------------------------------------
# Anthropic Messages (via the router's wire translator)
# ---------------------------------------------------------------------------


def to_anthropic(messages: list[InternalMessage]) -> MessageConversion[dict]:
    """Internal messages -> Anthropic Messages request payload.

    Built by converting to chat-completions first and running the
    router's wire translator (``puspa.router.translate``), so the
    two formats can never drift apart. The translator's report
    becomes this conversion's dropped/notes lists.
    """
    from puspa.router.translate import translate_request

    chat = to_chat_completions(messages)
    payload = {"messages": chat.value}
    result = translate_request(payload, source="openai", target="anthropic")
    dropped = list(chat.dropped) + [str(drop) for drop in result.report.dropped]
    notes = list(result.report.notes)
    return MessageConversion(value=result.payload, dropped=dropped, notes=notes)


def from_anthropic(payload: dict) -> MessageConversion[list[InternalMessage]]:
    """Anthropic Messages request payload -> internal messages.

    The reverse trip through the router's wire translator: the
    Anthropic request becomes a chat-completions request, which
    becomes internal messages. ``payload`` is a REQUEST
    (``{system, messages, ...}``), not a response.
    """
    from puspa.router.translate import translate_request

    if not isinstance(payload, dict):
        raise ValueError(f"Anthropic payload must be a dict, got {type(payload).__name__}")
    result = translate_request(payload, source="anthropic", target="openai")
    internal = from_chat_completions(result.payload.get("messages") or [])
    dropped = [str(drop) for drop in result.report.dropped] + list(internal.dropped)
    notes = list(result.report.notes)
    return MessageConversion(value=internal.value, dropped=dropped, notes=notes)


# ---------------------------------------------------------------------------
# Responses API (OpenAI Responses wire format)
# ---------------------------------------------------------------------------


def to_responses(messages: list[InternalMessage]) -> MessageConversion[dict]:
    """Internal messages -> OpenAI Responses API request payload.

    History becomes ``input`` items: role messages carry
    ``input_text`` (user/system) or ``output_text`` (assistant)
    content, assistant tool calls become ``function_call`` items,
    and tool results become ``function_call_output`` items.
    """
    dropped: list[str] = []
    items: list[dict] = []
    for index, message in enumerate(messages):
        if message.role in ("system", "user"):
            parts: list[dict] = []
            for block in message.blocks:
                if block.get("type") == "text":
                    parts.append({"type": "input_text", "text": block.get("text", "")})
                elif block.get("type") == "image":
                    source = block.get("source") or {}
                    if source.get("type") == "base64":
                        url = (
                            f"data:{source.get('media_type', 'image/png')};base64,"
                            f"{source.get('data', '')}"
                        )
                    else:
                        url = source.get("url", "")
                    if not url:
                        dropped.append(f"messages[{index}]: image block with no usable source")
                        continue
                    parts.append({"type": "input_image", "image_url": url})
                else:
                    dropped.append(
                        f"messages[{index}]: {block.get('type')!r} block has "
                        "no Responses input equivalent"
                    )
            items.append({"role": message.role, "content": parts})
            continue
        if message.role == "assistant":
            if message.text:
                items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": message.text}],
                    }
                )
            for call in message.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id") or "",
                        "name": call.get("name", ""),
                        "arguments": json.dumps(call.get("arguments") or {}, ensure_ascii=False),
                    }
                )
            continue
        if message.role == "tool":
            result = message.tool_result_block
            if result is None:
                dropped.append(f"messages[{index}]: tool message with no tool_result block")
                continue
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": result.get("tool_call_id") or "",
                    "output": result.get("content", ""),
                }
            )
            continue
        dropped.append(f"messages[{index}]: role {message.role!r} has no Responses equivalent")
    return MessageConversion(value={"input": items}, dropped=dropped)


def from_responses(payload: dict) -> MessageConversion[list[InternalMessage]]:
    """OpenAI Responses payload -> internal messages.

    Accepts a Responses REQUEST (``{"input": [...]}``) or a
    RESPONSE (``{"output": [...]}``). Response ``message`` items
    become assistant messages; ``function_call`` items attach to
    the running assistant message; ``reasoning`` items are
    dropped with a report entry (the loop keeps no reasoning
    trace in history); any other item type is reported, never
    silently swallowed.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"Responses payload must be a dict, got {type(payload).__name__}")
    items = payload.get("output")
    if items is None:
        items = payload.get("input")
    if items is None:
        raise ValueError("Responses payload has neither 'output' nor 'input' items")
    if not isinstance(items, list):
        raise ValueError("Responses items must be a list")
    dropped: list[str] = []
    messages: list[InternalMessage] = []
    pending_assistant: InternalMessage | None = None

    def flush_assistant() -> None:
        nonlocal pending_assistant
        if pending_assistant is not None:
            messages.append(pending_assistant)
            pending_assistant = None

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            dropped.append(f"items[{index}]: item is not an object")
            continue
        kind = item.get("type")
        if kind == "message":
            flush_assistant()
            role = item.get("role", "assistant")
            text = ""
            images: list[dict] = []
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    text += str(part)
                    continue
                part_type = part.get("type")
                if part_type in ("output_text", "input_text", "text"):
                    text += part.get("text", "")
                elif part_type == "input_image":
                    url = part.get("image_url", "")
                    if url.startswith("data:"):
                        try:
                            header, data = url[5:].split(",", 1)
                            media_type = header.split(";")[0] or "image/png"
                        except ValueError:
                            dropped.append(f"items[{index}]: malformed image data URL")
                            continue
                        images.append({"type": "base64", "media_type": media_type, "data": data})
                    elif url:
                        images.append({"type": "url", "url": url})
                else:
                    dropped.append(
                        f"items[{index}]: content part type {part_type!r} has "
                        "no internal equivalent"
                    )
            if role == "assistant":
                pending_assistant = InternalMessage.assistant(text)
            elif role == "user":
                messages.append(InternalMessage.user(text, images=images))
            elif role == "system":
                messages.append(InternalMessage.system(text))
            else:
                dropped.append(f"items[{index}]: role {role!r} has no internal equivalent")
            continue
        if kind == "function_call":
            arguments = item.get("arguments", "{}")
            if isinstance(arguments, dict):
                arguments_dict = arguments
            elif isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments) if arguments.strip() else {}
                except ValueError:
                    parsed = None
                if isinstance(parsed, dict):
                    arguments_dict = parsed
                else:
                    dropped.append(
                        f"items[{index}]: arguments are not a JSON object; sent as an empty input"
                    )
                    arguments_dict = {}
            else:
                dropped.append(
                    f"items[{index}]: arguments are not a JSON object; sent as an empty input"
                )
                arguments_dict = {}
            call = {
                "id": item.get("call_id") or item.get("id") or "",
                "name": item.get("name", ""),
                "arguments": arguments_dict,
            }
            if pending_assistant is None:
                pending_assistant = InternalMessage.assistant("", tool_calls=[call])
            else:
                pending_assistant.blocks.append(
                    {
                        "type": "tool_call",
                        "id": call["id"],
                        "name": call["name"],
                        "arguments": call["arguments"],
                    }
                )
            continue
        if kind == "function_call_output":
            flush_assistant()
            messages.append(
                InternalMessage.tool_result(item.get("call_id") or "", str(item.get("output", "")))
            )
            continue
        if kind in ("reasoning", "item_reference"):
            dropped.append(f"items[{index}]: {kind} item is not kept in turn history")
            continue
        # Bare role message without a "type" (request input shape).
        if kind is None and "role" in item:
            flush_assistant()
            role = item.get("role")
            text = "".join(
                part.get("text", "")
                for part in (item.get("content") or [])
                if isinstance(part, dict)
                and part.get("type") in ("input_text", "output_text", "text")
            )
            if role == "user":
                messages.append(InternalMessage.user(text))
            elif role == "assistant":
                pending_assistant = InternalMessage.assistant(text)
            elif role == "system":
                messages.append(InternalMessage.system(text))
            else:
                dropped.append(f"items[{index}]: role {role!r} has no internal equivalent")
            continue
        dropped.append(f"items[{index}]: type {kind!r} has no internal equivalent")
    flush_assistant()
    return MessageConversion(value=messages, dropped=dropped)


# ---------------------------------------------------------------------------
# Alternation
# ---------------------------------------------------------------------------


def _tool_call_ids(message: InternalMessage) -> set[str]:
    """Ids of a message's tool calls, as strings (empty ids dropped)."""
    return {str(call["id"]) for call in message.tool_calls if call.get("id")}


def validate_alternation(messages: list[InternalMessage]) -> list[str]:
    """List every alternation violation in a history, without fixing.

    Rules: system messages only at the start; after the system
    block, roles must strictly alternate user / assistant / user
    / assistant, where a run of tool messages counts as part of
    the assistant turn that called them (tool messages must
    follow an assistant message that holds a matching tool call).
    An empty history has no violations.
    """
    violations: list[str] = []
    seen_non_system = False
    previous_role: str | None = None
    pending_tool_ids: set[str] = set()
    for index, message in enumerate(messages):
        if message.role == "system":
            if seen_non_system:
                violations.append(f"messages[{index}]: system message after a non-system message")
            continue
        seen_non_system = True
        if message.role == "tool":
            result = message.tool_result_block
            call_id = result.get("tool_call_id") if result else None
            if not call_id or call_id not in pending_tool_ids:
                violations.append(
                    f"messages[{index}]: tool result with no matching assistant tool call before it"
                )
            # A tool run belongs to the assistant turn that called
            # it: the next assistant message is a new turn, not a
            # second assistant message in a row.
            previous_role = "tool"
            continue
        if message.role == "assistant":
            if previous_role == "assistant":
                violations.append(f"messages[{index}]: two assistant messages in a row")
            pending_tool_ids = _tool_call_ids(message)
        elif message.role == "user":
            if previous_role == "user":
                violations.append(f"messages[{index}]: two user messages in a row")
            pending_tool_ids = set()
        else:
            violations.append(f"messages[{index}]: unknown role {message.role!r}")
        previous_role = message.role
    return violations


def enforce_alternation(
    messages: list[InternalMessage],
) -> MessageConversion[list[InternalMessage]]:
    """Repair a history so roles alternate, reporting every repair.

    Deterministic repairs, in order:
    - a system message after the start becomes a user message;
    - consecutive same-role user/assistant messages are merged
      (text joined with a blank line, tool calls kept in order);
    - a tool message with no matching assistant tool call before
      it becomes a user message carrying its content;
    - a tool message directly after a user message (no assistant
      turn) becomes a user message the same way.
    The input list is never mutated. An empty history is returned
    unchanged. A message with an unknown role raises ValueError:
    that is a broken history, not a repairable one.
    """
    for message in messages:
        if message.role not in ROLES:
            raise ValueError(f"unknown role: {message.role!r} (want one of {ROLES})")
    repairs: list[str] = []
    fixed: list[InternalMessage] = []
    pending_tool_ids: set[str] = set()

    def append_user_text(text: str, *, index: int, what: str) -> None:
        """Append text as a user message, merging into a previous
        user message instead of creating two in a row."""
        if fixed and fixed[-1].role == "user":
            joined = (fixed[-1].text + "\n\n" + text).strip()
            blocks: list[dict] = [
                block for block in fixed[-1].blocks if block.get("type") != "text"
            ]
            if joined:
                blocks.insert(0, {"type": "text", "text": joined})
            fixed[-1] = InternalMessage(role="user", blocks=blocks)
            repairs.append(f"messages[{index}]: {what} merged into the previous user message")
        else:
            fixed.append(InternalMessage.user(text))
            repairs.append(f"messages[{index}]: {what} became a user message")

    def merge_into_last(message: InternalMessage, *, index: int) -> None:
        last = fixed[-1]
        joined = (last.text + "\n\n" + message.text).strip()
        blocks: list[dict] = []
        for block in last.blocks:
            if block.get("type") != "text":
                blocks.append(dict(block))
        if joined:
            blocks.insert(0, {"type": "text", "text": joined})
        for block in message.blocks:
            if block.get("type") == "tool_call":
                blocks.append(dict(block))
        fixed[-1] = InternalMessage(role=last.role, blocks=blocks)
        repairs.append(f"messages[{index}]: merged into the previous {last.role} message")

    for index, message in enumerate(messages):
        if message.role == "system":
            if fixed:
                append_user_text(message.text, index=index, what="system message after the start")
            else:
                fixed.append(message)
            continue
        if message.role == "tool":
            result = message.tool_result_block
            call_id = result.get("tool_call_id") if result else None
            if (
                not fixed
                or fixed[-1].role != "assistant"
                or not call_id
                or call_id not in pending_tool_ids
            ):
                content = result.get("content", "") if result else ""
                append_user_text(content, index=index, what="orphan tool result")
            else:
                fixed.append(message)
            continue
        if fixed and fixed[-1].role == message.role:
            merge_into_last(message, index=index)
            if message.role == "assistant":
                pending_tool_ids |= _tool_call_ids(message)
            continue
        fixed.append(message)
        pending_tool_ids = _tool_call_ids(message) if message.role == "assistant" else set()
    return MessageConversion(value=fixed, dropped=[], notes=repairs)
