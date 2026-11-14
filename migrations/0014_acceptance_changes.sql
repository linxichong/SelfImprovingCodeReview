-- 全体確認、変更監査と共通操作ID。既存の問題判断を保持する。
CREATE TABLE IF NOT EXISTS acceptance_operation_keys (
    project_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('issue','change')),
    request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
    PRIMARY KEY(project_id, operation_id),
    FOREIGN KEY(project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
);
INSERT OR IGNORE INTO acceptance_operation_keys
SELECT project_id,operation_id,'issue',json_extract(event_json,'$.request_hash')
FROM acceptance_decision_events;
CREATE TABLE IF NOT EXISTS acceptance_change_reviews (
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version>0),
    state_json TEXT NOT NULL,
    state_hash TEXT NOT NULL CHECK(length(state_hash)=64),
    PRIMARY KEY(project_id,draft_id,case_id),
    FOREIGN KEY(project_id,draft_id) REFERENCES acceptance_drafts(project_id,draft_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS acceptance_change_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    event_hash TEXT NOT NULL CHECK(length(event_hash)=64),
    UNIQUE(project_id,operation_id),
    FOREIGN KEY(project_id,draft_id,case_id)
        REFERENCES acceptance_change_reviews(project_id,draft_id,case_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS acceptance_change_event_lookup
ON acceptance_change_events(project_id,draft_id,case_id,sequence);
INSERT OR IGNORE INTO schema_migrations(version,applied_at)
VALUES(14,strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
