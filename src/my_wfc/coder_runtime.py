"""Trusted Docker boundary and GitHub publication; task code never runs on host."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import time
import tempfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlencode

from my_wfc.app_auth import AppVerifier, GitHubApp, REPOSITORY
from my_wfc.github import WorkflowError
from my_wfc.service import WorkflowService

DENIED = {".git", ".secret", ".runtime", ".codex", ".env", "AGENTS.md"}
GENERATED = {".pytest_cache", "__pycache__"}
MAX_BYTES = 10_000_000
WINDOWS_DEVICES = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{number}" for prefix in ("com", "lpt") for number in range(1, 10)
}


def is_linked(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or ":" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or any(
            part.casefold() in {name.casefold() for name in DENIED}
            or part.casefold().startswith(".env")
            or part.endswith((" ", "."))
            or part.split(".")[0].casefold() in WINDOWS_DEVICES
            for part in path.parts
        )
        or path.suffix.lower() in {".pem", ".key"}
    ):
        raise WorkflowError("unsafe coder path")
    return value


def writable(path: str, prefixes: list[str]) -> bool:
    safe_path(path)
    return any(
        path == prefix or path.startswith(prefix.rstrip("/") + "/")
        for prefix in prefixes
    )


def validate_config(config: dict) -> None:
    if config.get("repository") != REPOSITORY or not config.get("tasks"):
        raise WorkflowError("explicit repository and task allowlist required")
    for key in ("database", "pem", "openai_key", "scratch"):
        path = Path(config[key])
        if not path.is_absolute():
            raise WorkflowError("absolute operator paths required")
        # Never reference v2 directories, credentials or data.
        if any(
            "workflow" in part.lower() or "console" in part.lower()
            for part in path.parts
            if part.lower().startswith(("agent_", "workflow", "console"))
        ):
            raise WorkflowError("v2 paths are forbidden")
    profile = Path(config["seccomp_profile"])
    if (
        not profile.is_absolute()
        or not profile.is_file()
        or profile.is_symlink()
        or any("agent_workflow" in part.lower() for part in profile.parts)
    ):
        raise WorkflowError("absolute independent seccomp profile required")
    if (
        hashlib.sha256(profile.read_bytes()).hexdigest()
        != config["seccomp_sha256"]
    ):
        raise WorkflowError("seccomp profile digest mismatch")
    key = Path(config["openai_key"])
    if (
        key.suffix != ".txt"
        or key.parent.name != ".secret"
        or key.resolve() == Path(config["pem"]).resolve()
    ):
        raise WorkflowError("separate .secret OpenAI text key required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/@:-]*", config["image"]):
        raise WorkflowError("invalid runtime image")
    if not isinstance(config.get("model"), str) or not config["model"].strip():
        raise WorkflowError("operator-selected model required")
    roots = set()
    for task in config["tasks"].values():
        root = Path(task["worktree"])
        if not root.is_absolute() or is_linked(root) or not root.is_dir():
            raise WorkflowError("dedicated canonical worktree required")
        if str(root.resolve()) != str(root) or root.resolve() in roots:
            raise WorkflowError("duplicate or noncanonical worktree")
        if not (root / ".git").is_file():
            raise WorkflowError("task requires linked Git worktree")
        if any("agent_workflow" in part.lower() for part in root.parts):
            raise WorkflowError("v2 worktree forbidden")
        roots.add(root.resolve())
        if not task.get("write_paths"):
            raise WorkflowError("explicit write path allowlist required")
        for prefix in task["write_paths"]:
            safe_path(prefix)
    if (
        type(config.get("timeout_seconds", 1800)) is not int
        or not 60 <= config.get("timeout_seconds", 1800) <= 3600
    ):
        raise WorkflowError("invalid timeout")


def command(
    args: list[str],
    *,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    timeout: int = 30,
) -> bytes:
    try:
        return subprocess.run(
            args,
            cwd=cwd,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=timeout,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise WorkflowError(
            "trusted command failed; output withheld"
        ) from None


def git(root: Path, *args: str) -> bytes:
    return command(
        ["git", "-c", "core.hooksPath=/dev/null", "-C", str(root), *args]
    )


def snapshot(
    root: Path, sha: str, destination: Path
) -> dict[str, tuple[str, bytes]]:
    baseline = {}
    for entry in git(root, "ls-tree", "-rz", sha).split(b"\0"):
        if not entry:
            continue
        meta, raw_path = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        path = raw_path.decode("utf-8")
        if mode not in {"100644", "100755"} or kind != "blob":
            raise WorkflowError("symlinks and submodules are not supported")
        # Context may include AGENTS.md, but it is never publishable by the coder.
        template = (
            path == ".env.example"
            or path.startswith(".runtime/")
            and path.endswith(".example")
        )
        if path != "AGENTS.md" and not template:
            safe_path(path)
        content = git(root, "cat-file", "blob", oid)
        if (
            len(content) + sum(len(item[1]) for item in baseline.values())
            > MAX_BYTES
            or len(baseline) >= 2000
        ):
            raise WorkflowError("source snapshot limit exceeded")
        target = destination / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o777 if mode == "100755" else 0o666)
        for parent in target.parents:
            if parent == destination:
                break
            parent.chmod(0o777)
        baseline[path] = (mode, content)
    return baseline


def changes(stage: Path, baseline: dict, prefixes: list[str]) -> list[dict]:
    files = {}
    for directory, dirs, names in os.walk(stage, followlinks=False):
        for name in list(dirs):
            path = Path(directory) / name
            if is_linked(path):
                raise WorkflowError("snapshot contains a linked directory")
            if name in GENERATED:
                dirs.remove(name)
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(stage).as_posix()
            info = path.lstat()
            if (
                is_linked(path)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > MAX_BYTES
            ):
                raise WorkflowError("snapshot contains unsafe file")
            # Mode is preserved from Git for existing files; additions are ordinary files.
            mode = baseline.get(relative, ("100644", b""))[0]
            files[relative] = (mode, path.read_bytes())
            if (
                len(files) > 2000
                or sum(len(item[1]) for item in files.values()) > MAX_BYTES
            ):
                raise WorkflowError("snapshot file count exceeded")
    result = []
    for path in sorted(set(files) | set(baseline)):
        if files.get(path) == baseline.get(path):
            continue
        if not writable(path, prefixes):
            raise WorkflowError("coder modified a path outside the allowlist")
        result.append(
            {
                "path": path,
                "mode": files.get(path, baseline.get(path))[0],
                "content": files[path][1] if path in files else None,
            }
        )
    if not result:
        raise WorkflowError("coder produced no source changes")
    return result


def run_container(args: list[str], prompt: str, remaining: int) -> bytes:
    # Output goes to private bounded temporary storage, never Docker logs/chat.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=output, stderr=output
        )
        try:
            started = time.monotonic()
            payload = prompt.encode()
            while True:
                try:
                    process.communicate(payload, timeout=min(1, remaining))
                    break
                except subprocess.TimeoutExpired:
                    payload = None
                    if (
                        time.monotonic() - started >= remaining
                        or os.fstat(output.fileno()).st_size > 4_000_000
                    ):
                        raise WorkflowError(
                            "runtime deadline or output budget exceeded"
                        )
            size = output.tell()
            if process.returncode or size > 4_000_000:
                raise WorkflowError(
                    "coder/test container failed or output exceeded limit"
                )
            output.seek(0)
            return output.read()
        except BaseException:
            process.kill()
            process.wait(timeout=10)
            raise


class CoderRuntime:
    def __init__(
        self, config: dict, app: GitHubApp, service: WorkflowService
    ) -> None:
        self.config, self.app, self.service = config, app, service
        self.verify = AppVerifier(app)

    def stop(self, dispatch_id: str) -> None:
        if not re.fullmatch(r"[a-f0-9]{32}", dispatch_id):
            raise WorkflowError("invalid dispatch identifier")
        for suffix in ("code", "test", "probe"):
            name = f"my-wfc-{dispatch_id}-{suffix}"
            # docker rm -f is scoped to our exact persisted names. No v2 operations.
            found = command(
                [
                    "docker",
                    "container",
                    "ls",
                    "-aq",
                    "--filter",
                    f"name=^/{name}$",
                ]
            )
            if found.strip():
                command(["docker", "rm", "-f", name])
            if command(
                [
                    "docker",
                    "container",
                    "ls",
                    "-aq",
                    "--filter",
                    f"name=^/{name}$",
                ]
            ).strip():
                raise WorkflowError("runtime termination not confirmed")

    def docker(
        self, claim: dict, stage: Path, mode: str, prompt: str = ""
    ) -> bytes:
        name = f"my-wfc-{claim['dispatch_id']}-{mode}"
        args = [
            "docker",
            "run",
            "--name",
            name,
            "--label",
            "my-wfc.phase=3.1",
            "--read-only",
            "--user",
            "10001:10001",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--pids-limit=256",
            "--memory=2g",
            "--cpus=2",
            "--log-driver=none",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=512m,mode=1777",
            "--mount",
            f"type=bind,src={stage},dst=/workspace",
            "-i",
        ]
        if mode in {"code", "probe"}:
            profile = Path(self.config["seccomp_profile"]).resolve(strict=True)
            if (
                hashlib.sha256(profile.read_bytes()).hexdigest()
                != self.config["seccomp_sha256"]
            ):
                raise WorkflowError("seccomp profile changed")
            args += [
                "--security-opt",
                "seccomp=" + str(profile),
                "--tmpfs",
                "/home/coder:rw,nosuid,nodev,size=128m,uid=10001,gid=10001,mode=0700",
            ]
        if mode == "code":
            key = Path(self.config["openai_key"]).resolve(strict=True)
            args += [
                "--mount",
                f"type=bind,src={key},dst=/run/secrets/openai_api_key,readonly",
                "--env",
                "WFC_CODEX_MODEL=" + self.config["model"],
            ]
        else:
            args += ["--network=none"]

        remaining = int(claim["deadline"] - time.time())
        if remaining <= 0:
            raise WorkflowError("dispatch deadline exceeded")
        args += [
            "--env",
            "WFC_RUNTIME_SECONDS=" + str(remaining),
            self.config["image"],
            mode,
        ]
        return run_container(args, prompt, remaining)

    def check_worktree(self, root: Path, context: dict, sha: str) -> None:
        if context["repository"] != REPOSITORY or context["branch"] in {
            "main",
            "master",
        }:
            raise WorkflowError("unauthorized repository or protected branch")
        if (
            git(root, "branch", "--show-current").decode().strip()
            != context["branch"]
        ):
            raise WorkflowError("worktree branch mismatch")
        if git(root, "status", "--porcelain", "--untracked-files=all").strip():
            raise WorkflowError("worktree must be clean")
        if git(root, "remote", "get-url", "origin").decode().strip() not in {
            f"https://github.com/{REPOSITORY}.git",
            f"git@github.com:{REPOSITORY}.git",
        }:
            raise WorkflowError("worktree origin mismatch")
        self.verify(REPOSITORY, context["branch"], context["base_sha"], sha)
        if git(root, "rev-parse", "HEAD").decode().strip() != sha:
            # A previous App publication advances GitHub, not this local worktree.
            # Fetch credentials exist only in this trusted child's environment.
            auth = base64.b64encode(
                ("x-access-token:" + self.app.token()).encode()
            ).decode()
            env = dict(os.environ)
            env.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": "Authorization: Basic " + auth,
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
            try:
                subprocess.run(
                    [
                        "git",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "-c",
                        "credential.helper=",
                        "-C",
                        str(root),
                        "fetch",
                        "--no-tags",
                        "origin",
                        "refs/heads/" + context["branch"],
                    ],
                    env=env,
                    capture_output=True,
                    check=True,
                    timeout=30,
                )
            except (OSError, subprocess.SubprocessError):
                raise WorkflowError(
                    "App-authenticated worktree fetch failed"
                ) from None
            if git(root, "rev-parse", "FETCH_HEAD").decode().strip() != sha:
                raise WorkflowError(
                    "fetched head differs from authorized input"
                )
            git(root, "merge", "--ff-only", "--no-edit", "FETCH_HEAD")
        if git(root, "rev-parse", "HEAD").decode().strip() != sha:
            raise WorkflowError("local worktree SHA mismatch")

    def publish(
        self, context: dict, input_sha: str, entries: list[dict]
    ) -> str:
        self.verify(
            REPOSITORY, context["branch"], context["base_sha"], input_sha
        )
        name, email = self.app.identity()
        prefix = f"/repos/{REPOSITORY}"
        parent = self.app.api("GET", prefix + "/git/commits/" + input_sha)
        tree_entries = []
        for entry in entries:
            oid = None
            if entry["content"] is not None:
                blob = self.app.api(
                    "POST",
                    prefix + "/git/blobs",
                    {
                        "content": base64.b64encode(entry["content"]).decode(),
                        "encoding": "base64",
                    },
                )
                oid = blob["sha"]
            tree_entries.append(
                {
                    "path": entry["path"],
                    "mode": entry["mode"],
                    "type": "blob",
                    "sha": oid,
                }
            )
        tree = self.app.api(
            "POST",
            prefix + "/git/trees",
            {"base_tree": parent["tree"]["sha"], "tree": tree_entries},
        )
        commit = self.app.api(
            "POST",
            prefix + "/git/commits",
            {
                "message": f"{context['task_id']}: implement authorized coder assignment",
                "tree": tree["sha"],
                "parents": [input_sha],
                "author": {"name": name, "email": email},
                "committer": {"name": name, "email": email},
            },
        )
        sha = commit["sha"]
        self.verify(
            REPOSITORY, context["branch"], context["base_sha"], input_sha
        )
        self.app.api(
            "PATCH",
            prefix + "/git/refs/heads/" + quote(context["branch"], safe="/"),
            {"sha": sha, "force": False},
        )
        self.verify(REPOSITORY, context["branch"], context["base_sha"], sha)
        return sha

    def draft_pr(self, context: dict, sha: str, summary: str) -> str:
        prefix = f"/repos/{REPOSITORY}"
        query = urlencode(
            {
                "state": "open",
                "head": "builacviet:" + context["branch"],
                "base": "main",
            }
        )
        existing = self.app.api("GET", prefix + "/pulls?" + query)
        if existing:
            if (
                len(existing) != 1
                or not existing[0].get("draft")
                or existing[0].get("user", {}).get("login")
                != "builacviet-wfc-coder[bot]"
            ):
                raise WorkflowError(
                    "existing PR is not the expected bot draft"
                )
            self.app.api(
                "PATCH",
                prefix + "/pulls/" + str(existing[0]["number"]),
                {
                    "body": f"Exact head: {sha}\n\n{summary}\n\nAwaiting ChatGPT Tech Lead; no merge authorization."
                },
            )
            return existing[0]["html_url"]
        pr = self.app.api(
            "POST",
            prefix + "/pulls",
            {
                "title": context["task_id"] + ": coder handoff",
                "head": context["branch"],
                "base": "main",
                "draft": True,
                "body": f"Exact head: {sha}\n\n{summary}\n\nAwaiting ChatGPT Tech Lead; maximum two rounds. No merge authorization.",
            },
        )
        if pr.get("user", {}).get(
            "login"
        ) != "builacviet-wfc-coder[bot]" or not pr.get("draft"):
            raise WorkflowError("PR attribution mismatch")
        return pr["html_url"]

    def handoff_entries(
        self, task: dict, context: dict, entries: list[dict], summary: str
    ) -> list[dict]:
        handoff = task["handoff_path"]
        if handoff not in context["context_paths"] or not writable(
            handoff, task["write_paths"]
        ):
            raise WorkflowError("handoff path must be authorized and writable")
        handoff_text = (
            f"Task ID: {context['task_id']}\nSummary: isolated Codex assignment\n"
            f"Files: {', '.join(entry['path'] for entry in entries)}\n"
            f"Tests: {summary}\nRemaining risks: independent review pending\n"
            f"Branch: {context['branch']}\nBase SHA: {context['base_sha']}\n"
            "Head SHA: resolve commit containing this handoff on task branch\n"
            "Limitations: model/effort unverified; no approval or merge\n"
            "Push status: verified by controller before handoff registration\n"
            f"Requested review effort: {context['requested_effort']}\n"
            "Review status: awaiting ChatGPT Tech Lead\n"
        )
        entries = [entry for entry in entries if entry["path"] != handoff]
        entries.append(
            {
                "path": handoff,
                "mode": "100644",
                "content": handoff_text.encode(),
            }
        )
        return entries

    def execute(self, claim: dict, context: dict) -> str:
        task = self.config["tasks"][context["task_id"]]
        root = Path(task["worktree"]).resolve(strict=True)
        self.check_worktree(root, context, claim["input_sha"])
        scratch = Path(self.config["scratch"]).resolve()
        scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        scratch.chmod(0o700)
        stage = scratch / claim["dispatch_id"]
        stage.mkdir(mode=0o777)
        stage.chmod(0o777)
        baseline = snapshot(root, claim["input_sha"], stage)
        prompt = (
            "Implement only this authorized task. Read AGENTS.md and supplied context. "
            "Do not commit, push, merge, approve, access credentials or alter workflow policy. "
            "Modify only these paths: "
            + json.dumps(task["write_paths"])
            + "\n"
            + json.dumps(context, sort_keys=True)
        )
        try:
            self.docker(claim, stage, "code", prompt)
            # Stop coder before inspecting mutable files or running independent tests.
            self.stop(claim["dispatch_id"])
            entries = changes(stage, baseline, task["write_paths"])
            test_output = self.docker(claim, stage, "test")
            self.stop(claim["dispatch_id"])
            if entries != changes(stage, baseline, task["write_paths"]):
                raise WorkflowError("tests mutated publishable source")
            # Keep raw test logs private. Summary is generated from observed exit 0.
            summary = "python -m pytest -q: exit 0 in isolated credential-free test container"
            (scratch / (claim["dispatch_id"] + ".tests.txt")).write_bytes(
                test_output
            )
            self.check_worktree(root, context, claim["input_sha"])
            latest = self.service.get_coder_assignment(context["task_id"])
            if (
                latest["status"],
                latest["current_head_sha"],
                latest["completed_review_rounds"],
            ) != (
                context["status"],
                context["current_head_sha"],
                context["completed_review_rounds"],
            ):
                raise WorkflowError(
                    "workflow assignment changed during coding"
                )
            entries = self.handoff_entries(task, context, entries, summary)
            sha = self.publish(context, claim["input_sha"], entries)
            self.draft_pr(context, sha, summary)
            self.service.register_task_handoff(
                context["task_id"],
                REPOSITORY,
                context["branch"],
                context["base_sha"],
                sha,
                task["handoff_path"],
                summary,
            )
            return sha
        finally:
            self.stop(claim["dispatch_id"])
