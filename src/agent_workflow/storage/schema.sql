CREATE TABLE IF NOT EXISTS tasks (
 task_id TEXT PRIMARY KEY, metadata TEXT NOT NULL, repository TEXT NOT NULL,
 branch TEXT NOT NULL, base_sha TEXT NOT NULL, current_head_sha TEXT,
 requested_effort TEXT NOT NULL, require_verified_effort INTEGER NOT NULL,
 max_review_rounds INTEGER NOT NULL DEFAULT 2 CHECK(max_review_rounds=2),
 completed_review_rounds INTEGER NOT NULL DEFAULT 0 CHECK(completed_review_rounds BETWEEN 0 AND 2),
 status TEXT NOT NULL CHECK(status IN ('AWAITING_HANDOFF','READY_IN_REVIEW','IN_REVIEW','CHANGES_REQUESTED','APPROVED','ESCALATED')),
 created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS review_attempts (
 token TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
 expected_sha TEXT NOT NULL, reviewer TEXT NOT NULL, claimed_at REAL NOT NULL,
 expires_at REAL NOT NULL, status TEXT NOT NULL CHECK(status IN ('ACTIVE','EXPIRED','SUBMITTED')),
 submitted_payload TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_claim ON review_attempts(task_id) WHERE status='ACTIVE';
CREATE TABLE IF NOT EXISTS reviews (
 review_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
 round_number INTEGER NOT NULL CHECK(round_number BETWEEN 1 AND 2),
 base_sha TEXT NOT NULL, reviewed_head_sha TEXT NOT NULL, verdict TEXT NOT NULL,
 summary TEXT NOT NULL, requested_effort TEXT NOT NULL, actual_effort TEXT,
 verification_evidence TEXT NOT NULL, reviewer TEXT, reviewed_at REAL,
 imported_at REAL, prior_resolutions TEXT NOT NULL,
 UNIQUE(task_id, round_number), UNIQUE(task_id, reviewed_head_sha)
);
CREATE TABLE IF NOT EXISTS review_findings (
 review_id TEXT NOT NULL REFERENCES reviews(review_id), finding_id TEXT NOT NULL,
 task_id TEXT NOT NULL REFERENCES tasks(task_id), severity TEXT NOT NULL,
 acceptance_criterion TEXT NOT NULL, file_path TEXT NOT NULL, line INTEGER,
 description TEXT NOT NULL, suggested_correction TEXT NOT NULL,
 required_verification TEXT NOT NULL, PRIMARY KEY(task_id, finding_id)
);
CREATE TABLE IF NOT EXISTS task_events (
 event_id INTEGER PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(task_id),
 previous_status TEXT, next_status TEXT NOT NULL, action TEXT NOT NULL,
 sha TEXT, timestamp REAL NOT NULL, metadata TEXT NOT NULL
);
