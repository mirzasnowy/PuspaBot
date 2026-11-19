-- Shared-room schema for group chat (B6).
--
-- Tables: rooms, room_members, room_invites, room_memories,
-- room_tasks, room_money_approvals, room_channel_links,
-- delegated_tasks, delegated_task_steps, room_splits,
-- split_shares, split_payments, split_settlements.
-- delegated_tasks, delegated_task_steps, room_research_links.
--
-- B9 adds the last two: a delegation row per room task handed
-- to the agent or to a teammate (puspa/rooms/delegation.py), and
-- one row per proposed step of a delegated task. A step marked
-- irreversible may only run after approval through the G4/G5
-- approval path; the step row is what records that decision, so
-- the gate is never bypassed by a caller that forgets to ask.
--
-- B11 adds the last four: a split row per shared bill
-- (puspa/rooms/splitting.py), one share row per participant
-- (integer sen, never floats; ``position`` is the creation
-- order, which decides who gets leftover sen on an equal
-- split), one append-only row per recorded (partial) payment,
-- and one row per prepared settlement. A settlement is
-- prepare-only (B8 phase 1 seam): status ``prepared`` means
-- the recipient + amount + evidence are verified and recorded;
-- nothing ever executes from this table, and ``superseded``
-- marks a preparation replaced by a newer one for the same
-- debtor. There is no balance, no wallet, and no executed
-- status anywhere in this schema.
--
-- Run AFTER infra/schema_core.sql (the users table must exist
-- first), as a Postgres superuser, from the build/ directory:
--
--     psql "dbname=puspa_test user=root host=/var/run/postgresql" \
--          -f infra/schema_rooms.sql
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS, so
-- re-running it is safe. The Python entry point is
-- puspa.rooms.create_schema(), which executes this file (the test
-- suite calls it; conftest does NOT apply it).
--
-- Design (B6, beta-lite):
--   - A room has 2-5 people (MAX_ROOM_SIZE = 5, enforced in
--     puspa/rooms/rooms.py, not by a CHECK: the cap is a product
--     rule for the beta, not a permanent schema fact).
--   - Roles: owner / member / guest. The creator is the first owner.
--     A room always has at least one owner (enforced in Python).
--   - Invites are hash-only (see puspa/rooms/invites.py): a link
--     token or a one-time code; only the SHA-256 hash is stored.
--   - Group memory is separate from personal memory: room_memories
--     holds only what members share with the room. A row with
--     visibility = 'private' is a private answer: only its author
--     may read it, and it is deleted when the author leaves the
--     room (see puspa/rooms/memory.py). Personal memory stays in
--     puspa/memory and is never copied into a room.
--   - Money actions need two people or the owner (see
--     puspa/rooms/money.py). Approvals are recorded here; there
--     are no shared funds in the beta (no balance, no wallet).
--   - A channel group chat (Telegram group, WhatsApp group) is
--     linked to a room through room_channel_links; the link is
--     what routes a group message to the room (see
--     puspa/rooms/routing.py). WhatsApp groups are a postponed
--     surface (plan section 12.4): the link table is
--     transport-agnostic, but no live WhatsApp-group delivery is
--     verified in this iteration.
--
-- ROW LEVEL SECURITY (RLS) — same rules as infra/schema_teams.sql:
-- RLS is enabled WITHOUT "FORCE ROW LEVEL SECURITY". The table
-- owner (the role that runs this file, root in dev/tests) bypasses
-- RLS and performs the privileged operations that cannot run
-- through it: room creation (the first owner membership row
-- cannot pass the owner-only INSERT policy yet) and invitation
-- acceptance (the invitee has no membership row yet). Everything
-- else runs as the application role `puspa_app` under the policies
-- below. The application sets the current user per transaction
-- with SET LOCAL app.current_user_id (see puspa/accounts/rls.py).
--
-- Policies, in short:
--   - rooms: visible / updated / deleted only by a member;
--     only an owner may update or delete. INSERT has no policy
--     for puspa_app: rooms are created as the table owner.
--   - room_members: a member sees their own row; an owner sees
--     all rows of their room. INSERT/UPDATE only by an owner;
--     DELETE by an owner, or by the member themselves (leaving).
--   - room_invites: only a room owner or member may see them;
--     only an owner or member may create them; acceptance runs
--     as the table owner.
--   - room_memories: visible to room members, except private rows,
--     which only their author sees. INSERT only by a member, for
--     themselves. DELETE by the author, or by an owner.
--   - room_tasks: visible / created / updated only by a member of
--     the room.
--   - room_money_approvals: visible only to members of the room;
--     INSERT only by a member, for themselves.
--   - room_channel_links: visible / created / deleted only by a
--     member of the room.
--   - delegated_tasks: visible / created / updated only by a
--     member of the room (the room task itself is visible to
--     members under the room_tasks policy; the delegation row
--     follows the same rule).
--   - delegated_task_steps: visible / created / updated only by
--     a member of the room the task belongs to.
--   - room_splits / split_shares / split_payments /
--     split_settlements: visible / created / updated only by a
--     member of the room the split belongs to (the payer's
--     identity and every debtor's balance are group-visible;
--     a guest may read them, like the rest of the room).
--   - room_research_links: visible / created / deleted only by a
--     member of the room (B12).
--
-- No password anywhere in this file: auth is peer auth (local
-- socket, OS user = Postgres role). Do not add a password here.

