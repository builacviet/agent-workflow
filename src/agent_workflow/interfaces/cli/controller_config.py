"""Layer: Interfaces/CLI
Responsibility: Read the JSON config file shared by the Controller modes.
Dependencies: Python standard library JSON and filesystem paths.
"""

import json
from pathlib import Path


def read_controller_config(path: Path) -> object:
    return json.loads(path.resolve(strict=True).read_text(encoding="utf-8-sig"))
