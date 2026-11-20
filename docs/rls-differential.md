# RLS differential tests (E-13, plan sections 6.9 and 12)

## Why two layers need one test each

Every read in Puspa is guarded by two layers that can disagree:

- **The code layer.** Python checks in the service modules
  (`viewer_user_id` membership checks in `puspa/rooms/*`,
  per-object ownership checks in `DraftingService`,
  `PreparationService`, `MockupStore`) plus the HTTP handlers
  binding the session user id (`need_user` in `puspa/pwa/*`).
- **The RLS layer.** Postgres row-level security policies on the
  tables, keyed on the session variable `app.current_user_id`,
  enforced only on connections running as the `puspa_app` role.

Two disagreements are possible, and both are silent in normal
operation:

1. The code layer is too loose but RLS saves it. Nobody notices,
   so the looseness is never fixed — until the day a query runs
   on a connection where RLS does not apply.
2. RLS is too strict, so a legitimate read fails silently. The
   feature looks broken for no visible reason, or — worse — the
   code layer keeps working (it runs as the table owner) while
   the database layer would refuse the same read.

The differential suite (`tests/test_rls_differential.py`) runs
the same read through each layer on its own, with the other
layer disabled, and requires both to agree:

| Fixture | Code layer | RLS layer | What it proves |
| --- | --- | --- | --- |
| Service call (owner connection) | on | off (owner bypasses RLS) | The Python check rejects on its own |
| Raw SQL as `puspa_app` + `app.current_user_id` | off (no Python check) | on | The policy filters on its own |
| HTTP request through `create_app`, logged in as A | on (session binding) | off (services run as owner) | The endpoint rejects cross-user ids |

A canary user owns one row in every covered table, each
carrying the marker `rls-canary-e13`. No response or row set
returned to another user may contain that marker anywhere; the
marker is the tripwire for a leak through either layer.

## Coverage

Per-user tables (isolation by `user_id`): `users`,
`sessions`, `vault`, `profile_facts`, `episodic`, `drafts`,
`prepared_payments`, `mockups`.

