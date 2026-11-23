-- Generated from puspa/migrations/versions/0011_meetings.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS meeting_minutes (
    id             UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    room_id        UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    held_at        TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    author_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    minutes_text   TEXT NOT NULL,
    decisions      JSONB NOT NULL DEFAULT '[]',
    action_items   JSONB NOT NULL DEFAULT '[]',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_meeting_minutes_room_id ON meeting_minutes(room_id);

-- migrate:down
DROP TABLE IF EXISTS meeting_minutes CASCADE;
