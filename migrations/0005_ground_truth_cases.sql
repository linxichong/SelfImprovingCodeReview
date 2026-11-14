PRAGMA foreign_keys = ON;

CREATE TABLE ground_truth_cases (
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

INSERT INTO ground_truth_cases(
    set_id, sequence, change_ref, created_by, verified_by, approved_by, status
)
SELECT
    set_id,
    ROW_NUMBER() OVER (PARTITION BY set_id ORDER BY change_ref),
    change_ref,
    MIN(created_by),
    MIN(verified_by),
    MIN(approved_by),
    CASE WHEN MIN(status) = 'published' THEN 'published' ELSE MIN(status) END
FROM ground_truth_issues
GROUP BY set_id, change_ref;

INSERT INTO schema_migrations(version, applied_at)
VALUES (5, CURRENT_TIMESTAMP);
