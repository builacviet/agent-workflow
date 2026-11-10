# V1-03C — local live webhook/CI test (not production)

## Components and trust boundaries

- nginx router: /webhooks/github to isolated webhook receiver, all other paths including /mcp and OAuth discovery to the existing MCP service.
- Webhook receiver checks raw-body HMAC, expected repo and Controller App installation. HTTP 202 is notification receipt, never proof of CI success.
- Trusted Controller CI-only watcher consumes durable SQLite notifications and uses a separate read-only GitHub App to recheck current PR HEAD and complete named Python tests Checks.
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


1. On the trusted host, copy .runtime/controller-webhook.env.example to .runtime/controller-webhook.env and set the Controller App Installation ID.
2. Keep the local webhook secret file at .secret/controller-webhook-secret (exact configured secret, no extra spaces).
3. Copy .runtime/controller-ci.json.example to .runtime/controller-ci.json and set DB path, Controller App ID / Installation ID and absolute PEM path. This CI-only config does NOT require worker credentials.
4. Configure .runtime/controller-mcp.env and .runtime/ngrok.env. New controller-stack MCP templates select Auth0 OAuth, but copying the template alone is insufficient: restore the verified Auth0 issuer, allowed subject, and ChatGPT CIMD client identity from the existing trusted setup. Do not use a short-lived Bearer workaround for an OAuth deployment. MCP auth must remain enabled; never set WFC_POC_NO_AUTH=1 with public ingress.
5. Verify `.runtime/controller-ci.json` uses the same physical SQLite database as the Compose services (`./data/controller/workflow.sqlite3` in this checkout), with host-absolute paths for the DB and Controller App PEM. If testing CI reviewer gating, configure WFC_CI_GATE_REQUIRED=1 for the MCP process and the trusted Dev Controller process. A task must have a trusted PR handoff and ControllerCiState tracking first; no unregistered task can pass CI.

## Local Compose and CI-only process

Run from the repository root. Use a disposable local database first and back up current state before testing new SQLite migrations.

    docker compose -f compose.controller.yaml config --quiet
    docker compose -f compose.controller.yaml up -d --build mcp webhook router

Start CI-only consumption in a trusted host terminal (no Codex worker is dispatched):

    python -m agent_workflow.orchestration.controller .runtime/controller-ci.json --ci-once
    python -m agent_workflow.orchestration.controller .runtime/controller-ci.json --ci-watch

Run exactly one of the above commands, not both concurrently. The watcher scans local inbox only; it calls GitHub API on new events and bounded startup recovery, not global periodic CI polling.

Only after local processes have passed their checks, stop any older tunnel using the same reserved hostname and start ngrok:

    docker compose -f compose.controller.yaml --profile tunnel up -d ngrok

## Smoke-test and shutdown

- In GitHub App / Advanced / Recent deliveries, inspect a real harmless pull_request.synchronize or workflow_run.completed notification. A new valid webhook returns 202; replay returns 200. Invalid signature returns 403.
- Confirm the signed event is persisted once, the watcher re-queries current HEAD and named Python tests check (GitHub Actions app), and the trusted CI state is stored by task/head. A stale or missing check must remain BLOCKED.
- Confirm /mcp still requires the existing bearer/OAuth token; webhook HMAC is not a substitute. Never print tokens, raw payload or PEM.
- Stop the public tunnel after the local test:

    docker compose -f compose.controller.yaml --profile tunnel stop ngrok

## Remaining proof before the PR merge

- Independent QC and separate Owner review of this Draft PR.
- Real Docker Desktop/WSL2 + ngrok routing and OAuth-discovery verification; GitHub CI tests are offline only.
- Supervised test of the dedicated Controller App token/installation and a tracked task/PR; App configuration is Owner-controlled.
- Production go-live requires its own hardening/approval; this is a local test only.
