# Engineering Quality Standards — Agent Workflow V1

**Scope:** Engineering and code-quality conventions for this repository. **Manual Coder reads only the coding sections relevant to the approved task** (baseline and affected database/security conventions), not the later product-role/gate specifications. This document is not execution authorization; task AC and Owner security decisions remain authoritative.

## Authority and proportionality

Follow [AGENTS.md](../../AGENTS.md) and the approved task's Scope Envelope; public contracts, merged code and domain/security invariants are binding. Escalate conflicts and protected changes. Use KISS, YAGNI, DRY and SOLID as practical heuristics: add an abstraction only for a demonstrated boundary, shared rule or maintainability benefit. Review findings need a concrete consequence, not pattern preference.

## Current repository-specific coding baseline

- Python 3.11+, type hints, explicit errors and small cohesive functions. Ruff pinned via `quality`; lint/format changed Python only during current staged rollout. Verify actual CI scope. Full-tree gates are a separate target.
- Reuse `AppConfig`, canonical enums/serialized values, existing GitHub/auth/API adapters and error policies. No new scattered env reads or redundant HTTP clients. Preserve MCP names/schema/auth, packaging, idempotency, exact SHA, two-round review and transaction semantics.
- Fail closed on missing auth, stale SHA, unexpected state or uncertain effects. Bound timeouts/retries (retry only safe idempotent operations), queues and subprocess argv; no interpolated shell commands, untrusted `shell=True`, secrets in logs or untrusted filesystem/mount access. Trusted Prepare/Publish alone owns GitHub App credentials; Code/Test has no credentials, live DB or Docker socket. Ambiguous publish stays BLOCKED for reconciliation, never automatic replay.
- PostgreSQL-only R2; source/CI evidence, not old docs, establishes behavior. See [current architecture](../architecture.md) for legacy retirement status. No unrelated refactors or runtime expansion.

## R2 layer, DI and file header contract

**Read this section only for new/migrated R2 modules or changes to entrypoints, wiring or layer boundaries.** It defines the mandatory R2 convention. The remaining `orchestration/` and Webhook launcher code are tracked migrations.

| Layer | Owns | Does not own |
| --- | --- | --- |
| **Domain** | Pure entities, invariants, policies and errors | IO, ORM, transport, configuration |
| **Application** | Use Cases, DTOs, ports and business/transaction intent | Concrete PostgreSQL/HTTP/Docker adapters or framework handlers |
| **Infrastructure** | Implement ports: repositories, DB UoW, network/runtime adapters | Business eligibility or inbound request routing |
| **Composition / Dependencies** | Construct/inject Use Cases and infrastructure providers; manage scoped dependencies | Presentation adapters, inbound request parsing/handling, or invoking `UseCase.execute()` |
| **Interfaces / Presentation** | Own entrypoints/startup/shutdown, parse/validate transport request, obtain injected Use Case from explicit Composition factories, call `execute()`, map result/error | Creating concrete infrastructure/DB sessions or deciding business policy |
| **Worker / Trusted** | Isolated trusted Prepare/Publish actions | Owning Controller business policy or untrusted access to workflow DB |

**DI contract (five-part target):** In FastAPI, `get_register_user_use_case(session=Depends(get_db_session))` is a Composition provider: it **returns** a wired Use Case, and `register_user(..., use_case=Depends(get_register_user_use_case))` in Presentation calls `use_case.execute(...)`. FastAPI resolves `Depends` per request. Presentation owns application startup and registration. Composition **only constructs/injects dependencies and manages their scopes**; it must not construct or invoke Presentation handlers or invoke Use Cases. For CLI, Presentation selects modes, obtains scoped dependencies from explicit Composition factories and calls the injected Use Case directly. Interfaces may import explicit Composition factories, not concrete Infrastructure; Application and Domain must not depend on Composition or Presentation. Do not introduce redundant facade/DI layers just to mimic FastAPI.

**Controller/CLI conventions:** new executable launchers belong in the appropriate `interfaces/` transport package; feature-specific dependency assembly belongs in `composition/*_deps.py`. The Controller executable is `agent_workflow.interfaces.cli.controller` with mode-specific providers in Composition. The remaining Webhook launcher migration is tracked separately. Neutral CLI parsing/config-file helpers stay under `interfaces/cli/`, not in a sibling feature-specific handler. CI-only modes must not eagerly import/initialize Dispatch or Worker. Load validated `AppConfig` at startup/DI boundaries for **new** code and inject it; old Interface env reads are separately tracked debt, not a mandate to refactor unrelated files.

**File header:** every **new or materially migrated R2 Python source module** within the task's scope starts with a module docstring containing `Layer:`, `Responsibility:`, `Dependencies:` before imports (after optional shebang/encoding). Use `Domain`, `Application`, `Infrastructure`, `Composition`, `Interfaces/CLI`, `Interfaces/MCP`, `Interfaces/Webhook` or `Worker/Trusted`. Example:

