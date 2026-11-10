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
        page = int(path.split("page=")[-1])
        chunk = list(range(100)) if page == 1 else [100]
        return {"total_count": 101, "check_runs": [
            {
                "id": n + 1, "name": "Python tests", "head_sha": SHA,
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
