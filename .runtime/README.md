# Local runtime configuration — Agent Workflow V1

This directory holds **operator-owned configuration**, not just environment variables.
Only templates (`*.example`) and this inventory are committed to Git.
Actual `.env`, `.json`, PEM, keys and tokens are local-only.

- `.runtime/`: local process and Compose configuration (`.env` for environment variables, `.json` for structured Controller options)
- `.secret/`: GitHub App private keys, webhook HMAC secret, and other local secret files; never paste or commit them
- `data/controller/`: shared SQLite file for the local Controller stack; never commit it

## Files and owners

| Template (tracked) | Local file (ignored) | Owner / purpose | Required by |
| --- | --- | --- | --- |
| `controller-mcp.env.example` | `controller-mcp.env` | MCP process auth (Auth0 default for this stack) | `compose.controller.yaml` |
| `controller-webhook.env.example` | `controller-webhook.env` | Ingress validation: Controller GitHub App installation ID | `compose.controller.yaml` |
| `controller-ci.json.example` | `controller-ci.json` | Host CI-only watcher: Controller read-only App ID, installation, PEM, DB | `--ci-once` / `--ci-watch` |
| `controller.json.example` | `controller.json` | **Separate** full Coder-dispatch Controller config; not used in V1-03C CI-only smoke | Full Controller |
| `ngrok.env.example` | `ngrok.env` | ngrok authtoken | Both Compose stacks |
| `mcp.env.example` | `mcp.env` | **Legacy standalone POC stack**, isolated DB; not used by `compose.controller.yaml` | `compose.yaml` |

`mcp-auth0.env.example` was a duplicate of the Auth0 section already maintained in
`controller-mcp.env.example`. It has been removed. Keep one MCP auth template for
the Controller stack; do not create both variants.

Do not mistake the two Controllers: `controller-ci.json` runs **only**
trusted CI reconciliation on the host; `controller.json` configures the full
Codex dispatch worker path, with a different credential and additional options.

## Naming rules (current API contract)

A file name has the form `<stack>-<service>.<format>` for Controller-stack
components; `controller-ci.json` and `controller.json` distinguish the two
Controller execution modes. Standalone POC files retain their historical names
until that stack is retired or migrated.

Existing Python and Compose code uses a `WFC_` prefix as a **legacy environment
namespace**, not a security or protocol requirement. It avoids collisions in
shared host process environments. **Do not remove it only in the local files:**
that would make the running code ignore the setting. A separate coordinated
migration can introduce cleaner service-local names, tests, backward-compatible
aliases, and deprecation before deleting the old names.

| Existing key / config | What it actually means | Supplied by |
| --- | --- | --- |
| `WFC_AUTH_MODE` | MCP authentication mode (`auth0` or explicit legacy `bearer`) | Operator, `controller-mcp.env` |
| `WFC_AUTH0_ISSUER` | Auth0 tenant HTTPS issuer | Operator, `controller-mcp.env` |
| `WFC_AUTH0_ALLOWED_SUB` | Exact allowed Auth0 user subject | Operator, `controller-mcp.env` |
| `WFC_OAUTH_EXPECTED_CLIENT_ID` | Exact trusted OAuth / ChatGPT CIMD client identity URL | Operator, `controller-mcp.env` |
| `WFC_AUTH0_SCOPE` | Required scope (normally `wfc:workflow`) | `controller-mcp.env` |
| `WFC_AUTH0_DIAGNOSTICS` | Temporary diagnostic messages; keep off normally | `controller-mcp.env` |
| `WFC_POC_NO_AUTH` | **Unsafe** unauthenticated POC switch; controller stack forces `0` | Compose |
| `WFC_AUTH_TOKEN`, `WFC_REVIEWER_ID` | Legacy static Bearer credentials; **not used in Auth0 mode** | Legacy-only operator configuration |
| `WFC_CONTROLLER_INSTALLATION_ID` | Read-only Controller **GitHub App installation ID**, not App ID | `controller-webhook.env` |
| `WFC_WEBHOOK_DB_PATH`, `WFC_WEBHOOK_SECRET_FILE`, `WFC_WEBHOOK_PORT` | Webhook receiver database, HMAC secret path, listening port | Compose, not operator env file |
| `WFC_DB_PATH`, `WFC_MCP_HOST`, `WFC_PUBLIC_HOST` | MCP database path, bind host, externally allowed host | Compose for the Controller stack |
| `WFC_CI_GATE_REQUIRED` | Explicit gate requiring trusted exact-HEAD CI for reviewer workflow | All relevant trusted processes |
| `NGROK_AUTHTOKEN` | ngrok's own named authtoken variable (not a WFC credential) | Operator, `ngrok.env` |
| `NGROK_DOMAIN` | Optional Compose hostname override (external to service env files) | Shell / Compose project `.env` |

`controller-ci.json` uses **JSON field names** such as `database`,
`controller_github_app.app_id`, `installation_id` and `pem`. These are
not environment variables and therefore do not use `WFC_`.

## Safe setup and migration

1. Choose the active `compose.controller.yaml` stack; do not mix in `mcp.env` from the older POC stack.
2. Copy missing templates only. **Never overwrite a working ignored local env file** when pulling from Git.
3. For the Controller MCP, restore the existing verified Auth0 values in `controller-mcp.env`; **do not** change to static Bearer merely because the starter file was blank.
4. Keep the Controller App's **PEM** and **webhook secret** under `.secret/`, distinct from the Worker App.
5. Check `docker compose -f compose.controller.yaml config --quiet`, then restart only services whose configuration was intentionally changed.
6. To check auth, test `/mcp` with a real OAuth request and no credentials (expected 401 in Auth0 mode); never rely on a GET 400/406 alone.
