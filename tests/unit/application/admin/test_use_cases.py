"""Transaction and validation checks for the fixture use cases."""

from unittest.mock import MagicMock, Mock, call

import pytest

from agent_workflow.application.admin.use_cases import (
    AdminSeedCommand,
    ImportHistoricalReviewsUseCase,
    ResetSyntheticReviewUseCase,
    SeedSyntheticReviewUseCase,
)
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.domain.task.authorization import TaskAuthorizationSpec
from agent_workflow.domain.workflow_identity import REPOSITORY


def spec(task_id, synthetic):
    return TaskAuthorizationSpec(
        task_id=task_id,
        title="Fixture",
        description="Fixture",
        acceptance_criteria=("Fixture",),
        definition_of_done="Fixture",
        repository=REPOSITORY,
        branch="fixture",
        base_sha="a" * 40,
        requested_effort="high",
        risk_level="low",
        context_paths=("AGENTS.md",),
        synthetic=synthetic,
    )


@pytest.mark.parametrize(
    ("case", "task_id", "synthetic", "method"),
    [
        (ImportHistoricalReviewsUseCase, "TASK-001", False, "import_history"),
        (SeedSyntheticReviewUseCase, "POC-REVIEW-001", True, "seed_synthetic"),
        (ResetSyntheticReviewUseCase, "POC-REVIEW-001", True, "reset_synthetic"),
    ],
)
def test_use_cases_commit_after_success_and_reject_wrong_identity(
    case, task_id, synthetic, method
):
    uow = MagicMock()
    repo = Mock()
    command = AdminSeedCommand(spec(task_id, synthetic))
    case(uow, repo).execute(command)
    getattr(repo, method).assert_called_once_with(command.spec)
    uow.commit.assert_called_once()
    uow.reset_mock()
    with pytest.raises(InvalidApplicationRequestError):
        case(uow, repo).execute(AdminSeedCommand(spec("other", synthetic)))
    uow.__enter__.assert_not_called()


def test_reset_reseeds_in_same_scope_and_failure_does_not_commit():
    uow = MagicMock()
    repo = Mock()
    command = AdminSeedCommand(spec("POC-REVIEW-001", True))
    repo.seed_synthetic.side_effect = RuntimeError("failure")
    with pytest.raises(RuntimeError):
        ResetSyntheticReviewUseCase(uow, repo).execute(command)
    assert repo.mock_calls == [
        call.reset_synthetic(command.spec),
        call.seed_synthetic(command.spec),
    ]
    uow.commit.assert_not_called()
    assert uow.__exit__.call_args.args[0] is RuntimeError
