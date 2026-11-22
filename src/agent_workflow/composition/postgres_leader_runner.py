"""Explicit, one-iteration PostgreSQL leadership-gated orchestration.

The caller owns the factory, constructs a fresh composition for each lifecycle,
and supplies a trusted execution callback. This module never selects a CLI mode
or constructs a Worker/RuntimeHost.
"""

from __future__ import annotations

from collections.abc import Callable

from agent_workflow.application.ci.ports import ICiChecksReader
from agent_workflow.application.ci.use_cases.reconcile_ci import ReconcileCiUseCase
from agent_workflow.budget import dispatch_budget
from agent_workflow.composition.postgres_controller import (
    PostgresCiWatcherDependencies,
    PostgresControllerDependencies,
)
from agent_workflow.domain.ci_evidence import CiEvidence
from agent_workflow.domain.service import WorkflowService
from agent_workflow.infrastructure.ci.guarded_adapters import (
    GuardedCiChecks,
    GuardedCiState,
)
from agent_workflow.integrations.github import WorkflowError


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
    *,
    on_finished: Callable[[str], None] | None = None,
) -> bool:
    """Attempt one dispatch; an active slot is retained when ownership is lost.

    The trusted callback receives a guard for its internal material boundaries.
    It must use that guard around its own Git/Worker/DB effects. This runner
    additionally fences the callback as a whole and never retries it.
    Optional terminal cleanup is fenced after finish and before lock release;
    cleanup errors retain the committed terminal state for recovery.
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
        # Terminal state has committed. Cleanup failure must never rewrite it
        # as BLOCKED; retain the pin for explicit operator recovery instead.
        if on_finished is not None:
            try:
                _fenced(leadership, on_finished, claim["dispatch_id"])
            except Exception:
                raise WorkflowError(
                    "terminal dispatch cleanup unconfirmed; reconcile engine pin"
                ) from None
        return True
    finally:
        leadership.release()


def run_ci_watcher_once(
    dependencies: PostgresCiWatcherDependencies,
    checks: ICiChecksReader,
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

        def require_owner() -> None:
            _require_owner(leadership)

        coordinator = ReconcileCiUseCase(
            GuardedCiState(
                reader=dependencies.reader,
                recorder=dependencies.recorder,
                acknowledger=dependencies.acknowledger,
                require_owner=require_owner,
            ),
            GuardedCiChecks(checks, require_owner),
            installation_id=installation_id,
        )
        return _fenced(leadership, coordinator.execute, startup=startup)
    finally:
        leadership.release()
