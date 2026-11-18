"""Multi-tenant hosting auth, pairing, link, and relay.

Pattern source (DuskBot ``server/request-auth.ts`` +
``server/card-seal.ts`` + ``server/sessions.ts`` +
``apps/link-service/`` + ``apps/cloudflare/link-relay/``,
PolyForm Noncommercial — study only, patterns rewritten as Puspa
code, never copied). Google login (``puspa.auth``) stays in front;
this package is the server-to-server path behind it: loopback trust,
service-mode allowlist, device pairing, per-start card seals, link
codes, relay frames, and connection fallback.

Everything here is in-memory and transport-free by design: no
database, no network. The HTTP route, the Cloudflare tunnel client,
and the networked relay are follow-ups wired through the seams
documented in ``docs/hosting-auth.md``.
"""

from .errors import (
    AddressLockedError,
    AllRoutesFailedError,
    HostingError,
    IdentityKeyError,
    LinkError,
    PairingError,
    RateLimitError,
    RelayError,
    SealError,
)
from .fallback import (
    FALLBACK_ORDER,
    ConnectionResult,
    RouteAttempt,
    connect_with_fallback,
    pick_route,
)
from .gateway import (
    SERVICE_MODE,
    SINGLE_USER_MODE,
    RequestDecision,
    authorize_request,
)
from .identity import (
    IDENTITY_KEY_BACKUP_EXCLUDED,
    IDENTITY_KEY_FILENAME,
    IdentityKey,
    default_identity_key_path,
    load_or_create_identity_key,
)
from .link import (
    LINK_CODE_ALPHABET,
    LINK_CODE_LENGTH,
    LINK_CODE_TTL_SECONDS,
    LINK_CREATE_GLOBAL_PER_HOUR,
    LINK_CREATE_PER_ADDRESS_PER_HOUR,
    LinkService,
    generate_link_code,
)
from .loopback import (
    is_browser_request,
    is_loopback_host,
    is_loopback_origin,
    loopback_trusted,
)
from .pairing import (
    PAIRING_CODE_TTL_SECONDS,
    PAIRING_LOCKOUT_SECONDS,
    PAIRING_MAX_FAILURES,
    PAIRING_SESSION_TTL_SECONDS,
    PairingStore,
)
from .relay import MAX_FRAME_BYTES, RelayHub, open_frame, seal_frame
from .seal import (
    MAX_SEAL_PLAINTEXT_BYTES,
    SEAL_INFO,
    SEAL_VERSION,
    SealedCard,
    SealingKey,
    seal_card,
    unseal_card,
)
from .service_allow import DEFAULT_SERVICE_ALLOW, ServiceAllowlist

__all__ = [
    "DEFAULT_SERVICE_ALLOW",
    "FALLBACK_ORDER",
    "IDENTITY_KEY_BACKUP_EXCLUDED",
    "IDENTITY_KEY_FILENAME",
    "LINK_CODE_ALPHABET",
    "LINK_CODE_LENGTH",
    "LINK_CODE_TTL_SECONDS",
    "LINK_CREATE_GLOBAL_PER_HOUR",
    "LINK_CREATE_PER_ADDRESS_PER_HOUR",
    "MAX_FRAME_BYTES",
    "MAX_SEAL_PLAINTEXT_BYTES",
    "PAIRING_CODE_TTL_SECONDS",
    "PAIRING_LOCKOUT_SECONDS",
    "PAIRING_MAX_FAILURES",
    "PAIRING_SESSION_TTL_SECONDS",
    "SEAL_INFO",
    "SEAL_VERSION",
    "SERVICE_MODE",
    "SINGLE_USER_MODE",
    "AddressLockedError",
    "AllRoutesFailedError",
    "ConnectionResult",
    "HostingError",
    "IdentityKey",
    "IdentityKeyError",
    "LinkError",
    "LinkService",
    "PairingError",
    "PairingStore",
    "RateLimitError",
    "RelayError",
    "RelayHub",
    "RequestDecision",
    "RouteAttempt",
    "SealError",
    "SealedCard",
    "SealingKey",
    "ServiceAllowlist",
    "authorize_request",
    "connect_with_fallback",
    "default_identity_key_path",
    "generate_link_code",
    "is_browser_request",
    "is_loopback_host",
    "is_loopback_origin",
    "load_or_create_identity_key",
    "loopback_trusted",
    "open_frame",
    "pick_route",
    "seal_card",
    "seal_frame",
    "unseal_card",
]
