# Channels: Telegram, WhatsApp, Discord, Slack and LINE

Channels are **access channels, not the home**. The home stays the
PWA. A channel is a door the user can knock on — Telegram first,
WhatsApp second, Discord, Slack and LINE next — and the same agent
answers behind every door.

> **Honest limits, up front.** No live bot is verified in this
> iteration. There is no real Telegram, WhatsApp, Discord, Slack
> or LINE token in scope, no message was ever sent to a real chat
> on any of them, and nothing here contacts `api.telegram.org`,
> `graph.facebook.com`, `discord.com`, `slack.com` or `api.line.me`
> outside a test's `MockTransport`. What exists is the full
> pipeline — adapters, authorisation, pairing, delivery ledger,
> heartbeat — tested against fake HTTP transports. The first live
> check (a real bot token, one real DM) is a follow-up. The agent
> seam (`TaskRunnerAgent`) also needs its production executor
> wired (the model call through the Phase 0 chassis); until then
> it fails closed instead of pretending to answer.

## Architecture

One core, many adapters (the hermes-agent gateway pattern,
rewritten as PuspaBot code — nothing copied verbatim):

```
Telegram update / WhatsApp webhook / Discord poll / Slack poll / LINE webhook
        |
        v
Adapter (telegram.py / whatsapp.py / discord.py / slack.py / line.py)
        |  -- raw HTTPS via pinned httpx; no new dependency
        |  normalises to MessageEvent
        v
GatewayRunner (run.py)               -- one live runner per profile
        |
        v
handle_inbound (inbound.py)          -- the SAME steps for every platform
   1. bot-loop guard (bot / self / group -> drop)
   2. pairing command (6-digit code in a DM -> bind chat)
   3. idempotency (delivery ledger dedup_key -> drop duplicate)
   4. authorisation (allowlist or pairing -> else drop silently)
   5. agent (TaskRunnerAgent -> TaskRunner) -> reply via origin adapter
        |
        v
DeliveryService (delivery.py)        -- send + record in the ledger
```

Module map (`puspa/channels/`):

| Module | Job |
| --- | --- |
| `events.py` | `MessageEvent`: platform, chat_id, sender_id, text, message_id, timestamp, is_bot, is_group. `dedup_key` (`platform:chat:message`) and `session_key` (`platform:chat`). |
| `base.py` | `ChannelAdapter`: `parse_update`, `send_text`, `verify_webhook`, `poll_once`. |
| `telegram.py` | Telegram Bot API over raw HTTPS. Long polling (`getUpdates`) + webhook parsing. `sendMessage` with 3 attempts, 1s/2s backoff. |
| `whatsapp.py` | WhatsApp Cloud API over raw HTTPS. Webhook handshake (`hub.verify_token`), HMAC-SHA256 signature (`X-Hub-Signature-256`), `/{phone_number_id}/messages` send with the same retry shape. |
| `discord.py` | Discord Bot API over raw HTTPS. REST polling (`GET /channels/{id}/messages?after=...`, no gateway websocket, no new dependency) + `MESSAGE_CREATE` parsing. Ed25519 interaction signature (`X-Signature-Ed25519`, pinned `cryptography`). `POST /channels/{id}/messages` send with the same retry shape. `check_token()` proves the token via `GET /users/@me`. |
| `slack.py` | Slack Web API over raw HTTPS. REST polling (`conversations.history` with `oldest`, per channel) + Events API parsing. HMAC-SHA256 signing-secret signature (`X-Slack-Signature`, 5-minute replay window) and the URL-verification handshake. `chat.postMessage` send with the same retry shape. `check_token()` proves the token via `auth.test`. |
| `line.py` | LINE Messaging API over raw HTTPS. Webhook-only (LINE has no polling endpoint): HMAC-SHA256 signature (`X-Line-Signature`, channel secret), `events[]` parsing. Push send (`POST /v2/bot/message/push`) for replies plus `reply_text()` with the one-use reply token. `check_token()` proves the token via the message quota endpoint. |
| `authz.py` | `Allowlist` (`platform:chat_id` pairs) + `ChannelPairing` (6-digit codes, hash-only, 5-minute TTL, single use, 5 wrong codes lock a chat 10 minutes). |
| `ledger.py` | Delivery ledger: in-memory + Postgres (`channel_deliveries`). Idempotency (`seen`) + per-attempt audit. |
| `inbound.py` | The shared pipeline (guard -> pairing -> dedup -> authz -> agent -> reply). Returns an `InboundOutcome`; never raises for a drop. |
| `delivery.py` | `DeliveryService`: send through an adapter, record pending -> sent/failed. Ledger failure never loses a send. |
| `agent.py` | `TaskRunnerAgent`: one message = one `Task` with a single `respond` step on the existing `TaskRunner`. The production executor is a named follow-up. |
| `notify.py` | Content-free notifications: the text is the bot name only, never message content (the DuskBot rule). |
| `heartbeat.py` | `HeartbeatStore`: last beat + last polling offset in one JSON file per profile; restored at start. |
| `run.py` | `GatewayRunner`: the facade. One live runner per profile (sessions and persistence (single-writer, full-text search, lineage) single writer, process-wide registry). |
| `config.py` | `ChannelConfig` from env. Tokens only from env, never hardcoded. |
| `schema.py` | Applies migration `0008_channels.sql`. |

