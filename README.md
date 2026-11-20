# Agent Workflow

**Documentation status (2026-11-20):** reconciled against merged `main@70b333be08d87a90190cdb06cdde0a9b1cb635f3` (PR #98). This repository contains both **PostgreSQL-first R2 implementations** and **legacy Phase 3.1/SQLite paths awaiting separately approved retirement**. Merged code and CI do not by themselves prove a deployed end-to-end product.

**Agent onboarding:** Start with [AGENTS.md](AGENTS.md) and the [documentation map](docs/INDEX.md), then the current approved task and only its relevant source/tests. [CURRENT architecture](docs/architecture.md) and [TARGET CL-12 ADR](docs/adr/CL-12-architecture.md) are different; merged design decisions are **not** deployed features. For handoff use the [short template](docs/templates/handoff.md); the [historical log](docs/history/handoff-log.md) is not required startup context.

The `agent_workflow` package contains the FastMCP workflow engine, storage and
trusted Prepare/Publish Git worker. The trusted worker lives at
`agent_workflow.workers.trusted_git`; its GitHub App credentials and Git/GitHub
operations remain isolated to the Prepare/Publish execution path.

Requires Python 3.11+. From the checkout root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[mcp,test,postgres]'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m agent_workflow.admin --help
.venv\Scripts\python.exe -m agent_workflow.server
```

Current Python entrypoints use `agent_workflow`, for example
`python -m agent_workflow.admin` and `python -m agent_workflow.mcp.server`.
Existing Controller config and task records may use the former repository slug
as an alias. GitHub requests and review links use `builacviet/agent-workflow`.

The `mcp`, `test` and `postgres` extras provide the dependencies used by the
full test suite. The retired `my_wfc` and `order_service` source packages are no
longer distributed; some legacy Phase 3.1 processes still retain SQLite state.

**Local admin is PostgreSQL-only:** `provision --file <authorized-task-spec.json>`,
`seed-history`, `seed-synthetic` and `reset-synthetic` require a validated
**loopback** `PG_RUNTIME_DSN` and a separately migrated PostgreSQL database.
They never initialize/migrate schema automatically and never fall back to SQLite.
The accepted `--postgres` flag is a **deprecated no-op**. Run seed/reset only
against explicitly approved disposable fixtures; see [PostgreSQL connection
guide](docs/guides/postgres-connection.md) and [Alembic runbook](docs/guides/postgres-alembic.md).
Expected CLI validation/conflict failures exit `2`; temporary persistence/timeouts
exit `75`. CLI structured JSON logs and the SQL correlation comment use an
optional validated `CORRELATION_ID` (PR #98).

The server defaults to loopback port 8000 and /mcp. Do not start it on a port occupied by the original server. Migration verification uses temporary loopback ports; it does not switch the existing server, tunnel or plugin. Only ping/server_info are public; all seven workflow tools require protected transport authentication. No verified-effort attestation is configured, so verified High approval remains blocked.

**Separate runtime paths:** The source `agent_workflow.mcp.server` now composes a
PostgreSQL workflow provider; the tracked root `compose.yaml` still includes
legacy SQLite volume/environment wiring for its MCP/webhook stack and is **not
a verified PostgreSQL end-to-end deployment configuration**. The legacy
`WFC_DB_PATH` setting applies only to those older paths, **not** to the R2
admin CLI. For the root local Controller stack, inspect
`.runtime/controller-mcp.env.example` and [runtime configuration inventory](.runtime/README.md)
before changing any service. Compose uses an ignored env file; the Python
server does not auto-load it. Do not point these services at production or an
unapproved database; deployment/cutover requires separate Owner authorization.

TASK-001 historical state is imported as ESCALATED, two completed rounds, with original SHAs and unverified effort. This is historical import, not a new review execution. The synthetic fixture uses a dedicated real branch `poc/review-baseline`, pinned to the original baseline; main may advance independently. Existing copied fixture metadata requires explicit operator handling described below.

- [Project rules](AGENTS.md)
- [Architecture](docs/architecture.md)
- [Historical WFC workflow guide](docs/my-wfc.md)
- [Historical migration provenance and state-copy record](docs/migration-my-wfc.md)
- [Handoff and actual verification evidence](docs/handoff.md)
- [Review policy](docs/review-policy.md)
- [Historical TASK-001 specification](docs/tasks/TASK-001.md)

**Historical Phase 2.2 note:** The original migration branch was submitted for
Tech Lead review; it is not today's active implementation branch. Codex does
not approve its own work or merge branches. The TASK-001 fixture is historical
and importing it does not execute a new review.


## V1 Controller-stack MCP, webhook and ngrok

Use the root `compose.yaml` for the active local MCP, webhook receiver, router,
and optional ngrok tunnel. The old standalone POC Compose definition and
`.runtime/mcp.env.example` have been retired; do not use legacy POC setup commands.
MCP defaults to Auth0 in the **Controller stack configuration template**
(`.runtime/controller-mcp.env.example`) and requires verified local Auth0 values.
See the [runtime configuration inventory](.runtime/README.md) and the
[V1-03C local integration runbook](docs/guides/v1-03c-local-webhook.md).
Do not expose a public tunnel until OAuth access is verified.

**Legacy Phase 3.1:** The GitHub App / Codex Controller design and its
older local SQLite procedures are recorded in the [dated operator runbook](docs/phase-3.1.md)
and [historical task ACs](docs/tasks/PHASE-3.1.md). The trusted Worker owns
Git/GitHub operations. These records are not instructions to reactivate the
SQLite workflow or proof of a current PostgreSQL deployment. Activation,
credentials, tunnel access and real E2E still require independent evidence.

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
