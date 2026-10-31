import os
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

CLOUDFLARE_HOST = "cut-possess-updating-appraisal.trycloudflare.com"

mcp = FastMCP(
    "Workflow MCP Lab",
    host="127.0.0.1",
    port=8000,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[
            "127.0.0.1:*",
            "localhost:*",
            CLOUDFLARE_HOST,
        ],
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
        "mode": "workflow-writes-denied-unless-authenticated",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

from my_wfc.tools import register_tools

register_tools(mcp)

if __name__ == "__main__":
    mcp.run(transport="streamable-http")