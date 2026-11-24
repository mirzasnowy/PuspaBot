-- Agent avatar choice (PWA, 24 Nov 2026).
--
-- The icon a user picks for a custom agent in the Tambah Agent
-- form (one of the built-in flower avatars: tulip, sunflower,
-- rose, sakura) or "default" (the Puspa flower logo). Stored on
-- the agent row so every screen that lists the agent — inbox,
-- Tim Agen, room invites — shows the same icon. The Puspa
-- agent's own photo is NOT stored here: it lives in the user's
-- avatar dir (puspa/pwa/avatar_api.py) and stays "default".
--
-- Reversible: yes. The down section drops the column this
-- migration adds (and its data — roll back on a data copy
-- first, never on a live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
ALTER TABLE agent_meta ADD COLUMN IF NOT EXISTS avatar TEXT NOT NULL DEFAULT 'default';

-- migrate:down
ALTER TABLE agent_meta DROP COLUMN IF EXISTS avatar;
