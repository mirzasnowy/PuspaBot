"""GatewayRunner: the facade over the channel pipeline (G14).

One runner per profile, mirroring the hermes-agent shape
(``gateway/run.py``: one runner + one module per topic — inbound,
delivery, notification, heartbeat — as separate modules, not one
giant file):

- owns the adapters (Telegram first, WhatsApp second),
- owns the pairing store, the allowlist, the delivery ledger, and
  the agent seam,
- runs the inbound pipeline for every event, whichever adapter
  produced it,
- polls Telegram (``getUpdates``) with the restored offset and
  stores the next offset in the heartbeat state,
- answers webhook payloads (Telegram ``Update``, WhatsApp webhook
  body) through the same pipeline.

**Single writer (G26):** one live runner per profile, enforced
process-wide. ``GatewayRunner.open()`` refuses a second live
runner for the same profile with :class:`SingleWriterError`;
every other surface reaches channel state through the runner
(or a read-only ledger), never by opening the state itself.
``close()`` releases the claim.

The runner never touches the network at construction: adapters
are built lazily from the config when first needed, so importing
and opening a runner in tests costs nothing.
"""

from __future__ import annotations

import threading

from puspa.proactivity.budget import InterruptionBudget, initiative_from

from .agent import ChannelAgent, TaskRunnerAgent
from .authz import Allowlist, ChannelPairing
from .base import ChannelAdapter
from .config import ChannelConfig, ChannelConfigError, load_config
from .delivery import DeliveryService
from .errors import SingleWriterError
from .events import MessageEvent
from .heartbeat import HeartbeatStore
from .inbound import InboundOutcome, handle_inbound
from .ledger import InMemoryDeliveryLedger
from .telegram import TelegramAdapter
from .whatsapp import WhatsAppAdapter

__all__ = ["GatewayRunner"]

#: Live runners by profile (process-wide registry, G26 pattern).
_ACTIVE: dict[str, GatewayRunner] = {}
_LOCK = threading.Lock()


