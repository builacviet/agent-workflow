# Tech Lead Review POC

Two independently importable Python packages share this repository:

- `order_service`: small immutable in-memory order domain using Decimal. Application source and tests are unchanged from target main.
- `my_wfc`: migrated FastMCP workflow engine with SQLite, authenticated workflow tools, leases, GitHub SHA checks and two-round review history. It does not import or call order_service.

Requires Python 3.11+. From the checkout root:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e '.[mcp,test]'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m my_wfc.admin seed-history
.venv\Scripts\python.exe -m my_wfc.admin seed-synthetic
.venv\Scripts\python.exe -m my_wfc.server
```

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


## Optional Docker MCP deployment

A separate POC-only Docker Compose stack runs the MCP server with a persistent
SQLite volume and an optional ngrok container using the stable domain
`caddy-reopen-scotch.ngrok-free.dev`. It does not include the Codex Controller
or reuse Workflow Console v2. See [Docker setup](docs/docker-mcp.md).
Default protected workflow access remains unchanged; never enable public no-auth
against non-synthetic data.

Phase 3.1 adds an independent GitHub App / Codex Controller. See [operator runbook](docs/phase-3.1.md) and [authorized task / ACs](docs/tasks/PHASE-3.1.md). Activation requires local credentials and a synthetic live test; implementation does not authorize merge or operate Workflow Console v2.
