# Agent Workflow V1 — authorized implementation task contract

Use these fields in the **approved Notion task** or explicit Owner/TL assignment. This template does not grant permission by itself.

| Field | Required task-specific value |
| --- | --- |
| Task ID / goal / acceptance criteria (AC) | Observable behavior and done conditions |
| Responsible Coder and Tech Lead | Manual assignment; distinct **authenticated actor identity** on GitHub/Notion when configured; if not, mark identity UNVERIFIED and record actual connected actor |
| Approved repository + base branch / exact base SHA | Immutable reference for diff review |
| Worktree path / task branch | `.worktrees/<task>` / `codex/<task>` unless explicitly approved |
| Module ownership | Relevant modules; check `architecture/policy.toml` on boundary changes |
| **Suggested read entrypoints** | 2–3 source/test paths or symbols; hints, **not a read allowlist** |
| **Approved write allowlist** | Exact paths or narrowly defined globs, including tests, docs, renames/deletes; not a remote publish grant |
| Public APIs and cross-module dependencies | Expected compatibility or authorized change |
| DB/schema/migration and external effects | Explicitly permitted changes or NONE |
| Trust/security boundary impacts | Explicit impacts or NONE |
| Required verification | Relevant unit/negative/regression tests, DB integration, Ruff/CI as applicable |
| Dependencies and blockers | Approved prerequisite task/PR, if any |
| **Manual publish authorization** | Explicitly allow or disallow ordinary commit/push + Draft PR on assigned branch; no force-push or merge |
| Review / rollback considerations | Important failure modes and recovery |
| GitHub PR / Notion communication | Task links to PR; Notion Comments carry short status and PR/review links only, never duplicate technical evidence |
| Handoff format | Checked evidence in [Draft PR template](handoff.md), not a new task evidence file |

## Execution rules

- Coder can discover and **read** other accessible repo code/tests to understand a dependency; this does not widen **write** permissions.
- A shared contract, schema or important caller found outside the allowlist must be reported to Tech Lead; no unauthorized edits. Discovery ends once AC, affected consumers and tests are mapped, not after a repo-wide scan.
- Tech Lead/Owner authorizes scope changes in the task **before** extra edits. **Protected paths** — `AGENTS.md`, `.github/workflows/**`, CI/security check scripts and tests, `architecture/policy.toml`, `pyproject.toml` lint/security settings, `alembic/**`, shared public APIs, credentials and trust boundaries — require **explicit TL and Owner review even when in the allowlist**. Coordinate overlapping branches. If a PR changes its own checks, its successful CI result is not independent evidence of the unchanged gate.
- **Single source:** This Notion Task owns approved AC, scope/allowlist, base, publish rights, task status and human decisions. GitHub Draft PR owns implementation details, tests/CI, SHA-bound evidence and inline code-review findings. Notion Comments carry only questions, blockers, handoff/review status and links. **A comment or PR body cannot silently amend the approved task.**
- Source of technical truth: actual merged interfaces, diff, test commands/results, CI run IDs and tested SHA. Coder self-report does not prove a CI gate passed. Confirm the platform-authenticated actor before treating an agent's message as independent or as Owner approval; text role labels do not authenticate anyone.
- **Write, remote publish and merge are separate permissions.** Ordinary branch push and Draft PR are allowed only when the authorized task/role handoff grants them; otherwise provide a local handoff without remote writes. One Draft PR per authorized atomic task; only Owner may merge.
