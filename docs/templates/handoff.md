# Manual Coder — Draft PR handoff & checked self-review

**Use this template in the PR description.** Do not create a separate `docs/evidence/*.md` file for every task. This is the Coder's self-report; Tech Lead verifies against the task, actual diff, tests and GitHub CI. Never include credentials, raw DSNs or full secret-bearing logs.

## Task identity and scope

- Task ID / approved Notion AC:
- Authorized base SHA / branch / worktree:
- Tested code SHA / final pushed PR HEAD SHA:
- Draft PR URL:
- Approved **write allowlist**:
- Separate **remote publish authorization** (ordinary task-branch push + Draft PR, or NONE):
- Actual added/modified/deleted/renamed paths (compare approved base and PR base):
- Read-only context / important shared contracts checked (brief):

## AC implementation and test mapping

| AC | Status: PASS / FAIL / N/A / NOT VERIFIED | Implementation + concrete test / evidence |
| --- | --- | --- |
| AC-… | | |

A `PASS` is valid only if supported by actual inspection or test evidence. Explain every `N/A` or `NOT VERIFIED`; required unverified checks must be escalated. Do not turn a skipped test into PASS.

## Checked self-review (complete after coding)

Each `[x]` means **inspected**, not automatically passed. Give a status and a concise file/test/command link or explicit reason for each relevant item. For a small/docs-only task, mark an irrelevant group `N/A` with one short reason instead of writing a lengthy report.

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
- Exact PR HEAD and CI scope: [push run on HEAD vs pull_request merge ref, workflow jobs actually run, whether checks covered the **whole PR diff** or only the latest commit].
- **Protected files / CI self-modification:** [NONE or paths and independent review/validation; a green run from a modified CI/policy is not automatically trustworthy].
- External effects / shared DB / credentials / containers touched: [NONE or authorized facts].
- Verdict requested: **Ready for Tech Lead review** / **BLOCKED** [reason].

**Ownership:** Coder may commit/push and open a Draft PR on the assigned task branch **only when the approved task/role grant permits remote publication**. Tech Lead independently checks evidence at the reviewed HEAD; a newer HEAD requires fresh review/CI. **Owner alone merges.**
