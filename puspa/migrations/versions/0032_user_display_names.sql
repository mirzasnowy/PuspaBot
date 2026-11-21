-- Per-user display names (V2-27, group chat names).
--
-- Every user gets an editable display name: the name the chat
-- surface shows for them in room member lists, message authors,
-- memory authors, and decisions. The name lives on the `users`
-- row itself, because `users` is already the identity system
-- every room member belongs to (see `rooms.created_by` and
-- `room_members.user_id`); no second identity system is added.
--
-- The column is nullable: NULL means "no name set yet", and the
-- name resolver falls back to a readable placeholder ("Anggota
-- N" in a room, "Anggota" without one). A raw user id is never
-- shown to another user, so the fallback is what an unnamed
-- member is called, never their UUID.
--
-- The length cap (100 characters) is enforced here as well as in
-- the store (`puspa/rooms/names.py`), so a name written by any
-- path stays short enough to render in a member list.
--
-- Reversible: yes. The down section drops the column (and the
-- names with it, so roll back on a data copy first, never on a
-- live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
ALTER TABLE users ADD COLUMN IF NOT EXISTS display_name TEXT;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_display_name_length;
ALTER TABLE users ADD CONSTRAINT users_display_name_length
    CHECK (display_name IS NULL OR char_length(display_name) <= 100);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, UPDATE (display_name) ON users TO puspa_app;
    END IF;
END $$;

-- migrate:down
ALTER TABLE users DROP COLUMN IF EXISTS display_name;
