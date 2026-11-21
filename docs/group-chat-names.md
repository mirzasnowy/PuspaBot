# Group chat names

Every user gets an editable display name, every bot gets its own
editable name, and the chat surface resolves user ids and bot ids
to those names everywhere: member lists, message authors, memory
authors, decisions. No two bots in one room show the same
hardcoded name; no raw UUID is ever shown to a user.

## Where names come from

| Name | Source | Edit path |
| --- | --- | --- |
| User display name | `users.display_name` (migration 0032, max 100 characters) | Rooms screen, "Nama saya di ruang" form, or `POST /api/profile/display-name` |
| Bot name | The bot's own persona files: the `Name:` line of `IDENTITY.md` in that bot's Puspa home (V2-25, `docs/persona-files.md`), default `Puspa` | Edit `IDENTITY.md` in the bot's home |

User display names live on the `users` row itself because `users`
is already the identity system every room member belongs to. No
second identity system was added. A user edits only their own
name; the store refuses an edit by anyone else.

## The resolver

`puspa/rooms/names.py` is the one shared resolver. The PWA
(`apps/pwa/src/state/names.ts`) mirrors its fallback rules for
fields an older payload does not carry yet, but the server is
authoritative.

- `DisplayNameStore`: set/get display names. Every read hits the
  database, so an edit is visible on the next render.
- `resolve_room_names(room_id, bot_homes=...)`: member display
  names plus each member's bot name in one call. `bot_homes`
  maps a user id to the Puspa home holding that user's bot; a
  member with no entry reads the process home (the single-user
  local case).
- `resolve_memory_authors(rows)`: author display name plus
  rendered content per room-memory row.
- `render_names_in_text(text, names)`: replaces every UUID-shaped
  token in stored text (a decision's `Agreed by:` line) with its
  resolved name.

## Fallbacks (never a raw UUID)

- A user with no display name is `Anggota N` inside a room (N is
  their 1-based position in the member list, owners first, then
  join order, so the name is stable when other members rename
  themselves) and plain `Anggota` without room context.
- A bot with no name file is `Puspa`.
- An unknown id in stored text renders as `Anggota`.

## What the PWA shows

- `RoomsScreen`: member list by display name, memory authors by
  display name, decision content with agreed-by names, and the
  display-name edit form (label, helper text, and a submit
  button, per the `forms` guide in Modern Web Guidance).
- `ChatScreen`: message authors resolved through the bootstrap
  agents list, so a renamed bot is called by its own name in the
  thread, the empty state, and the composer placeholder.
- `Nav`: the brand is the required agent's name from the
  bootstrap, `Puspa` when unknown.
- The room detail and memory payloads carry the resolved names
  (`RoomMember.display_name`, `RoomDetail.names` /
  `bot_names`, `RoomMemory.author_display_name` /
  `rendered_content`) so the hosted rooms API can serve them;
  the local app serves only the own display-name routes
  (`GET`/`POST /api/profile/display-name`, `puspa/pwa/names_api.py`).

## Limits

- The rooms HTTP APIs themselves live in the hosted service, not
  in this repo, so the hosted service must pass the resolved
  names through in the room detail and memory payloads for the
  PWA to show them; until then the PWA falls back locally.
- Live-browser verification was not done here (no live browser
  in this environment). The rendering is covered by component
  and API tests; a live check in a real browser is still open.
