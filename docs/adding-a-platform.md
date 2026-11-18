# Adding a Platform (a third channel adapter)

This is the Puspa version of the hermes-agent
`gateway/platforms/ADDING_A_PLATFORM.md` guide, rewritten for this
repo. Follow it step by step; a new adapter is one module plus
tests plus one line in the runner — never a change to the shared
pipeline.

## 1. Read the pattern first

- `puspa/channels/base.py` — the `ChannelAdapter` interface you
  must implement (`parse_update`, `send_text`, `verify_webhook`,
  `poll_once` where the platform polls).
- `puspa/channels/telegram.py` — the polling adapter to copy the
  shape from (raw HTTPS, bounded retry + backoff, injectable HTTP
  client and sleep).
- `puspa/channels/whatsapp.py` — the webhook adapter to copy the
  shape from (handshake, signature check, batch payload parsing).
- `puspa/channels/inbound.py` — the pipeline your adapter feeds.
  Do NOT change it for a new platform: if your platform needs a
  pipeline change, that is a design discussion, not an adapter
  step.

## 2. Create the adapter module

New file `puspa/channels/<platform>.py`, in English, under 1000
lines. It must:

- Subclass `ChannelAdapter` and set `platform = "<platform>"`.
- Use ONLY the already-pinned `httpx` for HTTP. No new
  dependency. If the platform's official library is copyleft or
  non-commercial (LGPL, PolyForm Noncommercial, …), do NOT add
  it — the platform API is plain HTTPS and needs no library.
- Take every credential (token, secret, ids) as constructor
  arguments, never read them from the environment inside the
  adapter, never hardcode them, never log them.
- Accept an injectable `http_client` (an `httpx.Client`) and an
  injectable `sleep` callable. Tests MUST be able to run the
  adapter on `httpx.MockTransport` with a no-op sleep and never
  touch the real platform.
- Implement `parse_update(payload) -> list[MessageEvent]`:
  translate the platform payload into normalised events. Mark
  bot senders with `is_bot=True` (or mark the bot's own id when
  the platform has no bot flag). Mark group/channel messages
  with `is_group=True`. Non-message payloads (status callbacks,
  reactions, edits) produce no event. Never raise for an unknown
  payload shape — return `[]`.
- Implement `send_text(chat_id, text) -> dict`: one send with a
  bounded retry (3 attempts, 1s/2s backoff, same as Telegram and
  WhatsApp). Raise `DeliveryError` after all attempts fail.
  Return the platform's response dict.
- Implement `verify_webhook(request) -> bool` when the platform
  has webhooks (token header or HMAC signature). Polling-only
  platforms leave the base implementation (returns `False`).
- Implement `poll_once(...)` only when the platform polls, and
  expose a `next_offset(...)` helper so the gateway can persist
  the resume position in the heartbeat state.

## 3. Add the config

In `puspa/channels/config.py`:

- Add the new env variables to `ChannelConfig` (token, ids,
  webhook secret) following the existing naming
  (`<PLATFORM>_...`).
- Add `require_<platform>()` and a `<platform>_enabled` property.
- Document the variables in the `config.py` module docstring and
  in `docs/channels.md`.

## 4. Wire the runner

In `puspa/channels/run.py`:

- Add `<platform>_adapter()` (build once, cache in
  `self._adapters`) and one branch in `adapter_for()`.
- Nothing else. The pipeline, the ledger, the pairing, and the
  heartbeat already work for any platform that yields
  `MessageEvent`s.

## 5. Write the tests

New file `tests/test_channels_<platform>.py`. It must cover, at
minimum:

- `parse_update`: a normal message, a bot sender, a group
  message (when the platform has one), a non-message payload
  (empty result), a batch with several messages (when the
  platform batches).
- `send_text`: success (assert the URL and the auth header),
  retry-then-success (assert the attempt count and the backoff
  sleeps), exhausted retries (assert `DeliveryError`).
- `verify_webhook`: good credential passes, wrong credential
  fails, missing credential fails (when a secret is configured).
- `poll_once` + offset advance (polling platforms only).
- No test may contact the real platform: `MockTransport` only,
  fake tokens only.

## 6. Update the docs and the attribution

- `docs/channels.md`: the module map, the config table, and the
  honest-limits section.
- `REUSE.md` + `NOTICE.md`: the pattern attribution for the new
  adapter (patterns only, never copied verbatim; no new
  dependency unless its license is permissive — record it).

## 7. Verify

Run the checks the coordinator runs (do NOT run the central suite
yourself while it is running — the coordinator runs these):

```
.venv/bin/ruff check puspa/channels tests/test_channels_<platform>.py
.venv/bin/ruff format --check puspa/channels tests/test_channels_<platform>.py
.venv/bin/python -m mypy puspa/channels
.venv/bin/python -m pytest tests/test_channels_<platform>.py -q
```

## Checklist before the adapter is called done

- [ ] Adapter module under 1000 lines, English naming only.
- [ ] No new dependency; no copyleft/non-commercial library.
- [ ] No token/secret hardcoded or logged; config from env only.
- [ ] `MockTransport` tests pass; no test touches the real platform.
- [ ] Bot-loop guard works (bot sender dropped by the pipeline).
- [ ] Group messages handled (dropped or answered — decided and documented).
- [ ] Delivery recorded in the ledger (via `DeliveryService`).
- [ ] Docs + REUSE.md + NOTICE.md updated.
