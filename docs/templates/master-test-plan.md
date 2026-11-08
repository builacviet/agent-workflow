# Master Test Plan — <task ID> (template)

**PRE-DEV TEST DESIGN ONLY.** Do not report test PASS before executing approved tests on a reviewed exact HEAD. QC authors; **independent TL Reviewer** reviews; Owner approves the **full versioned Planning Package**, including MTP, before Dev starts.

## 1. Identity and gate references

| Item | Value |
| --- | --- |
| Planning package ID / version / hash / status | `<id / draft version / hash / DRAFT|APPROVED>` |
| Task ID / AC source and revision | `<id / exact Notion URL or task file revision>` |
| QC policy | `required` (if `skip`, explain reason instead of authoring MTP) |
| MTP ID / version / immutable content hash | `<id / version / hash pending approval>` |
| Approved changed-path scope / prohibited effects | `<allowlist / denylist>` |
| Test environment / fixtures / run budget | `<disposable, synthetic, timeouts, tool and network allowlist>` |
| QC author / independent TL reviewer | `<identity / pending>` |
| TL MTP verdict / evidence | `PENDING / PLAN_REVIEW_PASS / CHANGES_REQUESTED / BLOCKED` |
| Owner package approval ID / version / hash | `PENDING — not authorized for Dev` |
| Post-code PR / **current reviewed HEAD SHA** | `NOT APPLICABLE before Dev` |

## 2. Acceptance Criteria traceability (required for every AC)

| AC ID | Intended observable behavior | Test case IDs | Positive/negative/boundary/security/regression | Coverage gaps and risk |
| --- | --- | --- | --- | --- |
| AC-01 | `<specific assertion>` | TC-01, TC-02 | `<types>` | `<none or reason>` |

No AC without a test or explicit gap; unresolved critical gaps block package review.

## 3. Repeat for every test case

### TC-01 — <behavior and expected result>

- **Mapped AC / type / priority:** `AC-01 / positive|negative|boundary|integration|security|regression|smoke / P0..P2`
- **Purpose and threat/failure condition:** `<what this detects>`
- **Fixtures & preconditions:** `<synthetic data, pinned image, isolated workspace, allowed credentials only>`
- **Environment and command:** `<reproducible steps, bounded runtime, no production side effects>`
- **Steps:** `<exact setup, inputs, operation and cleanup>`
- **Expected behavior:** `<concrete assertions, exit code, fail-closed outcome for invalid inputs>`
- **Evidence to retain:** `<command, sanitized logs, test report file, SHA, result and timestamp>`
- **PRE-DEV result:** `NOT RUN — test design only`
- **POST-CODE result:** `NOT RUN|PASS|FAIL|BLOCKED`; `<tested current reviewed HEAD; actual evidence reference>`
- **Defect / dependency / uncertainty:** `<link, owner, severity>`

## 4. Security and negative-test coverage

- [ ] Missing/invalid/unsupported role, model and effort fail closed with no silent fallback.
- [ ] Caller cannot select its own role or increase tool/host/secret permissions.
- [ ] Dev/QC/TL have separate prompts, contexts, filesystem mounts and ephemeral workspaces.
- [ ] Untrusted roles cannot read GitHub App PEM, worker tokens, live WFC SQLite, Controller Docker socket or production network resources.
- [ ] Wrong package ID/version/hash, unapproved MTP, stale PR HEAD or missing/failing CI blocks progression.
- [ ] Failed tests preserve reproducible evidence; operational uncertainty is BLOCKED, not PASS.
- [ ] Repeatable positive, negative, edge and regression paths for each relevant AC; mark N/A with reason.
- [ ] No auto-merge, approval impersonation, direct production writes, or hidden test-data mutation.

## 5. Gate order and final execution report

1. **QC PRE-DEV:** author draft MTP; all cases `NOT RUN`.
2. **Independent TL planning review:** review Plan/AC/MTP; version fixes if necessary.
3. **Owner planning gate:** explicitly approve complete immutable package version/hash.
4. **Dev → Worker → CI → independent TL code review:** exact PR HEAD, CI PASS, code review PASS; code review max two failed rounds.
5. **QC POST-CODE:** run approved MTP on exactly reviewed HEAD; evidence case-by-case; TL validates QC evidence.
6. **Owner per-PR merge gate:** Owner approves actual merge, Controller later independently verifies GitHub merge.

| Execution summary (fill **only** after actual run) | Value |
| --- | --- |
| Approved package/MTP version + hashes | `NOT RUN` |
| PR URL / current exact SHA | `NOT RUN` |
| Exact-SHA CI named check and conclusion | `NOT RUN` |
| Independent TL code review round/verdict | `NOT RUN` |
| Total / PASS / FAIL / BLOCKED / NOT RUN test cases | `<counts from actual execution>` |
| Executed commands, safe logs, environment and exit codes | `NOT RUN` |
| QC handoff / independent TL QC verdict | `PENDING / PENDING` |
| Owner merge decision / merge SHA | `PENDING / NOT MERGED` |

**Failure policy:** missing approvals, outdated hash/SHA, unsafe fixtures, missing CI, uncertain external effects or insufficient traceability ⇒ `BLOCKED` with owner and reason. Never mark tests PASS from design alone. See [QC role contract](../roles/qc.md) and [V1 gate contract](../v1-scope-and-gates.md).
