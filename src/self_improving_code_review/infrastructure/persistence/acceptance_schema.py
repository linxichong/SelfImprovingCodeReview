"""受入下書きと取込み監査を追加する第12前進移行."""

ACCEPTANCE_SCHEMA = """
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
"""
