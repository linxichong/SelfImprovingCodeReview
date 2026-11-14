PRAGMA foreign_keys = ON;

ALTER TABLE review_runs
ADD COLUMN memory_snapshot_id TEXT
REFERENCES memory_snapshots(memory_snapshot_id) ON DELETE RESTRICT;

INSERT INTO schema_migrations(version, applied_at)
VALUES (3, CURRENT_TIMESTAMP);
