# Auth0 OAuth for the Phase 3.1 ChatGPT MCP (POC)

Scope: an **opt-in** Auth0-backed MCP resource server. No Auth0 client secret,
GitHub App PEM, ChatGPT device-login credential or live OAuth token belongs in
Git. This branch is stacked on PR #5; neither the existing Docker MCP
deployment nor Workflow Console v2 is changed by committing these files.

## Security/transport architecture

- `WFC_AUTH_MODE=bearer` (default): legacy static bearer check on seven
  workflow tools; `ping()` and `get_server_info()` remain public.
- `WFC_AUTH_MODE=auth0`: MCP SDK 1.30.0 protects **all** HTTP MCP requests,
  including `ping()` and `get_server_info()`. The SDK publishes
  RFC 9728 metadata at
  `/.well-known/oauth-protected-resource/mcp` and challenges anonymous
  callers with HTTP 401 + `WWW-Authenticate`. No login/consent/refresh logic
  runs inside the MCP server. Auth0 is the OAuth authorization server.
- Access tokens must be **RS256 JWTs** signed by an Auth0 JWKS key with
  matching `iss`, public MCP URL in `aud`, `exp`, `iat`, an exact
  `WFC_AUTH0_ALLOWED_SUB`, and the `wfc:workflow` permission in `scope`.
  JWKS HTTPS reads are bounded, key lists limited, keys cached five minutes.
  Wrong/missing credentials fail closed.
- This is a **single-authorized-user POC**, not multi-user RBAC. The OAuth
  actor is `auth0:<sub>`. Every valid token from this one Auth0 subject can
  call the seven workflow tools. A valid Auth0 user identity does not prove
  that ChatGPT (rather than another OAuth client) originated the request.
- No OAuth setting overrides two completed review rounds, trusted effort
  requirements, SQLite authorization, no-auto-merge, or exact SHA review.
- `WFC_POC_NO_AUTH=1` is explicitly incompatible with OAuth.
- The legacy HTTP endpoint remains unaffected until the operator launches the
  separate Phase 3.1 Compose stack and deliberately switches the public tunnel.

## 1. Configure Auth0 (operator actions, dedicated free POC tenant)

1. Create a separate Auth0 tenant for this POC. Under **Applications → APIs**,
   create a custom API. Set **Identifier** (audience) to exactly:

   `https://caddy-reopen-scotch.ngrok-free.dev/mcp`

   Use RS256 signing. Add API permission `wfc:workflow`. If RBAC is enabled,
   give the test user permission through an appropriate role; ensure the
   resulting access token actually includes the scope.
2. In **Settings → Advanced**, enable **Dynamic Client Registration** (DCR)
   and **Resource Parameter Compatibility Profile**. The latter is required
   for Auth0 to interpret ChatGPT's RFC 8707 `resource` parameter as this
   audience. Configure the tenant's **Default Audience** to the custom API
   identifier if needed to avoid opaque/userinfo tokens.
3. **Warning:** Auth0's open DCR can permit anyone to register OAuth clients
   in that tenant. Use a *dedicated POC tenant*, monitor applications, and
   disable DCR after connector setup if re-registration is no longer needed.
   Do not enable open DCR in a production/shared tenant casually.
4. Under **User Management → Users**, find the test user's immutable Auth0
   **User ID**, for example `auth0|...` or `google-oauth2|...`.
   This is `WFC_AUTH0_ALLOWED_SUB`; it is an allowlist, not a secret.
5. Keep the tenant domain/issuer (for example
   `https://example.us.auth0.com/`). Ensure its OAuth metadata advertises
   an authorization code flow, PKCE S256 and a registration endpoint for DCR.
   ChatGPT must be able to log in and request `wfc:workflow`.

If Auth0's login fails, verify metadata, DCR and the Resource Parameter
Compatibility Profile **before changing MCP code**. Check that the resulting
access token is a signed JWT for the exact public MCP URL, not a userinfo
token. Never paste access tokens, client secrets or cookies into chat.

## 2. Local config, build and start (Phase 3.1 worktree only)

