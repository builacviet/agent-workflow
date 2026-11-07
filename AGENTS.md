# Project agent rules — entry point

**Read before each authorized task:** (1) this file, (2) [`docs/INDEX.md`](docs/INDEX.md), (3) the **currently approved task / acceptance criteria**, then only the relevant code, tests and specific linked document sections. **Do not load all ADRs or the historical handoff log by default.** Distinguish **CURRENT implementation** from **TARGET CL-12 design**; an accepted ADR does not prove implementation, security or production deployment.

## Scope, code and security invariants

- Work only on the Owner/Tech Lead-authorized task and changed-path allowlist. Record exact base SHA, target branch, worktree and head SHA; never edit unrelated projects, credentials, DBs, containers, volumes, tunnels or environments without an explicit task-specific permission. Prefer a dedicated branch/worktree; do not force-push or merge without Owner authorization.
- Target Python 3.11+, type hints, PEP 8, small functions and explicit errors. Preserve order-service `Decimal` rules: finite/nonnegative amounts, never implicit float conversions. Keep `order_service` and the WFC package independently importable; no casual cross-package dependency or unrequested framework rewrite.
- Require deterministic pytest coverage for behavior changes, invalid input and regression. At implementation handoff, run `python -m pytest -q` where possible and record the **actual** environment, command, exit code, skipped count and limitations. Never invent tests, CI checks, Linux security evidence or model effort.
- Use principle of least privilege. Do not expose Docker/Podman engine socket/API to MCP, Controller, Codex, Worker or monitoring merely for convenience; do not leak PEM/tokens, GitHub auth, WFC DB or arbitrary host paths into untrusted stages. Avoid unauthorized installs, engine commands, external effects or implicit DB migration.
- Read [`docs/architecture.md`](docs/architecture.md) for **as-built** code and [CL-12 architecture ADR](docs/adr/CL-12-architecture.md) for **target development architecture** only when relevant. In particular, Linux-host Rootless Podman Executor, GHCR and Grafana are **planned**, not proof of an active deployment.

## Role, review and handoff contract

- The current **Phase 3.1** POC still uses its existing Docker/Controller/worker implementation and its [runbook](docs/phase-3.1.md). Where current authorized tasks require it, Codex Coder implements/tests/commits/pushes its branch and records evidence. The *target* four-stage Prepare→Coder→Test→trusted Publish architecture becomes operational only after separately approved code and security tests; do not treat it as today's worker workflow.
- ChatGPT Tech Lead authorizes task scope and reviews exact branch/head diff, acceptance criteria, tests, trust boundary and risks; at most **two review rounds per task**. Tech Lead-authored docs require independent Owner acceptance, not self-approval. **Only Owner** approves merges, next implementation tasks and deployment/cutover.
- Use [review policy](docs/review-policy.md) and the [short task handoff template](docs/templates/handoff.md) when preparing or reviewing a PR. `docs/handoff.md` is a compatibility pointer; old task evidence lives in [history](docs/history/handoff-log.md), read only for audit/comparison.
- The previous Phase 2.2 no-Controller/scheduler scope had a narrowly authorized **Phase 3.1 exception**. Read the actual task and [Phase 3.1 AC](docs/tasks/PHASE-3.1.md) rather than interpreting old prohibitions as a ban on already authorized functionality. No Phase 3.2, production Executor, new CI/registry or Grafana deployment is implied.

**Missing, contradictory or stale context?** Follow the [documentation index](docs/INDEX.md), recheck current source, accepted ADR/PR SHA and active Owner decision; report uncertainty rather than guessing or expanding the task.
