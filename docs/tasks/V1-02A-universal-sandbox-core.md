# V1-02A — Universal Sandbox Core (new foundation task)

**Status:** DRAFT / new Owner-directed foundation scope; separate from ESCALATED V1-02.
**Role:** Codex Subagent A. **Base:** `main@96f5d523814f94ebfec244c66e38d400e2144509`.
**References:** [ADR](../adr/ADR-V1-universal-agent-sandbox.md), [V1 scope](../v1-scope-and-gates.md).

## Goal

Implement the smallest trusted `SandboxPolicy v1` validator and `DockerSandboxBackend` that apply common restrictions regardless of agent CLI. Reuse Phase 3.1 host Docker runner; no new Executor, no Podman, no credential broker. Build independently of the runtime plugin.

## Owned candidate paths (freeze and resolve by Codex Lead before coding)

- `src/agent_workflow/runners/sandbox_policy.py` (new)
- `src/agent_workflow/runners/docker_sandbox.py` (new)
- `tests/test_universal_sandbox.py` (new)
- If integration needs changes to existing `coder_runtime.py`, `docker/coder_entry.py` or Dockerfile, **report to Lead**; do not claim write authority automatically. Lead owns shared integration edits.

Do not edit `docs/tasks/V1-02-plan.md`, `docs/tasks/V1-02-mtp.md`, shared contract documents, Controller or Subagent B-owned files.

## Testable AC / evidence

- **A1:** Policy v1 validates exact allowed roots, role/phase/attempt, pinned image, network/capability requests, bounded resources and rejects unknown/noncanonical/privilege-widening data before launch.
- **A2:** Real Docker invocation builder sets non-root user, privilege/capability restrictions, explicit mounts, network policy, read-only root where compatible, timeout, owned attempt identity. Docker engine access belongs only to trusted host.
- **A3:** Shared suite S01–S08 tests allowed read/write/shell, denied path/egress/escalation, synthetic *same-role* auth canary + benign-tool positive, timeout/delayed-create and cleanup fencing. Use disposable fixtures, no real auth or production effects. `NOT RUN` where environmental execution is unavailable.
- **A4:** Failure is safe and observable: absent capability, ambiguous engine ownership or failed same-role secret isolation => `BLOCKED`, never false PASS.
- **A5:** No legacy WFC/Phase 3.1 regression and no protected artifact mutations.

**Handoff:** branch/worktree, exact base and HEAD, changed paths, tests/environment, independent evidence vs mock, unresolved blockers. No merge.

**Non-goal:** prove that Codex/Claude/OpenCode/AGY sandboxes are equivalent, online login/smoke, generalized sandbox executor platform.
