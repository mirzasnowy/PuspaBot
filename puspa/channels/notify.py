"""Content-free notifications (G14).

The DuskBot rule, rewritten for Puspa: a push/notification from a
channel carries ONLY the bot name — never message content. The
user opens the PWA to read what happened; the channel is the
doorbell, not the letter.

:func:`notification_text` returns exactly the bot name. The
gateway sends it through the adapter like any other send (so it
is recorded in the delivery ledger), but the text never contains
anything from the event that triggered it.
"""

from __future__ import annotations

__all__ = ["notification_text", "notify"]


def notification_text(bot_name: str = "Puspa") -> str:
    """The only text a channel notification ever carries: the bot name."""
    return bot_name


def notify(adapter, *, chat_id: str, bot_name: str = "Puspa", delivery=None) -> dict | None:
    """Send a content-free notification to a chat.

    Returns the adapter's response dict, or the ledger record when
    a :class:`DeliveryService` is passed as ``delivery``. The
    notification text is the bot name and nothing else.
    """
    text = notification_text(bot_name)
    if delivery is not None:
        return delivery.send(adapter, chat_id=chat_id, text=text)
    return adapter.send_text(chat_id, text)
