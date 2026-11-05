"""Trusted developer-worker prepare/publish phases, never workflow DB access."""

from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlencode

from coder_worker.app_auth import GitHubApp, AppVerifier, REPOSITORY, BOT
from coder_worker.repository import snapshot, changes, writable, git
from coder_worker.github import WorkflowError, validate_sha
from my_wfc.budget import dispatch_budget, operation_timeout, check_deadline

ROOT = Path("/workspace")


def git_auth(app: GitHubApp, repo: Path, *args: str) -> bytes:
    import base64

    env = dict(os.environ)
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
            "GIT_CONFIG_VALUE_0": "Authorization: Basic "
            + base64.b64encode(
                ("x-access-token:" + app.token()).encode()
            ).decode(),
        }
    )
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "credential.helper=",
                "-C",
                str(repo),
                *args,
            ],
            env=env,
            capture_output=True,
            check=True,
            timeout=operation_timeout(30),
        ).stdout
        check_deadline()
        return result
    except (OSError, subprocess.SubprocessError):
        raise WorkflowError("worker Git operation failed") from None


def prepare(data: dict, app: GitHubApp) -> None:
    context = data["context"]
    AppVerifier(app)(
        REPOSITORY, context["branch"], context["base_sha"], data["input_sha"]
    )
    repo = ROOT / "repo"
    repo.mkdir()
    git(repo, "init")
    git(
        repo, "remote", "add", "origin", f"https://github.com/{REPOSITORY}.git"
    )
    git_auth(
        app,
        repo,
        "fetch",
        "--no-tags",
        "origin",
        "refs/heads/" + context["branch"],
    )
    if (
        git(repo, "rev-parse", "FETCH_HEAD").decode().strip()
        != data["input_sha"]
    ):
        raise WorkflowError("remote moved during worker fetch")
    git(repo, "checkout", "-b", context["branch"], "FETCH_HEAD")
    source = ROOT / "source"
    source.mkdir(mode=0o777)
    source.chmod(0o777)
    snapshot(repo, data["input_sha"], source)


def publish(data: dict, app: GitHubApp) -> None:
    context = data["context"]
    repo, source = ROOT / "repo", ROOT / "source"
    # Reconstruct immutable input snapshot from Git; coder cannot reach metadata.
    baseline_dir = ROOT / "baseline"
    baseline_dir.mkdir()
    baseline = snapshot(repo, data["input_sha"], baseline_dir)
    entries = changes(source, baseline, data["write_paths"])
    handoff = data["handoff_path"]
    if handoff not in context["context_paths"] or not writable(
        handoff, data["write_paths"]
    ):
        raise WorkflowError("unauthorized handoff path")
    entries = [entry for entry in entries if entry["path"] != handoff]
    entries.append(
        {
            "path": handoff,
            "content": (
                f"Task: {context['task_id']}\nBase: {context['base_sha']}\n"
                f"Branch: {context['branch']}\nTests: {data['test_summary']}\n"
                "Head: resolve containing commit; awaiting Tech Lead review, no approval/merge.\n"
            ).encode(),
            "mode": "100644",
        }
    )
    for entry in entries:
        target = repo / entry["path"]
        if entry["content"] is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(entry["content"])
    name, email = app.identity()
    git(repo, "config", "user.name", name)
    git(repo, "config", "user.email", email)
    git(repo, "add", "--all")
    git(
        repo,
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        context["task_id"] + ": worker implementation",
    )
    sha = git(repo, "rev-parse", "HEAD").decode().strip()
    verify = AppVerifier(app)
    verify(
        REPOSITORY, context["branch"], context["base_sha"], data["input_sha"]
    )
    git_auth(
        app, repo, "push", "origin", "HEAD:refs/heads/" + context["branch"]
    )
    verify(REPOSITORY, context["branch"], context["base_sha"], sha)
    prefix = f"/repos/{REPOSITORY}/pulls"
    existing = app.api(
        "GET",
        prefix
        + "?"
        + urlencode(
            {
                "state": "open",
                "head": "builacviet:" + context["branch"],
                "base": "main",
            }
        ),
    )
    body = f"Worker handoff SHA: {sha}\n{data['test_summary']}\nAwaiting Tech Lead; no merge authorization."
    if existing:
        if (
            len(existing) != 1
            or not existing[0]["draft"]
            or existing[0]["user"]["login"] != BOT
        ):
            raise WorkflowError("unexpected existing PR")
        pr = app.api(
            "PATCH", prefix + "/" + str(existing[0]["number"]), {"body": body}
        )
    else:
        pr = app.api(
            "POST",
            prefix,
            {
                "title": context["task_id"],
                "head": context["branch"],
                "base": "main",
                "draft": True,
                "body": body,
            },
        )
    if not pr["draft"] or pr["user"]["login"] != BOT:
        raise WorkflowError("PR attribution mismatch")
    result = {
        key: context[key]
        for key in ("task_id", "repository", "branch", "base_sha")
    }
    result.update(
        head_sha=sha, input_sha=data["input_sha"], pr_url=pr["html_url"]
    )
    (ROOT / "result.json").write_text(json.dumps(result))


def main() -> None:
    data = json.load(sys.stdin)
    context = data["context"]
    if context["repository"] != REPOSITORY or context["branch"] in {
        "main",
        "master",
    }:
        raise WorkflowError("unauthorized repository or branch")
    validate_sha(data["input_sha"])
    app = GitHubApp(
        data["app_id"],
        data["installation_id"],
        Path("/run/.secret/coder-app.pem"),
    )
    try:
        with dispatch_budget(data["deadline"]):
            {"prepare": prepare, "publish": publish}[sys.argv[1]](data, app)
    finally:
        app.revoke()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "worker failed; operator reconciliation required"
        ) from None
