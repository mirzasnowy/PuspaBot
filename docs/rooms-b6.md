# B6 — Invite and collaborate in group chat (shared rooms)

Date: 19 Nov 2026. Source: `docs/tickets-beta-plan.md` section B6,
plan section 8.1 (shared rooms lite) and section 12.1 / decision
P-05. Beta-lite scope: 2-5 people, no shared funds.

## What it is

A user invites other users into a shared room; the agent works for
the group, not for one person: it summarises the chat, splits
tasks, records decisions, and reminds owners about deadlines. Each
member shares only what the group allows.

## Modules

All new code lives in `puspa/rooms/`; the schema is migration `0010_rooms.sql` (applied by
`puspa.rooms.create_schema`, like `puspa/teams` and
`puspa/channels`).

| File | What it owns |
| --- | --- |
| `rooms.py` | Rooms, roles (owner/member/guest), membership, leaving. Max 5 members; a room always has at least one owner; the last owner cannot leave or be demoted. |
| `invites.py` | Invites by link (random token, 7-day TTL) or one-time 6-digit code (24-hour TTL). Hash-only secrets, single use, revocation, idempotent accept. The invite carries the role (member/guest); ownership is never granted by invite. |
| `memory.py` | Group memory in three layers: personal (never in a room — it stays in `puspa/memory`), room-visible, and private answers only their author may read. |
| `tasks.py` | Shared task board (owner, deadline, status) plus the deterministic chat summariser and task splitter. No model call: a summary never invents a decision or a task. |
| `money.py` | Money gate: the owner alone approves; any other member needs a second person (requester + one other owner/member). Guests never count. No shared funds exist in the beta. |
| `routing.py` | Links a platform group chat to a room and routes @Puspa group messages to the room, with the room (`room:<id>`) as principal. |
| `schema.py` / `errors.py` | Schema loader / reset, and the room error types. |

## Invite flow

1. An owner or member issues an invite: a link
   (`https://puspa.app/join/<token>`) or a 6-digit code to read out
   in the group chat. The token/code is returned exactly once at
   issue time; only its SHA-256 hash is stored.
2. The invitee presents the token or code. Acceptance inserts the
   membership and consumes the invite in one transaction, so one
   invite can never mint two memberships. Accepting twice (same
   user) returns `already_member=True` instead of failing.
3. The invite can be revoked by an owner or member; an expired
   invite is marked `expired` on first sight and can never be
   accepted. Accepting into a full room (5 members) is refused and
   leaves the invite pending, so it can still be used after
   someone leaves.

The flow works from a Telegram or WhatsApp group context in the
same way: the link or code is shared into the group, and joining
happens by presenting it. The room itself is transport-agnostic —
no channel-specific invite code exists.

## Group memory: three layers

1. **Personal** — each member's own memory in `puspa/memory`.
   It is never copied into a room and never shown to other
   members. The rooms package never reads it.
2. **Room** — `room_memories` rows with visibility `room`: notes,
   decisions, summaries every member may read.
3. **Private answer** — `room_memories` rows with visibility
   `private`: a member's answer or note that only they may read,
   even inside the room. The agent may use it to answer that
   member privately, but must never repeat it to the group.

The visibility filter is in the SQL (`list_room_memory`), not in
the caller, so a caller cannot forget it. An owner may delete
another member's room-visible row (moderation) but never another
member's private row.

## Leaving: private data leaves with the member

`RoomStore.leave_room` deletes the leaver's private room memories
and their money approvals in the same transaction as the
membership row, and unassigns their tasks (the work stays; the
owner leaves). Shared contributions (room-visible notes,
decisions, summaries) stay: they are the group's record, not the
leaver's private data. The last owner cannot leave — they delete
the room instead.

## @Puspa in the room is the principal

A group message is answered only when all three hold:

1. The group chat is linked to a room (`link_channel` — a member
   links a Telegram group chat id, or a WhatsApp group chat id, to
   a room). An unlinked group is still dropped: Puspa never
   answers a group it was not invited into.
2. The message is addressed to Puspa: the adapter flagged a bot
   mention (`MessageEvent.mentions_bot`) or the text contains
   `@puspa` (case-insensitive). Group chatter without a mention is
   dropped as `dropped_not_addressed` — the agent does not read
   along and does not answer unprompted.
3. The agent is called with `user_id="room:<room_id>"` — the room,
   not the sender — so the answer is the group's answer. The
   sender id still rides in the raw event for permission checks
   inside the agent.

