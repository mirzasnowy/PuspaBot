# Multi-agent teams

A team is a small group of **peer agents** that collaborate inside
**one shared conversation** to finish a task together. Each member
has its own role, model, skills, knowledge, and workspace — and no
member is the boss of another.

This is what `puspa.rooms` (people sharing one agent) and
`puspa.subagents` (helpers of one main agent) are not: rooms are
for people, subagents are helpers, teams are peers.

Pattern studied from zhayujie/CowAgent (MIT): CowAgent makes the
multi-agent team a headline capability on web, desktop, and IM
channels. The pattern is adapted here and rewritten as Puspa code,
never copied verbatim; see `REUSE.md` and `NOTICE.md`.

## What a team is

```python
from puspa.teams import TeamStore, TeamRunner, Contribution

store = TeamStore()
team = store.create_team("Research desk", owner_user_id)
store.add_member(
    team.id,
    name="researcher",
    role="researcher",
    model="openai/gpt-4o",
    skills=["web_search", "read_page"],
    knowledge=["market-notes"],
    workspace="/work/researcher",
    instruction="Find facts and cite sources.",
    added_by=owner_user_id,
)
store.add_member(
    team.id,
    name="writer",
    role="writer",
    model="anthropic/claude-sonnet-4",
    skills=["draft", "summarize"],
    knowledge=["style-guide"],
    workspace="/work/writer",
    instruction="Write clearly from the researcher's findings.",
    added_by=owner_user_id,
)
```

Rules enforced by `TeamStore`:

- At most 8 members per team (`MAX_TEAM_SIZE`).
- A member name is one token (letters, digits, `_`, `-`): names
  appear in `@name` handoffs, so spaces and `@` would make a
  handoff ambiguous.
- Adding a name that already exists raises `ValueError`; remove
  the old member first.
- Only the team owner may add or remove members or delete the
  team. Deleting a team deletes its members, conversations, and
  messages (`ON DELETE CASCADE`).

## The shared conversation

One run of a team at one task is one conversation
(`team_conversations`), and every turn is one row in
`team_messages`: numbered in order (`seq`) and attributed to the
member who wrote it (`author_agent_name`). The log is the team's
shared memory for that task — every member reads the whole log
before it speaks, so a later member can build on what an earlier
member found.

Status: `running` while the run goes, `completed` when every
member finished, `max_rounds` when the round budget ran out
first. A `max_rounds` log stays readable; nothing is thrown away.

## Running a team

The runner calls no model and no network. How a member produces
its turn is an injected `contributor` callable — the same seam
shape as the injected `executor` in `puspa.runtime.hierarchy`.
Production wiring injects a contributor that calls the member's
own model with its role, instruction, skills, knowledge, and
workspace; tests inject a scripted one.

```python
def contributor(member, history, task):
    # member: TeamMember. history: the shared log so far (list of
    # TeamMessage). task: the task text.
    return Contribution(text="...", done=False)

result = TeamRunner(store, TeamConversationStore()).run(
    team.id, "Write a one-line market update.", contributor=contributor
)
result.status        # "completed" or "max_rounds"
result.rounds_used   # how many rounds the run took
result.messages      # the full shared log, attributed per turn
```

Turn order:

- **Round-robin** in join order: every unfinished member speaks
  once per round, up to `max_rounds` rounds (default 3).
- **Handoff**: when a member's turn mentions `@name` of another
  unfinished member, that member speaks next instead of the
  round-robin order. A mention of a finished member, of the
  speaker itself, or of nobody in the team changes nothing.
- **Done**: a turn with `done=True` finishes that member; it is
  never asked again. The run ends `completed` when every member
  finished.
- **Pass**: empty text writes no message and the member is asked
  again next round.
- **Round budget**: when the budget runs out before every member
  finished, the run ends `max_rounds`.

A run needs at least two members with at least two distinct
roles — a team of one, or two members doing the same job, is not
a team run (`TeamTooSmallError`).

## What is NOT here yet (honest limits)

- **No live model is wired in.** The contributor is a seam: no
  production contributor exists yet that calls a member's model
  with its role, instruction, skills, knowledge, and workspace.
  Until one exists, teams run only with an injected contributor
  (scripted in tests, a real model call in production wiring).
- **Workspaces are recorded, not enforced.** A member's
  `workspace` is a stored path; nothing sandboxes one member's
  work away from another's. Per-member sandboxing lives in the
  hosted runtime, not in this store.
- **No PWA or channel UI.** There is no team page, no team
  picker, and no channel routing for teams yet; the store and
  runner are the data + turn-taking layer only.
- **No cross-team work.** A member belongs to one team; members
  cannot be shared between teams and there is no team-to-team
  handoff.
- **No cost or permission accounting.** Turns are not charged to
  a budget and do not go through the policy gateway; a
  production contributor must charge and gate its own model
  calls, the way the chassis does for single-agent turns.
- **Turns are sequential, not parallel.** Members speak one
  after another in a fixed order; real concurrency is a
  follow-up, the same single-threaded choice the hierarchy
  runner makes for parallel composition.
- **No knowledge retrieval is wired in.** A member's `knowledge`
  list is stored references only; nothing reads those documents
  into the member's context yet.
