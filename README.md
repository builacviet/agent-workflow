# Agent Workflow

**Agent onboarding:** Start with [AGENTS.md](AGENTS.md) and the [documentation map](docs/INDEX.md), then the current approved task and only its relevant source/tests. [CURRENT architecture](docs/architecture.md) and [TARGET CL-12 ADR](docs/adr/CL-12-architecture.md) are different; merged design decisions are **not** deployed features. For handoff use the [short template](docs/templates/handoff.md); the [historical log](docs/history/handoff-log.md) is not required startup context.

Two independently importable Python packages share this repository:

- `order_service`: small immutable in-memory order domain using Decimal. Application source and tests are unchanged from target main.
- `agent_workflow`: migrated FastMCP workflow engine with SQLite, authenticated workflow tools, leases, GitHub SHA checks and two-round review history. It does not import or call order_service.

Requires Python 3.11+. From the checkout root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[mcp,test]'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m agent_workflow.admin seed-history
.venv\Scripts\python.exe -m agent_workflow.admin seed-synthetic
.venv\Scripts\python.exe -m agent_workflow.server
```

The legacy `python -m my_wfc.admin`, `my_wfc.controller` and `my_wfc.server`
commands still work. Use `agent_workflow` for Python imports. Existing Controller
config and task records may use the former repository slug as an alias. GitHub
requests and review links use `builacviet/agent-workflow`.

The `mcp` extra is optional: `pip install -e '.[test]'` suffices for the sample application and its tests (`python -m pytest tests/test_order_service.py -q`). Running the full suite requires both extras. There are no mandatory distribution runtime dependencies for the order package.

The server defaults to loopback port 8000 and /mcp. Do not start it on a port occupied by the original server. Migration verification uses temporary loopback ports; it does not switch the existing server, tunnel or plugin. Only ping/server_info are public; all seven workflow tools require protected transport authentication. No verified-effort attestation is configured, so verified High approval remains blocked.

Database initialization is idempotent on the first authenticated workflow call or local admin command. Default editable-install state is checkout/data/workflow.sqlite3; WFC_DB_PATH may specify an absolute alternative. `.env.example` lists settings without secrets and is not automatically loaded. Do not point the consolidated server at the original project's database.

TASK-001 historical state is imported as ESCALATED, two completed rounds, with original SHAs and unverified effort. This is historical import, not a new review execution. The synthetic fixture uses a dedicated real branch `poc/review-baseline`, pinned to the original baseline; main may advance independently. Existing copied fixture metadata requires explicit operator handling described below.

- [Project rules](AGENTS.md)
- [Architecture](docs/architecture.md)
- [Workflow tools, schema, state machine and security](docs/my-wfc.md)
- [Migration provenance, state backup/copy, fixture strategy and rollback](docs/migration-my-wfc.md)
- [Handoff and actual verification evidence](docs/handoff.md)
- [Review policy](docs/review-policy.md)
- [Historical TASK-001 specification](docs/tasks/TASK-001.md)

The migration branch is for independent ChatGPT Tech Lead review. Codex does not approve its own work or merge branches. TASK-001 remains escalated; its branch and code are not changed by consolidation.


## V1 Controller-stack MCP, webhook and ngrok

Use the root `compose.yaml` for the active local MCP, webhook receiver, router,
and optional ngrok tunnel. The old standalone POC Compose definition and
`.runtime/mcp.env.example` have been retired; do not use legacy POC setup commands.
MCP defaults to Auth0 in the **Controller stack configuration template**
(`.runtime/controller-mcp.env.example`) and requires verified local Auth0 values.
See the [runtime configuration inventory](.runtime/README.md) and the
[V1-03C local integration runbook](docs/guides/v1-03c-local-webhook.md).
Do not expose a public tunnel until OAuth access is verified.

Phase 3.1 adds an independent GitHub App / Codex Controller. See [operator runbook](docs/phase-3.1.md) and [authorized task / ACs](docs/tasks/PHASE-3.1.md). Activation requires local credentials and a synthetic live test; implementation does not authorize merge or operate Workflow Console v2.

Current Phase 3.1: worker owns all Git/GitHub; WFC uses repository interfaces for local SQLite state only. See docs/phase-3.1.md.

## Optional Auth0 OAuth for ChatGPT MCP

The Phase 3.1 MCP can be configured as an Auth0-backed OAuth resource server
without changing the default legacy Bearer configuration. It verifies RS256
JWTs from a dedicated Auth0 tenant, publishes RFC 9728 metadata, and binds
workflow access to one allowlisted Auth0 user ID. In OAuth mode the SDK protects
all /mcp calls (including ping/info); legacy mode keeps those two tools public.
See [Auth0 OAuth setup and one-shot E2E gate](docs/oauth-auth0.md). Do not turn on
open dynamic client registration in a shared production Auth0 tenant.


## Opt-in Auth0 OAuth resource-server POC

For the isolated Auth0/CIMD OAuth implementation and prerequisites, see [Auth0 OAuth runbook](docs/auth0-oauth.md). It is stacked on Phase 3.1, disabled by default, and requires operator-run tests and actual Auth0 login before deployment. Existing bearer-mode MCP/ngrok remains unchanged until an explicit cutover.
