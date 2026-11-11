# Agent implementation task contract

Copy this template into an Owner/Tech Lead-authorized task. Approval is required **before** an implementation Agent begins.

| Field | Required value |
| --- | --- |
| Task ID / purpose | |
| Acceptance criteria / done definition | |
| Authorized role and responsible Agent | |
| Base branch + exact base SHA | |
| Assigned worktree / task branch | |
| Module ownership | Use architecture/policy.toml |
| Allowed changed paths | Exact globs; no implicit shared files |
| Forbidden paths or external effects | Credentials, production DB, secrets, engines, other worktrees |
| Public contract changes | Explicit list or none |
| DB schema / migration changes | Explicit list or none |
| Worker adapter / trust impact | Explicit list or none |
| Dependencies / blocker PRs | |
| Required unit/integration/architecture/security tests | |
| Review & rollback plan | |

## Coordination

- For overlapping modules or shared contracts, Tech Lead serializes or explicitly coordinates PRs. No implicit shared-owner permission.
- Changes to architecture/policy.toml, AGENTS.md, shared APIs, migration scripts or credential boundaries always require Tech Lead and Owner review even when allowlisted.
- Runtime evidence, tests and CI must report **actual** SHA, commands, verdicts and limitations. Never infer success.
- Agent opens a Draft PR; Owner controls merge and cutover. This task template is a **human-reviewed contract**, not proof that automatic changed-path enforcement is implemented.
