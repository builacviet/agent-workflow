# Worker Runtime Adapter — CURRENT and TARGET contract

**CURRENT:** `src/agent_workflow/application/runtime/dtos.py` defines RuntimeRequest/Spec, RuntimeManifest, RuntimePlan and WorkerResult; `application/runtime/ports.py` defines IAgentRuntimePlugin (manifest, plan, result). `infrastructure/runtime/registry.py` defines RuntimeRegistry, and `infrastructure/runtime/codex_plugin.py` implements CodexCLIPlugin. Composition constructs the trusted Codex-only registry once per dispatch scope, validates selection before database acquisition, and injects the same registry into the Worker when a claim is eligible. The host accepts an approved image and capability-matched request. RuntimeRegistry._resolve currently supports **only role=dev and phase=code**. CodexCLIPlugin is a built-in legacy adapter with special approved auth behavior.

The concrete offline Docker backend, its transport helpers and detached watchdog live in `infrastructure/runtime/sandbox.py` and `infrastructure/runtime/sandbox_watchdog.py`. The watchdog launches via `agent_workflow.infrastructure.runtime.sandbox_watchdog`. Host link detection lives in `infrastructure/runtime/workspace.py`. Composition creates a claim-scoped SandboxManager with the validated state directory from the entrypoint's AppConfig and injects that exact instance into the legacy CoderRuntime. CoderRuntime neither reads the environment nor constructs a default backend; orchestration cutover remains a separate task. This ownership change is not evidence of real Docker conformance; authorized live checks remain NOT VERIFIED until run.

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
