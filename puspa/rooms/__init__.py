"""Shared rooms: invite and collaborate in group chat (B6).

A room is a small group (beta-lite: 2-5 people, no shared funds)
that shares one Puspa agent. The agent works for the group, not
for one person: it summarises the chat, splits tasks, records
decisions, and reminds owners about deadlines. Each member shares
only what the group allows.

Public surface:

- :class:`RoomStore` — rooms, roles (owner/member/guest),
  membership, leaving (private data leaves with the leaver).
- :class:`RoomInviteStore` — invites by link or one-time code,
  hash-only secrets, single use, revocation, idempotent accept.
- :class:`RoomMemoryStore` — group memory in three layers:
  personal (never in a room), room-visible, and private answers
  only their author may read.
- :class:`RoomTaskStore` — the shared task board (owner,
  deadline, status) plus the deterministic chat summariser and
  task splitter.
- :class:`DelegationService` — delegated tasks with approval
  (B9): hand a room task to the agent or a teammate, propose
  steps, and wait for approval on irreversible steps through
  the G4/G5 path; the board rendered for the PWA and the group
  chat.
- :class:`MoneyGate` — money actions need two people, or the
  room owner alone.
- :class:`SplitService` — shared splitting / patungan (B11):
  split a bill among room members in integer sen, track partial
  payments, remind debtors only, and prepare (never execute)
  settlement through the money gate.
- :class:`RoomChannelLinker` / :func:`handle_group_inbound` —
  routing a linked Telegram/WhatsApp group chat to its room, with
  the room as principal; only @Puspa messages are answered.
- :class:`ResearchService` — group research and shopping (B12):
  the room collects product links, the agent compares price and
  seller for all of them with evidence per item, and the group
  decision is recorded with who agreed.

Reuse (mechanisms taken, rewritten as Puspa code, never copied
verbatim):

- ``puspa/teams`` (G13) — teams as their own entity, hash-only
  invitation tokens, single-use idempotent acceptance, RLS
  policies. Rooms follow the same shapes with group-chat roles
  (owner/member/guest) and a 5-person beta cap.
- ``puspa/channels`` (G14) — the adapter -> MessageEvent -> core
  -> delivery-ledger pipeline. Group messages run the same
  pipeline through the ``group_handler`` hook in
  ``puspa/channels/inbound.py``.
- ``puspa/memory`` (G7 + G24) — the layering idea: personal
  memory stays personal; room memory is a separate store with
  its own visibility rule.

Honest limits (see ``docs/rooms-b6.md``): no live Telegram or
WhatsApp bot is verified anywhere in this repo — channel tests
use ``MockTransport`` (same as G14). WhatsApp groups are a
postponed surface (plan section 12.4): the routing is
transport-agnostic, but live WhatsApp-group delivery is out of
scope for this iteration.
"""

from __future__ import annotations

from .delegation import (
    DELEGATION_STATUSES,
    STEP_STATUSES,
    BoardRow,
    DelegatedStep,
    DelegatedTask,
    DelegationService,
    approve_step,
    board,
    classify_task_due,
    complete_delegated_task,
    delegate_to_agent,
    delegate_to_teammate,
    execute_step,
    is_board_command,
    propose_step,
    reject_step,
    reminder_lines,
    render_board,
    report_progress,
    start_work,
)
from .errors import (
    InviteError,
    LastOwnerError,
    MoneyApprovalError,
    NotRoomMemberError,
    NotRoomOwnerError,
    RoomError,
    RoomFullError,
    RoomNotFoundError,
    SplitError,
)
from .invites import (
    CODE_TTL_HOURS,
    INVITE_LINK_TTL_DAYS,
    JOIN_LINK_BASE,
    RoomInvite,
    RoomInviteStore,
    accept_invite,
    accept_invite_code,
    get_invite,
    issue_invite_code,
    issue_invite_link,
    list_invites,
    purge_expired,
    revoke_invite,
)
from .memory import (
    MAX_MEMORY_CONTENT_CHARS,
    MEMORY_KINDS,
    RoomMemory,
    RoomMemoryStore,
    delete_memory,
    list_room_memory,
    record_decision,
    record_memory,
    record_summary,
)
from .money import (
    REQUIRED_APPROVERS,
    MoneyGate,
    approve_money_action,
    is_money_approved,
    list_money_approvals,
)
from .research import (
    COMPARE_COMMANDS,
    MAX_LABEL_CHARS,
    GroupComparison,
    ResearchItem,
    ResearchLink,
    ResearchService,
    add_research_link,
    answer_compare_command,
    compare_group_links,
    detect_source,
    extract_product_links,
    is_compare_command,
    list_research_links,
    record_group_decision,
    remove_research_link,
    render_group_comparison,
)
from .rooms import (
    MAX_ROOM_SIZE,
    ROLE_GUEST,
    ROLE_MEMBER,
    ROLE_OWNER,
    Room,
    RoomMember,
    RoomStore,
    add_member,
    create_room,
    delete_room,
    get_room,
    is_member,
    is_owner,
    leave_room,
    list_members,
    list_rooms_for_user,
    member_count,
    remove_member,
    role_of,
    set_role,
)
from .routing import (
    RoomChannelLink,
    RoomChannelLinker,
    handle_group_inbound,
    is_addressed_to_puspa,
    link_channel,
    resolve_room,
    room_principal_id,
    unlink_channel,
)
from .schema import ROOMS_TABLES, create_schema, reset_tables, schema_path
from .splitting import (
    DEFAULT_REMINDER_SCHEDULE,
    DEFAULT_REMINDER_TIMEZONE,
    SEN_PER_RUPIAH,
    SPLIT_KINDS,
    SPLIT_STATUSES,
    Payment,
    PreparedSettlement,
    Share,
    Split,
    SplitService,
    build_reminder_digest,
    cents_to_rupiah,
    create_split,
    equal_shares,
    format_rupiah,
    get_split,
    list_debtors,
    list_settlements,
    list_splits,
    prepare_settlement,
    record_payment,
    register_reminder_job,
    reminder_job_prompt,
    render_split,
    run_reminder_sweep,
    rupiah_to_cents,
    settlement_action_id,
    split_reminder_lines,
    split_reminders,
    weighted_shares,
)
from .tasks import (
    MAX_TASK_TITLE_CHARS,
    RoomTask,
    RoomTaskStore,
    add_task,
    complete_task,
    due_soon,
    list_tasks,
    overdue,
    split_tasks,
    summarize_messages,
)

