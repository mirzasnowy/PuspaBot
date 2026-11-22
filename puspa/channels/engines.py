"""External agent engines for channels (AST-05).

PuspaBot's own engine answers a channel by default. This module
lets the user plug an external agent platform in as the engine
behind a channel instead of rebuilding their agents there - the
idea AstrBot bridges to (Dify, Coze, Alibaba Cloud Bailian):
one call sends the user's message plus conversation context and
gets the engine's reply back.

The shape:

- :class:`ExternalEngine` - one adapter per platform, one call:
  ``send(text, *, conversation_id, user_id, history=()) -> str``.
  ``conversation_id`` is the Puspa session key
  (``platform:chat_id``); ``history`` is the caller's recent
  turns as ``{"role", "content"}`` dicts (empty when the caller
  has none - the platforms keep their own server-side history,
  so history is only sent where the platform takes it).
- :class:`ExternalEngineAgent` - the same :class:`ChannelAgent`
  seam ``TaskRunnerAgent`` implements, so a
  :class:`GatewayRunner` answers through the external engine
  with no pipeline change.
- :class:`EngineError` - the one failure type: the engine is
  unreachable, the key is missing or rejected, or the response
  carries no usable answer. The adapter raises it; the agent
  wrapper turns it into the same honest "could not answer"
  reply the built-in agent returns on failure.

Rules copied from the channel adapters (``docs/adding-a-platform.md``):

- Raw HTTPS over the already-pinned ``httpx`` only; no new
  dependency and no platform SDK (the Dify/Coze/DashScope SDKs
  are not needed - each platform API is plain HTTPS).
- Every credential (API key, bot id, app id) is a constructor
  argument, never read from the environment inside an adapter,
  never hardcoded, never logged.
- The HTTP client and the sleep are injectable, so tests run
  every adapter against an ``httpx.MockTransport`` fake server
  and never touch the real platform.
- Bounded retries (3 attempts, 1s/2s backoff, same as
  Telegram): transport failures and 5xx responses are retried;
  4xx responses are not (a rejected key will not become valid
  by retrying).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

import httpx

from .errors import ChannelError
from .events import MessageEvent

__all__ = [
    "ENGINE_NAMES",
    "EngineError",
    "ExternalEngine",
    "ExternalEngineAgent",
    "get_engine_json",
    "post_engine_json",
]

#: The engines a channel can answer through. ``builtin`` is
#: PuspaBot's own engine (the default); the rest are external
#: agent platforms, configured by the user's own API key.
ENGINE_NAMES = ("builtin", "dify", "coze", "bailian")

#: Bounded retries for engine calls: 3 attempts, 1s / 2s backoff.
MAX_ENGINE_ATTEMPTS = 3
ENGINE_BACKOFF_SECONDS = (1.0, 2.0)


class EngineError(ChannelError):
    """The chosen engine could not answer (unreachable, bad key, bad reply)."""


class _RetryableEngineError(EngineError):
    """A 5xx engine failure: worth one more attempt."""


def _decode_engine_response(resp: httpx.Response, *, platform: str) -> dict:
    """Turn one engine HTTP response into a JSON dict or raise EngineError."""
    if resp.status_code in (401, 403):
        raise EngineError(
            f"{platform} engine rejected the API key "
            f"(HTTP {resp.status_code}): check the key and try again"
        )
    if 400 <= resp.status_code < 500:
        raise EngineError(
            f"{platform} engine refused the request (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    if resp.status_code >= 500:
        raise _RetryableEngineError(f"{platform} engine failed (HTTP {resp.status_code})")
    try:
        data = resp.json()
    except ValueError as exc:
        raise EngineError(f"{platform} engine returned a response that is not JSON") from exc
    if not isinstance(data, dict):
        raise EngineError(f"{platform} engine returned a response that is not a JSON object")
    return data


def post_engine_json(
    client: httpx.Client,
    url: str,
    *,
    platform: str,
    headers: dict | None = None,
    payload: dict | None = None,
    params: dict | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """POST one JSON call to an external engine, with bounded retry.

    Returns the decoded JSON body (a dict). Raises
    :class:`EngineError` when the key is rejected (HTTP 401/403),
    when the request is refused (other 4xx), when the engine stays
    unreachable after all attempts, or when the response is not a
    JSON object. The message never contains a credential.
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_ENGINE_ATTEMPTS + 1):
        try:
            resp = client.post(url, headers=headers, json=payload, params=params)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < MAX_ENGINE_ATTEMPTS:
                sleep(ENGINE_BACKOFF_SECONDS[attempt - 1])
                continue
            raise EngineError(
                f"{platform} engine is unreachable after {MAX_ENGINE_ATTEMPTS} attempts: {exc}"
            ) from exc
        try:
            return _decode_engine_response(resp, platform=platform)
        except _RetryableEngineError as exc:
            last_error = exc
            if attempt < MAX_ENGINE_ATTEMPTS:
                sleep(ENGINE_BACKOFF_SECONDS[attempt - 1])
                continue
            raise EngineError(
                f"{platform} engine is unreachable after {MAX_ENGINE_ATTEMPTS} attempts: {exc}"
            ) from exc
    raise EngineError(f"{platform} engine is unreachable: {last_error}")


