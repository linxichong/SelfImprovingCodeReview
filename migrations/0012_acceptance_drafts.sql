-- 未検証の受入下書きと取込み監査。既存の正解集合には書き込まない。
CREATE TABLE IF NOT EXISTS acceptance_drafts (
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    source_hash TEXT NOT NULL CHECK (length(source_hash) = 64),
    document_json TEXT NOT NULL,
    document_hash TEXT NOT NULL CHECK (length(document_hash) = 64),
    imported_at TEXT NOT NULL,
    PRIMARY KEY (project_id, draft_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS acceptance_import_events (
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    imported_by TEXT NOT NULL,
    PRIMARY KEY (project_id, draft_id),
    FOREIGN KEY (project_id, draft_id)
        REFERENCES acceptance_drafts(project_id, draft_id) ON DELETE RESTRICT
);
INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES(12, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'));
