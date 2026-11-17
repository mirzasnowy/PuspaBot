-- Skema Postgres Fase 2 (idempotent; jalankan lewat puspa.runtime.create_schema).
-- Prasyarat: skema Fase 0 (pengguna, agen, kejadian) sudah ada.

CREATE TABLE IF NOT EXISTS agent_meta (
    agent_id TEXT PRIMARY KEY REFERENCES agents(agent_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    peran TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    required BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_meta_user_id ON agent_meta(user_id);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'accepted',
    cost_rp INTEGER NOT NULL DEFAULT 0,
    step INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_task_user_id ON tasks(user_id);

CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    dari_id TEXT NOT NULL,
    to_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    masukan JSONB NOT NULL DEFAULT '{}',
    expected_output JSONB NOT NULL DEFAULT '{}',
    timeout_seconds INTEGER NOT NULL DEFAULT 45,
    anggaran JSONB NOT NULL DEFAULT '{}',
    permission_class TEXT NOT NULL,
    completion_definition TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'proposed',
    trial INTEGER NOT NULL DEFAULT 1,
    last_heartbeat TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    handed_over_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_handoff_task_id ON handoffs(task_id);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    klaim TEXT NOT NULL,
    value NUMERIC NOT NULL,
    unit TEXT NOT NULL DEFAULT 'Rp',
    sumber_url TEXT NOT NULL,
    time_take TIMESTAMPTZ NOT NULL DEFAULT now(),
    text_excerpt TEXT NOT NULL DEFAULT '',
    page_hash TEXT NOT NULL DEFAULT '',
    screenshot TEXT,
    reader_id TEXT NOT NULL DEFAULT '',
    verifier_id TEXT,
    vonis TEXT NOT NULL DEFAULT 'Uncertain'
);
CREATE INDEX IF NOT EXISTS idx_evidence_task_id ON evidence(task_id);
CREATE INDEX IF NOT EXISTS idx_evidence_user_id ON evidence(user_id);

CREATE TABLE IF NOT EXISTS triggers (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    schedule TEXT NOT NULL DEFAULT '',
    workflow TEXT NOT NULL DEFAULT '',
    next_trigger_at TIMESTAMPTZ NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trigger_berikutnya ON triggers(next_trigger_at) WHERE active;

CREATE TABLE IF NOT EXISTS trigger_runs (
    idempotency_key TEXT PRIMARY KEY,
    trigger_id TEXT NOT NULL REFERENCES triggers(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    terlewat BOOLEAN NOT NULL DEFAULT FALSE,
    jalan_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_connectors (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    connector TEXT NOT NULL,
    scope TEXT[] NOT NULL DEFAULT '{}',
    terhubung BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, agent_id, connector)
);

CREATE TABLE IF NOT EXISTS cassettes (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    berkas TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_cassette_task_id ON cassettes(task_id);

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id TEXT PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_push_user_id ON push_subscriptions(user_id);

CREATE TABLE IF NOT EXISTS system_heartbeats (
    komponen TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Penolakan hapus Puspa di level DATABASE (bukan hanya API/UI):
-- baris agen_meta dengan wajib=TRUE tidak bisa di-DELETE.
CREATE OR REPLACE FUNCTION tolak_delete_puspa() RETURNS trigger AS $$
BEGIN
    IF OLD.required THEN
        RAISE EXCEPTION 'Puspa tidak bisa dihapus';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_tolak_delete_puspa ON agent_meta;
CREATE TRIGGER trg_tolak_delete_puspa BEFORE DELETE ON agent_meta
FOR EACH ROW EXECUTE FUNCTION tolak_delete_puspa();

-- RLS (pola yang sama seperti Fase 0/1: app.current_user_id).
-- Penegakan nyata hanya untuk koneksi sebagai puspa_app.
ALTER TABLE agent_meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE handoffs ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE triggers ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_connectors ENABLE ROW LEVEL SECURITY;
ALTER TABLE cassettes ENABLE ROW LEVEL SECURITY;
ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['agent_meta','tasks','handoffs','evidence','triggers','agent_connectors','cassettes','push_subscriptions'] LOOP
        EXECUTE format('DROP POLICY IF EXISTS %I ON %I', 'isolasi_' || t, t);
        EXECUTE format(
            'CREATE POLICY %I ON %I USING (current_setting(%L, true) = user_id::text) WITH CHECK (current_setting(%L, true) = user_id::text)',
            'isolasi_' || t, t, 'app.current_user_id', 'app.current_user_id');
    END LOOP;
END $$;
