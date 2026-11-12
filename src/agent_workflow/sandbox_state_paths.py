"""Cross-platform, fail-closed preflight for trusted sandbox fence storage.

The host operator must protect parent directories and mount points after
startup: this check cannot prevent a privileged post-validation path swap.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import sys

# Unrecognized Linux filesystem types are unsafe by default. Overlay is
# permitted for isolated dev/test; production must mount persistent host state.
_LOCAL_LINUX_FS = frozenset({
    "ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs",
    "bcachefs", "erofs", "jfs", "reiserfs", "ubifs",
    "vfat", "exfat", "ntfs", "ntfs3", "overlay",
})
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


def _windows_fixed_drive(anchor: str) -> bool:
    """Use Win32 only on Windows; reject remote/removable/unknown drives."""
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    drive_type = kernel.GetDriveTypeW
    drive_type.argtypes = [ctypes.c_wchar_p]
    drive_type.restype = ctypes.c_uint
    return drive_type(anchor) == 3  # DRIVE_FIXED


def _linux_mount_type(path: Path, mountinfo: str) -> str:
    """Find the most specific mounted filesystem in our namespace."""
    # Linux mountinfo always uses POSIX paths, even in Windows-hosted tests.
    target = PurePosixPath(path.as_posix())
    if not target.is_absolute():
        raise ValueError("invalid Linux target path")
    best: tuple[int, str] | None = None
    for line in mountinfo.splitlines():
        sections = line.split(" - ", 1)
        if len(sections) != 2:
            raise ValueError("invalid Linux mount table")
        left, right = sections[0].split(), sections[1].split()
        if len(left) < 5 or not right:
            raise ValueError("invalid Linux mount table")
        mount = PurePosixPath(_OCTAL_ESCAPE.sub(
            lambda m: chr(int(m.group(1), 8)), left[4]
        ))
        if not mount.is_absolute():
            raise ValueError("invalid Linux mount point")
        if target == mount or mount in target.parents:
            specificity = len(mount.parts)
            if best is None or specificity > best[0]:
                best = specificity, right[0]
    if best is None:
        raise ValueError("unknown Linux mount")
    return best[1]


def validate_sandbox_state_dir(path: Path) -> Path:
    """Ensure a nonredirected, local, durable operator-owned directory."""
    try:
        if not isinstance(path, Path):
            raise ValueError
        raw = str(path)
        windows = PureWindowsPath(raw)
        if (
            not raw or not path.is_absolute() or path == Path(path.anchor)
            or ".." in path.parts or ".." in windows.parts
            or "\x00" in raw or "://" in raw
            or windows.drive.startswith(("\\", "/"))
        ):
            raise ValueError

        if os.name == "nt":
            if not _windows_fixed_drive(windows.anchor):
                raise ValueError
            for part in windows.parts[1:]:
                if (
                    not part or part.endswith((" ", "."))
                    or ":" in part
                    or part.rstrip(" .").split(".", 1)[0].upper() in _WINDOWS_RESERVED
                ):
                    raise ValueError
        elif sys.platform == "linux":
            if windows.drive:
                raise ValueError
            mountinfo = Path("/proc/self/mountinfo").read_text(
                encoding="utf-8", errors="surrogateescape"
            )
            if _linux_mount_type(path, mountinfo) not in _LOCAL_LINUX_FS:
                raise ValueError
        else:
            raise ValueError  # Not an unverified local-filesystem claim.

        for part in (path, *path.parents):
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if (
                stat.S_ISLNK(info.st_mode)
                or (getattr(info, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
                or not stat.S_ISDIR(info.st_mode)
            ):
                raise ValueError
            if part == path and os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o022:
                raise ValueError
    except (ValueError, TypeError, OSError, AttributeError, UnicodeError):
        raise ValueError(
            "WFC_SANDBOX_STATE_DIR must be a safe, durable local directory"
        ) from None
    return path
