"""Full-loop test of ``assemble()``: the real AgentLoop, not a stub.

The factory test in ``test_factory_rails.py`` proves the right wiring
arguments reach ``AgentLoop.from_config`` by stubbing that call out.
This module proves the other half: ``assemble()`` builds a real
``AgentLoop`` that processes a turn end to end, with the Puspa
registry (the sandbox tool) and the Puspa hook attached.

The model itself is scripted, not live: a small provider subclass
answers with canned text, so the test needs no network and no model
key. Everything else is real: the loop, the session store, the tool
registry, the hooks, and the turn pipeline between them.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy

from nanobot.agent.loop import AgentLoop
from nanobot.config.schema import AgentDefaults, AgentsConfig, Config
from nanobot.providers.base import LLMProvider, LLMResponse

from puspa.chassis.factory import PuspaChassis, assemble


class _ScriptedProvider(LLMProvider):
    """A provider that answers with canned text and records every call.

    ``replies`` is answered in order, one per model call; once the list
    runs out the last reply repeats. ``calls`` records the messages and
    the tool list of every call, so tests can check what the real loop
    actually sent to the model.
    """

    def __init__(self, replies: list[str]):
        super().__init__(provider_name="offline-scripted-test")
        self._replies = replies
        self.calls: list[dict] = []

    def get_default_model(self):
        return "test-model"

    async def chat(self, messages, tools=None, **kwargs):
        index = min(len(self.calls), len(self._replies) - 1)
        self.calls.append({"messages": deepcopy(messages), "tools": deepcopy(tools or [])})
        return LLMResponse(content=self._replies[index], tool_calls=[])


def _tool_names(tools: list) -> list[str]:
    """Tool names from the tool list the loop hands to the model."""
    names = []
    for tool in tools or []:
        if isinstance(tool, dict):
            names.append(tool.get("name") or tool.get("function", {}).get("name"))
    return [name for name in names if name]


def _config(tmp_path) -> Config:
    """A nanobot config rooted at the test workspace."""
    return Config(agents=AgentsConfig(defaults=AgentDefaults(workspace=str(tmp_path))))


async def _run_turns(tmp_path, provider, texts: list[str], session_key: str, attributes=None):
    """Assemble the real loop and run turns through it, then close it.

    The turn and the close must share one event loop: the session
    state binds itself to the loop that first touches it and refuses
    a different one, so separate ``asyncio.run`` calls fail.
    ``attributes`` is the caller-owned request data the turn setup
    passes in (a verified permission-token payload lives here under
    ``"puspa_token"``); without it no tool is offered to the model.
    """
    loop = assemble(PuspaChassis(), _config(tmp_path), provider=provider)
    try:
        return loop, [
            await loop.process_direct(text, session_key=session_key, attributes=attributes)
            for text in texts
        ]
    finally:
        await loop.aclose()


def test_assembled_loop_runs_a_turn_end_to_end(tmp_path):
    provider = _ScriptedProvider(["hello back"])
    loop, (outbound,) = asyncio.run(_run_turns(tmp_path, provider, ["hello"], "cli:full-loop"))

    assert isinstance(loop, AgentLoop)
    assert loop.provider is provider
    assert outbound is not None
    assert outbound.content == "hello back"
    assert len(provider.calls) == 1
    user_texts = [
        str(message.get("content", ""))
        for message in provider.calls[0]["messages"]
        if message.get("role") == "user"
    ]
    assert any("hello" in text for text in user_texts)
    # The Puspa sandbox tool is registered in the real loop.
    assert "run_code" in loop.tool_names
    # Fail-closed without a permission token: the model is offered no
    # tools at all, even though the registry holds the sandbox tool.
    assert provider.calls[0]["tools"] == []


def test_assembled_loop_offers_the_puspa_tool_to_the_model(tmp_path):
    provider = _ScriptedProvider(["hello back"])
    asyncio.run(
        _run_turns(
            tmp_path,
            provider,
            ["hello"],
            "cli:full-loop",
            attributes={"puspa_token": {"tools": ["run_code"]}},
        )
    )

    assert len(provider.calls) == 1
    assert _tool_names(provider.calls[0]["tools"]) == ["run_code"]


def test_assembled_loop_keeps_session_history_across_turns(tmp_path):
    provider = _ScriptedProvider(["hello back", "still here"])
    _loop, (first, second) = asyncio.run(
        _run_turns(tmp_path, provider, ["hello", "hello again"], "cli:full-loop")
    )

    assert first is not None and first.content == "hello back"
    assert second is not None and second.content == "still here"
    assert len(provider.calls) == 2
    second_messages = provider.calls[1]["messages"]
    user_texts = [
        str(message.get("content", ""))
        for message in second_messages
        if message.get("role") == "user"
    ]
    assistant_texts = [
        str(message.get("content", ""))
        for message in second_messages
        if message.get("role") == "assistant"
    ]
    assert any("hello" in text for text in user_texts)
    assert any("hello back" in text for text in assistant_texts)
