# Worker Runtime Adapter â€” CURRENT and TARGET contract

## Current dispatch path

CLI Presentation invokes `RunDispatchPassUseCase`. Dispatch Composition preflights
the local settings, constructs one approved registry per scope, and injects
`AgentExecutionService` directly. Dispatch checks Domain eligibility, exact task
identity and the allowlist before the Service is called. The per-claim factory
creates a `SandboxManager`, the Docker adapter and the workspace adapter only
after the claim and leadership checks. The sandbox uses the state directory from
the entrypoint's validated `AppConfig`.

The Application Service builds the typed request, obtains the runtime plan and
coordinates Prepare → Code → Test → Publish. It records runtime evidence, submits
the handoff and tracks CI for the exact published HEAD. Dispatch commits finish,
clears the terminal engine pin through the same adapter, then releases leadership.
A phase or publication failure can leave the pin and ledger available for
reconciliation.

The Application runtime DTOs and `IAgentRuntimePlugin` contract remain unchanged.
`RuntimeRegistry` validates role, phase, capabilities, plan and image approval.
Dispatch supports Dev Code with the built-in Codex CLI plugin and its existing
device-login profile. Docker stage execution and engine recovery live in
`infrastructure/runtime/docker_runtime.py`. Snapshot and result checks live in
`workspace.py`, local preflight in `worker_config.py`, and the outbound Worker
assignment payload in `worker_payload.py`. The trusted Git worker owns Prepare
and Publish credentials. The sandbox transport and detached watchdog remain in
`sandbox.py` and `sandbox_watchdog.py`.

The legacy runners package and root compatibility import have been removed.
Live Docker conformance remains unverified.

**NOT CURRENT:** generic Planner/QC/Reviewer adapter role routing, a public plugin installation/SDK marketplace, a Docker/Podman interchangeable ExecutionBackend port, trusted model/effort attestation or automatic provider credentials. Do not report these as implemented.

## TARGET (proposed)

- WorkerRuntimePort: versioned request/manifest/capabilities, bounded plan, untrusted result, explicit error/cancellation contract. Application has no Codex/Claude/Gemini provider branches.
- WorkerRuntimeRegistry: approved adapters only; capability/role/phase negotiation; rejects unsupported versions, models, image IDs, paths, environment and credentials. No arbitrary dynamic plugin loading from untrusted workspace.
- ExecutionBackendPort: start/observe/timeout/stop/reconcile isolated attempts, with engine identity and independently verified immutable image. Docker adapter first, Podman only after separately approved tests.
- Runtime metadata and model/effort telemetry are **claims unless the host independently verifies them**; a plugin cannot self-certify the execution environment.
- Prepare/Publish retain independent trusted GitHub App permissions. Code/Test receive no workflow database, repository credentials, secrets or Docker socket.
- Adapter failure, network ambiguity, Controller death and publication uncertainty must fail closed. Distinct dispatch/attempt identifiers and durable recovery evidence are required.

## Compatibility and acceptance

An additional approved coding runtime should require **only** a version-compatible adapter, approved manifest/image/configuration and contract tests. Domain/Application business decisions and Controller orchestration do not gain provider-specific conditionals.

For any new adapter test: unsupported runtime/version/capability/role/phase, wrong image, malicious output, secret env value, timeout, cancellation, execution recovery, independent evidence and unchanged Code/Test isolation. Role expansion and new execution backend remain separate approved tasks; neither is required by R0.
