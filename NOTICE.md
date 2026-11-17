# NOTICE — Third-party code attribution in the Puspa repo

This is the standalone Puspa repo (mirzasnowy/puspa-ai), NOT a fork
of any repo. Third-party code that is reused is recorded here,
with its original license kept alongside. Non-commercial or
copyleft licenses (GPL, CC BY-NC-SA, PolyForm Noncommercial) are not used.

## nanobot (chassis) — MIT

- **Source:** https://github.com/HKUDS/nanobot
- **Version used as reference (local clone, not yet vendored):** HEAD `e767174` (17 Nov 2026), package `nanobot-ai` 0.4.1
- **Location in this repo:** the `nanobot/` directory (a full copy from the clone,
  including its original `LICENSE` file)
- **License:** MIT License
- **Copyright:** Copyright (c) 2025-present Xubin Ren and the nanobot contributors
- **Full license text:** `nanobot/LICENSE`
- **Third-party attribution inside nanobot:** `nanobot/THIRD_PARTY_NOTICES.md`
  (e.g. Tabler Icons, MIT). If nanobot code is reused outside this
  repo, the attribution above plus the LICENSE file must travel with it.

## Reuse rules (Nero's order)

- Never create a GitHub fork of any repo.
- Other people's code is used as a pip dependency, or vendored with its
  original LICENSE file + attribution in this file and in the README.
- Every new vendored entry must be recorded in this file before it is used.


# NOTICE — Puspa Phase 1

## Pattern attribution (not code)

Phase 1 studied the PATTERNS of the following repos and then REWROTE them
as Puspa's own code (no code copied verbatim, no new dependencies):

- **mem0ai/mem0** (Apache License 2.0) — pattern: conversational memory
  layer, sourced facts, scoped per user/agent/app.
  Used as a pattern in `puspa/memory/profile.py`.
- **getzep/graphiti** (Apache License 2.0) — pattern: temporal knowledge
  graph (facts have a validity period); procedures as templates without
  personal data. Used as a pattern in `puspa/memory/profile.py`
  and `puspa/memory/procedural.py`.
- **letta-ai/letta** (Apache License 2.0) — pattern: tiered memory +
  sleep-time compute (tidying memory while idle). Used as a
  pattern in `puspa/memory/consolidate.py`.
- **elizaOS/eliza** (MIT License) — pattern: character file (persona
  as data: name, traits, speaking style). Used as a pattern in
  `puspa/persona/persona.py`.

The full license of each repo lives in its source repo (links in
`docs/reuse.md`). No third-party LICENSE file needs to be
included here because no third-party code is vendored.

## Puspa license

The code in this directory is Puspa's own work (Nero / Luna). The Puspa
repo license follows the `mirzasnowy/puspa-ai` repo on push.
