"""Channel errors (G14).

One error type per failure the gateway must distinguish: a refused
chat, a bot-loop attempt, a failed delivery, a second live writer
for the same profile, a bad pairing code, and a webhook that fails
verification. Callers catch the base ``ChannelError`` when the
distinction does not matter.
"""

from __future__ import annotations


class ChannelError(Exception):
    """Any channel failure."""


class UnauthorizedChatError(ChannelError):
    """The chat is neither allowlisted nor paired; the message is dropped."""


class BotLoopError(ChannelError):
    """The inbound message came from a bot (or from Puspa itself)."""


class DeliveryError(ChannelError):
    """Sending through a platform adapter failed after all retries."""


class SingleWriterError(ChannelError):
    """A second live GatewayRunner for the same profile was refused."""


class PairingError(ChannelError):
    """Pairing failed (bad/expired code, locked chat, bad input)."""


class WebhookVerificationError(ChannelError):
    """A webhook request failed verification (token or signature)."""
