PRAGMA foreign_keys = ON;

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

CREATE INDEX IF NOT EXISTS scm_publications_run
ON scm_publications(run_id, status);

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
