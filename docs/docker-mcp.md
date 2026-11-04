# Containerize MCP + ngrok — isolated Tech Lead POC

Scope: **MCP Server + SQLite Docker volume + optional ngrok tunnel** in `techlead-review-poc`.
There is **no Codex CLI or Workflow Controller** in this change.
No resources from `agent_workflow_v2` / Workflow Console v2 are used or modified.

## Before running

- Requires Docker Engine / Docker Desktop with Compose v2.
- Stop only the previous `python -m my_wfc.server` process bound to local
  port 8000. Do not stop other Docker Compose projects.
- **Stop the ngrok agent running directly on the host before starting the ngrok
  container**. The same reserved public domain cannot be claimed by two agents.
- The existing Windows database and Cloudflare Quick Tunnel are not imported.
  Compose creates its own persistent named volume `techlead-review-poc_mcp_state`.
  Do not use `docker compose down -v` unless intentionally deleting test data.

Prepare only local ignored secret files from the templates:

```powershell
Copy-Item .runtime/mcp.env.example .runtime/mcp.env
Copy-Item .runtime/ngrok.env.example .runtime/ngrok.env
```

Set `NGROK_AUTHTOKEN` in `.runtime/ngrok.env` without posting it in chat or Git.
Set `GH_TOKEN` in `.runtime/mcp.env` to an appropriately scoped **read-only**
GitHub token for the private `builacviet/techlead-review-poc` repo. The existing
`my_wfc.github.GitHubVerifier` invokes `gh api`, so Docker does not inherit
Windows `gh auth login`. Never mount the host's global gh credential store.

The default `WFC_POC_NO_AUTH=0` in the sample protects all seven workflow tools.
`ping` / `get_server_info` are public. If, for a short isolated synthetic test,
you set `WFC_POC_NO_AUTH=1` in the local ignored file, **anyone who reaches the
public ngrok endpoint can invoke all seven APIs**. Do not seed real history or
copy real SQLite data into this volume. Stop the tunnel immediately afterwards.
The path must remain exactly `/state/workflow-poc-unauth.sqlite3` in that mode.

## Docker network address-pool exhaustion

On hosts with many Compose test networks, Docker may fail with
`all predefined address pools have been fully subnetted`. The POC therefore
allocates `10.254.240.0/24` explicitly by default. This does **not** change,
stop, prune or delete any existing Docker network.

Check that the subnet is also free of collisions with local networks, VPN
routes and other non-Docker interfaces before starting:

```powershell
route print -4
docker network ls -q --filter driver=bridge | ForEach-Object {
    docker network inspect $_ --format '{{.Name}}: {{range .IPAM.Config}}{{.Subnet}} {{end}}'
}
```

If a VPN or network uses the proposed range (including a broader containing
prefix such as `10.0.0.0/8`), choose another verified free CIDR and override
Compose's default rather than editing shared Docker Desktop address pools:

```powershell
$env:WFC_DOCKER_SUBNET = "10.254.240.0/24"
docker compose config --quiet
docker compose up -d mcp
```

`WFC_DOCKER_SUBNET` is a Compose interpolation variable in the PowerShell
environment or a local root `.env` file, **not** a variable from
`.runtime/mcp.env`. The proposed default has been checked only against the
network list supplied by the operator, not against the host's actual route
table. Never run `docker network prune`, `docker compose down -v`, or
change Workflow Console v2 networks as a troubleshooting shortcut.

## Build, seed, start

From the repository root:

```powershell
docker compose config --quiet
docker compose build mcp
docker compose up -d mcp
docker compose ps
# Optional: a fresh synthetic fixture. Requires GH_TOKEN read access.
docker compose exec mcp python -m my_wfc.admin seed-synthetic
```

Confirm the local MCP transport and healthcheck first. If the Python source
server still holds port 8000, Compose will fail safely; do not kill an unrelated
process to free the port.

The current fixed ngrok domain for this POC is:

```text
https://caddy-reopen-scotch.ngrok-free.dev/mcp
```

Start the optional public tunnel **only when actively testing**:

```powershell
docker compose --profile tunnel up -d ngrok
docker compose --profile tunnel ps
docker compose logs --tail 50 ngrok
```

ngrok reaches `mcp:8000` on the Compose network and uses the ngrok domain
`caddy-reopen-scotch.ngrok-free.dev`. The server enforces DNS-rebinding Host
validation. `WFC_PUBLIC_HOST` is configured by Compose, not hardcoded in Python.
If changing the ngrok domain in the future, set `NGROK_DOMAIN` in the Compose
interpolation environment and recreate both services. Do not insert `https://`
or `/mcp` into that hostname variable.

ChatGPT plugin MCP URL: `https://caddy-reopen-scotch.ngrok-free.dev/mcp`.
If the old plugin targets a Cloudflare URL, update/recreate its connection.
A stable domain is NOT authentication. OAuth support remains future work.

## Persistence / cleanup

With the same Compose project and volume, `docker compose restart mcp` must
not reset task/review data. Compare `get_review_result()` before and after.
The named volume remains separate from the original host `data/` directory.

```powershell
# Safest shutdown sequence: close public ingress before stopping the service.
docker compose --profile tunnel stop ngrok
docker compose stop mcp
# Optional cleanup, keeping the named SQLite volume:
docker compose --profile tunnel down
```

Do not run `down -v`, `docker volume prune`, migrate the real database, enable
anonymous access for production, or auto-seed historical tasks. Container startup
does not auto-provision, dispatch Codex, or update Notion.

## Verify before merging

1. Run `python -m pytest -q` in a test environment with the `[mcp,test]`
   extras installed; record observed results. Static configuration alone is
   not a passed Docker integration test.
2. `docker compose config --quiet`, build, then verify local `ping()`.
3. Confirm the public ngrok URL reaches MCP and Host validation remains enabled.
4. Seed only `POC-REVIEW-001` after configuring GH read access; do not change
   `TASK-001`. Verify the real private GitHub SHA lookup works in the container.
5. Verify SQLite survives an MCP restart with the same named volume.
6. Stop ngrok and confirm the public endpoint is no longer reachable.

Any failures remain blockers for merging to `main`.