## Message flow

1. Telegram sends an `Update` (webhook POST) or the gateway polls
   `getUpdates`. WhatsApp sends a webhook POST. Discord and Slack
   are polled over REST (`poll_discord_once` /
   `poll_slack_once`). LINE sends a webhook POST. The adapter turns
   the payload into `MessageEvent`s.
2. The pipeline drops bot messages and the bot's own messages.
   Group messages go to the optional `group_handler` hook (group chat,
   `puspa/rooms/routing.py`): a group chat linked to a room
   answers @PuspaBot messages with the room as principal; unlinked
   groups, and group chatter without a mention, are dropped.
3. A bare 6-digit code in a DM is a pairing attempt (see below).
4. The ledger is checked by `dedup_key`: a redelivered update is
   dropped silently (exactly-once).
5. The chat must be allowlisted or paired; anything else is
   dropped silently and recorded as a failed inbound row (visible
   to the operator, invisible to the sender).
6. The agent answers; the reply goes back through the adapter the
   message came from, and the send is recorded.

## Pairing (DM pairing as authorisation)

A user who is already logged in to the PWA asks for a pairing
code there, reads it off the PWA, and sends it to the bot in a DM:

1. PWA calls `ChannelPairing.issue(user_id)` -> `{"code", "expires_at"}`.
   Only the SHA-256 hash of the code is stored; the code itself is
   returned once and never written anywhere.
2. The user sends the 6-digit code in a DM. The pipeline verifies
   it and binds `platform:chat_id -> user_id`.
3. The bot answers "Paired. This chat is now connected to your
   PuspaBot account." Every later message from that chat resolves to
   that user.

Rules (same shape as `puspa.hosting.pairing`): 6 digits, single
use, 5-minute TTL, 5 wrong codes from one chat lock that chat for
10 minutes. The store is in-memory and process-local in this
iteration — bindings die with the process. The durable home for
bindings is a follow-up with its own schema migration.

## Allowlist

The operator's own chats can skip pairing:
`PUSPA_CHANNEL_ALLOWLIST="telegram:123456789,whatsapp:628123456789"`.
An allowlisted chat is authorised but has no bound user (the
caller treats it as the operator's own chat). With an empty
allowlist and no pairing, the gateway answers nobody.

## Delivery ledger: own table, not the core ledger

**Decision:** delivery records live in their own Postgres table,
`channel_deliveries` (migration `0008_channels.sql`), NOT in the
core ledger (`puspa.ledger`).

- The core ledger is a closed set of 31 task event types in a
  hash-chained audit log for tasks. Delivery records are
  operational rows: they need lookup by platform message id and
  per-attempt status updates, which the append-only task log
  cannot do without a new event type.
- No new core event type is added here. The task log stays at 31
  types; the channel table carries `id, platform, chat_id,
  direction, status, dedup_key, attempts, error, created_at,
  updated_at`, with a `UNIQUE (dedup_key, direction)` constraint
  (first write wins — a redelivery never overwrites the first
  record).
- **No message content is stored in this table** — only ids,
  direction, status, attempts, and error text. Same rule as the
  content-free notifications.

Apply it (after migration `0001_core.sql`):

```
python -m puspa.migrations up --target 0008
```

Or from Python: `puspa.channels.schema.create_schema()` (the test
suite calls it; `conftest` does not apply it — same pattern as the
teams schema).

## Sessions: single writer (sessions and persistence (single-writer, full-text search, lineage))

One live `GatewayRunner` per profile, enforced process-wide:
`GatewayRunner.open()` raises `SingleWriterError` when a runner
for the same profile is already live. The runner is the only
writer of channel state (pairing bindings, heartbeat/offset,
ledger writes through the pipeline). Every other surface reaches
channel state through the runner or a read-only ledger. The
session key for a chat is `platform:chat_id` (`MessageEvent.session_key`).

## Heartbeat / restore

`HeartbeatStore` keeps one JSON file per profile in the state
dir (`PUSPA_CHANNEL_STATE_DIR`, default `.puspa-channels`):
`{"profile", "last_beat_at", "last_offset"}`. Writes are atomic
(temp file + rename). At start, the runner restores the last
polling offset, so it neither reprocesses a flood of old messages
nor loses messages that arrived while it was down. `poll_telegram_once(drop_backlog=True)`
skips a stale backlog without answering it (first start after a
long downtime). A missing or corrupt state file restores to "no
offset" (poll from the beginning).

## Notifications without message content

The DuskBot rule: a push/notification from a channel carries
ONLY the bot name — never message content. `notify.notify()`
sends exactly `bot_name` (default `"PuspaBot"`). The user opens the
PWA to read what happened; the channel is the doorbell, not the
letter.

## Configuration

All tokens from the environment, never hardcoded, never logged:

| Variable | Meaning |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token. Enables Telegram. |
| `TELEGRAM_WEBHOOK_SECRET` | Optional secret Telegram sends as `X-Telegram-Bot-Api-Secret-Token`. |
| `WHATSAPP_ACCESS_TOKEN` | WhatsApp Cloud API access token. |
| `WHATSAPP_PHONE_NUMBER_ID` | The sender phone number id. |
| `WHATSAPP_VERIFY_TOKEN` | Webhook verification token (GET handshake). |
| `WHATSAPP_APP_SECRET` | App secret for the `X-Hub-Signature-256` check (recommended). |
| `DISCORD_BOT_TOKEN` | Discord bot token. Enables Discord. |
| `DISCORD_CHANNEL_IDS` | Comma-separated Discord channel ids the gateway polls. |
| `DISCORD_PUBLIC_KEY` | Optional Ed25519 public key (hex) for the interaction signature check. |
| `DISCORD_BOT_USER_ID` | Optional bot user id (bot-loop guard). |
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`). Enables Slack. |
| `SLACK_CHANNEL_IDS` | Comma-separated Slack channel ids the gateway polls. |
| `SLACK_SIGNING_SECRET` | Optional signing secret for the `X-Slack-Signature` check. |
| `SLACK_BOT_USER_ID` | Optional bot user id (bot-loop guard). |
| `LINE_CHANNEL_ACCESS_TOKEN` | LINE channel access token. Enables LINE. |
| `LINE_CHANNEL_SECRET` | Optional channel secret for the `X-Line-Signature` check. |
| `LINE_BOT_USER_ID` | Optional bot user id (bot-loop guard). |
| `PUSPA_CHANNEL_ALLOWLIST` | `platform:chat_id,...` pairs answered without pairing. |
| `PUSPA_CHANNEL_STATE_DIR` | Heartbeat/offset state dir (default `.puspa-channels`). |
| `PUSPA_BOT_NAME` | Bot name in content-free notifications (default `PuspaBot`). |

No new dependency was added: all five adapters use the
already-pinned `httpx==0.28.1`. (python-telegram-bot is LGPL and
is not used; the Telegram Bot API is plain HTTPS and needs no
library. discord.py, slack-sdk and line-bot-sdk are not used
either, for the same reason; Discord's Ed25519 check uses the
already-pinned `cryptography` package.)

## Honest limits

- **No live bot verified.** No real Telegram/WhatsApp/Discord/
  Slack/LINE token, no real DM answered, no real message sent.
  Tests use `httpx.MockTransport` only.
- **Discord inbound is REST polling, not the gateway
  websocket.** Polling `GET /channels/{id}/messages` needs the
  channel ids up front (`DISCORD_CHANNEL_IDS`) and the bot to
  already be a member of each channel; a live gateway
  (websocket) client would see DMs and new channels without a
  list, but it needs a websocket dependency this transport
  deliberately avoids.
- **LINE is webhook-only.** LINE offers no polling endpoint, so
  the adapter cannot fetch messages itself; a public webhook
  URL is required for inbound LINE messages. The reply token is
  one-use and short-lived: `reply_text()` only works while the
  webhook handler still holds it, otherwise the pipeline's
  push send is used.
- **Slack polling needs channel membership + scope.** The bot
  must be in each polled channel and the token needs
  `channels:history` / `groups:history` / `im:history` for the
  channel kinds polled; `conversations.history` only returns
  messages the bot can see.

## Wiring to the agent core

`TaskRunnerAgent` (`agent.py`) is the one narrow seam. One inbound
message becomes one `Task` (single `respond` step) on the existing
`TaskRunner` (`puspa.runtime.tasks`). The step executor is injected
by the caller. The production executor — a call to the model
through the Phase 0 chassis — is a **named follow-up**; until it
lands, the seam fails closed (`ChannelError` inside the executor
becomes an honest "could not handle that message" reply, never a
fabricated answer). No second agent is built here.

- **DM-only by default.** Group messages are dropped unless a
  room is linked to the group chat (group chat, `docs/rooms-b6.md`):
  the link plus an @PuspaBot mention routes the message to the room.
  Group support is otherwise a follow-up.
- **Pairing bindings are in-memory.** They die with the process;
  the durable binding table is a follow-up.
- **Production agent executor not wired.** The seam exists and is
  tested with injected executors; the model call is a follow-up.
- **WhatsApp template messages not covered.** Only free-form text
  replies inside the 24-hour window are sent; template sends for
  messages outside the window are a follow-up.
- **No multiplex yet.** One runner serves one profile; serving
  many profiles from one process (hermes multiplex) is a
  follow-up on top of the multi-tenant hosting foundation (multi-tenant hosting and teams).
