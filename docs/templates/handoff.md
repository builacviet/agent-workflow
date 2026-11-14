# Manual Coder — Draft PR handoff & checked self-review

**Use this template in the PR description.** Do not create a separate `docs/evidence/*.md` file for every task. This is the Coder's self-report; Tech Lead verifies against the task, actual diff, tests and GitHub CI. Never include credentials, raw DSNs or full secret-bearing logs.

## Task identity and scope

- Task ID / approved Notion AC:
- Authorized base SHA / branch / worktree:
- Tested code SHA / final pushed PR HEAD SHA:
- Draft PR URL:
- Approved **write allowlist**:
- Actual added/modified/deleted/renamed paths (compare approved base and PR base):
- Read-only context / important shared contracts checked (brief):

## AC implementation and test mapping

| AC | Status: PASS / FAIL / N/A / NOT VERIFIED | Implementation + concrete test / evidence |
| --- | --- | --- |
| AC-… | | |

A `PASS` is valid only if supported by actual inspection or test evidence. Explain every `N/A` or `NOT VERIFIED`; required unverified checks must be escalated. Do not turn a skipped test into PASS.

## Checked self-review (complete after coding)

Each `[x]` means **inspected**, not automatically passed. Give a status and a concise file/test/command link or explicit reason for each relevant item.

- [ ] **Scope & diff:** [status]; [base-vs-HEAD diff inspected, exact changed paths, no unauthorized edits].
- [ ] **Correctness & contracts:** [status]; [AC mapping, boundary/error cases, existing API and regression impact].
- [ ] **Clean Code & architecture:** [status]; [naming/types, duplication, modules/dependencies, refactors fixed or deferred].
- [ ] **Security & side effects:** [status]; [input/SQL/path/secret/permission review and negative tests, or N/A reason].
- [ ] **Persistence/concurrency/recovery (if relevant):** [status]; [transaction owner, rollback, isolation/deadline, idempotency/lock lifetime, or N/A reason].
- [ ] **Tests, lint & CI:** [status]; [actual scoped/full tests, Ruff, required CI jobs and gaps].

**Proactive fixes from self-review:** [within-scope fixes and tests re-run; or NONE]. **Out-of-scope findings / unresolved risks:** [paths, risk and requested TL decision; or NONE].

## Commands and independently checkable evidence

| Environment | Command / CI job link | Tested SHA or run/attempt | Exit code / conclusion | Passed / failed / skipped; limitation |
| --- | --- | --- | --- | --- |
| Local | | | | |
| GitHub Actions | | | | |

- Did not run / unavailable: [`NOT VERIFIED` + reason, not PASS].
- Exact PR HEAD and CI scope: [push vs pull_request event, workflow jobs actually run, whether checks covered the **whole PR diff** or only the latest commit].
- External effects / shared DB / credentials / containers touched: [NONE or authorized facts].
- Verdict requested: **Ready for Tech Lead review** / **BLOCKED** [reason].

**Ownership:** Coder commits/pushes only on the approved task branch and leaves PR Draft. Tech Lead independently checks evidence at the reviewed HEAD; a newer HEAD requires fresh review/CI. **Owner alone merges.**
