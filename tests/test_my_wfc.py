"""Deterministic tests: remote SHA responses and time are controlled, never invented evidence."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from agent_workflow.admin import BASE, ROUND1, ROUND2, REPO, import_history, seed_synthetic, reset_synthetic
from coder_worker.github import GitHubVerifier
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.mcp.security import authorize_request
from agent_workflow.domain.service import WorkflowService

HEAD = "a" * 40
NEXT = "b" * 40


class Remote:
    def __init__(self):
        self.head = HEAD
        self.available = True

    def __call__(self, repo, branch, base, head):
        if not self.available:
            raise WorkflowError("verification unavailable")
        if head != self.head:
            raise WorkflowError("stale remote")


@pytest.fixture
def env(tmp_path):
    remote = Remote()
    now = [1000.0]
    service = WorkflowService(tmp_path / "state.db", clock=lambda: now[0], lease_seconds=10)
    return service, remote, now


def spec(task_id="TEST", effort="medium", verified=False):
    return dict(task_id=task_id, title="SYNTHETIC test", description="Unit-test fixture only",
                acceptance_criteria=["AC1", "AC2"], definition_of_done="tests pass", repository=REPO,
                branch="task/test", base_sha=BASE, requested_effort=effort, risk_level="low",
                require_verified_effort=verified, context_paths=["AGENTS.md", "docs/architecture.md", "docs/handoff.md", "docs/tasks/TEST.md"], synthetic=True)


def ready(service, task_id="TEST", effort="medium", verified=False):
    service.provision_task(spec(task_id, effort, verified))
    return service.register_task_handoff(task_id, REPO, "task/test", BASE, HEAD, "docs/handoff.md", "mock test evidence")


def finding(fid="F-1"):
    return dict(finding_id=fid, severity="medium", acceptance_criterion="AC1", file_path="src/file.py",
                line=1, description="test finding", suggested_correction="test fix", required_verification="test regression")


def payload(claim, verdict="CHANGES_REQUESTED", **overrides):
    data = dict(task_id=claim["task_id"], claim_token=claim["claim_token"],
                reviewed_head_sha=claim["reviewed_sha"], review_round=claim["review_round"], verdict=verdict,
                summary="Synthetic review", findings=[finding()] if verdict != "APPROVED" else [],
                prior_resolutions={}, requested_effort="medium")
    return {**data, **overrides}


def test_ping_and_info_preserved():
    from agent_workflow.mcp.server import ping, get_server_info, mcp
    assert ping() == "pong"
    assert get_server_info()["name"] == "Workflow MCP Lab"
    assert mcp.settings.host == "127.0.0.1"
    assert mcp.settings.port == 8000


def test_restart_persistence_and_queue_filter(env):
    service, remote, now = env
    ready(service)
    ready(service, "HIGH", "high", True)
    restarted = WorkflowService(service.db_path)
    assert [t["task_id"] for t in restarted.list_review_queue("high")] == ["HIGH"]
    assert len(restarted.list_review_queue()) == 2
    assert restarted.list_review_queue("instant") == []


def test_historical_seed_idempotent_and_no_round_three(env):
    service, _, _ = env
    import_history(service)
    import_history(service)
    result = service.get_review_result("TASK-001")
    assert result["status"] == "ESCALATED"
    assert result["completed_review_rounds"] == 2
    assert result["current_head_sha"] == ROUND2
    assert result["actual_effort"] is None
    assert result["acceptance_criteria"] and result["definition_of_done"]
    assert [r["reviewed_head_sha"] for r in result["previous_reviews"]] == [ROUND1, ROUND2]
    assert [r["reviewed_at"] for r in result["previous_reviews"]] == [None, None]
    assert all(r["reviewer"] is None for r in result["previous_reviews"])
    assert result["previous_reviews"][1]["prior_resolutions"] == {"F-001": "FIXED"}
    assert not service.list_review_queue()
    assert [f["finding_id"] for f in service.get_coder_assignment("TASK-001")["unresolved_findings"]] == ["F-002"]
    with pytest.raises(WorkflowError):
        service.claim_review("TASK-001", ROUND2, "lead")
    with pytest.raises(WorkflowError):
        service.register_task_handoff("TASK-001", REPO, "task/TASK-001", BASE, NEXT, "docs/handoff.md", "x")


def test_concurrent_claims_only_one_winner(env):
    service, _, _ = env
    ready(service)
    def claim(_):
        try:
            return service.claim_review("TEST", HEAD, "lead")
        except WorkflowError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(claim, range(2)))
    assert sum(result is not None for result in outcomes) == 1
    assert not service.list_review_queue()


def test_lease_recovery_does_not_consume_round(env):
    service, _, now = env
    ready(service)
    first = service.claim_review("TEST", HEAD, "lead")
    now[0] += 11
    assert service.list_review_queue()[0]["review_round"] == 1
    second = service.claim_review("TEST", HEAD, "lead")
    assert first["claim_token"] != second["claim_token"]
    with pytest.raises(WorkflowError):
        service.submit_review(payload(first), "lead")
    assert service.get_review_result("TEST")["completed_review_rounds"] == 0


def test_expired_claim_direct_recovery(env):
    service, _, now = env
    ready(service)
    service.claim_review("TEST", HEAD, "lead")
    now[0] += 10
    assert service.claim_review("TEST", HEAD, "lead")["review_round"] == 1


def test_stale_client_sha_and_wrong_owner(env):
    service, _, _ = env
    ready(service)
    with pytest.raises(WorkflowError):
        service.claim_review("TEST", NEXT, "lead")
    claim = service.claim_review("TEST", HEAD, "lead")
    with pytest.raises(WorkflowError):
        service.submit_review(payload(claim, reviewed_head_sha=NEXT), "lead")
    with pytest.raises(WorkflowError):
        service.submit_review(payload(claim), "other")
    assert service.get_review_result("TEST")["completed_review_rounds"] == 0


def test_duplicate_submission_and_conflicting_duplicate(env):
    service, _, _ = env
    ready(service)
    claim = service.claim_review("TEST", HEAD, "lead")
    data = payload(claim)
    service.submit_review(data, "lead")
    assert service.submit_review(data, "lead")["duplicate"]
    with pytest.raises(WorkflowError):
        service.submit_review({**data, "summary": "different"}, "lead")
    assert service.get_review_result("TEST")["completed_review_rounds"] == 1


def test_two_round_cycle_full_history_and_resolution(env):
    service, remote, _ = env
    ready(service)
    first = service.claim_review("TEST", HEAD, "lead")
    service.submit_review(payload(first), "lead")
    assert service.get_review_result("TEST")["status"] == "CHANGES_REQUESTED"
    assert service.list_review_queue() == []
    with pytest.raises(WorkflowError):
        service.register_task_handoff("TEST", REPO, "task/test", BASE, HEAD, "docs/handoff.md", "x")
    remote.head = NEXT
    service.register_task_handoff("TEST", REPO, "task/test", BASE, NEXT, "docs/handoff.md", "mock correction")
    context = service.get_review_context("TEST")
    assert context["review_round"] == 2
    assert context["previous_reviews"][0]["findings"][0]["finding_id"] == "F-1"
    assert context["github_references"]["complete_diff"].endswith(f"{BASE}...{NEXT}")
    assert context["github_references"]["correction_diff"].endswith(f"{HEAD}...{NEXT}")
    second = service.claim_review("TEST", NEXT, "lead")
    with pytest.raises(WorkflowError):
        service.submit_review(payload(second, "ESCALATED", findings=[finding("F-2")]), "lead")
    review = payload(second, "ESCALATED", findings=[finding("F-2")], prior_resolutions={"F-1": "FIXED"})
    service.submit_review(review, "lead")
    result = service.get_review_result("TEST")
    assert result["completed_review_rounds"] == 2 and result["status"] == "ESCALATED"
    assert len(result["previous_reviews"]) == 2
    assert result["previous_reviews"][0]["prior_resolutions"] == {}
    assert result["previous_reviews"][1]["prior_resolutions"] == {"F-1": "FIXED"}
    assert [f["finding_id"] for f in service.get_coder_assignment("TEST")["unresolved_findings"]] == ["F-2"]
    with pytest.raises(WorkflowError):
        service.claim_review("TEST", NEXT, "lead")


@pytest.mark.parametrize("verdict", ["ESCALATED", "APPROVED"])
def test_policy_cannot_be_overridden(env, verdict):
    service, _, _ = env
    ready(service)
    claim = service.claim_review("TEST", HEAD, "lead")
    data = payload(claim, verdict, findings=[finding()])
    with pytest.raises(WorkflowError):
        service.submit_review(data, "lead")
    assert service.get_review_result("TEST")["completed_review_rounds"] == 0


def test_unverified_high_blocks_approval_without_consuming_round(env):
    service, _, _ = env
    ready(service, effort="high", verified=True)
    claim = service.claim_review("TEST", HEAD, "lead")
    data = payload(claim, "APPROVED", requested_effort="high", actual_effort="high", verification_evidence="client says high")
    with pytest.raises(WorkflowError, match="effort"):
        service.submit_review(data, "lead")
    assert service.get_review_result("TEST")["completed_review_rounds"] == 0
    assert service.get_review_result("TEST")["status"] == "IN_REVIEW"


def test_unverified_claim_not_recorded_as_actual_effort(env):
    service, _, _ = env
    ready(service)
    claim = service.claim_review("TEST", HEAD, "lead")
    service.submit_review(payload(claim, actual_effort="high", verification_evidence="untrusted"), "lead")
    review = service.get_review_result("TEST")["previous_reviews"][0]
    assert review["actual_effort"] is None and review["verification_evidence"] == "unverified"


def test_approval_terminal_and_branch_move_invalidates_approval(env):
    service, remote, _ = env
    ready(service)
    claim = service.claim_review("TEST", HEAD, "lead")
    data = payload(claim, "APPROVED")
    service.submit_review(data, "lead")
    assert service.get_review_result("TEST")["approval_current"] is None
    with pytest.raises(WorkflowError):
        service.claim_review("TEST", HEAD, "lead")
    remote.head = NEXT
    assert service.get_review_result("TEST")["approval_current"] is None
    assert service.submit_review(data, "lead")["duplicate"]
    with pytest.raises(WorkflowError):
        service.register_task_handoff("TEST", REPO, "task/test", BASE, NEXT, "docs/handoff.md", "x")


def test_transaction_rollback_after_partial_review_write(env, monkeypatch):
    service, _, _ = env
    ready(service)
    claim = service.claim_review("TEST", HEAD, "lead")
    before = service.get_review_context("TEST")
    original = service.insert_findings
    def fail(*args):
        original(*args)
        raise RuntimeError("simulated storage failure")
    monkeypatch.setattr(service, "insert_findings", fail)
    with pytest.raises(RuntimeError):
        service.submit_review(payload(claim), "lead")
    assert service.get_review_context("TEST") == before
    monkeypatch.setattr(service, "insert_findings", original)
    service.submit_review(payload(claim), "lead")
    assert service.get_review_result("TEST")["completed_review_rounds"] == 1


def test_poc_unauthenticated_mode_requires_disposable_database(monkeypatch, tmp_path):
    monkeypatch.setenv("WFC_POC_NO_AUTH", "1")
    monkeypatch.delenv("WFC_DB_PATH", raising=False)
    monkeypatch.delenv("WFC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("WFC_REVIEWER_ID", raising=False)

    # Unsafe mode fails closed unless a dedicated absolute SQLite path is set.
    with pytest.raises(PermissionError, match="WFC_DB_PATH"):
        authorize_request(None)
    monkeypatch.setenv("WFC_DB_PATH", "workflow-poc-unauth.sqlite3")
    with pytest.raises(PermissionError, match="WFC_DB_PATH"):
        authorize_request(None)
    monkeypatch.setenv("WFC_DB_PATH", str(tmp_path / "workflow.sqlite3"))
    with pytest.raises(PermissionError, match="WFC_DB_PATH"):
        authorize_request(None)

    monkeypatch.setenv("WFC_DB_PATH", str(tmp_path / "workflow-poc-unauth.sqlite3"))
    assert authorize_request(None) == "poc-unauthenticated"

    # Without the explicit flag, standard Bearer enforcement remains active.
    monkeypatch.setenv("WFC_POC_NO_AUTH", "0")
    with pytest.raises(PermissionError):
        authorize_request(None)


def test_auth_denies_default_missing_and_wrong_token(monkeypatch):
    monkeypatch.delenv("WFC_POC_NO_AUTH", raising=False)
    monkeypatch.delenv("WFC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("WFC_REVIEWER_ID", raising=False)
    request = lambda value: Request({"type": "http", "headers": [(b"authorization", value.encode())]})
    with pytest.raises(PermissionError):
        authorize_request(request("Bearer x"))
    monkeypatch.setenv("WFC_AUTH_TOKEN", "a" * 32)
    monkeypatch.setenv("WFC_REVIEWER_ID", "test-reviewer")
    for header in ("", "Bearer wrong"):
        with pytest.raises(PermissionError):
            authorize_request(request(header))
    assert authorize_request(request("Bearer " + "a" * 32)) == "test-reviewer"


def test_authorization_metadata_and_handoff_cannot_be_retargeted(env):
    service, _, _ = env
    with pytest.raises(WorkflowError):
        service.provision_task({**spec(), "repository": "other/repo"})
    ready(service)
    for args in [("other/repo", "task/test", BASE), (REPO, "other", BASE), (REPO, "task/test", NEXT)]:
        with pytest.raises(WorkflowError):
            service.register_task_handoff("TEST", *args, HEAD, "docs/handoff.md", "x")


def test_synthetic_reset_preserves_real_history(env):
    service, remote, _ = env
    import_history(service)
    remote.head = BASE
    seed_synthetic(service)
    before = service.get_review_context("TASK-001")
    reset_synthetic(service)
    seed_synthetic(service)
    assert service.get_review_context("TASK-001") == before
    assert service.list_review_queue()[0]["task_id"] == "POC-REVIEW-001"


def test_github_verifier_fail_closed_and_escapes_branch(monkeypatch):
    import subprocess
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout='{"commit":{"sha":"' + HEAD + '"}}' if "branches" in args[-1] else '{"status":"ahead"}')
    monkeypatch.setattr(subprocess, "run", run)
    verify = GitHubVerifier({REPO})
    verify(REPO, "task/test", BASE, HEAD)
    assert "task%2Ftest" in calls[0][-1]
    with pytest.raises(WorkflowError):
        verify("unknown/repo", "x", BASE, HEAD)
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired("gh", 15)
    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(WorkflowError, match="unavailable"):
        verify(REPO, "task/test", BASE, HEAD)


def test_http_mcp_boundary_and_complete_tool_contract(env, monkeypatch):
    """Real local HTTP/MCP handshake; external GitHub remains mocked."""
    import asyncio
    import socket
    import threading
    import time
    import uvicorn
    from mcp import ClientSession
    import httpx
    from mcp.client.streamable_http import streamable_http_client
    from agent_workflow.mcp.server import mcp as server_mcp

    engine, _, _ = env
    ready(engine)
    # Production MCP composition now requires trusted exact-HEAD CI; create
    # synthetic local evidence instead of disabling the gate for this test.
    from agent_workflow.storage.ci_state import ControllerCiState
    from agent_workflow.integrations.ci_gate import CiEvidence
    ci = ControllerCiState(engine.db_path)
    ci.track("TEST", 21, HEAD)
    ci.record(CiEvidence(
        "TEST", REPO, 17, 21, HEAD, HEAD, HEAD,
        "Python tests", "github-actions", "SUCCESS", "REQUIRED_CHECK_PASSED",
        71, 72, 1, "2026-11-11T00:00:00Z", "2026-11-11T00:01:00Z",
    ))
    monkeypatch.setenv("WFC_AUTH_TOKEN", "t" * 40)
    monkeypatch.setenv("WFC_REVIEWER_ID", "http-test-reviewer")
    monkeypatch.setenv("WFC_DB_PATH", engine.db_path)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(server_mcp.streamable_http_app(), log_level="critical"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
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
                        "register_task_handoff": dict(task_id="TEST", repository=REPO, branch="task/test", base_sha=BASE, new_head_sha=HEAD, handoff_file_path="docs/handoff.md", test_summary="x"),
                        "submit_review": {"submission": payload({"task_id": "TEST", "claim_token": "unknown", "reviewed_sha": HEAD, "review_round": 1})},
                    }
                    for name, args in mutation_args.items():
                        denied = await session.call_tool(name, args)
                        assert denied.isError
                    assert engine.get_review_context("TEST")["status"] == "READY_IN_REVIEW"
            async with httpx.AsyncClient(headers={"Authorization": "Bearer " + "t" * 40}) as http_client, streamable_http_client(url, http_client=http_client) as (read, write, _):
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
                    queue = await session.call_tool("list_review_queue", {"requested_effort": "medium"})
                    assert not queue.isError
                    claimed = await session.call_tool("claim_review", {"task_id": "TEST", "expected_head_sha": HEAD})
                    assert not claimed.isError
                    claim = __import__("json").loads(claimed.content[0].text)
                    # Generic dict returns use the SDK text JSON representation.
                    data = payload(claim, "APPROVED")
                    submitted = await session.call_tool("submit_review", {"submission": data})
                    assert not submitted.isError
                    assert engine.get_review_result("TEST")["status"] == "APPROVED"
        asyncio.run(exercise())
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
    assert not thread.is_alive()


def test_trusted_effort_adapter_only_path_to_verified_approval(env):
    service, _, _ = env
    ready(service, effort="high", verified=True)
    service.effort_verifier = lambda actor, task, data: ("high", "server-side test attestation")
    claim = service.claim_review("TEST", HEAD, "lead")
    result = service.submit_review(payload(claim, "APPROVED", requested_effort="high"), "lead")
    assert result["actual_effort"] == "high"


def test_concurrent_submissions_increment_exactly_once(env):
    service, _, _ = env
    ready(service)
    claim = service.claim_review("TEST", HEAD, "lead")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.submit_review(payload(claim), "lead"), range(2)))
    assert sum(bool(result.get("duplicate")) for result in results) == 1
    assert service.get_review_result("TEST")["completed_review_rounds"] == 1


def test_old_synthetic_metadata_is_not_silently_retargeted(env):
    from agent_workflow.admin import BASE, REPO, import_history, reset_synthetic, seed_synthetic
    from agent_workflow.domain.contracts import TaskSpec

    service, remote, _ = env
    import_history(service)
    remote.head = BASE
    seed_synthetic(service)
    context = service.get_review_context("POC-REVIEW-001")
    old_spec = {name: context[name] for name in TaskSpec.model_fields}
    reset_synthetic(service)
    old_spec["branch"] = "main"
    old_spec["require_verified_effort"] = bool(old_spec["require_verified_effort"])
    service.provision_task(old_spec)
    service.register_task_handoff("POC-REVIEW-001", REPO, "main", BASE, BASE, "docs/handoff.md", "synthetic legacy fixture")
    before = service.get_review_context("TASK-001")
    with pytest.raises(WorkflowError, match="conflicts"):
        seed_synthetic(service)
    assert service.get_review_context("POC-REVIEW-001")["branch"] == "main"
    reset_synthetic(service)
    seed_synthetic(service)
    assert service.get_review_context("POC-REVIEW-001")["branch"] == "poc/review-baseline"
    assert service.get_review_context("TASK-001") == before


def test_server_docker_bind_and_public_hostname_are_explicit(monkeypatch):
    from agent_workflow.mcp.server import server_bind_host, server_allowed_hosts
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
