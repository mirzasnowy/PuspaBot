-- Generated from puspa/migrations/versions/0019_conflicts.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
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
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
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

-- migrate:down
DROP TABLE IF EXISTS conflict_records CASCADE;
