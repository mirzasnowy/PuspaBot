"""Tool definitions served by Puspa's own MCP server (``puspa mcp``).

Six core tools expose Puspa's own capabilities — memory search,
the ledger, price comparison, bill status, and the scheduler —
plus the twenty-three desktop-hands tools from
:mod:`puspa.computer.mcp`, reused as-is (imported, never
duplicated). Every definition follows the shape the rest of
Puspa uses for MCP tools (``name`` / ``description`` /
``inputSchema``), so the whole set validates with
:func:`puspa.plugins.mcp.validate_mcp_definition`.

Each tool also carries a gateway *effect* (``"read"`` or
``"act"``) in :data:`TOOL_EFFECTS`: the server hands the effect
to the policy gateway with every call, so a policy can allow
reads while refusing acts (or refuse one tool by name) without
the handlers knowing about policy at all.
"""

from __future__ import annotations

import copy

from puspa.computer.mcp import TOOL_DEFINITIONS as COMPUTER_TOOL_DEFINITIONS

__all__ = [
    "CORE_TOOL_DEFINITIONS",
    "TOOL_EFFECTS",
    "all_tool_definitions",
    "core_tool_definitions",
    "mcp_server_definition",
]

#: Tools that only read state. Everything else is an act.
READ_TOOLS = frozenset(
    {
        "memory_search",
        "ledger_recent",
        "bill_status",
        "schedule_list",
        "computer_snapshot",
        "computer_verify",
        "computer_list_windows",
        "computer_wait_settle",
        "computer_visual_locate",
        "computer_web_open",
        "computer_audit",
    }
)

CORE_TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "memory_search",
        "description": (
            "Search Puspa's memory for facts about a topic. Returns the best-matching "
            "active facts (key, content, and how strongly each matched), best first. "
            "Read-only: never writes or changes memory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search memory for, e.g. 'user likes spicy food'.",
                },
                "limit": {
                    "type": "integer",
                    "default": 5,
                    "description": "How many facts to return at most (default 5).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "ledger_recent",
        "description": (
            "List recent entries from Puspa's append-only ledger (the record of what "
            "the agent did): newest first. Narrow it with a task id or an agent id, "
            "or leave both out for the most recent entries overall. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Only entries for this task.",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Only entries written by this agent.",
                },
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "How many entries to return at most (default 20).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "price_check",
        "description": (
            "Compare one product's price across Tokopedia, Shopee, and Blibli. Give "
            "the model number, the category, and a listing URL per marketplace. "
            "Returns the price found at each marketplace (or why one could not be "
            "checked) and which is cheapest. Live marketplace reads follow the "
            "same scope and terms-of-service gates as the in-app price check: a "
            "marketplace that forbids automated reading is reported as "
            "cannot-be-checked, never read anyway. Pass mode 'fixture' only for "
            "test data, never for real listings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "model_number": {
                    "type": "string",
                    "description": "The product's model number, e.g. 'SM-A556E'.",
                },
                "brand": {
                    "type": "string",
                    "description": "The product's brand, e.g. 'Samsung'.",
                },
                "category": {
                    "type": "string",
                    "description": "The product's category, e.g. 'smartphone'.",
                },
                "listing_urls": {
                    "type": "object",
                    "description": "Marketplace name to listing URL, e.g. {'Tokopedia': 'https://...'}",
                    "additionalProperties": {"type": "string"},
                },
                "mode": {
                    "type": "string",
                    "enum": ["live", "fixture"],
                    "default": "live",
                    "description": "Live reads (default) or fixture test data.",
                },
            },
            "required": ["model_number", "category"],
        },
    },
    {
        "name": "bill_status",
        "description": (
            "List the user's bills from the Bills module (PLN, PDAM, airtime, data "
            "package): provider, amount in Rupiah, due date, and status "
            "(not yet paid, already paid, or active). Narrow it by provider or "
            "status, or leave both out for every bill. Read-only: never pays "
            "anything. Empty when the Bills module has no bills yet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["PLN", "PDAM", "airtime", "paket data"],
                    "description": "Only bills from this provider.",
                },
                "status": {
                    "type": "string",
                    "enum": ["not_yet_pay", "already_pay", "active"],
                    "description": "Only bills with this status.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "schedule_list",
        "description": (
            "List the user's scheduled triggers (what runs, on which schedule, "
            "and when it next runs), soonest first. Read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "description": "How many triggers to return at most (default 20).",
                },
            },
            "required": [],
        },
    },
    {
        "name": "schedule_add",
        "description": (
            "Add one scheduled trigger: what kind of job it is (event_type), when "
            "it next runs (next_trigger_at, ISO-8601 date and time), and optionally "
            "a cron-style schedule string and a workflow name. The trigger is "
            "created active. This is an act: it changes what Puspa will run."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_type": {
                    "type": "string",
                    "enum": [
                        "schedule",
                        "due_date_bill",
                        "price_drop",
                        "manual",
                        "backup_daily",
                        "consolidation",
                        "trust_root_refresh",
                        "revocation_list_refresh",
                        "dream",
                        "sentinel_scan",
                        "ontology_review",
                        "amnesia_check",
                    ],
                    "description": "What kind of job this trigger runs.",
                },
                "next_trigger_at": {
                    "type": "string",
                    "description": "When it next runs, ISO-8601, e.g. '2026-11-23T03:00:00+07:00'.",
                },
                "schedule": {
                    "type": "string",
                    "description": "Cron-style schedule string (optional).",
                },
                "workflow": {
                    "type": "string",
                    "description": "Workflow name to run (optional).",
                },
            },
            "required": ["event_type", "next_trigger_at"],
        },
    },
]


def core_tool_definitions() -> list[dict]:
    """A fresh copy of the six core tool definitions."""
    return copy.deepcopy(CORE_TOOL_DEFINITIONS)


def all_tool_definitions() -> list[dict]:
    """Every tool the server exposes: core tools + desktop hands."""
    from puspa.computer.mcp import tool_definitions as computer_definitions

    return core_tool_definitions() + computer_definitions()


#: Tool name -> gateway effect ("read" or "act").
TOOL_EFFECTS: dict[str, str] = {
    tool["name"]: ("read" if tool["name"] in READ_TOOLS else "act")
    for tool in CORE_TOOL_DEFINITIONS + COMPUTER_TOOL_DEFINITIONS
}


def mcp_server_definition() -> dict:
    """The MCP server definition this server serves.

    Validated against
    :func:`puspa.plugins.mcp.validate_mcp_definition` in tests:
    id ``puspa``, stdio transport, command ``puspa mcp``.
    """
    return {
        "id": "puspa",
        "name": "Puspa",
        "version": "0.1.0",
        "description": (
            "Puspa's own capabilities as MCP tools: search memory, read the ledger, "
            "compare prices across marketplaces, check bill status, manage scheduled "
            "triggers, and drive the local desktop (snapshot, click, verify, type, "
            "windows, clipboard, and the emergency stop) — every call policed by "
            "the Puspa policy gateway."
        ),
        "server": {"command": "puspa", "args": ["mcp"], "transport": "stdio", "env": {}},
        "tools": all_tool_definitions(),
    }
