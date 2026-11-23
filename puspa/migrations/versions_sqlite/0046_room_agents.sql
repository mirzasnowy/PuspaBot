-- Generated from puspa/migrations/versions/0046_room_agents.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS room_agents (
    room_id    UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    agent_id   TEXT NOT NULL,
    added_by   UUID NOT NULL,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (room_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_room_agents_room_id ON room_agents(room_id);

CREATE INDEX IF NOT EXISTS idx_room_agents_agent_id ON room_agents(agent_id);

-- migrate:down
DROP TABLE IF EXISTS room_agents;
