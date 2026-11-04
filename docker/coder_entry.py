"""Small trusted image entrypoint; no GitHub credentials or workflow mutations."""

import os
from pathlib import Path
import subprocess
import sys


def run(args: list[str]) -> int:
    # Also bounds the container if the host controller dies after Docker launch.
    try:
        return subprocess.run(
            args, timeout=int(os.environ.get("WFC_RUNTIME_SECONDS", "1800"))
        ).returncode
    except subprocess.TimeoutExpired:
        return 124


if sys.argv[1:] == ["probe"]:
    Path(os.environ["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
    probe = """
from pathlib import Path
allowed = Path('/workspace/.sandbox-probe')
allowed.write_text('allowed')
allowed.unlink()
try:
    Path('/home/coder/outside-workspace').write_text('forbidden')
except OSError as error:
    if error.errno not in {1, 13, 30}:
        raise
    print('SANDBOX_WRITE_ALLOWED_OUTSIDE_DENIED')
else:
    raise SystemExit('sandbox allowed an outside write')
"""
    raise SystemExit(
        run(
            [
                "codex",
                "-c",
                'sandbox_mode="workspace-write"',
                "sandbox",
                "--",
                "python",
                "-c",
                probe,
            ]
        )
    )
if sys.argv[1:] == ["test"]:
    raise SystemExit(run(["python", "-m", "pytest", "-q"]))
if sys.argv[1:] != ["code"]:
    raise SystemExit("expected code or test")
os.environ["CODEX_API_KEY"] = (
    Path("/run/secrets/openai_api_key").read_text().strip()
)
Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
Path(os.environ["CODEX_HOME"]).mkdir(parents=True, exist_ok=True)
# No repository configuration, hooks, Git metadata or host credentials are mounted.
# workspace-write is retained; unsupported host sandbox support fails closed.
raise SystemExit(
    run(
        [
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--model",
            os.environ["WFC_CODEX_MODEL"],
            "-",
        ]
    )
)
