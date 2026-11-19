CREATE TABLE IF NOT EXISTS mockups (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    template TEXT NOT NULL,
    doc JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mockups_user_idx ON mockups (user_id, updated_at DESC);

ALTER TABLE mockups ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies WHERE tablename = 'mockups' AND policyname = 'mockups_owner'
    ) THEN
        CREATE POLICY mockups_owner ON mockups
            USING (user_id = current_setting('app.user_id', true));
    END IF;
END $$;
