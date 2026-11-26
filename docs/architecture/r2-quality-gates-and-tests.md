# R2 — Quality gates, unit/architecture tests and observability contract

**Status:** PROPOSED / NOT YET ENFORCED. Companion to [R2 architecture blueprint](r2-clean-architecture-blueprint.md).
**Compared sources:** `clean_arch_demo` at `b274fe483c88f1524cce0ac275ade266f03ec145`, Agent Workflow `main@33cebc9d7b53094477c48e24852cc06a936b9bbe`. This is a **target test contract** and a prioritized audit, not evidence that missing checks already run. CI/security file edits are protected and require their own approved task/review.

## Demo files to consult for R2 quality gates

Pinned demo: [`clean_arch_demo@b274fe4`](https://github.com/builacviet/clean_arch_demo/tree/b274fe483c88f1524cce0ac275ade266f03ec145).

- [`pyproject.toml`](https://github.com/builacviet/clean_arch_demo/blob/b274fe483c88f1524cce0ac275ade266f03ec145/pyproject.toml): Import Linter (`layers`, `independence`, `forbidden`), Ruff C901, Mypy Strict and coverage configuration.
- [`tests/architecture/test_architecture.py`](https://github.com/builacviet/clean_arch_demo/blob/b274fe483c88f1524cce0ac275ade266f03ec145/tests/architecture/test_architecture.py): additional AST architecture rules and mandatory UseCase unit-test checks.
- [`.github/workflows/ci.yml`](https://github.com/builacviet/clean_arch_demo/blob/b274fe483c88f1524cce0ac275ade266f03ec145/.github/workflows/ci.yml): five gates in GitHub Actions.

Import Linter and AST tests are **two separate checks**. Adapt the root to `agent_workflow` (not demo's `src`), keep the existing pytest runner/architecture tests, and reuse existing Agent Workflow security mechanisms rather than rebuilding them.

## Two-stage execution contract (when each R2 gate is enabled)

1. **Manual Codex local pre-push:** after coding, run all five enabled gates in the isolated task worktree. Fix failures before claiming `READY_FOR_TL_REVIEW`. Schema check runs against an explicitly authorized disposable PostgreSQL database; never production. During staged rollout, run the new-file gates and report any known legacy baseline violations truthfully instead of bypassing them.
2. **GitHub Actions post-push / Draft PR:** automatically rerun the same five mandatory gates on the pushed HEAD / PR merge ref, retaining the existing PostgreSQL, architecture, exact-SHA and sandbox checks. Independent TL review uses the actual run evidence, not Codex's local claims. **Only Owner merges.**

If a local gate cannot run because the environment lacks Docker/PostgreSQL or is denied permission, name the exact blocked gate and proceed with safe in-scope checks; do **not** report it PASS or treat a PR submission as proof it passed locally. Enabling a gate in Actions requires a separately approved CI/policy task; this Markdown PR does not activate the checks.

## 1. Baseline: what exists and what is missing

| Gate | Current Agent Workflow CI (`.github/workflows/v1-ci.yml`) | Target borrowing from demo |
| --- | --- | --- |
| **Ruff** | `scripts/ci_ruff_changed.py` formats/lints **changed Python files**; `E4,E7,E9,F,I`; no C901 | Full-tree Ruff format + lint, add `C901` complexity ≤10; no changed-files-only loophole at end of R2 |
| **Import boundaries** | `tests/test_architecture_policy.py`: AST, exact `architecture/policy.toml` exceptions, nested trusted worker aliases, runner cycle | Keep existing exact gate **and** add Import Linter enforcing dependencies/feature isolation with negative fixtures |
| **Static types** | Type hints by convention; `mypy --strict` not in CI | Strict Mypy for new core from day one; all application/domain and eventual source after cleanup, no global `ignore_errors` |
| **AST + unit + coverage** | `pytest` full suite and characterization/security tests; no formal 90% branch coverage or use-case-to-test mapping gate | Core AST guard; named unit tests for **every** new UseCase plus meaningful success/negative paths; ≥90% line **and** branch coverage for Domain and Application |
| **Alembic drift** | Ephemeral PostgreSQL `upgrade head`, migration/adapter tests and second upgrade (idempotency) | Keep all existing checks **and** `alembic check` against disposable migrated PostgreSQL to detect ORM/migration drift |

The demo additionally contains JSON logging with `ContextVar` request ID and `/health/live` + `/health/ready`. Agent Workflow has MCP `ping` and `get_server_info` but these are **not** dependency readiness probes and do not establish cross-process tracing.

The existing exact-HEAD push/PR evidence, PostgreSQL migration/locks/leadership, sandbox and credential-isolation tests are **not replaceable** by these five checks.

**PostgreSQL-only R2:** SQLite runtime persistence was retired in #113–119. Do not resurrect it or its retired tests for coverage. All retained Domain/Application code counts; remove demonstrably dead code only through approved retirement, never exclude active code to meet a gate. Current full-tree enforcement remains R2-QG-01; see [as-built source](../architecture.md).

## 2. Five new CI gates — final, mandatory target

| Gate | Intended command / implementation | Pass criteria |
| --- | --- | --- |
| **Q1 Ruff** | `python -m ruff format --check src tests alembic` + `python -m ruff check src tests alembic`, `select = ["E","F","I","C901"]`, `max-complexity = 10` | Final target: zero findings across the **retained** tree. No catch-all excludes. |
| **Q2 Import Linter + AST** | `lint-imports` with root package `agent_workflow`; run `python -m pytest -q tests/test_architecture_policy.py tests/architecture` | No forbidden import (including nested parent alias, relative/`*` and direct imports), no stale exceptions, no new dependency cycles; positive composition imports remain valid |
| **Q3 Mypy Strict** | `python -m mypy --strict src/agent_workflow` (final target) | Zero errors, including untyped use-case ctor/`execute`, leaking `Any`, unsafe generics; strict exceptions narrowly documented and approved |
| **Q4 Core unit + coverage** | `python -m pytest -q`, then coverage run with `branch = true` and targets `agent_workflow.domain` + `agent_workflow.application` | All newly introduced use cases have meaningful tests; core line and branch coverage **each ≥90%**, including error/rollback logic; observed skip count documented |
| **Q5 Alembic schema sync** | `python -m alembic -c alembic.ini upgrade head`, `python -m alembic -c alembic.ini check` on **ephemeral PostgreSQL** | No pending autogenerate operations; additionally verify idempotent upgrade, migrations, constraints and live transaction tests |

**Execution:** retain separate `push` and `pull_request` checks. Record tested SHA: push on actual commit, PR on merge ref **plus associated PR HEAD**. Do not label a synthetic merge SHA as the exact source HEAD. Use least-privilege checkout, disposable PostgreSQL, no production secrets or deployment. Separate `quality` job can depend only on installation/tests it needs; never remove the existing `Python tests`, `PostgreSQL Alembic migration` or `PostgreSQL dev Compose smoke` jobs.

**Example final command sequence** (illustrative; dependencies/config must be added and tested in a later implementation PR):

```bash
python -m ruff format --check src tests alembic
python -m ruff check src tests alembic
lint-imports
python -m mypy --strict src/agent_workflow
python -m pytest -q tests/test_architecture_policy.py tests/architecture
python -m pytest -q
# With coverage.py configured for branch=true and both core packages:
coverage run -m pytest -q tests/unit tests/architecture
coverage report --fail-under=90
# In the existing disposable PostgreSQL CI job:
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini check
```

The core coverage command above is **not sufficient to prove both packages separately hit 90%**; the actual gate must report and fail on **per-package line and branch** metrics (e.g., coverage JSON analyzed by a tested checker) and must run any tests needed for both core packages. Confirm coverage data includes the entire **retained, migrated** Domain/Application core, not just newly touched modules. A low-content/empty package must never satisfy a threshold vacuously. **Do not exclude active code to inflate the metric**; remove only confirmed dead code under approved cleanup instead.

### R2 import-linter contract

Apply to the **`agent_workflow` namespace**, not demo's installable `src` namespace. Conceptual rules:

1. `domain` may import Python standard library and approved pure value contracts only. Disallow `pydantic`, `sqlalchemy`, `fastapi`, `mcp`, `httpx`, `requests`, `subprocess`, `docker`, `psycopg`, `sqlite3`, `importlib` and lower-layer package imports.
2. `application` may import `domain` and its own ports/DTOs, not concrete `infrastructure`, `interfaces`, `composition`, `workers`, or IO frameworks. No SQL/session constructors in use cases.
3. `infrastructure` may implement `application` ports and depend on `domain`, not on inbound `interfaces` or `composition`.
4. `interfaces` (Presentation) may depend on `application`, pure contracts and explicit `composition` dependency factories, **not concrete infrastructure adapters**, DB sessions or legacy Bootstrap. Presentation owns entrypoints and invokes injected Use Cases directly. This aligns with the merged policy change in [#148](https://github.com/builacviet/agent-workflow/pull/148); Composition only constructs/injects and manages dependency scopes.
5. `composition` alone constructs concrete dependency graphs. `workers.trusted_git` has **no imports of workflow database drivers, workflow services, general coder plugins, or credential mounts into untrusted execution**. Allow only audited worker-local Git/GitHub adapters and minimal pure contracts.
6. Independent features cannot import each other's internals to bypass a port. `task` and `review` may share a deliberately approved pure domain contract, not ad-hoc cross-feature concrete repositories. Dependency cycles must be zero when R2 completes.
7. Handle static blind spots: alias/import-from parent packages, relative imports, `from ... import *`, dynamic imports, `TYPE_CHECKING` and optional dependencies. Reject or inspect explicitly; **do not weaken rules to achieve green**.

`Import Linter` and AST tests are **complementary**. Import Linter checks transitive dependencies; AST can enforce source-specific syntax, factories and ownership that a graph linter misses. Neither can prove semantic purity or runtime secret isolation by itself. Configuration code and import-policy changes need negative tests that *intentionally violate* each guard and prove the guard fails.

## 3. Architecture test review: specific upgrades

**Existing StrEnum gate:** `src/agent_workflow/domain/states.py` already provides canonical `WorkflowStatus(StrEnum)`, `DispatchStatus(StrEnum)` and `CiVerdict(StrEnum)` (R2-01A-P1 / PR #43). Future refactor must reuse these classes, preserve exact `.value` serialization and introduce no second enum authority. Converting `Literal`/Pydantic/MCP boundaries to enum-typed core is a separately tested compatibility change. Enumerate only genuinely closed business sets; never arbitrary plugin/provider IDs.

### Existing tests to preserve

- `tests/test_architecture_policy.py::test_no_new_cross_layer_imports_and_no_stale_exceptions` compares full import inventory to exact, reasoned exceptions; retain and **reduce** entries as migration proceeds.
- `test_import_policy_rejects_nested_worker_parent_aliases` covers the F88-01 bypass (`from agent_workflow.workers import trusted_git` and `from agent_workflow import workers`); preserve these regressions.
- `test_trusted_worker_never_imports_workflow_db_drivers` and `test_runner_dependency_cycles_have_exact_legacy_inventory` protect major current boundaries; delete cycle exception only when verified absent.
- `tests/test_architecture_boundary.py` exercises worker/Controller separation, sandbox, Git snapshot, trusted publish and disposable remote behavior; do not replace these with import-only assertions.
- `tests/test_r2_00_characterization.py` freezes review/CI/dispatch/public DTO/runtime behavior; reuse it during refactor, then progressively replace hard-coded legacy import points with tests for named use cases.

### Tests to add (each needs passing *positive* and failing *negative* fixture)

| Target test | Failure it must catch |
| --- | --- |
| `test_core_pure_imports` | `domain` or `application` imports SQLAlchemy/Pydantic/HTTP/MCP, including aliases and late imports; no undeclared grandfathering |
| `test_layer_dependency_direction` | Application imports concrete adapters or Composition; interfaces import DB or legacy Bootstrap; infrastructure imports interfaces; explicit Interfaces → Composition factory imports are allowed; composition-only concrete assembly restriction |
| `test_feature_independence` | Direct unauthorized review→dispatch/task implementation imports instead of approved ports |
| `test_ports_are_protocols_and_typed` | Non-`Protocol` ports, missing type annotations, `Any` dependencies in new UseCase constructors; keep sane style naming `IReviewRepository`/`IUnitOfWork` |
| `test_each_use_case_has_behavior_tests` | New `*UseCase` with no unit test **or** only an empty/smoke assertion; check scenario metadata/behavior manifest, not only filename existence |
| `test_domain_entities_are_pure` | Entity inherits ORM/Pydantic base class or performs filesystem/network/DB I/O |
| `test_canonical_strenum_states` | New closed Domain state is typed as raw `str`, known status comparisons use magic strings, duplicate/shadow `WorkflowStatus`/`DispatchStatus`/`CiVerdict` declarations appear, invalid wire enum value is silently accepted or an existing persisted value changes |
| `test_no_unapproved_dynamic_imports` | `__import__`, `importlib`, `eval`, `exec`, `open`/`print` in core; avoid false positives for known safe standard-library use |
| `test_composition_is_assembly_only` | Use-case decisions/`commit()`/SQL/privilege grants in `*_deps.py` or `database.py`; no hidden DB connection on module import |
| `test_trusted_worker_capability_boundary` | Worker imports workflow DB state or passes installation tokens into Code/Test; the negative must fail independently of AST import detection |
| `test_policy_change_cannot_hide_violation` | Mutating a forbidden edge/exemption or renaming an unclassified package cannot silently bypass gate; renamed/unclassified source must fail closed |
| `test_migration_schema_sync` | Changing ORM `models.py` without Alembic migration causes `alembic check` failure |
| `test_log_and_health_security` | Malformed/long request IDs, secret-bearing exceptions, unauthenticated health exposure, ready returning 200 when a mandatory dependency is down |

AST source tests should use fixture strings, synthetic package trees and deterministic temp directories. Unit tests use fakes/Protocol substitutes for repositories and clock, no real GitHub/Docker/network. Separate live PostgreSQL integration tests must use a disposable DB.

**Coverage is not quality on its own:** in each UseCase test at least success, validation/error, rollback/idempotent repetition, stale/unauthorized input and concurrent/conflict scenarios *as applicable*. Pure domain-policy tests verify transition tables with parametrized cases. Untrusted input and protected security boundaries need dedicated negative tests.

## 4. Observability and health checks (service-scoped)

### Structured logging

- Use JSON log events with safe ```timestamp```, ```level```, ```service```, ```logger```, ```event```, ```request_id```, ```task_id```, ```dispatch_id```, ```pr_number```, ```head_sha```, ```duration_ms```, ```message``` as appropriate. Correlation keys have explicit types/length limits; no dumping arbitrary payload dictionaries.
- HTTP `X-Request-ID`: validate fixed upper length and allowed printable characters; generate a safe opaque ID when absent/invalid. Scope/reset `ContextVar` in a `finally`, including exceptions. Propagate across MCP/HTTP → UseCase → DB log events; **ContextVar does not propagate to other OS processes or GitHub Actions**. Pass an allowlisted correlation ID explicitly through dispatch envelopes, webhook/CI records and worker evidence.
- No auth headers, private keys, DSNs, raw webhook body, prompt text, user code, signature, access token or SQL bound parameters in logs. Use deterministic redact/scrub tests and bounded exception messages. Log event names and stable error categories rather than exception string contents when unsafe.
- Design per-process logging for MCP, webhook listener, Controller, CI watcher and trusted Worker; no implicit global root-handler reset from a library import.

### Health semantics

- `/health/live` = process alive/HTTP responding (no DB requirement). Do not claim sandbox or worker phases are working solely because live is 200.
- `/health/ready` = **only mandatory dependencies for this component** verified within strict timeout, returning 200 or 503. MCP requires its configured DB/auth-dependent readiness checks; webhook requires its configured inbox/receiver readiness; Controller/CI watcher can expose internal/CLI status without public HTTP if more appropriate. RabbitMQ is *not* an Agent Workflow dependency merely because demo uses it.
- Decide each endpoint's access policy and binding explicitly. Prefer internal-only unauthenticated health probes on protected listeners; do not insert a public unauthenticated route into OAuth-protected MCP or into a signed webhook path without a separate threat review. No health response should reveal DSNs, secrets, installation IDs, internal paths, query text or stack traces.
- Test degraded DB, stale schema, auth configuration failure, timeout, webhook inbox failure, worker side-effect uncertainty, shutdown and cleanup; distinguish **liveness** from **readiness** and from **deep E2E attestation**.

## 5. Mandatory behavior/transaction/security regression matrix

| Area | Non-negotiable observations |
| --- | --- |
| Review | Exact-HEAD CI before claim and submission; lease expiration; two rounds max; duplicate payload idempotency; prior findings; effort verification; no stale SHA |
| Dispatch | One-slot/claim fencing; no automatic relaunch on ambiguous Prepare/Publish; leadership loss fail-closed; persisted reconciliation after restart |
| PostgreSQL | Three DB patterns: read-only snapshot/close, independent write commit/rollback, shared-Session UoW atomic rollback at both deadline checkpoints; concurrency claims, row/advisory ownership, isolation and idempotent migration |
| GitHub/Worker | GitHub App identity; ordinary branch push/Draft PR only; no Owner merge; sanitized credential-free Code/Test; image pin/digest, snapshot and symlink/denylist checks |
| MCP/Webhook | Auth0 scopes/Bearer compatibility, fail-closed unauthorized access, signature replay/delivery uniqueness, transport payload bounds and stable tool/DTO signatures |
| CI evidence | Verify actor/check provenance, SHA, run ID and sticky prior failures; no self-attestation; actual push and PR check status for the reviewed HEAD |
| Operability | DB/engine cleanup, no secrets in log/exception, correlation across process boundary, live vs ready failure semantics |

No new PR may silently remove or skip **retained-behavior** test categories to report a better count. A separately authorized SQLite-retirement PR may delete SQLite-only tests together with their removed obsolete implementation **after verifying no active callers**, documenting which tests were deleted and which PostgreSQL/security behavior remains covered; never hide a still-active regression or a failing retained gate. A test skip counts as `NOT VERIFIED` unless an environment-gated reason and an alternative check are recorded. Code touching `.github/workflows/**`, `architecture/policy.toml`, architecture security tests, `pyproject.toml` quality/security settings, credentials, migrations or trust-boundary code is **protected/HIGH** and needs prior focused TL + applicable Owner approval in the approved task.

## 6. Gating rollout and evidence to capture

1. **R2-01 baseline:** Run current full CI and standalone `ruff`/`lint-imports`/`mypy`/`coverage`/`alembic check` on a disposable environment; record actual failures and imports in a CI/PR report, **all retained source; retired SQLite is historical**. Do **not** declare missing tools PASS. Check whether `alembic check` autogenerate is meaningful on the existing SQLAlchemy metadata before enabling it.
2. **During R2-02 through R2-05:** New or materially rewritten **retained** `domain`/`application` files must pass strict types, C901, import rules, unit cases and coverage immediately. Any other remaining legacy exception must be explicit and shrinking. Never add broad `exclude`/`ignore_errors`/`pragma: no cover` or quietly remove active core code from coverage's denominator.
3. **R2-06 full enforcement:** eliminate remaining **retained-source** exemptions; make all five gates mandatory and green on the retained source/tests. Preserve the existing three CI **job categories** and exact-SHA/PG/security verification while replacing obsolete SQLite-specific assertions with relevant PostgreSQL checks. Add negative tests for the gate itself. `alembic check` must run with fresh, migrated PostgreSQL and the correct model metadata.
4. **Review:** PR descriptions give `base SHA`, `head SHA`, exact commands/results, per-gate tests/skips, changed paths, protected approvals, coverage line+branch breakdown, Mypy/import debt and runtime risks. Review against real code/CI on the exact PR HEAD, never text claims alone.
5. **Independent integrity:** When a PR changes `v1-ci.yml` or its policy/gates, require out-of-PR (trusted baseline or separately reviewed) negative canaries that prove malicious imports, untyped code, no tests, model drift and credentials are rejected. **Green CI from modified gates is not independent gate-integrity evidence.**

**Not in this docs-only R2-ARCH-00 PR:** enabling tools, changing workflow YAML, editing architecture import rules, adding or relocating Python packages, changing schema/DSN/runtime/deployment, or modifying the product's agent execution sandbox.
