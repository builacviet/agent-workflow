"""Layer: Infrastructure
Responsibility: Detect linked host filesystem paths for runtime workspaces.
Dependencies: Python standard library filesystem APIs.
"""

from __future__ import annotations

import stat
from pathlib import Path


def is_linked(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )
