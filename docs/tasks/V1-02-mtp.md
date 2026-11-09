# Master Test Plan — V1-02 Role Profiles from Deployment Environment

**PRE-DEV TEST DESIGN ONLY.** Draft design disposition: **READY_FOR_INDEPENDENT_MTP_REVIEW** against the TL Planner's proposed Plan/AC/DoD. Owner package approval and separate online execution authorization remain pending. All cases are **NOT RUN**. Post-code results belong in the separate [MTP Execution Report](../templates/mtp-execution-report.md).

## 1. Frozen design identity (no self-reference)

| Item | Value |
| --- | --- |
| Planning package ID / proposed version | `V1-02 / v1.1-draft` is the proposed package lineage/version. The external manifest and Owner approval are pending. |
| Atomic task ID and AC source/revision | `V1-02`, [TL Planner Plan/AC/DoD v1.1-draft addendum](https://app.notion.com/p/412f92014abd81e7b6b4d8d645553cde#:~:text=TL%20Planner%20Plan%2FAC%2FDoD%20v1.1-draft%20addendum), Notion snapshot last edited `2026-11-09T02:11:43.351Z`. The addendum adds AC-06 and retains the v1.0-draft AC-01–05. [Planner handoff on PR #28](https://github.com/builacviet/agent-workflow/pull/28#issuecomment-6730594917). |
| Repository / design baseline | `builacviet/agent-workflow`, `main@96f5d523814f94ebfec244c66e38d400e2144509` |
| QC policy | `required` given the role-selection, authorization and credential boundaries |
| MTP ID / version | `V1-02-MTP / 0.3-draft` |
| Changed-path scope / prohibited effects | PRE-DEV write scope is this file. The linked draft defines the proposed DoD and candidate Dev paths. The exact Dev allowlist awaits package freeze. Post-code QC writes only disposable harness/evidence and the separate execution report. No production config, live DB, tunnel, real App PEM/token access, generic Docker access for roles, or live workflow/merge mutations. |
| Environment, bounds and fixtures | Offline credential-free sandbox by default. Target Ubuntu 24.04 on WSL2 and pinned Codex Docker image for operator-authorized smoke. Synthetic role markers, fresh workspaces and fake services as described below. Draft smoke limits are 180 seconds and 64 KiB of sanitized retained output per role, one invocation per role, approved provider-only egress and no automatic retry. The execution authorization and operator inputs are pending. |
| QC author/session identifier | `Codex QC / qc-predev-v1-02-mtp-20261109-01` (artifact-local author session label) |
| Dependencies and risks | V1-01 feasibility, merged role contracts from PR #27, legacy Phase 3.1 boundaries, Owner direction on G-01, G-02, G-04 and G-05 and the AC-06 architecture addendum, with remaining review and execution prerequisites in §2. |

An independent trusted operator records the SHA-256 of the final raw UTF-8 MTP bytes and binds it to the frozen Plan, AC and task in the external package manifest. Package and MTP digests and Owner authorization are recorded externally. Byte changes after approval require a new version/hash and change control. [QC role](../roles/qc.md) · [Engineering standards](../conventions/engineering-standards.md)

## 2. Acceptance Criteria traceability

| AC ID | Observable requirement | Test case IDs | Coverage | Gap / risk |
| --- | --- | --- | --- | --- |
| AC-01 | In explicit V1 role mode, trusted startup validates all three independent canonical deployment model/effort pairs before any role spawn. Launch binds the selected pair. Legacy mode is explicit. | CFG-01–04, SMK-01–03, REG-01 | Valid, absent, noncanonical, unsupported and conflicting config, inactive-role rejection, legacy isolation and effective CLI settings | P0 silent fallback or wrong model/effort. |
| AC-02 | Trusted runner selects the role and phase manifest and enforces scoped prompts, mounts, tools, workspace, output and permissions. | CFG-04, ISO-01–04, PERM-01–03, SEC-01–02 | Six profiles, all ordered role pairs, successive runs, positive and denied capabilities, and injection | P0 cross-role data or privilege exposure. |
| AC-03 | Prefer one immutable pinned image for three ephemeral roles and require an evidenced Owner decision for any exception. | IMG-01, LIFE-01 | Image identity, bounded lifecycle, cleanup and uncertain stop | G-03. P1 image drift or lingering services. |
| AC-04 | Collect one distinct authorized, bounded online headless invocation per role, with trusted effective settings and sanitized evidence. | LIFE-01, SMK-01–03, EVD-01–02 | Three role launches, budget/egress enforcement, metadata, redaction and timeout handling | P0 unverified execution or disclosure. Operator inputs and execution authorization pending. |
| AC-05 | Trusted launch preflight binds authorized task/package/base/head/snapshot and records safe evidence. Preserve legacy contracts and the separation of later gates. | ISO-04, PERM-03, EVD-02, REG-01–04 | Identity/hash mismatch, changed paths, stale inputs, CI provenance and compatibility | G-06. P0 stale or spoofed evidence. Later gate evidence is verified by an authorized operator while automation is undeployed. |
| AC-06 | Trusted Controller builds a versioned, bounded role/phase WorkerBundle from canonical policy and scoped task context. A pinned runtime registry supports Codex and a fake substitute under one adapter/result contract without shifting authority or falling back. | ADP-01–02, CTX-01–04 | Adapter substitution and rejection, normalized result, canonical inputs and auto-discovery, bundle provenance, freshness and cross-role isolation | P0 authority drift, hidden instructions, stale context or spoofed success. See G-07. |

The draft DoD is covered by startup and permission assertions (CFG/ISO/PERM/SEC), image and three-role smoke checks (IMG/LIFE/SMK/EVD), exact-HEAD, changed-path and legacy compatibility checks (REG), and WorkerBundle, adapter substitution, context freshness and normalized-result checks (ADP/CTX). The separate approval, code review, QC evidence review and merge gates remain prerequisites or follow-on decisions as applicable.

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
| G-01 / AC-01 | Owner direction gives a testable oracle: explicit mode, six canonical keys, all-pair validation and a pinned catalog. | Independent review of the oracle and legacy cutover. Operator supplies catalog revision and pinned CLI identity for execution. |
| G-02 / AC-02 | Six role/phase profiles define positive and denied fixtures. Selection, permissions and output provenance are independently probed. | Implementation must expose independently observable effective mounts/capabilities and controlled output. Final allowlist is a package-freeze input. |
| G-03 / AC-03 | P1 for independent review. Require a single immutable digest or an explicit Owner exception supported by reproducible incompatibility, actual digest and cleanup evidence. | Confirm cleanup deadline and operator reconciliation procedure. Unverified cleanup is BLOCKED. |
| G-04 / AC-04 | Three distinct, bounded smokes have explicit settings, provenance and failure assertions. | Separate Owner execution authorization, approved auth provision, provider allowlist, pinned telemetry field mapping and redaction proof are execution prerequisites. A missing prerequisite blocks the affected smoke. |
| G-05 / AC-05 | Test runtime launch preflight independently from manual checks for later gates. | Final Plan/AC/DoD and path allowlist, trusted identities/digests and external package manifest are package-freeze inputs. Later gates use authorized operator evidence while undeployed. |
| G-06 / AC-05 | P1 for independent review. Deterministic regression covers Phase 3.1, nine MCP tools, WFC review and scoped App behavior with synthetic fixtures. | Confirm regression evidence and N/A boundaries. Production PEM ACL, systemd continuity and live App writes are outside this task. |
| G-07 / AC-06 | The addendum defines the portable contract and failure behavior. ADP/CTX use synthetic pins and budgets and test outcomes at observable boundaries. | Independent review checks the contract against the frozen Plan. Post-code harness needs the declared adapter/result schema, approved source and size policy, and the pinned Codex delivery/discovery mechanism and observable evidence. Missing required enforcement or trustworthy evidence prevents AC-06 clearance. |

Cross-role or host-secret exposure, silent config fallback, unverifiable invocation, uncontrolled instructions and stale or spoofed WorkerBundle/WorkerResult evidence are P0 risks. Image drift and regression scope are P1 risks. Cleanup uncertainty that allows a duplicate or unauthorized execution is P0. Accepted direction supplies draft assertions for independent review. Final package decisions and actual run prerequisites remain pending. When a remaining input prevents a reproducible assertion or authorized execution, report BLOCKED with the affected AC/test and owner.

## 3. Test cases

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
**Setup and action** — Inject conflicting model/effort and escalation flags via assignment payload, prompt, workspace config and mock tool output. Put a distinct supported pair in trusted env. Supply conflicting trusted values via the supported trusted input sources. Repeat invalid V1 startup with otherwise valid legacy model config present. Separately select explicit legacy mode with that model and no V1 keys.  
**Assert/failure** — Untrusted sources cannot change effective launch settings or authorize fallback. A launch that proceeds uses the trusted pair and approved sandbox. Conflicting trusted inputs or invalid V1 role config fail nonzero with zero launches. Explicit legacy mode uses its single-model path and does not start other role profiles.  
**Evidence** — Per-source actual argv/config diff and side-effect trace.

### ISO-01 — Enforce trusted role selection
**AC/type/risk/status** — AC-02, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — Use the normal trusted launch entry for all six matrix profiles with conflicting caller role, phase, tool policy and mount values in assignment, stdin and repo fixtures. Submit absent/unknown/noncanonical trusted role, unknown or wrong phase, missing/wrong profile, undeclared capability and an unauthorized selector.  
**Assert/failure** — Valid launches use the trusted immutable role/phase manifest and its authorized prompt and capabilities. Invalid trusted selections or unauthorized entry requests fail nonzero before role spawn or model invocation. Any role switch, privilege increase or fallback launch fails.  
**Evidence** — Trusted selection/dispatch trace, prompt digest and denied-launch counts.

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
**AC/type/risk/status** — AC-02, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — For each of the six matrix rows, read the allowed fixture and a denied private fixture, then exercise its allowed and denied writes or output. D also attempts adjacent out-of-scope and Git writes. Both QC phases attempt Dev writes. Post-code QC also attempts a frozen MTP edit. Each T phase emits one controlled output and attempts editing its reviewed target, writing to the wrong output channel and substituting a foreign input identity.  
**Assert/failure** — Only the listed operations succeed. Workspace permissions enforce the phase profile and the trusted changed-path check rejects a forged snapshot containing out-of-allowlist edits. Reviewer outputs reach only their controlled sink with the correct provenance. Denied operations leave protected artifacts and trusted state unchanged. All automated untrusted roles lack GitHub write, Owner approval and merge authority.  
**Evidence** — Per-role operation matrix and changed-path, capability and artifact digests.

### PERM-02 — Fail closed with missing required permissions or mounts
**AC/type/risk/status** — AC-02, negative/reliability, P0, **NOT RUN**.  
**Setup and action** — Remove one required read mount or allowed output capability per matrix profile, then deny the required access. Include missing smoke auth via a fake auth broker. Run a matched valid fixture first.  
**Assert/failure** — Safe explicit failure or BLOCKED outcome, no elevation/fallback, no published partial result and no successful handoff. A missing capability is not repaired by borrowing another role's mount or identity.  
**Evidence** — Matrix of required capabilities, preflight/runtime errors and no successful output transition.

### PERM-03 — Keep review and Owner authority independent
**AC/type/risk/status** — AC-02/05, negative/security, P0, **NOT RUN**.  
**Setup and action** — Against the trusted role output boundary and a disposable simulation of the manual operator gate, submit a QC self-review, replay an author identity as T, forge Owner approval, and request dispatch/merge from each role. A distinct authorized read-only reviewer receives the approved evidence as a positive control.  
**Assert/failure** — Requests do not create valid verdict, Owner decision, dispatch or merge. Legacy WFC code verdict API is never used to approve planning or QC evidence. A manual preflight only verifies the procedural gate.  
**Evidence** — Rejected operations, distinct nonsecret identities and unchanged synthetic gate state.

### SEC-01 — Keep host and Worker privileges outside all role containers
**AC/type/risk/status** — AC-02, integration/security, P0, **NOT RUN**.  
**Setup and action** — Create fake App PEM/token/DB, fake Git metadata, another role's auth context and a mock Docker endpoint in operator-owned disposable fixtures. Check effective mount/env/capability inventory and attempt targeted probes from D/Q/T. Use a trusted test-side read of canary fixtures as a positive control.  
**Assert/failure** — No role can read protected fixture contents or connect to the engine endpoint. No live paths, broad host mounts, App credentials, active WFC DB, uncontrolled GitHub/engine access or cross-role auth context enter a role. Check untrusted role runtime restrictions separately from trusted host engine authority.  
**Evidence** — Sanitized effective container manifest, denied read/connect codes and no captured protected markers.

### SEC-02 — Enforce the approved egress and subprocess boundary
**AC/type/risk/status** — AC-02, security/integration, P0, **NOT RUN**.  
**Setup and action** — Run each profile with fake network sinks for approved and denied destinations and a subprocess recorder. In the smoke profile, exercise an approved provider fixture, a non-provider sink and a redirect from the provider fixture to that denied sink. Test offline profiles with no network. Try to pass a synthetic secret marker via denied tool/egress and inject shell metacharacters through untrusted data.  
**Assert/failure** — Only approved provider smoke traffic succeeds in the smoke profile. Denied sinks see no request or marker, including after redirects. Offline profiles emit no traffic and argv remains data. No permissive retry or new credentials/mounts are granted when a request is denied.  
**Evidence** — Role-specific sink counts, sanitized argv trace and effective network policy. Real smoke egress is covered separately.

### IMG-01 — Reuse a pinned image across ephemeral role launches
**AC/type/risk/status** — AC-03, positive/integration, P1, **NOT RUN**.  
**Setup and action** — Inspect changed image/service declarations and launch D/Q/T sequentially in a disposable runtime. Observe resolved image ID/digest and CLI version, service/restart policy and owned containers before, during and after.  
**Assert/failure** — The launches share immutable image identity and no role is declared or left running as an always-on service. A differing digest requires a separately documented Owner exception backed by reproducible incompatibility, actual digests and cleanup evidence before acceptance. Existing MCP/optional tunnel are baseline infrastructure.  
**Evidence** — Image and CLI provenance matrix, declaration diff and labeled lifecycle inventory.

### LIFE-01 — Stop safely after role failure, timeout and ambiguous cleanup
**AC/type/risk/status** — AC-03/04, boundary/reliability, P0, **NOT RUN**.  
**Setup and action** — Use a deterministic clock and test-owned workload. Exercise successful completion just below and at the 180-second smoke limit, runtime beyond the limit, immediate nonzero exit, and a Docker adapter response where cleanup cannot be confirmed. Keep the operator-specified cleanup deadline separate from the smoke runtime limit. Replay the same attempt request after uncertainty.  
**Assert/failure** — Completion at or under the limit qualifies only with trusted exit and cleanup evidence. Over-limit invocations are stopped and do not count. Confirmed cleanup removes only owned disposable state. Uncertain cleanup blocks progress and repeat dispatch until reconciliation. A late response cannot produce success for the timed-out attempt.  
**Evidence** — Timing/exit trace, labels, cleanup disposition, zero duplicate/publish effects. Confirm one real bounded cleanup in an approved isolated Docker session when available.

### SMK-01 — Dev completes one isolated online headless turn
**AC/type/risk/status** — AC-04, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Run the shared smoke protocol below as D with a dedicated disposable workspace and `V102_SMOKE_DEV_<nonce>`.  
**Assert/failure** — Protocol passes with Dev's approved pair and only Dev's expected marker. Any mismatch, reroute outside policy or forbidden tool effect fails.  
**Evidence** — Dev-specific trusted invocation record and cleanup receipt.

### SMK-02 — QC completes one isolated online headless turn
**AC/type/risk/status** — AC-04, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Run the same protocol as Q with a fresh workspace and `V102_SMOKE_QC_<nonce>`. Use the QC phase recorded in the separate execution authorization.  
**Assert/failure** — Protocol passes with QC's approved pair and only QC's expected marker. No source edits or review effects.  
**Evidence** — QC-specific trusted invocation record and cleanup receipt.

### SMK-03 — Independent TL Reviewer completes one isolated online headless turn
**AC/type/risk/status** — AC-04, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Run the protocol as T in its separate workspace with read-only review inputs and the controlled output channel with `V102_SMOKE_TL_<nonce>`.  
**Assert/failure** — Protocol passes with T's approved pair and only T's expected marker. No artifact edits or verdict submission.  
**Evidence** — Reviewer-specific trusted invocation record and cleanup receipt.

**Shared smoke protocol.** On reviewed implementation HEAD, a separately authorized operator selects one role via the normal trusted runner, with approved disposable authentication supplied out of band and an empty synthetic workspace. Credentials are neither read by QC nor copied into evidence. Start the pinned CLI headlessly, with a request for the role marker and no actions, with the role/phase named in the authorization, the 180-second runtime limit, at most 64 KiB of sanitized retained output, and provider-only egress verified by the operator. Make one invocation per role and disable automatic retry. Observe provider completion, exact role marker, positive output usage where supported, exit zero and cleanup. Record role from the trusted launcher, requested and effective model/effort from trusted CLI/runtime telemetry, phase, pinned image digest and CLI identity, invocation ID, runtime, exit, usage when available, reviewed HEAD, tested snapshot digest, artifact SHA-256 and redaction/cleanup results. Require the approved pair and invocation identity to match the record. Three distinct invocations are required even when pairs coincide. V1-01 examples are `gpt-6-luna/low` for Dev and QC and `gpt-6-luna/medium` for T. Verify selected pairs against the approved policy at execution. Missing authorization, effective telemetry, egress verification, redaction proof or cleanup receipt yields BLOCKED. A model-authored marker or configuration echo cannot attest effective settings. Run no tools that touch real tasks, GitHub, WFC or Controller state.

### EVD-01 — Redact before publishing evidence
**AC/type/risk/status** — AC-04, security/negative, P0, **NOT RUN**.  
**Setup and action** — Inject fake token/PEM/account canaries into stdout, stderr, structured events and startup/error/timeout paths. Include split chunks and escaped representations for supported formats. Feed 65,535, 65,536 and 65,537 sanitized UTF-8 bytes and capture both success and failure artifacts through the normal evidence path.  
**Assert/failure** — No canary is visible in persisted/shareable evidence, summaries or exception text. Only approved safe fields remain available with enough provenance to verify the result. Retained sanitized output never exceeds 65,536 bytes. At overflow, safe truncation must preserve required proof or the result is BLOCKED. Reject unsafe or malformed capture.  
**Evidence** — Per-channel redaction assertion, bounded sanitized excerpts and artifact digest. Keep only synthetic markers in the test oracle.

### EVD-02 — Reject incomplete, stale or spoofed smoke evidence
**AC/type/risk/status** — AC-04/05, negative/boundary, P0, **NOT RUN**.  
**Setup and action** — At the trusted verification seam replay a successful Dev record as QC, omit effective effort, switch model, alter phase, invocation ID, reviewed HEAD or snapshot digest, swap the artifact behind its hash, supply a marker alone, omit redaction or cleanup proof, inject nonzero exit/timeout and duplicate a valid record.  
**Assert/failure** — Only one qualifying record per role counts toward three-role coverage. Contradictory or nonzero results fail. Missing or uncertain provenance blocks acceptance. No fake or replayed evidence advances a gate.  
**Evidence** — Verification reason matrix with per-role completion count and retained sanitized attempts.

### REG-01 — Preserve Phase 3.1 stage and recovery contracts
**AC/type/risk/status** — AC-01/05, regression/integration, P0, **NOT RUN**.  
**Setup and action** — Run baseline `tests/test_architecture_boundary.py` and `tests/test_phase31.py` with fake adapters and disposable state. In explicit legacy mode, drive success, stale assignment, source mutation during Test and an uncertain publish result through the Controller boundary.  
**Assert/failure** — Prepare → Code → Test → Publish order and result identity persist. Test stays offline/credential-free. No publication of an untested or mutated snapshot, duplicate dispatch or success on uncertain outcome. Worker owns Git/App effects.  
**Evidence** — Stage trace, snapshot hash before/after Test and ledger/outbound call trace.

### REG-02 — Preserve nine MCP tools and legacy review contracts
**AC/type/risk/status** — AC-05, regression/security, P0, **NOT RUN**.  
**Setup and action** — Compare exact base and reviewed HEAD through offline `/mcp` transport with ephemeral SQLite and mocked auth. Check `ping`, `get_server_info`, `list_review_queue`, `get_review_context`, `claim_review`, `submit_review`, `get_review_result`, `register_task_handoff`, `get_coder_assignment`. Exercise default Bearer public ping/info and protection of seven workflow tools, then Auth0 middleware protection for all `/mcp` using fake claims/keys. Verify the documented disposable-only POC bypass boundary without live endpoint access.  
**Assert/failure** — Names, schemas, response shapes, auth behavior, SQLite schema/history and exact task/SHA rules match baseline. Two formal WFC submissions total including PASS, no extra handoff after APPROVED and no planning/QC verdict through legacy API.  
**Evidence** — Baseline/target contract diff, synthetic auth result and round/state transition tables.

### REG-03 — Preserve Worker-only scoped App and Draft publishing
**AC/type/risk/status** — AC-05, regression/security, P0, **NOT RUN**.  
**Setup and action** — Reuse the fake GitHub App and disposable bare remote fixtures in `tests/test_phase31.py` and `tests/test_architecture_boundary.py`. Check wrong repository, stale SHA and unauthorized ref, then the allowed scoped publish path.  
**Assert/failure** — App credentials stay Worker-only and repository-scoped. Only the permitted ordinary task-branch push and Draft PR path are observed. No forced push, main write, merge or automatic repeat after ambiguous publish.  
**Evidence** — Sanitized mock scope and write-call matrix, disposable ref history and zero unauthorized effects.

### REG-04 — Require exact package/MTP/HEAD evidence and preserve CI gates
**AC/type/risk/status** — AC-05, regression/security, P0, **NOT RUN**.  
**Setup and action** — (A) Drive the V1-02 trusted launch preflight with a synthetic independently approved package, frozen MTP, task ID, expected base/HEAD, exact snapshot digest and frozen path allowlist. Change one field at a time, including package ID/version/hash, MTP ID/version/hash when required, task, base, HEAD, snapshot or approval provenance. Inject missing inputs and an out-of-allowlist changed path. (B) Independently exercise the authorized manual operator gate with exact current reviewed HEAD and named CI, old SHA, CI pending/failed/missing/ambiguous, synthetic PR merge SHA only, and same-SHA success after recorded failure without Owner acknowledgment. Run approved Python regression tests offline against reviewed HEAD.  
**Assert/failure** — (A) The matching approved fixture can reach the stub launcher and captures provenance. Missing, stale, mismatched or unapproved inputs yield an explicit rejection and zero role launches. A modified out-of-allowlist snapshot cannot be promoted. (B) Only trusted successful named exact-HEAD push CI and an independent TL code verdict for the approved package and current HEAD permit post-code QC. Missing/stale/ambiguous evidence is BLOCKED. A completed required failure follows separate CI repair policy. Later gate decisions use authorized operator evidence while automation is undeployed. Do not count this preflight as an Owner registry, V1-03 CI adapter, review verdict store or post-QC revalidation implementation.  
**Evidence** — Separate trusted preflight and manual gate records, input permutation and launch-count matrix, manifest and changed-path/snapshot digests, CI run/check IDs, event, attempt, tested SHA and history, TL verdict binding and offline pytest exit/skips.

### AC-06 deterministic fixture set

Use a disposable repository with two pinned synthetic source revisions, two approved task/package fixtures, distinct role/phase and auth canaries, and approved document bytes with independently calculated SHA-256. Give the Controller an immutable authorization fixture external to the submitted bundle. A recording registry pins each adapter's CLI/image identity and capability catalog. Its fake adapter returns scripted process observations, logs and output without a provider call. Use a deterministic clock and an in-memory, instrumented static document cache. Drive the same trusted launch and result boundaries for the Codex adapter and the fake substitute. Use a recording child for deterministic Codex argv/env assertions, plus the pinned real Codex CLI in an authorized offline disposable environment to verify its actual instruction-loading behavior. Reserve live Codex invocation for the separately authorized SMK cases. Real Claude Code and OpenCode adapters and smokes are out of scope.

### ADP-01 — Enforce the portable adapter and runtime registry contract
**AC/type/risk/status** — AC-06, positive/negative/contract, P0, **NOT RUN**.  
**Setup and action** — Register `codex-cli` and `fixture-runtime` with explicit adapter, CLI/image and capability pins. Drive declared validation, preparation, execution and normalization with the same authorized task and role/phase, using controlled child observations. Use disjoint fake provider catalogs with an overlapping model ID to test provider-qualified resolution. Vary runtime, provider and model/reasoning independently among supported combinations. Repeat with unknown/unregistered runtime, stale pin, ambiguous provider, incompatible provider/model/effort/profile and undeclared capability. Include an untrusted override of runtime, provider, model and permissions.  
**Assert/failure** — Valid inputs select the exact registered adapter and provider-qualified pair. Both adapters satisfy the declared contract, with the same task identity and gate authorization. Invalid trusted inputs fail closed before role launch with a sanitized reason and zero alternate-adapter/provider calls. Untrusted data cannot change the trusted selection. Substituting the runtime cannot reuse a different provider's configuration or auth context.  
**Evidence** — Registry and capability pin IDs, observed contract calls, selected role/runtime/provider/model/reasoning metadata and sanitized launch/fallback counters.

### ADP-02 — Normalize observed results and reject self-attested success
**AC/type/risk/status** — AC-06, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — Feed equivalent successful controlled observations through both adapter paths and validate the same declared result schema. Inject malformed or unknown output, missing/mismatched launch ID, role/phase/runtime, HEAD, snapshot or artifact digest, an agent-authored `SUCCESS` with nonzero exit or timeout, unverified effective settings, and unexpected changed paths/side effects. Also supply a valid process result while a downstream gate remains unsatisfied.  
**Assert/failure** — Independently observed launch identity, requested and verified effective settings when supported, exit/timeout, bounded sanitized evidence references, HEAD/snapshot and artifact digests, findings and changed-path summary normalize to the common contract. Verified facts and agent claims remain distinguishable. Unsupported or unverified telemetry is represented explicitly and never treated as verified. Malformed, mismatched or unsafe results cannot be promoted to PASS. Even a valid normalized result cannot advance an unsatisfied gate or override the trusted changed-path check.  
**Evidence** — Schema and provenance assertions for both paths, sanitized exit/timeout and validation traces, independent digest/changed-path comparisons and unchanged gate state in negative cases.

### CTX-01 — Bind canonical role instructions and control automatic discovery
**AC/type/risk/status** — AC-06, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — For all six profiles, build from exact approved `AGENTS.md`, `docs/INDEX.md` as a router, `docs/roles/{role}.md` and allowed versioned references. Compare selected source paths, versions and compiled bytes with the independent oracle. Remove an approved required file, alter bytes without changing the claimed hash, point INDEX to unapproved content and insert a wrong-role or outdated source. In the disposable Codex launch, seed project/ancestor/home `AGENTS.md` and `CLAUDE.md`, hooks, skills, plugins and MCP config with distinct canaries. Use a positive control for the authorized instruction route and independent probes for file loads and hook/tool effects.  
**Assert/failure** — Manifest and compiled prompt/context bind the approved role/phase, canonical source identities, content hashes and trust labels. Missing/mismatched required inputs block before launch. Unapproved sources are denied or explicitly audited and excluded from authority and context. Auto-discovered canaries cannot change effective instructions, mount/tool policy or perform side effects. Merely observing a matching agent response does not prove that discovery was controlled.  
**Evidence** — Independently computed digests, manifest/source-selection comparison, verified pinned CLI rules/config mode and sanitized file-access, hook and tool traces.

### CTX-02 — Block wrong, stale or oversized WorkerBundles before launch
**AC/type/risk/status** — AC-06, positive/negative/boundary/security, P0, **NOT RUN**.  
**Setup and action** — Build one valid bundle from the external authorization fixture. Verify its task, repo, base, expected HEAD or snapshot identity as applicable, package/AC/DoD/MTP versions and hashes, role/phase, policy hash, allowed paths and permitted findings/evidence. Change one identity or content input at a time, including stale expected SHA, wrong snapshot, wrong task, unauthorized source revision, missing required evidence and foreign-role context. Recompute the modified bundle's self-reported digest in one tampering case. Exercise sizes one byte below, at and one byte above the configured synthetic input ceiling, including multibyte UTF-8.  
**Assert/failure** — The matching approved fixture reaches the recording launch boundary with only authorized phase evidence. All missing required, mismatched, unauthorized, stale or oversized inputs are rejected or BLOCKED with zero role launches. Rehashing altered bytes cannot override the independent authorization fixture. Inputs at or below the synthetic byte ceiling qualify only when otherwise valid.  
**Evidence** — Per-field permutation, independent source/package/policy/snapshot digest comparisons, selected phase evidence IDs, measured byte counts and dispatch/spawn counters.

### CTX-03 — Reuse static content safely and refresh dynamic evidence
**AC/type/risk/status** — AC-06, positive/negative/state/security, P0, **NOT RUN**.  
**Setup and action** — Dispatch a valid fixture twice with identical static bytes, policy version and role/phase visibility. If memoization is used, inspect cache lookup identities and counts. Between dispatches change dynamic HEAD, CI, diff, review findings and QC status independently while holding static bytes constant. Repeat a change between launch and handoff. Vary document content, package revision, policy version and role/phase visibility, including byte-identical public inputs with distinct private context.  
**Assert/failure** — Reused static entries, if any, are bound to content hash, policy version and role/phase visibility. Current dynamic evidence is rechecked at every dispatch and handoff, and only relevant evidence enters the bundle. HEAD or package changes invalidate the old assembled context and dependent gate evidence even if unchanged public static bytes remain reusable. Changed policy or private visibility cannot reuse stale or foreign context. Stale or unverifiable evidence blocks the affected launch or handoff.  
**Evidence** — Resolver/cache keys and counters when applicable, phase evidence lookup and provenance timestamps, before/after manifests, zero foreign canaries and gate/spawn counters.

### CTX-04 — Verify runtime-specific instruction delivery without changing authority
**AC/type/risk/status** — AC-06, positive/negative/integration/security, P0, **NOT RUN**.  
**Setup and action** — Send the same approved WorkerBundle through the pinned Codex adapter and a registered fake with different instruction-delivery and reasoning mappings. Verify each adapter's declared mechanism against observed command/config/input boundaries and Codex's offline loading behavior. Attempt unsupported mapping and agent-supplied adapter, provider or tool override. Compare effective manifests, mounts, auth exposure, role permissions and trusted gate inputs across the substitution.  
**Assert/failure** — Both adapters deliver the approved instructions through their verified mechanisms, preserve task identity, role/phase and authority, and map reasoning only through pinned supported capabilities. No private context or auth carries over between runtime/provider fixtures. Unsupported mappings fail before execution. Evidence names the actual CLI delivery mechanism and claims system-priority placement only if independently demonstrated. Unregistered Claude Code and OpenCode identifiers never cause a process launch.  
**Evidence** — Sanitized argv/config/input observations, delivery-mode and reasoning-map IDs, manifest/digest comparisons, denied overrides and zero unregistered launches.

## 4. Universal and conditional safety coverage

- AC matrix and per-case positive/negative/boundary assertions are recorded above. Operator and package inputs listed in §2 must be available before applicable execution. Synthetic fixtures and captured trusted effects are required even when agent output looks correct.
- Test private prompts, workspaces, capability leakage, prompt injection, unauthorized file access and outbound effects with canaries. Keep role probes free of operational secrets, Docker socket and active DB. Authorized operator-only smoke auth is handled out of band.
- Verify package, MTP and reviewed HEAD before execution and again before handoff. Independent TL inspects each harness assertion against the frozen design. Include actual commands, environment, exit codes, skips, attempts, provenance and artifact digests. Missing or flaky evidence blocks a required case. Record each definite defect with severity, AC/test ID, expected and observed behavior, reproducer, safe evidence, impact, owner and required verification.
- Duplicate/reordered operations and timeout/recovery are covered for role lifecycle, evidence and legacy stage transitions. N/A for real Claude Code/OpenCode integration and smoke, persistent semantic cache and conversational-memory reuse, parallel task throughput, live production deployment/continuity, PEM ACL remediation, new Executor/Podman, live GitHub App writes and future Controller gate automation. These require separate scope or authorization.

## 5. Gate order and completion criteria

1. A distinct Independent TL Reviewer checks Plan/AC/MTP on the exact submitted draft revision and records findings against AC/test IDs. TL Planner resolves requirements. QC revises the MTP as needed. Maximum two formal Plan/MTP submissions over the same package lineage.
2. Resolve blocking clarifications and freeze the full package. Independent operator records the external digests. Owner explicitly approves that reviewed package/version/hash before Dev.
3. Trusted Worker publishes the implementation Draft PR. Verify named exact-current-HEAD GitHub CI PASS and independent TL code review PASS before starting QC. Use trusted operator evidence until the applicable automated gates exist.
4. QC executes against the reviewed snapshot in a separate disposable session. Record all results in the separate [MTP Execution Report](../templates/mtp-execution-report.md). Every required case must have independently evidenced PASS or explicit Owner-approved N/A to clear QC. Unresolved FAIL, BLOCKED, NOT RUN or stale evidence prevents clearance.
5. Independent TL reviews the QC evidence. Owner alone decides the merge on the current exact HEAD. Changed HEAD invalidates CI/code/QC evidence. A post-QC repair HEAD requires separate independent code revalidation. Legacy WFC refuses a new handoff after APPROVED, so until that separate gate exists, stop BLOCKED for Owner reconciliation. Apply the separate two-round limits, stable keys and stop conditions in the [engineering standards](../conventions/engineering-standards.md).
