"""Layer: Interfaces/MCP
Responsibility: Authorize MCP tool calls, invoke scoped dependencies and map wire responses.
Dependencies: MCP SDK, Application use cases and MCP security and integration adapters.
"""

from dataclasses import asdict

from mcp.server.auth.routes import build_resource_metadata_url
from mcp.server.fastmcp import Context
from mcp.types import CallToolResult, TextContent
from pydantic import StrictInt

from agent_workflow.app_config import AppConfig
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
)
from agent_workflow.application.review.use_cases.get_review_result import (
    GetReviewResultOutput,
)
from agent_workflow.application.review.use_cases.write_contracts import ClaimReviewInput
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.task.handoff import HandoffRequest
from agent_workflow.domain.workflow_identity import WorkflowError
from agent_workflow.interfaces.mcp.review_request import Submission
from agent_workflow.interfaces.review_assignment_response import (
    coder_assignment_response,
    review_context_response,
)
from agent_workflow.mcp.security import (
    TECHLEAD_SCOPE,
    TechLeadScopeRequired,
    authorize_context,
    authorize_techlead,
)
from agent_workflow.mcp.techlead_github import TechLeadGitHub
from agent_workflow.mcp.techlead_notion import TechLeadNotion


def _review_request(task_id: str) -> GetReviewContextInput:
    if not task_id.strip():
        raise WorkflowError("unknown authorized task")
    return GetReviewContextInput(task_id)


def _review_result_response(output: GetReviewResultOutput) -> dict:
    result = review_context_response(output.context)
    result.update(
        latest_reviewed_sha=output.context.previous_reviewed_sha,
        actual_effort=output.actual_effort,
        escalation_reason=output.escalation_reason,
        approval_current=None,
        remote_verification="not_performed_by_wfc",
    )
    if output.context.task.status == WorkflowStatus.APPROVED:
        result["approval_warning"] = (
            "Recorded approval covers only the stored SHA; Tech Lead must verify GitHub freshness before merge"
        )
    return result


