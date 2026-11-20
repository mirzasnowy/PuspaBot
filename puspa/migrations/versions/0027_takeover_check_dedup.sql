-- Drop the duplicate inline event-type CHECK (P-09 follow-up).
--
-- Migration 0026 extended the named CHECK ``event_jenis_check``
-- with the two takeover types, but the ``events`` table carries
-- a SECOND, identical CHECK: the inline one from the CREATE
-- TABLE in 0001_core, auto-named ``events_type_check``. 0026
-- left that one at 31 types, so appending ``takeover_started``
-- still failed with a CheckViolation on a fresh database.
--
-- This migration drops the duplicate and keeps the single named
-- CHECK (``event_jenis_check``, already at 33 types since
-- 0026). One CHECK, one name, one place to extend next time.
-- 0026 itself is not edited: an applied migration is never
-- changed; a new numbered migration fixes it instead.
--
-- Reversible: yes. The down section restores the inline CHECK
-- with the same 33-type list 0026 introduced.
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
ALTER TABLE events DROP CONSTRAINT IF EXISTS events_type_check;

-- migrate:down
ALTER TABLE events ADD CONSTRAINT events_type_check CHECK (type IN (
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
