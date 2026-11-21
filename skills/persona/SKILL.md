---
name: "Persona"
description: "The agent's editable identity: three plain files the user owns (soul, identity, profile) plus a reply style learned from their messages. Edits change how the agent talks."
author: "Puspa Team"
---

# Persona

This skill covers the agent's persona: who it is, how it talks,
and what it knows about the user. The user owns these files —
they can read them, edit them, and the agent changes accordingly.

## When to Use This Skill

Use this skill when the user:

- Asks the agent to talk differently ("be shorter", "less
  formal", "stop saying that")
- Asks what the agent's name, character, or vibe is
- Asks to change something about themselves in the profile
  ("I moved to Bandung", "call me by my nickname")
- Asks what the agent knows about them

## The Three Files

The user owns three plain Markdown files in their home
directory (`puspa.persona.files`):

| File | What it holds |
| ---- | ------------- |
| `SOUL.md` | The agent's persona and vibe: how it talks, what it cares about |
| `IDENTITY.md` | The agent's name and identity: who it is |
| `USER.md` | The user's own profile and preferences: what to call them, what matters |

```python
from puspa.persona.files import (
    load_persona_files,
    ensure_persona_files,
    render_persona_block,
)

persona = load_persona_files(home=home)   # reads the three files (defaults if missing)
created = ensure_persona_files(home=home) # creates them with defaults if absent
block = render_persona_block(persona)     # the block the agent actually reads
```

Editing a file changes the agent: the next turn reads the new
text. If the user asks to change how the agent talks, edit the
file — do not just promise to "keep it in mind" for this
conversation and forget it.

## Reply Style

Beyond the files, the agent learns a reply style from the user's
messages (`puspa.persona.persona`):

```python
from puspa.persona.persona import (
    choose_style,
    add_style_samples,
    list_style_samples,
    apply_correction,
    get_persona,
    render_reply,
)

choose_style(user_id, "short")                 # pick a named style
add_style_samples(user_id, messages)           # examples of how the user writes
apply_correction(user_id, "don't say 'great question'")  # a correction
render_reply(persona, base_text)               # the base text, restyled
```

A style correction is recorded (like a memory fact), so it
persists — the same correction does not need repeating next week.
`list_style_samples` shows which examples the style was learned
from, when the user asks why the agent talks the way it does.

## Limits — Read These First

- **The files are the source of truth.** If `USER.md` says the
  user's name is X, the agent calls them X — even if the chat
  history says something else. Do not override the files from
  memory.
- **Edits are the user's.** The agent may propose an edit and
  show it, but the files belong to the user. Never rewrite
  `SOUL.md` or `IDENTITY.md` silently; show the new text first.
- **Style is learned, not assumed.** `choose_style` picks from
  styles the user has actually used or named. Do not invent a
  style ("be more Gen-Z") and claim it is the user's style.
- **Corrections stick.** Once `apply_correction` records a
  correction, later replies must follow it. If a reply breaks a
  recorded correction, that is a bug — say so and fix the reply.

## Example

User: "Jangan panggil aku 'Bapak', panggil aku Nero aja. Terus
jawabnya yang singkat-singkat."

1. Edit `USER.md` to record the name Nero (show the edit first).
2. `apply_correction(user_id, "call me Nero; keep replies short")`.
3. Confirm both changes, and keep replies short from the next
   turn on.
