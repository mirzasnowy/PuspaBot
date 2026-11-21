# Persona files

The user can edit the agent's persona, name, and their own
profile as three plain Markdown files. The agent reads them fresh
on every turn, so an edit changes its behaviour on the next turn.
Like Muse's SOUL.md.

## Files

All three live in the Puspa home (`~/.puspa`, or wherever
`PUSPA_HOME` points), never in the repo:

| File | What it holds |
| --- | --- |
| `SOUL.md` | The agent's persona and vibe: how it talks, what tone it uses, what it never does. |
| `IDENTITY.md` | The agent's name and identity. A `Name: ...` line sets the name the agent introduces itself with. |
| `USER.md` | The user's own profile and preferences: what to call them, what they care about, how they like answers. |

The files are created with defaults on first use (when a turn
loop is built with a persona home). Only missing files are
created; an edit is never overwritten.

## Format

Plain Markdown, one concern per file. No front matter, no
special syntax. The only parsed line is `Name:` in IDENTITY.md:
it wins over a `# Heading`, which is the fallback when no
`Name:` line exists. Everything else is read as-is and handed
to the model as prompt text.

Example IDENTITY.md:

```markdown
# Identity

Name: Bimo

You are Bimo, a personal AI agent the user runs themselves.
```

## How the agent reads them

- Every turn, the turn loop (`puspa.turns.loop.TurnLoop`, built
  with `persona_home`) re-reads all three files from disk and
  rebuilds the leading system message from them. There is no
  cache, so an edit applies on the next turn.
- The rendered block is also available to the prompt assembler:
  `puspa.persona.files.render_persona_block` produces it, and
  `PromptBuilder.build(..., persona=block)` puts it at the head
  of the context layer. Persona files never go in the stable
  layer, so an edit never waits for the next session and never
  invalidates the frozen prompt-cache prefix.
- The loader itself (`puspa.persona.files.load_persona_files`)
  is independent of the turn loop and the database: it only reads
  files.

## Fallback behaviour

The turn never crashes on these files:

- A missing file falls back to its built-in default, silently.
  A fresh home simply behaves like the default agent.
- A file that cannot be read (not valid UTF-8, a directory in
  place of the file, over the 64 KiB cap) also falls back to its
  default, and the fallback is recorded as a `persona_file_warning`
  event on the turn loop's sink, so it shows up in the audit trail.
- An empty file is a deliberate blank: that section renders as
  nothing, with no warning.

## What this does not cover

- The files are prompt text, not learned memory: they do not
  change what the agent remembers (that is the memory system),
  only how it presents itself and what it knows about the user
  on each turn.
- The older database-backed persona (`puspa.persona`, speaking
  style learned from examples and corrections) is a separate
  mechanism and still works as before; the files layer composes
  with it instead of replacing it.

## Several personas, one active

The home's own three files above are the `default` persona.
Extra personas are the same three files in their own folder,
plus an optional reply style — see `puspa.persona.switcher`:

- `<home>/personas/<slug>/SOUL.md` — the prompt for that role.
- `<home>/personas/<slug>/IDENTITY.md` — the agent's name in
  that role (the `Name:` line, same parser as the default).
- `<home>/personas/<slug>/USER.md` — what the agent knows
  about the user in that role.
- `<home>/personas/<slug>/meta.json` — `{"style": "santai"}`,
  one of the speaking styles; it renders as a `# Reply style`
  section in the prompt block.
- `<home>/personas/active.json` — `{"active": "<slug>"}`;
  missing, unreadable, or pointing at a deleted persona means
  the default.

The turn loop reads the active persona fresh on every turn, so
switching takes effect on the next turn and the old persona
stays stored untouched. The Personas screen in the PWA creates,
edits, deletes, and switches personas; editing the files with
any text editor in the Puspa home still works the same way.
