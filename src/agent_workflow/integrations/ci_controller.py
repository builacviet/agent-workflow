"""Controller-side local inbox → trusted read-only CI reconciliation.

Webhook receipt is a wake-up hint only. All gate verdicts come from a fresh
PR head + complete Checks API read bound to task/PR/HEAD.
"""

from __future__ import annotations

from agent_workflow.domain.ci_evidence import ControllerCiStore
from agent_workflow.integrations.ci_gate import (
    CiEvidence,
    CiRequest,
    ReadOnlyChecks,
    verify_ci,
)
from agent_workflow.integrations.github import REPOSITORY, WorkflowError


class ControllerCiCoordinator:
    def __init__(
        self,
        state: ControllerCiStore,
        checks: ReadOnlyChecks,
        *,
        installation_id: int,
        check_name: str = "Python tests",
    ):
        if type(installation_id) is not int or installation_id <= 0:
            raise ValueError("invalid Controller installation ID")
        if checks.installation_id != installation_id:
            raise WorkflowError("Controller checks installation differs from App")
        if check_name != "Python tests":
            raise WorkflowError("only approved V1 check may pass CI")
        self.state = state
        self.checks = checks
        self.installation_id = installation_id
        self.check_name = check_name

    def observe_all(self) -> tuple[CiEvidence, ...]:
        """Reconcile only tracked, ready tasks; bounded to 32 per invocation."""
        observed = []
        for tracked in self.state.tracked():
            request = CiRequest(
                task_id=tracked["task_id"],
                repository=REPOSITORY,
                installation_id=self.installation_id,
                pr_number=tracked["pr_number"],
                expected_head_sha=tracked["head_sha"],
                check_name=self.check_name,
            )
            evidence = verify_ci(
                request,
                self.checks,
                allowed_repository=REPOSITORY,
                allowed_check_names=frozenset({self.check_name}),
                failure_history_complete=True,
                previous_failure_for_sha=self.state.previous_failure(
                    request.task_id,
                    request.expected_head_sha,
                ),
            )
            self.state.record(evidence)
            observed.append(evidence)
        return tuple(observed)

    def reconcile_once(self, *, startup: bool = False) -> tuple[CiEvidence, ...]:
        """Reconcile all pending notifications once, not once per webhook row.

        An unavailable GitHub API leaves events pending for future retries.
        Startup can recheck tracked work even after a crash before inbox wake.
        No periodic GitHub polling is used when the inbox is empty.
        """
        notifications = self.state.pending_events()
        if not notifications and not startup:
            return ()
        for event in notifications:
            if (
                event.repository != REPOSITORY
                or event.installation_id != self.installation_id
                or (event.event, event.action)
                not in {
                    ("pull_request", "synchronize"),
                    ("workflow_run", "completed"),
                }
            ):
                raise WorkflowError("CI inbox has incompatible notification")
        observations = self.observe_all()
        # BLOCKED means CI evidence is not terminal, even when GitHub was
        # reachable. Keep the signed wake-up until a fresh observation decides
        # SUCCESS or FAILURE; checks may appear after the webhook arrives.
        if any(e.verdict == "BLOCKED" for e in observations):
            return observations
        for notification in notifications:
            self.state.mark_processed(notification.delivery_id)
        return observations
