# Manual Development — Codex Dev implementation & self-review checklist v1.0

**Applies to:** Owner/TL-authorized **manual Codex development** of Agent Workflow V1, before handing off a Draft PR. This is **not** the automated V1 TARGET QC/MTP/independent Reviewer gate.

**Policy source of truth:** [Engineering Quality Standards](../conventions/engineering-standards.md), [AGENTS.md](../../AGENTS.md), the active task contract and AC. This checklist is a short execution aid; it does not grant new paths, host access or merge authority.

## Before editing

- [ ] Read `AGENTS.md`, `docs/INDEX.md`, active task/AC and only the relevant architecture/conventions. Distinguish DEVELOPMENT from TARGET.
- [ ] Verify `origin/main`, the authorized exact base SHA, worktree, branch, clean status and permitted changed paths. Do not change another worktree.
- [ ] Identify module owner, affected public contracts, call paths, security boundary, persistence effects and expected test cases.
- [ ] Choose the smallest workable design; avoid speculative abstractions, modules and unrequested refactors (KISS/YAGNI).

## Implement and verify

- [ ] **Correctness:** Map every applicable AC to behavior. Handle happy path, invalid input, boundaries, errors and backward compatibility.
- [ ] **Clean Code:** Prefer descriptive names, small cohesive functions, explicit types/exceptions, readable flow and Python 3.11+/PEP 8. Apply DRY to real duplicated knowledge; SOLID only where it improves boundaries or testability. Remove dead code, debug leftovers, avoid needless magic values and overengineering.
- [ ] **Ruff for changed Python:** Install the repo's `quality` extra, then run `python -m ruff check <changed Python files>` and `python -m ruff format --check <changed Python files>`. Use `python -m ruff format <authorized files>` to fix formatting before Draft PR. Do not auto-format the entire legacy tree.
- [ ] **Architecture:** Respect module ownership, dependency direction and existing interfaces. Do not add coupling or expand the changed-path allowlist.
- [ ] **Security:** Validate at trust boundaries; fail closed; protect credentials, paths, Docker/network permissions, SQL and subprocess arguments; use least privilege and no unjustified shell execution.
- [ ] **Reliability (when applicable):** Check timeouts, transactions/rollback, idempotency, concurrency, cleanup, restart/recovery and bounded resource use.
- [ ] **Tests:** Add meaningful positive, negative, boundary and regression checks for changed behavior. Keep fixtures isolated; do not hide failures with blanket mocks or unexplained skips.
- [ ] Run authorized scoped tests, architecture gate and full available suite; record real commands, exit codes, counts and environment. Mark unavailable Docker/OS/live tests **NOT VERIFIED**, never PASS.

## Mandatory self-review BEFORE Draft PR

- [ ] Inspect the **entire diff against the authorized base**, not only the files edited last. Confirm paths, imports, config, docs, migrations and tests match the task contract.
- [ ] Re-read for correctness, Clean Code, duplication, maintainability, security leaks, fragile tests and unintended external effects **even if all tests pass**.
- [ ] **Proactively fix every Clean Code defect found in self-review that is safely correctable within the approved task scope BEFORE opening the Draft PR.** Examples: misleading names, avoidable duplication, unnecessary complexity, dead code, unclear error handling or missing types. Re-run affected tests after fixes.
- [ ] Do **not** convert this rule into a broad style rewrite or speculative refactor. If a fix needs out-of-scope paths, changes a shared contract, increases risk, or conflicts with AC: **do not silently change it**; report the finding and request TL/Owner direction.
- [ ] For every relevant category record **PASS / FAIL / N/A / NOT VERIFIED**, with a short reason/evidence for N/A or NOT VERIFIED. Resolve actionable blocking FAIL findings before handoff; do not label skipped checks PASS.

## Handoff

- [ ] Commit/push the authorized task branch and open **one Draft PR** to `main`; never self-approve, merge or force-push.
- [ ] Report task ID, base/HEAD SHA, worktree/branch, changed paths, AC coverage, tests/CI evidence, self-review findings/fixes and residual risks in PR/handoff.
- [ ] Await ChatGPT Tech Lead review and Owner merge decision. Exact-HEAD CI and TL review remain mandatory after self-review.

**Rule:** “Tests PASS” is not the same as “Clean Code PASS”; self-review is the Dev's responsibility, not a replacement for TL review.
