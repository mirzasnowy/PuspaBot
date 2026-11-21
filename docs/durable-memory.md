# Durable MEMORY.md

PuspaBot remembers what matters in a file called MEMORY.md. It is
written in plain language, so the user can read it and fix it by
hand. This page explains how that file is built, where it lives,
and what happens when the user edits it.

## What it is

MEMORY.md is the long-term memory the agent reads before it does
anything. It has one section per kind of thing worth remembering:

- Standing Goals: what the user is aiming at.
- Commitments: promises the agent or the user made.
- Corrections: things the user fixed. These never fade.
- Preferences: how the user likes things done.
- Facts: plain facts about the user.
- People: people the user talks about.
- Lessons: what past work taught the agent.
- Unconfirmed Guesses: things the agent inferred but the user
  never confirmed. These are labelled as guesses, never shown as
  facts.
- Journal: one short entry per day, the story of what happened.

## How it is built

Every night the rebuild runs (`run_nightly_rebuild` in
`puspa/memory/durable.py`). It reads the lower memory layers and
rewrites the file from them, so the file is always a view of the
real memory, never a separate copy that drifts:

- Profile facts: the active facts, filed into their section by
  key. A fact the user confirmed goes under Corrections.
- Episodic rows: the per-user ledger of what happened. The
  knowledge graph is built from these rows, and the names it
  finds appear in the People section.
- Consolidation summaries: the nightly summaries become Lessons.
- Narrative journal: the newest daily entries become the Journal
  section.

The rebuild is deterministic: the same facts, episodes, and
summaries rebuild the same file, byte for byte. The stored
version number only grows when the text actually changes, so a
quiet night changes nothing.

## The narrative journal

The journal is the day-by-day story. `build_daily_entry` writes
one short entry per day from that day's summaries and completed
tasks, in plain sentences, with no model call. One row per user
per day lives in the `narrative_journal` table; rebuilding the
same day replaces that day's draft, past days are never
rewritten.

## When the user edits the file

An edit beats inference. `apply_user_edits` reads the edited
file against the generated one:

- A changed line becomes a user-confirmed fact, which replaces
  the old value. If the old value was only an inferred guess,
  the guess is rejected, so it cannot come back.
- A deleted line expires the fact. The old row stays in history
  as expired; nothing is hard-deleted.
- A new line in the Lessons section becomes a `lesson:` fact, so
  the next rebuild keeps it.
- Journal lines are history and are never rewritten. Edits there
  are reported as ignored.

## Where it lives

One row per user in the `durable_memory` table (migration
`0029_durable_memory`), holding the text, a hash of the text, a
version number, and per-section counts. The same text is what
the PWA shows and what the user edits.

## What this does not do

Write-through (writing the ledger and durable memory in the same
turn, so a turn cannot finish without both writes) is a separate
item. This module owns the durable store, the nightly rebuild,
the journal, and reading edits back.
