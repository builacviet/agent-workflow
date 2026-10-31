"""Local live MCP smoke test with real seeded DB, two server lifetimes and no verdicts."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).parent.parent


async def check(url, token):
    async with streamable_http_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            pong = await session.call_tool("ping", {})
            assert not pong.isError and pong.content[0].text == "pong"
            denied = await session.call_tool("claim_review", {"task_id": "POC-REVIEW-001", "expected_head_sha": "015ccc535b85ce1d0ad00da787b827970b9bf39a"})
            assert denied.isError
    async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}) as client:
        async with streamable_http_client(url, http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                def decode(result):
                    assert not result.isError
                    value = json.loads(result.content[0].text)
                    return value.get("result", value) if isinstance(value, dict) else value
                history = decode(await session.call_tool("get_review_context", {"task_id": "TASK-001"}))
                assert history["status"] == "ESCALATED" and len(history["previous_reviews"]) == 2
                assert history["previous_reviews"][1]["prior_resolutions"] == {"F-001": "FIXED"}
                queue = decode(await session.call_tool("list_review_queue", {}))["tasks"]
                assert all(t["task_id"] != "TASK-001" for t in queue)
                assert any(t["task_id"] == "POC-REVIEW-001" for t in queue)
                return {"historical_status": history["status"], "completed_rounds": history["completed_review_rounds"],
                        "queue_task_ids": [t["task_id"] for t in queue], "unauthenticated_claim": "denied", "ping": "pong"}


def main():
    results = []
    for _ in range(2):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        token = secrets.token_urlsafe(32)
        env = {**os.environ, "WFC_AUTH_TOKEN": token, "WFC_REVIEWER_ID": "local-smoke-test"}
        process = subprocess.Popen([sys.executable, "-c", f"from my_wfc import server; server.mcp.settings.port={port}; server.mcp.run(transport='streamable-http')"],
                                   cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 15
            while True:
                if process.poll() is not None:
                    raise RuntimeError("smoke server exited before listening")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("smoke server startup timed out")
                    time.sleep(0.05)
            results.append(asyncio.run(check(f"http://127.0.0.1:{port}/mcp", token)))
        finally:
            process.terminate()
            process.wait(timeout=10)
    assert results[0] == results[1]
    print(json.dumps({"server_lifetimes": 2, "persisted_state_matches": True, **results[1]}, indent=2))


if __name__ == "__main__":
    main()