class GatewayRunner:
    """The single writer for one profile's channels."""

    def __init__(
        self,
        *,
        profile: str = "default",
        config: ChannelConfig | None = None,
        agent: ChannelAgent | None = None,
        pairing: ChannelPairing | None = None,
        ledger=None,
        heartbeat_store: HeartbeatStore | None = None,
        http_client=None,
        budget: InterruptionBudget | None = None,
    ) -> None:
        self.profile = profile
        self.config = config or load_config()
        self.agent = agent or TaskRunnerAgent()
        self.pairing = pairing or ChannelPairing()
        self.allowlist: Allowlist = self.config.allowlist
        self.ledger = ledger or InMemoryDeliveryLedger()
        self.delivery = DeliveryService(self.ledger)
        # The interruption budget (K10) for this profile's
        # proactive pings. One runner owns one budget, so the
        # day's spend is shared by every notification the
        # runner sends. In-memory: a restart resets the day
        # (see puspa.proactivity.budget).
        self.budget = budget if budget is not None else InterruptionBudget()
        self.heartbeat_store = heartbeat_store or HeartbeatStore(
            profile=profile, state_dir=self.config.state_dir
        )
        self._http_client = http_client
        self._adapters: dict[str, ChannelAdapter] = {}
        self._closed = False
        # Restored polling offset (None = poll from the beginning).
        restored = self.heartbeat_store.load()
        self._telegram_offset: int | None = restored.last_offset

    # -- opening / single writer ------------------------------------------

    @classmethod
    def open(cls, profile: str = "default", **kwargs) -> GatewayRunner:
        """Open the runner for a profile.

        Raises :class:`SingleWriterError` when a live runner for
        the same profile already exists in this process.
        """
        with _LOCK:
            active = _ACTIVE.get(profile)
            if active is not None and not active._closed:
                raise SingleWriterError(
                    f"profile {profile!r} already has a live channel gateway; close it first"
                )
            runner = cls(profile=profile, **kwargs)
            _ACTIVE[profile] = runner
            return runner

    def close(self) -> None:
        """Close the runner and release the single-writer claim."""
        with _LOCK:
            _ACTIVE.pop(self.profile, None)
        self._closed = True
        for adapter in self._adapters.values():
            close = getattr(adapter, "close", None)
            if callable(close):
                close()

    def __enter__(self) -> GatewayRunner:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- adapters ---------------------------------------------------------

    def telegram_adapter(self) -> TelegramAdapter:
        """Build (once) the Telegram adapter from config."""
        adapter = self._adapters.get("telegram")
        if adapter is None:
            token = self.config.require_telegram()
            adapter = TelegramAdapter(
                token,
                http_client=self._http_client,
                webhook_secret=self.config.telegram_webhook_secret,
            )
            self._adapters["telegram"] = adapter
        assert isinstance(adapter, TelegramAdapter)
        return adapter

    def whatsapp_adapter(self) -> WhatsAppAdapter:
        """Build (once) the WhatsApp adapter from config."""
        adapter = self._adapters.get("whatsapp")
        if adapter is None:
            token, phone_number_id = self.config.require_whatsapp()
            adapter = WhatsAppAdapter(
                token,
                phone_number_id,
                verify_token=self.config.whatsapp_verify_token,
                app_secret=self.config.whatsapp_app_secret,
                http_client=self._http_client,
            )
            self._adapters["whatsapp"] = adapter
        assert isinstance(adapter, WhatsAppAdapter)
        return adapter

    def adapter_for(self, platform: str) -> ChannelAdapter:
        """The adapter for a platform name (``telegram`` / ``whatsapp``)."""
        if platform == "telegram":
            return self.telegram_adapter()
        if platform == "whatsapp":
            return self.whatsapp_adapter()
        raise ChannelConfigError(f"unknown channel platform: {platform!r}")

    # -- inbound ----------------------------------------------------------

    def handle_event(self, event: MessageEvent) -> InboundOutcome:
        """Run one normalised event through the shared pipeline."""
        adapter = self.adapter_for(event.platform)
        return handle_inbound(
            event,
            adapter=adapter,
            agent=self.agent,
            pairing=self.pairing,
            allowlist=self.allowlist,
            ledger=self.ledger,
            delivery=self.delivery,
        )

    def handle_webhook_payload(self, platform: str, payload: dict) -> list[InboundOutcome]:
        """Parse one webhook body and run each event through the pipeline."""
        adapter = self.adapter_for(platform)
        return [self.handle_event(event) for event in adapter.parse_update(payload)]

    # -- polling (Telegram) -------------------------------------------------

    @property
    def telegram_offset(self) -> int | None:
        """The next ``getUpdates`` offset (restored at open)."""
        return self._telegram_offset

    def poll_telegram_once(
        self, *, timeout: int = 30, drop_backlog: bool = False
    ) -> list[InboundOutcome]:
        """One Telegram long-poll batch, processed through the pipeline.

        Advances and persists the offset after the batch. With
        ``drop_backlog=True`` (first start after a long downtime),
        the offset jumps past the whole batch without answering it,
        so the gateway does not flood the user with stale replies.
        A heartbeat is recorded after every batch.
        """
        adapter = self.telegram_adapter()
        events = adapter.poll_once(offset=self._telegram_offset, timeout=timeout)
        update_ids = getattr(adapter, "_last_update_ids", [])
        next_offset = TelegramAdapter.next_offset(update_ids, self._telegram_offset)
        if drop_backlog:
            self._telegram_offset = next_offset
            self.heartbeat_store.beat(last_offset=next_offset)
            return []
        outcomes = [self.handle_event(event) for event in events]
        self._telegram_offset = next_offset
        self.heartbeat_store.beat(last_offset=next_offset)
        return outcomes

    def beat(self):
        """Record a heartbeat now (keeps the current offset)."""
        return self.heartbeat_store.beat(last_offset=self._telegram_offset)

    # -- notifications ------------------------------------------------------

    def notify(
        self,
        platform: str,
        chat_id: str,
        *,
        initiative=None,
        budget: InterruptionBudget | None = None,
    ) -> dict | None:
        """Send a content-free notification (bot name only) to a chat.

        See :mod:`puspa.channels.notify`: the notification carries
        no message content, only the bot name.

        With ``initiative`` given (an :class:`Initiative` or a
        ``{"kind", "value"}`` dict), the ping pays from the
        interruption budget (K10) first — ``budget`` when given,
        else this runner's own budget. A denied initiative sends
        nothing and returns None; the denial is recorded in the
        budget's spend log. Without ``initiative`` the send is
        ungated: this path is for proactive pings only, and
        replies to the user never go through :meth:`notify`.
        """
        if initiative is not None:
            decision = (budget or self.budget).try_spend(initiative_from(initiative))
            if not decision.allowed:
                return None
        from .notify import notify

        adapter = self.adapter_for(platform)
        return notify(
            adapter,
            chat_id=chat_id,
            bot_name=self.config.bot_name,
            delivery=self.delivery,
        )
