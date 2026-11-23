"""Handlers for the core tools served by ``puspa mcp``.

Every handler takes ``(arguments, context)`` and returns a plain
JSON-ready dict; bad arguments raise :class:`ValueError` (the
server turns that into an MCP tool error, never a crash).

The defaults call the real Puspa code: memory search runs the
hybrid recall over the user's active facts in Postgres, the
ledger reads the real append-only ledger, price comparison runs
the real wedge (same scope and terms-of-service gates as the
in-app check), bill status reads the Bills module's table, and
the scheduler reads and writes the real triggers table. No
handler hardcodes a database name: the DSN comes from the
context, which the CLI fills from ``PUSPA_TEST_DSN`` (or the
deployment's DSN) — never from a literal in this file.

Each handler is replaceable through :class:`HandlerContext`
(the seams the tests use): pass a fake callable and the server
calls it instead of the real code. The desktop-hands tools are
not handled here at all — they dispatch through
:func:`puspa.computer.mcp.handle_tool` with the hands object
from the context.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .tools import CORE_TOOL_DEFINITIONS

__all__ = [
    "HandlerContext",
    "build_handlers",
    "default_bill_status",
    "default_ledger_recent",
    "default_memory_search",
    "default_price_check",
    "default_schedule_add",
    "default_schedule_list",
]

#: Bill providers the Bills module knows (the manifest's choices).
BILL_PROVIDERS = ("PLN", "PDAM", "airtime", "paket data")

#: Bill statuses the Bills module knows (the manifest's choices).
BILL_STATUSES = ("not_yet_pay", "already_pay", "active")

#: Ledger look-back when no task or agent narrows the query.
LEDGER_DEFAULT_WINDOW_DAYS = 30


@dataclass
class HandlerContext:
    """What the handlers run against.

    ``user_id`` scopes every read and write to one user; ``dsn``
    is the Postgres DSN (``None`` means "let each module resolve
    it from the environment", exactly as the rest of Puspa does).
    The ``*_fn`` / ``price_readers`` / ``hands`` / ``fallback`` /
    ``web`` fields are seams: leave them unset for the real code,
    set them in tests to inject fakes.
    """

    user_id: str = "local-user"
    agent_id: str = "puspa"
    dsn: str | None = None
    memory_search_fn: Any = None
    ledger_fn: Any = None
    price_compare_fn: Any = None
    price_readers: dict | None = None
    bill_status_fn: Any = None
    schedule_list_fn: Any = None
    schedule_add_fn: Any = None
    hands: Any = None
    fallback: Any = None
    web: Any = None
    audit: Any = None


def _dsn(ctx: HandlerContext) -> str | None:
    """The DSN for this context (explicit, else the environment)."""
    return ctx.dsn or os.environ.get("PUSPA_TEST_DSN")


def _limit(arguments: dict, name: str, default: int, maximum: int) -> int:
    """One validated ``limit`` argument (a positive int, capped)."""
    limit = arguments.get(name, default)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"{name!r} must be a positive integer")
    return min(limit, maximum)


def _jsonable(value: Any) -> Any:
    """Make a value safe for JSON (datetimes and UUIDs to text)."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _require_string(arguments: dict, name: str) -> str:
    """One required non-empty string argument."""
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name!r} must be a non-empty string")
    return value.strip()


# ---------------------------------------------------------------------------
# Defaults (real Puspa code)
# ---------------------------------------------------------------------------


def default_memory_search(ctx: HandlerContext, arguments: dict) -> dict:
    """Search the user's active facts with hybrid recall."""
    from puspa.memory.profile import list_facts
    from puspa.memory.recall import hybrid_recall

    query = _require_string(arguments, "query")
    limit = _limit(arguments, "limit", 5, 14)
    facts = list_facts(ctx.user_id, status="active", dsn=_dsn(ctx))
    recall_facts = [
        {
            "id": fact["id"],
            "key": fact.get("key") or "",
            "content": str(fact.get("value") or ""),
            "status": fact.get("status"),
            "valid_from": fact.get("valid_from"),
            "created_at": fact.get("created_at"),
        }
        for fact in facts
    ]
    hits = hybrid_recall(query, recall_facts, limit=limit)
    return {
        "query": query,
        "facts": [
            {
                "key": hit["fact"].get("key"),
                "content": hit["fact"].get("content"),
                "score": round(hit["score"], 4),
                "cosine": round(hit["cosine"], 4),
                "keyword_hit": hit["keyword_hit"],
            }
            for hit in hits
        ],
    }


