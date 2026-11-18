-- Team schema for multi-tenant hosting (G13).
--
-- Tables: teams, team_members, invitations, bot_access_grants,
-- bot_access_requests, entitlements, workspace_moves.
--
-- Run AFTER infra/schema_core.sql (the users table must exist first),
-- as a Postgres superuser, from the build/ directory:
--
--     psql "dbname=puspa_test user=root host=/var/run/postgresql" \
--          -f infra/schema_teams.sql
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS, so
-- re-running it is safe. The Python entry point is
-- puspa.teams.create_schema(), which executes this file (the test
-- suite calls it; conftest does NOT apply it).
--
-- ROW LEVEL SECURITY (RLS) — same rules as puspa/accounts/schema.sql:
-- RLS is enabled WITHOUT "FORCE ROW LEVEL SECURITY". The table owner
-- (the role that runs this file, root in dev/tests) bypasses RLS and
-- performs the privileged operations that cannot run through it:
-- team creation (the first owner membership row cannot pass the
-- owner-only INSERT policy yet, because no membership exists),
-- invitation acceptance (the invitee has no membership row yet, so
-- the membership INSERT cannot pass the policy), and entitlement
-- issuance (the server holds the signing key). Everything else runs
-- as the application role `puspa_app` under the policies below.
-- The application sets the current user per transaction with
-- SET LOCAL app.current_user_id (see puspa/accounts/rls.py).
--
-- Policies, in short:
--   - teams: visible / updated / deleted only by a member of the
--     team; only an owner may update or delete. INSERT has no
--     policy for puspa_app: teams are created as the table owner.
--   - team_members: a member sees their own row; an owner sees all
--     rows of their team. INSERT/UPDATE only by an owner; DELETE
--     by an owner, or by the member themselves (leaving a team).
--     The owner/member checks are the SECURITY DEFINER helpers
--     is_team_owner / is_team_member below — a policy must never
--     subquery the table it guards (Postgres reports infinite
--     recursion in the policy).
--   - invitations: only a team owner may see, create, change, or
--     delete them. Acceptance runs as the table owner.
--   - bot_access_grants: visible to the grantee and to team owners;
--     created/deleted only by a team owner.
--   - bot_access_requests: visible to the requester and to team
--     owners; created only by a team member for themselves;
--     decided (approved/denied) only by a team owner.
--   - entitlements: visible and revocable only by a team owner.
--     Issuance runs as the table owner (it holds the signing key).
--   - workspace_moves: visible / created / updated only by a member
--     of the team the move is scoped under.
--
-- No password anywhere in this file: auth is peer auth (local
-- socket, OS user = Postgres role). Do not add a password here.

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

-- Application-role grants (same shape as puspa/accounts/schema.sql):
-- puspa_app may read/write all seven tables; isolation between
-- users is enforced by the RLS policies below, not by revoking
-- grants. Teams/invitations/entitlements INSERTs are still denied
-- to puspa_app by the absence of an INSERT policy (see above).
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

-- Membership helpers (SECURITY DEFINER): a policy may NOT subquery
-- the table it guards — Postgres applies the policy to the
-- subquery too and reports "infinite recursion detected in policy
-- for relation team_members" (that is what the first version of
-- this file did: the team_members SELECT policy subqueried
-- team_members for the owner check). These functions run as the
-- table owner (which bypasses RLS), so they read membership
-- directly. search_path is locked to pg_catalog and every table is
-- schema-qualified, so the functions cannot be hijacked through
-- search_path. The current user is passed in as a UUID; callers
-- pass NULLIF(current_setting('app.current_user_id', true), '')::uuid
-- so an unset variable becomes NULL (no membership) instead of an
-- error — fail-closed.
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

-- Row Level Security -------------------------------------------------------
-- Idempotent: re-running this file is safe (DROP POLICY IF EXISTS).

ALTER TABLE teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_access_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE bot_access_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE entitlements ENABLE ROW LEVEL SECURITY;
ALTER TABLE workspace_moves ENABLE ROW LEVEL SECURITY;

-- teams: members see the team; only owners update or delete it.
-- No INSERT policy: team creation runs as the table owner.
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

-- team_members: own row, or any row of a team you own.
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

-- invitations: team owners only.
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

-- bot_access_grants: the grantee, or a team owner.
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

-- bot_access_requests: the requester, or a team owner. A member may
-- only open a request for themselves.
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

-- entitlements: team owners only (read + revoke). Issuance runs as
-- the table owner, so there is no INSERT policy for puspa_app.
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

-- workspace_moves: members of the team the move is scoped under.
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

-- Quick verification (fails if a table did not form).
SELECT 'teams RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'teams';
SELECT 'team_members RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'team_members';
SELECT 'invitations RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'invitations';
SELECT 'bot_access_grants RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'bot_access_grants';
SELECT 'bot_access_requests RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'bot_access_requests';
SELECT 'entitlements RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'entitlements';
SELECT 'workspace_moves RLS: ' || (relrowsecurity::text) FROM pg_class WHERE relname = 'workspace_moves';
