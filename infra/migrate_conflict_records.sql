-- Conflict records migration (Kosmos K13, docs/anti-manipulation.md).
--
-- Idempotent and additive: run it as many times as you like
-- against an existing puspa_test database. It creates nothing
-- twice and drops nothing. Run it once per existing database:
--
--   psql "dbname=puspa_test" -f infra/migrate_conflict_records.sql
--
-- or from Python: puspa.policy.conflicts.create_schema().
-- Fresh databases need the same call before any record is
-- persisted; the in-memory ConflictStore works without it.
--
-- One row per surfaced conflict of interest: what conflicts
-- (kind), which tool, whose interest is on each side
-- (user_interest / other_interest, in plain words), what it
-- costs the user, what was surfaced to the user
-- (surfaced_text), and what the user chose (status /
-- user_choice / resolved_at). A record is never edited in
-- place by the agent: resolving one only moves its status
-- forward (surfaced -> resolved_proceed / resolved_alternative
-- / resolved_declined) and stamps the choice and time. The
-- primary key is the conflict id alone, so a re-surfaced
-- conflict for the same tool + kind is a second row with a new
-- id; the old one stays as history.
--
-- No new ledger event type is added: the closed set of 31
-- revision-7 event types stays closed. Surfacing writes
-- "approval_requested" and the user's choice writes
-- "approval_granted" / "approval_rejected" through the
-- gate's log callable (see puspa.policy.conflicts).

CREATE TABLE IF NOT EXISTS conflict_records (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    beneficiary TEXT NOT NULL,
    user_interest TEXT NOT NULL DEFAULT '',
    other_interest TEXT NOT NULL DEFAULT '',
    user_cost TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'low',
    status TEXT NOT NULL DEFAULT 'surfaced',
    user_choice TEXT,
    surfaced_text TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    CHECK (kind IN (
        'engagement_vs_goal',
        'seller_vs_user',
        'provider_vs_user',
        'retention_vs_independence',
        'attention_vs_benefit'
    )),
    CHECK (beneficiary IN ('user', 'seller', 'provider', 'system', 'other_person')),
    CHECK (severity IN ('low', 'high')),
    CHECK (status IN (
        'surfaced',
        'resolved_proceed',
        'resolved_alternative',
        'resolved_declined'
    )),
    CHECK (user_choice IS NULL OR user_choice IN ('proceed', 'alternative', 'decline')),
    CHECK (resolved_at IS NULL OR status <> 'surfaced')
);

CREATE INDEX IF NOT EXISTS idx_conflict_records_user_tool
    ON conflict_records (user_id, agent_id, tool_name, kind, created_at);
CREATE INDEX IF NOT EXISTS idx_conflict_records_unresolved
    ON conflict_records (user_id, agent_id, created_at) WHERE status = 'surfaced';

ALTER TABLE conflict_records ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    DROP POLICY IF EXISTS isolasi_conflict_records ON conflict_records;
    CREATE POLICY isolasi_conflict_records ON conflict_records
        USING (current_setting('app.current_user_id', true) = user_id::text)
        WITH CHECK (current_setting('app.current_user_id', true) = user_id::text);
END $$;
