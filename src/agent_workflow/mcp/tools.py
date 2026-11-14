"""MCP adapters; existing ping/info and transport settings are preserved."""

from mcp.server.fastmcp import Context
from pydantic import StrictInt

from agent_workflow.app_config import AppConfig
from agent_workflow.domain.contracts import Submission
from agent_workflow.domain.service import default_service
from agent_workflow.mcp.security import authorize_context, authorize_techlead
from agent_workflow.mcp.techlead_github import TechLeadGitHub
from agent_workflow.mcp.techlead_notion import TechLeadNotion


def configured_service_factory():
    """Read ENV only when constructing a service, not inside domain/use cases."""
    return default_service(require_ci=AppConfig.from_env().ci_gate_required)


def register_tools(mcp, service_factory=configured_service_factory):
    def service(ctx):
        actor = authorize_context(ctx)
        return service_factory(), actor

    @mcp.tool()
    def list_review_queue(ctx: Context, requested_effort: str | None = None) -> dict:
        """Authenticated: list reviewable tasks, optionally filtered by requested effort."""
        engine, _ = service(ctx)
        return {"tasks": engine.list_review_queue(requested_effort)}

    @mcp.tool()
    def get_review_context(task_id: str, ctx: Context) -> dict:
        """Authenticated: full task, immutable prior reviews/findings and GitHub references."""
        engine, _ = service(ctx)
        return engine.get_review_context(task_id)

    @mcp.tool()
    def claim_review(task_id: str, expected_head_sha: str, ctx: Context) -> dict:
        """Authenticated mutation: atomically lease the exact current task SHA."""
        engine, actor = service(ctx)
        return engine.claim_review(task_id, expected_head_sha, actor)

    @mcp.tool()
    def submit_review(submission: Submission, ctx: Context) -> dict:
        """Authenticated mutation: submit structured verdict using claim token as idempotency identity."""
        engine, actor = service(ctx)
        return engine.submit_review(submission.model_dump(), actor)

    @mcp.tool()
    def get_review_result(task_id: str, ctx: Context) -> dict:
        """Authenticated: persisted results, resolution history and approval validity."""
        engine, _ = service(ctx)
        return engine.get_review_result(task_id)

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
        engine, _ = service(ctx)
        return engine.register_task_handoff(
            task_id,
            repository,
            branch,
            base_sha,
            new_head_sha,
            handoff_file_path,
            test_summary,
        )

    @mcp.tool()
    def get_coder_assignment(task_id: str, ctx: Context) -> dict:
        """Authenticated read only: task and unresolved findings; never dispatch Codex."""
        engine, _ = service(ctx)
        return engine.get_coder_assignment(task_id)

    if mcp.settings.auth is None:
        return

    @mcp.tool()
    async def tl_github_identity() -> dict:
        """Verify the dedicated Tech Lead GitHub App, installation and bot."""
        authorize_techlead()
        return await TechLeadGitHub(AppConfig.techlead_github()).identity()

    @mcp.tool()
    async def tl_github_get_pr(pr_number: StrictInt) -> dict:
        """Read minimal metadata and the exact HEAD of an agent-workflow PR."""
        authorize_techlead()
        return await TechLeadGitHub(AppConfig.techlead_github()).get_pr(pr_number)

    @mcp.tool()
    async def tl_github_comment_review(
        pr_number: StrictInt, expected_head_sha: str, body: str
    ) -> dict:
        """Submit a COMMENT review pinned to the current HEAD of an allowlisted test PR."""
        authorize_techlead()
        return await TechLeadGitHub(AppConfig.techlead_github()).comment_review(
            pr_number, expected_head_sha, body
        )

    @mcp.tool()
    async def tl_notion_identity() -> dict:
        """Verify the dedicated Tech Lead Notion integration bot."""
        authorize_techlead()
        return await TechLeadNotion(AppConfig.techlead_notion()).identity()

    @mcp.tool()
    async def tl_notion_get_task(page_id: str) -> dict:
        """Read a bounded text preview of an allowlisted Notion task page."""
        authorize_techlead()
        return await TechLeadNotion(AppConfig.techlead_notion()).get_task(page_id)

    @mcp.tool()
    async def tl_notion_comment(page_id: str, body: str) -> dict:
        """Add a comment to an allowlisted Notion test page."""
        authorize_techlead()
        return await TechLeadNotion(AppConfig.techlead_notion()).comment(page_id, body)
