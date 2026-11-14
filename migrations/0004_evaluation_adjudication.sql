PRAGMA foreign_keys = ON;

ALTER TABLE evaluation_matches ADD COLUMN adjudication_token TEXT;
ALTER TABLE evaluation_matches ADD COLUMN adjudication_reason TEXT;
ALTER TABLE evaluation_matches ADD COLUMN adjudicated_at TEXT;
ALTER TABLE evaluation_matches ADD COLUMN is_superseded INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX evaluation_matches_adjudication_token
ON evaluation_matches(adjudication_token)
WHERE adjudication_token IS NOT NULL;

CREATE TABLE evaluation_review_runs (
    evaluation_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    PRIMARY KEY (evaluation_id, run_id),
    FOREIGN KEY (evaluation_id) REFERENCES evaluation_runs(evaluation_id) ON DELETE RESTRICT,
    FOREIGN KEY (run_id) REFERENCES review_runs(run_id) ON DELETE RESTRICT
);

INSERT INTO schema_migrations(version, applied_at)
VALUES (4, CURRENT_TIMESTAMP);
