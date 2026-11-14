PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    policy_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'archived')),
    lock_version INTEGER NOT NULL CHECK (lock_version >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_repositories (
    project_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    repository_external_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, provider, repository_external_id),
    UNIQUE (provider, repository_external_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS project_ground_truth_sets (
    project_id TEXT NOT NULL,
    set_id TEXT NOT NULL UNIQUE,
    adopted_at TEXT NOT NULL,
    PRIMARY KEY (project_id, set_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
    FOREIGN KEY (set_id) REFERENCES ground_truth_sets(set_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS project_scm_changes (
    project_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    repository_external_id TEXT NOT NULL,
    change_external_id TEXT NOT NULL,
    adopted_at TEXT NOT NULL,
    PRIMARY KEY (project_id, provider, repository_external_id, change_external_id),
    UNIQUE (provider, repository_external_id, change_external_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT,
    FOREIGN KEY (provider, repository_external_id, change_external_id)
        REFERENCES scm_changes(provider, repository_external_id, change_external_id)
        ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS governance_audit (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id_hash TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason_hash TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS governance_audit_project_time
ON governance_audit(project_id, occurred_at);

-- 共有済みの 0006～0008 は版記録を持たないため、ここで補完する。
INSERT OR IGNORE INTO schema_migrations(version, applied_at)
VALUES
    (6, CURRENT_TIMESTAMP),
    (7, CURRENT_TIMESTAMP),
    (8, CURRENT_TIMESTAMP),
    (9, CURRENT_TIMESTAMP);