def default_ledger_recent(ctx: HandlerContext, arguments: dict) -> dict:
    """Recent ledger entries, narrowed by task or agent when given."""
    from puspa.ledger import Ledger

    limit = _limit(arguments, "limit", 20, 1000)
    task_id = arguments.get("task_id")
    agent_id = arguments.get("agent_id")
    if task_id is not None and not isinstance(task_id, str):
        raise ValueError("'task_id' must be a string")
    if agent_id is not None and not isinstance(agent_id, str):
        raise ValueError("'agent_id' must be a string")
    ledger = Ledger(dsn=_dsn(ctx))
    if task_id:
        events = ledger.events(task_id)[:limit]
    elif agent_id:
        events = ledger.events_for_agent(agent_id, limit=limit)
    else:
        since = datetime.now(UTC) - timedelta(days=LEDGER_DEFAULT_WINDOW_DAYS)
        events = ledger.recent_events(since, limit=limit)
    return {"events": [_jsonable(event) for event in events], "count": len(events)}


def _default_price_readers(ctx: HandlerContext, listing_urls: dict) -> dict:
    """One listing reader per marketplace (injected fakes win)."""
    if ctx.price_readers is not None:
        return dict(ctx.price_readers)
    from puspa.pricing.readers import create_reader

    readers = {}
    for marketplace in listing_urls:
        reader, _path = create_reader(marketplace)
        readers[marketplace] = reader
    return readers


def _default_reread(ctx: HandlerContext, listing_urls: dict):
    """Re-read one listing URL straight from its source (no cache).

    The verifier calls this with a bare URL, so the marketplace
    is recovered from the listing URLs the caller gave.
    """
    from puspa.pricing.readers import create_reader

    url_to_marketplace = {url: name for name, url in listing_urls.items()}

    def reread(url: str):
        marketplace = url_to_marketplace.get(url)
        readers = ctx.price_readers or {}
        if marketplace in readers:
            read = readers[marketplace].read(url)
        elif marketplace is not None:
            reader, _path = create_reader(marketplace)
            read = reader.read(url)
        else:
            return None
        if not read.success or read.price is None:
            return None
        return {"value": read.price, "name": read.name or ""}

    return reread


def default_price_check(ctx: HandlerContext, arguments: dict) -> dict:
    """Compare one product's price across the three marketplaces."""
    from puspa.pricing.compare import MARKETPLACE, compare_price

    model_number = _require_string(arguments, "model_number")
    category = _require_string(arguments, "category")
    brand = arguments.get("brand")
    if brand is not None and not isinstance(brand, str):
        raise ValueError("'brand' must be a string")
    mode = arguments.get("mode", "live")
    if mode not in ("live", "fixture"):
        raise ValueError("'mode' must be 'live' or 'fixture'")
    listing_urls = arguments.get("listing_urls") or {}
    if not isinstance(listing_urls, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in listing_urls.items()
    ):
        raise ValueError("'listing_urls' must be an object of marketplace name to URL")
    unknown = sorted(set(listing_urls) - set(MARKETPLACE))
    if unknown:
        raise ValueError(f"unknown marketplace(s): {', '.join(unknown)}")
    readers = _default_price_readers(ctx, listing_urls)
    return _jsonable(
        compare_price(
            user_id=ctx.user_id,
            agent_id=ctx.agent_id,
            model_number=model_number,
            brand=brand,
            category=category,
            listing_urls=dict(listing_urls),
            reader=readers,
            reread=_default_reread(ctx, listing_urls),
            mode=mode,
            dsn=_dsn(ctx),
        )
    )


def default_bill_status(ctx: HandlerContext, arguments: dict) -> dict:
    """Bills from the Bills module's table, filtered when asked."""
    from puspa.modules.manifest import list_modules
    from puspa.modules.tables import list_tables, read_table

    provider = arguments.get("provider")
    if provider is not None and provider not in BILL_PROVIDERS:
        raise ValueError(f"'provider' must be one of {', '.join(BILL_PROVIDERS)}")
    status = arguments.get("status")
    if status is not None and status not in BILL_STATUSES:
        raise ValueError(f"'status' must be one of {', '.join(BILL_STATUSES)}")
    dsn = _dsn(ctx)
    modules = list_modules(ctx.user_id, dsn=dsn)
    bills_module = next((m for m in modules if m.get("name") == "Tagihan"), None)
    if bills_module is None:
        return {"bills": [], "total_rupiah": 0, "module_found": False, "table_found": False}
    tables = list_tables(str(bills_module["id"]), user_id=ctx.user_id, dsn=dsn)
    bill_table = next((t for t in tables if t.get("name") == "bill"), None)
    if bill_table is None:
        return {"bills": [], "total_rupiah": 0, "module_found": True, "table_found": False}
    rows = read_table(str(bill_table["id"]), user_id=ctx.user_id, dsn=dsn)
    bills = [
        row
        for row in rows
        if (provider is None or row.get("provider") == provider)
        and (status is None or row.get("status") == status)
    ]
    total = sum(
        row.get("amount") or 0 for row in bills if isinstance(row.get("amount"), (int, float))
    )
    return {
        "bills": [_jsonable(row) for row in bills],
        "total_rupiah": total,
        "module_found": True,
        "table_found": True,
    }


