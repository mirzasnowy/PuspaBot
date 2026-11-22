-- Multi-agent teams: peer agents with distinct roles in one shared conversation.
--
-- Creates the team tables (teams, team_members, team_conversations,
-- team_messages). A team is a small group of peer agents — not a
-- hierarchy and not helpers of one main agent: every member has its
-- own role, model, skills, knowledge, and workspace, and the members
-- take turns in ONE shared conversation (team_conversations +
-- team_messages) to finish a task together.
--
-- Reversible: yes. The down section drops the tables this migration
-- creates (and their data — roll back on a data copy first, never on
-- a live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS teams (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    owner_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_members (
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    role        TEXT NOT NULL,
    model       TEXT NOT NULL,
    skills      JSONB NOT NULL DEFAULT '[]'::jsonb,
    knowledge   JSONB NOT NULL DEFAULT '[]'::jsonb,
    workspace   TEXT NOT NULL DEFAULT '',
    instruction TEXT NOT NULL DEFAULT '',
    position    INT NOT NULL,
    joined_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, name)
);
CREATE INDEX IF NOT EXISTS idx_team_members_team_id ON team_members(team_id);

CREATE TABLE IF NOT EXISTS team_conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id     UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    task        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running', 'completed', 'max_rounds')),
    rounds_used INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_team_conversations_team_id ON team_conversations(team_id);

CREATE TABLE IF NOT EXISTS team_messages (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id   UUID NOT NULL REFERENCES team_conversations(id) ON DELETE CASCADE,
    seq               INT NOT NULL,
    author_agent_name TEXT,
    content           TEXT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_team_messages_conversation_id ON team_messages(conversation_id);

-- migrate:down
DROP TABLE IF EXISTS team_messages;
DROP TABLE IF EXISTS team_conversations;
DROP TABLE IF EXISTS team_members;
DROP TABLE IF EXISTS teams;
