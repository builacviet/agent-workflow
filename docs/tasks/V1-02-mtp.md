# Master Test Plan — V1-02 Role Profiles from Deployment Environment

**PRE-DEV TEST DESIGN ONLY.** Draft design disposition: **BLOCKED** on the clarification requests in §2. All cases are **NOT RUN**. Post-code results belong in the separate [MTP Execution Report](../templates/mtp-execution-report.md).

## 1. Frozen design identity (no self-reference)

| Item | Value |
| --- | --- |
| Planning package ID / proposed version | `V1-02 / draft v0` is a provisional reference to the Notion kickoff. Canonical lineage, package version and external manifest are pending TL Planner/Owner confirmation. |
| Atomic task ID and AC source/revision | `V1-02`, [Notion task](https://app.notion.com/p/412f92014abd81e7b6b4d8d645553cde), snapshot last edited `2026-11-09T00:38:30.440Z`. The four top-level AC bullets are indexed below in source order. Kickoff proposals and historical notes are separately identified. |
| Repository / design baseline | `builacviet/agent-workflow`, `main@96f5d523814f94ebfec244c66e38d400e2144509` |
| QC policy | `required` given the role-selection, authorization and credential boundaries |
| MTP ID / version | `V1-02-MTP / 0.1-draft` |
| Changed-path scope / prohibited effects | PRE-DEV write scope is this file. Dev allowlist and DoD are pending. Post-code QC writes only disposable harness/evidence and the separate execution report. No production config, live DB, tunnel, real App PEM/token access, generic Docker access for roles, or live workflow/merge mutations. |
| Environment, bounds and fixtures | Offline credential-free sandbox by default. Target Ubuntu 24.04 on WSL2 and pinned Codex Docker image for operator-authorized smoke. Synthetic role markers, fresh workspaces and fake services as described below. Exact online timeout, output/token ceiling and egress policy pending G-04. |
| QC author/session identifier | `Codex QC / qc-predev-v1-02-mtp-20261109-01` (artifact-local author session label) |
| Dependencies and risks | V1-01 feasibility, merged role contracts from PR #27, legacy Phase 3.1 boundaries, open P0 design gaps G-01, G-02, G-04, G-05 and P1 gaps G-03, G-06. See §2. |

An independent trusted operator records the SHA-256 of the final raw UTF-8 MTP bytes and binds it to the frozen Plan, AC and task in the external package manifest. Package and MTP digests and Owner authorization are recorded externally. Byte changes after approval require a new version/hash and change control. [QC role](../roles/qc.md) · [Engineering standards](../conventions/engineering-standards.md)

## 2. Acceptance Criteria traceability

| AC ID | Observable requirement | Test case IDs | Coverage | Gap / risk |
| --- | --- | --- | --- | --- |
| AC-01 | Dev, QC and Independent TL Reviewer each resolve model and reasoning effort from deployment configuration. Invalid values cause explicit startup failure. | CFG-01–04 | Independent pair resolution, missing/invalid inputs, unsupported pairs and startup failure | G-01. P0 silent fallback or wrong role/model/effort. |
| AC-02 | Trusted runner alone selects the role. Prompts, workspaces and permissions are isolated across roles. | CFG-04, ISO-01–04, PERM-01–03, SEC-01–02 | All ordered role pairs, same-role successive runs, untrusted selectors, write/authority denials, path/mount escape and injection | G-02/G-05. P0 cross-role data or privilege exposure. |
| AC-03 | Share one Docker image where feasible and avoid three always-on role services. | IMG-01, LIFE-01 | Image identity and feasibility, lifecycle and uncertain stop | G-03. P1 image drift, lingering containers or unsupported claims about feasibility. |
| AC-04 | Record at least one isolated headless invocation per role with model/effort and redacted logs. | LIFE-01, SMK-01–03, EVD-01–02 | Online role completion, independent metadata, redaction, timeout and evidence failure | G-04/G-05. P0 unverified execution or disclosure in evidence. |
| V1 compatibility | Preserve existing Phase 3.1 Controller/Worker, MCP and WFC interfaces, GitHub App scope and CI/review boundaries. | REG-01–04 | Existing contracts, failure atomicity, stale inputs and no scope expansion | G-05/G-06. Existing tests and gate prerequisites apply. Future gate automation requires separately authorized scope. |

The matrix uses the Notion AC section as the requirements source. Missing configuration, per-run mounts, pinned-image evidence, one online smoke per role and deterministic pytest are detailed in the kickoff draft. Proposed missing/wrong profile IDs, unknown capabilities, attestation and gate binding are tracked separately where their contract is open. The merged role contracts supply permission and safety invariants.

### As-built comparison at the design baseline

| AC | Static source evidence | V1-02 verification target |
| --- | --- | --- |
| AC-01 | [Controller startup](../../src/agent_workflow/orchestration/controller.py#L83) loads JSON and calls `validate_config`. [Runner validation](../../src/agent_workflow/runners/coder_runtime.py#L72) requires one nonblank `model`. The runner sets `WFC_CODEX_MODEL` only for Code, and the [entrypoint](../../docker/coder_entry.py) supplies it to `codex exec` without a per-role reasoning-effort argument. | Exercise the normal trusted startup path for three independent pairs and show the selected pair reaches the actual CLI boundary. |
| AC-02 | [Runner](../../src/agent_workflow/runners/coder_runtime.py#L237) constructs argv, gives Prepare/Publish the PEM mount, Code the source and POC auth volume, and Test the source with `--network=none`. [Execution path](../../src/agent_workflow/runners/coder_runtime.py#L310) uses a fixed Code prompt prefix plus assignment context. [Worker](../../src/coder_worker/main.py) owns Git/GitHub effects. | Verify three trust-boundary profiles and both denied and allowed operations. Current source separation is a regression baseline. The host Controller has broad engine authority and source inspection does not verify deployed mounts or egress. |
| AC-03 | [Dockerfile](../../docker/coder.Dockerfile) pins the base image and Codex CLI. The Phase 3.1 runner passes one configured image through the four stages. [Compose](../../compose.controller.yaml) defines MCP and an optional tunnel. | Inspect image identity and actual lifecycle for three ephemeral Codex roles. Existing four-stage behavior is a compatibility constraint. |
| AC-04 | [V1-01 report](V1-01.md) records three separate headless `app-server` threads with candidate pairs, matched markers and reported thread settings. | Exercise the V1-02 trusted launch path on the reviewed implementation HEAD and independently capture effective settings and sanitized evidence. The readiness spike did not exercise V1-02 role enforcement. |

### Clarification requests and risk assessment

All requests concern `V1-02-MTP 0.1-draft`. TL Planner owns Plan/AC revisions, QC owns revised tests, and Owner decides the package scope and authorizes the execution environment.

| ID / AC or cases | Risk and evidence | Clarification and candidate options for review |
| --- | --- | --- |
| G-01 / AC-01, CFG-01–04 | **P0.** Notion gives example variable names and candidate pairs. Current startup validates one nonblank string. | Confirm canonical env keys, role IDs, valid model/effort source and combinations, case/whitespace policy, whether an invalid inactive role blocks all startup, and precedence against file/CLI settings. Candidate policies are a versioned supported-pair catalog or a trusted pinned capability snapshot. Confirm the startup diagnostic contract. |
| G-02 / AC-02, ISO-01–04, PERM-01–03 | **P0.** Role docs describe boundaries. Runtime has a single Code context. | Provide a role × phase matrix for permitted reads, output paths, tools, egress, auth context and reviewer evidence submission. Confirm whether profile IDs and unknown capabilities are in scope. Provide approved paths and an observable enforcement point for each prohibition. Options include runner-owned fixtures representing the approved profile matrix or approved phase-specific capability manifests. |
| G-03 / AC-03, IMG-01, LIFE-01 | **P1.** The AC qualifies image reuse with feasibility. | Define what evidence warrants an exception, who records that decision, and the cleanup/stop budget. Candidate acceptance is equal immutable image IDs for all roles or a documented, Owner-accepted exception while roles remain ephemeral. |
| G-04 / AC-04, SMK-01–03, EVD-01–02 | **P0.** V1-01 reported thread settings for a feasibility probe. It gives no V1-02 launch or evidence contract. | Confirm authorized isolated auth mechanism, allowed online egress and budgets, CLI observation surface for effective role/model/effort, reroute policy and safe output schema. A trusted CLI event or separately trusted attestation could be acceptable if it binds to the invocation. Missing effective evidence blocks acceptance. |
| G-05 / AC-02, AC-04, REG-04 | **P0.** Notion proposes self-approval and stale HEAD/MTP cases. Standards require provenance and external hashes. Legacy WFC has no planning/QC verdict and V1-03 owns the CI adapter. | Pin Plan/AC/DoD and exact Dev allowlist. Identify which package/HEAD binding and trusted evidence assertions V1-02 implements, and which remain manual or belong to later gates. Choose a documented operator preflight for undeployed gates or an explicitly scoped trusted seam. Keep role denial and provenance assertions in scope. |
| G-06 / REG-02–04 | **P1.** V1-01 establishes scoped App permissions/CI read. V1-13 still tracks PEM ACL/host continuity. | Confirm regression suite, supported auth modes and synthetic integration evidence acceptable for this task. Confirm live production App write, host ACL and service continuity verification remain in their separately authorized scopes. |

Primary release risks are cross-role or host-secret exposure (P0, ISO/PERM/SEC), silent config fallback or unverifiable invocation (P0, CFG/SMK/EVD), and stale or untrusted evidence advancing a gate (P0, REG-04). Image drift, lifecycle and legacy compatibility are P1 risks, with a P0 failure if cleanup uncertainty allows a duplicate or unauthorized execution. P0 gaps prevent finalizing the design for Owner approval.

## 3. Test cases

**Common harness and evidence.** Roles `D/Q/T` are Dev/QC/Independent TL Reviewer. Prepare role-specific synthetic prompt marker `PROMPT_<R>`, context marker `CTX_<R>`, private fixture file `private_<R>.txt`, disposable workspace `ws_<R>_<run>`, and per-run nonce. In offline config tests stub the capability catalog and spawn adapter: `fixture-a` supports `low`, `fixture-b` supports `medium`, `fixture-c` supports both. Assign `fixture-a/low`, `fixture-b/medium`, `fixture-c/low` to D/Q/T. These labels are test doubles and never sent to a provider. Use a fresh process and a clean environment per startup permutation. Compare exact allow/deny matrices and file digests using the independent harness. Set fake clock/budgets for timeout tests and use unique test-owned labels and disposable storage. Unit stubs prove input handling. Container isolation requires independent probes on the supported Docker host.

For every case, the trusted runner/operator collects test ID, matrix row, command/argv with values redacted as needed, exit status, bounded sanitized outputs, exact tested HEAD and tested snapshot digest, plus the case-specific evidence below. The separate execution report binds the frozen MTP digest and report/harness/artifact digests externally. `FAIL` means a reproduced mismatch to a frozen assertion. Unavailable authorization, platform, evidence or uncertain effects produce `BLOCKED` during execution. Remove only known test-owned artifacts after each case. If ownership or cleanup is uncertain, preserve sanitized diagnostics and stop for reconciliation. All cases below are **NOT RUN**.

### CFG-01 — Resolve three independent model/effort pairs
**AC/type/risk/status** — AC-01, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Start the real trusted resolver with the offline catalog and all six inputs populated. Launch D/Q/T with a recording CLI stub. Rotate the valid pairs across roles, then change one role's pair while keeping the other two fixed.  
**Assert/failure** — Each launch carries only the selected pair. The changed role changes at launch and the other pairs remain equal. Any default, bleed-through, unsupported fallback or discrepancy fails.  
**Evidence** — Input-label/role-to-argv matrix with launch counts and config source provenance.

### CFG-02 — Reject missing and blank configuration
**AC/type/risk/status** — AC-01, negative/boundary, P0, **NOT RUN**.  
**Setup and action** — Independently unset, empty and set whitespace-only for each of the six keys with the other five valid. Attempt startup and selected role launch. Include all six absent.  
**Assert/failure** — For the affected role the trusted startup exits nonzero, gives a sanitized role/field diagnostic and emits no invocation or publish effect. Assert the approved startup scope for an invalid inactive role once G-01 resolves. A started invalid role or silent fallback fails.  
**Evidence** — 19 input rows, startup statuses, diagnostic categories and zero affected-role launches.

### CFG-03 — Reject invalid, unsupported and injection-shaped values
**AC/type/risk/status** — AC-01, negative/security/boundary, P0, **NOT RUN**.  
**Setup and action** — Per role inject unknown model `fixture-missing`, unknown effort `unsupported`, known incompatible pair `fixture-a/medium`, and a literal containing newline plus shell metacharacters in each field. Use approved policy vectors for case or surrounding spaces once G-01 resolves.  
**Assert/failure** — Invalid cases fail explicitly before launch. No shell command, provider request, partial state write or substitution to another model/effort. Diagnostics identify the field without echoing control characters or raw input.  
**Evidence** — Startup/argv/effect trace with zero subprocess/provider operations for rejected configurations.

### CFG-04 — Keep untrusted overrides from changing the resolved pair
**AC/type/risk/status** — AC-01/02, security, P0, **NOT RUN**.  
**Setup and action** — Inject conflicting model/effort and escalation flags via assignment payload, prompt, workspace config and mock tool output. Put a distinct supported pair in trusted env. Exercise approved conflicting trusted sources after G-01.  
**Assert/failure** — Untrusted sources cannot change effective launch settings or authorize fallback. A launch that proceeds uses the trusted pair and approved sandbox. Ambiguous trusted inputs follow the approved fail-closed policy.  
**Evidence** — Per-source actual argv/config diff and side-effect trace.

### ISO-01 — Enforce trusted role selection
**AC/type/risk/status** — AC-02, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — Use the normal trusted launch entry for each role with conflicting caller role values in assignment, stdin and repo fixtures. Submit absent/unknown trusted role and an unauthorized selector. Include missing/wrong profile ID and unknown capability once G-02 confirms that interface.  
**Assert/failure** — Valid launches use the trusted role and its authorized prompt and capabilities. Invalid trusted selections or unauthorized entry requests fail before model invocation. Any role switch, privilege increase or fallback launch fails.  
**Evidence** — Trusted selection/dispatch trace, prompt digest and denied-launch counts.

### ISO-02 — Prove prompt and workspace separation for every ordered role pair
**AC/type/risk/status** — AC-02, integration/security, P0, **NOT RUN**.  
**Setup and action** — Run all six ordered D/Q/T pairs with separate synthetic private markers, plus two successive runs of the same role. Inspect launch input and mount metadata outside the container. Inside each role probe a known own fixture and the other run's known private path and canary. Use synthetic authorized shared evidence as a read-positive control.  
**Assert/failure** — Only the own role prompt/context and approved shared artifacts are exposed. Foreign private bytes are unreachable and do not appear in argv, stdin, env or output. New run starts without prior run's private workspace state. An inaccessible probe mechanism yields BLOCKED, while actual cross-role access fails.  
**Evidence** — Ordered-pair allow/deny results, mount/digest matrix and source provenance for positive controls.

### ISO-03 — Deny traversal and alias escape
**AC/type/risk/status** — AC-02, boundary/security, P0, **NOT RUN**.  
**Setup and action** — Try relative traversal, absolute external paths, symlink/hardlink aliases and an allowlist lookalike prefix against synthetic foreign files, using forms supported by the target filesystem. Read and write one legitimately allowed fixture as a positive control where policy permits.  
**Assert/failure** — Reject invalid mount/workspace roots before launch or deny the attempted access in the running sandbox as appropriate. Outside canaries remain unchanged and unread. Record host/container enforcement separately.  
**Evidence** — Valid/invalid path labels, read/write outcomes and before/after canary digests.

### ISO-04 — Treat artifacts and model output as untrusted inputs
**AC/type/risk/status** — AC-02, security, P0, **NOT RUN**.  
**Setup and action** — Put synthetic instructions to become Dev, bypass QC, approve the package, read another workspace or send a canary in a PR diff, test output and fake tool result. Replay a forged success record.  
**Assert/failure** — No change in trusted role or permissions, no protected read/write/egress, no accepted forged result or gate advancement. Assess enforced effects rather than wording of the agent response.  
**Evidence** — Denied operations, network sink counts and unchanged trusted state digest.

### PERM-01 — Apply least privilege for each role and phase
**AC/type/risk/status** — AC-02, positive/negative/security, P0, **NOT RUN**.  
**Setup and action** — Use approved fixture matrix from G-02. D writes one allowed source/test file and attempts adjacent out-of-scope writes and Git actions. PRE-DEV Q writes its proposed MTP only. POST-CODE Q writes isolated harness/evidence and attempts source and frozen MTP edits. T reads exact submitted evidence and attempts writes to source, MTP and review inputs.  
**Assert/failure** — Only approved operations succeed. Forbidden writes are denied or rejected before promotion, per approved enforcement contract, with no outside or trusted-state mutation. All automated untrusted roles lack GitHub write, Owner approval and merge authority.  
**Evidence** — Per-role operation matrix and changed-path, capability and artifact digests.

### PERM-02 — Fail closed with missing required permissions or mounts
**AC/type/risk/status** — AC-02, negative/reliability, P0, **NOT RUN**.  
**Setup and action** — Remove one required read mount or allowed output capability per approved profile, then deny the required access. Include missing smoke auth via a fake auth broker. Run a matched valid fixture first.  
**Assert/failure** — Safe explicit failure or BLOCKED outcome, no elevation/fallback, no published partial result and no successful handoff. A missing capability is not repaired by borrowing another role's mount or identity.  
**Evidence** — Matrix of required capabilities, preflight/runtime errors and no successful output transition.

### PERM-03 — Keep review and Owner authority independent
**AC/type/risk/status** — AC-02 and V1 gate policy, negative/security, P0, **NOT RUN**.  
**Setup and action** — Against the approved local gate or operator preflight from G-05, submit a QC self-review, replay an author identity as T, forge Owner approval, and request dispatch/merge from each role. A distinct authorized read-only reviewer receives the approved evidence as a positive control.  
**Assert/failure** — Requests do not create valid verdict, Owner decision, dispatch or merge. Legacy WFC code verdict API is never used to approve planning or QC evidence. A manual preflight only verifies the procedural gate.  
**Evidence** — Rejected operations, distinct nonsecret identities and unchanged synthetic gate state.

### SEC-01 — Keep host and Worker privileges outside all role containers
**AC/type/risk/status** — AC-02, integration/security, P0, **NOT RUN**.  
**Setup and action** — Create fake App PEM/token/DB, fake Git metadata, another role's auth context and a mock Docker endpoint in operator-owned disposable fixtures. Check effective mount/env/capability inventory and attempt targeted probes from D/Q/T. Use a trusted test-side read of canary fixtures as a positive control.  
**Assert/failure** — No role can read protected fixture contents or connect to the engine endpoint. No live paths, broad host mounts, App credentials, active WFC DB, uncontrolled GitHub/engine access or cross-role auth context enter a role. Check untrusted role runtime restrictions separately from trusted host engine authority.  
**Evidence** — Sanitized effective container manifest, denied read/connect codes and no captured protected markers.

### SEC-02 — Enforce the approved egress and subprocess boundary
**AC/type/risk/status** — AC-02, security/integration, P0, **NOT RUN**.  
**Setup and action** — Run each profile with fake network sinks for approved and denied destinations and a subprocess recorder. Try to pass a synthetic secret marker via denied tool/egress and inject shell metacharacters through untrusted data.  
**Assert/failure** — Approved mock requests follow the role budget. Denied sinks see no request or marker, and argv remains data. No permissive retry or new credentials/mounts are granted when a request is denied.  
**Evidence** — Role-specific sink counts, sanitized argv trace and effective network policy. Real smoke egress is covered separately.

### IMG-01 — Reuse a pinned image across ephemeral role launches
**AC/type/risk/status** — AC-03, positive/integration, P1, **NOT RUN**.  
**Setup and action** — Inspect changed image/service declarations and launch D/Q/T sequentially in a disposable runtime. Observe resolved image ID/digest and CLI version, service/restart policy and owned containers before, during and after.  
**Assert/failure** — The launches share immutable image identity and no role is declared or left running as an always-on service. A differing digest requires G-03's feasibility decision and evidence before acceptance. Existing MCP/optional tunnel are baseline infrastructure.  
**Evidence** — Image and CLI provenance matrix, declaration diff and labeled lifecycle inventory.

### LIFE-01 — Stop safely after role failure, timeout and ambiguous cleanup
**AC/type/risk/status** — AC-03/04, boundary/reliability, P0, **NOT RUN**.  
**Setup and action** — Use a deterministic clock and test-owned workload. Inject immediate exit, child timeout at the configured deadline, and a Docker adapter response where cleanup cannot be confirmed. Replay the same attempt request after uncertainty.  
**Assert/failure** — Child exits and confirmed cleanup remove only owned disposable state. Uncertain cleanup blocks progress and repeat dispatch until reconciliation. A late response cannot produce success for the timed-out attempt.  
**Evidence** — Timing/exit trace, labels, cleanup disposition, zero duplicate/publish effects. Confirm one real bounded cleanup in an approved isolated Docker session when available.

### SMK-01 — Dev completes one isolated online headless turn
**AC/type/risk/status** — AC-04, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Run the shared smoke protocol below as D with a dedicated disposable workspace and `V102_SMOKE_DEV_<nonce>`.  
**Assert/failure** — Protocol passes with Dev's approved pair and only Dev's expected marker. Any mismatch, reroute outside policy or forbidden tool effect fails.  
**Evidence** — Dev-specific trusted invocation record and cleanup receipt.

### SMK-02 — QC completes one isolated online headless turn
**AC/type/risk/status** — AC-04, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Run the same protocol as Q with a fresh workspace and `V102_SMOKE_QC_<nonce>`. Use the QC phase approved in G-02/G-04.  
**Assert/failure** — Protocol passes with QC's approved pair and only QC's expected marker. No source edits or review effects.  
**Evidence** — QC-specific trusted invocation record and cleanup receipt.

### SMK-03 — Independent TL Reviewer completes one isolated online headless turn
**AC/type/risk/status** — AC-04, positive/integration, P0, **NOT RUN**.  
**Setup and action** — Run the protocol as T in its separate read-only role workspace with `V102_SMOKE_TL_<nonce>`.  
**Assert/failure** — Protocol passes with T's approved pair and only T's expected marker. No artifact edits or verdict submission.  
**Evidence** — Reviewer-specific trusted invocation record and cleanup receipt.

**Shared smoke protocol.** On reviewed implementation HEAD, a separately authorized operator selects one role via the normal trusted runner, with approved disposable authentication supplied out of band and an empty synthetic workspace. Credentials are neither read by QC nor copied into evidence. Start the pinned CLI headlessly, with a request for the role marker and no actions, bounded by the approved time, output, token and egress budgets. Observe provider completion, exact role marker, positive output usage where supported, exit zero and cleanup. Record role from the trusted launcher, requested and effective model/effort from trusted CLI/runtime telemetry, image ID, CLI version, invocation identity, snapshot digest, reviewed HEAD and redaction/cleanup results. Require the approved pair and invocation identity to match the record. Three distinct invocations are required even when pairs coincide. V1-01 examples are `gpt-6-luna/low` for Dev and QC and `gpt-6-luna/medium` for T. Verify selected pairs against the approved policy at execution. Missing auth, telemetry or budget authorization yields BLOCKED. Run no tools that touch real tasks, GitHub, WFC or Controller state.

### EVD-01 — Redact before publishing evidence
**AC/type/risk/status** — AC-04, security/negative, P0, **NOT RUN**.  
**Setup and action** — Inject fake token/PEM/account canaries into stdout, stderr, structured events and startup/error/timeout paths. Include split chunks and escaped representations for supported formats. Capture both success and failure artifacts through the normal evidence path.  
**Assert/failure** — No canary is visible in persisted/shareable evidence, summaries or exception text. Only approved safe fields remain available with enough provenance to verify the result. Reject unsafe or malformed capture rather than exporting raw logs.  
**Evidence** — Per-channel redaction assertion, bounded sanitized excerpts and artifact digest. Keep only synthetic markers in the test oracle.

### EVD-02 — Reject incomplete, stale or spoofed smoke evidence
**AC/type/risk/status** — AC-04, negative/boundary, P0, **NOT RUN**.  
**Setup and action** — At the trusted verification seam replay a successful Dev record as QC, omit effective effort, switch model, alter invocation ID or snapshot SHA, supply a marker alone, inject nonzero exit/timeout and duplicate a valid record.  
**Assert/failure** — Only one qualifying record per role counts toward three-role coverage. Contradictory or nonzero results fail. Missing or uncertain provenance blocks acceptance. No fake or replayed evidence advances a gate.  
**Evidence** — Verification reason matrix with per-role completion count and retained sanitized attempts.

### REG-01 — Preserve Phase 3.1 stage and recovery contracts
**AC/type/risk/status** — V1 compatibility, regression/integration, P0, **NOT RUN**.  
**Setup and action** — Run baseline `tests/test_architecture_boundary.py` and `tests/test_phase31.py` with fake adapters and disposable state. Drive success, stale assignment, source mutation during Test and an uncertain publish result through the Controller boundary.  
**Assert/failure** — Prepare → Code → Test → Publish order and result identity persist. Test stays offline/credential-free. No publication of an untested or mutated snapshot, duplicate dispatch or success on uncertain outcome. Worker owns Git/App effects.  
**Evidence** — Stage trace, snapshot hash before/after Test and ledger/outbound call trace.

### REG-02 — Preserve nine MCP tools and legacy review contracts
**AC/type/risk/status** — V1 compatibility, regression/security, P0, **NOT RUN**.  
**Setup and action** — Compare exact base and reviewed HEAD through offline `/mcp` transport with ephemeral SQLite and mocked auth. Check `ping`, `get_server_info`, `list_review_queue`, `get_review_context`, `claim_review`, `submit_review`, `get_review_result`, `register_task_handoff`, `get_coder_assignment`. Exercise default Bearer public ping/info and protection of seven workflow tools, then Auth0 middleware protection for all `/mcp` using fake claims/keys. Verify the documented disposable-only POC bypass boundary without live endpoint access.  
**Assert/failure** — Names, schemas, response shapes, auth behavior, SQLite schema/history and exact task/SHA rules match baseline. Two formal WFC submissions total including PASS, no extra handoff after APPROVED and no planning/QC verdict through legacy API.  
**Evidence** — Baseline/target contract diff, synthetic auth result and round/state transition tables.

### REG-03 — Preserve Worker-only scoped App and Draft publishing
**AC/type/risk/status** — V1 compatibility, regression/security, P0, **NOT RUN**.  
**Setup and action** — Reuse the fake GitHub App and disposable bare remote fixtures in `tests/test_phase31.py` and `tests/test_architecture_boundary.py`. Check wrong repository, stale SHA and unauthorized ref, then the allowed scoped publish path.  
**Assert/failure** — App credentials stay Worker-only and repository-scoped. Only the permitted ordinary task-branch push and Draft PR path are observed. No forced push, main write, merge or automatic repeat after ambiguous publish.  
**Evidence** — Sanitized mock scope and write-call matrix, disposable ref history and zero unauthorized effects.

### REG-04 — Require exact package/MTP/HEAD evidence and preserve CI gates
**AC/type/risk/status** — V1 compatibility, regression/security, P0, **NOT RUN**.  
**Setup and action** — Supply matching and mismatched package/MTP digest, exact current reviewed SHA versus old SHA, CI pending/failed/missing/ambiguous, synthetic PR merge SHA only, and same-SHA success after recorded failure without Owner acknowledgment. Exercise trusted seam if scoped, otherwise verify operator preflight under G-05. Run approved existing Python test suite offline against exact reviewed HEAD as a repository regression check.  
**Assert/failure** — Only the approved frozen package and current HEAD with trusted successful named exact-HEAD push CI and independent TL verdict can enter QC. Missing/stale/ambiguous evidence is BLOCKED, a completed required failure follows separate CI repair policy, and no stale or synthetic-merge-only result clears the gate. Preserve legacy limits and avoid claims of automated V1-03/05/10 enforcement from a manual test.  
**Evidence** — Manifest and tested digest, CI provenance including run/check IDs, event, attempt, tested SHA and history, TL verdict binding and offline pytest exit/skips. No production state changes.

## 4. Universal and conditional safety coverage

- AC matrix and per-case positive/negative/boundary assertions are recorded above. Open policy-dependent assertions remain BLOCKED for design approval. Synthetic fixtures and captured trusted effects are required even when agent output looks correct.
- Test private prompts, workspaces, capability leakage, prompt injection, unauthorized file access and outbound effects with canaries. Keep role probes free of operational secrets, Docker socket and active DB. Authorized operator-only smoke auth is handled out of band.
- Verify package, MTP and reviewed HEAD before execution and again before handoff. Independent TL inspects each harness assertion against the frozen design. Include actual commands, environment, exit codes, skips, attempts, provenance and artifact digests. Missing or flaky evidence blocks a required case. Record each definite defect with severity, AC/test ID, expected and observed behavior, reproducer, safe evidence, impact, owner and required verification.
- Duplicate/reordered operations and timeout/recovery are covered for role lifecycle, evidence and legacy stage transitions. N/A for parallel task throughput, live production deployment/continuity, PEM ACL remediation, new Executor/Podman, live GitHub App writes and future Controller gate automation. These require separate scope or authorization.

## 5. Gate order and completion criteria

1. Independent TL Reviewer checks Plan/AC/MTP on the exact submitted draft revision and records findings against AC/test IDs. TL Planner resolves requirements. QC revises the MTP as needed. Maximum two formal Plan/MTP submissions over the same package lineage.
2. Resolve blocking clarifications and freeze the full package. Independent operator records the external digests. Owner explicitly approves that reviewed package/version/hash before Dev.
3. Trusted Worker publishes the implementation Draft PR. Verify named exact-current-HEAD GitHub CI PASS and independent TL code review PASS before starting QC. Use trusted operator evidence until the applicable automated gates exist.
4. QC executes against the reviewed snapshot in a separate disposable session. Record all results in the separate [MTP Execution Report](../templates/mtp-execution-report.md). Every required case must have independently evidenced PASS or explicit Owner-approved N/A to clear QC. Unresolved FAIL, BLOCKED, NOT RUN or stale evidence prevents clearance.
5. Independent TL reviews the QC evidence. Owner alone decides the merge on the current exact HEAD. Changed HEAD invalidates CI/code/QC evidence. A post-QC repair HEAD requires separate independent code revalidation. Legacy WFC refuses a new handoff after APPROVED, so until that separate gate exists, stop BLOCKED for Owner reconciliation. Apply the separate two-round limits, stable keys and stop conditions in the [engineering standards](../conventions/engineering-standards.md).
