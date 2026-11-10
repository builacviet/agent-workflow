"""V1-03A: read-only exact-PR-HEAD CI decision; no gate mutations.

The caller owns authenticated GitHub transport and durable failure history.
Webhook notifications are not authoritative CI evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal, Mapping, Protocol

from agent_workflow.integrations.github import WorkflowError, validate_sha

Verdict = Literal["SUCCESS", "FAILURE", "BLOCKED"]
_FAILURE_CONCLUSIONS = frozenset({"failure"})


class ReadOnlyChecks(Protocol):
    """Trusted GitHub transport; implementations must fetch all check-run pages."""

    installation_id: int

    def get_pull_request(self, repository: str, number: int) -> Mapping: ...

    def list_check_runs(self, repository: str, sha: str) -> Mapping: ...


@dataclass(frozen=True, slots=True)
class CiRequest:
    task_id: str
    repository: str
    installation_id: int
    pr_number: int
    expected_head_sha: str
    check_name: str
    check_app_slug: str = "github-actions"

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not self.task_id.strip():
            raise WorkflowError("CI task id required")
        if not isinstance(self.repository, str) or not self.repository.strip():
            raise WorkflowError("CI repository required")
        if type(self.installation_id) is not int or self.installation_id < 1:
            raise WorkflowError("CI installation id invalid")
        if type(self.pr_number) is not int or self.pr_number < 1:
            raise WorkflowError("CI PR number invalid")
        validate_sha(self.expected_head_sha)
        if not isinstance(self.check_name, str) or not self.check_name.strip():
            raise WorkflowError("CI check name required")
        if not isinstance(self.check_app_slug, str) or not self.check_app_slug.strip():
            raise WorkflowError("CI check source required")


@dataclass(frozen=True, slots=True)
class CiEvidence:
    task_id: str
    repository: str
    installation_id: int
    pr_number: int
    expected_head_sha: str
    observed_head_sha: str | None
    tested_sha: str | None
    check_name: str
    check_app_slug: str
    verdict: Verdict
    reason: str
    check_run_id: int | None
    workflow_run_id: int | None
    run_attempt: int | None
    completed_at: str | None
    observed_at: str

    def as_dict(self) -> dict:
        """Serialize the immutable observation, never an authorization token."""
        return asdict(self)


def _head(pr: object, repository: str, number: int) -> str | None:
    if not isinstance(pr, Mapping) or pr.get("number") != number:
        return None
    base, head = pr.get("base"), pr.get("head")
    if not isinstance(base, Mapping) or not isinstance(head, Mapping):
        return None
    repo = base.get("repo")
    if not isinstance(repo, Mapping) or repo.get("full_name") != repository:
        return None
    sha = head.get("sha")
    try:
        validate_sha(sha)
    except (WorkflowError, TypeError):
        return None
    return sha


def _check_run(run: object, sha: str, request: CiRequest) -> bool:
    if not isinstance(run, Mapping):
        return False
    app = run.get("app")
    return (
        run.get("name") == request.check_name
        and run.get("head_sha") == sha
        and isinstance(app, Mapping)
        and app.get("slug") == request.check_app_slug
        and type(run.get("id")) is int
        and run["id"] > 0
        and run.get("event") == "push"
        and run.get("workflow_path") == ".github/workflows/v1-ci.yml"
        and run.get("tested_sha") == sha
        and type(run.get("workflow_run_id")) is int
        and run["workflow_run_id"] > 0
        and type(run.get("run_attempt")) is int
        and run["run_attempt"] > 0
    )


def verify_ci(
    request: CiRequest,
    checks: ReadOnlyChecks,
    *,
    allowed_repository: str,
    allowed_check_names: frozenset[str],
    failure_history_complete: bool,
    previous_failure_for_sha: bool,
    observed_at: datetime | None = None,
) -> CiEvidence:
    """Fail closed; durable same-SHA failure history must be trustworthy.

    An unknown history cannot authorize SUCCESS. The future Controller/ledger
    must construct these booleans, never an untrusted worker or webhook.
    """
    if type(request) is not CiRequest:
        raise WorkflowError("invalid CI request")
    if request.repository != allowed_repository or request.check_name not in allowed_check_names:
        raise WorkflowError("unapproved CI repository or required check")
    if checks.installation_id != request.installation_id:
        raise WorkflowError("CI GitHub installation mismatch")
    if type(failure_history_complete) is not bool or type(previous_failure_for_sha) is not bool:
        raise WorkflowError("CI history must be trusted booleans")
    now = observed_at or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise WorkflowError("CI observation must be timezone-aware")
    seen_head = None
    tested = None
    check_id = None
    workflow_run_id = None
    run_attempt = None
    completed = None

    def evidence(verdict: Verdict, reason: str) -> CiEvidence:
        return CiEvidence(
            request.task_id, request.repository, request.installation_id,
            request.pr_number, request.expected_head_sha, seen_head, tested,
            request.check_name, request.check_app_slug, verdict, reason,
            check_id, workflow_run_id, run_attempt, completed, now.isoformat(),
        )

    try:
        seen_head = _head(
            checks.get_pull_request(request.repository, request.pr_number),
            request.repository, request.pr_number,
        )
        if seen_head is None:
            return evidence("BLOCKED", "PR_IDENTITY_UNVERIFIED")
        if seen_head != request.expected_head_sha:
            return evidence("BLOCKED", "PR_HEAD_CHANGED")
        result = checks.list_check_runs(request.repository, seen_head)
        if not isinstance(result, Mapping):
            return evidence("BLOCKED", "CHECKS_UNVERIFIED")
        all_runs, count = result.get("check_runs"), result.get("total_count")
        # Incomplete pagination could hide an earlier failed check.
        if (
            not isinstance(all_runs, list)
            or type(count) is not int
            or count != len(all_runs)
            or not all(isinstance(run, Mapping) for run in all_runs)
        ):
            return evidence("BLOCKED", "CHECK_HISTORY_INCOMPLETE")
        relevant = [run for run in all_runs if _check_run(run, seen_head, request)]
        if not relevant:
            return evidence("BLOCKED", "REQUIRED_CHECK_MISSING")
        if len({r["id"] for r in relevant}) != len(relevant):
            return evidence("BLOCKED", "CHECK_ID_AMBIGUOUS")
        if any(run.get("status") != "completed" for run in relevant):
            return evidence("BLOCKED", "CHECK_PENDING")
        if any(not isinstance(run.get("completed_at"), str) or not run["completed_at"] for run in relevant):
            return evidence("BLOCKED", "COMPLETION_UNVERIFIED")
        # Re-fetch HEAD after checks; a PR synchronize event can race this query.
        if _head(
            checks.get_pull_request(request.repository, request.pr_number),
            request.repository, request.pr_number,
        ) != seen_head:
            return evidence("BLOCKED", "PR_HEAD_CHANGED_DURING_QUERY")
        latest = max(relevant, key=lambda run: (run["completed_at"], run["id"]))
        tested = latest["tested_sha"]
        check_id, completed = latest["id"], latest["completed_at"]
        workflow_run_id, run_attempt = latest["workflow_run_id"], latest["run_attempt"]
        conclusions = {run.get("conclusion") for run in relevant}
        if not failure_history_complete:
            return evidence("BLOCKED", "DURABLE_HISTORY_UNVERIFIED")
        if previous_failure_for_sha or (
            "success" in conclusions and bool(conclusions & _FAILURE_CONCLUSIONS)
        ):
            return evidence("BLOCKED", "SAME_SHA_FAILURE_CONFLICT")
        if conclusions == {"success"}:
            return evidence("SUCCESS", "REQUIRED_CHECK_PASSED")
        if conclusions == {"failure"}:
            return evidence("FAILURE", "REQUIRED_CHECK_FAILED")
        return evidence("BLOCKED", "CHECK_CONCLUSION_UNVERIFIED")
    except (OSError, TimeoutError, KeyError, ValueError, TypeError, AttributeError):
        return evidence("BLOCKED", "GITHUB_READ_UNAVAILABLE")
