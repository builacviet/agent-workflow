# Master Test Plan — <task ID> (template)

**PRE-DEV TEST DESIGN ONLY.** This file contains **no execution result, approval record or content hash of itself**. QC authors it before Dev; independent TL Reviewer reviews it; Owner approves its exact immutable bytes as part of the complete Planning Package. Post-code results belong **only** in the separate [MTP Execution Report](mtp-execution-report.md).

## 1. Frozen design identity (no self-reference)

| Item | Value |
| --- | --- |
| Planning package ID / proposed version | `<id / version>` — package manifest hash is recorded **outside this file** |
| Atomic task ID and AC source/revision | `<id / exact approved source reference>` |
| QC policy | `required` (if `skip`, document Owner-approved reason instead of creating MTP) |
| MTP ID and version | `<id / version>` — **no MTP content-hash field here** |
| Changed-path scope / prohibited effects | `<approved allowlist and denylist>` |
| Test environment, bounded resources and fixtures | `<synthetic/disposable, tool and network boundaries, max time>` |
| QC author/session identifier | `<non-secret identity and session ID>` |
| Risks, dependencies and clarification requests | `<blocking questions or none>` |

**Freeze/approval protocol:** Once the proposed MTP and package are complete, a trusted Controller (when implemented) computes **SHA-256 of the exact raw UTF-8 bytes of this finalized file**, and a separately defined immutable Planning Package manifest binds that hash to the Plan/AC/task artifacts. **Neither resulting hash is inserted into a file included in its own hash.** In manual bootstrap, the Owner/operator computes and records the file SHA-256 and package-manifest hash *outside* these approved files; the independent reviewer checks the submitted bytes; Owner approval records their exact identities. This is a design/operating procedure for V1-02, **not a claim that a Controller hash registry exists today**. Changing any approved byte requires a new version/hash, review and Owner decision under [product change control](../v1-scope-and-gates.md#5-change-control-evidence-ownership-and-rework-decisions).

## 2. Acceptance Criteria traceability

| AC ID | Observable requirement | Test case IDs | Positive/negative/edge/security coverage | Gaps, risk or N/A reason |
| --- | --- | --- | --- | --- |
| AC-01 | `<assertable behavior>` | `TC-01, TC-02` | `<scenarios>` | `<none or open question>` |

All approved ACs must appear. A critical gap/ambiguous expected result makes this **draft BLOCKED** until clarified.

## 3. Test case (repeat per case; keep low-risk cases concise)

### TC-01 — <specific expected behavior>

- **AC, type and risk:** `AC-01 / positive|negative|boundary|integration|security|regression / P0..P2`
- **Setup, synthetic fixtures and environment:** `<minimal reproducible preconditions; isolation>`
- **Steps / harness contract:** `<inputs/actions; stable test ID, expected interface, command if known>`
- **Expected assertions and error behavior:** `<specific observable result / fail-closed status>`
- **Evidence required at post-code execution:** `<runner exit, bounded sanitized output, artifact digest and tested HEAD>`
- **Cleanup and safety constraints:** `<only disposable state; no production effects>`

The AC/test semantics and expected results **must be specified before Dev**. A test command may be unknown before implementation; record the **test ID, exact behavior, input fixtures and required harness contract** now. Post-code independently implemented harnesses/commands are versioned and hashed as **execution artifacts** and must not silently modify expected MTP behavior. If implementation requires material test-plan changes, stop and request a revised MTP/package through Owner approval.

## 4. Universal and conditional safety coverage

**Universal (for every applicable task):**
- [ ] AC mapping includes positive/negative and boundary/error coverage proportionate to risk.
- [ ] Test fixture and environment are reproducible and isolated; expected results are independently observable.
- [ ] Untrusted inputs and unsafe external effects are restricted; logs/evidence do not expose secrets.
- [ ] Package/MTP identities and, during post-code QC, current PR HEAD are verified externally.
- [ ] Skipped or untestable cases carry `N/A + reason` or an explicit BLOCKED finding, never fabricated PASS.

**Conditional for auth/agent/Controller/CI tasks; mark N/A with reason elsewhere:**
- [ ] Unknown role, missing/unsupported model or effort, and privilege escalation fail closed.
- [ ] Role prompts, mounts, source contexts and secrets remain isolated.
- [ ] Wrong approved package hash or stale/failed/missing exact-HEAD CI cannot advance a gate.
- [ ] Duplicate/reordered events, retry, timeouts, release and recovery remain safe.
- [ ] No credential, active DB, Docker socket or auto-merge authority enters an untrusted role.

## 5. Gate order (reference only; authoritative contract is V1 scope)

1. QC creates/finalizes this **design-only** document.
2. Independent TL reviews proposed Plan/AC/MTP, including exact bytes and coverage.
3. Owner approves an immutable Planning Package ID/version/hash with a referenced MTP SHA-256.
4. Dev → trusted Worker publishes Draft PR → named GitHub CI PASS on **current PR HEAD** → independent TL code review PASS.
5. QC executes this frozen MTP against reviewed HEAD in an isolated environment, recording actual results in the **separate** [MTP Execution Report](mtp-execution-report.md) with runner evidence.
6. Independent TL reviews QC execution evidence; Owner alone approves/merges PR. Changes in code HEAD or approved package/MTP invalidate dependent evidence.

**Failure policy:** Ambiguous AC, missing approvals, stale hashes/SHA, incomplete critical test coverage or uncertain external effects ⇒ `BLOCKED` with an explicit clarification/findings owner. No MTP case is `PASS` during pre-Dev authoring. Refer to [V1 planning, QC and approval gates](../v1-scope-and-gates.md#2-planning-gates--once-per-approved-package) and [product change-control rules](../v1-scope-and-gates.md#5-change-control-evidence-ownership-and-rework-decisions).
