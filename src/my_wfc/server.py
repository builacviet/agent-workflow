import os
import re
from datetime import datetime, timezone

from my_wfc.security import poc_unauthenticated_enabled

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

def server_bind_host() -> str:
    """Keep local source execution loopback-only unless explicitly containerized."""
    host = os.environ.get("WFC_MCP_HOST", "127.0.0.1")
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("WFC_MCP_HOST must be 127.0.0.1 or 0.0.0.0")
    return host


def server_allowed_hosts() -> list[str]:
    """Explicit Host allowlist; never disable DNS-rebinding protection."""
    hosts = ["127.0.0.1:*", "localhost:*"]
    if server_bind_host() == "0.0.0.0":
        # Internal Docker Compose service target for the ngrok agent.
        hosts.append("mcp:*")
    public_host = os.environ.get("WFC_PUBLIC_HOST", "").strip().lower()
    if public_host:
        labels = public_host.split(".")
        if (
            len(public_host) > 253
            or len(labels) < 2
            or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            )
        ):
            raise ValueError("WFC_PUBLIC_HOST must be a plain DNS hostname without scheme or port")
        hosts.extend([public_host, public_host + ":*"])
    return hosts

mcp = FastMCP(
    "Workflow MCP Lab",
    host=server_bind_host(),
    port=8000,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=server_allowed_hosts(),
    ),
)

@mcp.tool()
def ping() -> str:
    """Check whether ChatGPT can call this MCP server."""
    return "pong"

@mcp.tool()
def get_server_info() -> dict:
    """Return basic, non-sensitive information about this server."""
    return {
        "name": "Workflow MCP Lab",
        "version": "0.2.0",
        "status": "running",
        "mode": ("POC-UNAUTHENTICATED-UNSAFE" if poc_unauthenticated_enabled()
                 else "workflow-writes-denied-unless-authenticated"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

from my_wfc.tools import register_tools

register_tools(mcp)

if __name__ == "__main__":
    if poc_unauthenticated_enabled():
        print(
            "WARNING: POC NO-AUTH MODE: all seven workflow tools are callable "
            "without credentials by anyone who can reach this endpoint. "
            "Use ONLY an isolated disposable SQLite DB and stop the tunnel after testing.",
            flush=True,
        )
    mcp.run(transport="streamable-http")