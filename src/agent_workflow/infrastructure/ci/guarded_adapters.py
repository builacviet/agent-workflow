"""Leadership-fenced outbound CI adapters implementing Application ports."""

from collections.abc import Callable, Mapping

from agent_workflow.application.ci.dtos import CiTrackedTaskSnapshot
from agent_workflow.application.ci.ports import ICiChecksReader, ICiReconciliationReader
from agent_workflow.domain.ci_evidence import WebhookNotification


class GuardedCiReader:
    def __init__(
        self,
        *,
        reader: ICiReconciliationReader,
        require_owner: Callable[[], None],
    ) -> None:
        self._reader = reader
        self._require_owner = require_owner

    def _call(self, operation: Callable, *args: object):
        self._require_owner()
        result = operation(*args)
        self._require_owner()
        return result

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        return self._call(self._reader.pending_events, limit)

    def tracked(self, limit: int = 32) -> tuple[CiTrackedTaskSnapshot, ...]:
        return self._call(self._reader.tracked, limit)

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        return self._call(self._reader.previous_failure, task_id, head_sha)


class GuardedCiChecks:
    def __init__(
        self, checks: ICiChecksReader, require_owner: Callable[[], None]
    ) -> None:
        self._checks, self._require_owner = checks, require_owner
        self.installation_id = checks.installation_id

    def _call(self, operation: Callable, *args: object):
        self._require_owner()
        result = operation(*args)
        self._require_owner()
        return result

    def get_pull_request(self, repository: str, number: int) -> Mapping:
        return self._call(self._checks.get_pull_request, repository, number)

    def list_check_runs(self, repository: str, sha: str) -> Mapping:
        return self._call(self._checks.list_check_runs, repository, sha)
