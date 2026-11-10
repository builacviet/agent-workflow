"""V1-03A exact-SHA CI gate: deterministic read-only fixtures only."""
from datetime import datetime, timezone

import pytest

from agent_workflow.integrations.ci_gate import CiRequest, verify_ci
from agent_workflow.integrations.github import WorkflowError

REPO = "builacviet/agent-workflow"
HEAD = "a" * 40
OLD = "b" * 40
WHEN = datetime(2026, 11, 10, tzinfo=timezone.utc)


def request(**changes):
    return CiRequest(**({
        "task_id": "V1-03A", "repository": REPO, "installation_id": 17,
        "pr_number": 55, "expected_head_sha": HEAD,
        "check_name": "Python tests",
    } | changes))


def run(conclusion="success", **changes):
    return {
        "id": 19, "name": "Python tests", "head_sha": HEAD,
        "app": {"slug": "github-actions"}, "status": "completed",
        "conclusion": conclusion, "completed_at": "2026-11-10T00:01:00Z",
        "event": "push", "workflow_path": ".github/workflows/v1-ci.yml",
        "tested_sha": HEAD, "workflow_run_id": 123, "run_attempt": 1,
    } | changes


class FakeGitHub:
    installation_id = 17

    def __init__(self, runs=None, head=HEAD):
        self.runs = [run()] if runs is None else runs
        self.head = head
        self.calls = []

    def get_pull_request(self, repo, number):
        self.calls.append("pr")
        return {"number": number, "base": {"repo": {"full_name": repo}},
                "head": {"sha": self.head}}

    def list_check_runs(self, repo, sha):
        self.calls.append(("checks", sha))
        return {"total_count": len(self.runs), "check_runs": self.runs}


def verdict(client=None, **kwargs):
    options = dict(
        allowed_repository=REPO,
        allowed_check_names=frozenset({"Python tests"}),
        failure_history_complete=True,
        previous_failure_for_sha=False,
        observed_at=WHEN,
    )
    options.update(kwargs)
    return verify_ci(request(), client or FakeGitHub(), **options)


def test_c01_true_head_success_and_serializable_evidence():
    client = FakeGitHub()
    ev = verdict(client)
    assert ev.verdict == "SUCCESS" and ev.tested_sha == HEAD
    assert ev.check_run_id == 19 and ev.check_name == "Python tests"
    assert ev.as_dict()["observed_at"] == WHEN.isoformat()
    assert client.calls == ["pr", ("checks", HEAD), "pr"]


@pytest.mark.parametrize("runs,reason", [
    ([], "REQUIRED_CHECK_MISSING"),
    ([run(status="queued", conclusion=None)], "CHECK_PENDING"),
    ([run(status="in_progress", conclusion=None)], "CHECK_PENDING"),
    ([run(conclusion="cancelled")], "CHECK_CONCLUSION_UNVERIFIED"),
    ([run(conclusion="timed_out")], "CHECK_CONCLUSION_UNVERIFIED"),
    ([run(conclusion="neutral")], "CHECK_CONCLUSION_UNVERIFIED"),
    ([run(conclusion=None)], "CHECK_CONCLUSION_UNVERIFIED"),
])
def test_c02_c07_missing_pending_unaccepted_conclusion(runs, reason):
    ev = verdict(FakeGitHub(runs))
    assert (ev.verdict, ev.reason) == ("BLOCKED", reason)


def test_c03_stale_pr_head_blocks_before_check_query():
    client = FakeGitHub(head=OLD)
    ev = verdict(client)
    assert ev.verdict == "BLOCKED" and ev.reason == "PR_HEAD_CHANGED"
    assert client.calls == ["pr"]


def test_c04_only_merge_sha_success_cannot_pass():
    ev = verdict(FakeGitHub([run(head_sha=OLD)]))
    assert ev.verdict == "BLOCKED" and ev.reason == "REQUIRED_CHECK_MISSING"


def test_c05_completed_failure_returned_without_review_count():
    ev = verdict(FakeGitHub([run("failure")]))
    assert ev.verdict == "FAILURE" and ev.reason == "REQUIRED_CHECK_FAILED"
    assert "review" not in ev.as_dict() and "repair" not in ev.as_dict()


