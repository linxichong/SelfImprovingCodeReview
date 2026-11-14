"""既存の初期スキーマと段階別テーブル定義を保持する."""

REVIEW_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_snapshots (
    rule_snapshot_id TEXT PRIMARY KEY,
    git_sha TEXT,
    rule_set_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_snapshot_items (
    rule_snapshot_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (rule_snapshot_id, rule_id),
    FOREIGN KEY (rule_snapshot_id) REFERENCES rule_snapshots(rule_snapshot_id)
);
CREATE TABLE IF NOT EXISTS review_runs (
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
    memory_snapshot_id TEXT,
    FOREIGN KEY (rule_snapshot_id) REFERENCES rule_snapshots(rule_snapshot_id),
    FOREIGN KEY (memory_snapshot_id) REFERENCES memory_snapshots(memory_snapshot_id)
        ON DELETE RESTRICT
);
CREATE UNIQUE INDEX IF NOT EXISTS review_runs_completed_idempotency
ON review_runs(idempotency_key)
WHERE status IN ('running', 'success', 'partial-success');
CREATE TABLE IF NOT EXISTS bundles (
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
CREATE TABLE IF NOT EXISTS bundle_files (
    run_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    path TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (run_id, bundle_id, path),
    FOREIGN KEY (run_id, bundle_id) REFERENCES bundles(run_id, bundle_id)
);
CREATE TABLE IF NOT EXISTS filtered_files (
    run_id TEXT NOT NULL,
    path TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    PRIMARY KEY (run_id, path),
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id)
);
CREATE TABLE IF NOT EXISTS findings (
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
CREATE TABLE IF NOT EXISTS finding_rules (
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    rule_snapshot_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id, rule_snapshot_id, rule_id),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id),
    FOREIGN KEY (rule_snapshot_id, rule_id)
        REFERENCES rule_snapshot_items(rule_snapshot_id, rule_id)
);
CREATE TABLE IF NOT EXISTS llm_calls (
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
"""

M3_SCHEMA = """
CREATE TABLE IF NOT EXISTS bundle_edges (
    run_id TEXT NOT NULL,
    left_path TEXT NOT NULL,
    right_path TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight INTEGER NOT NULL,
    decision TEXT NOT NULL
      CHECK (decision IN ('selected', 'cut-cycle', 'cut-budget')),
    PRIMARY KEY (run_id, left_path, right_path, relation),
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id)
);
CREATE TABLE IF NOT EXISTS tool_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    request_hash TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
      status IN (
        'success', 'not-found', 'invalid-request', 'repeated-request',
        'budget-exceeded', 'unsupported-language', 'failed', 'blocked'
      )
    ),
    consumed_tokens INTEGER NOT NULL CHECK (consumed_tokens >= 0),
    result_count INTEGER NOT NULL CHECK (result_count >= 0),
    reason_code TEXT NOT NULL,
    source_metadata_hash TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    UNIQUE (run_id, bundle_id, sequence),
    FOREIGN KEY (run_id, bundle_id) REFERENCES bundles(run_id, bundle_id)
);
CREATE TABLE IF NOT EXISTS context_source_files (
    run_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    path TEXT NOT NULL,
    source_revision TEXT,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, bundle_id, path),
    FOREIGN KEY (run_id, bundle_id) REFERENCES bundles(run_id, bundle_id)
);
CREATE TABLE IF NOT EXISTS reflection_results (
    run_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    reflection_call_id INTEGER NOT NULL,
    status TEXT NOT NULL
      CHECK (status IN ('supported', 'contradicted', 'needs-context')),
    reason_code TEXT NOT NULL,
    PRIMARY KEY (run_id, finding_id),
    FOREIGN KEY (run_id, finding_id) REFERENCES findings(run_id, finding_id),
    FOREIGN KEY (reflection_call_id) REFERENCES llm_calls(call_id)
);
CREATE TABLE IF NOT EXISTS run_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL
      CHECK (stage IN ('diff-parsed', 'plan-created', 'bundle-completed', 'finalized')),
    bundle_id TEXT NOT NULL DEFAULT '',
    input_hash TEXT NOT NULL,
    result_hash TEXT,
    created_at TEXT NOT NULL,
    CHECK (
      (stage = 'bundle-completed' AND bundle_id <> '' AND result_hash IS NOT NULL)
      OR
      (stage <> 'bundle-completed' AND bundle_id = '' AND result_hash IS NULL)
    ),
    UNIQUE (run_id, stage, bundle_id),
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id)
);
"""

M4_SCHEMA = """
CREATE TABLE IF NOT EXISTS scm_changes (
    provider TEXT NOT NULL CHECK (provider IN ('github')),
    repository_external_id TEXT NOT NULL,
    change_external_id TEXT NOT NULL,
    base_sha TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('open', 'closed')),
    is_draft INTEGER NOT NULL CHECK (is_draft IN (0, 1)),
    source_repository_external_id TEXT NOT NULL,
    latest_run_id TEXT,
    updated_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY (provider, repository_external_id, change_external_id),
    FOREIGN KEY (latest_run_id) REFERENCES review_runs(run_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS scm_publications (
    publication_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    finding_id TEXT,
    provider TEXT NOT NULL CHECK (provider IN ('github')),
    repository_external_id TEXT NOT NULL,
    change_external_id TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    fingerprint_hash TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('inline', 'summary')),
    external_comment_id TEXT,
    external_comment_url TEXT,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
      status IN ('planned', 'publishing', 'published', 'updating', 'failed', 'stale', 'blocked')
    ),
    attempt INTEGER NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    lease_until TEXT,
    safe_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (
      provider, repository_external_id, change_external_id,
      head_sha, kind, fingerprint_hash
    ),
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS scm_publications_run ON scm_publications(run_id, status);
CREATE TABLE IF NOT EXISTS scm_feedback_events (
    provider TEXT NOT NULL CHECK (provider IN ('github')),
    external_event_id TEXT NOT NULL,
    publication_id TEXT NOT NULL,
    external_comment_id TEXT NOT NULL,
    actor_id TEXT,
    label TEXT,
    payload_hash TEXT NOT NULL,
    mapped_feedback_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processed', 'ignored', 'failed')),
    safe_error_code TEXT,
    observed_at TEXT NOT NULL,
    processed_at TEXT,
    PRIMARY KEY (provider, external_event_id),
    FOREIGN KEY (publication_id) REFERENCES scm_publications(publication_id) ON DELETE RESTRICT,
    FOREIGN KEY (mapped_feedback_id) REFERENCES feedback(feedback_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS scm_event_cursors (
    provider TEXT NOT NULL CHECK (provider IN ('github')),
    repository_external_id TEXT NOT NULL,
    change_external_id TEXT NOT NULL,
    cursor TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (provider, repository_external_id, change_external_id)
);
"""
