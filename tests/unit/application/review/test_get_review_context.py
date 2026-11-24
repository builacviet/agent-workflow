from dataclasses import FrozenInstanceError, replace
from typing import assert_type

import pytest

from agent_workflow.application.review.dtos import (
    PreviousReview,
    ReviewContextSnapshot,
    ReviewEvent,
    ReviewFinding,
    ReviewTask,
)
from agent_workflow.application.review.ports import IReviewContextReader
from agent_workflow.application.review.use_cases.get_review_context import (
    GetReviewContextInput,
    GetReviewContextOutput,
    GetReviewContextUseCase,
)
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError

BASE = "a" * 40
HEAD = "b" * 40


class FakeReviewContextReader:
    def __init__(
        self, result: ReviewContextSnapshot | None, *, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.reads: list[str] = []

    def read(self, task_id: str) -> ReviewContextSnapshot | None:
        self.reads.append(task_id)
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def snapshot() -> ReviewContextSnapshot:
    return ReviewContextSnapshot(
        task=ReviewTask(
            task_id="R2-TEST",
            repository="builacviet/agent-workflow",
            branch="codex/test",
            base_sha=BASE,
            current_head_sha=None,
            requested_effort="medium",
            require_verified_effort=0,
            max_review_rounds=2,
            completed_review_rounds=0,
            status=WorkflowStatus.AWAITING_HANDOFF,
            created_at=1000.0,
            updated_at=1000.0,
            context_paths=("docs/review context.md",),
            spec_json='{"context_paths":["docs/review context.md"]}',
        ),
        previous_reviews=(),
        events=(
            ReviewEvent(
                event_id=1,
                task_id="R2-TEST",
                previous_status=None,
                next_status=WorkflowStatus.AWAITING_HANDOFF,
                action="LOCAL_AUTHORIZATION",
                sha=None,
                timestamp=1000.0,
                metadata="{}",
            ),
        ),
    )


def test_empty_history_is_typed_and_only_reads(snapshot: ReviewContextSnapshot) -> None:
    reader = FakeReviewContextReader(snapshot)
    port: IReviewContextReader = reader
    use_case = GetReviewContextUseCase(port)
    request = GetReviewContextInput("R2-TEST")

    assert reader.reads == []
    result = assert_type(use_case.execute(request), GetReviewContextOutput)
    assert result == use_case.execute(request)
    assert reader.reads == [request.task_id, request.task_id]
    assert result.task == snapshot.task
    assert (
        assert_type(result.task.status, WorkflowStatus)
        is WorkflowStatus.AWAITING_HANDOFF
    )
    assert result.task.current_head_sha is None
    assert result.review_round == 1
    assert result.rounds_remaining == 2
    assert result.previous_reviewed_sha is None
    assert result.previous_verdict is None
    assert result.previous_reviews == ()
    assert result.events == snapshot.events
    with pytest.raises(FrozenInstanceError):
        setattr(result.task, "status", WorkflowStatus.APPROVED)
    assert snapshot.task.status is WorkflowStatus.AWAITING_HANDOFF


def test_prior_review_retains_findings_and_caps_round(
    snapshot: ReviewContextSnapshot,
) -> None:
    finding = ReviewFinding(
        review_id="review-1",
        finding_id="F-1",
        task_id="R2-TEST",
        severity="medium",
        acceptance_criterion="AC1",
        file_path="src/module.py",
        line=None,
        description="Missing case",
        suggested_correction="Handle absent data",
        required_verification="Regression test",
    )
    first = PreviousReview(
        review_id=finding.review_id,
        task_id=finding.task_id,
        round_number=1,
        base_sha=BASE,
        reviewed_head_sha=HEAD,
        verdict=WorkflowStatus.CHANGES_REQUESTED,
        summary="Round one",
        requested_effort="medium",
        actual_effort=None,
        verification_evidence="",
        reviewer="tech-lead",
        reviewed_at=1000.0,
        imported_at=None,
        prior_resolutions=(),
        findings=(finding,),
    )
    last = replace(
        first,
        review_id="review-2",
        round_number=2,
        reviewed_head_sha="c" * 40,
        verdict=WorkflowStatus.APPROVED,
        prior_resolutions=(("F-1", "FIXED"),),
        findings=(),
    )
    snapshot = replace(
        snapshot,
        task=replace(
            snapshot.task,
            current_head_sha=last.reviewed_head_sha,
            completed_review_rounds=2,
            status=WorkflowStatus.APPROVED,
        ),
        previous_reviews=(first, last),
    )
    reader = FakeReviewContextReader(snapshot)

    result = GetReviewContextUseCase(reader).execute(GetReviewContextInput("R2-TEST"))

    assert reader.reads == ["R2-TEST"]
    assert result.task.current_head_sha == last.reviewed_head_sha
    assert (result.review_round, result.rounds_remaining) == (2, 0)
    assert result.previous_reviewed_sha == last.reviewed_head_sha
    assert result.previous_verdict is WorkflowStatus.APPROVED
    assert result.previous_reviews == (first, last)
    assert result.previous_reviews[0].findings == (finding,)
    assert result.events == snapshot.events


@pytest.mark.parametrize(
    "task_id",
    ["", "   ", "\t\r\n", "\u00a0"],
    ids=["empty", "spaces", "control", "unicode"],
)
def test_blank_task_id_is_rejected_before_read(task_id: str) -> None:
    reader = FakeReviewContextReader(None)
    use_case = GetReviewContextUseCase(reader)

    with pytest.raises(WorkflowError, match="^task_id must not be blank$"):
        use_case.execute(GetReviewContextInput(task_id))

    assert reader.reads == []


@pytest.mark.parametrize("task_id", ["missing", " missing "])
def test_unknown_task_fails_closed(task_id: str) -> None:
    reader = FakeReviewContextReader(None)
    with pytest.raises(WorkflowError, match="^unknown authorized task$"):
        GetReviewContextUseCase(reader).execute(GetReviewContextInput(task_id))
    assert reader.reads == [task_id]


def test_non_string_task_id_is_rejected():
    with pytest.raises(WorkflowError, match="^task_id must be a string$"):
        GetReviewContextInput(1)


def test_invalid_persisted_status_and_read_failure_propagate() -> None:
    with pytest.raises(ValueError) as invalid_state:
        WorkflowStatus("UNKNOWN")

    for error in (invalid_state.value, TimeoutError("read deadline expired")):
        reader = FakeReviewContextReader(None, error=error)
        with pytest.raises(type(error)) as failure:
            GetReviewContextUseCase(reader).execute(GetReviewContextInput("R2-TEST"))
        assert failure.value is error
        assert reader.reads == ["R2-TEST"]
