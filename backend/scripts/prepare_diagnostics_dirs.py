"""One-time Linux setup of fixed diagnostic bind-mount directories.

This deliberately never walks or recursively changes ownership of a data tree.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def _is_link(path: Path) -> bool:
    try:
        return path.is_symlink() or path.is_junction() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False


def _assert_safe(root: Path, data: Path):
    if not root.is_absolute() or not data.is_absolute() or root == data:
        raise ValueError("Use absolute, separate diagnostics and data paths")
    if root.is_relative_to(data) or data.is_relative_to(root):
        raise ValueError("Diagnostics must stay outside the data directory")
    for path in (root, *root.parents, data, *data.parents):
        if _is_link(path):
            raise ValueError("A path contains a symbolic link or reparse point")


def prepare(root: Path, data: Path, *, app_uid: int, app_gid: int, frontend_uid: int = 1000):
    root = Path(os.path.abspath(root))
    data = Path(os.path.abspath(data))
    _assert_safe(root, data)
    if min(app_uid, app_gid, frontend_uid) <= 0:
        raise ValueError("Service identities must be non-root")
    if os.name != "posix":
        raise OSError("This bootstrap is for Linux; check Windows directory ACLs explicitly")
    # Only known subtrees are touched. Existing event/ZIP files are never chowned.
    fixed = [
        (root, app_uid), (root / "backend", app_uid),
        (root / "backend" / "events", app_uid),
        (root / "backend" / "events" / "baseline", app_uid),
        (root / "backend" / "snapshots", app_uid),
        (root / "backend" / "bundles", app_uid),
        (root / "backend" / "control", app_uid),
        (root / "backend" / "capture-expiry", app_uid),
        (root / "frontend", frontend_uid),
        (root / "frontend" / "events", frontend_uid),
        (root / "frontend" / "events" / "baseline", frontend_uid),
    ]
    for path, uid in fixed:
        if _is_link(path):
            raise ValueError("A diagnostic directory is a link")
        path.mkdir(mode=0o770, parents=False, exist_ok=True)
        if not path.is_dir() or _is_link(path):
            raise ValueError("Expected a real diagnostic directory")
        os.chown(path, uid, app_gid)
        os.chmod(path, 0o2770)
    # The frontend sees only this read-only bind mount. A named ACL on this
    # directory gives its UID read access to new control files without joining
    # the backend's general APP_GID or exposing any other backend directory.
    if frontend_uid != app_uid:
        control = root / "backend" / "control"
        try:
            subprocess.run(["setfacl", "-m", f"u:{frontend_uid}:rx,d:u:{frontend_uid}:rx", str(control)],
                           check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise OSError("Targeted control-file ACL could not be applied") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prepare safe production diagnostic bind mounts")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--app-uid", required=True, type=int)
    parser.add_argument("--app-gid", required=True, type=int)
    parser.add_argument("--frontend-uid", default=1000, type=int)
    args = parser.parse_args(argv)
    try:
        prepare(args.root, args.data_dir, app_uid=args.app_uid, app_gid=args.app_gid,
                frontend_uid=args.frontend_uid)
    except (OSError, ValueError) as exc:
        print(f"Diagnostics directory setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
