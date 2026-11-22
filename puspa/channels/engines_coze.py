"""Coze engine adapter (AST-05).

One external agent platform as PuspaBot's engine: a Coze bot
answers the channel through Coze's Chat API v3. A v3 chat is
asynchronous, so one :meth:`CozeEngine.send` runs the whole
create-and-poll flow the official SDK wraps as
``create_and_poll``:

1. ``POST {base_url}/v3/chat`` with ``bot_id``, ``user_id``
   and one user message in ``additional_messages`` - creates
   the chat (``stream: false``, ``auto_save_history: true``).
2. ``GET {base_url}/v3/chat/retrieve`` until the chat status
   is ``completed`` (bounded polls, 1s apart); ``failed`` or
   ``cancelled`` is an error, never an answer.
3. ``GET {base_url}/v3/chat/message/list`` - the reply is the
   last message with role ``assistant`` and type ``answer``.

Conversation context stays on Coze's side: the create response
returns a ``conversation_id``; the adapter remembers it per
Puspa conversation key (in memory - a restart starts a fresh
Coze conversation) and sends it on later turns. A ``history``
passed by the caller is therefore not re-sent: Coze already
holds the thread.

Raw HTTPS over the already-pinned ``httpx`` only; no Coze SDK,
no new dependency. The API token, bot id, and base URL are
constructor arguments, never read from the environment here,
never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from .engines import EngineError, ExternalEngine, get_engine_json, post_engine_json

__all__ = ["COZE_DEFAULT_BASE_URL", "CozeEngine"]

#: Coze international API base (Coze China uses api.coze.cn).
COZE_DEFAULT_BASE_URL = "https://api.coze.com"

#: Bounded polls while a chat runs: 30 polls, 1s apart.
MAX_CHAT_POLLS = 30
CHAT_POLL_INTERVAL_SECONDS = 1.0


class CozeEngine(ExternalEngine):
    """A Coze bot as the channel engine."""

    platform = "coze"

    def __init__(
        self,
        api_token: str,
        bot_id: str,
        *,
        base_url: str = COZE_DEFAULT_BASE_URL,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_polls: int = MAX_CHAT_POLLS,
    ) -> None:
        if not api_token:
            raise ValueError("CozeEngine requires an API token")
        if not bot_id:
            raise ValueError("CozeEngine requires a bot id")
        self._api_token = api_token
        self._bot_id = bot_id
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.Client(timeout=60.0)
        self._owns_client = http_client is None
        self._sleep = sleep
        self._max_polls = max_polls
        # Puspa conversation key -> Coze conversation id (in memory).
        self._conversations: dict[str, str] = {}

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_token}"}

    def _check_envelope(self, data: dict) -> dict:
        """Unwrap a Coze ``{"code", "msg", "data"}`` envelope.

        ``code`` 0 is success; anything else is an
        :class:`EngineError` carrying Coze's message.
        """
        if data.get("code", 0) != 0:
            raise EngineError(
                f"coze engine refused the request: {data.get('msg', 'unknown error')}"
            )
        inner = data.get("data")
        if inner is None:
            raise EngineError("coze engine returned a response without data")
        return inner

    def send(
        self,
        text: str,
        *,
        conversation_id: str,
        user_id: str,
        history: tuple[dict, ...] = (),
    ) -> str:
        """Send one message to the Coze bot; return its answer text.

        ``history`` is accepted for the shared interface but not
        sent: Coze keeps the thread server-side under the
        conversation id (``auto_save_history``).
        """
        payload: dict = {
            "bot_id": self._bot_id,
            "user_id": user_id,
            "stream": False,
            "auto_save_history": True,
            "additional_messages": [{"role": "user", "content": text, "content_type": "text"}],
        }
        known_conversation = self._conversations.get(conversation_id)
        if known_conversation:
            payload["conversation_id"] = known_conversation
        created = self._check_envelope(
            post_engine_json(
                self._client,
                f"{self._base_url}/v3/chat",
                platform=self.platform,
                headers=self._headers(),
                payload=payload,
                sleep=self._sleep,
            )
        )
        if not isinstance(created, dict):
            raise EngineError("coze engine returned a chat that is not an object")
        chat_id = created.get("id")
        coze_conversation = created.get("conversation_id")
        if not isinstance(chat_id, str) or not chat_id:
            raise EngineError("coze engine returned a chat without an id")
        if not isinstance(coze_conversation, str) or not coze_conversation:
            raise EngineError("coze engine returned a chat without a conversation id")
        self._conversations[conversation_id] = coze_conversation
        self._wait_completed(coze_conversation, chat_id)
        return self._read_answer(coze_conversation, chat_id)

    def _wait_completed(self, coze_conversation: str, chat_id: str) -> None:
        """Poll ``/v3/chat/retrieve`` until the chat completes."""
        params = {"conversation_id": coze_conversation, "chat_id": chat_id}
        for _ in range(self._max_polls):
            chat = self._check_envelope(
                get_engine_json(
                    self._client,
                    f"{self._base_url}/v3/chat/retrieve",
                    platform=self.platform,
                    headers=self._headers(),
                    params=params,
                    sleep=self._sleep,
                )
            )
            if not isinstance(chat, dict):
                raise EngineError("coze engine returned a chat that is not an object")
            status = chat.get("status")
            if status == "completed":
                return
            if status in ("failed", "cancelled"):
                raise EngineError(f"coze engine chat ended with status {status!r}")
            self._sleep(CHAT_POLL_INTERVAL_SECONDS)
        raise EngineError(f"coze engine chat did not complete after {self._max_polls} polls")

    def _read_answer(self, coze_conversation: str, chat_id: str) -> str:
        """Read the assistant answer out of ``/v3/chat/message/list``."""
        messages = self._check_envelope(
            get_engine_json(
                self._client,
                f"{self._base_url}/v3/chat/message/list",
                platform=self.platform,
                headers=self._headers(),
                params={"conversation_id": coze_conversation, "chat_id": chat_id},
                sleep=self._sleep,
            )
        )
        if not isinstance(messages, list):
            raise EngineError("coze engine returned a message list that is not a list")
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if message.get("role") == "assistant" and message.get("type") == "answer":
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
        raise EngineError("coze engine returned no assistant answer")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