def default_schedule_list(ctx: HandlerContext, arguments: dict) -> dict:
    """The user's triggers, soonest first (read-only, user-scoped)."""
    from puspa.db import connect as _db_connect

    limit = _limit(arguments, "limit", 20, 100)
    dsn = _dsn(ctx)
    if dsn is None:
        raise ValueError("no Postgres DSN available (set PUSPA_TEST_DSN)")
    with _db_connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, type, schedule, workflow, next_trigger_at, active"
            " FROM triggers WHERE user_id = %s"
            " ORDER BY next_trigger_at ASC LIMIT %s",
            (ctx.user_id, limit),
        )
        rows = cur.fetchall()
    triggers = [
        {
            "id": row[0],
            "event_type": row[1],
            "schedule": row[2],
            "workflow": row[3],
            "next_trigger_at": row[4].isoformat() if row[4] is not None else None,
            "active": row[5],
        }
        for row in rows
    ]
    return {"triggers": triggers, "count": len(triggers)}


def default_schedule_add(ctx: HandlerContext, arguments: dict) -> dict:
    """Add one trigger (validated event type, ISO next-run time)."""
    from puspa.scheduler.scheduler import TRIGGER_TYPES, create_trigger

    event_type = arguments.get("event_type")
    if event_type not in TRIGGER_TYPES:
        raise ValueError(f"'event_type' must be one of {', '.join(TRIGGER_TYPES)}")
    raw_next = _require_string(arguments, "next_trigger_at")
    try:
        next_trigger_at = datetime.fromisoformat(raw_next)
    except ValueError as exc:
        raise ValueError(f"'next_trigger_at' must be ISO-8601, got {raw_next!r}") from exc
    if next_trigger_at.tzinfo is None:
        next_trigger_at = next_trigger_at.replace(tzinfo=UTC)
    schedule = arguments.get("schedule", "")
    workflow = arguments.get("workflow", "")
    if not isinstance(schedule, str) or not isinstance(workflow, str):
        raise ValueError("'schedule' and 'workflow' must be strings")
    trigger = create_trigger(
        ctx.user_id,
        event_type,
        next_trigger_at,
        schedule=schedule,
        workflow=workflow,
        dsn=_dsn(ctx),
    )
    return {
        "id": trigger.id,
        "event_type": trigger.event_type,
        "schedule": trigger.schedule,
        "workflow": trigger.workflow,
        "next_trigger_at": trigger.next_trigger_at.isoformat(),
        "active": trigger.active,
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

#: Core tool name -> default handler (ctx, arguments) -> dict.
_DEFAULTS = {
    "memory_search": default_memory_search,
    "ledger_recent": default_ledger_recent,
    "price_check": default_price_check,
    "bill_status": default_bill_status,
    "schedule_list": default_schedule_list,
    "schedule_add": default_schedule_add,
}

#: Core tool name -> context seam attribute holding its override.
_SEAMS = {
    "memory_search": "memory_search_fn",
    "ledger_recent": "ledger_fn",
    "price_check": "price_compare_fn",
    "bill_status": "bill_status_fn",
    "schedule_list": "schedule_list_fn",
    "schedule_add": "schedule_add_fn",
}


def build_handlers(ctx: HandlerContext) -> dict:
    """All handlers for one server: core tools + desktop hands.

    A core tool uses its seam when the context sets one, else the
    real default. A computer tool dispatches through
    :func:`puspa.computer.mcp.handle_tool` with the context's
    hands (and fallback / web when set); calling one with no
    hands wired up raises a plain error instead of crashing.
    """
    from puspa.computer.mcp import TOOL_DEFINITIONS as computer_definitions
    from puspa.computer.mcp import handle_tool as handle_computer_tool

    handlers: dict = {}
    for definition in CORE_TOOL_DEFINITIONS:
        name = definition["name"]
        seam = getattr(ctx, _SEAMS[name], None)
        default = _DEFAULTS[name]

        def _make(seam_fn, default_fn):
            def _handler(arguments: dict) -> dict:
                if seam_fn is not None:
                    return seam_fn(ctx, arguments)
                return default_fn(ctx, arguments)

            return _handler

        handlers[name] = _make(seam, default)
    for definition in computer_definitions:
        name = definition["name"]

        def _make_computer(tool_name):
            def _handler(arguments: dict) -> dict:
                if ctx.hands is None:
                    raise ValueError(
                        f"{tool_name} needs desktop hands (no hands wired up; "
                        "run `puspa mcp` on a machine with a desktop, or pass --backend fake)"
                    )
                return handle_computer_tool(
                    tool_name,
                    arguments,
                    ctx.hands,
                    fallback=ctx.fallback,
                    web=ctx.web,
                    audit=ctx.audit,
                )

            return _handler

        handlers[name] = _make_computer(name)
    return handlers
