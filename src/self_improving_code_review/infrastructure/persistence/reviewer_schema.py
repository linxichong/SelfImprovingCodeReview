"""M5 の担当実行、指摘採用元および実行リース用 SQLite スキーマ。."""

REVIEWER_LEASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS reviewer_executions (
    run_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    profile TEXT NOT NULL CHECK (profile IN ('general', 'security', 'data', 'test')),
    reason_code TEXT NOT NULL,
    score INTEGER NOT NULL CHECK (score >= 0),
    prompt_version TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'success', 'partial-success', 'failed', 'skipped')
    ),
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    latency_ms INTEGER NOT NULL CHECK (latency_ms >= 0),
    cost_usd TEXT NOT NULL,
    valid_finding_count INTEGER NOT NULL CHECK (valid_finding_count >= 0),
    duplicate_count INTEGER NOT NULL CHECK (duplicate_count >= 0),
    PRIMARY KEY (run_id, bundle_id, profile),
    FOREIGN KEY (run_id, bundle_id)
        REFERENCES bundles(run_id, bundle_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS reviewer_finding_sources (
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    profile TEXT NOT NULL CHECK (profile IN ('general', 'security', 'data', 'test')),
    source_finding_id TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    is_selected INTEGER NOT NULL CHECK (is_selected IN (0, 1)),
    PRIMARY KEY (run_id, finding_id, profile, source_finding_id),
    FOREIGN KEY (run_id, finding_id)
        REFERENCES findings(run_id, finding_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS run_leases (
    run_id TEXT PRIMARY KEY,
    lease_owner TEXT NOT NULL,
    lease_expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id) ON DELETE RESTRICT
);
"""
