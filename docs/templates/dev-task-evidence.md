# Dev completion checklist — <TASK-ID>

> Copy this file to `docs/evidence/<TASK-ID>-dev-checklist.md` **inside the task's approved changed-path allowlist**. This is a **self-report**, not independent test evidence. Complete it **after implementation and self-review, before Draft PR handoff**. Never put credentials, secret values, full DSNs or personal data in this file.

## Identity and scope

- Task / Notion AC: <TASK-ID> / <link>
- Authorized BASE SHA: <40-hex SHA>
- Tested implementation commit SHA **before this evidence-file commit**: <40-hex SHA or NOT VERIFIED>
- Worktree and branch: <path> / <branch>
- Allowed paths (including this file): <exact paths>
- Actual changed paths (from `git diff --name-only <BASE>...HEAD`): <actual paths>
- Review environment: <OS / Python / DB / tools; write NOT VERIFIED if unknown>

> **Self-reference rule:** Committing this file changes HEAD. Do not claim its content proves testing of its own final commit. Report final PR HEAD and exact-HEAD CI run links in the PR description after push; Tech Lead verifies them independently.

## Checked items

Each `[x]` means **I inspected this item**, not that it passed. Every item must show one explicit verdict: `PASS`, `FAIL`, `N/A`, or `NOT VERIFIED`, plus concrete evidence or a reason. Do not mark `PASS` based on expectation, another agent's assertion or a skipped command. Keep `[ ]` for not inspected. Do not hand off with unresolved blocking `FAIL` or without required evidence.

- [ ] **AC / public contract:** Verdict: <...>; Evidence: <AC IDs, tests, diff ranges>
- [ ] **Changed-path allowlist / base / worktree:** Verdict: <...>; Evidence: <git commands, exact paths>
- [ ] **Clean Code:** Verdict: <...>; Evidence: <names/types/duplication/dead code inspected and actual fixes or no finding>
- [ ] **Architecture / imports / dependency direction:** Verdict: <...>; Evidence: <policy gate command and result, or N/A reason>
- [ ] **Security / trust boundaries / secrets / external effects:** Verdict: <...>; Evidence: <negative tests, diff inspection, or N/A reason>
- [ ] **Persistence / transaction / timeouts / concurrency:** Verdict: <...>; Evidence: <live DB tests, rollback/lock/idempotency, or N/A reason>
- [ ] **Positive, negative and regression tests:** Verdict: <...>; Evidence: <specific test names, counts, failures and skips>
- [ ] **Ruff lint / format / architecture gate:** Verdict: <...>; Evidence: <actual commands and exit codes, or N/A reason>
- [ ] **Full available test suite and environment limitations:** Verdict: <...>; Evidence: <commands, exit, pass/skip, unrun Docker/PG checks>
- [ ] **Diff self-review after fixes:** Verdict: <...>; Evidence: <exact diff reviewed, concrete corrections, rerun evidence>
- [ ] **Task-specific PostgreSQL convention (if applicable):** Verdict: <...>; Evidence: <Session owner, ORM/Core/raw SQL reason, timeout/isolation parity, or N/A>
- [ ] **Documentation/migration/operation impact:** Verdict: <...>; Evidence: <changed docs/migrations or N/A reason>

## Commands actually executed

| Command (exact) | Environment / target | Exit code | Actual result / count | Evidence location |
| --- | --- | --- | --- | --- |
| <command or NOT RUN> | <env> | <0/nonzero/NOT RUN> | <real summary, skip count> | <CI or local log path / output excerpt> |

Never convert a reported local command to an independently verified CI result. Never mark skipped or unavailable live PostgreSQL/Docker tests PASS.

## Findings and remaining risks

- Fixed during self-review: <finding → files/lines changed → retest, or NONE observed>
- Open findings: <severity + file/line + impact + mitigation, or NONE observed>
- `NOT VERIFIED` / `N/A` items and reasons: <explicit list>
- Explicitly forbidden/unmodified scope: <relevant paths/effects>

## Handoff declaration

- [ ] I checked that this evidence file is included in the authorized allowlist and PR diff.
- [ ] I reported observed results only; I have **not** claimed unexecuted tests or self-approved merge.
- [ ] I will put the final PR HEAD SHA, push CI, PR CI and remaining limitations in the PR handoff **after** pushing this file.

**Status:** <READY FOR TL REVIEW | BLOCKED, with reason>

**Owner-only merge.** Tech Lead reviews this file against the actual diff, tests, logs and CI; its checkboxes are claims to verify, not proof by themselves.
