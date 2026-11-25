"""Check authorization and PostgreSQL scope ownership at MCP tool dispatch."""

import asyncio
import json
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy.orm import Session
from test_agent_workflow import BASE, HEAD, REPO, finding, payload, spec

from agent_workflow.application.review.dtos import ReviewQueueItem, ReviewTask
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
    GetReviewContextOutput,
    GetReviewContextUseCase,
)
from agent_workflow.application.review.use_cases.get_review_result import (
    GetReviewResultOutput,
    GetReviewResultUseCase,
)
from agent_workflow.application.review.use_cases.list_review_queue import (
    ListReviewQueueUseCase,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.infrastructure.database.session_factory import PostgresFactory
from agent_workflow.mcp.server import mcp


def test_server_starts_without_postgres_extra():
    script = """
import asyncio
import os
import sys

os.environ['WFC_AUTH_MODE'] = 'bearer'
sys.modules["sqlalchemy"] = None
from agent_workflow.mcp.server import mcp

tools = asyncio.run(mcp.list_tools())
assert {"ping", "get_server_info", "list_review_queue"} <= {tool.name for tool in tools}
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1] / "src",
        check=True,
    )


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
    "name,arguments",
    [("register_task_handoff", HANDOFF), ("get_coder_assignment", TASK)],
)
def test_workflow_paths_authorize_and_close(
    monkeypatch, authorized_workflow, name, arguments
):
    from agent_workflow.application.task.use_cases.get_coder_assignment import (
        CoderAssignmentOutput,
        GetCoderAssignmentUseCase,
    )
    from agent_workflow.application.task.use_cases.register_task_handoff import (
        HandoffResult,
        RegisterTaskHandoffUseCase,
    )
    from agent_workflow.domain.task.handoff import HandoffRequest

    factory = Mock()
    open_factory = Mock(return_value=factory)
    monkeypatch.setattr(PostgresFactory, "open", open_factory)
    if name == "register_task_handoff":
        factory.get_scoped_session.return_value = nullcontext(Session())
        operation = Mock(
            return_value=HandoffResult("TEST", WorkflowStatus.READY_IN_REVIEW, HEAD)
        )
        monkeypatch.setattr(RegisterTaskHandoffUseCase, "execute", operation)
        expected = {"task_id": "TEST", "status": "READY_IN_REVIEW", "head_sha": HEAD}
    else:
        operation = Mock(return_value=CoderAssignmentOutput(CONTEXT, ()))
        monkeypatch.setattr(GetCoderAssignmentUseCase, "execute", operation)
        expected = {**CONTEXT_WIRE, "unresolved_findings": []}
    reset = auth_context_var.set(None)
    try:
        with pytest.raises(ToolError, match="workflow OAuth authorization required"):
            asyncio.run(mcp.call_tool(name, arguments))
        open_factory.assert_not_called()
        operation.assert_not_called()
    finally:
        auth_context_var.reset(reset)
    output = asyncio.run(mcp.call_tool(name, arguments))
    assert json.loads(output[0].text) == expected
    open_factory.assert_called_once()
    factory.close.assert_called_once_with()
    if name == "register_task_handoff":
        factory.get_scoped_session.assert_called_once_with()
        operation.assert_called_once_with(HandoffRequest(**HANDOFF))
    else:
        factory.get_scoped_session.assert_not_called()
        operation.assert_called_once_with("TEST")


SPEC = spec()
CONTEXT = GetReviewContextOutput(
    task=ReviewTask(
        task_id="TEST",
        repository=REPO,
        branch="task/test",
        base_sha=BASE,
        current_head_sha=None,
        requested_effort="medium",
        require_verified_effort=0,
        max_review_rounds=2,
        completed_review_rounds=0,
        status=WorkflowStatus.AWAITING_HANDOFF,
        created_at=1000.0,
        updated_at=1000.0,
        context_paths=tuple(SPEC["context_paths"]),
        spec_json=json.dumps(SPEC),
    ),
    review_round=1,
    rounds_remaining=2,
    previous_reviewed_sha=None,
    previous_verdict=None,
    previous_reviews=(),
    events=(),
)
CONTEXT_WIRE = {
    **SPEC,
    "current_head_sha": None,
    "require_verified_effort": 0,
    "max_review_rounds": 2,
    "completed_review_rounds": 0,
    "status": "AWAITING_HANDOFF",
    "created_at": 1000.0,
    "updated_at": 1000.0,
    "review_round": 1,
    "rounds_remaining": 2,
    "previous_reviewed_sha": None,
    "previous_verdict": None,
    "previous_reviews": [],
    "events": [],
    "github_references": {
        "complete_diff": None,
        "correction_diff": None,
        "context_files": {},
    },
}
QUEUE_ITEM = {
    "task_id": "TEST",
    "title": SPEC["title"],
    "repository": REPO,
    "branch": "task/test",
    "head_sha": HEAD,
    "requested_effort": "medium",
    "review_round": 1,
}


@pytest.mark.parametrize(
    ("name", "arguments", "use_case", "request_arg", "output", "wire"),
    [
        (
            "list_review_queue",
            {"requested_effort": "medium"},
            ListReviewQueueUseCase,
            "medium",
            (ReviewQueueItem(**QUEUE_ITEM),),
            {"tasks": [QUEUE_ITEM]},
        ),
        (
            "get_review_context",
            TASK,
            GetReviewContextUseCase,
            GetReviewContextInput("TEST"),
            CONTEXT,
            CONTEXT_WIRE,
        ),
        (
            "get_review_result",
            TASK,
            GetReviewResultUseCase,
            GetReviewContextInput("TEST"),
            GetReviewResultOutput(CONTEXT, None, None),
            {
                **CONTEXT_WIRE,
                "latest_reviewed_sha": None,
                "actual_effort": None,
                "escalation_reason": None,
                "approval_current": None,
                "remote_verification": "not_performed_by_wfc",
            },
        ),
    ],
)
def test_review_reads_authorize_execute_and_close(
    monkeypatch,
    authorized_workflow,
    name,
    arguments,
    use_case,
    request_arg,
    output,
    wire,
):
    factory = Mock()
    open_factory = Mock(return_value=factory)
    execute = Mock(return_value=output)
    monkeypatch.setattr(PostgresFactory, "open", open_factory)
    monkeypatch.setattr(use_case, "execute", execute)

    reset = auth_context_var.set(None)
    try:
        with pytest.raises(ToolError, match="workflow OAuth authorization required"):
            asyncio.run(mcp.call_tool(name, arguments))
        open_factory.assert_not_called()
        execute.assert_not_called()
    finally:
        auth_context_var.reset(reset)

    response = asyncio.run(mcp.call_tool(name, arguments))
    assert json.loads(response[0].text) == wire
    open_factory.assert_called_once()
    assert open_factory.call_args.args[0].pg_runtime_dsn == DSN
    execute.assert_called_once_with(request_arg)
    factory.close.assert_called_once_with()


@pytest.mark.parametrize("name", ["get_review_context", "get_review_result"])
def test_invalid_task_id_fails_before_open(monkeypatch, authorized_workflow, name):
    open_factory = Mock()
    monkeypatch.setattr(PostgresFactory, "open", open_factory)
    with pytest.raises(ToolError, match="unknown authorized task"):
        asyncio.run(mcp.call_tool(name, {"task_id": " \t"}))
    open_factory.assert_not_called()


@pytest.mark.parametrize(
    "name", ["list_review_queue", "get_review_context", "get_review_result"]
)
def test_exception_and_next_call_use_separate_scopes(
    monkeypatch, authorized_workflow, name
):
    failed, successful = Mock(), Mock()
    open_factory = Mock(side_effect=[failed, successful])
    monkeypatch.setattr(PostgresFactory, "open", open_factory)
    if name == "list_review_queue":
        arguments, use_case, result = {}, ListReviewQueueUseCase, ()
    elif name == "get_review_context":
        arguments, use_case, result = TASK, GetReviewContextUseCase, CONTEXT
    else:
        arguments, use_case = TASK, GetReviewResultUseCase
        result = GetReviewResultOutput(CONTEXT, None, None)

    def execute(_request):
        if open_factory.call_count == 1:
            raise RuntimeError("synthetic operation failed")
        failed.close.assert_called_once_with()
        successful.close.assert_not_called()
        return result

    monkeypatch.setattr(use_case, "execute", staticmethod(execute))
    with pytest.raises(ToolError, match="synthetic operation failed"):
        asyncio.run(mcp.call_tool(name, arguments))
    failed.close.assert_called_once_with()
    successful.close.assert_not_called()

    output = asyncio.run(mcp.call_tool(name, arguments))
    assert json.loads(output[0].text) == (
        {"tasks": []}
        if name == "list_review_queue"
        else CONTEXT_WIRE
        if name == "get_review_context"
        else {
            **CONTEXT_WIRE,
            "latest_reviewed_sha": None,
            "actual_effort": None,
            "escalation_reason": None,
            "approval_current": None,
            "remote_verification": "not_performed_by_wfc",
        }
    )
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


@pytest.mark.parametrize("authorization", ["", "Bearer invalid"])
def test_retired_flag_does_not_bypass_bearer(monkeypatch, tmp_path, authorization):
    from types import SimpleNamespace

    monkeypatch.setenv("WFC_AUTH_MODE", "bearer")
    monkeypatch.setenv("WFC_AUTH_TOKEN", "t" * 40)
    monkeypatch.setenv("WFC_REVIEWER_ID", "reviewer")
    monkeypatch.setenv("WFC_POC_NO_AUTH", "1")
    monkeypatch.setenv("WFC_DB_PATH", str(tmp_path / "workflow-poc-unauth.sqlite3"))
    monkeypatch.setenv("PG_RUNTIME_DSN", DSN)
    open_factory = Mock()
    monkeypatch.setattr(PostgresFactory, "open", open_factory)
    request = SimpleNamespace(headers={"authorization": authorization})
    ctx = SimpleNamespace(request_context=SimpleNamespace(request=request))
    monkeypatch.setattr(type(mcp), "get_context", lambda self: ctx)

    with pytest.raises(ToolError, match="workflow authorization required"):
        asyncio.run(mcp.call_tool("get_review_context", TASK))
    open_factory.assert_not_called()


@pytest.mark.parametrize(
    "subject, scopes",
    [(None, []), ("auth0|other", ["wfc:workflow"]), (SUBJECT, ["wfc:techlead"])],
)
def test_denied_auth0_calls_do_not_open_postgres(
    monkeypatch, authorized_workflow, subject, scopes
):
    open_factory = Mock()
    monkeypatch.setattr(PostgresFactory, "open", open_factory)
    user = (
        AuthenticatedUser(
            AccessToken(
                token="synthetic", client_id="test", scopes=scopes, subject=subject
            )
        )
        if subject is not None
        else None
    )
    reset = auth_context_var.set(user)
    try:
        with pytest.raises(ToolError, match="workflow OAuth authorization required"):
            asyncio.run(mcp.call_tool("get_review_context", TASK))
    finally:
        auth_context_var.reset(reset)
    open_factory.assert_not_called()


@pytest.mark.parametrize("name", ["claim_review", "submit_review"])
def test_review_writes_use_cases_authorize_and_close(
    monkeypatch, authorized_workflow, name
):
    from agent_workflow.application.review.use_cases.claim_review import (
        ClaimReviewUseCase,
    )
    from agent_workflow.application.review.use_cases.submit_review import (
        SubmitReviewUseCase,
    )
    from agent_workflow.application.review.use_cases.write_contracts import (
        ClaimReviewOutput,
        SubmitReviewOutput,
    )

    factory = MagicMock(spec=PostgresFactory)
    factory.__enter__.return_value = factory
    session_scope = factory.get_scoped_session.return_value
    opened = Mock(return_value=factory)
    monkeypatch.setattr(PostgresFactory, "open", opened)
    if name == "claim_review":
        case = ClaimReviewUseCase
        arguments = {**TASK, "expected_head_sha": HEAD}
        result = ClaimReviewOutput("token", "TEST", HEAD, 1, 100.0)
        expected = {
            "claim_token": "token",
            "task_id": "TEST",
            "reviewed_sha": HEAD,
            "review_round": 1,
            "lease_expiration": 100.0,
        }
    else:
        case = SubmitReviewUseCase
        arguments = {"submission": SUBMISSION}
        result = SubmitReviewOutput(
            "TEST", "APPROVED", HEAD, review_id="review", review_round=1
        )
        expected = {
            "task_id": "TEST",
            "review_id": "review",
            "verdict": "APPROVED",
            "review_round": 1,
            "reviewed_head_sha": HEAD,
            "actual_effort": None,
        }
    execute = Mock(return_value=result)
    monkeypatch.setattr(case, "execute", lambda self, request: execute(request))
    reset = auth_context_var.set(None)
    try:
        with pytest.raises(ToolError, match="workflow OAuth authorization required"):
            asyncio.run(mcp.call_tool(name, arguments))
        opened.assert_not_called()
        factory.get_scoped_session.assert_not_called()
    finally:
        auth_context_var.reset(reset)
    assert json.loads(asyncio.run(mcp.call_tool(name, arguments))[0].text) == expected
    assert execute.call_args.args[0].reviewer == ACTOR
    opened.assert_called_once()
    factory.get_scoped_session.assert_called_once_with()
    session_scope.__enter__.assert_called_once_with()
    session_scope.__exit__.assert_called_once_with(None, None, None)
    factory.__exit__.assert_called_once_with(None, None, None)


def test_submit_review_fastmcp_schema_matches_baseline():
    expected = Path(__file__).with_name("fixtures") / "submit_review_input_schema.json"
    tool = next(
        tool for tool in asyncio.run(mcp.list_tools()) if tool.name == "submit_review"
    )
    assert tool.inputSchema == json.loads(expected.read_text(encoding="ascii"))


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"task_id": 123}, "task_id"),
        ({"review_round": "1"}, "review_round"),
        ({"review_round": True}, "review_round"),
        ({"review_round": 3}, "review_round"),
        ({"verdict": "IN_REVIEW"}, "verdict"),
        ({"summary": ""}, "summary"),
        ({"findings": [finding() | {"line": "1"}]}, "line"),
        ({"findings": [finding() | {"line": 0}]}, "line"),
        ({"findings": [finding() | {"severity": "unknown"}]}, "severity"),
        ({"findings": [finding() | {"extra": True}]}, "extra"),
        ({"prior_resolutions": {"F": "unknown"}}, "prior_resolutions"),
        ({"extra": True}, "extra"),
        ({"claim_token": " "}, "unknown claim"),
        ({"reviewed_head_sha": "short"}, "full lowercase"),
    ],
)
def test_invalid_review_submission_does_not_open_provider(
    monkeypatch, authorized_workflow, overrides, error
):
    opened = Mock()
    monkeypatch.setattr(PostgresFactory, "open", opened)
    with pytest.raises(ToolError, match=error):
        asyncio.run(
            mcp.call_tool("submit_review", {"submission": SUBMISSION | overrides})
        )
    opened.assert_not_called()
