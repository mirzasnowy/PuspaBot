# Co-drafting with context memory

Source: plan section 8.8; the co-drafting item in `docs/tickets-beta-plan.md`.
Depends on / overlaps: memory tightening + pluggable memory providers with background review and skill curation (memory), approvals taxonomy (approvals — send is
always an approval), learning loop.

## What it does

The agent drafts messages, documents, or emails in the user's
voice and remembers past decisions and preferences.

- `puspa/drafting/voice.py` — `VoiceProfile`, built from the
  user's existing memory facts (memory tightening/pluggable memory providers with background review and skill curation `puspa.memory.facts`).
  It reads only the `voice.*`, `preference.*`, and `decision.*`
  fact namespaces. A correction is parsed into a rule when it
  uses a recognised shape (`never use X`, `always start with X`,
  `always use X`, `keep it short`, `casual`, `formal` — in
  English or everyday Indonesian); anything else is kept as a
  style note and surfaced with the draft, never silently
  dropped.
- `puspa/drafting/drafting.py` — `DraftingService`: `draft`,
  `correct_voice`, `approve_draft`, `request_send_approval`,
  `send_draft`, `revise_draft`, `get_draft`, `list_drafts`.
  Two stores with the same interface: `MemoryDraftStore`
  (tests) and `PostgresDraftStore` (`drafts` +
  `draft_approvals`, migration `0012_drafting.sql`, RLS
  owner-only like the rooms tables).
- `puspa/pwa/drafts_api.py` — `POST /api/drafts`,
  `GET /api/drafts`, `GET /api/drafts/{id}`,
  `POST /api/drafts/{id}/approve`, `POST /api/drafts/{id}/send`,
  `GET /api/voice/profile`, `POST /api/voice/corrections`.
  Wired into `create_app` (optional `draft_sender` argument;
  unset by default). Contract in `docs/openapi-rooms.yaml`
  (schemas `Draft`, `DraftApproval`, `VoiceProfile`) with
  `$ref` stubs in `docs/openapi-pwa.yaml`; TS client
  regenerated (`npm run generate`, `npx tsc -b` clean).

## Acceptance mapping

| Ticket acceptance | How it is met | Test |
| --- | --- | --- |
| Drafts match the user's recorded voice and past decisions | Voice profile reads `voice.*` / `preference.*` facts; the draft opens with the recorded greeting, applies parsed rules, and surfaces `related_decisions` | `test_draft_reflects_recorded_preference`, `test_related_decision_is_surfaces_but_not_flagged` |
| A draft never sends without approval | `send_draft` is the only send path and requires a recorded approval id; no approval, wrong draft, expired, or already-used approval raises `DraftNotApprovedError` and nothing is delivered; the API maps it to 403 | `test_send_without_approval_is_refused`, `test_send_with_approval_for_another_draft_is_refused`, `test_approve_then_send_with_sender` |
| The user can correct the voice and the correction persists | `correct_voice` records a `voice.correction.<id>` fact (source `user_confirmed`) through the existing memory tightening fact machinery; the next draft applies it | `test_voice_correction_persists_and_changes_later_draft`, `test_voice_correction_changes_next_draft` (API) |
| A draft that contradicts a past decision is flagged | `flag_decisions` matches a recorded `never …` decision against the brief and returns it in `decision_flags`; the draft is still created — the flag is a warning, not a silent rewrite | `test_draft_contradicting_recorded_decision_is_flagged` |

Extra guarantees (beyond the ticket text):

- An approval binds to the SHA-256 of the draft text at
  approval time, expires after 24 hours (the approvals taxonomy window), and
  is consumed by the one send it authorises.
  `revise_draft` returns the draft to `draft` status, so an
  approval granted for the old text is void for the new text.
- With a approvals taxonomy `ApprovalPolicy` wired in,
  `request_send_approval` records the approval at once when
  the policy returns `allow` (a stored tiered answer covers
  sending); with no policy, every send waits for the user's
  explicit approval. The policy path is tested, the no-policy
  path is the default.
- An approved send with no delivery channel wired in raises
  `NoDeliveryChannelError` (API: 501) and the approval is NOT
  consumed — nothing is lost and nothing is claimed as sent.

## Honest limits

- **Draft assembly is deterministic template assembly, not
  free generation.** The brief supplies the content; the
  profile supplies the greeting, closing, and rules. There is
  no LLM call here. A draft is only ever as good as the brief
  and the recorded voice: a vague brief gives a vague draft,
  and a voice with no recorded facts gives the default
  register. A future step can route assembly through the
  agent's model with the same voice profile and the same
  approval gate — the gate does not depend on how the text
  was produced.
- **Decision matching is word overlap, not meaning.** A
  `never …` decision flags when its content words (stemmed,
  through a small fixed Indonesian–English synonym map)
  appear in the brief. It can miss a paraphrase and it can
  flag a coincidental word. A flag is a warning for the user
  to read, never proof of a contradiction, and never a reason
  to rewrite the draft silently.
- **Rule parsing covers recognised shapes only.** A
  correction like "write like you're chatting at a warung"
  is stored and surfaced as a style note, but no rule is
  applied from it — the draft lists it under
  `voice_rules_applied` only when a rule actually applied.
  The unapplied notes are visible in the voice profile so
  the user can see what the agent did not understand.
- **No delivery channel is wired into the PWA yet.**
  `create_app` leaves `app.state.draft_sender` unset, so
  the live PWA refuses sends with 501 until a channel
  (Telegram / WhatsApp / email) is connected. Approving
  works today; delivering does not. The API tests cover
  both states (injected sender, and no sender).
- **Language detection is one marker word.** A brief with
  any common Indonesian word drafts in Indonesian; anything
  else drafts in English. Mixed-language briefs pick the
  side with a marker word, which may not be the side the
  user meant.

## Indonesian context pass Indonesian-context notes

- Default register is everyday Indonesian, never formal:
  a message or email opens with `Halo,` and closes with
  `Terima kasih,` unless the user recorded otherwise. No
  `Yth.` / `Dear Sir/Madam` unless a `formal` rule or a
  recorded greeting says so.
- Corrections work in Indonesian (`jangan pakai …`,
  `selalu mulai dengan …`, `singkat`, `santai`) as well as
  in English, because that is how the user writes them.
- Drafts are personal memory: the `drafts` tables are
  RLS owner-only, and a draft is never copied into a room
  (room memory stays in `puspa/rooms/memory.py`).
- No payment or address is ever collected by drafting;
  a draft that needs a price or an address says so in the
  brief — the agent does not invent one.

## Schema

Run after migration `0001_core.sql`:

```sh
python -m puspa.migrations up --target 0012
```

`puspa.drafting.create_schema()` is the programmatic entry
point (the test suite calls it; conftest does not apply it).
