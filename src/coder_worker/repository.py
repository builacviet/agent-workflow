"""Worker-only Git and snapshot validation."""

from pathlib import Path, PurePosixPath
import os, stat, subprocess
from agent_workflow.budget import check_deadline, operation_timeout
from coder_worker.github import WorkflowError

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


def independent_path(path: Path, *, directory: bool = False) -> None:
    forbidden = (
        "agent_workflow_v2",
        "workflow_console_v2",
        "workflow-console-v2",
    )
    for candidate in (path, path.resolve()):
        directories = candidate.parts if directory else candidate.parts[:-1]
        if any(part.casefold().startswith(forbidden) for part in directories):
            raise WorkflowError("v2 paths are forbidden")


def command(
    args: list[str],
    *,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
    timeout: int = 30,
) -> bytes:
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=operation_timeout(timeout),
        ).stdout
        check_deadline()
        return result
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
