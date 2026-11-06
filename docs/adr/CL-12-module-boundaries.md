# CL-12.3 — Target package boundaries and DRY ownership

## Authority, baseline and limits

Design proposal for `builacviet/techlead-review-poc`, 2026-11-06.
Actual base and fetched `origin/main` before work:
`1d0974ed4198aff041fb8d0032b90977fc167a80`.
Branch: `codex/cl-12.3`; dedicated worktree:
`D:/WORK/Project/techlead-review-poc-CL-12.3`.

[Authorized CL-12.3 task](https://app.notion.com/p/410f92014abd8100854dd97f561cfc47)
and [parent Decision Set v0.2](https://app.notion.com/p/410f92014abd81cdbceded689b7d4945)
control this documentation-only proposal. CL-12.2 PR #8 is merged at the base
above; its inventory and full PR handoff were read. Governing context:
`AGENTS.md`, `docs/architecture.md`, `docs/review-policy.md`, `docs/handoff.md`,
`docs/tasks/CL-12.md`, `docs/adr/CL-12-current-state.md`, source and boundary tests.
Later explicit authorization supersedes historical locked-subtask notices; it
does not approve the final parent ADR or release implementation/CL-12.4.

Only `docs/adr/CL-12-module-boundaries.md` changes. All trees, module names,
ports and refactor slices below are proposed targets, not existing packages or
approved tasks. No source/tests/build/config/dependency/DB/auth/secret/volume
changes, Controller/worker launch, Docker experiment, migration, v2 operation or
Phase 3.2 execution is included. Handoff evidence stays in PR/chat.

Evidence references are repository-relative, one-based lines at the base above.
The inventory's original evidence SHA is `e3086a058c488db79f0f514aa19e926ec3e1d265`;
PR #8 added documentation only, so cited source spans remain applicable.
**Observed** labels describe current code; **Proposed** labels describe target
ownership. Potential risks are not claims of demonstrated exploitation.

## Proposed target tree and responsibilities (AC1)

```text
src/
  workflow_engine/                 # shared workflow library, not a service
    domain/
      contracts.py                # pure workflow values and validation rules
      policy.py                   # review/status/round/SHA-equality rules
      errors.py                   # domain-facing aliases/specializations
    application/
      service.py                  # existing WorkflowService use cases
    ports/
      repository.py               # WorkflowRepository/WorkflowUnit contracts
      dispatch.py                 # durable dispatch claim/state storage contract
    infrastructure/sqlite/
      repository.py               # concrete units, connections, transactions
      dispatch.py                 # concrete DispatchLedger persistence
      schema.sql                  # preserve schema resource; CL-11 owns migration
      migrations/                 # reserved ownership, subject to CL-12.4/CL-11
    operator/
      admin.py                    # explicit provision/import/fixture CLI
      bootstrap.py                # operator composition/config/resource loading
      resources/                  # historical reference, never task authorization
  mcp_server/
    server.py                     # MCP composition root and entrypoint
    tools.py                      # thin authenticated tool adapters
    dto.py                        # framework DTO parsing/serialization adapter
    config.py                     # transport and deployment settings
    auth/                         # bearer, Auth0/OAuth, verified identity
  controller/
    main.py                       # controller root, CLI/process lock/lifecycle
    dispatcher.py                 # run_once, budget, fail-closed orchestration
    runtime.py                    # typed executor client, no raw Docker control
    config.py                     # logical tasks/workspaces, operator validation
  worker_executor/                 # trusted service, only after ADR-06 gates
    main.py                       # composition root/internal IPC lifecycle
    contracts_adapter.py          # decode, authenticate, validate bounded jobs
    policy.py                     # approved images/mounts/stages/resources
    workspace.py                  # host-real paths, ownership, stage mapping
    docker_adapter.py             # dedicated worker engine lifecycle/cleanup
  coder_worker/                     # trusted Git/GitHub prepare and publish
    main.py                       # trusted stage entrypoint/composition root
    app_auth.py                   # GitHub App key/token lifecycle
    github.py                     # remote SHA/ancestry verification adapters
    http_transport.py             # bounded private HTTP child process
    repository.py                 # Git, snapshot and allowlisted publication IO
  worker_contracts/                 # optional minimal pure lower-level library
    sha.py                        # lexical SHA policy, no remote checks
    paths.py                      # lexical relative-path/write-prefix rules
    deadlines.py                  # pure remaining-budget calculations
    errors.py                     # minimal shared error identity
    jobs.py                       # immutable narrow executor/worker DTOs
  order_service/                    # sample app, remains entirely independent
    __init__.py
    models.py
    service.py
```

`worker_contracts` is a conditional, responsibility-scoped shared library, not a
new runtime service or a mandatory catch-all. Its name does not grant worker
capabilities. Introduce each module only after proving matching semantics and at
least two real consumers. Do not add a parallel `shared/utils.py` hierarchy.
No shared package may load env/secrets, inspect files, open sockets/DBs, invoke
Docker/Git, initialize external resources or perform import-time IO. Clock
samples are inputs to pure deadline calculations; actual clocks, ContextVar
lifecycle and cancellation are adapter-owned. A small stable error type or
immutable value definition is not an external side effect.

Framework-neutral core is the end state. Current Pydantic validation behavior
must be preserved at the adapters/domain boundary, including strict types,
extra-field rejection and error behavior. Moving a file alone does not achieve
this. Separating existing Pydantic workflow DTOs from pure values requires its
own characterization and independently approved slice; no silent broadening of
accepted input is allowed. No FastMCP/Pydantic rewrite is implemented here.

## Proposed import and capability rules

| Importer/owner | Permitted targets | Forbidden targets/capabilities |
| --- | --- | --- |
| workflow_engine.domain | stdlib pure types and justified pure worker_contracts modules | MCP, OAuth, Pydantic transport DTOs, SQLite adapter, controller, executor, coder_worker, network/Git/Docker/secret loading |
| workflow_engine.ports | domain and pure contracts/Protocol definitions | Concrete adapters, process/service entrypoints and external IO |
| workflow_engine.application | domain, ports, pure value/policy helpers | Concrete SQLite default construction, FastMCP/auth, controller, executor, coder_worker, GitHub/Docker transport |
| workflow_engine.infrastructure.sqlite | domain, ports, pure timeout/error helpers, sqlite3/local storage | MCP/auth/GitHub/worker/Docker; must not import service to obtain raw DB handles |
| mcp_server | application/ports/domain, its DTO/auth adapters; root may wire SQLite | Controller/Executor/Worker transport, Docker socket and GitHub App keys; auth adapters alone own necessary identity-provider HTTP |
| controller | application/ports, pure contracts, injected executor client; root may wire SQLite and config | Git/GitHub operations/App PEM possession, raw Docker engine API/CLI, imports of executor implementation or coder_worker |
| worker_executor | pure contracts, service-local policy/workspace/Docker adapter | Workflow DB/service, MCP OAuth, worker App auth library, unbounded caller-supplied Docker arguments; only executor adapter gets reviewed worker-engine capability |
| coder_worker | own modules plus pure shared contracts | workflow_engine DB/application, mcp_server, controller, executor implementation, Docker socket; code/test receive no GitHub App key, repo metadata or workflow DB |
| worker_contracts | stdlib pure types/calculations only | All higher packages, framework/IO/credentials; never import consumers back |
| order_service | itself and stdlib | Any workflow, MCP, controller, executor or worker package; runtime dependencies remain empty |

Composition roots are the explicit exception for wiring, not for business logic:
MCP root wires auth/DTOs, WorkflowService and SQLite; Controller root wires service,
dispatch storage, clock and executor client; operator bootstrap wires admin/storage
without importing MCP tools; Executor root wires IPC authentication, local policy,
workspace provider and engine adapter; Worker main wires App client/Git/HTTP.
Core imports never reverse into a root. Pure shared errors/SHA may be used by core
without importing a worker implementation. Imports must remain acyclic.

DI boundaries: inject WorkflowRepository/WorkflowUnit into WorkflowService rather
than letting application construct SQLite; inject a DispatchStore into dispatcher
instead of ledger reaching through service.repository.raw_*; inject Clock/Budget
at lifecycle adapters and executor client into Controller; inject engine/workspace
providers into Executor and GitHub/HTTP/Git adapters into trusted worker tests.
Use cases continue to enforce authorization, exact SHA, lease, rounds, immutable
history and idempotency; adapters cannot bypass these rules.

## Complete current-module mapping (AC2)

All 24 modules from CL-12.2 are represented once below. Targets describe eventual
ownership after rename/extraction, not changes authorized in this PR. Source
paths are under `src/`. Split means separately characterized responsibilities,
not a license to duplicate code. New target-only modules are not extra current
modules. Package markers must stay free of eager privileged imports.

| Current module and evidence | Disposition | Proposed destination / wiring constraint |
| --- | --- | --- |
| my_wfc/__init__.py:1 | Rename, then narrow | workflow_engine/__init__.py; package marker, no service startup |
| my_wfc/admin.py:7–9,18–100 | Move/split | workflow_engine/operator/admin.py and bootstrap.py; replace tools.default_service dependency with operator-local composition; historical resources remain packaged |
| my_wfc/auth0_oauth.py:19–21,35–239 | Move | mcp_server/auth/auth0_oauth.py; keep verifier configuration/JWKS/claims semantics and sanitized logs |
| my_wfc/budget.py:16–55 | Split; optional pure share | worker_contracts/deadlines.py for matching calculations; ContextVar dispatch/cleanup scopes and actual clock sampling live in adapter budget modules; preserve cancellation/cleanup separation |
| my_wfc/coder_runtime.py:20–123,148–401 | Split in separate stages | controller/runtime.py becomes executor client/result handoff orchestration; worker_executor owns raw Docker, host workspace/config/mount policy and cleanup; pure lexical rules only to worker_contracts if justified. Preserve current host adapter until gated replacement |
| my_wfc/contracts.py:3–51 | Split, preserve schema | pure workflow contracts in workflow_engine/domain/contracts.py; framework DTO adapter in mcp_server/dto.py and operator parsing boundary; service receives validated pure values. Characterize validation/error compatibility before removing Pydantic from core |
| my_wfc/controller.py:23–49,53–143 | Move/split | controller/main.py (root/lock/CLI) and dispatcher.py (claims/run_once); no GitHub capability; runtime client injected |
| my_wfc/dispatch_repository.py:7,13–100 | Move/split | workflow_engine/infrastructure/sqlite/dispatch.py plus ports/dispatch.py; explicit storage DI, no WorkflowService/raw-handle dependence. DDL relocation belongs only to CL-11 |
| my_wfc/github.py:5–11 | Rename/split; optional pure share | worker_contracts/errors.py and sha.py for shared minimal identity/syntax; domain/errors.py exposes domain-facing types without remote adapter dependency. Retain ValueError-compatible WorkflowError identity/handling |
| my_wfc/paths.py:6–17 | Move | composition config/bootstrap for DB location; one authoritative operator DB-path resolver wired into MCP/admin/Controller. Never make worker_contracts choose a DB path |
| my_wfc/repository.py:11–37,40–235 | Split | Protocols to workflow_engine/ports/repository.py; SQLiteUnit/repository to infrastructure/sqlite/repository.py. Constructors/schema behavior unchanged until separately authorized CL-11 work |
| my_wfc/security.py:7–62 | Move | mcp_server/auth/security.py; preserve bearer/verified OAuth identity and disposable POC no-auth constraints; do not move credential logic into shared code |
| my_wfc/server.py:39–103 | Move/split | mcp_server/server.py root and config.py; preserve HTTP transport, DNS-rebinding protections, public ping/info and explicit startup |
| my_wfc/service.py:7–41,64–482 | Move/split | workflow_engine/application/service.py; domain policy/values extracted only after tests; remove default adapter construction via root DI, not business-rule rewrite |
| my_wfc/tools.py:2–17,20–62 | Move | mcp_server/tools.py; root supplies factory, adapters authenticate and map DTOs; no dispatch behavior |
| coder_worker/__init__.py:1 | Remain | package marker; no workflow dependency introduced |
| coder_worker/app_auth.py:14–15,21–232 | Remain; replace helper dependency only | trusted App/HTTP lifecycle stays worker-local; inject pure budget calculations/adapter budget; never share token/key management |
| coder_worker/github.py:8–45 | Remain; rebind pure helpers | remote verification remains worker-owned; replace my_wfc error/SHA import with justified pure contracts. Legacy gh transport removal requires separate consumer audit |
| coder_worker/http_transport.py:9–62 | Remain | fixed-origin no-redirect private HTTP subprocess, response bounds; no common HTTP abstraction imposed |
| coder_worker/main.py:11–14,57–232 | Remain; narrow imports | trusted prepare/publish root; use pure shared values only where needed, keep App auth, exact remote checks, allowlisted bot publication and result handling |
| coder_worker/repository.py:5–62,65–178 | Remain/split helpers | Git/snapshot/change filesystem IO remains worker-local; optional lexical path helper sharing, typed filesystem facts without moving filesystem access into common |
| order_service/__init__.py:1–6 | Remain | same independent public exports |
| order_service/models.py:1–51 | Remain | same frozen Decimal-based domain, validation/totals behavior |
| order_service/service.py:1–20 | Remain | same isolated in-memory service and IDs |

Non-Python resource ownership: current my_wfc/schema.sql follows SQLite package;
resources/TASK-001-reference.md follows operator historical import. Packaging
must keep resource lookup working when installed from a wheel. No schema,
fixture history or task authorization changes follow from moving a resource.
The proposed worker_executor has no existing package: it is a new trust boundary,
not a rename of an already isolated service. `docker/coder_entry.py` remains an
image entrypoint responsibility to be assigned during separate packaging work.

## DRY ownership and policy matrix (AC3)

| Rule/contract | Observed evidence / matching consumers | Proposed owner and reuse boundary | Parity/negative gate for later implementation |
| --- | --- | --- | --- |
| SHA syntax | my_wfc/github.py:9–11, imported by service/ledger/runtime and coder_worker/github.py:8 | Optional worker_contracts.sha; full lowercase 40-hex syntax is one rule with genuine workflow/worker consumers | Reject malformed/type-invalid SHA identically; remote truth and stored equality remain separate |
| Remote head/base ancestry | coder_worker/github.py:29–45; main.py:76–80,148–156 | Worker GitHub adapter; Tech Lead independently checks through GitHub plugin. Never core/common network logic | Preserve pre-fetch/pre-push/post-push checks at distinct times; stale head/base rejection, ordinary push only |
| Relative path/write-prefix policy | my_wfc/coder_runtime.py:20–74 and coder_worker/repository.py:8–62 repeat constants and is_linked/safe_path/writable/independent_path | Pure lexical subset in worker_contracts.paths when semantics match; filesystem link/reparse/resolve checks belong to privileged adapter. Shared predicates may accept already-observed facts, never call stat/resolve themselves | Case/device names, separators, traversal, denied secret paths, prefix boundaries; Windows/Linux parity plus adapter link tests |
| Workspace access and source integrity | coder_runtime.py:126–145,308–325; coder_worker/repository.py:96–178 | Executor owns stage mapping/permissions; Worker owns Git snapshot/provenance/changed-file IO; digest algorithm may be shared only after a second genuine consumer is proven | No .git/DB/App keys in code/test, no unauthorized mounts/paths, framing ambiguity and post-test mutation checks retained |
| Deadline arithmetic | my_wfc/budget.py:16–46; consumers in Controller, SQLite adapter and worker App/Git | Pure remaining-time/finite-deadline calculations optionally shared; inject wall/monotonic samples. Context scopes, clock reads and process/network cleanup stay adapter-local | Deadline expiry/clock rollback, full execution budget across stages, no later publication/handoff/DONE after expiry |
| Cancellation/cleanup | coder_runtime.py:162–233; coder_worker/app_auth.py:26–59; docker/coder_entry.py:9–16 | Each process owner retains bounded kill/reap and cleanup grace. Do not merge host/container/HTTP lifetimes into one generic helper | Timeout after remote effects remains uncertain; cleanup still possible after budget expiry; no automatic retry |
| Error identity | my_wfc/github.py:5; worker re-export via coder_worker/github.py:8 | Minimal shared WorkflowError only if matching semantics; domain and adapter errors remain explicit, stable catch hierarchy; no credential-bearing messages | Existing ValueError/catch behavior, sanitized transport failure and blocked-reconciliation behavior retained |
| Workflow DTOs | my_wfc/contracts.py:6–51; service.py:8 and tools.py:5 | Domain owns review/task semantics; MCP/operator adapters own Pydantic parsing and translation. No worker dependency on workflow DTO bundle | Strict strings/types, extra=forbid, round bounds, serialization, validation order and rejection compatibility characterized |
| Worker/executor envelopes | coder_runtime.py:326–334,357–388 and coder_worker/main.py:195–202 | Optional immutable worker_contracts.jobs after field/consumer audit; identifiers, SHA, bounded parameters and result reference only. Service adapters decode/authenticate; no arbitrary host paths/flags or secret values | Schema/version compatibility, mismatched task/input/result/PR, unknown fields and out-of-bounds arguments; final executor wire details await CL-12.5 |
| Review policy | service.py:115–180,208–233,287–442,456–457 | workflow_engine.domain/application owns round/claim/status/exact-SHA/idempotency rules; MCP and Controller reuse use cases, never independent policy copies | Two-round limit, immutable history, duplicate submission, authorization and unknown remote freshness maintained |
| Privileged security policy | security.py:25–62; auth0_oauth.py:120–239; coder_runtime.py:235–305; worker app_auth.py:103–232 | MCP owns OAuth/bearer; Executor owns image/mount/engine/UID/network/resource policy; Worker owns App credentials; code-only model auth remains a narrowly scoped mount | No transfer of capabilities via common package; trusted prepare/publish and untrusted code/test stay isolated |

Unprefixed workflow evidence in this table refers to src/my_wfc; worker paths
refer to src/coder_worker; docker paths are repository-root-relative. These are
ownership decisions for a proposal, not reports of applied fixes. Only lexical
path validation is observed copied; SHA helper is already shared through an
inappropriate package dependency, not duplicated. Multiple timeout checks protect
different lifetimes. Shared abstraction requires semantic proof, not matching
function names. No service-local privileged helper is promoted to common merely
to reduce line count. Existing repository interface is reused through DI rather
than creating parallel MCP/Controller implementations.

## Process/image packaging and data boundary

Proposed separate process images/entrypoints: MCP -> mcp_server.server;
Controller -> controller.main; Executor -> worker_executor.main (gated);
Worker -> coder_worker.main for prepare/publish and trusted entry shim for
code/test; operator admin is an explicit CLI, not a background service.
workflow_engine and worker_contracts are libraries, not new services.

| Process/image | Required packages/capabilities | Exclusions |
| --- | --- | --- |
| MCP | workflow domain/application/SQLite, mcp_server auth/DTO adapters, required MCP/JWT dependencies | No worker package/Git/GitHub App key/engine socket; no implicit Controller start |
| Controller | workflow library/storage at composition root, controller executor client and pure contracts | No App auth, GitHub client, Git CLI or host-wide Docker authority |
| Executor | worker_executor and pure contracts, reviewed worker-engine transport/workspace provider | No workflow SQLite or MCP auth/App token logic; no generic Docker endpoint; no privileged DinD |
| Trusted worker | coder_worker and justified pure contracts plus Git/OpenSSL/HTTP dependencies | No workflow app/DB/MCP/controller modules; App key only in prepare/publish stage mount |
| Code/test stage | Source snapshot and tools required for task; code gets model auth only, test gets neither model nor GitHub auth and no network | No repo/.git, App PEM, workflow DB or Docker socket; installed tooling alone is not authority |
| order_service | stdlib only as independent example | No new runtime dependency on workflow/shared worker packages |

Observed `docker/coder.Dockerfile:11` currently copies all src, and
`docker/mcp.Dockerfile:12–13` copies my_wfc/order_service; these exceed target
process needs. Narrow copies/install sets only in an independently approved
packaging task, with wheel/resource/import tests. Do not claim that current
images satisfy this table. Worker phase separation can remain in one narrowly
packaged worker image if later threat review accepts it; this proposal does not
require additional microservices or silently create a code-image project.

MCP and Controller are the workflow DB writers through injected SQLite adapters;
Executor/Worker/code/test have no workflow DB access. Target persistence remains
single-host Linux-local Docker-managed volume per ADR-04, not a new store here.
SQLite versioning/DDL/backup and transactional dispatch remain CL-12.4/CL-11
responsibilities. Rootless dedicated worker daemon preference, fallback host
executor with explicit Owner approval and Linux/Desktop feasibility remain
CL-12.5 gates. Workspace/Compose parity and operations remain CL-12.6.

### Required nonblocking permission trace

**Observed:** `src/my_wfc/coder_runtime.py:324–325` creates stage with mode 0700
then calls `stage.chmod(0o777)`. Worker also broadens source permissions
(`src/coder_worker/main.py:83–84`, repository.py:125–130).
**Potential Issue:** ownership and effective host/UID access need threat-model
review; broad mode alone is not proof an attacker can reach or exploit this
stage on the deployed host. No runtime/ACL experiment was performed here.
**Follow-up:** CL-02 and CL-12.5 must track executor workspace ownership,
trusted-stage/source separation, Windows ACL versus Linux UID/mode mapping,
minimum necessary permissions and hostile local/source access tests. No change
to these permissions is authorized by CL-12.3. Review this separately from a
behavior-preserving extraction; never hide a permissions change in a rename.

## Proposed atomic rollout and rollback gates (AC4)

The following are candidate slices, not created task records or execution
authorizations. Owner/Tech Lead must approve exact tasks, allowlists and order.
Each slice gets one branch/worktree/PR, exact-SHA review (maximum two formal
rounds), ordinary push, Owner-approved merge and explicit next-task release.
No slice combines namespace rename, policy rewrite, schema migration and engine
privilege redesign.

| Order / candidate slice | Scope and dependencies | Required independently verifiable gate | Rollback boundary |
| --- | --- | --- | --- |
| 0: CL-07 regression baseline | Characterize existing state/review/auth/worker behavior before rename | Existing complete suite plus planned strict DTO, import, resource, error/deadline/path parity checks; no production invocation | Tests/docs only; revert isolated baseline commit if needed |
| 1: CL-09 namespace only | my_wfc -> workflow_engine with mechanical import/resource/entrypoint reference updates; retain flat structure/behavior initially | Complete suite, import and installed-wheel/resource/CLI resolution; no service extraction, pure-core claim or DDL change | Revert source/build reference rename as a unit; no DB path/schema change |
| 2: Pure error/SHA dependency slice | Minimal justified pure contracts; redirect worker imports without changing validation/exception identity | SHA parity, error catches, SHA/error consumers independent of workflow imports, no side effects/cycles; whole-worker independence waits for slice 4 | Revert contract/import slice; no persisted state or wire format changes |
| 3: Pure lexical path slice | Shared relative-path rules only; retain filesystem checks in adapters | Differential Windows/Linux lexical cases and existing link/allowlist regressions; no chmod/mount change | Revert shared helper and callers together |
| 4: Deadline calculation slice | Pure arithmetic reuse, explicit adapter context/clock/cancellation ownership; no new timeout semantics | Existing deadline/clock rollback/HTTP trickle/cleanup and no-later-effect tests; adapter-specific cancellation maintained | Revert calculation/wiring changes together; blocked dispatch reconciliation remains operator-owned |
| 5: SQLite ports/composition slice | Split current repository Protocols/concrete adapters; injectable dispatch store removes service.raw_* coupling; schema initialization behavior unchanged | Memory injection, atomic lease/single-slot/idempotency/concurrency and rollback tests; installed resources intact | Revert source wiring only; DB bytes/schema unchanged, no migration |
| 6: Core validation/application boundary slice | Separate framework DTO parsing and pure workflow validation/service; characterize input/error/serialization compatibility first | Strict Pydantic input parity, review/claim/history/two-round/exact-SHA invariants; pure-core imports blocked from frameworks/adapters | Revert adapter/domain mapping together, no stored metadata retargeting |
| 7: MCP package extraction | Move server/tools/auth/config and composition root; operator bootstrap no longer imports MCP tools | Bearer/OAuth/Host policy, seven-tool signatures, public ping/info, explicit startup and wheel/entrypoint smoke in later authorized environment | Revert package/entrypoint references; no live auth/tunnel changes in extraction |
| 8: Controller package extraction | Move CLI/dispatcher/current runtime adapter with injected ports; raw host adapter remains until next design gate | Lifecycle, result validation, timeout, crash BLOCKED/ack, no Git/GitHub and no round consumption regressions | Revert package wiring; uncertain dispatches stay blocked, never auto-retry |
| 9: Worker envelope contract slice (conditional) | Extract only proven shared job/result values after CL-12.5 contract decision; preserve existing envelope semantics | Matching task/input/SHA/PR checks, bounded fields, compatibility fixtures, no secrets/host paths in public job API | Revert producers/consumers together; never mix incompatible versions on active job |
| 10: Per-process packaging slices | Separate PR for MCP, Controller, Worker package/install/COPY reduction after their imports stabilize; Executor packaging follows its implementation | Import allowlist/absence checks, wheel resources, independent image entrypoints and phase mount/credential regressions; builds only when authorized | Restore prior pinned artifact without dispatch activation or volume removal |
| 11: Dedicated Executor design implementation (new authorization) | Only after CL-12 final sign-off and CL-12.5/12.6 security/platform gates; extract Docker/workspaces from Controller into reviewed typed API | Reject arbitrary images/mounts/flags, tested limits, stage ownership, crash/reconciliation and Linux/Desktop feasibility; B1 fallback needs Owner decision | Stop dispatch and reconcile workers before restoring reviewed prior setup; no automatic privilege broadening |

The two core slices (ports/composition and validation separation) may need finer
PRs after characterization; do not treat this document as a fixed implementation
batch. CL-11 schema migrations and CL-10.2/10.3 operational cutover are separate
authorized tracks with their own backup/restore gates, not incidental steps of
package extraction. CL-04 internal runtime refactor is distinct from a new
Executor privilege boundary. CL-05 review-policy refactor and CL-06 multi-repo
configuration remain deferred. Final ordering must reconcile those dependencies
before any task is released; this proposal creates none of them.

## Verification and handoff limits

Observed test anchors: `tests/test_architecture_boundary.py:18–56` local-only
workflow/service transport exclusion; L106–114 repository injection; L117–174
Controller phase/result/failure behavior; L177 onward worker bot/local Git and
digest tests. These checks characterize current behavior, not future package
boundaries. Proposed static tests must check forbidden imports transitively,
local/function imports and cold import side effects; test worker installation
without workflow packages and isolated order_service imports. Update tests only
in later authorized implementation slices, not this docs-only task.

CL-12.3 runs required `git diff --check` and `python -m pytest -q` in the existing
environment and reports actual results via PR/chat. No new tests or dependencies
are added. Regression success cannot certify the proposed schema/privilege/
permission/portability gates. Risks include API/error drift during DTO separation,
clock/cancellation divergence, image dependency omissions, source-resource lookup
breakage and inadvertent transfer of privileged capabilities into shared code.
All remain gates for later approved tasks, not resolved by document authorship.

Handoff records full base/head SHA, one changed path, branch/worktree, commands
and real results, remote push SHA, PR URL, risks/limits, requested effort
`unspecified`, model/effort evidence `unverified`, and status awaiting ChatGPT
Tech Lead. No self-approval, merge, task creation or CL-12.4 execution. Final
parent ADR sign-off and all implementation/production decisions remain separate.