```python
"""Layer: Composition
Responsibility: Build a scoped RegisterUserUseCase.
Dependencies: Application contracts and Infrastructure adapters.
"""
```

Existing Bootstrap headers remain valid descriptions of transitional modules until migration. Headers describe actual ownership and **do not grant scope, suppress checks or prove correctness**. Do not add headers to unrelated legacy modules, tests or generated files. On review, inspect who constructs/injects vs. who calls `execute()`, imports and mode-specific initialization; use focused negative tests. Consult the [R2 blueprint](../architecture/r2-clean-architecture-blueprint.md) **only** when a task changes the target architecture; do not preload the full blueprint for routine coding.

## R2 validation and helper ownership

Applies to **new or materially changed R2 features within an approved task**. Reference patterns: [Owner's validation commit](https://github.com/builacviet/clean_arch_demo/commit/9828b2c6cf69d22ecd3b4ac81be2683f981f3fa1) and [helper/cross-domain commit](https://github.com/builacviet/clean_arch_demo/commit/c0e8674fa0c2a61aadd6236ff893c8975f7d5728). Adapt to Agent Workflow's `interfaces/`, PostgreSQL and existing security contracts; **do not copy demo features or force a new layer**.

| Boundary | MUST for affected R2 code |
| --- | --- |
| **Domain** | Entities/value objects and feature-specific pure Python validators enforce business invariants on creation and operations; raise defined Domain errors. Keep `domain/` free of Pydantic, SQLAlchemy, HTTP and I/O. Reuse canonical `StrEnum` for closed workflow states. |
| **Application** | Validate command prerequisites when invoked **directly**, before calling a repository, opening a UoW or triggering a side effect. Call Domain policies for business eligibility; never assume an MCP/API adapter already validated input. Use typed DTOs/ports rather than framework validation in core. |
| **Interfaces** (demo's Presentation) | At MCP/webhook/CLI entrypoints, validate input type, size, shape and format with Pydantic `Field`/`field_validator` or the existing framework's equivalent; normalize only when contract-safe. Map transport/domain failures to established errors. Do **not** reimplement Domain business rules here. |
| **Infrastructure** | Validate persisted values when converting to typed core DTOs; reject unknown enum values rather than coercing to a default. Translate persistence errors safely and preserve transaction/rollback/identity guarantees. |

**Verification:** For relevant changed behavior, test valid and invalid/blank/whitespace inputs, boundary lengths or malformed payloads, and invalid persisted states as applicable. Assert rejected commands **do not call repositories, commit, or trigger external effects**. For multi-domain writes, a Use Case coordinates Domain guards through its authorized UoW with **one transaction**; do not duplicate cross-domain rules or open independent commits. Keep existing MCP contract/security tests when interfaces change. Use `PASS / FAIL / N/A / NOT VERIFIED` evidence; a docs-only task does not require new Python tests.

**Helpers belong to their responsibility, not a generic root folder:** Keep feature-specific pure validators in `domain/<feature>/validators.py` or a value object; put truly reusable **pure** functions under `domain/shared/` only when justified; transport parsing/pagination helpers under `interfaces/<transport>/`; third-party integration helpers in `infrastructure/` behind an Application `Protocol` only if a dependency boundary needs one. **Do not create root-level `utils/`, `helpers/` or `common/`, empty packages or speculative `shared/utils.py`.** For complex read-only projections, flat typed DTOs with a narrow query/read port can avoid loading full Domain Entities; use this only when it simplifies the actual query.

No mandatory global CQRS rewrite, duplicate Domain/Interface checks or unapproved protected/public contract changes.

## PostgreSQL ORM and DTO conventions

Use canonical SQLAlchemy 2.x typed models (`DeclarativeBase`, `Mapped`, `mapped_column`); Alembic alone owns schema. No implicit migrations or `create_all()`. Use ORM for CRUD, explicit Core projections/atomic upserts/bulk operations where justified, fixed bound-parameter PostgreSQL SQL for locks/settings. Never interpolate untrusted SQL/identifiers, select wildcard columns or insert positional whole rows. Preserve field aliases (e.g. `metadata_text`), JSON/nulls and serialized values. Map to typed immutable Domain/Application values before scope closes; reject corrupt persistence, sanitize DB errors. Pydantic is transport validation or an adapter-internal detail, not a core DTO dependency; no ORM or raw persistence dicts escape to Application.

## PostgreSQL Session, query and transaction ownership

**Canonical R2 policy (Owner-approved after #123): choose the transaction boundary by business invariant, not table count.** These are three patterns, not a requirement for three new abstractions. Reuse merged `PostgresFactory` and `SqlAlchemyUnitOfWork` (sync now; async only for an approved consumer).

| Pattern | Selection and owner | Contract |
| --- | --- | --- |
| Reader | Independent read/query projection; Infrastructure owns read scope | Feature/query-specific `I*Reader`, typed immutable DTO/snapshot (immutable nested values too). Short READ COMMITTED READ ONLY for simple queries; one REPEATABLE READ READ ONLY scope for a consistent multi-query snapshot. Bounded result/timeout, rollback/close. No Application commit or hidden writes. |
| Independent Repository write | One self-contained atomic persistence operation; Infrastructure owns short transaction | Feature-local `I*Repository`; INSERT/UPDATE/DELETE/UPSERT or locked operation whose complete invariant is encapsulated in adapter. Return only after successful commit; rollback on failure. No Application UoW wrapper for an independent operation. Infrastructure may reuse the concrete UoW locally (e.g. dispatch writes) without transferring business intent to Application. |
| Business UnitOfWork | Application coordinates dependent reads/validation/writes whose success must be atomic, even on one table | Inject `IUnitOfWork` and typed repositories directly; same fresh Session, one transaction, explicit commit intent only after successful mutation; no repository commit or separate transaction. Composition only constructs/injects. |

Repositories persist Domain entities/aggregates or feature mutations; Readers group by actual query/use case/access boundary (not table/person) and return typed projections without unnecessary aggregates. No generic reader/repository, forced CQRS or interface per table. Authorization is Application policy; adapters enforce authorized query scopes. Avoid N+1/explosive joins; use JOIN/CTE/batched queries where justified.

**Lifecycle:** `get_scoped_session()` lends an idle connection-bound Session. With the canonical UoW, `commit()` records a decision; for workflow scopes, context exit refreshes deadline/timeout, commits the ORM Session (joined with `rollback_only`), checks the deadline after Session commit, closes Session, then physically commits the outer connection transaction. CI write scopes use their fixed short profile without workflow budget hooks. Without commit intent or on error, rollback; read-only never requests commit. Do not add transaction listeners in repositories or reuse Session/UoW across concurrent invocations or long-running Worker operations.

**Preserve distinct DB profiles:** CI/webhook short scopes currently use READ COMMITTED, DB-enforced READ ONLY for reads, transaction-local 5s lock/statement timeouts and existing durability; Review context uses REPEATABLE READ READ ONLY; Workflow/dispatch uses dynamic deadline-bounded timeout checks. Keep existing advisory transaction and row-lock order/invariants; session-level leadership owns a separate pinned backend connection until explicit release/retirement. Neither DB lock nor DB transaction fences GitHub/Docker effects. Keep read/write lifetimes, rollback/close, sanitized errors and ambiguous-effect reconciliation. Read-only is not autocommit.

**Shared infrastructure policy:** `session_factory.py` owns connection/Session lifecycle; `unit_of_work.py` owns transaction coordination; `transaction_options.py` owns shared SQL settings; `lock_keys.py` owns stable transaction advisory key constants (distinct from session leadership). Keep named timeout constants near their semantic owner, with units in the name; do not merge unrelated settings merely because values match. Do not introduce configuration or generic wrappers without a real approved need. Preserve exact existing values and deadline behavior unless explicitly approved.

**Merged examples (not a claim of exhaustive repository-wide migration):**
- Task authorization + event, Handoff/CI repair, Review claim/submit, lease recovery and CI evidence/binding coordination: business transaction where Application owns multi-step decisions.
- Review context/result/assignment and true queue projection, CI evidence reads: Readers. The current queue listing also recovers expired leases; do not classify that mutation as a pure Reader or change its atomicity without approval.
- Webhook inbox standalone insert/dedup and CI acknowledgment: independent writes; dispatch ledger writes reuse the concrete UoW locally where the invariant is wholly encapsulated. Classify each actual operation, preserve exact claim/lease/fencing/evidence policy.

Only simplify/remove proven redundant setup or retired scopes after inventory and focused TL+Owner approval for transaction changes. No blanket timeout/isolation changes, no unmerged APIs or new wrappers for style. Test changed transaction/lock/deadline/consistency behavior on disposable PostgreSQL 16; skipped checks are NOT VERIFIED. Compare query/round-trip counts when claiming performance improvements.

## Dev implementation and self-check

Use [AGENTS.md](../../AGENTS.md#finish-and-hand-off) and the [single Draft PR template](../templates/handoff.md). Record AC-to-test evidence, actual diff, commands/counts/skips and tested SHA. Do not generate duplicate per-task evidence Markdown. TL reviews the exact current HEAD; Owner alone merges.

## Product orchestration contracts (on demand)

Product Code/Test/Publish, QC, reviewer and approval-gate behavior is specified in the existing [V1 scope and gates](../v1-scope-and-gates.md), not in manual Coder onboarding.