__all__ = [
    "CODE_TTL_HOURS",
    "COMPARE_COMMANDS",
    "DEFAULT_REMINDER_SCHEDULE",
    "DEFAULT_REMINDER_TIMEZONE",
    "DELEGATION_STATUSES",
    "INVITE_LINK_TTL_DAYS",
    "JOIN_LINK_BASE",
    "MAX_LABEL_CHARS",
    "MAX_MEMORY_CONTENT_CHARS",
    "MAX_ROOM_SIZE",
    "MAX_TASK_TITLE_CHARS",
    "MEMORY_KINDS",
    "REQUIRED_APPROVERS",
    "ROLE_GUEST",
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "ROOMS_TABLES",
    "SEN_PER_RUPIAH",
    "SPLIT_KINDS",
    "SPLIT_STATUSES",
    "STEP_STATUSES",
    "BoardRow",
    "DelegatedStep",
    "DelegatedTask",
    "DelegationService",
    "GroupComparison",
    "InviteError",
    "LastOwnerError",
    "MoneyApprovalError",
    "MoneyGate",
    "NotRoomMemberError",
    "NotRoomOwnerError",
    "Payment",
    "PreparedSettlement",
    "ResearchItem",
    "ResearchLink",
    "ResearchService",
    "Room",
    "RoomChannelLink",
    "RoomChannelLinker",
    "RoomError",
    "RoomFullError",
    "RoomInvite",
    "RoomInviteStore",
    "RoomMember",
    "RoomMemory",
    "RoomMemoryStore",
    "RoomNotFoundError",
    "RoomStore",
    "RoomTask",
    "RoomTaskStore",
    "Share",
    "Split",
    "SplitError",
    "SplitService",
    "accept_invite",
    "accept_invite_code",
    "add_member",
    "add_research_link",
    "add_task",
    "answer_compare_command",
    "approve_money_action",
    "approve_step",
    "board",
    "build_reminder_digest",
    "cents_to_rupiah",
    "classify_task_due",
    "compare_group_links",
    "complete_delegated_task",
    "complete_task",
    "create_room",
    "create_schema",
    "create_split",
    "delegate_to_agent",
    "delegate_to_teammate",
    "delete_memory",
    "delete_room",
    "detect_source",
    "due_soon",
    "equal_shares",
    "execute_step",
    "extract_product_links",
    "format_rupiah",
    "get_invite",
    "get_room",
    "get_split",
    "handle_group_inbound",
    "is_addressed_to_puspa",
    "is_board_command",
    "is_compare_command",
    "is_member",
    "is_money_approved",
    "is_owner",
    "issue_invite_code",
    "issue_invite_link",
    "leave_room",
    "link_channel",
    "list_debtors",
    "list_invites",
    "list_members",
    "list_money_approvals",
    "list_research_links",
    "list_room_memory",
    "list_rooms_for_user",
    "list_settlements",
    "list_splits",
    "list_tasks",
    "member_count",
    "overdue",
    "prepare_settlement",
    "propose_step",
    "purge_expired",
    "record_decision",
    "record_group_decision",
    "record_memory",
    "record_payment",
    "record_summary",
    "register_reminder_job",
    "reject_step",
    "reminder_job_prompt",
    "reminder_lines",
    "remove_member",
    "remove_research_link",
    "render_board",
    "render_group_comparison",
    "render_split",
    "report_progress",
    "reset_tables",
    "resolve_room",
    "revoke_invite",
    "role_of",
    "room_principal_id",
    "run_reminder_sweep",
    "rupiah_to_cents",
    "schema_path",
    "set_role",
    "settlement_action_id",
    "split_reminder_lines",
    "split_reminders",
    "split_tasks",
    "start_work",
    "summarize_messages",
    "unlink_channel",
    "weighted_shares",
]
