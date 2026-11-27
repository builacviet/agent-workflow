from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, Mock, call

import pytest
from test_review_writes import HEAD, TASK, submission

from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.application.review.use_cases.submit_review import (
    SubmitReviewUseCase,
)
from agent_workflow.application.review.use_cases.write_contracts import (
    ReviewFinding,
    SubmitReviewInput,
)
from agent_workflow.application.shared.unit_of_work import IUnitOfWork
from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.review.policy import ClaimFacts
from agent_workflow.domain.states import WorkflowStatus

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


def test_submit_duplicate_and_effort_gate():
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    task = replace(TASK, status="IN_REVIEW", verified_effort=True)
    repo.lock_task.return_value = task
    repo.has_successful_ci.return_value = True
    repo.prior_findings.return_value = set()
    repo.find_claim.return_value = ClaimFacts(
        "token", "lead", HEAD, 101, "ACTIVE", None
    )
    case = SubmitReviewUseCase(
        uow, repo, clock=lambda: 100, token=lambda: "review", require_ci=True
    )
    with pytest.raises(BusinessRuleError, match="effort cannot be verified"):
        case.execute(SubmitReviewInput(submission(), "lead"))
    repo.submit.assert_not_called()
    uow.commit.assert_not_called()
    verified = SubmitReviewUseCase(
        uow,
        repo,
        clock=lambda: 100,
        token=lambda: "review",
        require_ci=True,
        effort_verifier=lambda *_: ("medium", "verified"),
    )
    result = verified.execute(SubmitReviewInput(submission(), "lead"))
    assert result.actual_effort == "medium"
    assert repo.submit.call_count == 1
    uow.commit.assert_called_once_with()
    canonical = (FIXTURES / "review_submission_round_one.json").read_text(
        encoding="ascii"
    )
    repo.find_claim.return_value = ClaimFacts(
        "token", "lead", HEAD, 101, "SUBMITTED", canonical
    )
    assert case.execute(SubmitReviewInput(submission(), "lead")).duplicate
    assert repo.submit.call_count == 1
    uow.commit.assert_called_once_with()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"task": None}, "unknown authorized task"),
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
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    repo.lock_task.return_value = change.get("task", replace(TASK, status="IN_REVIEW"))
    repo.find_claim.return_value = change.get(
        "claim", ClaimFacts("token", "lead", HEAD, 101, "ACTIVE", None)
    )
    repo.has_successful_ci.return_value = change.get("ci", True)
    case = SubmitReviewUseCase(
        uow, repo, clock=lambda: 100, token=lambda: "review", require_ci=True
    )
    with pytest.raises(BusinessRuleError, match=message):
        case.execute(
            SubmitReviewInput(
                change.get("data", submission()), change.get("reviewer", "lead")
            )
        )
    repo.submit.assert_not_called()
    uow.commit.assert_not_called()


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

    with pytest.raises(BusinessRuleError, match=message):
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
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    repo.lock_task.return_value = replace(
        TASK, status=WorkflowStatus.IN_REVIEW, rounds=submitted.review_round - 1
    )
    repo.has_successful_ci.return_value = True
    repo.prior_findings.return_value = set(submitted.prior_resolutions)
    claim = ClaimFacts("token", "lead", HEAD, 101, "ACTIVE", None)
    repo.find_claim.return_value = claim
    calls = Mock()
    calls.attach_mock(repo, "repo")
    calls.attach_mock(uow, "uow")
    case = SubmitReviewUseCase(
        uow, repo, clock=lambda: 100, token=lambda: "review", require_ci=True
    )
    result = case.execute(SubmitReviewInput(submitted, "lead"))
    assert not result.duplicate
    assert repo.submit.call_args.args[2].encode("ascii") == historical
    assert calls.mock_calls[-3:] == [
        call.repo.submit(*repo.submit.call_args.args),
        call.uow.commit(),
        call.uow.__exit__(None, None, None),
    ]
    assert result.actual_effort is None

    calls.reset_mock()
    repo.find_claim.return_value = replace(
        claim, status="SUBMITTED", submitted_payload=historical.decode("ascii")
    )
    assert case.execute(SubmitReviewInput(submitted, "lead")).duplicate
    with pytest.raises(BusinessRuleError, match="conflicting duplicate"):
        case.execute(SubmitReviewInput(replace(submitted, summary="different"), "lead"))
    repo.submit.assert_not_called()
    uow.commit.assert_not_called()
    repo.has_successful_ci.assert_not_called()


