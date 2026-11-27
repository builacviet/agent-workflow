"""Deterministic tests: remote SHA responses and time are controlled, never invented evidence."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from agent_workflow.application.review.dtos import ReviewQueueItem
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.interfaces.cli.admin import BASE, REPO
from agent_workflow.interfaces.mcp.security import authorize_request
from agent_workflow.workers.trusted_git.github import GitHubVerifier

HEAD = "a" * 40
NEXT = "b" * 40


def spec(task_id="TEST", effort="medium", verified=False):
    return dict(
        task_id=task_id,
        title="SYNTHETIC test",
        description="Unit-test fixture only",
        acceptance_criteria=["AC1", "AC2"],
        definition_of_done="tests pass",
        repository=REPO,
        branch="task/test",
        base_sha=BASE,
        requested_effort=effort,
        risk_level="low",
        require_verified_effort=verified,
        context_paths=[
            "AGENTS.md",
            "docs/architecture.md",
            "docs/handoff.md",
            "docs/tasks/TEST.md",
        ],
        synthetic=True,
    )


def finding(fid="F-1"):
    return dict(
        finding_id=fid,
        severity="medium",
        acceptance_criterion="AC1",
        file_path="src/file.py",
        line=1,
        description="test finding",
        suggested_correction="test fix",
        required_verification="test regression",
    )


def payload(claim, verdict="CHANGES_REQUESTED", **overrides):
    data = dict(
        task_id=claim["task_id"],
        claim_token=claim["claim_token"],
        reviewed_head_sha=claim["reviewed_sha"],
        review_round=claim["review_round"],
        verdict=verdict,
        summary="Synthetic review",
        findings=[finding()] if verdict != "APPROVED" else [],
        prior_resolutions={},
        requested_effort="medium",
    )
    return {**data, **overrides}


def test_ping_and_info_preserved():
    from agent_workflow.interfaces.mcp.server import (
        build_mcp_server,
        get_server_info,
        ping,
    )

    mcp = build_mcp_server()
    assert ping() == "pong"
    assert get_server_info()["name"] == "Workflow MCP Lab"
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.port == 8000


def test_auth_denies_default_missing_and_wrong_token(monkeypatch):
    monkeypatch.delenv("WFC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("WFC_REVIEWER_ID", raising=False)

    def request(value):
        return Request(
            {"type": "http", "headers": [(b"authorization", value.encode())]}
        )

    with pytest.raises(PermissionError):
        authorize_request(request("Bearer x"))
    monkeypatch.setenv("WFC_AUTH_TOKEN", "a" * 32)
    monkeypatch.setenv("WFC_REVIEWER_ID", "test-reviewer")
    for header in ("", "Bearer wrong"):
        with pytest.raises(PermissionError):
            authorize_request(request(header))
    assert authorize_request(request("Bearer " + "a" * 32)) == "test-reviewer"


def test_github_verifier_fail_closed_and_escapes_branch(monkeypatch):
    import subprocess

    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            stdout='{"commit":{"sha":"' + HEAD + '"}}'
            if "branches" in args[-1]
            else '{"status":"ahead"}'
        )

    monkeypatch.setattr(subprocess, "run", run)
    verify = GitHubVerifier({REPO})
    verify(REPO, "task/test", BASE, HEAD)
    assert "task%2Ftest" in calls[0][-1]
    with pytest.raises(BusinessRuleError):
        verify("unknown/repo", "x", BASE, HEAD)

    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("gh", 15)

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(BusinessRuleError, match="unavailable"):
        verify(REPO, "task/test", BASE, HEAD)


def test_http_mcp_boundary_and_complete_tool_contract(monkeypatch):
    """Real local HTTP/MCP handshake; external GitHub remains mocked."""
    import asyncio
    import socket
    import threading
    import time
    from contextlib import nullcontext

    import httpx
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from agent_workflow.interfaces.mcp.server import build_mcp_server

    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    monkeypatch.setenv("WFC_AUTH_TOKEN", "t" * 40)
    monkeypatch.setenv("WFC_REVIEWER_ID", "http-test-reviewer")
    monkeypatch.setenv("WFC_AUTH_MODE", "bearer")
    monkeypatch.setenv(
        "PG_RUNTIME_DSN", "postgresql+psycopg://user:pass@localhost/test"
    )
    monkeypatch.setattr(
        "agent_workflow.composition.postgres_mcp.postgres_mcp_task_operations",
        lambda config: pytest.fail("unauthorized task request opened PostgreSQL"),
    )

    # Transport contract only: real CI decisions and persistence are covered
    # by the current review use-case and PostgreSQL tests.
    from unittest.mock import Mock

    from agent_workflow.application.review.use_cases.write_contracts import (
        ClaimReviewInput,
        ClaimReviewOutput,
        ReviewSubmission,
        SubmitReviewInput,
        SubmitReviewOutput,
    )
    from agent_workflow.domain.states import WorkflowStatus

    claim_operation = Mock()
    claim_operation.execute.return_value = ClaimReviewOutput(
        "token", "TEST", HEAD, 1, 1000.0
    )
    submit_operation = Mock()
    submit_operation.execute.return_value = SubmitReviewOutput(
        "TEST", "APPROVED", HEAD, review_id="review", review_round=1
    )
    provider = Mock(
        side_effect=lambda config: nullcontext((claim_operation, submit_operation))
    )
    monkeypatch.setattr(
        "agent_workflow.composition.postgres_mcp.postgres_mcp_review_writes",
        provider,
    )

    reads_provider = Mock(
        side_effect=AssertionError("invalid review read opened PostgreSQL")
    )
    monkeypatch.setattr(
        "agent_workflow.composition.postgres_mcp.postgres_mcp_review_reads",
        reads_provider,
    )

    def list_queue(requested_effort):
        assert requested_effort == "medium"
        return (
            ReviewQueueItem(
                "TEST", "SYNTHETIC test", REPO, "task/test", HEAD, "medium", 1
            ),
        )

    monkeypatch.setattr(
        "agent_workflow.composition.postgres_mcp.postgres_mcp_review_queue",
        lambda config: nullcontext(SimpleNamespace(execute=list_queue)),
    )
    server_mcp = build_mcp_server()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(server_mcp.streamable_http_app(), log_level="critical")
    )
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": [sock]}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                pytest.fail("local HTTP server did not start")
            time.sleep(0.01)

        async def exercise():
            url = f"http://127.0.0.1:{port}/mcp"
            async with streamable_http_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool("ping", {})
                    assert not result.isError and result.content[0].text == "pong"
                    info = await session.call_tool("get_server_info", {})
                    assert not info.isError
                    mutation_args = {
                        "claim_review": {"task_id": "TEST", "expected_head_sha": HEAD},
                        "register_task_handoff": dict(
                            task_id="TEST",
                            repository=REPO,
                            branch="task/test",
                            base_sha=BASE,
                            new_head_sha=HEAD,
                            handoff_file_path="docs/handoff.md",
                            test_summary="x",
                        ),
                        "submit_review": {
                            "submission": payload(
                                {
                                    "task_id": "TEST",
                                    "claim_token": "unknown",
                                    "reviewed_sha": HEAD,
                                    "review_round": 1,
                                }
                            )
                        },
                    }
                    for name, args in mutation_args.items():
                        denied = await session.call_tool(name, args)
                        assert denied.isError
                    provider.assert_not_called()
                    claim_operation.execute.assert_not_called()
                    submit_operation.execute.assert_not_called()
            async with (
                httpx.AsyncClient(
                    headers={"Authorization": "Bearer " + "t" * 40}
                ) as http_client,
                streamable_http_client(url, http_client=http_client) as (
                    read,
                    write,
                    _,
                ),
            ):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert len(tools.tools) == 9
                    assert {tool.name for tool in tools.tools} == {
                        "ping",
                        "get_server_info",
                        "list_review_queue",
                        "get_review_context",
                        "claim_review",
                        "submit_review",
                        "get_review_result",
                        "register_task_handoff",
                        "get_coder_assignment",
                    }
                    for name in ("get_review_context", "get_review_result"):
                        invalid = await session.call_tool(name, {"task_id": " \t"})
                        assert invalid.isError
                        assert invalid.content[0].text == (
                            f"Error executing tool {name}: unknown authorized task"
                        )
                    reads_provider.assert_not_called()
                    invalid = await session.call_tool(
                        "submit_review",
                        {
                            "submission": {
                                **mutation_args["submit_review"]["submission"],
                                "claim_token": " ",
                            }
                        },
                    )
                    assert invalid.isError
                    assert invalid.content[0].text == (
                        "Error executing tool submit_review: "
                        "unknown claim or claim owner mismatch"
                    )
                    provider.assert_not_called()
                    submit_operation.execute.assert_not_called()
                    queue = await session.call_tool(
                        "list_review_queue", {"requested_effort": "medium"}
                    )
                    assert not queue.isError
                    claimed = await session.call_tool(
                        "claim_review", {"task_id": "TEST", "expected_head_sha": HEAD}
                    )
                    assert not claimed.isError
                    claim = __import__("json").loads(claimed.content[0].text)
                    # Generic dict returns use the SDK text JSON representation.
                    data = payload(claim, "APPROVED")
                    submitted = await session.call_tool(
                        "submit_review", {"submission": data}
                    )
                    assert not submitted.isError
                    assert __import__("json").loads(submitted.content[0].text) == {
                        "task_id": "TEST",
                        "review_id": "review",
                        "verdict": "APPROVED",
                        "review_round": 1,
                        "reviewed_head_sha": HEAD,
                        "actual_effort": None,
                    }
                    claim_operation.execute.assert_called_once_with(
                        ClaimReviewInput("TEST", HEAD, "http-test-reviewer")
                    )
                    submit_operation.execute.assert_called_once_with(
                        SubmitReviewInput(
                            ReviewSubmission(
                                task_id="TEST",
                                claim_token="token",
                                reviewed_head_sha=HEAD,
                                review_round=1,
                                verdict=WorkflowStatus.APPROVED,
                                summary="Synthetic review",
                                findings=(),
                                prior_resolutions={},
                                requested_effort="medium",
                            ),
                            "http-test-reviewer",
                        )
                    )
                    assert provider.call_count == 2

        asyncio.run(exercise())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive()


def test_server_docker_bind_and_public_hostname_are_explicit(monkeypatch):
    from agent_workflow.interfaces.mcp.server import (
        server_allowed_hosts,
        server_bind_host,
    )

    monkeypatch.delenv("WFC_MCP_HOST", raising=False)
    monkeypatch.delenv("WFC_PUBLIC_HOST", raising=False)
    assert server_bind_host() == "127.0.0.1"
    assert server_allowed_hosts() == ["127.0.0.1:*", "localhost:*"]

    monkeypatch.setenv("WFC_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("WFC_PUBLIC_HOST", "caddy-reopen-scotch.ngrok-free.dev")
    hosts = server_allowed_hosts()
    assert "mcp:*" in hosts
    assert "caddy-reopen-scotch.ngrok-free.dev" in hosts
    assert "caddy-reopen-scotch.ngrok-free.dev:*" in hosts

    monkeypatch.setenv("WFC_PUBLIC_HOST", "https://bad.example.com/mcp")
    with pytest.raises(ValueError, match="plain DNS hostname"):
        server_allowed_hosts()
    monkeypatch.setenv("WFC_PUBLIC_HOST", "bad..example.com")
    with pytest.raises(ValueError, match="plain DNS hostname"):
        server_allowed_hosts()
    monkeypatch.setenv("WFC_PUBLIC_HOST", "caddy-reopen-scotch.ngrok-free.dev")
    monkeypatch.setenv("WFC_MCP_HOST", "localhost")
    with pytest.raises(ValueError, match="WFC_MCP_HOST"):
        server_bind_host()
