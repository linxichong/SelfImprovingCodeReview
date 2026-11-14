PRAGMA foreign_keys = ON;

CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE rule_snapshots (
    rule_snapshot_id TEXT PRIMARY KEY,
    git_sha TEXT,
    rule_set_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE rule_snapshot_items (
    rule_snapshot_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (rule_snapshot_id, rule_id),
    FOREIGN KEY (rule_snapshot_id) REFERENCES rule_snapshots(rule_snapshot_id)
);

CREATE TABLE review_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    source_revision TEXT,
    model TEXT NOT NULL,
    package_version TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    rule_snapshot_id TEXT NOT NULL,
    rule_set_hash TEXT NOT NULL,
    secret_engine_version TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    metrics_json TEXT,
    FOREIGN KEY (rule_snapshot_id) REFERENCES rule_snapshots(rule_snapshot_id)
);

CREATE UNIQUE INDEX review_runs_completed_idempotency
ON review_runs(idempotency_key)
WHERE status IN ('success', 'partial-success');

CREATE TABLE bundles (
    run_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    risk TEXT NOT NULL,
    status TEXT NOT NULL,
    diff_token_count INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_usd TEXT NOT NULL,
    error_code TEXT,
    error_message TEXT,
    PRIMARY KEY (run_id, bundle_id),
    UNIQUE (run_id, sequence),
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id)
);

CREATE TABLE bundle_files (
    run_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    path TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (run_id, bundle_id, path),
    FOREIGN KEY (run_id, bundle_id) REFERENCES bundles(run_id, bundle_id)
);

CREATE TABLE filtered_files (
    run_id TEXT NOT NULL,
    path TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (run_id, path),
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id)
);

CREATE TABLE findings (
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    category TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    primary_entity TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    evidence_quote TEXT NOT NULL,
    evidence_reason TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    enclosing_symbol TEXT NOT NULL,
    position_status TEXT NOT NULL,
    evidence_status TEXT NOT NULL,
    reflection_status TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    is_main INTEGER NOT NULL,
    PRIMARY KEY (run_id, finding_id),
    UNIQUE (run_id, fingerprint),
    FOREIGN KEY (run_id, bundle_id) REFERENCES bundles(run_id, bundle_id)
);

CREATE TABLE finding_rules (
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    rule_snapshot_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id, rule_snapshot_id, rule_id),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id),
    FOREIGN KEY (rule_snapshot_id, rule_id)
        REFERENCES rule_snapshot_items(rule_snapshot_id, rule_id)
);

CREATE TABLE llm_calls (
    call_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    bundle_sequence INTEGER NOT NULL,
    request_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    status TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL,
    cost_usd TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id)
);

INSERT INTO schema_migrations(version, applied_at)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'));
