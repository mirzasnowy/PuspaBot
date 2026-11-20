"""Health endpoint payload (E-14, plan section 7.4).

``GET /health`` is public (read-only, see
``puspa/hosting/service_allow.py``): it answers whether the server
is up and whether its embedding subsystem runs degraded. The
endpoint itself always answers 200 when the server is up; the
``status`` field carries the verdict (``ok`` / ``degraded``), so a
monitor alerts on the field instead of on the HTTP code.

The embedding block is the ``EmbeddingHealth`` snapshot built at
startup (``puspa.retrieval.embedding_health``). The operator
banner is the same snapshot rendered for the operator surface
(``GET /api/operator/status``): one canonical text, built here
from the snapshot dict so both surfaces always agree.
"""

from __future__ import annotations

from typing import Any

from puspa.retrieval.embedding_health import STATUS_DEGRADED, EmbeddingHealth


def build_health_payload(
    embedding: dict[str, Any] | None, *, serve_mode: str = "local"
) -> dict[str, Any]:
    """The ``GET /health`` payload for one embedding snapshot."""
    status = "degraded" if (embedding or {}).get("status") == STATUS_DEGRADED else "ok"
    return {
        "status": status,
        "serve_mode": serve_mode,
        "embedding": embedding,
    }


def embedding_banner(embedding: dict[str, Any] | None) -> dict[str, str] | None:
    """Operator banner for one embedding snapshot, or None if healthy."""
    if not embedding:
        return None
    health = EmbeddingHealth(
        embedder_id=str(embedding.get("embedder_id") or ""),
        dimension=int(embedding.get("dimension") or 0),
        status=str(embedding.get("status") or ""),
        reason=str(embedding.get("reason") or ""),
        fallback_count=int(embedding.get("fallback_count") or 0),
        environment=str(embedding.get("environment") or ""),
        alarm=bool(embedding.get("alarm") or False),
        alarm_reasons=list(embedding.get("alarm_reasons") or []),
        canary=embedding.get("canary"),
    )
    return health.to_banner()


__all__ = ["build_health_payload", "embedding_banner"]
