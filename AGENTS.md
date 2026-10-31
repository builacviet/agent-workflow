# Project rules

- Target Python 3.11+. Use type hints in application code, PEP 8 naming, small functions and explicit errors. Monetary inputs are finite, nonnegative `Decimal` values; never convert floats implicitly.
- Keep runtime dependencies empty. No database, HTTP server, external services, MCP integration, scheduling or orchestration in Phase 1.
- Add deterministic pytest coverage for behavior changes, invalid inputs and regressions. Run `python -m pytest -q` before every implementation handoff; record the actual command and outcome. Never invent evidence.
- Before implementing a task, read this file, `docs/architecture.md`, `docs/review-policy.md`, `docs/handoff.md` and the authorized task. TASK-001 is proposed and must not be implemented until ChatGPT Tech Lead authorizes it.
- Baseline bootstrap may use `main`. Future tasks require a dedicated branch and Git worktree, e.g. `git worktree add ../techlead-review-poc-TASK-001 -b task/TASK-001 main`. Keep the main checkout clean; do not modify unrelated repositories.
- Record the base SHA before changes. Make focused commits, push the task branch to the private GitHub repository, and record the exact head SHA. Do not force-push shared branches or merge task branches without explicit authorization.
- Complete the handoff fields with changes, files, tests, risks, branch, base/head SHA and limitations. A commit cannot embed its own SHA; use a separate handoff message or resolve the committed evidence anchor described in `docs/handoff.md`.
- Codex is the Coder/Environment Builder: implement, test, commit, push and report evidence. Codex must never approve its own work or assign final approval to another agent.
- ChatGPT Tech Lead owns task authorization, acceptance-criteria review, exact-SHA approval and escalation. Maximum two Tech Lead review rounds per task; follow `docs/review-policy.md`. Requested effort is task-level `instant`, `medium` or `high`; never claim unverified model/reasoning settings.
