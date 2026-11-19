"""Co-drafting with context memory (B13).

The agent drafts messages, documents, or emails in the user's
voice and remembers past decisions and preferences. The voice
profile is built from the existing memory stores (G7/G24
``puspa.memory.facts``) — this module invents no second memory
system: voice rules, preferences, and decisions are profile
facts, and a voice correction is recorded as a fact, so it
persists and changes later drafts.

The send path is approval-gated by construction (G4): a draft
can only be sent through :meth:`DraftingService.send_draft`
with a recorded approval for that exact draft text, granted by
the draft owner and not yet expired or consumed. There is no
other send path in this package.
"""

from .drafting import (
    APPROVAL_TTL_SECONDS,
    DRAFT_KINDS,
    Draft,
    DraftApproval,
    DraftingService,
    MemoryDraftStore,
    PostgresDraftStore,
)
from .errors import (
    DraftNotApprovedError,
    DraftNotFoundError,
    DraftValidationError,
    NoDeliveryChannelError,
)
from .schema import DRAFTING_TABLES, create_schema, reset_tables, schema_path
from .voice import VoiceProfile, build_voice_profile

__all__ = [
    "APPROVAL_TTL_SECONDS",
    "DRAFTING_TABLES",
    "DRAFT_KINDS",
    "Draft",
    "DraftApproval",
    "DraftNotApprovedError",
    "DraftNotFoundError",
    "DraftValidationError",
    "DraftingService",
    "MemoryDraftStore",
    "NoDeliveryChannelError",
    "PostgresDraftStore",
    "VoiceProfile",
    "build_voice_profile",
    "create_schema",
    "reset_tables",
    "schema_path",
]