Shared-room tables (isolation by membership, plan section 12):
`rooms`, `room_members`, `room_invites`, `room_memories`
(including the private-visibility rule: a fellow member never
sees another member's private memory), `room_tasks`,
`room_money_approvals`, `room_channel_links`,
`delegated_tasks`, `delegated_task_steps`, `room_splits`,
`split_shares`, `split_payments`, `split_settlements`,
`room_research_links`, `meeting_minutes`.

Also covered: removal is immediate — after a member leaves,
both layers deny them at once (code layer raises
`NotRoomMemberError`, RLS returns zero rows, HTTP returns 403);
and the fail-closed case — with no user set, the app role sees
nothing in any covered table.

## Findings

### 1. Mockup RLS was broken in two ways — fixed by migration 0021

The differential run caught a real disagreement before this
ticket landed it as a regression test
(`test_rls_owner_sees_own_mockup`):

- Migration 0015 created the `mockups_owner` policy against
  `app.user_id`, a session variable nothing ever sets (the app
  sets `app.current_user_id` via `puspa/accounts/rls.py`). As
  `puspa_app`, the policy compared `user_id` against an unset
  variable, so every row was invisible — including the owner's
  own mockups.
- Migration 0015 also granted nothing to `puspa_app`, so even a
  correct policy could not have run: reads failed outright with
  `permission denied`.

The code layer masked both defects: `MockupStore` connects as
the table owner and filters by `user_id` in SQL, so mockups
worked in every existing test while the database layer would
have refused the same read. This is exactly failure mode 2 of
plan 6.9.

Migration `0021_mockup_rls_grant.sql` repairs only the
database layer: it grants `puspa_app` the same privileges the
other PWA tables have and points the policy at
`app.current_user_id`. It changes no application code. The
stale one-line docstring in `puspa/mockup/store.py` was updated
to match. Migration 0015 itself was not edited (never edit an
applied migration); its header comment still describes the old
policy, which is now historical.

### 2. Per-user stores trust the `user_id` they are handed

`test_code_layer_trusts_caller_supplied_user_id` pins this
deliberately: called with B's id, `vault.list_names`,
`profile.list_facts`, `MockupStore.list`, and
`RoomStore.list_rooms_for_user` return B's data. These stores
have no viewer parameter and perform no rejection; their only
protection is the caller passing the right id.

That means for per-user reads the code layer is not an
independent rejection — it is caller discipline. The two real
protections are the HTTP handler binding the session user id
and the RLS layer, which is the only layer that rejects a
caller that passes the wrong id. If a store ever gains a viewer
check, that test must change with it. (Room readers, drafts,
payments, and mockup single-object reads do have viewer checks
and reject independently — see the `test_code_*` tests.)

### 3. Thirty-seven RLS-enabled tables have no grant to `puspa_app`

After migration 0021, 37 tables still have RLS enabled but no
`SELECT` grant to the application role, so their policies are
dead letters: as `puspa_app` every read fails with `permission
denied`, and the policies can never be exercised — or falsified
— through that role. The list (20 Nov 2026, `puspa_test_w1`):
`agent_connectors`, `agent_goal_models`, `agent_meta`,
`belief_versions`, `block_requests`, `canary_runs`,
`canonical_products`, `cassettes`, `causal_hypotheses`,
`conflict_records`, `evidence`, `evidence_cache`,
`false_reports`, `handoffs`, `listing_links`,
`loop_corrections`, `loop_experiences`, `module_rows`,
`module_tables`, `modules`, `monthly_credits`, `price_series`,
`procedure_uses`, `procedure_versions`, `push_subscriptions`,
`read_budgets`, `shared_graph_private_records`,
`source_readings`, `tasks`, `token_metrics`, `triggers`,
`watchlist`, `workflow_approvals`, `workflow_library`,
`workflow_runs`, `workflows`, `world_beliefs`.

Nothing breaks today, because the services for those tables run
as the table owner. But a policy on a table the app role cannot
read is untested by definition, and the next person to wire one
of these tables to `puspa_app` will meet either a silent empty
result or a permission error. Each of these tables needs a
decision — grant the role and cover it with a differential case,
or drop the policy and say so — before its first `puspa_app`
read path lands. That decision is out of scope for E-13; this
note is the record that it is pending.

### 4. The two layers are never composed on one connection

Today no production path runs both layers at once: services
connect as the table owner (code layer only), and RLS only
bites on connections that run as `puspa_app` with
`app.current_user_id` set. The differential suite therefore
proves each layer correct in isolation; it does not prove a
composed path, because none exists yet. Running read services
as `puspa_app` with `rls.as_user` — so a wrong-id call would
have to defeat both layers — is a follow-up, not part of E-13.

## Review of the no-FORCE-RLS decision (plan 6.9)

Decision 15 (`docs/status-phase-0.md`) stands: RLS is enabled
**without** `FORCE ROW LEVEL SECURITY`. The table owner and
superuser bypass RLS; real enforcement applies only to
connections as `puspa_app`. This review re-examined it against
the current code and keeps it, for these reasons:

**What runs as the table owner, and why it must keep bypassing
RLS:**

- Registration and session validation (`create_user`,
  `authenticate`, `validate_session`): before a session is
  validated, no user id is known — the chicken-and-egg that
  forces an owner connection. The users INSERT policy already
  encodes the same exception (an empty `app.current_user_id`
  may insert a user row).
- Room creation (`RoomStore.create_room`) and team creation:
  there is no INSERT policy for `rooms`, and the
  `room_members` INSERT policy is owner-only, so creation
  cannot run as the application role.
- The migration runner, backup/restore drills, and test
  fixtures: they create tables, truncate, and seed across users
  by design.

**What FORCE would break:** enabling `FORCE ROW LEVEL SECURITY`
would make every one of those paths fail unless each gained a
dedicated privileged role or `SECURITY DEFINER` wrapper — a
larger change with its own review surface, and it would not add
protection where the owner paths are already the only callers
that may legitimately cross users. The risk FORCE would address
is real but narrower than it looks: an owner connection only
leaks when code running on it serves a user-supplied id without
a viewer check (finding 2 above). The mitigation in place is
the differential suite plus the rule that an owner connection
must never serve a user-supplied id without a viewer check or
an RLS-enforced connection underneath it.

**When to revisit:** if read services move to `puspa_app` +
`rls.as_user` as their default connection (finding 4), FORCE
becomes cheap to adopt for the tables whose owner-only paths
have been moved behind `SECURITY DEFINER` helpers, and the
decision should be re-reviewed then. Until that migration
happens, keep no-FORCE and keep the differential tests green.

**Related note:** the room policies depend on the
`SECURITY DEFINER` helpers `is_room_member` /
`is_room_owner` (migration 0010). Those functions bypass RLS by
design, so a bug in either helper would silently widen every
room policy at once. They are two small `EXISTS` queries over
`room_members`; any change to them must re-run this suite.

## Extending the suite

- **A new per-user table:** seed one row per user in the
  `seeded` fixture (plus a canary row carrying the marker),
  add `(table, user_column)` to `PER_USER_TABLES`, and add a
  list-endpoint case to `test_http_list_endpoints_never_leak_canary`
  if the table has one.
- **A new room table:** seed one row in the canary room, add
  `(table, member_visible_count)` to
  `ROOM_TABLES_MEMBER_VISIBLE`, and add a viewer-checked reader
  case to `test_code_room_readers_reject_outsider` if the table
  has one.
- **A new read endpoint:** add its cross-user id to
  `test_http_cross_user_reads_are_rejected` and its list path to
  `test_http_list_endpoints_never_leak_canary`.
- **A policy change:** change the policy in a new numbered
  migration, regenerate `infra/schema_snapshot.sql` per its
  header, and let this suite arbitrate whether the code and RLS
  layers still agree. If they disagree, the suite fails on the
  side that is wrong — that is the point.