Do not overwrite an existing configuration or point to any Workflow Console
v2 data/worker. The Controller and this MCP use the same physical file at
`./data/controller/workflow.sqlite3`; its host directory must already exist
and be writable by container UID 10001.

```powershell
# In dedicated techlead-review-poc-phase31 worktree, after pulling this branch:
Copy-Item .runtime/controller-mcp.env.example .runtime/controller-mcp.env
# Make a separate ignored .runtime/ngrok.env containing only your NGROK_AUTHTOKEN.
# If already present, do not overwrite.
```

Edit **ignored** `.runtime/controller-mcp.env` (values below are *examples*):

```dotenv
WFC_AUTH_MODE=auth0
WFC_POC_NO_AUTH=0
WFC_AUTH0_ISSUER=https://YOUR-TENANT.us.auth0.com/
WFC_AUTH0_ALLOWED_SUB=auth0|YOUR-USER-ID
WFC_AUTH0_SCOPE=wfc:workflow
WFC_PUBLIC_HOST=caddy-reopen-scotch.ngrok-free.dev
WFC_AUTH_TOKEN=
WFC_REVIEWER_ID=
```

Then:

```powershell
docker compose -f compose.controller.yaml config --quiet
docker compose -f compose.controller.yaml build mcp
docker compose -f compose.controller.yaml up -d mcp
docker compose -f compose.controller.yaml ps
```

This Compose project is `my-wfc-phase31`, separate from the original
`techlead-review-poc` project. Its MCP binds to
`127.0.0.1:18001`. The Controller is **not** started by this command.
The image receives no GitHub App private key, GitHub token or Codex auth volume.

**Reserved ngrok URL:** only one ngrok agent can own it. First stop the old
`techlead-review-poc` ngrok service from the old worktree:

```powershell
docker compose --profile tunnel stop ngrok
```

Now from the Phase 3.1 worktree:

```powershell
docker compose -f compose.controller.yaml --profile tunnel up -d ngrok
docker compose -f compose.controller.yaml --profile tunnel ps
```

Run OAuth discovery smoke (no model use):

```powershell
Invoke-RestMethod https://caddy-reopen-scotch.ngrok-free.dev/.well-known/oauth-protected-resource/mcp
```

Expected `resource` is the exact public `/mcp` URL and
`authorization_servers` contains the Auth0 issuer. POST to `/mcp`
without credentials must return HTTP **401** and a
`WWW-Authenticate` challenge; a JWT with wrong issuer, audience, subject,
permission or expired signature must also be denied.

## 3. ChatGPT Plugin / E2E gate

In ChatGPT create a **new custom MCP plugin** using:
`https://caddy-reopen-scotch.ngrok-free.dev/mcp`,
authentication **OAuth**, DCR (not `No authentication`). Follow Auth0
Universal Login. Do not remove the working `my-wfc-docker-test` plugin.
Only after the OAuth plugin can call `list_review_queue()` through an
authenticated session should the operator proceed to one synthetic
Controller `--once` job and Tech Lead exact-SHA handoff review.

The one-job Controller runner, GitHub App and Codex authentication were added
in PR #5. The OAuth branch must not start the Controller, submit a verdict,
auto-merge, update Notion or operate Workflow Console v2.

## 4. Recovery

Stop **only the Phase 3.1 tunnel** and MCP if the test fails:

```powershell
docker compose -f compose.controller.yaml --profile tunnel stop ngrok
docker compose -f compose.controller.yaml stop mcp
```

Optionally restart the old project's tunnel from its original worktree:
`docker compose --profile tunnel up -d ngrok`. This does not delete
SQLite data. Never run `down -v` or `docker volume prune` as cleanup.

## Status / unverified gates

- Isolated deterministic JWT tests use synthetic RSA signing keys.
- Auth0 tenant, dynamic registration, user consent, token issuance, public
  discovery and ChatGPT OAuth login require operator credentials/setup and
  have **not** been executed merely by merging/creating this branch.
- Full MCP SDK Docker tests, actual model request and bot draft PR remain
  operator integration gates; no recorded review rounds/approvals are implied.
