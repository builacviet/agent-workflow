"""Stable local data location, independent of the process working directory."""
import os
from pathlib import Path


def database_path() -> Path:
    override = os.environ.get("WFC_DB_PATH")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise ValueError("WFC_DB_PATH must be absolute for a stable data location")
        return path
    root = Path(__file__).resolve().parents[2]
    if (root / "pyproject.toml").is_file() and (root / "src/my_wfc").is_dir():
        return root / "data/workflow.sqlite3"
    # A wheel installation has no checkout data directory. Use operator-owned data.
    return Path.home() / ".my_wfc/data/workflow.sqlite3"
