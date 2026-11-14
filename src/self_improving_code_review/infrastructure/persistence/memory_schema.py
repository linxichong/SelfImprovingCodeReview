"""M2 の SQLite スキーマ定義.

M1 スキーマとは別モジュールに保持し、共有済みの M1 定義を変更しない.
制約は ``migrations/0002_m2.sql`` と一致させる.
"""

from __future__ import annotations

__all__ = ["MEMORY_SCHEMA"]

MEMORY_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    finding_id TEXT,
    label TEXT NOT NULL CHECK (
        label IN (
            'true-positive',
            'false-positive',
            'needs-more-evidence',
            'not-applicable',
            'missed-issue'
        )
    ),
    file TEXT,
    start_line INTEGER,
    symbol TEXT,
    issue_type TEXT,
    severity TEXT CHECK (severity IS NULL OR severity IN ('critical', 'high', 'medium', 'low')),
    description TEXT,
    evidence TEXT,
    reason TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    supersedes_feedback_id TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE RESTRICT,
    FOREIGN KEY (supersedes_feedback_id) REFERENCES feedback(feedback_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_feedback_run ON feedback(run_id);
CREATE TABLE IF NOT EXISTS memory_versions (
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 1),
    category TEXT NOT NULL CHECK (
        category IN ('framework', 'architecture', 'domain-rule', 'security', 'testing', 'style')
    ),
    scope_json TEXT NOT NULL,
    fact TEXT NOT NULL,
    review_instruction TEXT NOT NULL,
    polarity TEXT NOT NULL CHECK (polarity IN ('reinforce', 'suppress')),
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_summary TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (memory_id, version)
);
CREATE TABLE IF NOT EXISTS memories (
    memory_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    current_version INTEGER NOT NULL,
    current_status TEXT NOT NULL CHECK (
        current_status IN ('pending', 'active', 'rejected', 'conflict', 'superseded', 'archived')
    ),
    lock_version INTEGER NOT NULL CHECK (lock_version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (memory_id, current_version)
        REFERENCES memory_versions(memory_id, version)
        ON DELETE RESTRICT
        DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX IF NOT EXISTS idx_memories_project_status
    ON memories(project_id, current_status);
CREATE TABLE IF NOT EXISTS memory_evidence (
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    feedback_id TEXT NOT NULL,
    run_id TEXT,
    finding_id TEXT,
    relation TEXT NOT NULL,
    PRIMARY KEY (memory_id, version, feedback_id),
    FOREIGN KEY (memory_id, version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT,
    FOREIGN KEY (feedback_id) REFERENCES feedback(feedback_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS memory_transitions (
    transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    memory_version INTEGER NOT NULL,
    from_status TEXT CHECK (
        from_status IS NULL
        OR from_status IN ('pending', 'active', 'rejected', 'conflict', 'superseded', 'archived')
    ),
    to_status TEXT NOT NULL CHECK (
        to_status IN ('pending', 'active', 'rejected', 'conflict', 'superseded', 'archived')
    ),
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (memory_id, memory_version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_memory_transitions_memory
    ON memory_transitions(memory_id);
CREATE TABLE IF NOT EXISTS memory_extraction_runs (
    extraction_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_revision TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'success', 'failed', 'blocked')
    ),
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    prompt_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    request_hash TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    cost_usd TEXT NOT NULL DEFAULT '0',
    safe_error_code TEXT,
    candidate_memory_id TEXT,
    candidate_version INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    CHECK (
        status <> 'success'
        OR (candidate_memory_id IS NOT NULL AND candidate_version IS NOT NULL)
    ),
    FOREIGN KEY (candidate_memory_id, candidate_version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS memory_extraction_feedback (
    extraction_id TEXT NOT NULL,
    feedback_id TEXT NOT NULL,
    feedback_content_hash TEXT NOT NULL,
    PRIMARY KEY (extraction_id, feedback_id),
    FOREIGN KEY (extraction_id)
        REFERENCES memory_extraction_runs(extraction_id) ON DELETE RESTRICT,
    FOREIGN KEY (feedback_id) REFERENCES feedback(feedback_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS memory_snapshots (
    memory_snapshot_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_snapshot_items (
    memory_snapshot_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (memory_snapshot_id, memory_id, version),
    FOREIGN KEY (memory_snapshot_id)
        REFERENCES memory_snapshots(memory_snapshot_id) ON DELETE RESTRICT,
    FOREIGN KEY (memory_id, version)
        REFERENCES memory_versions(memory_id, version) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS finding_memories (
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    memory_snapshot_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    PRIMARY KEY (run_id, finding_id, memory_snapshot_id, memory_id, version),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE RESTRICT,
    FOREIGN KEY (memory_snapshot_id, memory_id, version)
        REFERENCES memory_snapshot_items(memory_snapshot_id, memory_id, version)
        ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS ground_truth_sets (
    set_id TEXT PRIMARY KEY,
    split TEXT NOT NULL CHECK (split IN ('development', 'test')),
    version INTEGER NOT NULL CHECK (version >= 1),
    set_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'verified', 'approved', 'published', 'rejected')
    ),
    approved_by TEXT,
    published_at TEXT,
    CHECK (status <> 'published' OR (approved_by IS NOT NULL AND published_at IS NOT NULL)),
    UNIQUE (split, version)
);
CREATE TABLE IF NOT EXISTS ground_truth_cases (
    set_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    change_ref TEXT NOT NULL,
    created_by TEXT NOT NULL,
    verified_by TEXT,
    approved_by TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'verified', 'approved', 'published', 'rejected')
    ),
    PRIMARY KEY (set_id, sequence),
    UNIQUE (set_id, change_ref),
    FOREIGN KEY (set_id) REFERENCES ground_truth_sets(set_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS ground_truth_issues (
    issue_id TEXT NOT NULL,
    set_id TEXT NOT NULL,
    change_ref TEXT NOT NULL,
    file TEXT NOT NULL,
    start_line INTEGER,
    symbol TEXT,
    issue_type TEXT NOT NULL,
    rule_id TEXT,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    evidence TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_by TEXT NOT NULL,
    verified_by TEXT,
    approved_by TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('draft', 'verified', 'approved', 'published', 'rejected')
    ),
    PRIMARY KEY (set_id, issue_id),
    CHECK (start_line IS NOT NULL OR symbol IS NOT NULL),
    FOREIGN KEY (set_id) REFERENCES ground_truth_sets(set_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS evaluation_runs (
    evaluation_id TEXT PRIMARY KEY,
    set_id TEXT NOT NULL,
    arm TEXT NOT NULL CHECK (arm IN ('A', 'B', 'C')),
    repeat_index INTEGER NOT NULL CHECK (repeat_index >= 1),
    config_hash TEXT NOT NULL,
    rule_snapshot_id TEXT,
    memory_snapshot_id TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'success', 'partial-success', 'failed', 'blocked')
    ),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE (set_id, arm, repeat_index),
    FOREIGN KEY (set_id) REFERENCES ground_truth_sets(set_id) ON DELETE RESTRICT,
    FOREIGN KEY (rule_snapshot_id) REFERENCES rule_snapshots(rule_snapshot_id) ON DELETE RESTRICT,
    FOREIGN KEY (memory_snapshot_id)
        REFERENCES memory_snapshots(memory_snapshot_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS evaluation_matches (
    evaluation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    run_id TEXT,
    finding_id TEXT,
    set_id TEXT,
    issue_id TEXT,
    result TEXT NOT NULL CHECK (
        result IN (
            'true-positive', 'false-positive', 'false-negative', 'duplicate', 'review-required'
        )
    ),
    adjudicator TEXT,
    adjudication_token TEXT,
    adjudication_reason TEXT,
    adjudicated_at TEXT,
    is_superseded INTEGER NOT NULL DEFAULT 0 CHECK (is_superseded IN (0, 1)),
    PRIMARY KEY (evaluation_id, sequence),
    CHECK (finding_id IS NOT NULL OR issue_id IS NOT NULL),
    FOREIGN KEY (evaluation_id) REFERENCES evaluation_runs(evaluation_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id) ON DELETE RESTRICT,
    FOREIGN KEY (set_id, issue_id)
        REFERENCES ground_truth_issues(set_id, issue_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS evaluation_review_runs (
    evaluation_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (evaluation_id, run_id),
    FOREIGN KEY (evaluation_id) REFERENCES evaluation_runs(evaluation_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS evaluation_metrics (
    evaluation_id TEXT NOT NULL,
    metric TEXT NOT NULL,
    numerator INTEGER NOT NULL,
    denominator INTEGER NOT NULL,
    value REAL,
    status TEXT NOT NULL CHECK (status IN ('evaluated', 'not-evaluable')),
    PRIMARY KEY (evaluation_id, metric),
    CHECK ((status = 'evaluated') = (value IS NOT NULL)),
    FOREIGN KEY (evaluation_id) REFERENCES evaluation_runs(evaluation_id) ON DELETE RESTRICT
);
"""
