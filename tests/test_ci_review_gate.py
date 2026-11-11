"""V1-03C optional reviewer gate enforces trusted exact-HEAD CI."""
import pytest
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.ci_state import ControllerCiState

REPO = "builacviet/agent-workflow"
A, B = "a"*40, "b"*40


def test_review_gate_denies_missing_and_stale_ci(tmp_path, monkeypatch):
    service = WorkflowService(tmp_path / "state.db")
    service.provision_task(dict(
        task_id="T", title="Fixture", description="CI", acceptance_criteria=["AC"],
        definition_of_done="test", repository=REPO, branch="codex/test",
        base_sha=A, requested_effort="high", risk_level="high",
        require_verified_effort=True, context_paths=["docs/handoff.md"], synthetic=True,
    ))
    service.register_task_handoff("T", REPO, "codex/test", A, B, "docs/handoff.md", "test")
    monkeypatch.setenv("WFC_CI_GATE_REQUIRED", "1")
    assert service.list_review_queue() == []
    with pytest.raises(WorkflowError, match="exact-HEAD CI"):
        service.claim_review("T", B, "reviewer")
    state = ControllerCiState(service.db_path)
    state.track("T", 21, B)
    assert service.list_review_queue() == []
    state.record(CiEvidence(
        "T", REPO, 17, 21, B, B, B, "Python tests", "github-actions",
        "SUCCESS", "REQUIRED_CHECK_PASSED", 3, 44, 1,
        "2026-11-10T00:00:00Z", "2026-11-10T00:01:00Z",
    ))
    assert service.list_review_queue()[0]["head_sha"] == B
    assert service.claim_review("T", B, "reviewer")["reviewed_sha"] == B
    with service.repository.raw_transaction() as db:
        db.execute("UPDATE controller_ci_tasks SET verdict='BLOCKED' WHERE task_id='T'")
    assert service.list_review_queue() == []
