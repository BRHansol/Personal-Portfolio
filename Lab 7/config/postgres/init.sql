-- ══════════════════════════════════════════════════════
-- Lab 7 PostgreSQL init — replaces mock_storage/mock_metadata
-- ══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS uploads (
    file_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    filename         TEXT        NOT NULL,
    size             BIGINT      NOT NULL,
    mime_type        TEXT        NOT NULL DEFAULT 'application/octet-stream',
    status           TEXT        NOT NULL DEFAULT 'uploaded',
    file_path        TEXT,
    request_id       TEXT,
    upload_timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    job_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id         UUID        REFERENCES uploads(file_id) ON DELETE CASCADE,
    operation       TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending',
    output_file     TEXT,
    processing_time FLOAT,
    request_id      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS ai_analyses (
    analysis_id      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id          UUID        REFERENCES uploads(file_id) ON DELETE CASCADE,
    analysis_type    TEXT        NOT NULL,
    confidence       FLOAT,
    model_version    TEXT,
    results          JSONB,
    request_id       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS workflows (
    workflow_id      TEXT        PRIMARY KEY,
    file_id          UUID        REFERENCES uploads(file_id),
    request_id       TEXT,
    upload_status    TEXT,
    processing_status TEXT,
    ai_analysis_status TEXT,
    total_time       FLOAT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_uploads_timestamp    ON uploads(upload_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_processing_file_id   ON processing_jobs(file_id);
CREATE INDEX IF NOT EXISTS idx_ai_analyses_file_id  ON ai_analyses(file_id);
CREATE INDEX IF NOT EXISTS idx_workflows_created_at ON workflows(created_at DESC);

GRANT ALL ON ALL TABLES    IN SCHEMA public TO lab7;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO lab7;
