"""Layer: Infrastructure
Responsibility: Read GitHub PRs and verify the provenance of Controller CI checks.
Dependencies: Domain workflow identity, CI wire types and injected GitHub JSON requests.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from agent_workflow.domain.workflow_identity import (
    REPOSITORY,
    WorkflowError,
    validate_sha,
)
from agent_workflow.infrastructure.ci.github_ci_types import (
    DECISIVE_CONCLUSIONS,
    GitHubCheckConclusion,
    GitHubRunStatus,
)

_MAX_CHECKS = 2_000
_MAX_ACTIONS_RUNS = 2_000
_MAX_ACTIONS_JOBS = 2_000
_REQUIRED_WORKFLOW = ".github/workflows/v1-ci.yml"
_REQUIRED_JOB = "Python tests"


class ControllerChecksClient:
    """Implement ICiChecksReader with full pagination and exact push provenance."""

    def __init__(
        self,
        *,
        installation_id: int,
        token_provider: Callable[[], str],
        request_json: Callable[[str, str, str], Mapping],
        repository: str = REPOSITORY,
    ):
        if type(installation_id) is not int or installation_id <= 0:
            raise ValueError("invalid trusted GitHub installation")
        if repository != REPOSITORY:
            raise ValueError(
                "Controller may read only the configured workflow repository"
            )
        self.installation_id = installation_id
        self._token_provider = token_provider
        self._request_json = request_json

    def _request(self, method: str, path: str) -> Mapping:
        try:
            token = self._token_provider()
            if not isinstance(token, str) or not token:
                raise ValueError("empty installation token")
            result = self._request_json(method, path, token)
            if not isinstance(result, Mapping):
                raise ValueError("GitHub API returned non-object")
            return result
        except (ValueError, TypeError, KeyError):
            raise OSError("trusted GitHub API read unavailable") from None

    def get_pull_request(self, repository: str, number: int) -> Mapping:
        if repository != REPOSITORY or type(number) is not int or number <= 0:
            raise OSError("unapproved GitHub PR read")
        return self._request("GET", f"/repos/{repository}/pulls/{number}")

    def _pages(self, path: str, key: str, cap: int) -> list[Mapping]:
        """Fail closed unless every server-advertised page is retrieved."""
        entries: list[Mapping] = []
        total: int | None = None
        for page in range(1, cap // 100 + 1):
            suffix = "&" if "?" in path else "?"
            payload = self._request(
                "GET",
                f"{path}{suffix}per_page=100&page={page}",
            )
            count, chunk = payload.get("total_count"), payload.get(key)
            if (
                type(count) is not int
                or count < 0
                or count > cap
                or not isinstance(chunk, list)
                or len(chunk) > 100
                or any(not isinstance(item, Mapping) for item in chunk)
                or (total is not None and total != count)
            ):
                raise OSError("GitHub CI pagination incomplete")
            total = count
            entries.extend(chunk)
            if len(entries) == total:
                return entries
            if not chunk or len(entries) > total:
                raise OSError("GitHub CI pagination incomplete")
        raise OSError("GitHub CI exceeded bounded pagination")

    def list_check_runs(self, repository: str, sha: str) -> Mapping:
        """Join ALL Checks history with ALL push Actions runs and job attempts.

        A pull_request check on a synthetic merge is not evidence for a push.
        The job identity, SHA, suite, attempt and conclusion must agree across
        two independently queried GitHub API endpoint families.
        """
        if repository != REPOSITORY:
            raise OSError("unapproved GitHub checks repository")
        try:
            validate_sha(sha)
        except WorkflowError:
            raise OSError("unapproved GitHub checks SHA") from None

        checks = self._pages(
            f"/repos/{repository}/commits/{sha}/check-runs?filter=all",
            "check_runs",
            _MAX_CHECKS,
        )
        check_by_id: dict[int, Mapping] = {}
        for check in checks:
            check_id = check.get("id")
            if type(check_id) is not int or check_id <= 0 or check_id in check_by_id:
                raise OSError("GitHub CI check identity ambiguous")
            check_by_id[check_id] = check

        runs = self._pages(
            f"/repos/{repository}/actions/runs?head_sha={sha}&event=push",
            "workflow_runs",
            _MAX_ACTIONS_RUNS,
        )
        verified: list[dict] = []
        seen_jobs: set[int] = set()
        for run in runs:
            if run.get("path") != _REQUIRED_WORKFLOW:
                continue
            run_id, suite, attempt = (
                run.get("id"),
                run.get("check_suite_id"),
                run.get("run_attempt"),
            )
            repo = run.get("repository")
            if (
                type(run_id) is not int
                or run_id <= 0
                or type(suite) is not int
                or suite <= 0
                or type(attempt) is not int
                or attempt <= 0
                or run.get("event") != "push"
                or run.get("head_sha") != sha
                or run.get("name") != "V1 CI"
                or not isinstance(repo, Mapping)
                or repo.get("full_name") != repository
                or run.get("status") != GitHubRunStatus.COMPLETED
            ):
                raise OSError("trusted push workflow provenance unavailable")

            jobs = self._pages(
                f"/repos/{repository}/actions/runs/{run_id}/jobs?filter=all",
                "jobs",
                _MAX_ACTIONS_JOBS,
            )
            latest_required_job = False
            for job in jobs:
                if job.get("name") != _REQUIRED_JOB:
                    continue
                job_id, job_attempt = job.get("id"), job.get("run_attempt")
                if (
                    type(job_id) is not int
                    or job_id <= 0
                    or job_id in seen_jobs
                    or type(job_attempt) is not int
                    or not 0 < job_attempt <= attempt
                    or job.get("run_id") != run_id
                    or job.get("head_sha") != sha
                    or job.get("workflow_name") != "V1 CI"
                ):
                    raise OSError("trusted push job provenance unavailable")
                seen_jobs.add(job_id)
                check = check_by_id.get(job_id)
                check_suite = check.get("check_suite") if check else None
                check_app = check.get("app") if check else None
                if (
                    not isinstance(check, Mapping)
                    or not isinstance(check_suite, Mapping)
                    or check_suite.get("id") != suite
                    or not isinstance(check_app, Mapping)
                    or check_app.get("slug") != "github-actions"
                    or check.get("name") != _REQUIRED_JOB
                    or check.get("head_sha") != sha
                    or check.get("status") != job.get("status")
                    or check.get("conclusion") != job.get("conclusion")
                ):
                    raise OSError("trusted push job/check identity mismatch")
                if job.get("status") != GitHubRunStatus.COMPLETED:
                    raise OSError("trusted push job not completed")
                conclusion = job.get("conclusion")
                if job_attempt == attempt:
                    latest_required_job = True
                    # The latest run must not reuse an older successful job.
                    # Check before skipping cancelled/timed-out/skipped jobs.
                    if (
                        run["conclusion"] in DECISIVE_CONCLUSIONS
                        and conclusion != run["conclusion"]
                    ):
                        raise OSError("latest push job/workflow verdict mismatch")
                if conclusion not in DECISIVE_CONCLUSIONS:
                    # Cancelled/timed-out/skipped jobs are not verdict evidence.
                    # Validate their identity above, but do not poison all
                    # later successful attempts on the same immutable SHA.
                    continue
                if run["conclusion"] not in DECISIVE_CONCLUSIONS:
                    # A cancelled latest run can still contain a previously
                    # completed failed job. Keep that failure sticky, but
                    # never promote a success from a non-verdict workflow.
                    if conclusion != GitHubCheckConclusion.FAILURE:
                        continue
                verified.append(
                    {
                        **check,
                        "event": "push",
                        "workflow_path": _REQUIRED_WORKFLOW,
                        "workflow_run_id": run_id,
                        "run_attempt": job_attempt,
                        "tested_sha": job["head_sha"],
                    }
                )
            if run["conclusion"] in DECISIVE_CONCLUSIONS and not latest_required_job:
                raise OSError("trusted push workflow missing latest required job")
        return {"total_count": len(verified), "check_runs": verified}
