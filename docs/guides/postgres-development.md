# PostgreSQL 16 for local development

This is an independent development-only Compose project. It does not migrate or connect the existing services. Docker Desktop with WSL2 integration or a Linux Docker Engine, Compose v2, and Python 3.11+ are required. Run commands from this worktree's root. The fixed project name `agent-workflow-pg-dev` reserves its own network and volume; do not use it for other projects.

## Configure and start

PowerShell:

```powershell
Copy-Item .runtime/postgres-dev.env.example .runtime/postgres-dev.env
notepad .runtime/postgres-dev.env
python scripts/pg_dev_preflight.py up -d --wait --wait-timeout 120
```

WSL2/Linux:

```sh
cp .runtime/postgres-dev.env.example .runtime/postgres-dev.env
${EDITOR:-vi} .runtime/postgres-dev.env
python3 scripts/pg_dev_preflight.py up -d --wait --wait-timeout 120
```

Set a local password. The ignored file uses one literal `KEY=value` per line: no shell expansion, quoting or escape syntax; spaces and dollar signs are literal. Required database, user and password cannot be blank. Port defaults to 55432 and must be 1–65535. The preflight reads this file and supplies the validated values to Compose, overriding ambient PG_DEV variables. Run every Compose operation through the preflight; do not print `config` output because it contains the password. A nonzero exit or timeout means readiness was not verified. Check Docker availability, port conflicts and `python scripts/pg_dev_preflight.py logs postgres` (use `python3` on Linux).

Connect a local client to `127.0.0.1` and the configured port, database and user. The named volume keeps the initialized database across stop/start, recreation and ordinary down/up. PostgreSQL initialization variables apply only to a fresh volume; changing them later does not update existing roles or passwords.

```sh
python3 scripts/pg_dev_preflight.py stop
python3 scripts/pg_dev_preflight.py start
python3 scripts/pg_dev_preflight.py up -d --force-recreate --wait --wait-timeout 120
python3 scripts/pg_dev_preflight.py down
python3 scripts/pg_dev_preflight.py up -d --wait --wait-timeout 120
```

On PowerShell replace `python3` with `python`. The network is project-scoped and the port is bound to IPv4 loopback. This is not a guarantee against host-gateway access or a malicious Docker host.

## Destructive development reset

This permanently deletes all data in the dev volume. First verify the Docker context and inspect the project resources: `docker context show`, `docker compose -p agent-workflow-pg-dev -f compose.postgres.dev.yaml ls` and `docker volume inspect agent-workflow-pg-dev_pg_data`. Proceed only if they identify this disposable development project and its expected volume. Then run `python scripts/pg_dev_preflight.py down --volumes` (Linux: `python3`). Never use this reset for a shared or production project. Back up any data you need first.
