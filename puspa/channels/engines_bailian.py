"""Bailian engine adapter (AST-05).

One external agent platform as PuspaBot's engine: an Alibaba
Cloud Bailian (Model Studio) application answers the channel
through the DashScope application API
(``POST {base_url}/api/v1/apps/{app_id}/completion``). The
DashScope API key is the credential and the application id
identifies the app (an agent, a workflow, or an orchestration).

Conversation context: Bailian keeps a thread server-side under
a ``session_id`` (one hour of inactivity expires it). The
adapter uses the Puspa conversation key itself as the
``session_id``, so no mapping is kept - the thread resumes
across turns as long as the platform still holds it. When the
caller passes explicit ``history``, the call instead sends
``messages`` (the platform's own rule: ``messages`` wins over
``prompt`` plus ``session_id``), so the history the caller
holds is what the app sees.

Raw HTTPS over the already-pinned ``httpx`` only; no DashScope
SDK, no new dependency. The API key, app id, and base URL are
constructor arguments, never read from the environment here,
never logged. The base URL defaults to the international
endpoint (Singapore region); the China endpoint is
``https://dashscope.aliyuncs.com``.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from .engines import EngineError, ExternalEngine, post_engine_json

__all__ = ["BAILIAN_DEFAULT_BASE_URL", "BailianEngine"]

#: DashScope international API base (Singapore region).
BAILIAN_DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com"


class BailianEngine(ExternalEngine):
    """A Bailian application as the channel engine."""

    platform = "bailian"

    def __init__(
        self,
        api_key: str,
        app_id: str,
        *,
        base_url: str = BAILIAN_DEFAULT_BASE_URL,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("BailianEngine requires an API key")
        if not app_id:
            raise ValueError("BailianEngine requires an app id")
        self._api_key = api_key
        self._app_id = app_id
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=60.0)
        self._owns_client = http_client is None
        self._sleep = sleep

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}"}

    def send(
        self,
        text: str,
        *,
        conversation_id: str,
        user_id: str,
        history: tuple[dict, ...] = (),
    ) -> str:
        """Send one message to the Bailian app; return its answer text.

        With ``history`` given, the call sends ``messages``
        (history plus this turn); otherwise it sends ``prompt``
        with the Puspa conversation key as the platform
        ``session_id``.
        """
        if history:
            messages = [
                {"role": turn.get("role", "user"), "content": turn.get("content", "")}
                for turn in history
            ]
            messages.append({"role": "user", "content": text})
            engine_input: dict = {"messages": messages}
        else:
            engine_input = {"prompt": text, "session_id": conversation_id}
        data = post_engine_json(
            self._client,
            f"{self._base_url}/api/v1/apps/{self._app_id}/completion",
            platform=self.platform,
            headers=self._headers(),
            payload={"input": engine_input},
            sleep=self._sleep,
        )
        output = data.get("output")
        if not isinstance(output, dict):
            raise EngineError("bailian engine returned a response without output")
        answer = output.get("text")
        if not isinstance(answer, str) or not answer.strip():
            raise EngineError("bailian engine returned a response without an answer")
        return answer

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
