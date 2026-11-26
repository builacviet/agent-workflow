# V1-03C — historical local webhook/CI test (not production)

> **Historical Phase 3.1 runbook (2026-11-20).** The tracked root Compose stack still has SQLite configuration. The CI-only command below uses the PostgreSQL-only launcher. This mixed-stack guide is historical webhook and auth evidence. Use the [as-built architecture](../architecture.md), [runtime factory](postgres-connection.md), [migration runbook](postgres-alembic.md) and [dispatch recovery gate](dispatch-recovery.md#postgresql-controller-and-retirement-gate) to evaluate a separately approved cutover.

The repository's Compose `webhook` service now starts the PostgreSQL-only
composition from `docker/webhook.Dockerfile`. Set `PG_RUNTIME_DSN` in the
ignored `.runtime/controller-webhook.env` file. This source change does not start
services or activate external traffic. The former SQLite webhook inbox is not
copied or migrated; reconcile any pending deliveries before a separately
approved live cutover.

## Components and trust boundaries

- nginx router: /webhooks/github to isolated webhook receiver, all other paths including /mcp and OAuth discovery to the existing MCP service.
- Webhook receiver checks raw-body HMAC, expected repo and Controller App installation. HTTP 202 is notification receipt, never proof of CI success.
- The PostgreSQL Controller CI-only watcher consumes durable PostgreSQL notifications and uses a separate read-only GitHub App to recheck the current PR HEAD and completed Python tests check.
- WFC_CI_GATE_REQUIRED=1 on both worker and MCP enables exact-head review gating. Without it, the old development workflow remains unchanged.
- Real credentialed Codex dispatch is separate from this smoke test and not authorized by V1-03C.

## GitHub App (Owner setup only)

Create a dedicated read-only App installed only on builacviet/agent-workflow. Permissions: Pull requests read, Checks read, Actions read, metadata default. Subscribe to Pull request and Workflow run events. Configure:

- Webhook URL: https://caddy-reopen-scotch.ngrok-free.dev/webhooks/github (replace if the actual reserved ngrok hostname changed)
- Secret: a new 32+ character random value, kept only in the local ignored .secret/controller-webhook-secret file and in GitHub App settings
- App ID, Installation ID, private key PEM: keep separately under local .secret; do not use the Worker App key
- Enable SSL verification. No additional repository webhook is needed.

## Local configuration

See [.runtime configuration inventory](../../.runtime/README.md) for the active and legacy templates, ownership, and key meanings. All non-example files and PEM/secret material are local-only and Git-ignored. The full worker Controller uses a separate `controller.json`; do not substitute it for the `controller-ci.json` CI-only configuration.


1. On the trusted host, copy .runtime/controller-webhook.env.example to .runtime/controller-webhook.env and set the PostgreSQL runtime DSN and Controller App Installation ID.
2. Keep the local webhook secret file at .secret/controller-webhook-secret (exact configured secret, no extra spaces).
3. Copy `.runtime/controller-ci.json.example` to `.runtime/controller-ci.json`, remove its legacy `database` field, and set the Controller App ID, Installation ID and absolute PEM path. CI-only mode uses only Controller App credentials.
4. Configure .runtime/controller-mcp.env and .runtime/ngrok.env. New controller-stack MCP templates select Auth0 OAuth, but copying the template alone is insufficient: restore the verified Auth0 issuer, allowed subject, and ChatGPT CIMD client identity from the existing trusted setup. Do not use a short-lived Bearer workaround for an OAuth deployment.
5. In the trusted host Controller environment, set `PG_RUNTIME_DSN` to the same PostgreSQL database used by the webhook receiver. For CI reviewer gating, MCP must also use that database and both MCP and Controller need `WFC_CI_GATE_REQUIRED=1`. Verify the tracked task has a trusted PR handoff and CI tracking before testing the reviewer gate.

## Local Compose and CI-only process

These commands describe the historical root Compose stack, which still includes
legacy SQLite configuration. They do not establish a complete PostgreSQL
deployment or verify live cutover. A PostgreSQL smoke test requires a separately
approved, disposable PostgreSQL environment.

    docker compose -f compose.yaml config --quiet
    docker compose -f compose.yaml up -d --build mcp webhook router

Start CI-only consumption in a trusted host terminal (no Codex worker is dispatched):

    python -m agent_workflow.interfaces.cli.controller .runtime/controller-ci.json --ci-once
    python -m agent_workflow.interfaces.cli.controller .runtime/controller-ci.json --ci-watch

Run exactly one of the commands above. Both modes read the durable webhook inbox
from PostgreSQL through `PG_RUNTIME_DSN`. Use a config containing
`controller_github_app` with the read-only App settings and omit the historical
SQLite `database` field. At startup the Controller can recheck a bounded set
of tracked PRs. After startup, watch mode checks PostgreSQL for pending
notifications and queries GitHub when pending work needs reconciliation. A pass
with an empty inbox after startup makes no GitHub requests.

Only after local processes have passed their checks, stop any older tunnel using the same reserved hostname and start ngrok:

    docker compose -f compose.yaml --profile tunnel up -d ngrok

## Smoke-test and shutdown

- In GitHub App / Advanced / Recent deliveries, inspect a real harmless pull_request.synchronize or workflow_run.completed notification. A new valid webhook returns 202; replay returns 200. Invalid signature returns 403.
- Confirm the signed event is persisted once, the watcher re-queries current HEAD and named Python tests check (GitHub Actions app), and the trusted CI state is stored by task/head. A stale or missing check must remain BLOCKED.
- Confirm /mcp still requires the existing bearer/OAuth token; webhook HMAC is not a substitute. Never print tokens, raw payload or PEM.
- Stop the public tunnel after the local test:

    docker compose -f compose.yaml --profile tunnel stop ngrok

## Remaining proof before the PR merge

- Independent QC and separate Owner review of this Draft PR.
- Real Docker Desktop/WSL2 + ngrok routing and OAuth-discovery verification; GitHub CI tests are offline only.
- Supervised test of the dedicated Controller App token/installation and a tracked task/PR; App configuration is Owner-controlled.
- Production go-live requires its own hardening/approval; this is a local test only.
