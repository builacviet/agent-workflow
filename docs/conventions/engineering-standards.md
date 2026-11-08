# Engineering Quality Standards — Agent Workflow V1

**Version:** 1.0 · **Status:** shared engineering guidance for Dev, QC and Independent TL Reviewer, subject to Owner merge. This is a **quality rubric**, not an authorization, sandbox, deployment claim or substitute for approved AC.

## Authority and proportionality

In conflicts, follow `AGENTS.md`, the Owner-approved [V1 scope and gates](../v1-scope-and-gates.md), the exact approved task/AC and existing source invariants before this guidance. Read only relevant sections. The following are **judgment principles, not blanket architecture mandates**:

| Principle | Practical application | Avoid |
| --- | --- | --- |
| **KISS** | Prefer the simplest solution that meets AC, is safe, and is testable | Complex layering, meta-frameworks or excessive indirection |
| **YAGNI** | Build only current approved requirements; identify future needs without implementing them | Speculative APIs, optional flags, unused plugin systems |
| **DRY** | Remove **real knowledge duplication** when rules are stable and change together | Abstracting two superficially similar lines into a confusing generic helper |
| **SOLID** | Use cohesion, small responsibilities and dependency direction as heuristics, especially at important boundaries | Forcing an interface/factory/class per function or adding abstraction without demonstrated benefit |
| **Clean Code** | Explicit names, contracts, types, errors and clear control flow; small purposeful units | Clever tricks, broad exception swallowing, unbounded side effects |
| **Clean Architecture** | Keep domain behavior independent of persistence, network, frameworks and credentials; allow adapters at the boundary | Rebuilding the existing codebase into artificial layers outside task scope |
| **Consistency over novelty** | Follow the *as-built* package structure and project conventions; isolate changes | Unrequested sweeping refactors disguised as small fixes |

**Pragmatic rule:** Start with a focused change that satisfies AC. Introduce an abstraction only when it improves clarity, testability or a demonstrated boundary. Reviewers must cite a concrete defect, regression or maintainability consequence rather than fail a PR for not using a named pattern.

## Current repository-specific coding baseline

- **Language and style:** Python 3.11+, PEP 8, type hints, descriptive identifiers, explicit input validation and exceptions. Prefer small functions that are easy to unit test. Preserve public contract and backward compatibility unless approved AC says otherwise.
- **Behavior:** Maintain `order_service` `Decimal` invariants (finite/nonnegative prices, no implicit floats), validation-before-state-mutation, immutable snapshots, documented `TypeError` / `ValueError` / `KeyError` semantics. Preserve idempotency, atomic transitions, exact SHA checks and two-round review behavior where applicable. Never silently reinterpret existing persistence or status semantics.
- **As-built architecture:** `agent_workflow/domain` owns rules; `storage` owns SQLite; `orchestration` coordinates Controller; `runners` operate Docker stages; `integrations` handles repository identity; `mcp` serves authenticated tools. `coder_worker` owns GitHub App auth and publishing. Avoid domain → storage/network dependency inversion violations and cross-package imports that couple `order_service` with WFC. New architectural modules require approved task scope.
- **Security:** Validate untrusted input at boundaries; parameterize SQL; restrict filesystem paths and mounted capabilities; keep GitHub App PEM/tokens, live WFC database and Docker socket away from untrusted agents. Do not print secrets in errors, logs or reports. Fail closed on missing credentials, authorization, stale SHA, unexpected status or uncertain remote effects.
- **Reliability:** Prefer explicit timeouts and bounded retries only for safe, idempotent operations. Respect crash/reconciliation behavior; an ambiguous publish must be `BLOCKED`, never automatically repeated. Avoid hidden global state, unbounded queues or nondeterministic tests.
- **Scope/compatibility:** Preserve existing nine MCP tool names/signatures/auth behavior, project packaging and service entrypoints. V1 does not introduce Executor/Podman, parallel dispatch, automated merge, or live credential/DB migrations.

## Dev implementation and self-check

Before coding, map each AC to observable behavior and related tests; note unchanged invariants and permitted paths. While coding, prefer clear control flow and single-responsibility units; avoid needless generalization. After coding, inspect diff for unrelated changes, duplicated business rules, accidental secret handling, unsafe defaults and breaks in dependency direction. Run permitted deterministic pytest and negative tests; report actual commands, environment, exit status, skipped cases and limitations. Do not self-approve.

## QC testing and evidence standard

Prioritize tests by **impact × likelihood** and trust-boundary risk. Design from AC and failure modes, not implementation details alone:

- **Functional:** positive, negative, invalid type/value, equivalence partition, boundary values and state transitions.
- **Safety/security:** permissions, unauthorized access, secret leakage, path traversal, input validation, role separation, stale SHA/package version and fail-closed errors.
- **Reliability:** idempotency, failure atomicity, timeout/recovery, duplicate/reordered events and partial operations **when relevant to approved AC**.
- **Integration/regression:** existing public contract, persisted history, cross-module interactions and Python packaging. Use isolated fixtures and synthetic data.
- **Evidence:** deterministic assertions, independent setup/teardown, real commands and exit codes, exact reviewed HEAD, per-case `PASS|FAIL|BLOCKED|NOT RUN`; no unstated test coverage percentage target. Pre-Dev MTP cases are **NOT RUN**, not PASS.
- **Proportionate testing:** do not require a load test for a docs-only change or test a future subsystem not in scope. Document N/A with reason; escalate uncovered high-risk ACs.

QC tests *what the system does* against AC and approved MTP. QC may recommend implementation fixes but does not rewrite production logic or approve its own MTP/evidence.

## Independent TL Reviewer code-quality rubric

Review findings should identify a **measurable risk and supporting evidence**, with severity, AC link, file/line and minimal remediation:

1. **Correctness and requirements:** AC/DoD behavior, error paths, invariants, migrations and compatibility; unauthorized scope.
2. **Security and trust boundaries:** injection/path safety, auth/permissions, secrets, untrusted input, sandbox separation, exact SHA and human approval gates.
3. **Architecture and maintainability:** responsibilities/cohesion, dependency direction, interfaces only where justified, semantic DRY, change blast-radius, coupling, clarity and rollback.
4. **Tests and evidence:** case/AC traceability, meaningful negative/boundary/regression tests, deterministic fixtures, reliable CI on exact current HEAD and explicit gaps.
5. **Operational quality where applicable:** logging without secrets, timeouts, failure handling, idempotency, observability, resource use and recovery without unsafe retries.

**Severity guidance:** `BLOCKER` for a demonstrated security/authorization breach, AC failure, data loss, or required gate bypass; `MAJOR` for substantive regression or high-risk omission; `MINOR` for limited maintainability/testability risk; `SUGGESTION` for optional style. Do **not** block on a subjective DRY/SOLID preference alone. Missing or stale evidence is operational `BLOCKED` (not a completed failed code review round). Second failed formal code-review round is `ESCALATED`, per V1 gates.

## Review boundaries

Dev implements under approved scope. QC designs the approved test plan then executes it separately. Independent TL Reviewer reviews the plan, code and QC evidence but does not edit artifacts under its own review. **Owner alone approves package versions and merges PRs.** These Markdown standards are advisory; actual role selection, model/effort policy, mounted paths and capabilities must be enforced by trusted runtime in V1-02.
