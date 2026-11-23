-- Generated from puspa/migrations/versions/0014_marketplace.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS marketplace_installs (
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    version       TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    source        TEXT NOT NULL,
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'disabled',
    installed_at  TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (skill_id, user_id)
);

CREATE TABLE IF NOT EXISTS marketplace_versions (
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    version       TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    source        TEXT NOT NULL,
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    manifest      JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (skill_id, user_id, version)
);

CREATE TABLE IF NOT EXISTS marketplace_audit (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event         TEXT NOT NULL,
    skill_id      TEXT NOT NULL,
    user_id       TEXT NOT NULL,
    sha256        TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    license       TEXT NOT NULL DEFAULT '',
    price_credits INTEGER NOT NULL DEFAULT 0,
    detail        JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

-- migrate:down
DROP TABLE IF EXISTS marketplace_audit, marketplace_versions, marketplace_installs CASCADE;