@pytest.mark.parametrize(
    ("updates", "reviewer", "error_type", "message"),
    [
        (
            {"task_id": 1},
            "lead",
            InvalidApplicationRequestError,
            "task_id must be a string",
        ),
        ({"task_id": " "}, "lead", InvalidApplicationRequestError, "unknown claim"),
        ({"claim_token": ""}, "lead", InvalidApplicationRequestError, "unknown claim"),
        ({"reviewed_head_sha": "short"}, "lead", BusinessRuleError, "full lowercase"),
        (
            {"review_round": True},
            "lead",
            InvalidApplicationRequestError,
            "review_round must be 1 or 2",
        ),
        (
            {"review_round": 3},
            "lead",
            InvalidApplicationRequestError,
            "review_round must be 1 or 2",
        ),
        (
            {"verdict": WorkflowStatus.IN_REVIEW},
            "lead",
            InvalidApplicationRequestError,
            "invalid review verdict",
        ),
        (
            {"summary": ""},
            "lead",
            InvalidApplicationRequestError,
            "summary must be a nonempty string",
        ),
        (
            {"findings": ({},)},
            "lead",
            InvalidApplicationRequestError,
            "findings must be a tuple of review findings",
        ),
        (
            {"prior_resolutions": []},
            "lead",
            InvalidApplicationRequestError,
            "prior_resolutions must be a mapping",
        ),
        (
            {"prior_resolutions": {1: "FIXED"}},
            "lead",
            InvalidApplicationRequestError,
            "invalid prior resolution",
        ),
        (
            {"prior_resolutions": {"F": "unknown"}},
            "lead",
            InvalidApplicationRequestError,
            "invalid prior resolution",
        ),
        (
            {"actual_effort": 3},
            "lead",
            InvalidApplicationRequestError,
            "actual_effort must be a string",
        ),
        (
            {"verification_evidence": False},
            "lead",
            InvalidApplicationRequestError,
            "verification_evidence must be a string",
        ),
        ({}, " ", InvalidApplicationRequestError, "invalid reviewer"),
    ],
)
def test_invalid_typed_submission_does_not_open_scope(
    updates, reviewer, error_type, message
):
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    case = SubmitReviewUseCase(uow, repo, clock=Mock(), token=Mock(), require_ci=True)
    with pytest.raises(error_type, match=message):
        case.execute(SubmitReviewInput(submission(**updates), reviewer))
    uow.__enter__.assert_not_called()
    uow.commit.assert_not_called()
    assert repo.mock_calls == []


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
    uow = MagicMock(spec=IUnitOfWork)
    repo = Mock()
    case = SubmitReviewUseCase(uow, repo, clock=Mock(), token=Mock(), require_ci=True)
    with pytest.raises(InvalidApplicationRequestError, match=message):
        case.execute(
            SubmitReviewInput(
                submission(findings=(replace(FINDING, **updates),)), "lead"
            )
        )
    uow.__enter__.assert_not_called()
    uow.commit.assert_not_called()
    assert repo.mock_calls == []


def test_typed_submission_snapshots_prior_resolutions():
    resolutions = {"F": "FIXED"}
    submitted = submission(prior_resolutions=resolutions)
    resolutions["F"] = "NOT_FIXED"
    assert submitted.prior_resolutions == {"F": "FIXED"}
    with pytest.raises(TypeError):
        submitted.prior_resolutions["F"] = "NOT_VERIFIED"