def register_tools(
    mcp,
    task_operations_provider,
    review_reads_provider,
    review_writes_provider,
):
    """Open a fresh scope for each authorized workflow tool call."""

    @mcp.tool()
    def list_review_queue(ctx: Context, requested_effort: str | None = None) -> dict:
        """Authenticated: list reviewable tasks, optionally filtered by requested effort."""
        authorize_context(ctx)
        with review_reads_provider() as (queue, _, _):
            return {"tasks": [asdict(item) for item in queue.execute(requested_effort)]}

    @mcp.tool()
    def get_review_context(task_id: str, ctx: Context) -> dict:
        """Authenticated: full task, immutable prior reviews/findings and GitHub references."""
        authorize_context(ctx)
        request = _review_request(task_id)
        with review_reads_provider() as (_, context, _):
            return review_context_response(context.execute(request))

    @mcp.tool()
    def claim_review(task_id: str, expected_head_sha: str, ctx: Context) -> dict:
        """Authenticated mutation: atomically lease the exact current task SHA."""
        actor = authorize_context(ctx)
        with review_writes_provider() as (claim, _):
            return asdict(
                claim.execute(ClaimReviewInput(task_id, expected_head_sha, actor))
            )

    @mcp.tool()
    def submit_review(submission: Submission, ctx: Context) -> dict:
        """Authenticated mutation: submit structured verdict using claim token as idempotency identity."""
        actor = authorize_context(ctx)
        request = submission.to_input(actor)
        with review_writes_provider() as (_, submit):
            result = submit.execute(request)
            if result.duplicate:
                return {
                    "task_id": result.task_id,
                    "duplicate": True,
                    "reviewed_head_sha": result.reviewed_head_sha,
                    "verdict": result.verdict,
                }
            return {
                "task_id": result.task_id,
                "review_id": result.review_id,
                "verdict": result.verdict,
                "review_round": result.review_round,
                "reviewed_head_sha": result.reviewed_head_sha,
                "actual_effort": result.actual_effort,
            }

    @mcp.tool()
    def get_review_result(task_id: str, ctx: Context) -> dict:
        """Authenticated: persisted results, resolution history and approval validity."""
        authorize_context(ctx)
        request = _review_request(task_id)
        with review_reads_provider() as (_, _, result):
            return _review_result_response(result.execute(request))

    @mcp.tool()
    def register_task_handoff(
        task_id: str,
        repository: str,
        branch: str,
        base_sha: str,
        new_head_sha: str,
        handoff_file_path: str,
        test_summary: str,
        ctx: Context,
    ) -> dict:
        """Authenticated mutation: record reported handoff for a locally authorized task; no remote verification."""
        authorize_context(ctx)
        request = HandoffRequest(
            task_id,
            repository,
            branch,
            base_sha,
            new_head_sha,
            handoff_file_path,
            test_summary,
        )
        with task_operations_provider() as (handoff, _):
            return asdict(handoff.execute(request))

    @mcp.tool()
    def get_coder_assignment(task_id: str, ctx: Context) -> dict:
        """Authenticated read only: task and unresolved findings; never dispatch Codex."""
        authorize_context(ctx)
        with task_operations_provider() as (_, assignment):
            return coder_assignment_response(assignment.execute(task_id))

    if mcp.settings.auth is None:
        return

    workflow_scope = mcp.settings.auth.required_scopes[0]
    techlead_scopes = [workflow_scope, TECHLEAD_SCOPE]
    techlead_meta = {"securitySchemes": [{"type": "oauth2", "scopes": techlead_scopes}]}
    metadata_url = build_resource_metadata_url(mcp.settings.auth.resource_server_url)

    def techlead_challenge() -> CallToolResult | None:
        try:
            authorize_techlead()
        except TechLeadScopeRequired as exc:
            return CallToolResult(
                content=[TextContent(type="text", text=str(exc))],
                isError=True,
                _meta={
                    "mcp/www_authenticate": [
                        'Bearer error="insufficient_scope", '
                        'error_description="Tech Lead OAuth authorization required", '
                        f'resource_metadata="{metadata_url}", '
                        f'scope="{" ".join(techlead_scopes)}"'
                    ]
                },
            )
        return None

    @mcp.tool(meta=techlead_meta)
    async def tl_github_identity() -> dict:
        """Verify the dedicated Tech Lead GitHub App, installation and bot."""
        if challenge := techlead_challenge():
            return challenge
        return await TechLeadGitHub(AppConfig.techlead_github()).identity()

    @mcp.tool(meta=techlead_meta)
    async def tl_github_get_pr(pr_number: StrictInt) -> dict:
        """Read minimal metadata and the exact HEAD of an agent-workflow PR."""
        if challenge := techlead_challenge():
            return challenge
        return await TechLeadGitHub(AppConfig.techlead_github()).get_pr(pr_number)

    @mcp.tool(meta=techlead_meta)
    async def tl_github_comment_review(
        pr_number: StrictInt, expected_head_sha: str, body: str
    ) -> dict:
        """Submit a COMMENT review pinned to the current HEAD of an allowlisted test PR."""
        if challenge := techlead_challenge():
            return challenge
        return await TechLeadGitHub(AppConfig.techlead_github()).comment_review(
            pr_number, expected_head_sha, body
        )

    @mcp.tool(meta=techlead_meta)
    async def tl_notion_identity() -> dict:
        """Verify the dedicated Tech Lead Notion integration bot."""
        if challenge := techlead_challenge():
            return challenge
        return await TechLeadNotion(AppConfig.techlead_notion()).identity()

    @mcp.tool(meta=techlead_meta)
    async def tl_notion_get_task(page_id: str) -> dict:
        """Read a bounded text preview of an allowlisted Notion task page."""
        if challenge := techlead_challenge():
            return challenge
        return await TechLeadNotion(AppConfig.techlead_notion()).get_task(page_id)

    @mcp.tool(meta=techlead_meta)
    async def tl_notion_comment(page_id: str, body: str) -> dict:
        """Add a comment to an allowlisted Notion test page."""
        if challenge := techlead_challenge():
            return challenge
        return await TechLeadNotion(AppConfig.techlead_notion()).comment(page_id, body)