Wiring: `puspa/channels/inbound.py` takes an optional
`group_handler` callable; the room handler
(`puspa.rooms.routing.handle_group_inbound`) is passed in by the
caller. Without a handler, every group message is dropped exactly
as before (G14). The adapter -> MessageEvent -> core ->
delivery-ledger pattern is unchanged, including exactly-once
handling by `dedup_key`.

## Money actions: two people, or the owner

There are no shared funds in the beta — no balance, no wallet.
`MoneyGate` is the gate in front of any future money action
(payment preparation lives in B8): the owner alone approves; any
other member needs a second person (the requester approves, and
one other owner/member approves the same action id). A guest's
approval never counts, and the same person approving twice still
counts once. Approvals stay recorded in `room_money_approvals` so
the group can audit who approved what, and they are deleted when
the approver leaves.

## Chat work: summarise, split, remind

- `summarize_messages` is deterministic: message counts per
  author, `Decision:` lines, `TODO:` / `- [ ]` lines. A chat with
  no decisions lists none — nothing is invented. A model-written
  summary is a follow-up; the deterministic summary is what the
  beta ships.
- `split_tasks` turns only explicit markers (`TODO:`, `- [ ]`)
  into task titles. Ordinary chat is never guessed into a task.
- `RoomTaskStore` is the shared board: owner, optional deadline,
  `open`/`done`. `due_soon` / `overdue` are the reminder queries —
  the caller (a scheduler, B9) sends the reminders; this module
  only answers which tasks need one.

## Honest limits

- **No live Telegram or WhatsApp bot is verified anywhere in this
  repo.** Channel tests use `MockTransport` (same as G14); room
  routing tests use a fake adapter and a fake agent that record
  what would be sent. Build the machinery + tests; do not claim
  live delivery works.
- **WhatsApp groups are a postponed surface** (plan section 12.4):
  the WhatsApp Cloud API has no group messages, so the adapter
  never marks a WhatsApp event `is_group=True`. The link table
  accepts `whatsapp` links anyway, so the routing works the day a
  group-capable WhatsApp transport exists — but live
  WhatsApp-group delivery is out of scope for this iteration.
- **The production agent executor is not wired** (G14 follow-up):
  the room handler calls the same agent seam as DMs, so whatever
  executor the DM path gets, rooms get too. Until then the seam
  fails closed with an honest failure notice.
- **Summaries are deterministic, not model-written** (see above).
  Reminders are queries, not sends: no scheduler sends them yet
  (B9).
- **Room events are not ledger event types.** The ledger's closed
  31-type set (revision 7) is untouched; room actions are audited
  through the room tables themselves (created_at, approvals,
  invite status). General outcome records (B2) are the follow-up
  that gives rooms the shared write path.

## Reuse

Mechanisms taken, rewritten as Puspa code, never copied verbatim:

- `puspa/teams` (G13) — teams as their own entity, hash-only
  invitation tokens, single-use idempotent acceptance, RLS
  policies with SECURITY DEFINER helpers.
- `puspa/channels` (G14) — the adapter -> MessageEvent -> core ->
  delivery-ledger pipeline, exactly-once by `dedup_key`.
- `puspa/memory` (G7 + G24) — the layering idea: personal memory
  stays personal; room memory is a separate store with its own
  visibility rule.

No outside repository was studied for B6 beyond the mechanisms
already recorded in `REUSE.md` for G13/G14.

## Tests

- `tests/test_rooms.py` — rooms, roles, 5-person cap, last-owner rule, leaving, delete.
- `tests/test_rooms_invites.py` — link/code issue, hash-only storage, accept, idempotency, revoke, expiry, full-room refusal.
- `tests/test_rooms_memory.py` — three layers, private-answer visibility, owner moderation limits, leave-takes-private-data.
- `tests/test_rooms_tasks_money.py` — summariser/splitter, task board, due/overdue, leave-unassigns-tasks, money gate (owner alone, two people, guest never counts).
- `tests/test_rooms_routing.py` — link/resolve/unlink, room-as-principal answer, unlinked-group drop, no-mention drop, bot drop, duplicate drop, pipeline hook wiring, adapter mention flags.

Helpers: `tests/rooms_helpers.py` (schema fixture + factories;
the DSN always comes from the `test_dsn` fixture, never a
hardcoded database name).
