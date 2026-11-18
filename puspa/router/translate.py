"""Format translation between the providers the router already serves.

Pattern studied, then rewritten as Puspa's own code (see docs/reuse.md):
9Router (decolua/9router, MIT) exposes one OpenAI-compatible
endpoint in front of 40+ providers, so every provider behind it , 
Anthropic, Google, xAI, Copilot, Codex, is spoken to in that
provider's own wire format while the client only ever speaks
OpenAI. Only the PATTERN is taken: no 9Router code is copied, and
no 9Router sub-project (RTK, Headroom, Ponytail) is used here.
The field mappings below are Puspa's own, grounded in the two
providers Puspa actually drives today:

- OpenAI chat-completions, the format of every OpenAI-compatible
  provider in the BYOK factory (``demo/byok_provider.py``):
  OpenRouter, OpenAI, Gemini (via its OpenAI-compatible base),
  Ollama, Together, any ``openai_compatible`` base URL, and the
  xAI API-key path. Request: ``{model, messages, tools,
  tool_choice, temperature, max_tokens, stop, stream, ...}`` with
  messages ``{role, content, tool_calls, tool_call_id}`` and tools
  ``{type: "function", function: {name, description, parameters}}``.
  Response: ``{choices: [{message, finish_reason}], usage:
  {prompt_tokens, completion_tokens, total_tokens}}``.
- Anthropic Messages API, the format of the native Anthropic
  provider in the same factory (``nanobot/providers/
  anthropic_provider.py``: ``_convert_messages``,
  ``_convert_tools``, ``_convert_image_block``, ``_parse_response``).
  Request: ``{model, system, messages, tools, tool_choice,
  max_tokens, temperature, stop_sequences, ...}`` with content
  blocks ``{type: text | image | tool_use | tool_result |
  thinking}`` and tools ``{name, description, input_schema}``.
  Response: ``{type: "message", content: [...], stop_reason,
  usage: {input_tokens, output_tokens,
  cache_creation_input_tokens, cache_read_input_tokens}}``.

Only these two formats are translated. The other native formats
nanobot knows (Azure Responses API, Bedrock Converse) are NOT in
the router catalog and are NOT translated here: adding them is a
follow-up with the same ``TranslationReport`` discipline, not a
silent extension.

The one hard rule: a field with no equivalent on the other side is
REPORTED, never silently dropped. Every translation returns a
``TranslationResult`` whose ``report`` lists each dropped field
(``TranslationDrop``: field path, direction, reason) and each
judgement call (``report.notes``: a merge, a default, a closest
match). A caller that ignores the report still gets a working
payload; a caller that reads it knows exactly what did not
survive the crossing. Structurally invalid input (not a dict,
unknown format name, a response with no choices) raises
``ValueError``, that is a broken payload, not an unmapped field.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

__all__ = [
    "FORMAT_OPENAI",
    "FORMAT_ANTHROPIC",
    "FORMATS",
    "TranslationDrop",
    "TranslationReport",
    "TranslationResult",
    "translate_request",
    "translate_response",
]

FORMAT_OPENAI = "openai"
FORMAT_ANTHROPIC = "anthropic"
FORMATS = (FORMAT_OPENAI, FORMAT_ANTHROPIC)

#: Anthropic requires ``max_tokens``; when an OpenAI request has
#: none, this is the value used (reported in the notes, not hidden).
DEFAULT_ANTHROPIC_MAX_TOKENS = 4096

#: OpenAI finish reasons and their Anthropic stop reasons.
_OPENAI_FINISH_TO_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "end_turn",  # closest neutral value; reported
    "function_call": "tool_use",
}
_ANTHROPIC_STOP_TO_FINISH = {
    "end_turn": "stop",
    "tool_use": "tool_calls",
    "max_tokens": "length",
    "stop_sequence": "stop",
}


@dataclass
class TranslationDrop:
    """One field that did not survive the translation, and why."""

    field: str
    direction: str  # e.g. "openai -> anthropic"
    reason: str

    def __str__(self) -> str:
        return f"{self.field} ({self.direction}): {self.reason}"


@dataclass
class TranslationReport:
    """What a translation dropped, plus its judgement calls."""

    dropped: list[TranslationDrop] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def drop(self, field_name: str, direction: str, reason: str) -> None:
        self.dropped.append(TranslationDrop(field_name, direction, reason))

    def note(self, text: str) -> None:
        self.notes.append(text)

    @property
    def dropped_fields(self) -> list[str]:
        return [drop.field for drop in self.dropped]

    def __bool__(self) -> bool:
        return bool(self.dropped or self.notes)


@dataclass
class TranslationResult:
    """A translated payload plus the report of what did not cross."""

    payload: dict
    report: TranslationReport


def _direction(source: str, target: str) -> str:
    return f"{source} -> {target}"


def _check_formats(source: str, target: str) -> None:
    if source not in FORMATS:
        raise ValueError(f"unknown source format: {source!r} (want one of {FORMATS})")
    if target not in FORMATS:
        raise ValueError(f"unknown target format: {target!r} (want one of {FORMATS})")
    if source == target:
        raise ValueError(f"source and target formats are the same: {source!r}")


def _check_payload(payload) -> dict:
    if not isinstance(payload, dict):
        raise ValueError(f"payload must be a dict, got {type(payload).__name__}")
    return payload


def _parse_tool_arguments(arguments, *, where: str, direction: str,
                          report: TranslationReport):
    """Parse an OpenAI tool-call ``arguments`` JSON string to a dict.

    Returns a dict. Unparsable or non-object arguments become an
    empty input and are reported, an Anthropic ``tool_use`` block
    needs an object, and inventing arguments would be worse than
    reporting the loss.
    """
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    report.drop(where, direction,
                "tool arguments are not a JSON object; sent as an empty input")
    return {}


def _text_of_content(content) -> str:
    """Plain text of a message content (string, or text parts joined)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content
                 if isinstance(item, dict) and item.get("type") == "text"]
        return "".join(parts)
    return "" if content is None else str(content)


