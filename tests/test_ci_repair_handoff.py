"""Regression coverage for the atomic public CI repair handoff."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Barrier

import pytest

from agent_workflow.domain.service import WorkflowService
from agent_workflow.domain.states import CiVerdict
from agent_workflow.integrations.ci_gate import CiEvidence
from agent_workflow.integrations.github import REPOSITORY, WorkflowError
from agent_workflow.storage.ci_state import ControllerCiState
from agent_workflow.storage.repository import SQLiteUnit

BASE, HEAD, FIRST, SECOND, THIRD = (letter * 40 for letter in "abcde")
TASK, PR, BRANCH = "R1-02A", 31, "codex/repair"
HANDOFF, OTHER_PATH = "docs/handoff.md", "docs/other.md"


def register(service: WorkflowService, sha: str = HEAD, **changes):
    return service.register_task_handoff(**{
        "task_id": TASK,
        "repository": REPOSITORY,
        "branch": BRANCH,
        "base_sha": BASE,
        "new_head_sha": sha,
        "handoff_file_path": HANDOFF,
        "test_summary": "Synthetic handoff",
        **changes,
    })


def evidence(sha: str = HEAD, verdict: CiVerdict = CiVerdict.FAILURE) -> CiEvidence:
    return CiEvidence(
        TASK, REPOSITORY, 17, PR, sha, sha, sha,
        "Python tests", "github-actions", verdict,
        {
            CiVerdict.FAILURE: "REQUIRED_CHECK_FAILED",
            CiVerdict.SUCCESS: "REQUIRED_CHECK_PASSED",
            CiVerdict.BLOCKED: "CHECK_PENDING",
        }[verdict],
        7, 44, 1, "2026-11-12T00:00:00Z", "2026-11-12T00:01:00Z",
    )


@pytest.fixture
def workflow(tmp_path):
    path = tmp_path / "workflow.db"
    service = WorkflowService(path)
    service.provision_task({
        "task_id": TASK, "title": "CI repair", "description": "Synthetic task",
        "acceptance_criteria": ["CI is required"], "definition_of_done": "tests pass",
        "repository": REPOSITORY, "branch": BRANCH, "base_sha": BASE,
        "requested_effort": "medium", "risk_level": "low",
        "require_verified_effort": False, "context_paths": [HANDOFF, OTHER_PATH],
        "synthetic": True,
    })
    register(service)
    return service, ControllerCiState(path)


@pytest.fixture
def failed(workflow):
    service, state = workflow
    state.track(TASK, PR, HEAD)
    state.record(evidence())
    return service, state


def test_repair_blocks_review_until_exact_head_ci_and_replays(failed):
    service, state = failed
    state.record(evidence(verdict=CiVerdict.SUCCESS))
    assert state.tracked()[0]["verdict"] == CiVerdict.FAILURE
    result = register(service, FIRST)
    assert result["head_sha"] == FIRST
    assert register(service, FIRST) == result
    assert state.tracked() == [{
        "task_id": TASK, "pr_number": PR, "head_sha": FIRST,
        "verdict": CiVerdict.BLOCKED, "repair_heads": 1,
    }]
    assert state.previous_failure(TASK, HEAD)
    events = service.get_review_context(TASK)["events"]
    assert [event["action"] for event in events] == [
        "LOCAL_AUTHORIZATION", "HANDOFF", "CI_REPAIR_HANDOFF",
    ]
    assert events[-1]["sha"] == FIRST
    assert json.loads(events[-1]["metadata"])["previous_head_sha"] == HEAD
    assert json.loads(events[-1]["metadata"])["pr_number"] == PR

    state.track(TASK, PR, FIRST)
    assert state.tracked()[0]["repair_heads"] == 1
    with pytest.raises(WorkflowError, match="persisted binding"):
        state.track(TASK, PR + 1, FIRST)
    with pytest.raises(WorkflowError, match="mismatched"):
        state.record(replace(evidence(FIRST, CiVerdict.SUCCESS), pr_number=PR + 1))
    with pytest.raises(WorkflowError, match="mismatched"):
        state.record(evidence(HEAD, CiVerdict.SUCCESS))
    with pytest.raises(WorkflowError, match="stale"):
        service.claim_review(TASK, HEAD, "reviewer")
    restarted = WorkflowService(service.db_path, require_ci=False)
    assert restarted.list_review_queue() == []
    with pytest.raises(WorkflowError, match="exact-HEAD CI"):
        restarted.claim_review(TASK, FIRST, "reviewer")

    state.record(evidence(FIRST, CiVerdict.SUCCESS))
    assert restarted.list_review_queue()[0]["head_sha"] == FIRST
    assert restarted.claim_review(TASK, FIRST, "reviewer")["review_round"] == 1


def test_repair_budget_and_stale_sha(failed):
    service, state = failed
    register(service, FIRST)
    state.record(evidence(FIRST))
    with pytest.raises(WorkflowError, match="stale"):
        register(service, HEAD)
    register(service, SECOND)
    state.record(evidence(SECOND))
    with pytest.raises(WorkflowError, match="CI repair"):
        register(service, THIRD)
    assert register(service, SECOND)["head_sha"] == SECOND
    assert state.tracked()[0]["repair_heads"] == 2
    assert service.get_review_context(TASK)["completed_review_rounds"] == 0


@pytest.mark.parametrize("change", [
    {"task_id": "unrelated"}, {"repository": "other/repo"},
    {"branch": "codex/unrelated"}, {"base_sha": THIRD},
    {"handoff_file_path": OTHER_PATH}, {"handoff_file_path": "unlisted.md"},
    {"new_head_sha": "invalid"},
])
def test_repair_rejects_invalid_identity(failed, change):
    service, state = failed
    with pytest.raises(WorkflowError):
        register(service, FIRST, **change)
    assert state.tracked()[0]["head_sha"] == HEAD
    assert state.tracked()[0]["repair_heads"] == 0


@pytest.mark.parametrize("verdict", [CiVerdict.BLOCKED, CiVerdict.SUCCESS, None])
def test_repair_requires_tracked_failure(workflow, verdict):
    service, state = workflow
    if verdict is not None:
        state.track(TASK, PR, HEAD)
        state.record(evidence(verdict=verdict))
    with pytest.raises(WorkflowError, match="CI repair"):
        register(service, FIRST)
    assert service.get_review_context(TASK)["current_head_sha"] == HEAD


def test_unverified_terminal_failure_cannot_enable_repair(workflow):
    service, state = workflow
    state.track(TASK, PR, HEAD)
    with pytest.raises(WorkflowError, match="exact-HEAD"):
        state.record(replace(evidence(), observed_head_sha=SECOND))
    with pytest.raises(WorkflowError, match="CI repair"):
        register(service, FIRST)
    assert state.tracked()[0]["verdict"] == CiVerdict.BLOCKED


@pytest.mark.parametrize("sql,value", [
    ("UPDATE controller_ci_observations SET pr_number=?", PR + 1),
    ("UPDATE controller_ci_observations SET head_sha=?", SECOND),
    ("UPDATE controller_ci_tasks SET head_sha=?", SECOND),
    ("UPDATE controller_ci_observations SET workflow_run_id=?", None),
])
def test_repair_rejects_inconsistent_failure(failed, sql, value):
    service, _ = failed
    with service.repository.raw_transaction() as db:
        db.execute(sql, (value,))
    with pytest.raises(WorkflowError, match="CI repair"):
        register(service, FIRST)
    context = service.get_review_context(TASK)
    assert context["current_head_sha"] == HEAD
    assert len(context["events"]) == 2


def test_conflicting_repairs_only_one_wins(failed):
    service, state = failed
    other = WorkflowService(service.db_path)
    barrier = Barrier(2)

    def propose(worker, sha):
        barrier.wait(timeout=10)
        try:
            return register(worker, sha)["head_sha"]
        except WorkflowError as exc:
            assert "CI repair" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        proposals = [pool.submit(propose, service, FIRST), pool.submit(propose, other, SECOND)]
        outcomes = [proposal.result(timeout=30) for proposal in proposals]
    assert outcomes.count(None) == 1
    winner = next(sha for sha in outcomes if sha is not None)
    assert service.get_review_context(TASK)["current_head_sha"] == winner
    assert len(service.get_review_context(TASK)["events"]) == 3
    assert state.tracked()[0]["head_sha"] == winner
    assert state.tracked()[0]["repair_heads"] == 1


def test_repair_rolls_back_after_event_write_and_restarts(failed, monkeypatch):
    service, _ = failed
    insert = SQLiteUnit.insert_event

    def fail_after_insert(unit, values):
        insert(unit, values)
        raise RuntimeError("injected failure after event")

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteUnit, "insert_event", fail_after_insert)
        with pytest.raises(RuntimeError, match="injected failure"):
            register(service, FIRST)

    restarted = WorkflowService(service.db_path)
    state = ControllerCiState(service.db_path)
    context = restarted.get_review_context(TASK)
    assert context["current_head_sha"] == HEAD
    assert len(context["events"]) == 2
    assert state.tracked() == [{
        "task_id": TASK, "pr_number": PR, "head_sha": HEAD,
        "verdict": CiVerdict.FAILURE, "repair_heads": 0,
    }]
    assert state.previous_failure(TASK, HEAD)
    register(restarted, FIRST)
    assert ControllerCiState(service.db_path).tracked()[0]["head_sha"] == FIRST
