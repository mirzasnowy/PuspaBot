---
name: "Rooms"
description: "Shares one Puspa agent with a small group: rooms with roles, a shared task board, group memory, and chat summaries. Data layer only — invites and channel routing live in the hosted service."
author: "Puspa Team"
---

# Rooms

This skill covers rooms: small groups (2-5 people, no shared
funds) that share one Puspa agent. The agent works for the group,
not for one person: it summarises the chat, splits tasks, records
decisions, and reminds owners about deadlines.

## When to Use This Skill

Use this skill when the user:

- Asks to create a group room, add or remove members, or change
  a member's role
- Asks what the group decided, who owes what, or what is overdue
- Asks to summarise a group chat or split it into tasks
- Asks to leave or delete a room

## How Rooms Work

### Rooms and Roles

```python
from puspa.rooms import RoomStore

store = RoomStore(dsn=dsn)
room = store.create_room("Family", created_by_user_id=user_id)
store.add_member(room.id, new_user_id, role="member", added_by=user_id)
store.set_role(room.id, member_id, "owner", changed_by=user_id)
store.list_members(room.id)
store.leave_room(room.id, user_id=user_id)
store.delete_room(room.id, requested_by=user_id)
```

Roles are `owner`, `member`, and `guest`. Only the owner can
invite, change roles, and delete the room. When a member leaves,
their private data leaves with them. Member lists, message
authors, and decisions never show a raw UUID: names go through
the shared resolver in `puspa.rooms.names` (per-user display
names and per-bot names).

### The Shared Task Board

```python
from puspa.rooms import RoomTaskStore

tasks = RoomTaskStore(dsn=dsn)
task = tasks.add_task(room_id, title="Buy tickets", owner_id=..., due=...)
tasks.complete_task(room_id, task_id, user_id=user_id)
tasks.list_tasks(room_id, viewer_user_id=user_id)
tasks.due_soon(room_id, viewer_user_id=user_id)
tasks.overdue(room_id, viewer_user_id=user_id)
```

### Chat Summary and Task Splitting

```python
from puspa.rooms import summarize_messages, split_tasks

summary = summarize_messages(messages)  # deterministic summary of the chat
items = split_tasks(text)              # task lines pulled out of the text
```

Meeting action items are board tasks too (see the `meetings`
skill): a meeting's `TODO` lines land on the same board.

### Group Memory

`RoomMemoryStore` keeps group memory in three layers: personal
(never in a room), room-visible, and private answers only their
author may read. A fact's layer decides who can see it — check
the layer before quoting group memory back to anyone.

## Limits — Read These First

- **Data layer only.** This package is rooms, the task board,
  and group memory. Invites, delegation, channel routing, the
  job queue, money splits, group research, and the rooms HTTP
  APIs live in the hosted service, not here. If the user asks to
  invite someone by phone number or link, say the invite step
  runs in the hosted service.
- **Beta-lite: 2-5 people, no shared funds.** Larger groups and
  shared money are out of scope. Do not promise them.
- **Membership-checked everywhere.** Listing tasks, reading
  minutes, and reading group memory all require membership. A
  non-member gets nothing.
- **Leaving removes private data.** Tell the user before they
  leave: their private rows go with them and are not recoverable
  from the room.

## Example

User: "Buatin grup buat keluarga, terus bagi tugas bersih-bersih."

1. `create_room("Family", created_by_user_id=...)`, then
   `add_member` for each family member.
2. `split_tasks` on the user's message to pull out the chores,
   then `add_task` for each with its owner.
3. Show the board; owners get reminded through the `meetings`
   skill's reminder sweep.
