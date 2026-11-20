-- Teams: membership, invitations, entitlements (E-08 migration 0009_teams.sql).
--
-- Creates the team tables (teams, team_members, invitations, bot_access_grants, bot_access_requests, entitlements, workspace_moves), the SECURITY DEFINER membership helpers, and the RLS policies.
--
-- Source: infra/schema_teams.sql.
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
CREATE TABLE IF NOT EXISTS teams (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id   UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role      TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_team_members_user_id ON team_members(user_id);

CREATE TABLE IF NOT EXISTS invitations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    inviter_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    invited_email   TEXT NOT NULL,
    token_hash      TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_invitations_team_id ON invitations(team_id);

CREATE TABLE IF NOT EXISTS bot_access_grants (
    bot_id     TEXT NOT NULL,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    team_id    UUID REFERENCES teams(id) ON DELETE CASCADE,
    granted_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (bot_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_bot_access_grants_team_id ON bot_access_grants(team_id);

CREATE TABLE IF NOT EXISTS bot_access_requests (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id            TEXT NOT NULL,
    team_id           UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    requester_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'approved', 'denied')),
    decided_by        UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at        TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_bot_access_requests_team_id ON bot_access_requests(team_id);
CREATE INDEX IF NOT EXISTS idx_bot_access_requests_status ON bot_access_requests(status);

CREATE TABLE IF NOT EXISTS entitlements (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    machine_id  TEXT NOT NULL,
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    tier        TEXT NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    revoked     BOOLEAN NOT NULL DEFAULT false,
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (machine_id, team_id)
);
CREATE INDEX IF NOT EXISTS idx_entitlements_team_id ON entitlements(team_id);

CREATE TABLE IF NOT EXISTS workspace_moves (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bot_id           TEXT NOT NULL,
    team_id          UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    source_workspace TEXT NOT NULL,
    target_workspace TEXT NOT NULL,
    requested_by     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'in_progress', 'done', 'failed', 'cancelled')),
    current_step     TEXT NOT NULL DEFAULT 'created',
    error            TEXT,
    state            JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workspace_moves_bot_id ON workspace_moves(bot_id);
CREATE INDEX IF NOT EXISTS idx_workspace_moves_status ON workspace_moves(status);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON teams TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON team_members TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON bot_access_grants TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON bot_access_requests TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON entitlements TO puspa_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_moves TO puspa_app;
    END IF;
END $$;

CREATE OR REPLACE FUNCTION public.is_team_member(p_team_id UUID, p_user_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.team_members m
        WHERE m.team_id = p_team_id AND m.user_id = p_user_id
    );
$$;

CREATE OR REPLACE FUNCTION public.is_team_owner(p_team_id UUID, p_user_id UUID)
RETURNS BOOLEAN
LANGUAGE sql
SECURITY DEFINER
STABLE
SET search_path = pg_catalog AS $$
    SELECT EXISTS (
        SELECT 1 FROM public.team_members m
        WHERE m.team_id = p_team_id AND m.user_id = p_user_id AND m.role = 'owner'
    );
$$;


ALTER TABLE teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_access_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_access_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE entitlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_moves ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS teams_view_member ON teams;
CREATE POLICY teams_view_member ON teams
    FOR SELECT
    USING (
        public.is_team_member(
            teams.id, NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS teams_update_owner ON teams;
CREATE POLICY teams_update_owner ON teams
    FOR UPDATE
    USING (
        public.is_team_owner(
            teams.id, NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        public.is_team_owner(
            teams.id, NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS teams_delete_owner ON teams;
CREATE POLICY teams_delete_owner ON teams
    FOR DELETE
    USING (
        public.is_team_owner(
            teams.id, NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS team_members_view_own_or_owner ON team_members;
CREATE POLICY team_members_view_own_or_owner ON team_members
    FOR SELECT
    USING (
        user_id::text = coalesce(current_setting('app.current_user_id', true), '')
        OR public.is_team_owner(
            team_members.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS team_members_insert_owner ON team_members;
CREATE POLICY team_members_insert_owner ON team_members
    FOR INSERT
    WITH CHECK (
        public.is_team_owner(
            team_members.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS team_members_update_owner ON team_members;
CREATE POLICY team_members_update_owner ON team_members
    FOR UPDATE
    USING (
        public.is_team_owner(
            team_members.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        public.is_team_owner(
            team_members.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS team_members_delete_owner_or_self ON team_members;
CREATE POLICY team_members_delete_owner_or_self ON team_members
    FOR DELETE
    USING (
        user_id::text = coalesce(current_setting('app.current_user_id', true), '')
        OR public.is_team_owner(
            team_members.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS invitations_owner ON invitations;
CREATE POLICY invitations_owner ON invitations
    FOR ALL
    USING (
        public.is_team_owner(
            invitations.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        public.is_team_owner(
            invitations.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS bot_access_grants_view ON bot_access_grants;
CREATE POLICY bot_access_grants_view ON bot_access_grants
    FOR SELECT
    USING (
        user_id::text = coalesce(current_setting('app.current_user_id', true), '')
        OR (
            team_id IS NOT NULL AND public.is_team_owner(
                bot_access_grants.team_id,
                NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
        )
    );

DROP POLICY IF EXISTS bot_access_grants_write_owner ON bot_access_grants;
CREATE POLICY bot_access_grants_write_owner ON bot_access_grants
    FOR INSERT
    WITH CHECK (
        team_id IS NOT NULL AND public.is_team_owner(
            bot_access_grants.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS bot_access_grants_delete_owner ON bot_access_grants;
CREATE POLICY bot_access_grants_delete_owner ON bot_access_grants
    FOR DELETE
    USING (
        team_id IS NOT NULL AND public.is_team_owner(
            bot_access_grants.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS bot_access_requests_view ON bot_access_requests;
CREATE POLICY bot_access_requests_view ON bot_access_requests
    FOR SELECT
    USING (
        requester_user_id::text = coalesce(current_setting('app.current_user_id', true), '')
        OR public.is_team_owner(
            bot_access_requests.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS bot_access_requests_insert_member ON bot_access_requests;
CREATE POLICY bot_access_requests_insert_member ON bot_access_requests
    FOR INSERT
    WITH CHECK (
        requester_user_id::text = coalesce(current_setting('app.current_user_id', true), '')
        AND public.is_team_member(
            bot_access_requests.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS bot_access_requests_update_owner ON bot_access_requests;
CREATE POLICY bot_access_requests_update_owner ON bot_access_requests
    FOR UPDATE
    USING (
        public.is_team_owner(
            bot_access_requests.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        public.is_team_owner(
            bot_access_requests.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS entitlements_owner ON entitlements;
CREATE POLICY entitlements_owner ON entitlements
    FOR SELECT
    USING (
        public.is_team_owner(
            entitlements.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS entitlements_update_owner ON entitlements;
CREATE POLICY entitlements_update_owner ON entitlements
    FOR UPDATE
    USING (
        public.is_team_owner(
            entitlements.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        public.is_team_owner(
            entitlements.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS entitlements_delete_owner ON entitlements;
CREATE POLICY entitlements_delete_owner ON entitlements
    FOR DELETE
    USING (
        public.is_team_owner(
            entitlements.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

DROP POLICY IF EXISTS workspace_moves_member ON workspace_moves;
CREATE POLICY workspace_moves_member ON workspace_moves
    FOR ALL
    USING (
        public.is_team_member(
            workspace_moves.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    )
    WITH CHECK (
        public.is_team_member(
            workspace_moves.team_id,
            NULLIF(current_setting('app.current_user_id', true), '')::uuid
        )
    );

-- migrate:down
DROP TABLE IF EXISTS workspace_moves, entitlements, bot_access_requests, bot_access_grants, invitations, team_members, teams CASCADE;
DROP FUNCTION IF EXISTS public.is_team_owner(UUID, UUID);
DROP FUNCTION IF EXISTS public.is_team_member(UUID, UUID);
