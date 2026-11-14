ALTER TABLE review_runs ADD COLUMN pipeline_config_hash TEXT;
ALTER TABLE review_runs ADD COLUMN bundle_plan_hash TEXT;
ALTER TABLE review_runs ADD COLUMN context_source_hash TEXT;
ALTER TABLE review_runs ADD COLUMN agent_prompt_version TEXT;
ALTER TABLE review_runs ADD COLUMN agent_prompt_hash TEXT;
ALTER TABLE review_runs ADD COLUMN reflection_prompt_version TEXT;
ALTER TABLE review_runs ADD COLUMN reflection_prompt_hash TEXT;
ALTER TABLE review_runs ADD COLUMN resume_count INTEGER NOT NULL DEFAULT 0
  CHECK (resume_count >= 0);

ALTER TABLE bundles ADD COLUMN reviewer_type TEXT
  CHECK (reviewer_type IN ('general', 'security', 'test'));
ALTER TABLE bundles ADD COLUMN profile_reason_code TEXT;
ALTER TABLE bundles ADD COLUMN bundle_input_hash TEXT;
ALTER TABLE bundles ADD COLUMN bundle_result_hash TEXT;

ALTER TABLE llm_calls ADD COLUMN call_kind TEXT NOT NULL DEFAULT 'review'
  CHECK (call_kind IN ('review', 'review-turn', 'reflection'));
ALTER TABLE llm_calls ADD COLUMN turn_index INTEGER NOT NULL DEFAULT 1
  CHECK (turn_index >= 1);

CREATE TABLE bundle_edges (
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

CREATE TABLE tool_calls (
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

CREATE TABLE context_source_files (
    run_id TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    path TEXT NOT NULL,
    source_revision TEXT,
    content_hash TEXT NOT NULL,
    PRIMARY KEY (run_id, bundle_id, path),
    FOREIGN KEY (run_id, bundle_id) REFERENCES bundles(run_id, bundle_id)
);

CREATE TABLE reflection_results (
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

CREATE TABLE run_checkpoints (
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
