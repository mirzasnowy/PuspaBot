"""Shared rooms: the room store layer.

A room is a small group (beta-lite: 2-5 people, no shared funds)
that shares one Puspa agent. The agent works for the group, not
for one person: it summarises the chat, splits tasks, records
decisions, and reminds owners about deadlines. Each member shares
only what the group allows.

This package is the data layer only: rooms, the shared task board,
and group memory. The sharing layer (invites, delegation, channel
routing, the job queue, money splits, group research, and the rooms
HTTP APIs) lives in the hosted service, which builds on this store.

Public surface:

- :class:`RoomStore` — rooms, roles (owner/member/guest),
  membership, leaving (private data leaves with the leaver).
- :class:`RoomMemoryStore` — group memory in three layers:
  personal (never in a room), room-visible, and private answers
  only their author may read.
- :class:`RoomTaskStore` — the shared task board (owner,
  deadline, status) plus the deterministic chat summariser and
  task splitter.
- :mod:`puspa.rooms.names` — group chat names (V2-27): the one
  shared resolver for per-user display names and per-bot names,
  so member lists, message authors, memory authors, and
  decisions never show a raw UUID or a hardcoded bot name.
"""

from __future__ import annotations

from .errors import (
    GuestReadOnlyError,
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
from .names import (
    DEFAULT_BOT_NAME,
    FALLBACK_MEMBER_NAME,
    MAX_DISPLAY_NAME_CHARS,
    DisplayNameStore,
    NamedRoomMember,
    NamedRoomMemory,
    RoomNames,
    get_display_name,
    get_display_names,
    render_names_in_text,
    resolve_bot_name,
    resolve_memory_authors,
    resolve_room_bot_names,
    resolve_room_members,
    resolve_room_names,
    resolve_user_names,
    set_display_name,
    validate_display_name,
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
from .schema import ROOMS_TABLES, create_schema, reset_tables, schema_path
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
    "DEFAULT_BOT_NAME",
    "FALLBACK_MEMBER_NAME",
    "MAX_DISPLAY_NAME_CHARS",
    "MAX_MEMORY_CONTENT_CHARS",
    "MAX_ROOM_SIZE",
    "MAX_TASK_TITLE_CHARS",
    "MEMORY_KINDS",
    "ROLE_GUEST",
    "ROLE_MEMBER",
    "ROLE_OWNER",
    "ROOMS_TABLES",
    "DisplayNameStore",
    "GuestReadOnlyError",
    "InviteError",
    "LastOwnerError",
    "MoneyApprovalError",
    "NamedRoomMember",
    "NamedRoomMemory",
    "NotRoomMemberError",
    "NotRoomOwnerError",
    "Room",
    "RoomError",
    "RoomFullError",
    "RoomMember",
    "RoomMemory",
    "RoomMemoryStore",
    "RoomNames",
    "RoomNotFoundError",
    "RoomStore",
    "RoomTask",
    "RoomTaskStore",
    "SplitError",
    "add_member",
    "add_task",
    "complete_task",
    "create_room",
    "create_schema",
    "delete_memory",
    "delete_room",
    "due_soon",
    "get_display_name",
    "get_display_names",
    "get_room",
    "is_member",
    "is_owner",
    "leave_room",
    "list_members",
    "list_room_memory",
    "list_rooms_for_user",
    "list_tasks",
    "member_count",
    "overdue",
    "record_decision",
    "record_memory",
    "record_summary",
    "remove_member",
    "render_names_in_text",
    "reset_tables",
    "resolve_bot_name",
    "resolve_memory_authors",
    "resolve_room_bot_names",
    "resolve_room_members",
    "resolve_room_names",
    "resolve_user_names",
    "role_of",
    "schema_path",
    "set_display_name",
    "set_role",
    "split_tasks",
    "summarize_messages",
    "validate_display_name",
]
