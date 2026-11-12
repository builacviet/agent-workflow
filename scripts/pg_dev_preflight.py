"""Validate a literal development env file and run the dedicated Compose project."""
from __future__ import annotations
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".runtime" / "postgres-dev.env"
KEYS = ("PG_DEV_DB", "PG_DEV_USER", "PG_DEV_PASSWORD", "PG_DEV_PORT")
ALLOWED_COMMANDS = {
    ("up", "-d", "--wait", "--wait-timeout", "120"),
    ("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "120"),
    ("stop",),
    ("start",),
    ("down",),
    ("logs", "postgres"),
}


def load_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in KEYS or key in values:
            raise ValueError("Invalid or duplicate configuration key")
        values[key] = value
    for key in KEYS[:3]:
        if not values.get(key, "").strip():
            raise ValueError(f"{key} is required and must not be whitespace")
    port = values.get("PG_DEV_PORT", "55432")
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("PG_DEV_PORT must be an integer from 1 to 65535")
    values["PG_DEV_PORT"] = port
    return values


def main() -> int:
    args = tuple(sys.argv[1:])
    if args not in ALLOWED_COMMANDS:
        print("Unsupported operation; see the PostgreSQL development guide", file=sys.stderr)
        return 2
    try:
        values = load_config(ENV_FILE)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"PostgreSQL dev configuration error: {exc}", file=sys.stderr)
        return 2
    env = os.environ.copy()
    env.update(values)
    for key in ("COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES"):
        env.pop(key, None)
    try:
        return subprocess.run(
            ["docker", "compose", "--project-name", "agent-workflow-pg-dev",
             "--file", str(ROOT / "compose.postgres.dev.yaml"), *args],
            cwd=ROOT, env=env, timeout=180,
            check=False,
        ).returncode
    except subprocess.TimeoutExpired:
        print("Docker Compose timed out; inspect project state before retrying", file=sys.stderr)
        return 1
    except OSError:
        print("Could not start Docker Compose; check Docker installation", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
