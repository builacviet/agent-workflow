# CL-12.2 — Current-state module and dependency inventory

## Scope, authority and evidence convention

Observed repository snapshot: `builacviet/techlead-review-poc` at
`e3086a058c488db79f0f514aa19e926ec3e1d265` (base and fetched `origin/main`
at task start, 2026-11-06). Dedicated branch/worktree: `codex/cl-12.2`,
`D:/WORK/Project/techlead-review-poc-CL-12.2`.

[Authorized task](https://app.notion.com/p/410f92014abd8110b7edfa3b4f1860c8)
and [parent ADR](https://app.notion.com/p/410f92014abd81cdbceded689b7d4945).
Read-only governing context: `AGENTS.md`, `docs/architecture.md`,
`docs/review-policy.md`, `docs/handoff.md`, `docs/tasks/CL-12.md`.
The task's later authorization update supersedes its earlier Planned-only
callout and the historical authorization table in CL-12. No conflict requiring
scope expansion was found.

Only this document is created. No refactor, target redesign, runtime experiment,
Controller/worker dispatch, Docker launch, migration, DB/credential inspection or
live deployment is performed. Source analysis uses file reads and Python AST
parsing, not application imports. All file/line references below are one-based
at the recorded base SHA; paths are repository-relative and ranges inclusive.

- **Observed:** directly supported by source/build/config text at that SHA. A configured path is not evidence of a running deployment.
- **Potential Issue:** interpretation or risk candidate; not a proven exploit, failure or measured duplication cost.
- **Recommendation for later task:** input for CL-12.3 and subsequent separately authorized tasks; no implementation permission.

## Observed — complete Python module inventory

There are **24 Python modules**: 15 in `my_wfc`, 6 in `coder_worker`, 3 in
`order_service`, including package markers. Non-Python resources and image
entrypoints are listed separately below. The inventory is recursively enumerated
from the three source packages, including function-local imports.

| File and evidence span | Responsibility |
| --- | --- |
| `src/coder_worker/__init__.py:1–1` | Worker package marker; no eager imports. |
| `src/coder_worker/app_auth.py:1–232` | Trusted GitHub App JWT signing/token lifecycle, installation/repository/bot identity, bounded HTTP child process and AppVerifier specialization. |
| `src/coder_worker/github.py:1–45` | GitHubVerifier branch-head/base-ancestry checks; legacy gh API transport; re-exports local SHA/error types from my_wfc. |
| `src/coder_worker/http_transport.py:1–62` | JSON-over-stdin HTTP child CLI; fixed GitHub API origin, redirect denial, response limits and JSON output. |
| `src/coder_worker/main.py:1–232` | Trusted prepare/publish CLI: authenticated fetch, snapshots, allowlisted publication, ordinary push, bot draft PR and result envelope. |
| `src/coder_worker/repository.py:1–178` | Trusted Git subprocess wrapper, path/link policy, tracked-blob snapshot and changed-path validation with file/count/size bounds. |
| `src/my_wfc/__init__.py:1–1` | Package marker; no eager imports. |
| `src/my_wfc/admin.py:1–100` | Operator CLI: load packaged historical task spec, import history, seed/reset synthetic fixture; composes default workflow service. |
| `src/my_wfc/auth0_oauth.py:1–239` | Auth0 configuration and async RS256 JWT verifier; HTTPS JWKS retrieval/cache, issuer/resource/scope and client checks with sanitized diagnostics. |
| `src/my_wfc/budget.py:1–55` | Context-local dispatch budget combining wall and monotonic deadlines; operation timeouts, deadline checks and cleanup exemption. |
| `src/my_wfc/coder_runtime.py:1–401` | Controller Docker adapter: config/path policy, snapshot digest, bounded subprocesses, phase mounts/resources, cleanup and local handoff validation. |
| `src/my_wfc/contracts.py:1–51` | Pydantic strict workflow input models: Finding, Submission and TaskSpec. |
| `src/my_wfc/controller.py:1–143` | Operator CLI/process lock, one-attempt orchestration, dispatch budget, 60-second polling and explicit reconciliation acknowledgement. |
| `src/my_wfc/dispatch_repository.py:1–100` | SQLite dispatch adapter: constructor DDL, durable unique active slot/idempotency identity, claim/finish/block/acknowledge. |
| `src/my_wfc/github.py:1–11` | Local WorkflowError and lowercase full-SHA syntax validator; despite filename, no remote calls. |
| `src/my_wfc/paths.py:1–17` | Stable absolute DB path selection from environment, source checkout or installed-user fallback. |
| `src/my_wfc/repository.py:1–235` | WorkflowUnit/WorkflowRepository Protocols alongside concrete SQLiteUnit and SQLiteWorkflowRepository; SQL, schema initialization, connections and transactions. |
| `src/my_wfc/security.py:1–62` | Transport-bound bearer/OAuth authorization, actor identity and explicitly constrained disposable no-auth POC mode. |
| `src/my_wfc/server.py:1–103` | FastMCP composition/startup, bind/Host policy, optional Auth0 verifier, ping/info and workflow tool registration. |
| `src/my_wfc/service.py:1–482` | Workflow use cases: provisioning, handoff, queue/claims, review validation/history/findings, status/round rules and coder assignment; injectable repository with SQLite default. |
| `src/my_wfc/tools.py:1–62` | Seven authenticated MCP tool adapters and default service factory; no coder dispatch tool. |
| `src/order_service/__init__.py:1–6` | Public exports for Order, OrderItem and OrderService. |
| `src/order_service/models.py:1–51` | Frozen order/item dataclasses; Decimal, IDs, quantities, item tuple and computed totals validation. |
| `src/order_service/service.py:1–20` | In-memory per-instance order store with sequential IDs and lookup; depends only on order models and stdlib. |

## Observed — local import matrix

This is the complete direct intra-repository Python import edge list obtained
from AST Import/ImportFrom nodes; standard-library and third-party edges are
summarized after it. Relative package exports are included. No import cycles
were found among these direct module edges; `server` imports `tools` late,
while `tools` does not import `server`.

| Importer | Local targets with import line |
| --- | --- |
| `src/coder_worker/__init__.py` | None |
| `src/coder_worker/app_auth.py` | `my_wfc.budget` (L14); `coder_worker.github` (L15) |
| `src/coder_worker/github.py` | `my_wfc.github` (L8) |
| `src/coder_worker/http_transport.py` | None |
| `src/coder_worker/main.py` | `coder_worker.app_auth` (L11); `coder_worker.repository` (L12); `coder_worker.github` (L13); `my_wfc.budget` (L14) |
| `src/coder_worker/repository.py` | `my_wfc.budget` (L5); `coder_worker.github` (L6) |
| `src/my_wfc/__init__.py` | None |
| `src/my_wfc/admin.py` | `my_wfc.github` (L7); `my_wfc.service` (L8); `my_wfc.tools` (L9) |
| `src/my_wfc/auth0_oauth.py` | None |
| `src/my_wfc/budget.py` | None |
| `src/my_wfc/coder_runtime.py` | `my_wfc.budget` (L16); `my_wfc.github` (L17); `my_wfc.service` (L18); `my_wfc.github` (L379) |
| `src/my_wfc/contracts.py` | None |
| `src/my_wfc/controller.py` | `my_wfc.budget` (L15); `my_wfc.github` (L16); `my_wfc.service` (L17); `my_wfc.dispatch_repository` (L20); `my_wfc.coder_runtime` (L95); `my_wfc.coder_runtime` (L105) |
| `src/my_wfc/dispatch_repository.py` | `my_wfc.github` (L6); `my_wfc.service` (L7) |
| `src/my_wfc/github.py` | None |
| `src/my_wfc/paths.py` | None |
| `src/my_wfc/repository.py` | `my_wfc.budget` (L7); `my_wfc.github` (L8) |
| `src/my_wfc/security.py` | None |
| `src/my_wfc/server.py` | `my_wfc.security` (L5); `my_wfc.auth0_oauth` (L48); `my_wfc.tools` (L88) |
| `src/my_wfc/service.py` | `my_wfc.repository` (L7); `my_wfc.contracts` (L8); `my_wfc.github` (L9) |
| `src/my_wfc/tools.py` | `my_wfc.paths` (L2); `my_wfc.contracts` (L5); `my_wfc.security` (L6); `my_wfc.service` (L7) |
| `src/order_service/__init__.py` | `order_service.models` (L3); `order_service.service` (L4) |
| `src/order_service/models.py` | None |
| `src/order_service/service.py` | `order_service.models` (L5) |

Observed external boundaries:

- `my_wfc/contracts.py:3` uses Pydantic; `tools.py:4` and `server.py:7–8` use MCP. `server.py:46–48` loads Pydantic/AuthSettings/Auth0 only for OAuth mode. `security.py:43` reads SDK auth context locally in the OAuth branch.
- `my_wfc/auth0_oauth.py:19–21` imports httpx, PyJWT and MCP AccessToken. Async verifier/JWKS logic is at L120–239. This is present source, even though historical Phase 3.1 documentation says no OAuth in that earlier phase; the inventory does not remove current OAuth support.
- `my_wfc/repository.py:5` imports sqlite3. `controller.py:7` also imports sqlite3 for caught database errors (L127); SQL dispatch operations live in `dispatch_repository.py:17–100`.
- `coder_worker` uses standard-library Git/HTTP/OpenSSL subprocess adapters, plus the cross-package budget/error/SHA imports listed above. It does not import WorkflowService, repository/SQLite adapters, MCP or Auth0 modules. Code presence in an image and imports are separate observations.
- `order_service` imports only itself and stdlib, with no imports to/from either workflow/worker package in this graph. `pyproject.toml:10–15` declares no mandatory runtime dependencies, optional test pytest and MCP/PyJWT extra. Pydantic/httpx usage is provided through the MCP dependency tree rather than explicit direct declarations here; no dependency changes are made.

## Observed — process, entrypoint and persistence paths

```text
HTTP MCP client
  -> my_wfc.server (FastMCP, optional OAuth token verifier)
  -> my_wfc.tools -> authorize_context -> WorkflowService
  -> WorkflowRepository/WorkflowUnit -> SQLiteWorkflowRepository/SQLiteUnit
  -> workflow SQLite file

Operator Controller CLI (host Python process; not a Compose service here)
  -> WorkflowService + DispatchLedger -> same configured SQLite file
  -> run_once -> CoderRuntime -> host Docker CLI/engine
       -> trusted prepare -> untrusted code -> untrusted test -> trusted publish
            GitHub/Git       Codex model      pytest/no-net      GitHub/Git
       <- result.json -> WorkflowService.register_task_handoff
  -> DispatchLedger.finish; failures -> block; operator reconciliation

Operator admin CLI -> tools.default_service -> WorkflowService -> SQLite
order_service -> its own in-memory dictionary (no workflow or DB edge)
```

This is a source execution graph, not a claim that any process currently runs.
The two SQLite consumers share a database only when operator configuration
points them at the same physical file; code does not prove deployment parity.

### MCP path

`python -m my_wfc.server` is the image CMD (`docker/mcp.Dockerfile:22`). Server
construction/registration occurs at module load (`src/my_wfc/server.py:88–93`),
with streamable HTTP under `__main__` (L95–103). Bind/Host validation is L10–37;
mode selection is L39–69. Ping/info are registered separately (L91–92).
Seven workflow tools authorize before creating their service
(`src/my_wfc/tools.py:14–62`, notably L15–17). The default factory uses
`database_path()` (L10–11). OAuth identity requires the verified subject/scope;
bearer comparison uses transport headers, not model arguments
(`src/my_wfc/security.py:25–62`).

`WorkflowService` accepts a repository but defaults to concrete SQLite storage
(`src/my_wfc/service.py:19–41`). Use cases invoke repository units, not SQL;
SQL/connection/transaction ownership is in `src/my_wfc/repository.py:40–235`.
Repository construction creates parent directories and executes `schema.sql`
after accepting user_version 0/1 (L185–196). Reads use BEGIN (L231–235); writes
use BEGIN IMMEDIATE with deadline/commit/rollback handling (L210–229).
`src/my_wfc/schema.sql` is a packaged non-Python schema resource.
DB selection is environment override, checkout data, then installed-user data
(`src/my_wfc/paths.py:6–17`).

Handoff validates reported head SHA and stored authorization
(`src/my_wfc/service.py:115–180`); review submission validates claims/rounds and
records findings (L287–442). Result explicitly sets `approval_current=None`
and `remote_verification=not_performed_by_wfc` (L456–457). GitHub URL generation
(L266 onward) is not a remote GitHub API call. Tech Lead owns remote freshness
verification under `docs/review-policy.md`.

### Controller and DispatchLedger path

`python -m my_wfc.controller CONFIG` parses operator JSON, validates config and
holds a platform-specific process lock (`src/my_wfc/controller.py:53–109`).
`operate` composes service, ledger and runtime, supports explicit reconciliation,
and polls every 60 seconds (L104–139). `run_once` claims, enters dispatch budget,
rechecks assignment round/status/input SHA, calls injected execute, then finishes;
on BaseException it blocks the dispatch (L23–49).

Ledger constructor uses the concrete repository's `raw_connection()` to execute
`CREATE TABLE IF NOT EXISTS coder_dispatches`
(`src/my_wfc/dispatch_repository.py:13–27`). Claims use raw transactions,
unique slot 1 and `(task_id, round_number, input_sha)` identity, authorized task
IDs/repository/status and fewer than two completed rounds (L29–73). Deadline is
stored but expiry does not free the slot. Finish/block/acknowledge mutate dispatch
state (L75–100); acknowledgement is gated by operator reconciliation and runtime
stop (`src/my_wfc/controller.py:110–116`).

Runtime validates canonical absolute linked worktrees, task write paths, image
syntax, model, seccomp digest, POC auth volume and timeout
(`src/my_wfc/coder_runtime.py:77–123`). It invokes only Docker commands through
its command wrapper (L148–159), not host Git/GitHub operations. `execute`
creates task staging/envelope (L308–334), sequences phases (L335–356), checks
source digest stability across tests (L347–351), validates result task/repo/
branch/base/input/head/PR URL (L357–388), and calls local handoff (L389–399).
Cleanup uses exact container names under a separate cleanup budget (L204–233,
L400–401). These are observed safeguards, not a new isolation certification.

### SQLite writers and operator entrypoints

| Process/consumer | Writes and evidence |
| --- | --- |
| MCP process | Tool claims, reviews and handoffs through WorkflowService and SQLite units (`tools.py:32–56`, `service.py:115–180,208–233,287–442`). Even service construction can execute schema initialization (`repository.py:185–196`). |
| Controller process | DispatchLedger DDL and claim/finish/block/ack SQL; runtime handoff via WorkflowService (`dispatch_repository.py:17–100`, `coder_runtime.py:390–398`). |
| Admin operator CLI | Historical import, synthetic seed/reset via engine transactions (`src/my_wfc/admin.py:28–100`); CLI depends on `tools.default_service` at L9. Packaged `my_wfc/resources/TASK-001-reference.md` is historical context, not execution authorization. |
| Trusted worker / code / test | No workflow DB mount in generated phase args (`coder_runtime.py:235–305`) and no SQLite imports in worker graph. Worker writes Git/snapshot/result artifacts; code modifies source; test uses disposable source. Tests may create their own temporary DBs; that differs from workflow DB access. |
| order_service | Per-instance memory only (`src/order_service/service.py:8–20`). |

Paths in this table without a prefix refer to `src/my_wfc/`.

## Observed — images, stages and trust boundaries

| Artifact/phase | Source-visible capability and boundary |
| --- | --- |
| MCP image | `docker/mcp.Dockerfile:1–22`: Python 3.12 tag, copies my_wfc and order_service, installs MCP extra, UID appuser, /state, server CMD; does not copy coder_worker or install Git/gh. It still packages Controller/runtime modules as part of my_wfc. |
| Worker image | `docker/coder.Dockerfile:1–16`: digest-pinned Node base, Codex 0.161.0, Python/testenv/pytest/MCP, Git/OpenSSL, UID 10001. `COPY src /opt/worker-src` at L11 exposes all three source packages to image phases through PYTHONPATH L12. One image serves all phases; it is not a separate minimal code image. |
| Trusted prepare | `coder_runtime.py:274–279,335–337` mounts App PEM read-only and whole task stage. `coder_worker/main.py:57–85` verifies remote input, initializes/fetches Git, checks fetched SHA and writes a tracked-file snapshot. Repo/.git stays outside source subdirectory. |
| Untrusted code | `coder_runtime.py:280–290,338–346` mounts only source at /workspace and POC Codex auth volume RW at /home/coder/.codex; no App PEM, workflow DB or repo/.git mount. Network is not disabled for code. `docker/coder_entry.py:55–85` requires device auth, removes API-key env, uses ephemeral Codex exec with workspace-write. Untrusted means model/task code, not credential-free: code receives its model credential scope. |
| Untrusted test | `coder_runtime.py:291–292,347–351` gives source mount, no App PEM/Codex auth volume and network=none; source digest must be unchanged after successful test. `docker/coder_entry.py:51–52` runs python -m pytest -q. Tests execute task-controlled Python. |
| Trusted publish | `coder_runtime.py:355` supplies whole stage and PEM again. `coder_worker/main.py:88–202` rebuilds baseline, validates allowed changes, constructs handoff, stages/verifies exact bytes, bot-commits, ordinary-pushes, verifies remote SHA, creates/updates bot draft PR and writes result.json. App revoked in finally (L205–223). |

All phase containers receive read-only root, nonroot UID, dropped capabilities,
no-new-privileges, bounded CPU/memory/pids, tmpfs and no Docker logs
(`src/my_wfc/coder_runtime.py:239–260`). Code/probe additionally get the reviewed
seccomp profile (L261–273). Docker socket is absent from the constructed mounts.
The host Controller currently controls Docker directly; a separate trusted
executor service is a target ADR concept, not current implementation.
The image contains Git and extra application code even in code/test, but those
phases do not receive GitHub credentials or Git repository metadata through these
mounts. Do not confuse installed tooling with granted credentials.

`compose.controller.yaml:1–34` defines MCP, /state host bind to
`./data/controller`, loopback port mapping and no Controller service; L36–59
adds optional profile-gated ngrok. This file describes a possible configuration,
not actual live mounts or services. `docker/coder_entry.py:19–20` routes trusted
prepare/publish to worker main; L9–16 supplies an inner timeout. The HTTP child
also has a CLI (`src/coder_worker/http_transport.py:46–62`), called by
`app_auth.py:21–24`; it is not a public service entrypoint.

## Potential Issues and recommendations for later tasks

Each finding separates direct evidence from an interpretation. None is a runtime
exploit demonstration or permission to change implementation.

| ID | Observed evidence | Potential Issue | Recommendation for later task |
| --- | --- | --- | --- |
| INV-01 | `src/my_wfc/coder_runtime.py:20–74` and `src/coder_worker/repository.py:8–62` repeat DENIED/GENERATED/MAX_BYTES/WINDOWS_DEVICES and is_linked/safe_path/writable/independent_path logic. | Security-policy copies can drift; similar code alone does not prove identical lifecycle requirements or an existing bypass. | CL-12.3 ownership matrix should separate pure lexical/path/SHA policy from filesystem IO/link inspection; specify parity cases and allowed consumers before a later extraction. |
| INV-02 | `coder_worker/main.py:14`, `repository.py:5`, `app_auth.py:14` import my_wfc.budget; `coder_worker/github.py:8` imports my_wfc.github. | Worker package is not import-independent, despite absence of DB/service imports. A workflow package rename could break worker. | CL-12.3 map a minimal pure shared deadline/error/SHA contract and its consumers without importing privileged modules into core/worker. No extraction here. |
| INV-03 | `src/my_wfc/github.py:9–11` owns the one SHA syntax implementation; worker re-exports it. `coder_worker/github.py:29–45` checks remote head/ancestry; `main.py:76–80,148–156` rechecks fetch/push state. | Repeated SHA checks have different trust/time purposes and must not be deleted as duplicate validation. Filename my_wfc.github obscures its local-only role. | CL-12.3 distinguish pure syntax ownership, persisted equality checks and remote freshness responsibilities. Retain independent security gates. |
| INV-04 | `my_wfc/budget.py:16–55`, runtime `run_container:162–197`, `coder_entry.py:9–16`, `coder_worker/app_auth.py:26–59`, worker repository `command:65–87` bound different process/cleanup layers. `coder_worker/github.py:15–23` legacy gh path uses fixed timeout=15; AppVerifier overrides _get (`app_auth.py:226–232`). | Multiple timeout wrappers are not automatically duplicates: host/container/HTTP and cleanup have distinct lifetimes. Legacy transport's fixed timeout differs from budget-aware App transport. | CL-12.3 document deadline contract versus service-local cancellation/cleanup ownership; identify actual legacy consumers before proposing removal/unification. |
| INV-05 | `docker/coder.Dockerfile:11–12` copies all src; MCP image copies whole my_wfc (`docker/mcp.Dockerfile:12`). | Packaging is broader than process responsibilities; image presence does not prove active DB access or secret exposure. | CL-12.3 record per-image/package dependency ownership and later minimal COPY boundaries; keep runtime/privilege redesign separate from packaging. |
| INV-06 | `service.py:7,38` imports/concretely defaults SQLite adapter from the same module as Protocols (`repository.py:11–37,185`). Ledger depends on WorkflowService and uses raw_connection/raw_transaction (`dispatch_repository.py:7,17,32`). | Domain/application abstraction is partially separated, but default wiring and dispatch storage still couple to concrete SQLite capabilities not declared on WorkflowRepository. | CL-12.3 identify composition-root, ports and storage ownership preserving injection/transactions; do not claim core is already framework/storage independent. |
| INV-07 | `repository.py:185–196` initializes schema; ledger constructor adds coder_dispatches at L17–27. | Constructors perform DDL; constructing a default service may create a DB at configured path. This is current behavior, not a new migration architecture. | Record ownership dependency for CL-12.4/CL-11 explicit migrations and wrong-path/version guards; no DB inspection or migration now. |
| INV-08 | `admin.py:9` imports tools.default_service, and `tools.py:4` imports MCP Context. | Operator administration inherits an interface/framework dependency for service composition. | CL-12.3 document CLI/factory ownership and possible independent composition root; no framework rewrite. |
| INV-09 | `contracts.py:6–51` contains strict Pydantic workflow models; phase envelopes/results are ad hoc dictionaries (`coder_runtime.py:326–334,357–388`, `coder_worker/main.py:195–202`). | Shared workflow review models and worker protocol are different contracts; moving all models into common could drag framework or privileged dependencies across boundaries. | CL-12.3 inventory field/validation ownership and genuine consumers; consider only minimal pure shared protocol where semantics match. |
| INV-10 | Absolute paths/linked-worktree checks (`coder_runtime.py:80–118`), host stage bind (L257–258), Compose host data bind (`compose.controller.yaml:17–23`). | Current host-oriented path mapping is not proof of Linux-containerized Controller/daemon-host path compatibility. | CL-12.3 record ownership questions for CL-12.6 portability; no claim that target Compose/executor exists. |

Paths without a prefix in INV-06–09 are under `src/my_wfc/`; worker and Docker
paths are explicitly identified. These findings nominate responsibility owners,
not a chosen new package layout or a refactor plan authorized for execution.

## CL-12.3 input and validation limits

CL-12.3 should resolve the ownership/import matrix for the findings above:
application/domain versus interfaces, concrete SQLite versus ports/composition,
pure path/SHA/deadline contracts versus IO/cancellation, worker envelope versus
workflow review models, and per-image dependency ownership. Preserve existing
review rounds, exact SHA checks, authorization, lease/idempotency, failure and
reconciliation semantics. Carry persistence, executor threat-model and portable
operations questions to CL-12.4–CL-12.6 rather than implementing them here.

Existing tests provide useful later regression anchors:
`tests/test_architecture_boundary.py:18,49,106,117,177,243,326,341` cover local-only
workflow lifecycle, SQL/adapter boundary, repository injection, Controller phase
results, worker bot publication, real local Git push, digest framing and Compose
syntax respectively. These test definitions do not prove a live deployment or
future architecture. No new tests are introduced for this documentation change.

Required handoff: actual `git diff --check` and `python -m pytest -q` result,
changed path, base/head SHA, remote push verification and PR URL are supplied in
the PR/separate Coder handoff, without editing `docs/handoff.md`. This inventory
is static evidence only; no deployed image/daemon/DB state, credential isolation
at OS level, rootless feasibility or concurrency performance was tested.
Stop for exact-SHA Tech Lead review (maximum two rounds); Owner decides merge.
No merge or CL-12.3 execution is authorized by this document.
