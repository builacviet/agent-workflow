# DOC-01 — Agent context navigation and documentation hygiene

**State:** Proposed documentation-only cleanup following CL-12.7 merge; **Owner agreed to organize docs for correct, token-efficient agent onboarding** on 2026-11-07. Implementation and merge are independently reviewed; no runtime change authorized.

## Goal

Make a fresh agent identify the correct current runtime versus target ADR, task requirements, evidence boundaries and relevant source files **without reading all docs or historical handoffs**.

## Scope and acceptance criteria

- **AC1 Entry:** `AGENTS.md` points to `docs/INDEX.md`, explicit authorized task, task-specific reading, and preserve all mandatory security/role boundaries. It no longer forces loading the 28 KB historical handoff for every change.
- **AC2 Navigation:** `docs/INDEX.md` has task-specific read routes (code, DB, executor, deployment, OAuth, review), current vs target vs historical categories, source-of-truth and version/evidence warning.
- **AC3 Handoff:** Preserve old `docs/handoff.md` content in versioned `docs/history/handoff-log.md`; supply a short canonical `docs/templates/handoff.md` and backward-compatible `docs/handoff.md` pointer. No content silently lost.
- **AC4 Compatibility:** `README.md` and `docs/architecture.md` direct agent to the new index and distinguish running Phase 3.1 from Owner-selected CL-12 target. Keep existing external/relative links valid; do not move approved ADRs.
- **AC5 Safety:** Documentation-only allowlist: `AGENTS.md`, `README.md`, `docs/INDEX.md`, `docs/architecture.md`, `docs/handoff.md`, `docs/history/handoff-log.md`, `docs/templates/handoff.md`, `docs/tasks/DOC-01.md`. No source, tests, SQLite, images, registry/CI, service, credentials, worker, Linux/Compose host operations.
- **AC6 Evidence:** Exact base and HEAD SHAs, changed-path audit, link-target check, content-preservation comparison and GitHub PR diff review. Test commands only if actually executed; do not claim runtime checks.

## Notes

This task does **not** rewrite historical ADRs or make the B3 Rootless Podman design operational. The P1 PR #14 preflight is a dated BLOCKED report; WSL2 follow-up evidence cannot be silently promoted to production proof. No deletion or merge is implicit.
