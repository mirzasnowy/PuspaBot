-- Buku Kerja (definisi 2.3): tabel kejadian, append-only.
-- Dijalankan oleh Ledger.create_schema() sebagai peran pemilik DB (root di tes).
-- Peran aplikasi `puspa_app` dibuat sekali oleh superuser Postgres (di luar
-- skema ini, karena peran pemilik DB tidak punya CREATEROLE); blok DO di bawah
-- hanya memberi izin kalau peran itu sudah ada.

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    task_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    subagent_id TEXT,
    type TEXT NOT NULL CHECK (type IN (
        'task_created', 'plan_created', 'subagent_created',
        'handoff_proposed', 'handoff_accepted', 'handoff_handed_over',
        'handoff_rejected', 'handoff_expired', 'heartbeat',
        'tool_called', 'tool_completed', 'evidence_recorded',
        'verification_completed', 'approval_requested', 'approval_granted',
        'approval_rejected', 'user_message', 'agent_message', 'budget_exhausted',
        'task_completed', 'task_failed', 'task_stopped',
        'workflow_proposed', 'workflow_activated', 'workflow_paused',
        'trigger_fired', 'trigger_missed',
        'module_proposed', 'module_activated',
        'false_report', 'data_consent'
    )),
    content JSONB NOT NULL DEFAULT '{}',
    parent_id UUID REFERENCES events(id),
    previous_hash TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_event_task_id_id ON events (task_id, id);
CREATE INDEX IF NOT EXISTS idx_event_jenis ON events (type);
CREATE INDEX IF NOT EXISTS idx_event_time ON events (created_at);

-- B2 outcome records (docs/outcome-records.md): the per-user
-- and per-group outcome queries filter on the content marker
-- plus user_id / room_id, so those lookups are indexed, not
-- full scans.
CREATE INDEX IF NOT EXISTS idx_events_outcome_user
    ON events ((content->>'kind'), (content->>'user_id'));
CREATE INDEX IF NOT EXISTS idx_events_outcome_room
    ON events ((content->>'kind'), (content->>'room_id'));

-- Migrasi CHECK jenis: CREATE TABLE IF NOT EXISTS di atas tidak mengubah
-- constraint tabel yang sudah ada (mis. dibuat saat himpunan masih 22 jenis),
-- jadi constraint lama diganti dengan daftar 31 jenis revisi 7 di sini.
-- Idempotent: DROP IF EXISTS lalu ADD dengan nama constraint yang sama.
ALTER TABLE events DROP CONSTRAINT IF EXISTS event_jenis_check;
ALTER TABLE events ADD CONSTRAINT event_jenis_check CHECK (type IN (
    'task_created', 'plan_created', 'subagent_created',
    'handoff_proposed', 'handoff_accepted', 'handoff_handed_over',
    'handoff_rejected', 'handoff_expired', 'heartbeat',
    'tool_called', 'tool_completed', 'evidence_recorded',
    'verification_completed', 'approval_requested', 'approval_granted',
    'approval_rejected', 'user_message', 'agent_message', 'budget_exhausted',
    'task_completed', 'task_failed', 'task_stopped',
    'workflow_proposed', 'workflow_activated', 'workflow_paused',
    'trigger_fired', 'trigger_missed',
    'module_proposed', 'module_activated',
    'false_report', 'data_consent'
));

-- Bitemporal (K3, docs/bitemporal-state.md): one valid instant per
-- entry. An event is a point in valid time (when the described
-- event was true in the world); the recorded time stays
-- `created_at` (chain order). `valid_at` is metadata recorded
-- alongside, OUTSIDE the hashed payload — the hash inputs are
-- exactly as before, so the chain is untouched. Backfill is
-- honest: the past has no separately recorded valid time, so
-- existing entries get valid_at = created_at. New entries get
-- valid_at from the caller, defaulting to created_at.
ALTER TABLE events ADD COLUMN IF NOT EXISTS valid_at TIMESTAMPTZ;
UPDATE events SET valid_at = created_at WHERE valid_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_event_valid_at ON events (valid_at);

-- Izin: peran aplikasi hanya INSERT + SELECT. Tanpa UPDATE/DELETE.
REVOKE UPDATE, DELETE ON events FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT ON events TO puspa_app;
        REVOKE UPDATE, DELETE ON events FROM puspa_app;
    END IF;
END $$;

-- Kill switch (K12, docs/audit-kill-switch-export.md): one row, the
-- single flag that stops the agent. The application role may only
-- READ it (SELECT); only the table owner (the operator connection)
-- may INSERT/UPDATE it, so the agent can neither arm nor disarm it.
-- The row also records the last change (actor, reason, time); the
-- transitions themselves are recorded in the ledger as well (see
-- puspa/ledger/kill_switch.py).
CREATE TABLE IF NOT EXISTS kill_switch (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    active BOOLEAN NOT NULL DEFAULT FALSE,
    actor TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

REVOKE INSERT, UPDATE, DELETE ON kill_switch FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT ON kill_switch TO puspa_app;
        REVOKE INSERT, UPDATE, DELETE ON kill_switch FROM puspa_app;
    END IF;
END $$;
