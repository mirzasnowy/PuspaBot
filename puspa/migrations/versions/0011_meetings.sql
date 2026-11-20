-- Meetings: minutes (E-08 migration 0011_meetings.sql).
--
-- Creates the meeting-minutes table (meeting_minutes) with RLS. Depends on the rooms migration for the is_room_member helper (the old file re-created it; the definition is identical, so it lives in exactly one place now).
--
-- Source: infra/schema_meetings.sql (duplicate is_room_member definition removed).
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

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT ON meeting_minutes TO puspa_app;
    END IF;
END $$;


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

-- migrate:down
DROP TABLE IF EXISTS meeting_minutes CASCADE;
