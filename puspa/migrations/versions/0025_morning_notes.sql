-- Morning notes (P-07, plan section 12.8): notes-to-actions.
--
-- One row per note (voice message, pasted text, meeting, lecture,
-- or study note): the raw text, the extractive summary, and the
-- actions extracted from it (JSONB, each with its ``task_id`` in
-- the ``tasks`` table, where the action lives as a tracked task).
-- The note is the receipt for where its tasks came from.
--
-- Reversible: yes. The down section drops the table.
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS morning_notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    source TEXT NOT NULL
        CHECK (source IN ('voice', 'text', 'meeting', 'lecture', 'study')),
    title TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_morning_notes_user_created
    ON morning_notes (user_id, created_at DESC);

-- migrate:down
DROP TABLE IF EXISTS morning_notes;
