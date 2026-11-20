-- Rooms: group chat, tasks, splits (E-08 migration 0010_rooms.sql).
--
-- Creates the shared-room tables (rooms, room_members, room_invites, room_memories, room_tasks, room_money_approvals, room_channel_links, delegated_tasks, delegated_task_steps, room_splits, split_shares, split_payments, split_settlements, room_research_links), the SECURITY DEFINER membership helpers, and the RLS policies.
--
-- Source: infra/schema_rooms.sql.
--
-- Reversible: yes. The down section drops the tables this migration
-- creates (and their data — roll back on a data copy first, never on
-- a live database without a backup). The puspa_app role, once created
-- by 0001, is never dropped by any down migration.
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
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

CREATE TABLE IF NOT EXISTS split_payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    split_id        UUID NOT NULL REFERENCES room_splits(id) ON DELETE CASCADE,
    debtor_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0),
    recorded_by     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_split_payments_split_id ON split_payments(split_id);

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

-- migrate:down
DROP TABLE IF EXISTS room_research_links, split_settlements, split_payments, split_shares, room_splits, delegated_task_steps, delegated_tasks, room_channel_links, room_money_approvals, room_tasks, room_memories, room_invites, room_members, rooms CASCADE;
DROP FUNCTION IF EXISTS public.is_room_owner(UUID, UUID);
DROP FUNCTION IF EXISTS public.is_room_member(UUID, UUID);
