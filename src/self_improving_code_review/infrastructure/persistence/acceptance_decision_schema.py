"""受入判断の状態と監査を追加する第13前進移行."""

ACCEPTANCE_DECISION_SCHEMA = """
CREATE TABLE IF NOT EXISTS acceptance_issue_states (
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    state_json TEXT NOT NULL,
    state_hash TEXT NOT NULL CHECK (length(state_hash) = 64),
    PRIMARY KEY(project_id, draft_id, issue_id),
    FOREIGN KEY(project_id, draft_id)
        REFERENCES acceptance_drafts(project_id, draft_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS acceptance_decision_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    draft_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    event_hash TEXT NOT NULL CHECK (length(event_hash) = 64),
    UNIQUE(project_id, operation_id),
    FOREIGN KEY(project_id, draft_id, issue_id)
        REFERENCES acceptance_issue_states(project_id, draft_id, issue_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS acceptance_decision_event_lookup
ON acceptance_decision_events(project_id, draft_id, issue_id, sequence);
"""