def get_engine_json(
    client: httpx.Client,
    url: str,
    *,
    platform: str,
    headers: dict | None = None,
    params: dict | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """GET one JSON call from an external engine, with bounded retry.

    Same failure contract as :func:`post_engine_json`.
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_ENGINE_ATTEMPTS + 1):
        try:
            resp = client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < MAX_ENGINE_ATTEMPTS:
                sleep(ENGINE_BACKOFF_SECONDS[attempt - 1])
                continue
            raise EngineError(
                f"{platform} engine is unreachable after {MAX_ENGINE_ATTEMPTS} attempts: {exc}"
            ) from exc
        try:
            return _decode_engine_response(resp, platform=platform)
        except _RetryableEngineError as exc:
            last_error = exc
            if attempt < MAX_ENGINE_ATTEMPTS:
                sleep(ENGINE_BACKOFF_SECONDS[attempt - 1])
                continue
            raise EngineError(
                f"{platform} engine is unreachable after {MAX_ENGINE_ATTEMPTS} attempts: {exc}"
            ) from exc
    raise EngineError(f"{platform} engine is unreachable: {last_error}")


class ExternalEngine(ABC):
    """One external agent platform as PuspaBot's engine.

    ``platform`` is the engine name (``dify`` / ``coze`` /
    ``bailian``). Subclasses take their credentials as
    constructor arguments and an injectable ``httpx.Client``.
    An engine that owns its HTTP client also defines
    ``close()`` to release it; one built on a client handed
    in by the caller does not close that client.
    """

    platform: str = ""

    @abstractmethod
    def send(
        self,
        text: str,
        *,
        conversation_id: str,
        user_id: str,
        history: tuple[dict, ...] = (),
    ) -> str:
        """Send one user message and return the engine's reply text.

        ``conversation_id`` identifies the conversation (the
        Puspa session key); ``history`` is the caller's recent
        turns, newest last. Raises :class:`EngineError` when the
        engine is unreachable, the key is rejected, or the
        response carries no usable answer.
        """


class ExternalEngineAgent:
    """Answer channel messages through an external engine.

    The same :class:`ChannelAgent` seam ``TaskRunnerAgent``
    implements: ``answer(event, user_id=...) -> str``. The
    event's session key is the engine conversation id, so the
    platform keeps the thread across turns. An
    :class:`EngineError` becomes a short honest failure notice
    naming the engine - never a silent drop, never a fabricated
    answer - exactly like the built-in agent's failure notice.
    """

    def __init__(self, engine: ExternalEngine) -> None:
        self.engine = engine

    @property
    def platform(self) -> str:
        return self.engine.platform

    def answer(self, event: MessageEvent, *, user_id: str | None) -> str:
        try:
            reply = self.engine.send(
                event.text,
                conversation_id=event.session_key,
                user_id=user_id or event.sender_id,
            )
        except EngineError as exc:
            return (
                f"Sorry, the {self.engine.platform} engine could not "
                f"answer right now ({exc}). Please try again in a moment."
            )
        if not reply.strip():
            return "Sorry, the engine answered with an empty reply. Please try again in a moment."
        return reply

    def close(self) -> None:
        """Release the HTTP client the engine owns (if it owns one)."""
        close = getattr(self.engine, "close", None)
        if callable(close):
            close()
