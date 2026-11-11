"""Offline V1-03C GitHub App adapter and exact-SHA pagination checks."""
import pytest

from agent_workflow.integrations.ci_gate import CiRequest, verify_ci
from agent_workflow.integrations.github_app_checks import (
    ControllerAppToken, ControllerChecksClient,
)

SHA = "a" * 40
REPO = "builacviet/agent-workflow"


def test_controller_app_config_requires_ids_and_absolute_pem(tmp_path):
    with pytest.raises(ValueError):
        ControllerAppToken(app_id=0, installation_id=12, pem_path=tmp_path / "key.pem")
    with pytest.raises(ValueError):
        ControllerAppToken(app_id=1, installation_id=12, pem_path="key.pem")


def test_app_adapter_paginates_all_checks_before_verdict():
    calls = []

    def api(method, path, token):
        assert token == "installation-token"
        calls.append((method, path))
        if "/pulls/" in path:
            return {
                "number": 3,
                "base": {"repo": {"full_name": REPO}},
                "head": {"sha": SHA},
            }
        if "/actions/runs?" in path:
            return {"total_count": 1, "workflow_runs": [{
                "id": 456, "check_suite_id": 456, "run_attempt": 1,
                "event": "push", "head_sha": SHA, "name": "V1 CI",
                "path": ".github/workflows/v1-ci.yml",
                "repository": {"full_name": REPO},
                "status": "completed", "conclusion": "success",
            }]}
        page = int(path.split("page=")[-1])
        chunk = list(range(100)) if page == 1 else [100]
        if "/actions/runs/456/jobs?" in path:
            return {"total_count": 101, "jobs": [
                {"id": n + 1, "name": "Python tests", "head_sha": SHA,
                 "run_id": 456, "run_attempt": 1, "workflow_name": "V1 CI",
                 "status": "completed", "conclusion": "success"}
                for n in chunk
            ]}
        assert "/check-runs?" in path and "filter=all" in path
        return {"total_count": 101, "check_runs": [
            {
                "id": n + 1, "name": "Python tests", "head_sha": SHA,
                "check_suite": {"id": 456},
                "app": {"slug": "github-actions"}, "status": "completed",
                "conclusion": "success", "completed_at": "2026-11-10T00:00:00Z",
            }
            for n in chunk
        ]}

    checks = ControllerChecksClient(
        installation_id=17,
        token_provider=lambda: "installation-token",
        request_json=api,
    )
    evidence = verify_ci(
        CiRequest("task-1", REPO, 17, 3, SHA, "Python tests"),
        checks,
        allowed_repository=REPO,
        allowed_check_names=frozenset({"Python tests"}),
        failure_history_complete=True,
        previous_failure_for_sha=False,
    )
    assert evidence.verdict == "SUCCESS"
    assert len([path for _, path in calls if "/check-runs?" in path]) == 2
    assert len([path for _, path in calls if "/jobs?" in path]) == 2
    assert evidence.workflow_run_id == 456 and evidence.run_attempt == 1


def test_incomplete_page_cannot_mark_ci_success():
    calls = []

    def api(method, path, token):
        calls.append(path)
        if "/pulls/" in path:
            return {"number": 3, "base": {"repo": {"full_name": REPO}},
                    "head": {"sha": SHA}}
        return {"total_count": 101, "check_runs": []}

    checks = ControllerChecksClient(
        installation_id=17,
        token_provider=lambda: "tok",
        request_json=api,
    )
    evidence = verify_ci(
        CiRequest("task-1", REPO, 17, 3, SHA, "Python tests"),
        checks,
        allowed_repository=REPO,
        allowed_check_names=frozenset({"Python tests"}),
        failure_history_complete=True,
        previous_failure_for_sha=False,
    )
    assert (evidence.verdict, evidence.reason) == ("BLOCKED", "GITHUB_READ_UNAVAILABLE")


def test_wrong_repository_and_untrusted_installation_rejected():
    checks = ControllerChecksClient(installation_id=17, token_provider=lambda: "tok")
    with pytest.raises(OSError):
        checks.get_pull_request("wrong/repo", 1)
    with pytest.raises(OSError):
        checks.list_check_runs("wrong/repo", SHA)
    with pytest.raises(ValueError):
        ControllerChecksClient(installation_id=17, token_provider=lambda: "tok", repository="wrong/repo")


def test_token_provider_missing_pem_fails_closed(tmp_path):
    tokens = ControllerAppToken(
        app_id=1, installation_id=2, pem_path=tmp_path / "absent.pem",
    )
    with pytest.raises(OSError, match="authentication unavailable"):
        tokens()

def test_push_run_missing_and_merge_only_checks_cannot_open_gate():
    def api(method, path, token):
        if "/actions/runs?" in path:
            return {"total_count": 0, "workflow_runs": []}
        if "/check-runs?" in path:
            return {"total_count": 1, "check_runs": [{
                "id": 20, "name": "Python tests", "head_sha": SHA,
                "check_suite": {"id": 22},
                "app": {"slug": "github-actions"}, "status": "completed",
                "conclusion": "success", "completed_at": "2026-11-10T00:00:00Z",
            }]}
        return {"number": 3, "base": {"repo": {"full_name": REPO}},
                "head": {"sha": SHA}}
    checks = ControllerChecksClient(
        installation_id=17, token_provider=lambda: "tok", request_json=api,
    )
    ev = verify_ci(
        CiRequest("task-1", REPO, 17, 3, SHA, "Python tests"),
        checks, allowed_repository=REPO,
        allowed_check_names=frozenset({"Python tests"}),
        failure_history_complete=True, previous_failure_for_sha=False,
    )
    assert (ev.verdict, ev.reason) == ("BLOCKED", "REQUIRED_CHECK_MISSING")


