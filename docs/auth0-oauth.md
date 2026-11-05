# Auth0 OAuth for the isolated WFC MCP — operator runbook (POC)

This is a **separate, opt-in resource-server mode** stacked on PR #5. It does
not issue tokens, operate Codex, change workflow database schema, merge PRs,
or touch Workflow Console v2. Keep the existing deployed MCP/ngrok untouched
until local tests and an explicit cutover plan pass. No production readiness
or live ChatGPT login is claimed by this code commit.

## Auth0 console setup

- Use a **separate POC tenant**, not one shared with unrelated applications.
- Enable **Client ID Metadata Document (CIMD) Registration**; DCR may remain
  disabled. Import the ChatGPT CIMD metadata and verify the application has
  the **Third-party / CIMD** label.
- Turn on **Resource Parameter Compatibility Profile** (RFC 8707 resource).
- Custom API identifier (audience) must exactly equal:
  `https://caddy-reopen-scotch.ngrok-free.dev/mcp` (or the newly authorized
  public MCP resource URL). Signing algorithm: RS256.
- Add API permission `wfc:workflow`. Under API Application Access, grant
  **only** User-delegated Access / `wfc:workflow` to the ChatGPT CIMD
  application. Grant neither Client Access nor Anonymous Access.
- Promote `Username-Password-Authentication` to domain-level for the
  third-party client. Consider disabling public sign-up for this test tenant.
- Create one test user; copy its exact **User ID** (`auth0|...`) locally.
  Record the exact ChatGPT **CIMD Client ID**, shown in its Auth0 Application
  page. Do not put credentials, user password, secret or tokens in Git/chat.

## Runtime settings

Use a local ignored env file, e.g. copy
`.runtime/controller-mcp.env.example` to `.runtime/controller-mcp.env`.
The standalone `compose.controller.yaml` uses this ignored file. This is for an **isolated test deployment**, not a direction to
overwrite an already-running Compose service.

- `WFC_AUTH_MODE=auth0`: enable OAuth mode. Omitted or `bearer` keeps
  preexisting static Bearer behavior.
- `WFC_AUTH0_ISSUER=https://YOUR-TENANT.us.auth0.com/`: HTTPS Auth0
  issuer with trailing slash; do not substitute the ngrok domain.
- `WFC_AUTH0_ALLOWED_SUB=auth0|...`: exact single allowed user subject.
- `WFC_AUTH0_CLIENT_ID=...`: exact ChatGPT CIMD application client ID
  (not the unrelated precreated TechLead WFC MCP application).
- `WFC_AUTH0_SCOPE=wfc:workflow`: one required permission.
- `WFC_PUBLIC_HOST=caddy-reopen-scotch.ngrok-free.dev`: DNS host only.
  The externally accessible resource URL is `https://HOST/mcp`.
- `WFC_POC_NO_AUTH=0`: **must not** be public bypass mode.
- `WFC_DB_PATH`: explicit absolute path for the isolated operator-owned
  test SQLite. If eventually testing the full Controller workflow, both MCP
  and Controller must use **the same physical database**, not copied files.

The token verifier retrieves Auth0's JWKS over HTTPS and accepts only RS256
signatures matching issuer, audience, expiry/issued-at, exact subject, exact
CIMD client ID, and required scope. Issuer and JWKS source are operator
configured; no secret/token is logged. A JWKS outage or unknown signing key
fails closed. Signing-key changes can take up to the five-minute JWKS cache
lifetime to be accepted.

**Important transport behavior:** With SDK OAuth enabled, the HTTP middleware
requires a bearer token at the **entire /mcp endpoint**, including
`ping` and `get_server_info`. Protected-resource metadata remains public.
The older public `ping` behavior applies to Bearer mode, not OAuth mode.
Never bypass the SDK middleware merely to expose these tools anonymously.

## Tests before deployment

From the isolated OAuth Git worktree:

```powershell
python -m pip install -e ".[mcp,test]"
python -m pytest -q tests/test_auth0_oauth.py
python -m pytest -q
docker build -f docker/mcp.Dockerfile -t my-wfc-mcp:auth0-poc .
```

These tests use synthetic RSA keys; **no live Auth0 or ChatGPT login**.
The implementation author has not independently run these commands at
publication time unless documented separately with actual output.

Expected OAuth HTTP smoke after deployment to a **non-production test
endpoint**: unauthenticated `POST /mcp` returns 401 with
`WWW-Authenticate` pointing to the protected-resource metadata; metadata
reports the exact resource URL and Auth0 issuer; ChatGPT OAuth login yields a
token whose `sub`, `aud`, `azp`, and `scope` match the configured
constraints; `list_review_queue` returns only authorized test state.
Wrong/expired tokens, wrong user/client/scope, and JWKS failure must deny.

Only then authorize one separate synthetic Controller/Codex E2E dispatch.
No automatic retry, merge, approval, Notion updates or v2 operations.

## Explicit limitations

- No live Auth0 login, ChatGPT OAuth plugin connection or Docker smoke has
  been verified by this change alone.
- This is single-user **allowlisted POC** authentication, not a generalized
  user/role administration system. Existing review policy and verified-effort
  gating still apply.
- No claim that the Config/Auth0 features remain free after any account
  trial without validating Auth0's current plan and tenant state.
