DROP INDEX IF EXISTS review_runs_completed_idempotency;

CREATE UNIQUE INDEX review_runs_completed_idempotency
ON review_runs(idempotency_key)
WHERE status IN ('running', 'success');