def _n1_history_verdict(history):
    """Exercise real Actions↔Checks adapter joins, including old rerun attempts."""
    from copy import deepcopy

    def api(method, path, token):
        assert method == "GET" and token == "tok"
        if "/pulls/" in path:
            return {
                "number": 3,
                "base": {"repo": {"full_name": REPO}},
                "head": {"sha": SHA},
            }
        if "/commits/" in path and "/check-runs?" in path:
            checks = []
            for run in history:
                for job_id, job_attempt, conclusion in run["jobs"]:
                    checks.append({
                        "id": job_id, "name": "Python tests",
                        "head_sha": SHA,
                        "check_suite": {"id": run["id"] + 1000},
                        "app": {"slug": "github-actions"},
                        "status": "completed", "conclusion": conclusion,
                        "completed_at": "2026-11-11T00:00:00Z",
                    })
            return {"total_count": len(checks), "check_runs": checks}
        if "/actions/runs?" in path:
            runs = []
            for run in history:
                runs.append({
                    "id": run["id"],
                    "check_suite_id": run["id"] + 1000,
                    "run_attempt": run["attempt"],
                    "event": "push", "head_sha": SHA,
                    "name": "V1 CI",
                    "path": ".github/workflows/v1-ci.yml",
                    "repository": {"full_name": REPO},
                    "status": run.get("status", "completed"),
                    "conclusion": run["conclusion"],
                })
            return {"total_count": len(runs), "workflow_runs": runs}
        if "/actions/runs/" in path and "/jobs?" in path:
            run_id = int(path.split("/actions/runs/")[1].split("/")[0])
            run = next(r for r in history if r["id"] == run_id)
            jobs = [{
                "id": job_id, "name": "Python tests",
                "head_sha": SHA, "run_id": run_id,
                "run_attempt": job_attempt, "workflow_name": "V1 CI",
                "status": "completed", "conclusion": conclusion,
            } for job_id, job_attempt, conclusion in run["jobs"]]
            return {"total_count": len(jobs), "jobs": jobs}
        raise AssertionError("unexpected trusted API path: " + path)

    client = ControllerChecksClient(
        installation_id=17, token_provider=lambda: "tok", request_json=api,
    )
    return verify_ci(
        CiRequest("task-n1", REPO, 17, 3, SHA, "Python tests"),
        client,
        allowed_repository=REPO,
        allowed_check_names=frozenset({"Python tests"}),
        failure_history_complete=True,
        previous_failure_for_sha=False,
    )


@pytest.mark.parametrize("cancel_conclusion", ["cancelled", "timed_out"])
def test_n1_cancelled_or_timed_out_job_recovers_on_green_rerun(cancel_conclusion):
    # GitHub Actions re-runs reuse a run id but increment run_attempt.
    run = {
        "id": 501, "attempt": 1, "conclusion": cancel_conclusion,
        "jobs": [(601, 1, cancel_conclusion)],
    }
    ev = _n1_history_verdict([run])
    assert (ev.verdict, ev.reason) == ("BLOCKED", "REQUIRED_CHECK_MISSING")
    run["attempt"] = 2
    run["conclusion"] = "success"
    run["jobs"].append((602, 2, "success"))
    recovered = _n1_history_verdict([run])
    assert (recovered.verdict, recovered.reason) == (
        "SUCCESS", "REQUIRED_CHECK_PASSED"
    )
    assert (recovered.check_run_id, recovered.run_attempt) == (602, 2)


def test_n1_cancelled_run_without_allocated_job_does_not_wedge_other_run():
    cancelled = {"id": 501, "attempt": 1, "conclusion": "cancelled", "jobs": []}
    green = {
        "id": 502, "attempt": 1, "conclusion": "success",
        "jobs": [(603, 1, "success")],
    }
    ev = _n1_history_verdict([cancelled, green])
    assert (ev.verdict, ev.reason) == ("SUCCESS", "REQUIRED_CHECK_PASSED")


def test_n1_failed_job_plus_green_rerun_is_still_same_sha_conflict():
    run = {
        "id": 501, "attempt": 2, "conclusion": "success",
        "jobs": [(601, 1, "failure"), (602, 2, "success")],
    }
    ev = _n1_history_verdict([run])
    assert (ev.verdict, ev.reason) == ("BLOCKED", "SAME_SHA_FAILURE_CONFLICT")


def test_n1_failed_job_remains_sticky_after_newer_cancelled_attempt():
    # A cancelled latest run must never erase a verified earlier test failure.
    run = {
        "id": 501, "attempt": 2, "conclusion": "cancelled",
        "jobs": [(601, 1, "failure"), (602, 2, "cancelled")],
    }
    ev = _n1_history_verdict([run])
    assert (ev.verdict, ev.reason) == ("FAILURE", "REQUIRED_CHECK_FAILED")


def test_n1_unfinished_workflow_still_blocks_even_with_old_green_evidence():
    green = {
        "id": 501, "attempt": 1, "conclusion": "success",
        "jobs": [(601, 1, "success")],
    }
    pending = {
        "id": 502, "attempt": 1, "status": "in_progress",
        "conclusion": None, "jobs": [],
    }
    ev = _n1_history_verdict([green, pending])
    assert (ev.verdict, ev.reason) == ("BLOCKED", "GITHUB_READ_UNAVAILABLE")
