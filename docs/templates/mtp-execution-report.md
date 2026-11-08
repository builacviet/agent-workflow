# MTP Execution Report — <task ID / PR HEAD> (template)

**POST-CODE QC EVIDENCE ONLY.** This file is **separate from the immutable Owner-approved Master Test Plan**. It records facts captured during a bounded, authorized QC execution. QC's summary is advisory; a trusted runner/operator supplies evidence and independent TL Reviewer evaluates it. Never edit the frozen MTP to record results.

## 1. Provenance and gate references

| Item | Actual value / evidence reference |
| --- | --- |
| Task ID, package ID, immutable package version/hash | `<from approved external manifest>` |
| Approved MTP ID/version and **SHA-256 of frozen file** | `<external manifest hash, rechecked before execution>` |
| PR URL, base SHA, **current reviewed HEAD SHA** | `<from trusted GitHub evidence>` |
| CI check name/event/tested SHA/conclusion and trusted provenance | `<exact HEAD push check + optional PR merge check>` |
| Independent TL code verdict and reviewed SHA | `<reference, verified matches HEAD>` |
| QC role/session, environment/image, isolation, test budget | `<trusted run configuration>` |
| Test harness/script version/hash, command allowlist and fixture ID | `<runner-captured artifacts, no hidden production state>` |
| Execution timestamp, runner/operator and artifact storage/digests | `<observed; redact tokens and personal secrets>` |
| Independent TL QC verdict / Owner merge decision | `PENDING` until independently recorded outside this report |

## 2. Per-test results and defects

| Test ID / AC | Expected from frozen MTP | Actual result | Status | Runner evidence / artifact hash | Defect |
| --- | --- | --- | --- | --- | --- |
| TC-01 / AC-01 | `<approved expected result>` | `<observed, not guessed>` | `PASS|FAIL|BLOCKED|NOT RUN` | `<exit, sanitized evidence>` | `<ID / none>` |

**Defect schema:** ID, severity, task/AC/test ID, exact tested SHA, reproduction/setup, expected vs observed, sanitized evidence, impact, owner and required verification.

**Flaky/unavailable/untestable:** Preserve first and repeated attempt results, fixtures, command, environment and run IDs. Never convert intermittent FAIL into unconditional PASS or select only a passing retry. Report `BLOCKED` on unreliable required evidence; a genuinely untestable required case needs clarified AC/MTP and Owner disposition. Extra exploratory checks are findings; they **cannot silently overwrite a frozen MTP case's expected result**.

## 3. Trusted evidence rules

- Prefer Controller/Worker/test-runner **captured** commands, exit status, capped/sanitized stdout/stderr, test report (e.g. JUnit XML) and artifact digest, bound to a tested snapshot/head SHA. Agent prose is not independent PASS evidence.
- Capturing raw stdout/stderr **may expose secrets**; do not publish it. Restrict access and redact/scan before any durable or user-visible report. If trusted runner capture/attestation is not yet implemented, explicitly mark `NOT VERIFIED` or use independently checked manual artifacts; never assert a future V1-02 feature is already deployed.
- Trusted Worker/Controller validates actual changed paths against the approved allowlist; QC/Reviewer do not self-assert a clean diff. Snapshots without Git metadata require an externally supplied tested snapshot manifest/digest.
- Re-query or trusted re-attestation of the current PR HEAD is required before accepting QC evidence and again before Owner merge reconciliation. Any HEAD or package/MTP mismatch makes old case results stale.

## 4. Execution summary (populate only after actual run)

| Item | Observed value |
| --- | --- |
| Total / PASS / FAIL / BLOCKED / NOT RUN | `<actual per-case counts>` |
| Bounded commands, exit codes, environment and sanitized artifact refs | `<actual>` |
| Findings / remaining risk / scope and N/A decisions | `<ids, rationale>` |
| QC handoff status | `QC_EVIDENCE_READY|BLOCKED`, not self-approval |
| Independent TL QC review | `PENDING|QC_REVIEW_PASS|QC_CHANGES_REQUIRED|BLOCKED` |
| Owner merge authorization | `PENDING` until independent Owner action |

**No silent result conversion:** A QC execution with any unresolved required `FAIL`, `BLOCKED` or `NOT RUN` cannot receive a full `QC_REVIEW_PASS`. Refer to [QC contract](../roles/qc.md), [TL Reviewer](../roles/tl-reviewer.md) and [MTP template](master-test-plan.md).
