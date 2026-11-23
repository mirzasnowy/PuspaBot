-- Generated from puspa/migrations/versions/0006_world.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS world_beliefs (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    state JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (user_id, agent_id)
);

CREATE TABLE IF NOT EXISTS belief_versions (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    belief_key TEXT NOT NULL,
    version INT NOT NULL,
    value JSONB NOT NULL DEFAULT '{}',
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    known_from TIMESTAMPTZ NOT NULL,
    known_to TIMESTAMPTZ,
    supersedes INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (user_id, agent_id, belief_key, version),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (known_to IS NULL OR known_to > known_from)
);

CREATE INDEX IF NOT EXISTS idx_belief_versions_known ON belief_versions
    (user_id, agent_id, belief_key, known_from, known_to);

CREATE INDEX IF NOT EXISTS idx_belief_versions_valid ON belief_versions
    (user_id, agent_id, belief_key, valid_from, valid_to);

-- Ledger entries: one valid instant per entry (from the old
-- migrate_bitemporal.sql upgrade script). The recorded time stays
-- created_at (chain order); valid_at is metadata recorded
-- alongside, OUTSIDE the hashed payload, so the hash chain is
-- untouched. Backfill is honest: the past has no separately
-- recorded valid time, so existing entries get
-- valid_at = created_at. Fresh databases already have the column
-- from migration 0001; this block is the upgrade path for an
-- events table created before K3, and it is what makes
-- re-applying this migration restore the column after it was
-- dropped. The DO block guards the case where the ledger table
-- does not exist yet in this database.
UPDATE events SET valid_at = created_at WHERE valid_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_event_valid_at ON events (valid_at);

CREATE TABLE IF NOT EXISTS causal_hypotheses (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    cause TEXT NOT NULL,
    effect TEXT NOT NULL,
    effect_value JSONB NOT NULL DEFAULT 'null',
    condition TEXT NOT NULL DEFAULT '',
    strength DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    evidence JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    expires_at TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    supporting INT NOT NULL DEFAULT 0,
    contradicting INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    CHECK (strength >= 0 AND strength <= 1),
    CHECK (status IN ('active', 'expired', 'contradicted')),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (supporting >= 0),
    CHECK (contradicting >= 0)
);

CREATE INDEX IF NOT EXISTS idx_causal_hypotheses_cause ON causal_hypotheses
    (user_id, agent_id, cause, condition, status);

CREATE INDEX IF NOT EXISTS idx_causal_hypotheses_expiry ON causal_hypotheses
    (user_id, agent_id, expires_at) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_causal_hypotheses_valid ON causal_hypotheses
    (user_id, agent_id, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS agent_goal_models (
    id UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE,
    contact_id TEXT NOT NULL,
    goals JSONB NOT NULL DEFAULT '{}',
    observations INT NOT NULL DEFAULT 0,
    replies INT NOT NULL DEFAULT 0,
    reply_delays JSONB NOT NULL DEFAULT '[]',
    channels JSONB NOT NULL DEFAULT '{}',
    last_day INT,
    predictions JSONB NOT NULL DEFAULT '[]',
    stats JSONB NOT NULL DEFAULT '{"predictions": 0, "correct": 0}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE (user_id, agent_id, contact_id),
    CHECK (observations >= 0),
    CHECK (replies >= 0),
    CHECK (replies <= observations)
);

CREATE INDEX IF NOT EXISTS idx_agent_goal_models_contact ON agent_goal_models
    (user_id, agent_id, contact_id);

-- migrate:down
DROP TABLE IF EXISTS agent_goal_models, causal_hypotheses, belief_versions, world_beliefs CASCADE;

ALTER TABLE events DROP COLUMN IF EXISTS valid_at;
