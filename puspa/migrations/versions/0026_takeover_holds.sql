-- Takeover holds + takeover ledger event types (P-09, plan section 12.9).
--
-- Two things, both reversible:
--
-- 1. The closed ledger event set gains ``takeover_started`` and
--    ``takeover_released`` (33 types now: 31 from revision 7 + 2
--    here). A takeover is an event, not an edit: pressing "take
--    over" appends ``takeover_started`` to the ledger and releasing
--    appends ``takeover_released``; the hold itself is never an
--    UPDATE/DELETE on a ledger row.
-- 2. A ``takeover_holds`` table holds the one live hold per agent:
--    while a row exists, the agent takes no new autonomous action
--    (the turn loop consults it before every model call and every
--    tool call). Releasing deletes the row; the ledger events are
--    the durable audit trail, so the table keeps no history.
--
-- Reversible: yes. The down section drops the table and restores
-- the 31-type CHECK. (Rows of the two new types, if any were
-- appended before the rollback, would then violate the restored
-- CHECK on any future write to them — but the CHECK only fires on
-- INSERT/UPDATE, and the ledger never UPDATEs, so old rows stay
-- readable.)
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
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
    'false_report', 'data_consent',
    'takeover_started', 'takeover_released'
));

CREATE TABLE IF NOT EXISTS takeover_holds (
    agent_id TEXT PRIMARY KEY,
    held_by_user_id TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    task_id TEXT,
    held_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_takeover_holds_user
    ON takeover_holds (held_by_user_id);

-- migrate:down
DROP TABLE IF EXISTS takeover_holds;
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
