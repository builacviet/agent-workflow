"""Layer: Domain
Responsibility: Enforce review eligibility and verdict invariants.
Dependencies: Domain errors and immutable review facts.
"""

from dataclasses import dataclass

from agent_workflow.domain.exceptions import BusinessRuleError
from agent_workflow.domain.states import WorkflowStatus


@dataclass(frozen=True)
class TaskFacts:
    task_id: str
    status: WorkflowStatus
    head: str | None
    base: str
    rounds: int
    effort: str
    verified_effort: bool


@dataclass(frozen=True)
class ClaimFacts:
    token: str
    reviewer: str
    expected_sha: str
    expires_at: float
    status: str
    submitted_payload: str | None


def require_ci(required: bool, handoff: str | None, success: bool) -> bool:
    return (not required and handoff != "CI_REPAIR_HANDOFF") or success


def claim_allowed(task: TaskFacts, sha: str, ci_ok: bool, active: bool) -> None:
    if task.status != WorkflowStatus.READY_IN_REVIEW or task.rounds >= 2 or active:
        raise BusinessRuleError("task is not reviewable")
    if sha != task.head:
        raise BusinessRuleError("stale claim SHA")
    if not ci_ok:
        raise BusinessRuleError("verified exact-HEAD CI is required before review")


def submission_allowed(
    task: TaskFacts,
    claim: ClaimFacts | None,
    reviewer: str,
    now: float,
    canonical: str,
    sha: str,
    round_number: int,
    effort: str,
    ci_ok: bool,
) -> bool:
    if claim is None or claim.reviewer != reviewer:
        raise BusinessRuleError("unknown claim or claim owner mismatch")
    if claim.status == "SUBMITTED":
        if claim.submitted_payload != canonical:
            raise BusinessRuleError("conflicting duplicate submission")
        return True
    if claim.status != "ACTIVE" or claim.expires_at <= now:
        raise BusinessRuleError("claim expired; reclaim without consuming a round")
    if task.status != WorkflowStatus.IN_REVIEW or task.rounds >= 2:
        raise BusinessRuleError("task is not in an active review")
    if sha != task.head or sha != claim.expected_sha:
        raise BusinessRuleError("stale submission SHA")
    if not ci_ok:
        raise BusinessRuleError(
            "verified exact-HEAD CI is required for review submission"
        )
    if round_number != task.rounds + 1 or effort != task.effort:
        raise BusinessRuleError("round or requested effort mismatch")
    return False


def verdict_allowed(
    round_number: int,
    verdict: str,
    prior: set[str],
    resolutions: dict[str, str],
    new_ids: list[str],
) -> None:
    if set(resolutions) != prior:
        raise BusinessRuleError("all prior findings require explicit resolution")
    if len(set(new_ids)) != len(new_ids) or prior.intersection(new_ids):
        raise BusinessRuleError("finding identities must be new and unique")
    failure = (
        WorkflowStatus.CHANGES_REQUESTED
        if round_number == 1
        else WorkflowStatus.ESCALATED
    )
    if verdict not in {WorkflowStatus.APPROVED, failure}:
        raise BusinessRuleError("verdict violates two-round policy")
    unresolved = any(value != "FIXED" for value in resolutions.values())
    if verdict == WorkflowStatus.APPROVED and (new_ids or unresolved):
        raise BusinessRuleError(
            "approval requires no new findings and all prior findings fixed"
        )
    if verdict != WorkflowStatus.APPROVED and not new_ids and not unresolved:
        raise BusinessRuleError(
            "failure verdict requires a code finding; operational limitations are not verdicts"
        )


def effort_allowed(
    task: TaskFacts, verdict: str, actual: str | None, evidence: str
) -> None:
    if (
        verdict == WorkflowStatus.APPROVED
        and task.verified_effort
        and (actual != task.effort or not evidence or evidence == "unverified")
    ):
        raise BusinessRuleError(
            "required effort cannot be verified; approval blocked without consuming a round"
        )
