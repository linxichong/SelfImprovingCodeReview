-- M5 メモリ統制、競合解決、期限再検証の前進移行。
ALTER TABLE memory_versions ADD COLUMN valid_from TEXT;
ALTER TABLE memory_versions ADD COLUMN review_after TEXT;
ALTER TABLE memory_versions
    ADD COLUMN framework_version_constraints_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE memories ADD COLUMN last_confirmed_at TEXT;

UPDATE memory_versions
SET valid_from = created_at
WHERE valid_from IS NULL;
UPDATE memory_versions
SET review_after = strftime('%Y-%m-%dT%H:%M:%f+00:00', created_at, '+180 days')
WHERE review_after IS NULL;

CREATE TABLE IF NOT EXISTS memory_conflicts (
    conflict_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    left_memory_id TEXT NOT NULL,
    left_version INTEGER NOT NULL,
    left_content_hash TEXT NOT NULL,
    right_memory_id TEXT NOT NULL,
    right_version INTEGER NOT NULL,
    right_content_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    resolution TEXT CHECK (
        resolution IS NULL OR resolution IN (
            'narrow-scope', 'supersede-left', 'supersede-right', 'reject-candidate'
        )
    ),
    resolved_by TEXT,
    reason_hash TEXT,
    evidence_hash TEXT,
    detected_at TEXT NOT NULL,
    resolved_at TEXT,
    lock_version INTEGER NOT NULL DEFAULT 1 CHECK (lock_version >= 1),
    CHECK (left_memory_id < right_memory_id),
    FOREIGN KEY (left_memory_id, left_version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT,
    FOREIGN KEY (right_memory_id, right_version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS memory_conflicts_open_pair
    ON memory_conflicts(left_memory_id, left_version, right_memory_id, right_version, reason_code)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS memory_conflicts_project_status
    ON memory_conflicts(project_id, status);

CREATE TABLE IF NOT EXISTS memory_relations (
    source_memory_id TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    target_memory_id TEXT NOT NULL,
    target_version INTEGER NOT NULL,
    relation TEXT NOT NULL CHECK (relation IN ('supersedes', 'restored-from')),
    actor_id TEXT NOT NULL,
    reason_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (
        source_memory_id, source_version, target_memory_id, target_version, relation
    ),
    FOREIGN KEY (source_memory_id, source_version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT,
    FOREIGN KEY (target_memory_id, target_version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS memory_revalidations (
    revalidation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    result_version INTEGER,
    status TEXT NOT NULL CHECK (status IN ('due', 'confirmed', 'invalidated')),
    due_at TEXT NOT NULL,
    next_review_after TEXT,
    actor_id TEXT,
    reason_hash TEXT,
    evidence_hash TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (memory_id, source_version, status, next_review_after),
    FOREIGN KEY (memory_id, source_version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT,
    FOREIGN KEY (memory_id, result_version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS memory_revalidations_project_status
    ON memory_revalidations(project_id, status);

INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES (10, CURRENT_TIMESTAMP);
