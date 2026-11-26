# Agent Workflow

**Source baseline:** merged [main after #150](https://github.com/builacviet/agent-workflow/tree/fb552670bf2b9985e52c862482f3af613d3263a6); PostgreSQL-only persistence, legacy `storage/` package retired, R2 migration in progress. Review claim/submit and queue recovery use the canonical Unit of Work. The dispatch ledger lives in Infrastructure and uses immutable typed claims. MCP Presentation and dependency composition are migrated (#144–150); the legacy MCP namespace is retired. [Current architecture](docs/architecture.md) distinguishes source, target and unverified deployment.

**Coder:** read [AGENTS.md](AGENTS.md) + approved task; consult only relevant [standards](docs/conventions/engineering-standards.md) sections/source/tests. [Index](docs/INDEX.md) is optional; old ADRs/runbooks are historical.

The `agent_workflow` package contains the FastMCP workflow engine, storage and
trusted Prepare/Publish Git worker. The trusted worker lives at
`agent_workflow.workers.trusted_git`; its GitHub App credentials and Git/GitHub
operations remain isolated to the Prepare/Publish execution path.

Requires Python 3.11+. From the checkout root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[mcp,test,postgres]'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m agent_workflow.interfaces.cli.admin --help
.venv\Scripts\python.exe -m agent_workflow.interfaces.mcp.server
```

Current Python entrypoints use `agent_workflow`, for example
`python -m agent_workflow.interfaces.cli.admin` and `python -m agent_workflow.interfaces.mcp.server`.
Existing Controller config and task records may use the former repository slug
as an alias. GitHub requests and review links use `builacviet/agent-workflow`.

The `mcp`, `test` and `postgres` extras provide the dependencies used by the
full test suite. The retired `my_wfc` and `order_service` source packages are no
longer distributed; SQLite runtime persistence has been retired.

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

The MCP entrypoint explicitly builds the server in Presentation, registers tools with Composition providers and starts the transport. Importing the module does not create a server instance. The server defaults to loopback port 8000 and `/mcp`; do not start it on a port occupied by another service. The MCP source exposes **seven core workflow tools** and **six additional Tech Lead integration tools** with a separate Auth0 scope. In legacy Bearer mode `ping` and `get_server_info` are public, but **Auth0 OAuth mode protects all `/mcp` calls** (including ping/info). The Tech Lead integration tools require the `wfc:techlead` scope in addition to the workflow scope; configured scope and credentials must be verified before access. No verified-effort attestation is configured, so a task requiring verified High review remains blocked unless separately resolved. See [Tech Lead MCP scopes](docs/guides/mcp-techlead-integrations.md).

**Deployment warning:** root `compose.yaml` still contains obsolete WFC_DB_PATH and SQLite state mount; it is not a verified PostgreSQL deployment. Source requires configured PostgreSQL. Do not use old SQLite runbooks or activate services/tunnels without separate approval. See [runtime inventory](.runtime/README.md), [DB guide](docs/guides/postgres-connection.md), and [architecture](docs/architecture.md).

TASK-001 is historical import, not new review execution. See [historical task](docs/tasks/TASK-001.md), [migration record](docs/migration-my-wfc.md), and [handoff archive](docs/history/handoff-log.md) only when needed.

## V1 Controller-stack MCP, webhook and ngrok

Root `compose.yaml` describes the local MCP, webhook receiver, router and optional ngrok tunnel; validate the PostgreSQL configuration before any separately authorized activation. The old standalone POC Compose definition and
`.runtime/mcp.env.example` have been retired; do not use legacy POC setup commands.
MCP defaults to Auth0 in the **Controller stack configuration template**
(`.runtime/controller-mcp.env.example`) and requires verified local Auth0 values.
The webhook Docker image runs `python -m agent_workflow.interfaces.webhook.server`.
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
