"""Check authorization and PostgreSQL scope ownership at MCP tool dispatch."""

import asyncio
import json
from unittest.mock import Mock

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError
from test_my_wfc import BASE, HEAD, REPO, payload

from agent_workflow.domain.service import WorkflowService
from agent_workflow.mcp.server import mcp
from agent_workflow.storage.postgres_connection import PostgresFactory

DSN = "postgresql+psycopg://user:pass@localhost/test"
SUBJECT = "auth0|workflow-test"
ACTOR = f"auth0:{SUBJECT}"
TASK = {"task_id": "TEST"}
HANDOFF = {
    **TASK,
    "repository": REPO,
    "branch": "task/test",
    "base_sha": BASE,
    "new_head_sha": HEAD,
    "handoff_file_path": "docs/handoff.md",
    "test_summary": "test evidence",
}
SUBMISSION = payload(
    {**TASK, "claim_token": "test-token", "reviewed_sha": HEAD, "review_round": 1},
    "APPROVED",
    actual_effort=None,
    verification_evidence=None,
)
RECORD = {"task_id": "TEST"}


@pytest.fixture
def authorized_workflow(monkeypatch):
    monkeypatch.setenv("WFC_AUTH_MODE", "auth0")
    monkeypatch.setenv("WFC_AUTH0_ALLOWED_SUB", SUBJECT)
    monkeypatch.setenv("WFC_AUTH0_SCOPE", "wfc:workflow")
    monkeypatch.delenv("WFC_POC_NO_AUTH", raising=False)
    monkeypatch.setenv("PG_RUNTIME_DSN", DSN)
    token = AccessToken(
        token="synthetic",
        client_id="test",
        scopes=["wfc:workflow"],
        subject=SUBJECT,
    )
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


@pytest.mark.parametrize(
    ("name", "arguments", "service_args", "service_result", "tool_result"),
    [
        (
            "list_review_queue",
            {"requested_effort": "medium"},
            ("medium",),
            [RECORD],
            {"tasks": [RECORD]},
        ),
        ("get_review_context", TASK, ("TEST",), RECORD, RECORD),
        (
            "claim_review",
            {**TASK, "expected_head_sha": HEAD},
            ("TEST", HEAD, ACTOR),
            RECORD,
            RECORD,
        ),
        (
            "submit_review",
            {"submission": SUBMISSION},
            (SUBMISSION, ACTOR),
            RECORD,
            RECORD,
        ),
        ("get_review_result", TASK, ("TEST",), RECORD, RECORD),
        (
            "register_task_handoff",
            HANDOFF,
            ("TEST", REPO, "task/test", BASE, HEAD, "docs/handoff.md", "test evidence"),
            RECORD,
            RECORD,
        ),
        ("get_coder_assignment", TASK, ("TEST",), RECORD, RECORD),
    ],
)
def test_workflow_paths_authorize_and_close(
    monkeypatch,
    authorized_workflow,
    name,
    arguments,
    service_args,
    service_result,
    tool_result,
):
    factory = Mock()
    open_factory = Mock(return_value=factory)
    monkeypatch.setattr(PostgresFactory, "open", open_factory)
    operation = Mock(return_value=service_result)

    def run(service, *args):
        assert service.repository.factory is factory
        factory.close.assert_not_called()
        return operation(*args)

    monkeypatch.setattr(WorkflowService, name, run)
    reset = auth_context_var.set(None)
    try:
        with pytest.raises(ToolError, match="workflow OAuth authorization required"):
            asyncio.run(mcp.call_tool(name, arguments))
        open_factory.assert_not_called()
        operation.assert_not_called()
    finally:
        auth_context_var.reset(reset)

    output = asyncio.run(mcp.call_tool(name, arguments))
    assert json.loads(output[0].text) == tool_result
    open_factory.assert_called_once()
    assert open_factory.call_args.args[0].pg_runtime_dsn == DSN
    operation.assert_called_once_with(*service_args)
    factory.close.assert_called_once_with()


def test_exception_and_next_call_use_separate_scopes(monkeypatch, authorized_workflow):
    failed, successful = Mock(), Mock()
    open_factory = Mock(side_effect=[failed, successful])
    monkeypatch.setattr(PostgresFactory, "open", open_factory)

    def get_context(service, task_id):
        if service.repository.factory is failed:
            raise RuntimeError("synthetic operation failed")
        failed.close.assert_called_once_with()
        assert service.repository.factory is successful
        return {"task_id": task_id}

    monkeypatch.setattr(WorkflowService, "get_review_context", get_context)
    with pytest.raises(ToolError, match="synthetic operation failed"):
        asyncio.run(mcp.call_tool("get_review_context", TASK))
    failed.close.assert_called_once_with()
    successful.close.assert_not_called()

    output = asyncio.run(mcp.call_tool("get_review_context", TASK))
    assert json.loads(output[0].text) == RECORD
    assert open_factory.call_count == 2
    successful.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("dsn", "message"),
    [(None, "PG_RUNTIME_DSN is required"), ("secret-canary", "PG_RUNTIME_DSN must be")],
)
def test_dsn_errors_fail_closed(monkeypatch, authorized_workflow, dsn, message):
    if dsn is None:
        monkeypatch.delenv("PG_RUNTIME_DSN")
    else:
        monkeypatch.setenv("PG_RUNTIME_DSN", dsn)
    open_factory = Mock()
    monkeypatch.setattr(PostgresFactory, "open", open_factory)

    with pytest.raises(ToolError, match=message) as error:
        asyncio.run(mcp.call_tool("get_review_context", TASK))
    assert "secret-canary" not in str(error.value)
    open_factory.assert_not_called()


def test_poc_no_auth_cannot_open_postgres(monkeypatch, tmp_path):
    monkeypatch.setenv("WFC_AUTH_MODE", "bearer")
    monkeypatch.setenv("WFC_POC_NO_AUTH", "1")
    monkeypatch.setenv("WFC_DB_PATH", str(tmp_path / "workflow-poc-unauth.sqlite3"))
    monkeypatch.setenv("PG_RUNTIME_DSN", DSN)
    open_factory = Mock()
    monkeypatch.setattr(PostgresFactory, "open", open_factory)

    with pytest.raises(
        ToolError, match="PostgreSQL workflow tools require authenticated"
    ):
        asyncio.run(mcp.call_tool("get_review_context", TASK))
    open_factory.assert_not_called()
