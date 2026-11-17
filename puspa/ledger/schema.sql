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
        'false_reports', 'data_consents'
    )),
    content JSONB NOT NULL DEFAULT '{}',
    parent_id UUID REFERENCES events(id),
    previous_hash TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_event_task_id_id ON events (task_id, id);
CREATE INDEX IF NOT EXISTS idx_event_jenis ON events (type);
CREATE INDEX IF NOT EXISTS idx_event_time ON events (created_at);

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
    'false_reports', 'data_consents'
));

-- Izin: peran aplikasi hanya INSERT + SELECT. Tanpa UPDATE/DELETE.
REVOKE UPDATE, DELETE ON events FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT, INSERT ON events TO puspa_app;
        REVOKE UPDATE, DELETE ON events FROM puspa_app;
    END IF;
END $$;
