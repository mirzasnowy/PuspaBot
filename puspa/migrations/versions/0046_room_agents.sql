-- Room agents (PWA inbox, 24 Nov 2026).
--
-- A room can invite the user's own agents (Puspa and the custom
-- agents from POST /api/agen), not just human members. Members
-- are users (room_members.user_id is a UUID); agents have their
-- own ids ("puspa-<user uuid>" / "agen-<hex>"), so they get
-- their own table instead of being forced into room_members.
-- Only the room owner may invite or remove an agent, and only
-- an agent the inviter owns (checked against agent_meta).
--
-- Reversible: yes. The down section drops the table this
-- migration creates (and its data — roll back on a data copy
-- first, never on a live database without a backup).
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS room_agents (
    room_id    UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    agent_id   TEXT NOT NULL,
    added_by   UUID NOT NULL,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_room_agents_room_id ON room_agents(room_id);
CREATE INDEX IF NOT EXISTS idx_room_agents_agent_id ON room_agents(agent_id);

-- migrate:down
DROP TABLE IF EXISTS room_agents;
