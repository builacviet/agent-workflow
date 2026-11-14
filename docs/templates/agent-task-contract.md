# Agent Workflow V1 — authorized implementation task contract

Use these fields in the **approved Notion task** or explicit Owner/TL assignment. This template does not grant permission by itself.

| Field | Required task-specific value |
| --- | --- |
| Task ID / goal / acceptance criteria (AC) | Observable behavior and done conditions |
| Responsible Coder and Tech Lead | Manual development assignment |
| Approved repository + base branch / exact base SHA | Immutable reference for diff review |
| Worktree path / task branch | `.worktrees/<task>` / `codex/<task>` unless explicitly approved |
| Module ownership | Relevant modules; check `architecture/policy.toml` on boundary changes |
| **Suggested read entrypoints** | 2–3 source/test paths or symbols; hints, **not a read allowlist** |
| **Approved write allowlist** | Exact paths or narrowly defined globs, including tests, docs, renames/deletes |
| Public APIs and cross-module dependencies | Expected compatibility or authorized change |
| DB/schema/migration and external effects | Explicitly permitted changes or NONE |
| Trust/security boundary impacts | Explicit impacts or NONE |
| Required verification | Relevant unit/negative/regression tests, DB integration, Ruff/CI as applicable |
| Dependencies and blockers | Approved prerequisite task/PR, if any |
| Review / rollback considerations | Important failure modes and recovery |
| Handoff format | Checked evidence in [Draft PR template](handoff.md), not a new task evidence file |

## Execution rules

- Coder can discover and **read** other accessible repo code/tests to understand a dependency; this does not widen **write** permissions.
- A shared contract, schema or important caller found outside the allowlist must be reported to Tech Lead; no unauthorized edits. Discovery ends once AC, affected consumers and tests are mapped, not after a repo-wide scan.
- Tech Lead/Owner authorizes scope changes in the task **before** extra edits. Coordinate overlapping branches, especially shared standards, architecture policy, migrations and credentials.
- Source of truth: approved task + actual merged interfaces. Evidence: current diff, actual commands/results, CI run IDs and tested SHA. Coder self-report does not prove a CI gate passed.
- One Draft PR per atomic task; only Owner may merge.
