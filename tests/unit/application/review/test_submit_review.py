from dataclasses import replace
from unittest.mock import Mock

import pytest
from test_review_writes import (
    HEAD,
    TASK,
    scope_for,
    submission,
    submit_rejects_expired_and_conflicting_duplicate_without_write,
)

from agent_workflow.application.review.use_cases.submit_review import (
    SubmitReviewUseCase,
)
from agent_workflow.application.review.use_cases.write_contracts import (
    SubmitReviewInput,
)
from agent_workflow.domain.review.policy import ClaimFacts
from agent_workflow.domain.workflow_identity import WorkflowError


def test_submission_validation():
    submit_rejects_expired_and_conflicting_duplicate_without_write()


def test_submit_duplicate_and_effort_gate():
    import json

    repo = Mock()
    task = replace(TASK, status="IN_REVIEW", verified_effort=True)
    repo.lock_task.return_value = task
    repo.has_successful_ci.return_value = True
    repo.prior_findings.return_value = set()
    repo.find_claim.return_value = ClaimFacts(
        "token", "lead", HEAD, 101, "ACTIVE", None
    )
    case = SubmitReviewUseCase(
        scope_for(repo), clock=lambda: 100, token=lambda: "review", require_ci=True
    )
    with pytest.raises(WorkflowError, match="effort cannot be verified"):
        case.execute(SubmitReviewInput(submission(), "lead"))
    repo.submit.assert_not_called()
    verified = SubmitReviewUseCase(
        scope_for(repo),
        clock=lambda: 100,
        token=lambda: "review",
        require_ci=True,
        effort_verifier=lambda *_: ("medium", "verified"),
    )
    result = verified.execute(SubmitReviewInput(submission(), "lead"))
    assert result.actual_effort == "medium"
    assert repo.submit.call_count == 1
    canonical = json.dumps(
        submission().model_dump(),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    repo.find_claim.return_value = ClaimFacts(
        "token", "lead", HEAD, 101, "SUBMITTED", canonical
    )
    assert case.execute(SubmitReviewInput(submission(), "lead")).duplicate
    assert repo.submit.call_count == 1


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"reviewer": "other"}, "owner mismatch"),
        ({"claim": None}, "owner mismatch"),
        (
            {"claim": ClaimFacts("token", "lead", HEAD, 100, "ACTIVE", None)},
            "claim expired",
        ),
        ({"task": replace(TASK, status="READY_IN_REVIEW")}, "not in an active review"),
        (
            {"task": replace(TASK, status="IN_REVIEW", rounds=2)},
            "not in an active review",
        ),
        (
            {"task": replace(TASK, status="IN_REVIEW", head="c" * 40)},
            "stale submission SHA",
        ),
        (
            {"claim": ClaimFacts("token", "lead", "c" * 40, 101, "ACTIVE", None)},
            "stale submission SHA",
        ),
        ({"ci": False}, "exact-HEAD CI"),
        ({"data": submission(review_round=2)}, "round or requested effort"),
        ({"data": submission(requested_effort="high")}, "round or requested effort"),
    ],
)
def test_submit_rejections_have_no_persistence(change, message):
    repo = Mock()
    repo.lock_task.return_value = change.get("task", replace(TASK, status="IN_REVIEW"))
    repo.find_claim.return_value = change.get(
        "claim", ClaimFacts("token", "lead", HEAD, 101, "ACTIVE", None)
    )
    repo.has_successful_ci.return_value = change.get("ci", True)
    case = SubmitReviewUseCase(
        scope_for(repo), clock=lambda: 100, token=lambda: "review", require_ci=True
    )
    with pytest.raises(WorkflowError, match=message):
        case.execute(
            SubmitReviewInput(
                change.get("data", submission()), change.get("reviewer", "lead")
            )
        )
    repo.submit.assert_not_called()


@pytest.mark.parametrize(
    ("round_number", "verdict", "prior", "resolutions", "new_ids", "message"),
    [
        (1, "ESCALATED", set(), {}, ["F"], "two-round"),
        (2, "CHANGES_REQUESTED", set(), {}, ["F"], "two-round"),
        (2, "APPROVED", {"F"}, {"F": "NOT_FIXED"}, [], "approval requires"),
        (1, "APPROVED", set(), {}, ["F"], "approval requires"),
        (1, "CHANGES_REQUESTED", set(), {}, [], "failure verdict requires"),
        (1, "CHANGES_REQUESTED", set(), {}, ["F", "F"], "unique"),
    ],
)
def test_verdict_policy_rejects_invalid_cases(
    round_number, verdict, prior, resolutions, new_ids, message
):
    from agent_workflow.domain.review.policy import verdict_allowed

    with pytest.raises(WorkflowError, match=message):
        verdict_allowed(round_number, verdict, prior, resolutions, new_ids)
