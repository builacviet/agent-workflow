from types import TracebackType
from typing import Self

import pytest

from agent_workflow.application.shared.unit_of_work import IUnitOfWork


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.committed = False
        self.commit_requested = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.committed = exc_type is None and self.commit_requested

    def commit(self) -> None:
        self.commit_requested = True

    def rollback(self) -> None:
        self.commit_requested = False


@pytest.mark.parametrize("valid", [True, False])
def test_application_can_decide_commit_against_a_pure_protocol(valid: bool) -> None:
    fake = FakeUnitOfWork()
    unit: IUnitOfWork = fake

    with unit as transaction:
        if valid:
            transaction.commit()
        else:
            transaction.rollback()
        assert not fake.committed

    assert fake.committed is valid
