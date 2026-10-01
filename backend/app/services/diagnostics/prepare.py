"""Parent-side supervised preparation of safe component streams."""
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import threading

from .worker_protocol import FrameError, JobChannel, QuotaBroker
from .bundle import BundleTooLarge
from .sanitize import valid_uuid


@dataclass(frozen=True)
class PrepareResult:
    directory: Path
    paths: tuple[Path, ...]
    counts: dict
    gaps: tuple[str, ...]
    pid: int


def prepare_in_child(store, view, job_id: str, *, frontend_root=None,
                     timeout_seconds=300, on_child=None) -> PrepareResult:
    if valid_uuid(job_id) is None or view.released or view.store is not store:
        raise ValueError("Invalid diagnostic prepare job")
    directory = store.safe_path(store.root / "snapshots" / job_id)
    directory.mkdir(mode=0o770)
    payload = {
        "root": str(store.root), "frontend_root": str(frontend_root) if frontend_root else None,
        "job_id": job_id, "cutoff_at": view.cutoff_at.isoformat(),
        "filter": {key: value.isoformat() if hasattr(value, "isoformat") else value
                   for key, value in vars(view.filter).items()},
        "limits": {"baseline_seconds": store.limits.baseline_seconds,
                   "capture_seconds": store.limits.capture_seconds,
                   "frontend_bytes": store.limits.frontend_bytes,
                   "bundle_bytes": store.limits.bundle_bytes},
        "descriptors": [{"path": str(item.path), "stream": item.stream,
                         "identity": list(item.identity), "length": item.length,
                         "expires_at": item.expires_at, "mtime_ns": item.mtime_ns,
                         "source": item.source} for item in view.descriptors],
    }
    module_root = str(Path(__file__).resolve().parents[3])
    options = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
               "stderr": subprocess.DEVNULL,
               "env": {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}}
    if os.name == "nt":
        options["cwd"] = module_root
        options["creationflags"] = (getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
                                    | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        # Match the ZIP worker's posix_spawn path. Linux may temporarily share
        # the parent VM; RSS observers must not sum that VM twice.
        # Python-created descriptors use CLOEXEC.
        options["close_fds"] = False
        options["env"]["PYTHONPATH"] = os.pathsep.join(filter(None, (
            module_root, options["env"].get("PYTHONPATH"),
        )))
    broker = QuotaBroker(store)
    child = None
    timer = None
    try:
        child = subprocess.Popen([sys.executable, "-m", "app.services.diagnostics.prepare_worker", job_id], **options)
        if on_child:
            on_child(child)
        timer = threading.Timer(timeout_seconds, child.kill)
        timer.daemon = True
        timer.start()
        outgoing = JobChannel(job_id)
        incoming = JobChannel(job_id)
        outgoing.send_prepare(child.stdin, payload)
        result = None
        while True:
            frame = incoming.receive(child.stdout)
            kind, data = frame["type"], frame["payload"]
            if kind == "phase":
                continue
            if kind == "request_credit":
                component, amount = data.get("component"), data.get("bytes")
                if component not in {"backend", "frontend", "browser"} or type(amount) is not int:
                    raise FrameError("Invalid worker credit request")
                path = directory / (component + ".jsonl")
                grant_id = broker.grant(job_id, amount, path)
                outgoing.send(child.stdin, "grant", {"grant_id": grant_id, "bytes": amount})
                continue
            if kind == "commit_growth":
                grant_id, actual = data.get("grant_id"), data.get("bytes")
                broker.commit(grant_id, actual)
                continue
            if kind == "result":
                result = data
                break
            raise FrameError("Unexpected child message")
        child.stdin.close()
        if child.wait(timeout=5) != 0 or broker.outstanding(job_id):
            raise RuntimeError("Diagnostic prepare failed")
        if result.get("status") == "too_large":
            raise BundleTooLarge()
        if result.get("status") != "prepared":
            raise RuntimeError("Diagnostic prepare failed")
        names = result.get("filenames")
        if not isinstance(names, list) or len(set(names)) != len(names) or any(
                name not in {"backend.jsonl", "frontend.jsonl", "browser.jsonl"} for name in names):
            raise FrameError("Invalid prepare files")
        paths = tuple(store.safe_path(directory / name) for name in names)
        if any(not path.is_file() for path in paths) or type(result.get("pid")) is not int:
            raise FrameError("Invalid prepare result")
        counts = result.get("counts")
        gaps = result.get("gaps")
        if not isinstance(counts, dict) or not isinstance(gaps, list) or any(
                gap not in {"invalid_input", "segment_unavailable", "segment_expired"} for gap in gaps):
            raise FrameError("Invalid prepare coverage")
        return PrepareResult(directory, paths, counts, tuple(gaps), result["pid"])
    except Exception:
        if child is not None:
            if child.poll() is None:
                child.kill()
            if child.stdin is not None and not child.stdin.closed:
                child.stdin.close()
            if child.stdout is not None:
                child.stdout.close()
            child.wait(timeout=5)
        store.delete_tree(directory)
        raise
    finally:
        if timer is not None:
            timer.cancel()
        broker.release_job(job_id)
