-- Generated from puspa/migrations/versions/0013_payments.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS billing_usage_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    user_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    thread_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    driver TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    input_tokens BIGINT NOT NULL DEFAULT 0,
    output_tokens BIGINT NOT NULL DEFAULT 0,
    cached_tokens BIGINT NOT NULL DEFAULT 0,
    cost_idr BIGINT NOT NULL DEFAULT 0,
    trigger TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL DEFAULT '',
    steps INT NOT NULL DEFAULT 0,
    tools JSONB NOT NULL DEFAULT '[]',
    tool_errors INT NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'IDR',
    provider TEXT NOT NULL DEFAULT '',
    module_id TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_billing_usage_user_time
    ON billing_usage_rows (user_id, recorded_at);

CREATE INDEX IF NOT EXISTS idx_billing_usage_task
    ON billing_usage_rows (user_id, task_id);

CREATE INDEX IF NOT EXISTS idx_billing_usage_bot_time
    ON billing_usage_rows (bot_id, recorded_at);

-- migrate:down
DROP TABLE IF EXISTS billing_usage_rows CASCADE;
