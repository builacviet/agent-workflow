# Master Test Plan — V1-02 Role Profiles from Deployment Environment

**PRE-DEV TEST DESIGN ONLY.** Draft design disposition: **READY_FOR_INDEPENDENT_MTP_REVIEW** against the TL Planner's proposed Plan/AC/DoD. Owner package approval and separate online execution authorization remain pending. All cases are **NOT RUN**. Post-code results belong in the separate [MTP Execution Report](../templates/mtp-execution-report.md).

## 1. Frozen design identity (no self-reference)

| Item | Value |
| --- | --- |
| Planning package ID / proposed version | `V1-02 / v1.2-draft` is the proposed package lineage/version. External manifest and Owner approval remain pending. |
| Atomic task ID and AC source/revision | `V1-02`, [Plan/AC/DoD v1.2-draft](https://github.com/builacviet/agent-workflow/blob/cb454e4d0f488dd361010f4b027cddf8fb132eeb/docs/tasks/V1-02-plan.md) at PR #29 commit `cb454e4d0f488dd361010f4b027cddf8fb132eeb`. This Git revision is the normative draft for AC-01–06. [Round 1/2 revision handoff](https://github.com/builacviet/agent-workflow/pull/28#issuecomment-6730939277). |
| Repository / design baseline | `builacviet/agent-workflow`, `main@96f5d523814f94ebfec244c66e38d400e2144509` |
| QC policy | `required` given the role-selection, authorization and credential boundaries |
| MTP ID / version | `V1-02-MTP / 0.4-draft` |
| Changed-path scope / prohibited effects | PRE-DEV QC writes only this MTP. The exact candidate Dev paths are pinned in Plan §3 and tested in §3 below. POST-CODE QC writes only disposable harness/logs and the separate execution report. Final path authorization remains part of package freeze. No production or credential effects. |
| Environment, bounds and fixtures | Offline credential-free disposable fixtures by default, including synthetic auth and deterministic Docker fault injection. A separately authorized real isolated Docker lifecycle check is required. Separately authorized online smoke limits are 180 seconds and 65,536 bytes of sanitized retained output per role, one attempt per role. Cleanup/reconciliation has a separate 60-second grace target. |
| QC author/session identifier | `Codex QC / qc-predev-v1-02-mtp-20261109-01` (artifact-local author session label) |
| Dependencies and risks | V1-01 feasibility, merged role contracts from PR #27, legacy Phase 3.1 boundaries, and PG1-01–06 oracles from pinned Plan v1.2. Formal planning round 1/2 returned `CHANGES_REQUESTED`. Next review is round 2/2 on the same lineage. See §2. |

An independent trusted operator records the SHA-256 of the final raw UTF-8 MTP bytes and binds it to the frozen Plan, AC and task in the external package manifest. Package and MTP digests and Owner authorization are recorded externally. Byte changes after approval require a new version/hash and change control. [QC role](../roles/qc.md) · [Engineering standards](../conventions/engineering-standards.md)

## 2. Acceptance Criteria traceability

| AC ID | Observable requirement | Test case IDs | Coverage | Gap / risk |
| --- | --- | --- | --- | --- |
| AC-01 | Explicit role mode validates all three canonical pairs at trusted startup against pinned CLI/provider capabilities and verifies effective settings independently. | CFG-01–04, ADP-01, SMK-01–03, EVD-02, REG-01 | Selected/inactive pairs, unsupported config, override rejection, legacy separation and no fallback | P0 silent fallback or unverifiable settings. |
| AC-02 | Trusted launcher selects one of six profiles with phase-specific `DRAFT_PLANNING` or `APPROVED_EXECUTION` authority, and enforces actual capabilities. | CFG-04, ISO-01–04, PERM-01–03, SEC-01–02, REG-04(A), CTX-02, CTX-04 | Valid pending-Owner planning, execution-only approval, replay rejection, profile isolation and positive/negative permission probes | P0 cross-role access, privilege escalation or circular planning gate. |
| AC-03 | Prefer one pinned Codex image and prove unique attempt ownership, fenced cleanup and quiescence under late create. | IMG-01, LIFE-01 | Digest, engine/ID ownership, timeout/grace boundaries, delayed create and authorized real Docker evidence | P1 lifecycle verification, P0 if unowned cleanup or replay is possible. |
| AC-04 | Prove same-role credential isolation and collect three authorized bounded headless smokes with effective telemetry. | SEC-01–02, LIFE-01, SMK-01–03, EVD-01–02, ADP-02, CTX-04 | Equivalent auth ingress, provider and tool controls, denied redirect, telemetry and evidence | P0 credential exposure or unverifiable execution. |
| AC-05 | Bind exact authorization, Plan/MTP, task/base/HEAD, snapshot and path scope. Preserve Phase 3.1 and separately verify operator gates and legacy WFC states. | ISO-04, PERM-01/03, EVD-02, REG-01–04, CTX-02 | Trusted launch vs manual verification, exact path list, legacy two-round transitions and CI/TL evidence | P0 stale/spoofed evidence or scope escalation. |
| AC-06 | Verify canonical v1 WorkerBundle/WorkerResult, pinned runtime substitution, prelaunch TOCTOU defense and per-dispatch/handoff freshness. | ADP-01–02, CTX-01–04, SMK-01–03, EVD-02 | Strict schema, byte bounds, trusted normalization, controlled discovery, identity, freshness and no fallback | P0 unauthorized inputs, hidden instructions or spoofed success. |

The draft DoD is covered by startup and permission assertions (CFG/ISO/PERM/SEC), image and three-role smoke checks (IMG/LIFE/SMK/EVD), exact-HEAD, changed-path and legacy compatibility checks (REG), and WorkerBundle, adapter substitution, context freshness and normalized-result checks (ADP/CTX). The separate approval, code review, QC evidence review and merge gates remain prerequisites or follow-on decisions as applicable.

### Planning Gate round 1 finding traceability

| Finding | Cases in this revision | Oracle for independent review |
| --- | --- | --- |
| PG1-01 P0 | ISO-01, PERM-03, REG-04(A), CTX-02 | Pending-Owner draft planning succeeds with distinct identities and outputs. Draft replay into execution or forged authority is denied before launch. |
| PG1-02 P0 | SEC-01–02, SMK-01–03, CTX-04 | Equivalent synthetic auth ingress succeeds for the CLI and denies same-role tool access/export. Actual policy and denied redirect evidence gate credentialed smoke. |
| PG1-03 P0 | ADP-01–02, CTX-01–04, SMK-01–03, EVD-02 | Strict v1 schemas, canonical hashes, exact byte boundaries, prelaunch mutation rejection, observed Codex delivery and attempt-bound telemetry. |
| PG1-04 P1 | IMG-01, LIFE-01 | Attempt/engine/container ownership, 60-second grace, late create and dispatch fence, plus real isolated Docker evidence. |
| PG1-05 P1 | REG-02, REG-04(B) | Concrete legacy WFC transitions and no state change on reject. Named exact-HEAD CI cannot substitute for independent TL verdict. |
| PG1-06 P1 | PERM-01, REG-04, CTX-02 | Exact per-path Dev scope, separate QC output, pinned Git artifacts and external raw-byte SHA-256. |

### As-built comparison at the design baseline

| AC | Static source evidence | V1-02 verification target |
| --- | --- | --- |
| AC-01 | [Controller startup](../../src/agent_workflow/orchestration/controller.py#L83) loads JSON and calls `validate_config`. [Runner validation](../../src/agent_workflow/runners/coder_runtime.py#L72) requires one nonblank `model`. The runner sets `WFC_CODEX_MODEL` only for Code, and the [entrypoint](../../docker/coder_entry.py) supplies it to `codex exec` without a per-role reasoning-effort argument. | Exercise the normal trusted startup path for three independent pairs and show the selected pair reaches the actual CLI boundary. |
| AC-02 | [Runner](../../src/agent_workflow/runners/coder_runtime.py#L237) constructs argv, gives Prepare/Publish the PEM mount, Code the source and POC auth volume, and Test the source with `--network=none`. [Execution path](../../src/agent_workflow/runners/coder_runtime.py#L310) uses a fixed Code prompt prefix plus assignment context. [Worker](../../src/coder_worker/main.py) owns Git/GitHub effects. | Verify three trust-boundary profiles and both denied and allowed operations. Current source separation is a regression baseline. The host Controller has broad engine authority and source inspection does not verify deployed mounts or egress. |
| AC-03 | [Dockerfile](../../docker/coder.Dockerfile) pins the base image and Codex CLI. The Phase 3.1 runner passes one configured image through the four stages. [Compose](../../compose.controller.yaml) defines MCP and an optional tunnel. | Inspect image identity and actual lifecycle for three ephemeral Codex roles. Existing four-stage behavior is a compatibility constraint. |
| AC-04 | [V1-01 report](V1-01.md) records three separate headless `app-server` threads with candidate pairs, matched markers and reported thread settings. | Exercise the V1-02 trusted launch path on the reviewed implementation HEAD and independently capture effective settings and sanitized evidence. The readiness spike did not exercise V1-02 role enforcement. |
| AC-06 | The existing [Controller](../../src/agent_workflow/orchestration/controller.py) invokes the Phase 3.1 runner, whose [execution path](../../src/agent_workflow/runners/coder_runtime.py#L310) constructs the Code prompt. The [Codex entrypoint](../../docker/coder_entry.py) invokes Codex with explicit CLI options. | Exercise the new trusted WorkerBundle and runtime contract without treating existing Codex flags or the fixed Code prompt as proof of canonical context, adapter portability or controlled discovery. |

### Role and phase fixture matrix

| Trusted profile | Allowed read fixture | Allowed write or output fixture | Denied example |
| --- | --- | --- | --- |
| `dev/code` | Approved task/AC and scoped source, tests and conventions | Allowlisted disposable source/test path. Trusted Worker receives the snapshot. | GitHub writes, out-of-scope source and Owner approval |
| `qc/pre-dev` | Draft Plan/AC, template and conventions | Proposed MTP and safe design handoff | Dev source, git push and self-review |
| `qc/post-code` | Frozen MTP, exact reviewed snapshot and read-only CI/TL evidence | Disposable harness, bounded logs and separate execution report | Source or frozen MTP edits, TL/Owner verdicts |
| `tl-reviewer/plan` | Exact Plan/AC/MTP submitted for review | Controlled provenance-bound plan review output | Editing Plan/MTP or submitting a code/QC verdict |
| `tl-reviewer/code` | Exact reviewed diff, current HEAD and CI evidence | Controlled provenance-bound code review output | Editing source, pushing and merging |
| `tl-reviewer/qc` | Exact QC evidence and relevant frozen artifacts | Controlled provenance-bound QC evidence review output | Editing the report/harness or issuing Owner approval |

Use synthetic gate-specific artifact markers for allowed reads and denied reads of other private contexts. For each reviewer phase, the controlled output sink receives a fixture labeled with its role, phase and input identity. Reject submissions to a different phase or with a spoofed identity. Across all profiles also deny access to other-role private prompts/workspaces/auth, live DB, host Docker and deployment assets. The fixture sink models the output boundary. Verdict persistence and authority to advance later gates are outside that boundary.

### Decision register and remaining risks

| Gap | Draft test design disposition | Remaining evidence or decision |
| --- | --- | --- |
| G-01 / AC-01 | Pinned CLI/provider catalog and legacy cutover have explicit test oracles. | Operator supplies the exact catalog, CLI and image pins. |
| G-02 / AC-02 | Six profiles and both authorization kinds have allow/deny fixtures. | Observe actual effective permissions and separate output sinks during execution. |
| G-03 / AC-03 | Plan pins the 60-second cleanup grace, ownership and fence requirements. | Safe fencing and quiescence require fault injection and a separately authorized real isolated Docker check. If safe fencing is infeasible, escalate to Owner. |
| G-04 / AC-04 | Equivalent synthetic auth and independently observed effective settings are required. | The pinned CLI must support both auth-positive/tool-negative isolation and telemetry for the same attempt. If either cannot be proved, BLOCKED. No real credentials before the auth control passes. Any alternate design requires Owner change control. |
| G-05 / AC-05 | Trusted launch validation and authorized operator gate verification have separate tests. | Final pinned artifact identities, path authorization, gate evidence and external manifest must be supplied at their respective gates. |
| G-06 / AC-05 | Deterministic legacy regression includes MCP auth and WFC state/round transitions. | Production PEM ACL, systemd continuity and real App writes are N/A. |
| G-07 / AC-06 | Plan pins v1 schemas, byte limits and Codex CLI 0.161.0. | Independently observe CLI loading/precedence offline and effective model/effort online after separate authorization. Required unverified evidence blocks clearance. |

Cross-role or host-secret exposure, silent config fallback, unverifiable invocation, uncontrolled instructions and stale or spoofed WorkerBundle/WorkerResult evidence are P0 risks. Image drift and regression scope are P1 risks. Cleanup uncertainty that allows a duplicate or unauthorized execution is P0. Accepted direction supplies draft assertions for independent review. Final package decisions and actual run prerequisites remain pending. When a remaining input prevents a reproducible assertion or authorized execution, report BLOCKED with the affected AC/test and owner.

## 3. Test cases

**Authorization fixtures.** Issue `DRAFT_PLANNING` only for `qc/pre-dev` and `tl-reviewer/plan` while Owner Gate is Pending. Bind exact Plan/MTP Git revision or digest, task, role/phase, distinct author/reviewer identities, issuer/attempt, scoped inputs and MTP-only author output or separate immutable review sink. Do not seed Owner approval in these positive fixtures. Issue `APPROVED_EXECUTION` only for `dev/code`, `qc/post-code`, `tl-reviewer/code` and `tl-reviewer/qc`. Bind the synthetic Owner-approved immutable package hash and task/base/HEAD/snapshot, plus phase-specific gates: package approval for Dev, current code diff/HEAD and applicable CI for TL code, exact reviewed source plus frozen MTP and current code/CI for post-code QC, authenticated immutable QC execution attempt for TL QC evidence review. Fixtures use an independent trusted issuer and verifier. An authorized operator supplies evidence for later gates that are still manual.

**Exact candidate Dev write fixture from pinned Plan §3.** Use each path as an individual allow vector with an explicitly authorized fixture. Never treat a directory prefix as authorization.

- Runner: `src/agent_workflow/runners/coder_runtime.py`, `src/agent_workflow/runners/runtime_contract.py`, `src/agent_workflow/runners/runtime_registry.py`, `src/agent_workflow/runners/codex_adapter.py`.
- Orchestration: `src/agent_workflow/orchestration/controller.py`, `src/agent_workflow/orchestration/role_policy.py`, `src/agent_workflow/orchestration/task_context.py`, `src/agent_workflow/orchestration/worker_bundle.py`.
- Entrypoint: `docker/coder_entry.py` for bounded role/auth/instruction invocation only.
- Tests: `tests/test_architecture_boundary.py`, `tests/test_phase31.py`, `tests/test_device_auth.py`, `tests/test_v1_02_roles.py`, `tests/test_v1_02_runtime.py`.
- Example: `.env.example` for non-secret documentation only.

Deny neighboring/lookalike paths and unlisted files, including `docs/tasks/V1-02-plan.md`, frozen `docs/tasks/V1-02-mtp.md`, `docs/tasks/V1-02-mtp-execution-report.md`, other docs, Dockerfiles, `pyproject.toml`, Compose, active `.runtime`, `.secret`, `data`, DB schema, GitHub workflows, auth volumes, PEM and Git hooks. A narrowed synthetic authorization also denies a candidate path omitted from the actual frozen allowlist. Use only inert canaries. Planner output, QC author output and separate post-code QC report have distinct owners and paths. Link the exact Plan and MTP commits in the review record. The independent operator records raw UTF-8 file SHA-256 and package manifest externally after review.

**Common harness and evidence.** Roles `D/Q/T` are Dev/QC/Independent TL Reviewer. Prepare role-specific synthetic prompt marker `PROMPT_<R>`, context marker `CTX_<R>`, private fixture file `private_<R>.txt`, disposable workspace `ws_<R>_<run>`, and per-run nonce. In offline config tests stub the capability catalog and spawn adapter: `fixture-a` supports `low`, `fixture-b` supports `medium`, `fixture-c` supports both. Assign `fixture-a/low`, `fixture-b/medium`, `fixture-c/low` to D/Q/T. These labels are test doubles and never sent to a provider. Use a fresh process and a clean environment per startup permutation. In explicit V1 role mode use canonical role IDs `dev`, `qc`, `tl-reviewer` and key pairs `DEV_MODEL/DEV_EFFORT`, `QC_MODEL/QC_EFFORT`, `TL_REVIEWER_MODEL/TL_REVIEWER_EFFORT`. The offline catalog fixture represents an operator-pinned version derived from supported model/effort metadata of the pinned CLI. Record the fixture catalog identity and keep startup offline. Compare exact allow/deny matrices and file digests using the independent harness. Set fake clock/budgets for timeout tests and use unique test-owned labels and disposable storage. Unit stubs prove input handling. Container isolation requires independent probes on the supported Docker host.

For every case, the trusted runner/operator collects test ID, matrix row, command/argv with values redacted as needed, exit status, bounded sanitized outputs, exact tested HEAD and tested snapshot digest, plus the case-specific evidence below. The separate execution report binds the frozen MTP digest and report/harness/artifact digests externally. `FAIL` means a reproduced mismatch to a frozen assertion. Unavailable authorization, platform, evidence or uncertain effects produce `BLOCKED` during execution. Remove only known test-owned artifacts after each case. If ownership or cleanup is uncertain, preserve sanitized diagnostics and stop for reconciliation. All cases below are **NOT RUN**.

### CFG-01 — Resolve three independent model/effort pairs
**AC/type/risk/status** — AC-01, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Start the real trusted resolver with the offline catalog and all six inputs populated. Launch D/Q/T with a recording CLI stub. Rotate the valid pairs across roles, then change one role's pair while keeping the other two fixed.  
**Assert/failure** — Startup validates all three pairs even if only one role is selected for launch. Each launch carries only the selected pair. The changed role changes at launch and the other pairs remain equal. Any default, bleed-through, unsupported fallback or discrepancy fails.  
**Evidence** — Input-label/role-to-argv matrix with launch counts and config source provenance.

### CFG-02 — Reject missing and blank configuration
**AC/type/risk/status** — AC-01, negative/boundary, P0, **NOT RUN**.  
**Setup and action** — Independently unset, empty and set whitespace-only for each of the six keys with the other five valid. Include all six absent. Attempt startup with an invalid selected role and with a valid selected role while another role has invalid config.  
**Assert/failure** — Trusted startup exits nonzero for every invalid environment, reports a sanitized role/field reason and emits zero role launches or publish effects, including when the invalid pair belongs to an inactive role.  
**Evidence** — 19 base input vectors with applicable selected/inactive permutations, startup statuses, diagnostic categories and zero launches.

### CFG-03 — Reject invalid, unsupported and injection-shaped values
**AC/type/risk/status** — AC-01, negative/security/boundary, P0, **NOT RUN**.  
**Setup and action** — Per role inject unknown model `fixture-missing`, unknown effort `unsupported`, known incompatible pair `fixture-a/medium`, and a literal containing newline plus shell metacharacters in each field. Also substitute mixed-case noncanonical forms and leading/trailing whitespace in each field. Repeat with the malformed pair belonging to a role that was not selected.  
**Assert/failure** — Invalid cases fail explicitly before launch. No shell command, provider request, partial state write or substitution to another model/effort. Diagnostics identify the field without echoing control characters or raw input.  
**Evidence** — Startup/argv/effect trace with zero subprocess/provider operations for rejected configurations.

### CFG-04 — Keep untrusted overrides from changing the resolved pair
**AC/type/risk/status** — AC-01/02, security, P0, **NOT RUN**.  
**Setup and action** — Inject conflicting model/effort and escalation flags via assignment, prompt, workspace config and tool output. Supply conflicting trusted values. Repeat invalid V1 startup with a valid legacy model configured, then explicitly select legacy mode with no V1 keys.  
**Assert/failure** — An attempted untrusted override or conflicting trusted input is rejected before spawn, without fallback. Valid trusted-only role mode uses the pinned pair and sandbox. Invalid role mode cannot downgrade. Explicit legacy mode uses the single-model path and starts no extra profiles.  
**Evidence** — Per-source actual argv/config diff, sanitized reject reason and spawn/effect counters.

### ISO-01 — Enforce trusted role selection
**AC/type/risk/status** — AC-02, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — Launch all six trusted profiles using the matching authorization fixtures. While Owner Gate is Pending, exercise both valid planning profiles with no Owner approval. Replay each draft grant into each of the four execution profiles and vary task, draft revision, role, phase, issuer and output. Include a forged Owner claim, unknown authorization kind, absent/unknown/noncanonical role, unknown phase/profile/capability and caller-supplied role/tool/mount overrides in assignment, stdin and repo fixtures.  
**Assert/failure** — Valid planning reaches the stub only with its scoped role and output, independently of Owner approval. Valid execution reaches the stub only with `APPROVED_EXECUTION` and its required gate evidence. Invalid/replayed/forged input, role switch, privilege increase or fallback fails closed with zero spawn.  
**Evidence** — Six-profile allow/deny matrix, trusted issuer/revision/attempt and output binding, Owner Gate Pending snapshot, sanitized reasons and launch counts.

### ISO-02 — Prove prompt and workspace separation for every ordered role pair
**AC/type/risk/status** — AC-02, integration/security, P0, **NOT RUN**.  
**Setup and action** — Run all six ordered D/Q/T pairs with separate synthetic private markers, plus two successive runs of the same role. Include every matrix profile and a same-role cross-phase probe to check phase-private context. Inspect launch input and mount metadata outside the container. Inside each role probe a known own fixture and the other run's known private path and canary. Use synthetic authorized shared evidence as a read-positive control.  
**Assert/failure** — Only the own role prompt/context and approved shared artifacts are exposed. Foreign private bytes are unreachable and do not appear in argv, stdin, env or output. New run starts without prior run's private workspace state. An inaccessible probe mechanism yields BLOCKED, while actual cross-role access fails.  
**Evidence** — Ordered-pair allow/deny results, mount/digest matrix and source provenance for positive controls.

### ISO-03 — Deny traversal and alias escape
**AC/type/risk/status** — AC-02, boundary/security, P0, **NOT RUN**.  
**Setup and action** — Try relative traversal, absolute external paths, symlink/hardlink aliases and an allowlist lookalike prefix against synthetic foreign files, using forms supported by the target filesystem. Read and write one legitimately allowed fixture as a positive control where policy permits.  
**Assert/failure** — Reject invalid mount/workspace roots before launch or deny the attempted access in the running sandbox as appropriate. Outside canaries remain unchanged and unread. Record host/container enforcement separately.  
**Evidence** — Valid/invalid path labels, read/write outcomes and before/after canary digests.

### ISO-04 — Treat artifacts and model output as untrusted inputs
**AC/type/risk/status** — AC-02/05, security, P0, **NOT RUN**.  
**Setup and action** — Put synthetic instructions to become Dev, bypass QC, approve the package, read another workspace or send a canary in a PR diff, test output and fake tool result. Replay a forged success record.  
**Assert/failure** — No change in trusted role or permissions, no protected read/write/egress, no accepted forged result or gate advancement. Assess enforced effects rather than wording of the agent response.  
**Evidence** — Denied operations, network sink counts and unchanged trusted state digest.

### PERM-01 — Apply least privilege for each role and phase
**AC/type/risk/status** — AC-02/05, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — Exercise each profile's allowed/denied reads and writes. For Dev, run every exact Plan §3 candidate path and every deny vector above, including a narrowed authorization and a rehashed forged snapshot/bundle claiming permission to edit Plan, frozen MTP or QC report. Test the entrypoint/example content limits with an out-of-scope change. QC PRE-DEV writes only the proposed MTP and handoff in its own workspace. POST-CODE QC writes its harness/logs/report and attempts a frozen MTP/source edit. Each TL phase attempts target mutation, wrong output and foreign identity.  
**Assert/failure** — Only authorized role operations and exact Dev paths with permitted changes qualify. Workspace restrictions and independent changed-path/promotion checks reject forged or disallowed changes. No protected artifact or trusted state changes. QC phase outputs remain separate and each TL output is provenance-bound. Roles lack GitHub write, Owner approval and merge authority.  
**Evidence** — Plan commit/path oracle, per-profile positive and negative path matrix, before/after protected digests, content scope and promotion verdict, distinct QC/TL output receipts.

### PERM-02 — Fail closed with missing required permissions or mounts
**AC/type/risk/status** — AC-02, negative/reliability, P0, **NOT RUN**.  
**Setup and action** — Remove one required read mount or allowed output capability per matrix profile, then deny the required access. Include missing smoke auth via a fake auth broker. Run a matched valid fixture first.  
**Assert/failure** — Safe explicit failure or BLOCKED outcome, no elevation/fallback, no published partial result and no successful handoff. A missing capability is not repaired by borrowing another role's mount or identity.  
**Evidence** — Matrix of required capabilities, preflight/runtime errors and no successful output transition.

### PERM-03 — Keep review and Owner authority independent
**AC/type/risk/status** — AC-02/05, negative/security, P0, **NOT RUN**.  
**Setup and action** — With Owner Gate Pending, issue distinct valid draft author/reviewer grants. Record an MTP-only author write and a read-only reviewer emission to the immutable review sink with no Owner approval fixture. Attempt QC self-review, replay author as reviewer, mutate the MTP from reviewer workspace, swap task/revision/output and replay both grants into Dev, post-code QC and TL code/QC. Forge Owner approval and request dispatch/merge from each role.  
**Assert/failure** — Valid draft operations succeed with separate identities and sinks. Replays and forged approval are rejected with zero role launch where submitted as launch requests, and no verdict, dispatch, merge or protected write. A manual operator check verifies only its own gate. Legacy WFC code verdict API cannot approve planning/QC.  
**Evidence** — Separate identity and output receipts, Pending Owner record, denied replay/authority matrix, zero launches and unchanged synthetic gate state.

### SEC-01 — Keep host and Worker privileges outside all role containers
**AC/type/risk/status** — AC-02/04, integration/security, P0, **NOT RUN**.  
**Setup and action** — For each smoke role/phase, use its intended CLI launch, mount or broker, UID and namespace pattern with a synthetic same-role credential canary. Trusted launcher/CLI must authenticate to an isolated authorized provider fixture. In the same running role exercise agent-controlled tool, shell/subprocess, workspace/file reads (including CLI home or broker endpoint), inherited environment and output/evidence capture. Also probe fake App PEM/token/DB, Git metadata, foreign-role auth and mock host engine.  
**Assert/failure** — CLI positive auth succeeds. Agent-controlled paths cannot retrieve or export any canary bytes even in their own role. Foreign/host/Worker assets remain inaccessible. Treat the baseline Code-stage Codex-home mount as exposure to probe. If equivalent ingress or actual denial cannot be demonstrated, or the agent can read bytes, record BLOCKED and withhold credentialed smoke. Redaction cannot clear a read breach.  
**Evidence** — Sanitized launch/mount/broker inventory, actual UID/namespace and access decisions, positive provider-auth receipt, negative probe counts and zero shared-output canaries. Retain no credential bytes.

### SEC-02 — Enforce the approved egress and subprocess boundary
**AC/type/risk/status** — AC-02/04, security/integration, P0, **NOT RUN**.  
**Setup and action** — Use effective container/OS network policy with isolated provider and denied sinks. Positive CLI authentication uses the SEC-01 ingress. Probe agent-controlled tool/shell egress with a synthetic export attempt, direct denied destination and redirect from approved provider to denied sink. Test workspace/env/file/output evidence routes, offline profiles and shell metacharacters with an argv recorder.  
**Assert/failure** — Only authorized provider operations succeed. Denied sink and redirect receive no request or canary, offline profiles emit no traffic and untrusted argv remains data. No fallback, retry or expanded privilege on denial. Missing effective network proof or same-role export denial blocks real smoke.  
**Evidence** — Effective network/OS policy, provider-auth and redirect observations, denied sink counts and sanitized argv/evidence trace.

### IMG-01 — Reuse a pinned image across ephemeral role launches
**AC/type/risk/status** — AC-03, positive/integration, P1, **NOT RUN**.  
**Setup and action** — Run sequential D/Q/T on disposable fixtures and inspect pinned image/CLI and service/restart declarations. Assign distinct trusted attempts and compare engine endpoint/context, image digest, task/dispatch ID, nonce, authenticated labels and persisted actual container ID once known. Inject same-name/colliding or missing labels and wrong-engine identity.  
**Assert/failure** — Roles share one immutable image and pinned Codex identity, or an Owner-documented incompatibility exception supplies exact digests and separate cleanup proof. No role service remains always-on. Missing/mismatched ownership cannot authorize reuse or cleanup. Names and one empty inventory are insufficient.  
**Evidence** — Image/CLI and service declaration diff, per-attempt ownership ledger and actual container IDs, rejected mismatch and cleanup receipts.

### LIFE-01 — Stop safely after role failure, timeout and ambiguous cleanup
**AC/type/risk/status** — AC-03/04, boundary/reliability, P0, **NOT RUN**.  
**Setup and action** — Use a fake clock and recording Docker adapter. Cover success below and at 180 seconds, timeout beyond 180 seconds, nonzero exit, and reconciliation at 59/60/61 seconds after timeout/failure. Delay create completion until after caller timeout and then after an empty inventory. Inject missing/colliding labels, wrong engine and unowned IDs. Attempt duplicate dispatch, publish or fence release during uncertainty. Independently run one separately authorized real isolated Docker check on disposable owned assets with an actual late effect or verified quiescence.  
**Assert/failure** — Only verified owned assets may be stopped/reaped. Grace expiration stays BLOCKED. A late result cannot complete the timed-out attempt, and the fence remains until the operator proves no late command or write principal can affect it and exact owned resources are settled. No replay, publish or gate advancement under uncertainty. Real Docker proof is required for DoD. Unsafe or unprovable fencing escalates to Owner.  
**Evidence** — Fake-clock event/ownership trace, actual engine/context and container ID, late-effect or verified-quiescence evidence, fence history and zero unauthorized deletions or dispatch/publish effects.

### SMK-01 — Dev completes one isolated online headless turn
**AC/type/risk/status** — AC-01/04/06, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Apply the shared protocol as Dev with a fresh disposable workspace and `V102_SMOKE_DEV_<nonce>`. No source or workflow effects.  
**Assert/failure** — Qualifies only after the role's synthetic ingress/export controls, with the authorized pair independently verified for this attempt, correct marker and clean exit/reconciliation. Mismatched settings or prohibited effects fail. Missing proof is BLOCKED.  
**Evidence** — Role-specific SEC-01/02 and CTX-04 receipts, independent attempt-bound effective telemetry, sanitized invocation record and cleanup receipt.

### SMK-02 — QC completes one isolated online headless turn
**AC/type/risk/status** — AC-01/04/06, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Apply the shared protocol as QC with a fresh disposable workspace and `V102_SMOKE_QC_<nonce>`. Use the phase named by the authorization. No source edits or review effects.  
**Assert/failure** — Qualifies only after the role's synthetic ingress/export controls, with the authorized pair independently verified for this attempt, correct marker and clean exit/reconciliation. Mismatched settings or prohibited effects fail. Missing proof is BLOCKED.  
**Evidence** — Role-specific SEC-01/02 and CTX-04 receipts, independent attempt-bound effective telemetry, sanitized invocation record and cleanup receipt.

### SMK-03 — Independent TL Reviewer completes one isolated online headless turn
**AC/type/risk/status** — AC-01/04/06, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Apply the shared protocol as Independent TL Reviewer with a fresh disposable workspace and `V102_SMOKE_TL_<nonce>`. Use read-only review inputs and the controlled output channel. No artifact edits or verdict submission.  
**Assert/failure** — Qualifies only after the role's synthetic ingress/export controls, with the authorized pair independently verified for this attempt, correct marker and clean exit/reconciliation. Mismatched settings or prohibited effects fail. Missing proof is BLOCKED.  
**Evidence** — Role-specific SEC-01/02 and CTX-04 receipts, independent attempt-bound effective telemetry, sanitized invocation record and cleanup receipt.

**Shared smoke protocol.** For each role, first record SEC-01/02 and CTX-04 evidence that equivalent synthetic auth ingress is CLI-positive and same-role tool-negative and that the actual provider-only network barrier denies redirects. An absent or failed control is BLOCKED and bars real credentials. Then, with separate Owner/operator execution authorization for that role/phase, start pinned Codex CLI 0.161.0 headlessly in a fresh disposable workspace using operator-provisioned auth out of band. One attempt per role, no automatic retries, at most 180 seconds and 65,536 bytes of retained sanitized output. Observe the expected role marker, process exit, cleanup and usage when available. Bind launcher-observed role/phase, invocation and trusted attempt ID, exact HEAD/snapshot and image/CLI to independently observed CLI/runtime telemetry for the same invocation/attempt. Record requested settings separately from effective model/effort and from model text. Report v1 result telemetry as `VERIFIED`, `UNVERIFIED` or `UNAVAILABLE`. A marker, requested-pair echo or agent JSON cannot verify effective settings. Only matching independently verified effective settings qualify. Missing effective telemetry, egress, redaction or cleanup evidence is BLOCKED. If the pinned CLI lacks observability, route alternate attestation design to Owner. Collect sanitized evidence and artifact digests, no credential bytes or production mutations.

### EVD-01 — Redact before publishing evidence
**AC/type/risk/status** — AC-04, security/negative, P0, **NOT RUN**.  
**Setup and action** — Inject fake token/PEM/account canaries into stdout, stderr, structured events and startup/error/timeout paths. Include split chunks and escaped representations for supported formats. Feed 65,535, 65,536 and 65,537 sanitized UTF-8 bytes and capture both success and failure artifacts through the normal evidence path.  
**Assert/failure** — No canary is visible in persisted/shareable evidence, summaries or exception text. Only approved safe fields remain available with enough provenance to verify the result. Retained sanitized output never exceeds 65,536 bytes. At overflow, safe truncation must preserve required proof or the result is BLOCKED. Reject unsafe or malformed capture.  
**Evidence** — Per-channel redaction assertion, bounded sanitized excerpts and artifact digest. Keep only synthetic markers in the test oracle.

### EVD-02 — Reject incomplete, stale or spoofed smoke evidence
**AC/type/risk/status** — AC-01/04/05/06, negative/boundary, P0, **NOT RUN**.  
**Setup and action** — Replay Dev as QC, change invocation/attempt/task/phase/runtime/HEAD/snapshot, substitute a different artifact digest, omit telemetry/redaction/cleanup or change observed effort. Compare verified same-attempt CLI metadata with launcher-only requested settings, model JSON/marker, wrong-attempt metadata and absent observation. Inject nonzero exit/timeout and duplicate valid evidence.  
**Assert/failure** — Only `VERIFIED` independently observed effective settings tied to the same attempt count toward the required smokes. Present but unverified evidence is `UNVERIFIED`, absent telemetry is `UNAVAILABLE`. Neither can pass. Contradictory evidence or bad exit fails, missing provenance blocks, and duplicate/replayed evidence cannot advance a gate.  
**Evidence** — Per-source and per-role status matrix, trusted verification reasons, invocation/attempt joins and retained sanitized evidence counts.

### REG-01 — Preserve Phase 3.1 stage and recovery contracts
**AC/type/risk/status** — AC-01/05, regression/integration, P0, **NOT RUN**.  
**Setup and action** — Run baseline `tests/test_architecture_boundary.py` and `tests/test_phase31.py` with fake adapters and disposable state. In explicit legacy mode, drive success, stale assignment, source mutation during Test and an uncertain publish result through the Controller boundary.  
**Assert/failure** — Prepare → Code → Test → Publish order and result identity persist. Test stays offline/credential-free. No publication of an untested or mutated snapshot, duplicate dispatch or success on uncertain outcome. Worker owns Git/App effects.  
**Evidence** — Stage trace, snapshot hash before/after Test and ledger/outbound call trace.

### REG-02 — Preserve nine MCP tools and legacy review contracts
**AC/type/risk/status** — AC-05, regression/security, P0, **NOT RUN**.  
**Setup and action** — Compare pinned base and reviewed HEAD offline through `/mcp` with isolated SQLite and fake auth. Verify `ping`, `get_server_info`, `list_review_queue`, `get_review_context`, `claim_review`, `submit_review`, `get_review_result`, `register_task_handoff`, `get_coder_assignment`, default Bearer public ping/info and seven protected tools, Auth0 protection for all `/mcp`, and the documented disposable-only POC bypass. On task A submit first `CHANGES_REQUESTED` with a finding, make an authorized new-HEAD handoff, then submit `APPROVED` as round two with all prior findings fixed and no new ones. On task B submit a first failure, make a valid new-HEAD handoff, then submit the second failure as `ESCALATED`. Attempt stale-SHA claim/submission, a third submission and handoff after APPROVED. Include a first-round APPROVED control with one round remaining to isolate the terminal-state guard.  
**Assert/failure** — Tool names/schemas/response/auth and legacy schema/history match baseline. Formal submissions including APPROVED count. A ends APPROVED at two and B ESCALATED at two. Stale, third and post-approval operations are denied with unchanged task/HEAD, counters, leases and immutable review/finding history. Legacy code verdicts cannot represent planning or QC decisions.  
**Evidence** — Baseline/target MCP diff, isolated SQLite before/after state and review/finding history, auth vectors and per-sequence statuses/counters.

### REG-03 — Preserve Worker-only scoped App and Draft publishing
**AC/type/risk/status** — AC-05, regression/security, P0, **NOT RUN**.  
**Setup and action** — Reuse the fake GitHub App and disposable bare remote fixtures in `tests/test_phase31.py` and `tests/test_architecture_boundary.py`. Check wrong repository, stale SHA and unauthorized ref, then the allowed scoped publish path.  
**Assert/failure** — App credentials stay Worker-only and repository-scoped. Only the permitted ordinary task-branch push and Draft PR path are observed. No forced push, main write, merge or automatic repeat after ambiguous publish.  
**Evidence** — Sanitized mock scope and write-call matrix, disposable ref history and zero unauthorized effects.

### REG-04 — Require exact package/MTP/HEAD evidence and preserve CI gates
**AC/type/risk/status** — AC-02/05, regression/security, P0, **NOT RUN**.  
**Setup and action** — (A) Exercise independent trusted launch preflight with both Pending-Owner draft profiles and no Owner approval fixture, plus valid `APPROVED_EXECUTION` for each execution profile with its required gate evidence. Verify immediately before dispatch and again at handoff. Vary kind, issuer, task, exact pinned Plan/MTP revision, role/phase, output, package ID/version/hash, base/HEAD/snapshot and required gates. Replay draft grants into all execution phases, forge Owner approval and rehash an out-of-allowlist snapshot claiming Plan/MTP/report writes. (B) Separately use a synthetic trusted operator gate to verify named exact-HEAD push CI plus independent TL verdict, then try CI PASS with verdict missing, wrong task/HEAD/package or agent self-attestation. Include stale/pending/failed/missing/ambiguous CI, synthetic merge SHA only, and same-SHA success after recorded failure without Owner acknowledgment. Run approved Python regression offline on reviewed HEAD.  
**Assert/failure** — (A) Matching planning and execution fixtures reach the stub under their own authorization classes. Invalid/stale/replayed or unapproved launch inputs yield zero spawn. Handoff mismatch blocks promotion. Exact external allowlist wins over a self-hashed claim. (B) Only trusted current CI and independent TL verdict for the same task/HEAD/package allow post-code QC. Missing or stale evidence blocks and definite CI failure follows separate repair policy. Manual operator proof grants no future registry, CI adapter, verdict persistence or revalidation implementation.  
**Evidence** — Separate preflight/operator records, authorization and exact Plan/MTP commit/digest provenance, positive and negative spawn/promotion counters, CI run/check IDs and tested SHA/history, TL identity/binding and offline pytest exit/skips.

### AC-06 deterministic fixture set

Use a disposable repository with two pinned synthetic source revisions, two approved task/package fixtures, distinct role/phase and auth canaries, and approved document bytes with independently calculated SHA-256. Give the Controller an immutable authorization fixture external to the submitted bundle. A recording registry pins each adapter's CLI/image identity and capability catalog. Its fake adapter returns scripted process observations, logs and output without a provider call. Use a deterministic clock and an in-memory, instrumented static document cache. Drive the same trusted launch and result boundaries for the Codex adapter and the fake substitute. Use a recording child for deterministic Codex argv/env assertions, plus the pinned real Codex CLI in an authorized offline disposable environment to verify its actual instruction-loading behavior. Reserve live Codex invocation for the separately authorized SMK cases. Real Claude Code and OpenCode adapters and smokes are out of scope.

**Versioned schema oracle.** Use the exact v1 field sets and types from pinned Plan §2 AC-06, not an implementation-generated schema. `WorkerBundle` has `schema_version`, `task_id`, `authorization` (kind, issuer and exactly its applicable hash), role/phase, runtime/provider/model/reasoning, policy digest, source manifest (path, Git revision, content digest, trust class and visibility), context/base/HEAD/snapshot identity, read/write paths, output channel, attempt and limits. `WorkerResult` has `schema_version`, attempt/task/role/phase/runtime/provider, requested and observed model/reasoning, telemetry status, source HEAD, snapshot and output-manifest digests, changed paths, process exit/timeout/status, sanitized log references and findings. Encode bundle hash input independently as UTF-8 JSON with sorted keys, no insignificant whitespace and no NaN/Infinity. Use independently pinned byte/hash vectors and canonical lowercase 64-hex SHA-256 digest fixtures. Compare required phase fields and exact schema version 1 on both paths.

### ADP-01 — Enforce the portable adapter and runtime registry contract
**AC/type/risk/status** — AC-01/06, positive/negative/contract, P0, **NOT RUN**.  
**Setup and action** — Register pinned Codex CLI 0.161.0 and one fake with a distinct delivery format. Drive `validate(config, capabilities)`, `prepare(bundle)`, `execute(invocation)`, `normalize(raw)` under the same trusted task and role/phase. Vary supported role/runtime/provider/model/reasoning selections independently with overlapping model IDs across fake providers. Inject unsupported version/profile/capability/pair, wrong type, unknown runtime, stale pin, ambiguous provider and agent overrides.  
**Assert/failure** — Only explicit supported pinned adapter/provider combinations proceed with valid v1 input. Fake substitution preserves approved task/package, allowed capabilities and gate decisions without sharing provider config/auth. Invalid cases fail before spawn with zero alternate calls or fallback. Untrusted overrides cannot steer selection.  
**Evidence** — Pinned catalog and adapter identities, ordered interface calls and input schema verification, phase/task/capability comparison and denied/fallback counters.

### ADP-02 — Normalize observed results and reject self-attested success
**AC/type/risk/status** — AC-04/06, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — Feed equivalent controlled observations through both adapter paths. Test strict `WorkerResult.schema_version = 1` fields/types, missing/extra/duplicate fields and unsupported version. Inject wrong attempt/task/role/phase/runtime/provider/HEAD/snapshot or unverifiable output-manifest digest, unknown telemetry/process status, unsafe changed paths, leaked log references and agent `SUCCESS` paired with nonzero exit, timeout or an unsatisfied gate.  
**Assert/failure** — Both paths normalize to one exact v1 shape. Telemetry is `VERIFIED|UNVERIFIED|UNAVAILABLE` and process is `COMPLETED|FAILED|BLOCKED` based on independently observed facts. Missing or unverified telemetry cannot pass a required smoke. Reject invalid shape, mismatched provenance, unsafe evidence and unauthorized effects. Even COMPLETED cannot replace a CI/QC/TL/Owner decision or promote unauthorized changes.  
**Evidence** — Independent schema/status vectors, exact attempt/identity joins, sanitized process/result/digest and changed-path traces, no advancement or promotion on negative inputs.

### CTX-01 — Bind canonical role instructions and control automatic discovery
**AC/type/risk/status** — AC-06, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — For all six profiles compile exact approved `AGENTS.md`, `docs/INDEX.md` as router, `docs/roles/{role}.md` and allowed versioned references. Independently produce canonical bytes and SHA-256 for the manifest and context. Remove required input, change selected bytes or revision, route INDEX to an unapproved source and insert wrong-role content. With pinned Codex CLI 0.161.0 offline and no login, seed canary project/ancestor/home `AGENTS.md`, `CLAUDE.md`, hooks, skills, plugins and MCP config. Use an authorized instruction positive control and observe actual stdin/config bytes, argv, sandbox and access/side effects.  
**Assert/failure** — Canonical manifest binds each selected source path/revision/content hash/trust/visibility to the approved role/phase and compiled context. Required mismatches block before launch. Uncontrolled discovery is disabled or independently traced and rejected if it could expand context/authority. No canary can override policy or cause side effects. Independently observed delivery and precedence are required, with no system-priority inference from prompt content.  
**Evidence** — Independent byte/hash vectors, selected source manifest diff, actual CLI version/argv/config/stdin and sandbox/file-access traces, canary and hook/tool outcomes.

### CTX-02 — Block wrong, stale or oversized WorkerBundles before launch
**AC/type/risk/status** — AC-02/05/06, positive/negative/boundary/security, P0, **NOT RUN**.  
**Setup and action** — Build valid v1 bundles for both Pending-Owner planning profiles without Owner approval and for all four execution profiles with approved package and current gates. Validate exact task/base/HEAD/snapshot, policy, canonical source manifest and allowed paths. Swap issuer, authorization kind/hash, role/phase, task, pinned Plan/MTP revision, package hash, allowed path/output and source bytes. Include replay into execution, wrong-kind or both hashes, missing or extra/duplicate fields, malformed/noncanonical digests, wrong types, unknown/duplicate version, unexpected capabilities and NaN/Infinity. Rehash a forged bundle claiming frozen Plan/MTP/report writes. Independently measure actual UTF-8 bytes at 524,287/524,288/524,289 bundle bytes and 131,071/131,072/131,073 selected-document bytes with multibyte fixtures. Hold the other limit valid for each boundary. After successful preflight, mutate a selected source, snapshot, manifest or role/policy separately at a deterministic barrier before CLI start.  
**Assert/failure** — Valid planning and execution reach the stub only under their own authorization with phase-appropriate inputs. Exact limits are inclusive for otherwise valid input. Oversized required inputs block without truncation. Only documented deterministic selection may omit optional lower-trust evidence and must account for that in the manifest. Recheck source bytes/digest, manifest, role/policy and snapshot immediately before CLI start. Any mismatch or invalid/unauthorized input fails closed with zero spawn, including after preflight and after attacker rehash. An unauthorized artifact cannot be promoted.  
**Evidence** — Independent canonical bytes/digests and schema mutation matrix, per-limit measured bytes, draft/execution provenance and narrowed path decisions, preflight-to-spawn barrier trace and zero-spawn proof.

### CTX-03 — Reuse static content safely and refresh dynamic evidence
**AC/type/risk/status** — AC-06, positive/negative/state/security, P0, **NOT RUN**.  
**Setup and action** — Dispatch twice with matching static content/policy/role/phase/visibility. Inspect cache hits if used. Vary each cache key member independently and replay across attempt, role and phase with private canaries. Change HEAD/diff/CI/findings/QC between dispatches and again between launch and handoff, holding static bytes constant. Change package revision and policy hash separately.  
**Assert/failure** — Static reuse, if used, keys on `content_sha256 + policy_sha256 + role + phase + visibility`. No combined dynamic authorization or private session is reused. Recheck fresh dynamic evidence and exact identity at every dispatch and handoff, include only phase-appropriate evidence and block stale launch/promotion. Changed HEAD/package invalidates assembled context and gate evidence even if allowed static bytes remain reusable.  
**Evidence** — Instrumented cache keys/counts when applicable, independent dispatch/handoff lookups and evidence IDs, manifest/digest comparisons, zero foreign canaries and gate/launch counters.

### CTX-04 — Verify runtime-specific instruction delivery without changing authority
**AC/type/risk/status** — AC-02/04/06, positive/negative/integration/security, P0, **NOT RUN**.  
**Setup and action** — Feed the same authorized v1 WorkerBundle through Codex CLI 0.161.0 and the fake's different instruction/reasoning mapping. Verify exact stdin bytes, argv, config, observed discovery/precedence and sandbox with the offline Codex probe in CTX-01, then compare effective policy and gates after substitution. On the intended Codex ingress, exercise the SEC-01/02 synthetic CLI-positive and same-role tool/shell/env/file/evidence-export negatives and actual provider-only redirect denial. Inject unsupported mapping and agent-selected adapter/provider/tool override.  
**Assert/failure** — Each adapter delivers approved bytes through its observed mechanism without changing task, role/phase, capabilities or gates. Document the observed delivery mode, including system priority only if independently proven. Unsupported mappings or overrides fail before launch. No cross-role/provider auth bleed and no unregistered Claude Code/OpenCode launch. Same-role credential isolation and denied redirect must be demonstrated before any real credentialed smoke. If the pinned CLI has no verifiable delivery route or safe auth boundary, BLOCKED.  
**Evidence** — Actual offline CLI version/stdin/argv/config and precedence trace, sanitized mount/UID/namespace and sandbox decisions, positive auth/negative export receipts, policy/identity comparisons and zero unregistered launches.

## 4. Universal and conditional safety coverage

- AC matrix and per-case positive/negative/boundary assertions are recorded above. Operator and package inputs listed in §2 must be available before applicable execution. Synthetic fixtures and captured trusted effects are required even when agent output looks correct.
- Test private prompts, workspaces, capability leakage, prompt injection, unauthorized file access and outbound effects with canaries. Keep role probes free of operational secrets, Docker socket and active DB. Authorized operator-only smoke auth is handled out of band.
- Verify package, MTP and reviewed HEAD before execution and again before handoff. Independent TL inspects each harness assertion against the frozen design. Include actual commands, environment, exit codes, skips, attempts, provenance and artifact digests. Missing or flaky evidence blocks a required case. Record each definite defect with severity, AC/test ID, expected and observed behavior, reproducer, safe evidence, impact, owner and required verification.
- Duplicate/reordered operations and timeout/recovery are covered for role lifecycle, evidence and legacy stage transitions. N/A for real Claude Code/OpenCode integration and smoke, persistent semantic cache and conversational-memory reuse, parallel task throughput, live production deployment/continuity, PEM ACL remediation, new Executor/Podman, live GitHub App writes and future Controller gate automation. These require separate scope or authorization.

## 5. Gate order and completion criteria

1. After exact-current-HEAD repository CI, a distinct Independent TL Reviewer checks the pinned Plan commit and the revised exact MTP HEAD in formal planning round 2/2, citing PG1-01–06 and affected AC/test IDs. Round 1/2 is consumed on the same lineage. This revision records no review verdict. If round 2 fails, escalate to Owner. Any changed Plan/MTP bytes invalidate a verdict tied to earlier inputs.
2. After successful independent review, the trusted operator records the raw UTF-8 SHA-256 of both pinned files and the package manifest externally. Owner explicitly approves the immutable package before Dev. Draft planning can be authorized while Owner Gate is Pending. Execution authority requires the approved package.
3. Trusted Worker publishes the implementation Draft PR. Verify named exact-current-HEAD GitHub CI PASS and independent TL code review PASS before starting QC. Use trusted operator evidence until the applicable automated gates exist.
4. QC executes against the reviewed snapshot in a separate disposable session. Record all results in the separate [MTP Execution Report](../templates/mtp-execution-report.md). The three online smokes and the real isolated Docker lifecycle check each require recorded execution authorization. Same-role credential isolation must be established before real auth. Every required case must have independently evidenced PASS or explicit Owner-approved N/A to clear QC. Unresolved FAIL, BLOCKED, NOT RUN or stale evidence prevents clearance.
5. Independent TL reviews the QC evidence. Owner alone decides the merge on the current exact HEAD. Changed HEAD invalidates CI/code/QC evidence. A post-QC repair HEAD requires separate independent code revalidation. Legacy WFC refuses a new handoff after APPROVED, so until that separate gate exists, stop BLOCKED for Owner reconciliation. Apply the separate two-round limits, stable keys and stop conditions in the [engineering standards](../conventions/engineering-standards.md).
