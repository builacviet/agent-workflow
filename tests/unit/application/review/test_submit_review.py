from dataclasses import replace
from pathlib import Path
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
    ReviewFinding,
    SubmitReviewInput,
)
from agent_workflow.domain.review.policy import ClaimFacts
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import WorkflowError

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"
FINDING = ReviewFinding(
    finding_id="F2",
    severity="critical",
    acceptance_criterion="AC-2",
    file_path="src/review.py",
    description='Ti\u1ebfng Vi\u1ec7t\n"quoted" \\ \U0001f680',
    suggested_correction="fix",
    required_verification="rerun",
)


def test_submission_validation():
    submit_rejects_expired_and_conflicting_duplicate_without_write()


def test_submit_duplicate_and_effort_gate():
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
    canonical = (FIXTURES / "review_submission_round_one.json").read_text(
        encoding="ascii"
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


@pytest.mark.parametrize(
    ("fixture", "submitted"),
    [
        ("review_submission_round_one.json", submission()),
        (
            "review_submission_round_two.json",
            submission(
                review_round=2,
                verdict=WorkflowStatus.ESCALATED,
                summary="round two",
                findings=(
                    FINDING,
                    replace(FINDING, finding_id="F3", severity="low", line=42),
                ),
                prior_resolutions={
                    "Z": "NOT_FIXED",
                    "A": "FIXED",
                    "B\u00e9": "PARTIALLY_FIXED",
                    "C": "NOT_VERIFIED",
                },
                actual_effort="high",
                verification_evidence="line 1\tline 2",
            ),
        ),
    ],
    ids=["defaulted-round-one", "nested-round-two"],
)
def test_submit_matches_and_replays_historical_canonical_payload(fixture, submitted):
    historical = (FIXTURES / fixture).read_bytes()
    repo = Mock()
    repo.lock_task.return_value = replace(
        TASK, status=WorkflowStatus.IN_REVIEW, rounds=submitted.review_round - 1
    )
    repo.has_successful_ci.return_value = True
    repo.prior_findings.return_value = set(submitted.prior_resolutions)
    claim = ClaimFacts("token", "lead", HEAD, 101, "ACTIVE", None)
    repo.find_claim.return_value = claim
    case = SubmitReviewUseCase(
        scope_for(repo), clock=lambda: 100, token=lambda: "review", require_ci=True
    )
    result = case.execute(SubmitReviewInput(submitted, "lead"))
    assert not result.duplicate
    assert repo.submit.call_args.args[2].encode("ascii") == historical
    assert result.actual_effort is None

    repo.reset_mock()
    repo.find_claim.return_value = replace(
        claim, status="SUBMITTED", submitted_payload=historical.decode("ascii")
    )
    assert case.execute(SubmitReviewInput(submitted, "lead")).duplicate
    with pytest.raises(WorkflowError, match="conflicting duplicate"):
        case.execute(SubmitReviewInput(replace(submitted, summary="different"), "lead"))
    repo.submit.assert_not_called()
    repo.has_successful_ci.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "reviewer", "message"),
    [
        ({"task_id": 1}, "lead", "task_id must be a string"),
        ({"task_id": " "}, "lead", "unknown claim"),
        ({"claim_token": ""}, "lead", "unknown claim"),
        ({"reviewed_head_sha": "short"}, "lead", "full lowercase"),
        ({"review_round": True}, "lead", "review_round must be 1 or 2"),
        ({"review_round": 3}, "lead", "review_round must be 1 or 2"),
        ({"verdict": WorkflowStatus.IN_REVIEW}, "lead", "invalid review verdict"),
        ({"summary": ""}, "lead", "summary must be a nonempty string"),
        ({"findings": ({},)}, "lead", "findings must be a tuple of review findings"),
        ({"prior_resolutions": []}, "lead", "prior_resolutions must be a mapping"),
        ({"prior_resolutions": {1: "FIXED"}}, "lead", "invalid prior resolution"),
        ({"prior_resolutions": {"F": "unknown"}}, "lead", "invalid prior resolution"),
        ({"actual_effort": 3}, "lead", "actual_effort must be a string"),
        (
            {"verification_evidence": False},
            "lead",
            "verification_evidence must be a string",
        ),
        ({}, " ", "invalid reviewer"),
    ],
)
def test_invalid_typed_submission_does_not_open_scope(updates, reviewer, message):
    scope = Mock()
    case = SubmitReviewUseCase(scope, clock=Mock(), token=Mock(), require_ci=True)
    with pytest.raises(WorkflowError, match=message):
        case.execute(SubmitReviewInput(submission(**updates), reviewer))
    scope.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"finding_id": ""}, "finding_id must be a nonempty string"),
        ({"severity": "unknown"}, "invalid finding severity"),
        ({"line": False}, "finding line must be a positive integer"),
        ({"line": 0}, "finding line must be a positive integer"),
    ],
)
def test_invalid_typed_finding_does_not_open_scope(updates, message):
    scope = Mock()
    case = SubmitReviewUseCase(scope, clock=Mock(), token=Mock(), require_ci=True)
    with pytest.raises(WorkflowError, match=message):
        case.execute(
            SubmitReviewInput(
                submission(findings=(replace(FINDING, **updates),)), "lead"
            )
        )
    scope.assert_not_called()


def test_typed_submission_snapshots_prior_resolutions():
    resolutions = {"F": "FIXED"}
    submitted = submission(prior_resolutions=resolutions)
    resolutions["F"] = "NOT_FIXED"
    assert submitted.prior_resolutions == {"F": "FIXED"}
    with pytest.raises(TypeError):
        submitted.prior_resolutions["F"] = "NOT_VERIFIED"
