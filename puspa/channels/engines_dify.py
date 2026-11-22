"""Dify engine adapter (AST-05).

One external agent platform as PuspaBot's engine: a Dify
chatbot, chatflow, or agent app answers the channel through
Dify's Chat API (``POST {base_url}/v1/chat-messages``,
``response_mode: "blocking"``). The app's own API key is the
credential (each Dify app has its own key); the base URL
defaults to Dify Cloud (``https://api.dify.ai/v1``) and points
at a self-hosted Dify for a local install.

Conversation context stays on Dify's side: the first call
starts a conversation and the response returns a
``conversation_id``; the adapter remembers that id per Puspa
conversation key (in memory - a restart starts a fresh Dify
conversation) and sends it on later turns. A ``history`` passed
by the caller is therefore not re-sent: Dify already holds the
thread.

Raw HTTPS over the already-pinned ``httpx`` only; no Dify SDK,
no new dependency. The API key and base URL are constructor
arguments, never read from the environment here, never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from .engines import EngineError, ExternalEngine, post_engine_json

__all__ = ["DIFY_DEFAULT_BASE_URL", "DifyEngine"]

#: Dify Cloud API base (self-hosted Dify uses its own URL).
DIFY_DEFAULT_BASE_URL = "https://api.dify.ai/v1"


class DifyEngine(ExternalEngine):
    """A Dify app as the channel engine."""

    platform = "dify"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DIFY_DEFAULT_BASE_URL,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise ValueError("DifyEngine requires an API key")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=60.0)
        self._owns_client = http_client is None
        self._sleep = sleep
        # Puspa conversation key -> Dify conversation id (in memory).
        self._conversations: dict[str, str] = {}

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
        """Send one message to the Dify app; return its answer text.

        ``history`` is accepted for the shared interface but not
        sent: Dify keeps the thread server-side under the
        conversation id.
        """
        payload: dict = {
            "inputs": {},
            "query": text,
            "response_mode": "blocking",
            "conversation_id": self._conversations.get(conversation_id, ""),
            "user": user_id,
        }
        data = post_engine_json(
            self._client,
            f"{self._base_url}/chat-messages",
            platform=self.platform,
            headers=self._headers(),
            payload=payload,
            sleep=self._sleep,
        )
        answer = data.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise EngineError("dify engine returned a response without an answer")
        returned_conversation = data.get("conversation_id")
        if isinstance(returned_conversation, str) and returned_conversation:
            self._conversations[conversation_id] = returned_conversation
        return answer

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