CREATE TABLE IF NOT EXISTS rooms (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS room_members (
    room_id   UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role      TEXT NOT NULL CHECK (role IN ('owner', 'member', 'guest')),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_room_members_user_id ON room_members(user_id);

CREATE TABLE IF NOT EXISTS room_invites (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id         UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    inviter_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('link', 'code')),
    role            TEXT NOT NULL CHECK (role IN ('member', 'guest')),
    token_hash      TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_room_invites_room_id ON room_invites(room_id);

CREATE TABLE IF NOT EXISTS room_memories (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id        UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    author_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('note', 'decision', 'summary')),
    content        TEXT NOT NULL,
    visibility     TEXT NOT NULL DEFAULT 'room'
                   CHECK (visibility IN ('room', 'private')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_room_memories_room_id ON room_memories(room_id);

CREATE TABLE IF NOT EXISTS room_tasks (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id       UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    due_at        TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    created_by    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_room_tasks_room_id ON room_tasks(room_id);
CREATE INDEX IF NOT EXISTS idx_room_tasks_owner ON room_tasks(owner_user_id);

CREATE TABLE IF NOT EXISTS room_money_approvals (
    action_id       TEXT NOT NULL,
    room_id         UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    approver_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (action_id, room_id, approver_user_id)
);
CREATE INDEX IF NOT EXISTS idx_room_money_approvals_room ON room_money_approvals(room_id);

CREATE TABLE IF NOT EXISTS room_channel_links (
    platform   TEXT NOT NULL CHECK (platform IN ('telegram', 'whatsapp')),
    chat_id    TEXT NOT NULL,
    room_id    UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    linked_by  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (platform, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_room_channel_links_room ON room_channel_links(room_id);

-- Delegated tasks (B9): one row per room task handed to the agent
-- or to a teammate. The room task itself (room_tasks) keeps its
-- owner, deadline, and open/done status; this row adds the
-- delegation: who works on it (an agent subagent id, or a
-- teammate user id), and the delegation status. A task that was
-- never delegated has no row here.
CREATE TABLE IF NOT EXISTS delegated_tasks (
    task_id        UUID PRIMARY KEY REFERENCES room_tasks(id) ON DELETE CASCADE,
    room_id        UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    delegate_kind  TEXT NOT NULL CHECK (delegate_kind IN ('agent', 'teammate')),
    assignee_ref   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'delegated'
                   CHECK (status IN ('delegated', 'in_progress', 'awaiting_approval', 'blocked', 'done')),
    last_report    TEXT,
    reported_at    TIMESTAMPTZ,
    started_at     TIMESTAMPTZ,
    blocked_reason TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_delegated_tasks_room_id ON delegated_tasks(room_id);
CREATE INDEX IF NOT EXISTS idx_delegated_tasks_status ON delegated_tasks(status);

-- Proposed steps of a delegated task (B9). A step is one unit of
-- work the agent wants to do; ``irreversible`` marks steps that
-- must wait for approval (delete, send, share, post, pay, or any
-- step the worker flags irreversible). ``content`` is the exact
-- action payload the approval binds to (same content-hash idea
-- as the G4/G5 gate). ``result`` holds the executor's return
-- value once, so re-executing an executed step returns the
-- stored result instead of running twice.
CREATE TABLE IF NOT EXISTS delegated_task_steps (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id             UUID NOT NULL REFERENCES room_tasks(id) ON DELETE CASCADE,
    description         TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    irreversible        BOOLEAN NOT NULL DEFAULT FALSE,
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'awaiting_approval', 'approved', 'rejected', 'executed')),
    content             JSONB NOT NULL DEFAULT '{}'::jsonb,
    approval_request_id TEXT,
    approved_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at          TIMESTAMPTZ,
    executed_at         TIMESTAMPTZ,
    result              TEXT,
    created_by          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_delegated_task_steps_task_id ON delegated_task_steps(task_id);
CREATE INDEX IF NOT EXISTS idx_delegated_task_steps_status ON delegated_task_steps(status);

-- Shared splits (B11): one row per shared bill or purchase.
-- Money is integer sen (1 rupiah = 100 sen); the CHECKs keep
-- it that way at the database layer too. ``status`` is
-- maintained by puspa/rooms/splitting.py: open -> partially_paid
-- -> settled (with settled_at) once every share is fully paid.
CREATE TABLE IF NOT EXISTS room_splits (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id        UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('meal', 'gift', 'arisan', 'other')),
    total_cents    BIGINT NOT NULL CHECK (total_cents > 0),
    payer_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_by     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    due_at         TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'partially_paid', 'settled')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    settled_at     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_room_splits_room_id ON room_splits(room_id);

-- One share per participant. ``position`` is the creation order
-- (it decides who gets leftover sen on an equal split).
-- ``paid_cents`` never exceeds ``share_cents``: overpaying is
-- refused in Python and impossible here too.
CREATE TABLE IF NOT EXISTS split_shares (
    split_id     UUID NOT NULL REFERENCES room_splits(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    share_cents  BIGINT NOT NULL CHECK (share_cents > 0),
    paid_cents   BIGINT NOT NULL DEFAULT 0 CHECK (paid_cents >= 0),
    position     INT NOT NULL DEFAULT 0,
    CHECK (paid_cents <= share_cents),
    PRIMARY KEY (split_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_split_shares_split_id ON split_shares(split_id);

-- Recorded (partial) payments: append-only, one row per payment.
-- Nothing here moves money; it records that the debtor paid the
-- payer (in their own bank/e-wallet app, or in cash) and how much.
CREATE TABLE IF NOT EXISTS split_payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    split_id        UUID NOT NULL REFERENCES room_splits(id) ON DELETE CASCADE,
    debtor_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0),
    recorded_by     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_split_payments_split_id ON split_payments(split_id);

-- Prepared settlements (B8 phase-1 seam): a prepared payment the
-- user pays in their own bank/e-wallet app. ``status`` is only
-- ever ``prepared`` or ``superseded`` — there is no executed
-- status, because nothing here executes a payment.
CREATE TABLE IF NOT EXISTS split_settlements (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    split_id          UUID NOT NULL REFERENCES room_splits(id) ON DELETE CASCADE,
    debtor_user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount_cents      BIGINT NOT NULL CHECK (amount_cents > 0),
    action_id         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'prepared'
                      CHECK (status IN ('prepared', 'superseded')),
    prepared_by       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    prepared_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_split_settlements_split_id ON split_settlements(split_id);
-- Collected product links (B12): one row per product link a room
-- member collected for the group comparison. A link is collected
-- once per room (UNIQUE room_id + url); the label is the name the
-- member gave the option. `source` is the marketplace the URL
-- belongs to (Tokopedia / Shopee / Blibli) or "other" for an
-- allowlisted host outside the three marketplaces.
CREATE TABLE IF NOT EXISTS room_research_links (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id    UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    url        TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL,
    added_by   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (room_id, url)
);
CREATE INDEX IF NOT EXISTS idx_room_research_links_room_id ON room_research_links(room_id);

-- Application-role grants (same shape as infra/schema_teams.sql):
-- puspa_app may read/write all seven tables; isolation between
-- users is enforced by the RLS policies below, not by revoking
-- grants. Room creation / invitation acceptance INSERTs are still
-- denied to puspa_app by the absence of an INSERT policy (see
-- above).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON rooms TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON room_members TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON room_invites TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON room_memories TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON room_tasks TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON room_money_approvals TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON room_channel_links TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON delegated_tasks TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON delegated_task_steps TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON room_splits TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON split_shares TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON split_payments TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON split_settlements TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON room_research_links TO puspa_app;
    END IF;
END $$;

-- SECURITY DEFINER helpers: a policy must never subquery the table
-- it guards (Postgres reports infinite recursion in the policy),
-- so the owner/member checks live in these helpers (same pattern
-- as infra/schema_teams.sql).
CREATE OR REPLACE FUNCTION is_room_owner(p_room_id UUID, p_user_id UUID)
RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM room_members
        WHERE room_id = p_room_id AND user_id = p_user_id AND role = 'owner'
    );
$$;

CREATE OR REPLACE FUNCTION is_room_member(p_room_id UUID, p_user_id UUID)
RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM room_members
        WHERE room_id = p_room_id AND user_id = p_user_id
    );
$$;

ALTER TABLE rooms ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_invites ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_money_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_channel_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE delegated_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE delegated_task_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_splits ENABLE ROW LEVEL SECURITY;
ALTER TABLE split_shares ENABLE ROW LEVEL SECURITY;
ALTER TABLE split_payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE split_settlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE room_research_links ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rooms_select ON rooms;
CREATE POLICY rooms_select ON rooms FOR SELECT TO puspa_app
    USING (is_room_member(id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS rooms_update ON rooms;
CREATE POLICY rooms_update ON rooms FOR UPDATE TO puspa_app
    USING (is_room_owner(id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS rooms_delete ON rooms;
CREATE POLICY rooms_delete ON rooms FOR DELETE TO puspa_app
    USING (is_room_owner(id, current_setting('app.current_user_id', TRUE)::UUID));

DROP POLICY IF EXISTS room_members_select ON room_members;
CREATE POLICY room_members_select ON room_members FOR SELECT TO puspa_app
    USING (
        user_id = current_setting('app.current_user_id', TRUE)::UUID
        OR is_room_owner(room_id, current_setting('app.current_user_id', TRUE)::UUID)
    );
DROP POLICY IF EXISTS room_members_insert ON room_members;
CREATE POLICY room_members_insert ON room_members FOR INSERT TO puspa_app
    WITH CHECK (is_room_owner(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_members_update ON room_members;
CREATE POLICY room_members_update ON room_members FOR UPDATE TO puspa_app
    USING (is_room_owner(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_members_delete ON room_members;
CREATE POLICY room_members_delete ON room_members FOR DELETE TO puspa_app
    USING (
        user_id = current_setting('app.current_user_id', TRUE)::UUID
        OR is_room_owner(room_id, current_setting('app.current_user_id', TRUE)::UUID)
    );

DROP POLICY IF EXISTS room_invites_select ON room_invites;
CREATE POLICY room_invites_select ON room_invites FOR SELECT TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_invites_insert ON room_invites;
CREATE POLICY room_invites_insert ON room_invites FOR INSERT TO puspa_app
    WITH CHECK (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_invites_update ON room_invites;
CREATE POLICY room_invites_update ON room_invites FOR UPDATE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_invites_delete ON room_invites;
CREATE POLICY room_invites_delete ON room_invites FOR DELETE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));

DROP POLICY IF EXISTS room_memories_select ON room_memories;
CREATE POLICY room_memories_select ON room_memories FOR SELECT TO puspa_app
    USING (
        is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID)
        AND (
            visibility = 'room'
            OR author_user_id = current_setting('app.current_user_id', TRUE)::UUID
        )
    );
DROP POLICY IF EXISTS room_memories_insert ON room_memories;
CREATE POLICY room_memories_insert ON room_memories FOR INSERT TO puspa_app
    WITH CHECK (
        is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID)
        AND author_user_id = current_setting('app.current_user_id', TRUE)::UUID
    );
DROP POLICY IF EXISTS room_memories_delete ON room_memories;
CREATE POLICY room_memories_delete ON room_memories FOR DELETE TO puspa_app
    USING (
        author_user_id = current_setting('app.current_user_id', TRUE)::UUID
        OR is_room_owner(room_id, current_setting('app.current_user_id', TRUE)::UUID)
    );

DROP POLICY IF EXISTS room_tasks_select ON room_tasks;
CREATE POLICY room_tasks_select ON room_tasks FOR SELECT TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_tasks_insert ON room_tasks;
CREATE POLICY room_tasks_insert ON room_tasks FOR INSERT TO puspa_app
    WITH CHECK (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_tasks_update ON room_tasks;
CREATE POLICY room_tasks_update ON room_tasks FOR UPDATE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_tasks_delete ON room_tasks;
CREATE POLICY room_tasks_delete ON room_tasks FOR DELETE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));

DROP POLICY IF EXISTS room_money_approvals_select ON room_money_approvals;
CREATE POLICY room_money_approvals_select ON room_money_approvals FOR SELECT TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_money_approvals_insert ON room_money_approvals;
CREATE POLICY room_money_approvals_insert ON room_money_approvals FOR INSERT TO puspa_app
    WITH CHECK (
        is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID)
        AND approver_user_id = current_setting('app.current_user_id', TRUE)::UUID
    );

DROP POLICY IF EXISTS room_channel_links_select ON room_channel_links;
CREATE POLICY room_channel_links_select ON room_channel_links FOR SELECT TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_channel_links_insert ON room_channel_links;
CREATE POLICY room_channel_links_insert ON room_channel_links FOR INSERT TO puspa_app
    WITH CHECK (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_channel_links_delete ON room_channel_links;
CREATE POLICY room_channel_links_delete ON room_channel_links FOR DELETE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));

DROP POLICY IF EXISTS delegated_tasks_select ON delegated_tasks;
CREATE POLICY delegated_tasks_select ON delegated_tasks FOR SELECT TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS delegated_tasks_insert ON delegated_tasks;
CREATE POLICY delegated_tasks_insert ON delegated_tasks FOR INSERT TO puspa_app
    WITH CHECK (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS delegated_tasks_update ON delegated_tasks;
CREATE POLICY delegated_tasks_update ON delegated_tasks FOR UPDATE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS delegated_tasks_delete ON delegated_tasks;
CREATE POLICY delegated_tasks_delete ON delegated_tasks FOR DELETE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));

DROP POLICY IF EXISTS delegated_task_steps_select ON delegated_task_steps;
CREATE POLICY delegated_task_steps_select ON delegated_task_steps FOR SELECT TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_tasks t
            WHERE t.id = delegated_task_steps.task_id
            AND is_room_member(t.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS delegated_task_steps_insert ON delegated_task_steps;
CREATE POLICY delegated_task_steps_insert ON delegated_task_steps FOR INSERT TO puspa_app
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM room_tasks t
            WHERE t.id = delegated_task_steps.task_id
            AND is_room_member(t.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS delegated_task_steps_update ON delegated_task_steps;
CREATE POLICY delegated_task_steps_update ON delegated_task_steps FOR UPDATE TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_tasks t
            WHERE t.id = delegated_task_steps.task_id
            AND is_room_member(t.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS delegated_task_steps_delete ON delegated_task_steps;
CREATE POLICY delegated_task_steps_delete ON delegated_task_steps FOR DELETE TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_tasks t
            WHERE t.id = delegated_task_steps.task_id
            AND is_room_member(t.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );

-- Shared splits (B11): visible / created / updated only by a
-- member of the room the split belongs to. The share and
-- payment rows follow their split; the settlement rows follow
-- theirs.
DROP POLICY IF EXISTS room_splits_select ON room_splits;
CREATE POLICY room_splits_select ON room_splits FOR SELECT TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_splits_insert ON room_splits;
CREATE POLICY room_splits_insert ON room_splits FOR INSERT TO puspa_app
    WITH CHECK (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_splits_update ON room_splits;
CREATE POLICY room_splits_update ON room_splits FOR UPDATE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_splits_delete ON room_splits;
CREATE POLICY room_splits_delete ON room_splits FOR DELETE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));

DROP POLICY IF EXISTS split_shares_select ON split_shares;
CREATE POLICY split_shares_select ON split_shares FOR SELECT TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_shares.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS split_shares_insert ON split_shares;
CREATE POLICY split_shares_insert ON split_shares FOR INSERT TO puspa_app
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_shares.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS split_shares_update ON split_shares;
CREATE POLICY split_shares_update ON split_shares FOR UPDATE TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_shares.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS split_shares_delete ON split_shares;
CREATE POLICY split_shares_delete ON split_shares FOR DELETE TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_shares.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );

DROP POLICY IF EXISTS split_payments_select ON split_payments;
CREATE POLICY split_payments_select ON split_payments FOR SELECT TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_payments.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS split_payments_insert ON split_payments;
CREATE POLICY split_payments_insert ON split_payments FOR INSERT TO puspa_app
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_payments.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );

DROP POLICY IF EXISTS split_settlements_select ON split_settlements;
CREATE POLICY split_settlements_select ON split_settlements FOR SELECT TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_settlements.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS split_settlements_insert ON split_settlements;
CREATE POLICY split_settlements_insert ON split_settlements FOR INSERT TO puspa_app
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_settlements.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS split_settlements_update ON split_settlements;
CREATE POLICY split_settlements_update ON split_settlements FOR UPDATE TO puspa_app
    USING (
        EXISTS (
            SELECT 1 FROM room_splits s
            WHERE s.id = split_settlements.split_id
            AND is_room_member(s.room_id, current_setting('app.current_user_id', TRUE)::UUID)
        )
    );
DROP POLICY IF EXISTS room_research_links_select ON room_research_links;
CREATE POLICY room_research_links_select ON room_research_links FOR SELECT TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_research_links_insert ON room_research_links;
CREATE POLICY room_research_links_insert ON room_research_links FOR INSERT TO puspa_app
    WITH CHECK (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS room_research_links_delete ON room_research_links;
CREATE POLICY room_research_links_delete ON room_research_links FOR DELETE TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
