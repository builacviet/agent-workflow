"""Read the MCP review queue through the existing PostgreSQL workflow unit."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from time import time

from agent_workflow.application.review.dtos import ReviewQueueItem
from agent_workflow.application.review.ports import ReviewQueueOperations
from agent_workflow.domain.states import WorkflowStatus
from agent_workflow.domain.workflow_identity import canonical_repository
from agent_workflow.storage.postgres_workflow import (
    PostgresWorkflowRepository,
    PostgresWorkflowUnit,
)


class PostgresReviewQueueReader:
    def __init__(self, unit: PostgresWorkflowUnit, *, require_ci: bool) -> None:
        self._unit = unit
        self._require_ci = require_ci

    def read(self, requested_effort: str | None) -> tuple[ReviewQueueItem, ...]:
        return tuple(
            ReviewQueueItem(
                task_id=task["task_id"],
                title=json.loads(task["metadata"])["title"],
                repository=canonical_repository(task["repository"]),
                branch=task["branch"],
                head_sha=task["current_head_sha"],
                requested_effort=task["requested_effort"],
                review_round=task["completed_review_rounds"] + 1,
            )
            for task in self._unit.list_ready_tasks()
            if (
                requested_effort is None or task["requested_effort"] == requested_effort
            )
            and self._ci_allows(task["task_id"], task["current_head_sha"])
        )

    def _ci_allows(self, task_id: str, head_sha: str | None) -> bool:
        if not self._require_ci:
            handoff = self._unit.find_head_handoff(task_id, head_sha)
            if not handoff or handoff["action"] != "CI_REPAIR_HANDOFF":
                return True
        return self._unit.ci_approved((task_id, head_sha))


def _recover_expired_claims(unit: PostgresWorkflowUnit) -> None:
    # The workflow unit locks these task rows in task ID order.
    for task in unit.list_claimed_tasks():
        task_id = task["task_id"]
        claim = unit.find_active_claim((task_id,))
        if claim and claim["expires_at"] <= time():
            unit.expire_claim((claim["token"],))
            now = time()
            ready = WorkflowStatus.READY_IN_REVIEW
            unit.insert_event(
                (
                    task_id,
                    task["status"],
                    ready,
                    "LEASE_EXPIRED",
                    task["current_head_sha"],
                    now,
                    "{}",
                )
            )
            unit.set_status((ready, now, task_id))


class PostgresReviewQueueScope:
    def __init__(
        self, repository: PostgresWorkflowRepository, *, require_ci: bool
    ) -> None:
        self._repository = repository
        self._require_ci = require_ci

    @contextmanager
    def __call__(self) -> Iterator[ReviewQueueOperations]:
        # A queue request has always included lease recovery in its transaction.
        with self._repository.transaction() as unit:
            reader = PostgresReviewQueueReader(unit, require_ci=self._require_ci)

            def recover_expired_claims() -> None:
                _recover_expired_claims(unit)

            yield recover_expired_claims, reader
