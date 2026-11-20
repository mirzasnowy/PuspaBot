-- Memory rights (P-01, plan section 12.6): confidence and source
-- detail on profile facts, consent withdrawal on data consents.
--
-- ``profile_facts.confidence`` persists the confidence a fact was
-- recorded with (consolidation and dream produce 0.0-1.0 values
-- that were previously computed and then dropped). NULL means no
-- confidence was ever recorded for the fact; the read service
-- derives a display value from the fact's source in that case
-- (see puspa/pwa/memory_api.py), it never invents a stored one.
--
-- ``profile_facts.source_detail`` is a short human-readable
-- provenance note written at record time, for example "from the
-- chat on 3 November 2026". It is display text only and is never
-- used for access control or retrieval.
--
-- ``data_consents.withdrawn_at`` records a consent withdrawal:
-- the consent row stays (the fact that consent was once given is
-- audit history), and a non-NULL value means the user has
-- withdrawn it. Withdrawing consent does not delete data; deletion
-- is the forget / account-delete path.
--
-- Reversible: yes. The down section drops the three columns.
-- Apply with: python -m puspa.migrations up
-- Never edit an applied migration: add a new numbered migration instead.
-- Never run this file with psql -f: only the migration runner applies
-- it, in order, inside one transaction per migration.

-- migrate:up
ALTER TABLE profile_facts ADD COLUMN IF NOT EXISTS confidence REAL
    CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
ALTER TABLE profile_facts ADD COLUMN IF NOT EXISTS source_detail TEXT;
ALTER TABLE data_consents ADD COLUMN IF NOT EXISTS withdrawn_at TIMESTAMPTZ;

-- migrate:down
ALTER TABLE profile_facts DROP COLUMN IF EXISTS confidence;
ALTER TABLE profile_facts DROP COLUMN IF EXISTS source_detail;
ALTER TABLE data_consents DROP COLUMN IF EXISTS withdrawn_at;