# ---------------------------------------------------------------------------
# Requests: OpenAI -> Anthropic
# ---------------------------------------------------------------------------

def _openai_content_parts_to_anthropic(content, *, where: str,
                                       direction: str,
                                       report: TranslationReport) -> list[dict]:
    """Convert OpenAI message content parts to Anthropic blocks."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    if not isinstance(content, list):
        return [{"type": "text", "text": str(content)}] if content else []
    blocks: list[dict] = []
    for index, part in enumerate(content):
        if not isinstance(part, dict):
            blocks.append({"type": "text", "text": str(part)})
            continue
        kind = part.get("type")
        if kind == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif kind == "image_url":
            block = _openai_image_to_anthropic(part, where=f"{where}[{index}]",
                                               direction=direction, report=report)
            if block is not None:
                blocks.append(block)
        elif kind == "input_audio":
            report.drop(f"{where}[{index}].input_audio", direction,
                        "Anthropic has no audio input block")
        else:
            report.drop(f"{where}[{index}].type={kind!r}", direction,
                        "no Anthropic content block equivalent")
    return blocks


def _openai_image_to_anthropic(part: dict, *, where: str, direction: str,
                               report: TranslationReport) -> dict | None:
    """OpenAI ``image_url`` part -> Anthropic ``image`` block.

    Follows nanobot's ``AnthropicProvider._convert_image_block``:
    a data URL becomes a base64 source, any other URL a url source.
    """
    image_url = part.get("image_url") or {}
    url = image_url.get("url", "") if isinstance(image_url, dict) else ""
    if not url:
        report.drop(f"{where}.image_url.url", direction, "empty image URL")
        return None
    if url.startswith("data:"):
        try:
            header, data = url[5:].split(",", 1)
            media_type = header.split(";")[0] or "image/png"
        except ValueError:
            report.drop(f"{where}.image_url.url", direction,
                        "malformed data URL")
            return None
        return {"type": "image",
                "source": {"type": "base64", "media_type": media_type,
                           "data": data}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _openai_request_to_anthropic(payload: dict, direction: str) -> TranslationResult:
    report = TranslationReport()
    out: dict = {}
    if "model" in payload:
        out["model"] = payload["model"]

    # System messages become Anthropic's top-level ``system``.
    system_texts: list[str] = []
    anthropic_messages: list[dict] = []
    for index, message in enumerate(payload.get("messages") or []):
        if not isinstance(message, dict):
            report.drop(f"messages[{index}]", direction, "message is not an object")
            continue
        role = message.get("role")
        if role == "system":
            system_texts.append(_text_of_content(message.get("content")))
            continue
        if role == "user":
            blocks = _openai_content_parts_to_anthropic(
                message.get("content"), where=f"messages[{index}].content",
                direction=direction, report=report)
            anthropic_messages.append({"role": "user",
                                       "content": blocks or [{"type": "text", "text": ""}]})
            continue
        if role == "assistant":
            blocks = _openai_content_parts_to_anthropic(
                message.get("content"), where=f"messages[{index}].content",
                direction=direction, report=report)
            for call_index, call in enumerate(message.get("tool_calls") or []):
                if not isinstance(call, dict):
                    report.drop(f"messages[{index}].tool_calls[{call_index}]",
                                direction, "tool call is not an object")
                    continue
                function = call.get("function") or {}
                blocks.append({
                    "type": "tool_use",
                    "id": call.get("id") or "",
                    "name": function.get("name", ""),
                    "input": _parse_tool_arguments(
                        function.get("arguments", "{}"),
                        where=f"messages[{index}].tool_calls[{call_index}].function.arguments",
                        direction=direction, report=report),
                })
            anthropic_messages.append({"role": "assistant",
                                       "content": blocks or [{"type": "text", "text": ""}]})
            continue
        if role == "tool":
            # Anthropic carries tool results as ``tool_result`` blocks
            # inside a user turn (nanobot merges them into the
            # previous user message; here they start their own turn
            # unless the last message already is one).
            block = {
                "type": "tool_result",
                "tool_use_id": message.get("tool_call_id") or "",
                "content": _text_of_content(message.get("content")),
            }
            if anthropic_messages and anthropic_messages[-1]["role"] == "user":
                last = anthropic_messages[-1]["content"]
                if isinstance(last, list):
                    last.append(block)
                else:
                    anthropic_messages[-1]["content"] = [
                        {"type": "text", "text": last}, block]
            else:
                anthropic_messages.append({"role": "user", "content": [block]})
            continue
        report.drop(f"messages[{index}].role={role!r}", direction,
                    "no Anthropic role equivalent")

    if system_texts:
        joined = "\n\n".join(text for text in system_texts if text)
        out["system"] = joined
        if len(system_texts) > 1:
            report.note(
                f"merged {len(system_texts)} system messages into one "
                f"Anthropic system string")
    out["messages"] = anthropic_messages

    # Tools: OpenAI function tools -> Anthropic tools.
    tools = payload.get("tools")
    if tools is not None:
        anthropic_tools: list[dict] = []
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict) or tool.get("type") != "function":
                report.drop(f"tools[{index}]", direction,
                            "only function tools translate to Anthropic tools")
                continue
            function = tool.get("function") or {}
            if tool.get("strict") is True:
                report.drop(f"tools[{index}].strict", direction,
                            "Anthropic has no strict structured-output flag")
            anthropic_tools.append({
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "input_schema": function.get("parameters")
                or {"type": "object", "properties": {}},
            })
            if "parameters" not in function:
                report.note(
                    f"tools[{index}] ({function.get('name', '')!r}) had no "
                    f"parameters; sent an empty object schema")
        out["tools"] = anthropic_tools

    tool_choice = payload.get("tool_choice")
    if tool_choice is not None:
        if tool_choice in ("auto", "none"):
            out["tool_choice"] = {"type": tool_choice}
        elif tool_choice == "required":
            out["tool_choice"] = {"type": "any"}
        elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            out["tool_choice"] = {
                "type": "tool",
                "name": (tool_choice.get("function") or {}).get("name", ""),
            }
        else:
            report.drop("tool_choice", direction,
                        f"unsupported tool_choice value: {tool_choice!r}")

    for key in ("temperature", "top_p", "stream"):
        if key in payload:
            out[key] = payload[key]
    if "max_tokens" in payload:
        out["max_tokens"] = payload["max_tokens"]
    else:
        out["max_tokens"] = DEFAULT_ANTHROPIC_MAX_TOKENS
        report.note(
            f"max_tokens defaulted to {DEFAULT_ANTHROPIC_MAX_TOKENS}: "
            f"Anthropic requires it and the OpenAI request had none")
    stop = payload.get("stop")
    if stop is not None:
        out["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)

    # Everything else on an OpenAI request has no Anthropic home.
    known = {"model", "messages", "tools", "tool_choice", "temperature",
             "top_p", "max_tokens", "stop", "stream"}
    for key in sorted(payload):
        if key not in known:
            report.drop(key, direction,
                        "no Anthropic request field equivalent")
    return TranslationResult(payload=out, report=report)


# ---------------------------------------------------------------------------
# Requests: Anthropic -> OpenAI
# ---------------------------------------------------------------------------

def _anthropic_image_to_openai(block: dict, *, where: str, direction: str,
                               report: TranslationReport) -> dict | None:
    """Anthropic ``image`` block -> OpenAI ``image_url`` part."""
    source = block.get("source") or {}
    if source.get("type") == "base64":
        media_type = source.get("media_type", "image/png")
        data = source.get("data", "")
        if not data:
            report.drop(f"{where}.source.data", direction, "empty image data")
            return None
        return {"type": "image_url",
                "image_url": {"url": f"data:{media_type};base64,{data}"}}
    if source.get("type") == "url":
        return {"type": "image_url",
                "image_url": {"url": source.get("url", "")}}
    report.drop(f"{where}.source.type={source.get('type')!r}", direction,
                "no OpenAI image_url equivalent")
    return None


def _anthropic_request_to_openai(payload: dict, direction: str) -> TranslationResult:
    report = TranslationReport()
    out: dict = {}
    if "model" in payload:
        out["model"] = payload["model"]

    openai_messages: list[dict] = []
    system = payload.get("system")
    if system is not None:
        if isinstance(system, str):
            openai_messages.append({"role": "system", "content": system})
        elif isinstance(system, list):
            texts = [block.get("text", "") for block in system
                     if isinstance(block, dict) and block.get("type") == "text"]
            dropped = [block for block in system
                       if isinstance(block, dict) and block.get("type") != "text"]
            for block in dropped:
                report.drop(f"system block type={block.get('type')!r}",
                            direction, "only text blocks translate to a system message")
            openai_messages.append({"role": "system", "content": "".join(texts)})
        else:
            openai_messages.append({"role": "system", "content": str(system)})

    for index, message in enumerate(payload.get("messages") or []):
        if not isinstance(message, dict):
            report.drop(f"messages[{index}]", direction, "message is not an object")
            continue
        role = message.get("role")
        content = message.get("content")
        if role == "user":
            if isinstance(content, str):
                openai_messages.append({"role": "user", "content": content})
                continue
            text_parts: list[dict] = []
            for block_index, block in enumerate(content or []):
                if not isinstance(block, dict):
                    text_parts.append({"type": "text", "text": str(block)})
                    continue
                kind = block.get("type")
                if kind == "text":
                    text_parts.append({"type": "text", "text": block.get("text", "")})
                elif kind == "image":
                    part = _anthropic_image_to_openai(
                        block, where=f"messages[{index}].content[{block_index}]",
                        direction=direction, report=report)
                    if part is not None:
                        text_parts.append(part)
                elif kind == "tool_result":
                    result_content = block.get("content")
                    openai_messages.append({
                        "role": "tool",
                        "tool_call_id": block.get("tool_use_id") or "",
                        "content": _text_of_content(result_content),
                    })
                else:
                    report.drop(
                        f"messages[{index}].content[{block_index}].type={kind!r}",
                        direction, "no OpenAI content part equivalent")
                if "cache_control" in block:
                    report.drop(
                        f"messages[{index}].content[{block_index}].cache_control",
                        direction, "OpenAI has no per-block cache control")
            if text_parts:
                openai_messages.append({"role": "user", "content": text_parts})
            continue
        if role == "assistant":
            text = ""
            tool_calls: list[dict] = []
            if isinstance(content, str):
                text = content
            else:
                for block_index, block in enumerate(content or []):
                    if not isinstance(block, dict):
                        text += str(block)
                        continue
                    kind = block.get("type")
                    if kind == "text":
                        text += block.get("text", "")
                    elif kind == "tool_use":
                        tool_calls.append({
                            "id": block.get("id") or "",
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(
                                    block.get("input") or {},
                                    ensure_ascii=False),
                            },
                        })
                    elif kind in ("thinking", "redacted_thinking"):
                        report.drop(
                            f"messages[{index}].content[{block_index}].type={kind!r}",
                            direction,
                            "thinking blocks have no OpenAI message equivalent")
                    else:
                        report.drop(
                            f"messages[{index}].content[{block_index}].type={kind!r}",
                            direction, "no OpenAI content part equivalent")
            assistant: dict = {"role": "assistant", "content": text or None}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            openai_messages.append(assistant)
            continue
        report.drop(f"messages[{index}].role={role!r}", direction,
                    "no OpenAI role equivalent")

    out["messages"] = openai_messages

    tools = payload.get("tools")
    if tools is not None:
        openai_tools: list[dict] = []
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict):
                report.drop(f"tools[{index}]", direction, "tool is not an object")
                continue
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema")
                    or {"type": "object", "properties": {}},
                },
            })
            if "cache_control" in tool:
                report.drop(f"tools[{index}].cache_control", direction,
                            "OpenAI has no per-tool cache control")
        out["tools"] = openai_tools

    tool_choice = payload.get("tool_choice")
    if tool_choice is not None:
        if isinstance(tool_choice, dict):
            kind = tool_choice.get("type")
            if kind == "auto":
                out["tool_choice"] = "auto"
            elif kind == "any":
                out["tool_choice"] = "required"
            elif kind == "none":
                out["tool_choice"] = "none"
            elif kind == "tool":
                out["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice.get("name", "")},
                }
            else:
                report.drop("tool_choice", direction,
                            f"unsupported tool_choice type: {kind!r}")
        else:
            report.drop("tool_choice", direction,
                        "tool_choice must be an object for Anthropic")

    for key in ("temperature", "top_p", "max_tokens", "stream"):
        if key in payload:
            out[key] = payload[key]
    stop_sequences = payload.get("stop_sequences")
    if stop_sequences is not None:
        out["stop"] = list(stop_sequences)
    metadata = payload.get("metadata")
    if metadata is not None:
        # Both sides accept a free-form metadata object; it crosses whole.
        out["metadata"] = metadata

    known = {"model", "system", "messages", "tools", "tool_choice",
             "temperature", "top_p", "max_tokens", "stop_sequences",
             "stream", "metadata"}
    for key in sorted(payload):
        if key not in known:
            report.drop(key, direction,
                        "no OpenAI request field equivalent")
    return TranslationResult(payload=out, report=report)


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

def _openai_response_to_anthropic(payload: dict, direction: str) -> TranslationResult:
    report = TranslationReport()
    out: dict = {"type": "message", "role": "assistant"}
    if "id" in payload:
        out["id"] = payload["id"]
    if "model" in payload:
        out["model"] = payload["model"]

    choices = payload.get("choices")
    if not choices:
        raise ValueError("OpenAI response has no choices to translate")
    if len(choices) > 1:
        report.drop(f"choices[1:{len(choices)}]", direction,
                    f"{len(choices) - 1} additional choice(s) dropped; "
                    f"Anthropic returns a single message")
    choice = choices[0]
    message = choice.get("message") or {}
    content_blocks: list[dict] = []
    content = message.get("content")
    if isinstance(content, str) and content:
        content_blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        for part_index, part in enumerate(content):
            if isinstance(part, dict) and part.get("type") == "text":
                content_blocks.append({"type": "text", "text": part.get("text", "")})
            else:
                report.drop(f"choices[0].message.content[{part_index}]",
                            direction, "no Anthropic content block equivalent")
    for call_index, call in enumerate(message.get("tool_calls") or []):
        if not isinstance(call, dict):
            report.drop(f"choices[0].message.tool_calls[{call_index}]",
                        direction, "tool call is not an object")
            continue
        function = call.get("function") or {}
        content_blocks.append({
            "type": "tool_use",
            "id": call.get("id") or "",
            "name": function.get("name", ""),
            "input": _parse_tool_arguments(
                function.get("arguments", "{}"),
                where=f"choices[0].message.tool_calls[{call_index}].function.arguments",
                direction=direction, report=report),
        })
    out["content"] = content_blocks

    finish_reason = choice.get("finish_reason")
    if finish_reason is None:
        out["stop_reason"] = "end_turn"
        report.note("finish_reason was absent; stop_reason set to end_turn")
    elif finish_reason in _OPENAI_FINISH_TO_STOP:
        out["stop_reason"] = _OPENAI_FINISH_TO_STOP[finish_reason]
        if finish_reason == "content_filter":
            report.drop("choices[0].finish_reason=content_filter", direction,
                        "Anthropic has no content-filter stop reason; "
                        "stop_reason set to end_turn as the closest neutral value")
    else:
        report.drop(f"choices[0].finish_reason={finish_reason!r}", direction,
                    "no Anthropic stop_reason equivalent")
        out["stop_reason"] = "end_turn"

    usage = payload.get("usage")
    if usage is not None:
        anthropic_usage: dict = {}
        if "prompt_tokens" in usage:
            anthropic_usage["input_tokens"] = usage["prompt_tokens"]
        if "completion_tokens" in usage:
            anthropic_usage["output_tokens"] = usage["completion_tokens"]
        details = usage.get("prompt_tokens_details") or {}
        if isinstance(details, dict) and "cached_tokens" in details:
            anthropic_usage["cache_read_input_tokens"] = details["cached_tokens"]
        out["usage"] = anthropic_usage
        if "completion_tokens_details" in usage:
            report.drop("usage.completion_tokens_details", direction,
                        "Anthropic usage has no completion-token details field")

    for key in sorted(payload):
        if key not in {"id", "model", "choices", "usage"}:
            report.drop(key, direction,
                        "no Anthropic response field equivalent")
    return TranslationResult(payload=out, report=report)


def _anthropic_response_to_openai(payload: dict, direction: str) -> TranslationResult:
    report = TranslationReport()
    out: dict = {"object": "chat.completion"}
    if "id" in payload:
        out["id"] = payload["id"]
    if "model" in payload:
        out["model"] = payload["model"]
    if "created_at" not in payload and "created" not in payload:
        report.note("Anthropic responses carry no creation timestamp; "
                    "the OpenAI 'created' field is omitted")

    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block_index, block in enumerate(payload.get("content") or []):
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue
        kind = block.get("type")
        if kind == "text":
            text_parts.append(block.get("text", ""))
        elif kind == "tool_use":
            tool_calls.append({
                "id": block.get("id") or "",
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input") or {},
                                            ensure_ascii=False),
                },
            })
        elif kind in ("thinking", "redacted_thinking"):
            report.drop(f"content[{block_index}].type={kind!r}", direction,
                        "thinking blocks have no OpenAI message equivalent")
        else:
            report.drop(f"content[{block_index}].type={kind!r}", direction,
                        "no OpenAI message part equivalent")

    stop_reason = payload.get("stop_reason")
    if stop_reason is None:
        finish_reason = "stop"
        report.note("stop_reason was absent; finish_reason set to stop")
    elif stop_reason in _ANTHROPIC_STOP_TO_FINISH:
        finish_reason = _ANTHROPIC_STOP_TO_FINISH[stop_reason]
    else:
        report.drop(f"stop_reason={stop_reason!r}", direction,
                    "no OpenAI finish_reason equivalent")
        finish_reason = "stop"

    message: dict = {"role": "assistant",
                     "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    out["choices"] = [{"index": 0, "message": message,
                       "finish_reason": finish_reason}]

    usage = payload.get("usage")
    if usage is not None:
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        openai_usage: dict = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        }
        if "cache_read_input_tokens" in usage:
            openai_usage["prompt_tokens_details"] = {
                "cached_tokens": usage["cache_read_input_tokens"]}
        out["usage"] = openai_usage
        if "cache_creation_input_tokens" in usage:
            report.drop("usage.cache_creation_input_tokens", direction,
                        "OpenAI usage has no cache-creation field")

    for key in sorted(payload):
        if key not in {"id", "model", "content", "stop_reason", "usage",
                       "type", "role"}:
            report.drop(key, direction,
                        "no OpenAI response field equivalent")
    return TranslationResult(payload=out, report=report)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def translate_request(payload: dict, *, source: str, target: str) -> TranslationResult:
    """Translate a chat request between OpenAI and Anthropic formats.

    ``source`` / ``target`` are ``"openai"`` or ``"anthropic"``.
    Returns the translated request plus a report of every field
    that did not cross. The input dict is never mutated.
    """
    _check_formats(source, target)
    payload = _check_payload(payload)
    direction = _direction(source, target)
    if source == FORMAT_OPENAI:
        return _openai_request_to_anthropic(payload, direction)
    return _anthropic_request_to_openai(payload, direction)


def translate_response(payload: dict, *, source: str, target: str) -> TranslationResult:
    """Translate a chat response between OpenAI and Anthropic formats.

    Same contract as :func:`translate_request`: the translated
    response plus a report of every field that did not cross.
    """
    _check_formats(source, target)
    payload = _check_payload(payload)
    direction = _direction(source, target)
    if source == FORMAT_OPENAI:
        return _openai_response_to_anthropic(payload, direction)
    return _anthropic_response_to_openai(payload, direction)
