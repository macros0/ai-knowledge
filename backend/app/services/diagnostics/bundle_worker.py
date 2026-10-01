"""Child-only ZIP builder. The backend owns the snapshot, quota and SQL publication."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from .bundle import BundleTooLarge, build_bundle
from .sanitize import valid_uuid
from .schema import DiagnosticLimits
from .safe_metadata import _safe_operations, _safe_runtime
from .store import _is_link

MAX_INPUT_BYTES = 4 * 1048576


class WorkerPathStore:
    """Path guard only; the parent owns the ledger and all credits."""
    def __init__(self, root):
        self.root = Path(os.path.abspath(root))

    def safe_path(self, path):
        path = Path(os.path.abspath(path))
        if not path.is_relative_to(self.root) or any(_is_link(part) for part in (path, *path.parents)):
            raise ValueError("Unsafe bundle worker path")
        return path


def _work(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Invalid worker payload")
    bundle_id, snapshot_id = payload["bundle_id"], payload["snapshot_id"]
    if valid_uuid(bundle_id) is None or valid_uuid(snapshot_id) is None:
        raise ValueError("Invalid worker identity")
    limits = DiagnosticLimits(**payload["limits"])
    store = WorkerPathStore(Path(payload["root"]))
    directory = store.safe_path(store.root / "snapshots" / snapshot_id)
    if not directory.is_dir():
        raise ValueError("Snapshot unavailable")
    names = payload["filenames"]
    if not isinstance(names, list) or len(names) > 1000 or len(set(names)) != len(names):
        raise ValueError("Invalid snapshot files")
    paths = []
    for name in names:
        if (not isinstance(name, str) or len(name) > 128 or "/" in name or "\\" in name
                or name != Path(name).name or not name.endswith(".jsonl")):
            raise ValueError("Invalid snapshot filename")
        paths.append(store.safe_path(directory / name))
    snapshot = SimpleNamespace(
        store=store, paths=tuple(paths), cutoff_at=datetime.fromisoformat(payload["cutoff_at"]),
        runtime=_safe_runtime(payload["runtime"]), operations=_safe_operations(payload["operations"]),
        counts=payload["counts"], partial=payload["partial"], gaps=tuple(payload["gaps"]),
        capture_policies=payload.get("capture_policies", []),
        normalized=payload.get("normalized") is True,
    )
    destination = store.safe_path(store.root / "bundles" / (bundle_id + ".part"))
    built = build_bundle(snapshot, destination, limits, reserved_upper=payload["reserved_upper"])
    return {"status": "ready", "size_bytes": built.size_bytes, "sha256": built.sha256,
            "manifest": built.manifest}


def main() -> int:
    # This process has no database connection or recorder. A lower scheduling
    # priority leaves CPU time for business requests while it compresses.
    if os.name == "posix":
        try:
            os.nice(10)
        except OSError:
            pass
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("Worker payload too large")
        result = _work(json.loads(raw))
    except BundleTooLarge:
        result = {"status": "too_large"}
    except Exception:
        # Never send raw exceptions, paths or event data over the result pipe.
        result = {"status": "failed"}
    sys.stdout.write(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