def test_c06_failed_then_passed_rerun_on_same_sha_requires_owner():
    ev = verdict(FakeGitHub([run("success")]), previous_failure_for_sha=True)
    assert ev.verdict == "BLOCKED" and ev.reason == "SAME_SHA_FAILURE_CONFLICT"
    ev2 = verdict(FakeGitHub([run("failure"), run("success", id=20)]))
    assert ev2.verdict == "BLOCKED" and ev2.reason == "SAME_SHA_FAILURE_CONFLICT"


def test_c06_unknown_durable_history_is_not_accepted():
    ev = verdict(FakeGitHub(), failure_history_complete=False)
    assert ev.verdict == "BLOCKED" and ev.reason == "DURABLE_HISTORY_UNVERIFIED"


def test_c08_webhook_says_success_but_api_evidence_missing():
    # The verifier intentionally has no webhook argument or interpretation.
    webhook_payload = {"conclusion": "success", "head_sha": HEAD}
    ev = verdict(FakeGitHub([]))
    assert webhook_payload["conclusion"] == "success"
    assert ev.verdict == "BLOCKED"


def test_enriched_actions_provenance_is_recorded():
    ev = verdict(FakeGitHub([run(workflow_run_id=123, run_attempt=2)]))
    assert ev.verdict == "SUCCESS"
    assert (ev.workflow_run_id, ev.run_attempt) == (123, 2)


def test_partial_actions_provenance_blocks():
    ev = verdict(FakeGitHub([run(run_attempt=None)]))
    assert (ev.verdict, ev.reason) == ("BLOCKED", "REQUIRED_CHECK_MISSING")


def test_pull_request_check_with_matching_head_cannot_pass():
    ev = verdict(FakeGitHub([run(event="pull_request")]))
    assert (ev.verdict, ev.reason) == ("BLOCKED", "REQUIRED_CHECK_MISSING")


def test_self_asserted_tested_sha_cannot_pass():
    ev = verdict(FakeGitHub([run(tested_sha=OLD)]))
    assert (ev.verdict, ev.reason) == ("BLOCKED", "REQUIRED_CHECK_MISSING")


def test_check_source_is_pinned_and_cannot_be_spoofed_by_other_app():
    ev = verdict(FakeGitHub([run(app={"slug": "untrusted-bot"})]))
    assert ev.verdict == "BLOCKED"


def test_mismatched_installation_is_rejected():
    client = FakeGitHub()
    client.installation_id = 99
    with pytest.raises(WorkflowError, match="installation mismatch"):
        verdict(client)


def test_unapproved_named_check_or_repo_is_rejected():
    with pytest.raises(WorkflowError, match="unapproved"):
        verify_ci(request(check_name="Fake success"), FakeGitHub(),
                  allowed_repository=REPO,
                  allowed_check_names=frozenset({"Python tests"}),
                  failure_history_complete=True, previous_failure_for_sha=False)


def test_partial_paginated_check_history_blocks():
    class Incomplete(FakeGitHub):
        def list_check_runs(self, repo, sha):
            return {"total_count": 2, "check_runs": [run()]}
    ev = verdict(Incomplete())
    assert ev.verdict == "BLOCKED" and ev.reason == "CHECK_HISTORY_INCOMPLETE"


def test_pr_head_changed_while_querying_prevents_stale_pass():
    class Racy(FakeGitHub):
        def get_pull_request(self, repo, number):
            result = super().get_pull_request(repo, number)
            if self.calls.count("pr") == 2:
                result["head"]["sha"] = OLD
            return result
    ev = verdict(Racy())
    assert ev.verdict == "BLOCKED" and ev.reason == "PR_HEAD_CHANGED_DURING_QUERY"


def test_github_errors_fail_closed():
    class Offline(FakeGitHub):
        def list_check_runs(self, repo, sha):
            raise TimeoutError("unavailable")
    ev = verdict(Offline())
    assert ev.verdict == "BLOCKED" and ev.reason == "GITHUB_READ_UNAVAILABLE"


def test_invalid_sha_rejected():
    with pytest.raises(WorkflowError):
        request(expected_head_sha="main")
