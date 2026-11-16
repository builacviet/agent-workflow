"""Explicit, one-iteration PostgreSQL leadership-gated orchestration.

The caller owns the factory, constructs a fresh composition for each lifecycle,
and supplies a trusted execution callback. This module never selects a CLI mode
or constructs a Worker/RuntimeHost.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.postgres_controller import (
    PostgresCiWatcherDependencies,
    PostgresControllerDependencies,
)
from agent_workflow.domain.ci_evidence import CiEvidence, WebhookNotification
from agent_workflow.domain.service import WorkflowService
from agent_workflow.integrations.ci_controller import ControllerCiCoordinator
from agent_workflow.integrations.ci_gate import ReadOnlyChecks
from agent_workflow.integrations.github import WorkflowError
from agent_workflow.storage.postgres_ci_state import PostgresControllerCiState


def _require_owner(leadership: object) -> None:
    if leadership.is_held() is not True:
        raise PermissionError("PostgreSQL leadership lost; reconcile in-flight work")


def _fenced(leadership: object, operation: Callable, *args: object, **kwargs: object):
    _require_owner(leadership)
    result = operation(*args, **kwargs)
    _require_owner(leadership)
    return result


def run_controller_once(
    dependencies: PostgresControllerDependencies,
    assignment: WorkflowService,
    task_ids: set[str],
    execute: Callable[[dict, dict, Callable[[], None]], str],
    timeout: int = 1800,
) -> bool:
    """Attempt one dispatch; an active slot is retained when ownership is lost.

    The trusted callback receives a guard for its internal material boundaries.
    It must use that guard around its own Git/Worker/DB effects. This runner
    additionally fences the callback as a whole and never retries it.
    """
    if assignment.repository is not dependencies.workflow:
        raise TypeError(
            "Controller assignment must use the composed PostgreSQL workflow"
        )
    leadership = dependencies.leadership
    try:
        if not leadership.try_acquire():
            return False

        def guard() -> None:
            _require_owner(leadership)

        claim = _fenced(
            leadership,
            dependencies.dispatch.claim,
            task_ids,
            timeout,
            track_pre_launch=True,
        )
        if claim is None:
            return False
        try:
            with dispatch_budget(claim["deadline"]):
                context = _fenced(
                    leadership, assignment.get_coder_assignment, claim["task_id"]
                )
                if (
                    context["completed_review_rounds"] != claim["round_number"]
                    or context["status"]
                    not in {"AWAITING_HANDOFF", "CHANGES_REQUESTED"}
                    or (context["current_head_sha"] or context["base_sha"])
                    != claim["input_sha"]
                ):
                    raise WorkflowError("assignment changed after dispatch")
                sha = _fenced(leadership, execute, claim, context, guard)
                _fenced(
                    leadership, dependencies.dispatch.finish, claim["dispatch_id"], sha
                )
        except BaseException:
            # A lost owner must not even write BLOCKED. The slot stays active
            # for an operator to reconcile uncertain external effects.
            if leadership.is_held() is True:
                _fenced(leadership, dependencies.dispatch.block, claim["dispatch_id"])
            raise
        return True
    finally:
        leadership.release()


class _GuardedCiState:
    def __init__(self, state: PostgresControllerCiState, leadership: object) -> None:
        self._state, self._leadership = state, leadership

    def pending_events(self, limit: int = 20) -> list[WebhookNotification]:
        return _fenced(self._leadership, self._state.pending_events, limit)

    def tracked(self, limit: int = 32) -> list[dict]:
        return _fenced(self._leadership, self._state.tracked, limit)

    def previous_failure(self, task_id: str, head_sha: str) -> bool:
        return _fenced(
            self._leadership, self._state.previous_failure, task_id, head_sha
        )

    def record(self, evidence: CiEvidence) -> None:
        _fenced(self._leadership, self._state.record, evidence)

    def mark_processed(self, delivery_id: str) -> None:
        _fenced(self._leadership, self._state.mark_processed, delivery_id)


class _GuardedChecks:
    def __init__(self, checks: ReadOnlyChecks, leadership: object) -> None:
        self._checks, self._leadership = checks, leadership
        self.installation_id = checks.installation_id

    def get_pull_request(self, repository: str, number: int) -> Mapping:
        return _fenced(
            self._leadership, self._checks.get_pull_request, repository, number
        )

    def list_check_runs(self, repository: str, sha: str) -> Mapping:
        return _fenced(self._leadership, self._checks.list_check_runs, repository, sha)


def run_ci_watcher_once(
    dependencies: PostgresCiWatcherDependencies,
    checks: ReadOnlyChecks,
    *,
    installation_id: int,
    startup: bool = False,
) -> tuple[CiEvidence, ...] | None:
    """Reconcile one inbox iteration after independent CI watcher acquisition.

    None means acquisition was denied. No caller-owned factory is closed.
    """
    leadership = dependencies.leadership
    try:
        if not leadership.try_acquire():
            return None
        _require_owner(leadership)
        coordinator = ControllerCiCoordinator(
            _GuardedCiState(dependencies.ci_state, leadership),
            _GuardedChecks(checks, leadership),
            installation_id=installation_id,
        )
        return _fenced(leadership, coordinator.reconcile_once, startup=startup)
    finally:
        leadership.release()
