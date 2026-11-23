-- Generated from puspa/migrations/versions/0010_rooms.sql by
-- scripts/generate_sqlite_migrations.py. Do not edit by hand;
-- edit the Postgres file and regenerate.

-- migrate:up
CREATE TABLE IF NOT EXISTS rooms (
    id         UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    name       TEXT NOT NULL,
    created_by UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE TABLE IF NOT EXISTS room_members (
    room_id   UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role      TEXT NOT NULL CHECK (role IN ('owner', 'member', 'guest')),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (room_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_room_members_user_id ON room_members(user_id);

CREATE TABLE IF NOT EXISTS room_invites (
    id              UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    room_id         UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    inviter_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('link', 'code')),
    role            TEXT NOT NULL CHECK (role IN ('member', 'guest')),
    token_hash      TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'revoked', 'expired')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    expires_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_room_invites_room_id ON room_invites(room_id);

CREATE TABLE IF NOT EXISTS room_memories (
    id             UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    room_id        UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    author_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('note', 'decision', 'summary')),
    content        TEXT NOT NULL,
    visibility     TEXT NOT NULL DEFAULT 'room'
                   CHECK (visibility IN ('room', 'private')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_room_memories_room_id ON room_memories(room_id);

CREATE TABLE IF NOT EXISTS room_tasks (
    id            UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    room_id       UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    due_at        TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
    created_by    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    completed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_room_tasks_room_id ON room_tasks(room_id);

CREATE INDEX IF NOT EXISTS idx_room_tasks_owner ON room_tasks(owner_user_id);

CREATE TABLE IF NOT EXISTS room_money_approvals (
    action_id       TEXT NOT NULL,
    room_id         UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    approver_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (action_id, room_id, approver_user_id)
);

CREATE INDEX IF NOT EXISTS idx_room_money_approvals_room ON room_money_approvals(room_id);

CREATE TABLE IF NOT EXISTS room_channel_links (
    platform   TEXT NOT NULL CHECK (platform IN ('telegram', 'whatsapp')),
    chat_id    TEXT NOT NULL,
    room_id    UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    linked_by  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    PRIMARY KEY (platform, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_room_channel_links_room ON room_channel_links(room_id);

CREATE TABLE IF NOT EXISTS delegated_tasks (
    task_id        UUID PRIMARY KEY REFERENCES room_tasks(id) ON DELETE CASCADE,
    room_id        UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    delegate_kind  TEXT NOT NULL CHECK (delegate_kind IN ('agent', 'teammate')),
    assignee_ref   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'delegated'
                   CHECK (status IN ('delegated', 'in_progress', 'awaiting_approval', 'blocked', 'done')),
    last_report    TEXT,
    reported_at    TIMESTAMPTZ,
    started_at     TIMESTAMPTZ,
    blocked_reason TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_delegated_tasks_room_id ON delegated_tasks(room_id);

CREATE INDEX IF NOT EXISTS idx_delegated_tasks_status ON delegated_tasks(status);

CREATE TABLE IF NOT EXISTS delegated_task_steps (
    id                  UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    task_id             UUID NOT NULL REFERENCES room_tasks(id) ON DELETE CASCADE,
    description         TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    irreversible        BOOLEAN NOT NULL DEFAULT FALSE,
    status              TEXT NOT NULL DEFAULT 'proposed'
                        CHECK (status IN ('proposed', 'awaiting_approval', 'approved', 'rejected', 'executed')),
    content             JSONB NOT NULL DEFAULT '{}',
    approval_request_id TEXT,
    approved_by         UUID REFERENCES users(id) ON DELETE SET NULL,
    decided_at          TIMESTAMPTZ,
    executed_at         TIMESTAMPTZ,
    result              TEXT,
    created_by          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_delegated_task_steps_task_id ON delegated_task_steps(task_id);

CREATE INDEX IF NOT EXISTS idx_delegated_task_steps_status ON delegated_task_steps(status);

CREATE TABLE IF NOT EXISTS room_splits (
    id             UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    room_id        UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('meal', 'gift', 'arisan', 'other')),
    total_cents    BIGINT NOT NULL CHECK (total_cents > 0),
    payer_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_by     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    due_at         TIMESTAMPTZ,
    status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'partially_paid', 'settled')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    settled_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_room_splits_room_id ON room_splits(room_id);

CREATE TABLE IF NOT EXISTS split_shares (
    split_id     UUID NOT NULL REFERENCES room_splits(id) ON DELETE CASCADE,
    user_id      UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    share_cents  BIGINT NOT NULL CHECK (share_cents > 0),
    paid_cents   BIGINT NOT NULL DEFAULT 0 CHECK (paid_cents >= 0),
    position     INT NOT NULL DEFAULT 0,
    CHECK (paid_cents <= share_cents),
    PRIMARY KEY (split_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_split_shares_split_id ON split_shares(split_id);

CREATE TABLE IF NOT EXISTS split_payments (
    id              UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    split_id        UUID NOT NULL REFERENCES room_splits(id) ON DELETE CASCADE,
    debtor_user_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount_cents    BIGINT NOT NULL CHECK (amount_cents > 0),
    recorded_by     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_split_payments_split_id ON split_payments(split_id);

CREATE TABLE IF NOT EXISTS split_settlements (
    id                UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    split_id          UUID NOT NULL REFERENCES room_splits(id) ON DELETE CASCADE,
    debtor_user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    recipient_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount_cents      BIGINT NOT NULL CHECK (amount_cents > 0),
    action_id         TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'prepared'
                      CHECK (status IN ('prepared', 'superseded')),
    prepared_by       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    prepared_at       TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now'))
);

CREATE INDEX IF NOT EXISTS idx_split_settlements_split_id ON split_settlements(split_id);

CREATE TABLE IF NOT EXISTS room_research_links (
    id         UUID PRIMARY KEY DEFAULT (lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))), 2) || '-' || substr('ab89', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' || lower(hex(randomblob(6)))),
    room_id    UUID NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    url        TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL,
    added_by   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00','now')),
    UNIQUE (room_id, url)
);

CREATE INDEX IF NOT EXISTS idx_room_research_links_room_id ON room_research_links(room_id);

-- migrate:down
DROP TABLE IF EXISTS room_research_links, split_settlements, split_payments, split_shares, room_splits, delegated_task_steps, delegated_tasks, room_channel_links, room_money_approvals, room_tasks, room_memories, room_invites, room_members, rooms CASCADE;
