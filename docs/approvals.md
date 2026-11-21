# Approvals taxonomy (approvals taxonomy)

PuspaBot already had a single outbound gate (`puspa/policy/gate.py`:
content-hash binding, 24-hour token expiry). Every approval looked
the same. This module adds the taxonomy on top of it, in
`puspa/policy/approvals.py`.

Pattern source (studied, rewritten as PuspaBot code, never copied
verbatim — see `NOTICE.md` and `REUSE.md`): OpenBot
`docs/approvals.md` + `app/src/lib/approval-rules/` +
`api/openapi/approvals.yaml` (MIT).

## Action kinds

Seven kinds always ask in every edition:

| Kind | Meaning |
| --- | --- |
| `delete` | Delete something |
| `send_to_other` | Send to another person |
| `share` | Share something |
| `post` | Post publicly |
| `pay` | Pay money |
| `change_credentials` | Change an account password or settings |
| `install` | Install a skill from the marketplace (skill discovery) |

## Tiered answers

When the user answers a question, they pick a scope:

- **Allow once** — approves exactly this action (bound to the exact
  content hash). Used once, then spent.
- **For this conversation** — approves this kind for the rest of this
  conversation.
- **Always** — approves this kind until it expires.

Every answer expires 24 hours after it was granted — the same window
the gate uses for tokens. `ApprovalStore` is in-memory; answers do
not survive a restart (same as the gate's in-memory request table).

## Rules: strictest wins

Rules come from three owners — own, admin, allowed for this
conversation — and the strictest wins. An admin can close one kind
for everyone:

- **Ask every time** — the kind always asks, even when an "always"
  answer exists.
- **Members do it themselves** — the bot must not perform this kind
  at all; members do it themselves. The decision is `deny`.

Decision order in `ApprovalPolicy.decide`:

1. Credential change → escalate to a human, always (see below).
2. Any `members_self_serve` rule for the kind → deny.
3. Any `ask_every_time` rule for the kind → ask.
4. Extra vigilant switch on → ask.
5. Task from check-in → ask before every change.
6. Auto-review findings → ask.
7. A usable tiered answer → allow.
8. Otherwise → ask (these kinds always ask by default).

## Auto-review

A deterministic pre-check runs before a human sees the request
(`auto_review`). It never silently allows; every finding forces
"ask" (or worse):

- unrecognized action (kind could not be read from the tool name or
  button label),
- payment without a nominal amount,
- delete without a named target,
- send without a recipient,
- credential change (never approvable).

## Extra vigilant

A personal switch, default off. When on, every action asks even
when an "always" answer exists. It is a switch on the
`ApprovalPolicy`, not a stored setting: the caller owns where the
value comes from.

## Tasks from check-in

A task that comes from check-in asks before every change, whatever
the answers say (`from_checkin=True` in `decide` / `request`).
Admin closure still outranks it: a `members_self_serve` rule denies
even a check-in task.

## Recognition: tool name and button label, never content

`classify_action(tool_name, button_label=None, page_text=None)`
reads the first verb of the tool name (the last dotted segment:
`gmail.send` → `send`) and the button label. It never reads the
action content — except for the one specified exception: the word
`payment` anywhere in the action or the page is a payment signal.

Payment is recognized from any of these signals:

- the word `payment` in the tool name, the button label, or the page
  text;
- a tool name built on `transfer` / `payout` / `order` / `payroll` /
  `plan upgrade` / `subscription` (whole-word match, so `recorder`
  does not match `order`), or a name starting with `pay`;
- a button label: Transfer / Withdraw / Pay / Buy / Place your order
  / Subscribe (case-insensitive).

`classify_gate_kind` maps a taxonomy kind to a gate kind
(`pay` → `prepare_payment`, `send_to_other`/`share`/`post` →
`send`, `delete` → `delete`). `puspa/chassis/tools.py`
`kind_for_tool` falls back to it for tool names outside the gate's
own table, so a `transfer` tool reaches the gate as a payment at
the real call site.

## Credential changes can never be approved

`change_credentials` has no gate kind on purpose: it never reaches
the gate. `ApprovalPolicy.request` raises `GateDenied` for it, and
`decide` returns `escalate_to_human` for every answer — including
an "always" answer and an admin rule. A credential change is handed
to a human, fail-closed.

`install` also has no gate kind on purpose: installing a skill is
a local enablement, never an outbound action, so it never reaches
the gate either. `ApprovalPolicy.request` raises `GateDenied` for
it; the install flow asks through `decide` + the store instead
(see `docs/skill-discovery.md`): the answer is recorded with
content `{"skill_slug": ..., "sha256": ...}`, the evidence is
built fresh from the store per install, and a "once" answer is
spent after a successful install.

## Anti-double-approval

When the gate is asking, the bot is told through the
`ASKING_GUIDANCE` marker (`mark_asking_guidance` /
`is_asking_guidance`), so it does not place its own approval card
on top of the gate's question. `GuidanceState` tracks whether the
gate is asking; `should_place_approval_card` is False while it is.
Destructive tools are marked in their description
(`mark_destructive` / `is_destructive`, marker
`[destructive: requires approval]`).

## Wiring to the gate

`ApprovalPolicy.request(user_id, agent_id, token, tool_name,
content, ...)` is the seam:

- `escalate_to_human` / `deny` → `GateDenied`; nothing reaches the
  gate.
- `allow` (a usable tiered answer) → the request is filed with the
  gate and approved at once from the stored answer; a `once` answer
  is consumed.
- `ask` → the request is filed and left pending; the caller presents
  the approval card.

The gate still binds the approval to the content hash and still
enforces its 24-hour token expiry; this layer only decides
*whether* to ask. The tests prove both still hold: a tampered
content hash voids the approval, and an expired token is refused.

## Server-rendered approval cards (server-rendered approval card and narrowed always-approve, plan 6.2)

`puspa/policy/approval_cards.py` renders what the user sees, from
the structured tool arguments that will actually run — and nothing
else. Every visible line is one argument field (who, how much,
where to); the card carries the content hash of exactly those
arguments, computed with the same canonical JSON the gate uses, so
the hash on the card is the hash the gate enforces.
`verify_card` re-renders from fresh arguments and checks both the
hash and the lines, so a card whose text was edited after
rendering fails even if its hash was copied along.

A model narrative may be passed to the render call, but it is
never rendered — only flagged as ignored. The card carries no
trace of its wording. Missing required fields render as "Not
provided" and mark the card incomplete (never invented); fields
that look like secrets are withheld. Credential changes never get
a card: they are never approvable.

Seams: `ApprovalPolicy.render_card(tool_name, content, ...)`
classifies the action and renders; `request_with_card(...)`
returns `(gate_request, card)` for the same content the gate
binds to. `DraftingService.request_send_approval` includes the
rendered card in its `awaiting_approval` answer.

## Narrowed "always" + beta ban (server-rendered approval card and narrowed always-approve, plan 6.3)

An "always" answer used to bind only to the action kind. It can
now be narrowed when recorded:

- `recipient_pattern` / `domain_pattern` / `path_pattern` — globs
  (`*` allowed) the action's target must match;
- `max_nominal` — the highest amount the answer covers.

Narrowing is fail-closed: a set pattern with no value on that
axis never matches, and a missing nominal never passes a cap. An
answer with no narrowing fields is unconstrained, as before. When
a stored "always" answer exists but does not cover the action,
the decision is "ask" and says so.

In beta mode (`ApprovalStore(beta_mode=True)` /
`ApprovalPolicy(beta_mode=True)`), "always" is disabled entirely
for `pay` and `send_to_other`: recording one is refused, and an
answer recorded before beta mode was switched on never applies.
Other kinds are unaffected.

## Approval fatigue (server-rendered approval card and narrowed always-approve, plan 6.3)

`ApprovalFatigueTracker` (same module) measures the signal the
plan names: the share of decisions made faster than a human could
have read the card (under one second). The PWA records when a
card is first shown (tasks screen) and when the user decides
(`POST /api/persetujuan/{request_id}`); the numbers are served at
`GET /api/approvals/fatigue`. The tracker only measures — it
never blocks a decision, however fast.

## Honest limits

- **Recognition gaps.** A button without a label, a keyboard
  shortcut, or a tool whose name hides its verb (`do_it`, `run`)
  is not recognized and therefore asks every time (fail-closed to
  asking). A payment tool named without any listed signal (for
  example a tool called `settle`) is missed until the auto-review
  or the human catches it. A tool named `order` for something that
  is not money (for example ordering a list) is treated as a
  payment and asks — a false positive, fail-closed to asking.
- **Answers are in-memory.** `ApprovalStore` does not survive a
  restart, and there is no admin UI for rules yet: rules are added
  in code (`ApprovalPolicy.add_rule`). The "admin" owner is a rule
  owner string, not an admin role check — the caller must only let
  an admin add admin rules.
- **No persistence of the check-in flag.** Whether a task came from
  check-in is passed per call (`from_checkin`); nothing records
  which tasks are check-in tasks.
- **The gate is unchanged, except for `Gate.reject` (visual approval overlay).**
  `puspa/policy/gate.py` was not modified by the taxonomy; the
  taxonomy sits in front of it. visual approval overlay added one method, `reject`,
  as the complement of `approve`: a rejected request can never
  execute or be approved later. A caller that goes straight to
  the gate bypasses the taxonomy.
- **Visual overlay (visual approval overlay).** The overlay over this gate lives in
  `puspa/pwa/approval_overlay.py` (see
  `docs/approval-overlay.md`): it highlights the target element
  in the event-based live frame and records the per-action
  decision. It has no screenshot to point at — the highlight is
  a pointer into the frame, never a pixel position — and its
  store is in-memory like the gate's requests.
- **The PWA card component still accepts a `summary` field.**
  Server-rendered cards never set it — lines + hash are the
  source of truth — but an older payload carrying a narrative
  summary would still render it. Removing that field from the
  component is a frontend follow-up, not part of server-rendered approval card and narrowed always-approve.
- **Fatigue timing starts at first render.** The clock starts
  when the tasks screen first serves the card, not when the user
  actually looks at it; a card left open and decided later
  counts as read, and a decision with no recorded showing counts
  as untimed rather than unread.
