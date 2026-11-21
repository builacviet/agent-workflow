"""Trusted GitHub Checks verification; Application service used by reconciliation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone

from agent_workflow.application.ci.dtos import CiRequest
from agent_workflow.application.ci.ports import ICiChecksReader
from agent_workflow.application.exceptions import InvalidApplicationRequestError
from agent_workflow.domain.ci_evidence import CiEvidence, Verdict
from agent_workflow.domain.exceptions import DomainError
from agent_workflow.domain.workflow_identity import validate_sha


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
    except (DomainError, TypeError):
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
    checks: ICiChecksReader,
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
        raise InvalidApplicationRequestError("invalid CI request")
    if (
        request.repository != allowed_repository
        or request.check_name not in allowed_check_names
    ):
        raise InvalidApplicationRequestError("unapproved CI repository or required check")
    if checks.installation_id != request.installation_id:
        raise InvalidApplicationRequestError("CI GitHub installation mismatch")
    if (
        type(failure_history_complete) is not bool
        or type(previous_failure_for_sha) is not bool
    ):
        raise InvalidApplicationRequestError("CI history must be trusted booleans")
    now = observed_at or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise InvalidApplicationRequestError("CI observation must be timezone-aware")
    seen_head = None
    tested = None
    check_id = None
    workflow_run_id = None
    run_attempt = None
    completed = None

    def evidence(verdict: Verdict, reason: str) -> CiEvidence:
        return CiEvidence(
            request.task_id,
            request.repository,
            request.installation_id,
            request.pr_number,
            request.expected_head_sha,
            seen_head,
            tested,
            request.check_name,
            request.check_app_slug,
            verdict,
            reason,
            check_id,
            workflow_run_id,
            run_attempt,
            completed,
            now.isoformat(),
        )

    try:
        seen_head = _head(
            checks.get_pull_request(request.repository, request.pr_number),
            request.repository,
            request.pr_number,
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
        if any(
            not isinstance(run.get("completed_at"), str) or not run["completed_at"]
            for run in relevant
        ):
            return evidence("BLOCKED", "COMPLETION_UNVERIFIED")
        # Re-fetch HEAD after checks; a PR synchronize event can race this query.
        if (
            _head(
                checks.get_pull_request(request.repository, request.pr_number),
                request.repository,
                request.pr_number,
            )
            != seen_head
        ):
            return evidence("BLOCKED", "PR_HEAD_CHANGED_DURING_QUERY")
        latest = max(relevant, key=lambda run: (run["completed_at"], run["id"]))
        tested = latest["tested_sha"]
        check_id, completed = latest["id"], latest["completed_at"]
        workflow_run_id, run_attempt = latest["workflow_run_id"], latest["run_attempt"]
        conclusions = {run.get("conclusion") for run in relevant}
        if not failure_history_complete:
            return evidence("BLOCKED", "DURABLE_HISTORY_UNVERIFIED")
        if previous_failure_for_sha or (
            "success" in conclusions and "failure" in conclusions
        ):
            return evidence("BLOCKED", "SAME_SHA_FAILURE_CONFLICT")
        if conclusions == {"success"}:
            return evidence("SUCCESS", "REQUIRED_CHECK_PASSED")
        if conclusions == {"failure"}:
            return evidence("FAILURE", "REQUIRED_CHECK_FAILED")
        return evidence("BLOCKED", "CHECK_CONCLUSION_UNVERIFIED")
    except (PermissionError, DomainError):
        # Leadership fencing and domain errors are never GitHub API outages.
        raise
    except (OSError, TimeoutError, KeyError, ValueError, TypeError, AttributeError):
        return evidence("BLOCKED", "GITHUB_READ_UNAVAILABLE")
