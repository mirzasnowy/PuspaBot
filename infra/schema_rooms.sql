-- Shared-room schema for group chat (B6).
--
-- Tables: rooms, room_members, room_invites, room_memories,
-- room_tasks, room_money_approvals, room_channel_links.
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
