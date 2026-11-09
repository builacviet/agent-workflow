# V1-02B — Portable Runtime Plugin Core (new foundation task)

**Status:** DRAFT / new Owner-directed foundation scope; separate from ESCALATED V1-02.
**Role:** Codex Subagent B. **Base:** `main@96f5d523814f94ebfec244c66e38d400e2144509`.
**References:** [ADR](../adr/ADR-V1-universal-agent-sandbox.md), [V1 scope](../v1-scope-and-gates.md).

## Goal

Implement a versioned vendor-neutral runtime plugin interface, allowlisted registry and **one real Codex CLI plugin** using the existing pinned Codex CLI. Add a deterministic fake adapter for substitution testing. Do not implement Claude Code, OpenCode or AGY yet.

## Owned candidate paths (freeze and resolve by Codex Lead before coding)

- `src/agent_workflow/runners/runtime_plugin.py` (new)
- `src/agent_workflow/runners/runtime_registry.py` (new)
- `src/agent_workflow/runners/codex_plugin.py` (new)
- `tests/test_runtime_plugins.py` (new)
- If wiring requires modifying Controller, existing runner or the Docker entrypoint, **report to Lead**; Lead owns shared integration edits.

Do not modify Subagent A files, `docs/tasks/V1-02-plan.md`, `docs/tasks/V1-02-mtp.md`, or shared contracts during parallel implementation.

## Integration seam (freeze with Lead)

Plugin declares versioned `runtime_id`, pinned runtime/image, supported provider/model/effort pairs, tool/permission capabilities, credential mode, instruction delivery and telemetry availability. Plugin emits **declarative `LaunchSpecV1`**, not Docker commands or authority; shared backend maps it onto `SandboxPolicy v1`. CLI-specific output is normalized without accepting an agent-authored success/approval. Runtime and model provider are separate concepts.

## Testable AC / evidence

- **B1:** `codex-cli` plugin registry and pinned manifest are valid, no auto-discovered plugins or agent overrides.
- **B2:** Fake plugin with different launch/result format substitutes without changing trusted task/role/phase/allowlist/gate identity.
- **B3:** Unknown runtime, schema, unsupported provider/model/effort/capability, malformed launch spec and missing sandbox requirements => prelaunch reject, no fallback.
- **B4:** Result provenance joins task, role/phase, attempt, base/HEAD/snapshot and actual execution observation; no self-attested CI/QC/TL/Owner approval.
- **B5:** Model/effort metadata classifications distinguish requested, runtime-reported and independently verified inference. Absent inference-level proof yields `UNVERIFIED` or `UNAVAILABLE` (never false `VERIFIED`).
- **B6:** Codex plugin supports offline integration/contract tests and defers actual credentialed online smoke pending separate authorization. Do not read or expose real tokens.

**Handoff:** branch/worktree, exact base and HEAD, changed paths, tests/environment and unproven runtime/security assumptions. No merge.

**Non-goal:** implement extra runtime adapters, universalized native Codex sandbox, secret broker, production deployment.
