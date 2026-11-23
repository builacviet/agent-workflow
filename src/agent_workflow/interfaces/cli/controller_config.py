"""Read the controller configuration shared by the CLI modes."""

import json
from pathlib import Path


def read_controller_config(path: Path) -> object:
    return json.loads(path.resolve(strict=True).read_text(encoding="utf-8-sig"))
