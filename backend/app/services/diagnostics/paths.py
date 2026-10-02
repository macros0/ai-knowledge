"""Dependency-free link guards shared by the parent process and worker children."""
import os
from pathlib import Path
import stat


def _is_link(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & 0x400
    )  # Windows FILE_ATTRIBUTE_REPARSE_POINT includes junctions.


def canonical_root(root) -> Path:
    """Resolve operator-owned links above root (macOS /var, /tmp); root itself stays checked."""
    root = Path(os.path.abspath(root))
    return Path(os.path.realpath(root.parent)) / root.name


def assert_no_links(path: Path, root: Path) -> None:
    """Reject links from path up to and including root, never above it.

    Callers pass a canonical root, so ancestors were resolved deliberately at
    startup; a link created inside the spool is still refused.
    """
    for part in (path, *path.parents):
        if _is_link(part):
            raise ValueError("Diagnostic path contains a link")
        if part == root:
            return
