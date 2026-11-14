-- Web発行と監査を追加する第15前進移行.
BEGIN IMMEDIATE;
ALTER TABLE acceptance_operation_keys RENAME TO acceptance_operation_keys_v14;
CREATE TABLE acceptance_operation_keys (
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('issue','change','publication')),
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    PRIMARY KEY(project_id,operation_id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
);
INSERT INTO acceptance_operation_keys SELECT * FROM acceptance_operation_keys_v14;
DROP TABLE acceptance_operation_keys_v14;
CREATE TABLE acceptance_publications (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    set_id TEXT NOT NULL UNIQUE,
    operation_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_hash TEXT NOT NULL CHECK(length(record_hash)=64),
    UNIQUE(project_id,operation_id),
    FOREIGN KEY(project_id,draft_id)
        REFERENCES acceptance_drafts(project_id,draft_id) ON DELETE RESTRICT,
    FOREIGN KEY(set_id) REFERENCES ground_truth_sets(set_id) ON DELETE RESTRICT
);
CREATE INDEX acceptance_publication_lookup
ON acceptance_publications(project_id,draft_id,sequence);
INSERT INTO schema_migrations(version,applied_at)
VALUES(15,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
COMMIT;
