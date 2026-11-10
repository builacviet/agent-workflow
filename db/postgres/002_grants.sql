-- Explicit runtime grants; NO runtime role owns tables or may perform DDL.
-- Re-run this after each reviewed schema migration to grant new objects.
REVOKE ALL ON ALL TABLES IN SCHEMA workflow FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA workflow FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE ON
  workflow.tasks, workflow.review_attempts, workflow.reviews,
  workflow.review_findings, workflow.task_events
TO mcp_user;
GRANT SELECT ON workflow.controller_ci_tasks TO mcp_user;
GRANT USAGE, SELECT ON SEQUENCE workflow.task_events_event_id_seq TO mcp_user;

-- Trusted Controller owns dispatch and the same service workflow transitions.
GRANT SELECT, INSERT, UPDATE ON
  workflow.tasks, workflow.review_attempts, workflow.reviews,
  workflow.review_findings, workflow.task_events,
  workflow.coder_dispatches, workflow.coder_runtime_evidence
TO controller_user;
GRANT SELECT ON workflow.controller_ci_tasks TO controller_user;
GRANT USAGE, SELECT ON SEQUENCE workflow.task_events_event_id_seq TO controller_user;

-- Receiver only stores and deduplicates authenticated notifications.
GRANT SELECT, INSERT ON workflow.webhook_inbox TO webhook_user;

-- CI watcher reads task identities and inbox; writes only its own CI ledger.
GRANT SELECT ON workflow.tasks, workflow.webhook_inbox TO ci_user;
GRANT SELECT, INSERT, UPDATE ON workflow.controller_ci_tasks TO ci_user;
GRANT SELECT, INSERT ON
  workflow.controller_ci_observations, workflow.controller_ci_processed
TO ci_user;
GRANT USAGE, SELECT ON SEQUENCE workflow.controller_ci_observations_id_seq TO ci_user;

-- Coder Worker, nginx, and ngrok do not have database login roles.
