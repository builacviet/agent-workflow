"""MCP adapters; existing ping/info and transport settings are preserved."""
import os
from my_wfc.paths import database_path

from mcp.server.fastmcp import Context
from my_wfc.contracts import Submission
from my_wfc.github import GitHubVerifier
from my_wfc.security import authorize_context
from my_wfc.service import WorkflowService


def default_service():
    return WorkflowService(
        database_path(),
        GitHubVerifier({"builacviet/techlead-review-poc"}),
    )


def register_tools(mcp, service_factory=default_service):
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
    def register_task_handoff(task_id: str, repository: str, branch: str, base_sha: str,
                              new_head_sha: str, handoff_file_path: str, test_summary: str,
                              ctx: Context) -> dict:
        """Authenticated mutation: verify a pushed handoff for a locally authorized task."""
        engine, _ = service(ctx)
        return engine.register_task_handoff(task_id, repository, branch, base_sha, new_head_sha,
                                            handoff_file_path, test_summary)

    @mcp.tool()
    def get_coder_assignment(task_id: str, ctx: Context) -> dict:
        """Authenticated read only: task and unresolved findings; never dispatch Codex."""
        engine, _ = service(ctx)
        return engine.get_coder_assignment(task_id)
