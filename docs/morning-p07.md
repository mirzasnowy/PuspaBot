# P-07 — Morning summary + notes-to-actions (plan section 12.8)

## What it is

Two features, one screen (`/morning`):

1. **Morning summary** — today's schedule, bills due, open tasks,
   national holidays, and prayer times (with imsak during Ramadan),
   from the Google connectors plus two public sources.
2. **Notes to actions** — a voice message, meeting, lecture, or
   study note (`pengajian`) becomes a summary plus a list of actions
   with an owner; every action is a tracked task.

Out of scope (plan 12.8's other three items — small-business
assistant, light personal finance, contextual reminders) — those
are not P-07. No WhatsApp (Meta bans general-purpose AI assistants
on the WhatsApp Business API since 15 Jan 2026). No Figma, voice
cloning, or GPU video.

## Sources and honest status

Every source reports its own status in `source_status`
(`ok` / `connected` / `not_connected` / `unknown` / `unavailable`),
the same pattern as `GET /api/voice/status` (P-03). A source that
is not connected or not reachable says so; its section is empty
with a reason. Nothing is invented.

| Section | Source | Status today |
| --- | --- | --- |
| Schedule | Google Calendar (`calendar_read`) | `not_connected` — Gmail + Drive are connected, Calendar is not yet |
| Bills due | Gmail (`gmail_search` + `gmail_read`) | Connected; candidates are keyword matches, always `verified: false` |
| Open tasks | `tasks` table (P-02 store) | Live |
| Holidays | Nager.Date (`date.nager.at`, no key) | Live, verified from this VM 20 Nov 2026 |
| Prayer times | Aladhan (`api.aladhan.com`, no key), method 20 (Kemenag RI) | Live, verified from this VM 20 Nov 2026 |

**Bills are candidates, not bills.** A message matches when its
subject or snippet contains a bill word (`tagihan`, `jatuh tempo`,
`PLN`, `PDAM`, `cicilan`, `paylater`, ...). A due date is shown
only when it is literally printed in the subject or snippet
(`12 Desember 2026`, `tanggal 25`), labelled with its source.
No amount is parsed out of an email body. The summary tells the
user to open the email to confirm the amount and due date.

**Imsak only during Ramadan.** The Aladhan response carries the
Hijri date; imsak is shown when the Hijri month is 9, otherwise
the section says imsak is shown only during Ramadan.

## Notes to actions

Extraction is deterministic and rule-based (`puspa/morning/notes.py`):
a line is an action when it starts with a bullet, contains an
Indonesian action verb, says `todo`, or names an owner with `@`.
The owner comes from `@Name` or an explicit `PJ:` /
`penanggung jawab:` line; without one the action is unassigned
and the screen says so. The due hint is only the date words
printed in the line (`besok`, `Senin`, `12 Desember`), kept
verbatim, never parsed into a date the note did not state. The
summary is extractive: the note's first two statements, never a
paraphrase.

Storage: the note (raw text, summary, actions with task ids) goes
to `morning_notes` (migration 0025); each action goes to the
`tasks` table as a tracked task (`status: accepted`), so it shows
on the Tasks screen and in the weekly report. The transcript
behind a voice note comes from the P-03 voice pipeline — the
transcriber is still a stub, and that limitation carries over:
a voice note's transcript is only as real as the configured
transcriber. Audio is deleted right after transcribing, like
`/api/voice/upload`.

## Endpoints

Contract: `docs/openapi-morning.yaml` (split out of the main spec
to stay under the 2000-line cap; the main spec holds `$ref` stubs).

- `GET /api/morning/status` — per-source availability.
- `GET /api/morning/summary?date=YYYY-MM-DD&city=Jakarta` — the summary.
- `POST /api/morning/notes/text` — `{text, source, title}`.
- `POST /api/morning/notes/voice` — multipart `audio`, optional `source`/`title`.
- `GET /api/morning/notes` — newest first.
- `GET /api/morning/notes/{note_id}` — one note with full text.

## What is NOT verified live

- **Google Calendar**: not connected, so no real calendar event
  has ever flowed through the summary; the code path is tested
  with a fake connector only.
- **Gmail bills**: the connector is connected, but no real bill
  email was read during this work; candidates are tested with
  fake messages only.
- **Voice notes**: the transcriber is a stub; a voice note on a
  real phone has not been tested (same limitation as P-03).
- **Prayer times / holidays**: verified live from this VM on
  20 Nov 2026 (Aladhan Jakarta timings + Hijri date; Nager.Date
  2026 Indonesia holidays). Both are free public APIs with no
  key; if either stops answering, its section reports
  `unavailable`.

## Files

- `puspa/morning/` — `prayer.py`, `holidays.py`, `summary.py`, `notes.py`
- `puspa/pwa/morning_api.py` — HTTP layer
- `puspa/migrations/versions/0025_morning_notes.sql`
- `docs/openapi-morning.yaml` + `$ref` stubs in `docs/openapi-pwa.yaml`
- `apps/pwa/src/screens/MorningScreen.tsx` (+ test), `apps/pwa/templates/morning.html`
- Tests: `tests/test_morning_{prayer,holidays,summary,notes}.py`,
  `tests/test_pwa_morning_api.py`
