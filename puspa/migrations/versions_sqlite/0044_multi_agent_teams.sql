-- Generated from puspa/migrations/versions/0044_multi_agent_teams.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS teams (
    id            UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    name          TEXT NOT NULL,
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL,
    model       TEXT NOT NULL,
    skills      JSONB NOT NULL DEFAULT '[]',
    knowledge   JSONB NOT NULL DEFAULT '[]',
    workspace   TEXT NOT NULL DEFAULT '',
    instruction TEXT NOT NULL DEFAULT '',
    position    INT NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (team_id, name)
);

CREATE INDEX IF NOT EXISTS idx_team_members_team_id ON team_members(team_id);

CREATE TABLE IF NOT EXISTS team_conversations (
    id          UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    task        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'max_rounds')),
    rounds_used INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_team_conversations_team_id ON team_conversations(team_id);

CREATE TABLE IF NOT EXISTS team_messages (
    id                UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    conversation_id   UUID NOT NULL REFERENCES team_conversations(id) ON DELETE CASCADE,
    seq               INT NOT NULL,
    author_agent_name TEXT,
    content           TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE (conversation_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_team_messages_conversation_id ON team_messages(conversation_id);

-- migrate:down
DROP TABLE IF EXISTS team_messages;

DROP TABLE IF EXISTS team_conversations;

DROP TABLE IF EXISTS team_members;

DROP TABLE IF EXISTS teams;
