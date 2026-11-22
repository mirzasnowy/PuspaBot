---
name: "Skill Creator"
description: "Turns a described workflow or API into a reusable skill: interviews the user, drafts the SKILL.md, validates it, and registers it as a trial skill."
author: "Puspa Team"
---

# Skill Creator

This skill turns a workflow or a third-party API the user describes
in conversation into a reusable skill. It never invents the missing
pieces: when the description is incomplete it asks a follow-up
question instead of guessing, and when the draft is invalid it says
so instead of registering a broken skill.

All of the work goes through `puspa/marketplace/skill_creator.py`
— the interview session, the draft renderer, the validator, and
the trial registration. The skill compiler behind the registration
is `puspa/governor/compiler.py`, and the authoring standards the
draft must pass live in `puspa/marketplace/authoring.py`.

## When to Use This Skill

Use this skill when the user:

- Says "make a skill for X", "create a skill", or "teach me how
  to automate X"
- Describes a workflow they repeat and wants it saved as a skill
- Pastes an API description or a base URL with endpoints and wants
  a skill that calls it
- Asks whether Puspa can learn a new trick from a description

## How to Author a Skill

### Step 1: Interview

Drive `SkillCreatorSession` with the user's replies. It asks for
what is missing, in this order:

1. The description — a workflow with steps, or an API with a base
   URL and endpoints.
2. The skill name.
3. For a workflow: the steps, one per line, and which tool runs
   each step when the step does not name one.
4. For an API: the base URL, the endpoints as `METHOD /path`
   lines, and the auth kind (none, api key, bearer token, oauth).

```python
from puspa.marketplace.skill_creator import SkillCreatorSession

session = SkillCreatorSession(author="user")
question = session.next_question()
question = session.answer(user_reply)
draft = session.draft()
```

A reply that does not answer the question re-asks it with the
reason — an empty reply, an unknown tool, a bad URL, an unknown
auth kind. Never answer on the user's behalf.

### Step 2: Show the Draft

Render the draft and show it to the user before anything is
registered:

```python
from puspa.marketplace.skill_creator import render_skill_md, validate_draft

text = render_skill_md(draft)
problems = validate_draft(draft)
```

The draft is a `SKILL.md` in the repo's own format: frontmatter
with a short description and the author, then the canonical
sections in order — Description, When to Use This Skill, Tools,
Configuration, Examples. A workflow draft lists its steps with
their tools; an API draft lists its endpoints and declares its
base URL and secret as setup-time config.

### Step 3: Validate

`validate_draft` returns the problems, empty when the draft is
valid. It checks the name and id, that every step names a tool
that resolves, that an API draft has a real base URL and at least
one endpoint, and that the rendered `SKILL.md` parses and passes
the authoring standards. When it returns problems, show them and
ask for the missing piece — never register the draft.

### Step 4: Register as a Trial Skill

Only with the user's explicit approval, register through the
skill compiler:

```python
from puspa.governor import InMemorySkillStore, SkillCompiler
from puspa.marketplace.skill_creator import register_trial_skill

compiler = SkillCompiler(InMemorySkillStore())
result = register_trial_skill(draft, compiler)
```

The skill is stored under `authored:<skill id>` with status
trial: versioned and replayable, never silently active. The trial
replay only proves the steps are well-formed and every tool
resolves; it runs nothing for real. Tell the user the skill is a
trial and what would promote it — repeated successful runs, the
same lifecycle the depth governor applies to compiled skills.

## Limits

- The interview cannot run a workflow it has never seen: steps
  must be described, and a step that names no known tool needs
  the user to name the tool.
- An API skill only calls endpoints over plain HTTP verbs through
  `fetch_url`; it cannot run a vendor SDK or a login flow the API
  does not expose as endpoints.
- A drafted skill is a trial until it proves itself. Never present
  a trial skill as ready, and never enable one without the user
  approving the exact draft shown.
- Secrets (API keys, bearer tokens) are setup-time config, never
  pasted into chat or into the skill body.

## Example

User: "Every morning check the price of beras, then tell me if it
went up."

Agent: "List the steps, one per line — numbered or bulleted. Name
the tool in a step when you know it."

User: "1. check the price of beras with price_check 2. tell me the
result"

Agent: "What should the skill be called?"

User: "Morning beras check"

Agent shows the draft `SKILL.md` for "Morning beras check" — two
steps, the first with `price_check`, the second asking which tool
sends the message — and only registers it as a trial skill after
the user approves that exact draft.
