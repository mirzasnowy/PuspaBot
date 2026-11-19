-- Meeting-minutes schema (B10).
--
-- Tables: meeting_minutes.
--
-- Run AFTER infra/schema_core.sql (the users table must exist
-- first) and after infra/schema_rooms.sql (the rooms and
-- room_tasks tables must exist first), as a Postgres superuser,
-- from the build/ directory:
--
--     psql "dbname=puspa_test user=root host=/var/run/postgresql" \
--          -f infra/schema_meetings.sql
--
-- Idempotent: CREATE TABLE IF NOT EXISTS + DROP POLICY IF EXISTS,
-- so re-running it is safe. The Python entry point is
-- puspa.meetings.create_schema(), which executes this file (the
-- test suite calls it; conftest does NOT apply it).
--
-- Design (B10, beta):
--   - One row per meeting: the rendered minutes text plus the
--     structured extraction (decisions and action items as JSONB)
--     so the minutes can be re-rendered and queried without
--     re-parsing the transcript. The transcript itself is NOT
--     stored: it may contain anything the room said, and the
--     minutes are the room's record.
--   - Action items live on the shared room task board
--     (room_tasks, B6): each extracted action item is a row
--     there, and the minutes row keeps its task id, so the
--     reminder queries in puspa/meetings/reminders.py can tell
--     meeting action items apart from other board tasks without
--     changing the room_tasks schema.
--   - Decisions are also recorded as room memory of kind
--     "decision" (puspa/rooms/memory.py) and as ledger events
--     (see puspa/meetings/store.py); the minutes row is the
--     human-readable record, not the only copy.
--
-- ROW LEVEL SECURITY (RLS) — same rules as infra/schema_rooms.sql:
-- RLS is enabled WITHOUT "FORCE ROW LEVEL SECURITY". The table
-- owner (the role that runs this file, root in dev/tests)
-- bypasses RLS. The application role `puspa_app` may read and
-- insert only as a member of the room, through the
-- is_room_member helper (re-created here with CREATE OR REPLACE
-- so this file also works when schema_rooms.sql has not run yet;
-- the definition is identical in both files).

CREATE TABLE IF NOT EXISTS meeting_minutes (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    room_id        UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    held_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    author_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    minutes_text   TEXT NOT NULL,
    decisions      JSONB NOT NULL DEFAULT '[]'::jsonb,
    action_items   JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_meeting_minutes_room_id ON meeting_minutes(room_id);

-- Application-role grants (same shape as infra/schema_rooms.sql).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT ON meeting_minutes TO puspa_app;
    END IF;
END $$;

-- SECURITY DEFINER helper (identical to the one in
-- infra/schema_rooms.sql): a policy must never subquery the table
-- it guards, so the member check lives here.
CREATE OR REPLACE FUNCTION is_room_member(p_room_id UUID, p_user_id UUID)
RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM room_members
        WHERE room_id = p_room_id AND user_id = p_user_id
    );
$$;

ALTER TABLE meeting_minutes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS meeting_minutes_select ON meeting_minutes;
CREATE POLICY meeting_minutes_select ON meeting_minutes FOR SELECT TO puspa_app
    USING (is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID));
DROP POLICY IF EXISTS meeting_minutes_insert ON meeting_minutes;
CREATE POLICY meeting_minutes_insert ON meeting_minutes FOR INSERT TO puspa_app
    WITH CHECK (
        is_room_member(room_id, current_setting('app.current_user_id', TRUE)::UUID)
        AND author_user_id = current_setting('app.current_user_id', TRUE)::UUID
    );
