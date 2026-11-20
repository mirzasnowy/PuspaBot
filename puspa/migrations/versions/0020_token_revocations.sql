-- Token revocations: durable revocation lists for permission tokens (E-11, plan section 6.6).
--
-- Two tables:
--
-- - token_revocations: one row per individually revoked token, keyed by
--   the token's ``jti`` (see puspa/policy/tokens.py). A token whose jti
--   is listed here is dead, whatever its expiry says.
-- - agent_revocations: one row per stopped agent ("revoke-all"). Every
--   token of that agent issued at or before ``revoked_at`` is dead, so a
--   stop kills the agent's live tokens AND any refresh re-issued from
--   them (a refresh re-verifies the parent token, which now fails).
--
-- These are operator state, not user data, so there is no row-level
-- security policy keyed on a user id. The enforcement is grants: the
-- agent role (puspa_app) may SELECT the lists — the Gate reads them on
-- every call — but may never INSERT, UPDATE, or DELETE a row. Only the
-- table owner (the operator connection) writes revocations, through
-- puspa/policy/revocations.py. A compromised agent can read that it
-- has been stopped; it cannot un-stop itself.
--
-- Reversible: yes. The down section drops the tables this migration
-- creates (and their data — roll back on a data copy first, never on
-- a live database without a backup). The puspa_app role, once created
-- by 0001, is never dropped by any down migration.
--
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
CREATE TABLE IF NOT EXISTS token_revocations (
    jti TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS agent_revocations (
    agent_id TEXT PRIMARY KEY,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reason TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_token_revocations_agent
    ON token_revocations (agent_id, revoked_at);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'puspa_app') THEN
        GRANT SELECT ON token_revocations TO puspa_app;
        GRANT SELECT ON agent_revocations TO puspa_app;
        REVOKE INSERT, UPDATE, DELETE ON token_revocations FROM puspa_app;
        REVOKE INSERT, UPDATE, DELETE ON agent_revocations FROM puspa_app;
    END IF;
END $$;

-- migrate:down
DROP TABLE IF EXISTS token_revocations CASCADE;
DROP TABLE IF EXISTS agent_revocations CASCADE;
