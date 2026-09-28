"""Collect a bounded safe bundle while the application is stopped.

The help and dry-run paths intentionally import no application modules.
"""
import argparse
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import json
import re
import shutil
import sys
from uuid import uuid4


def _parser():
    parser = argparse.ArgumentParser(description="Offline export of safe OKF diagnostic events")
    parser.add_argument("--root", type=Path, required=True, help="Backend safe diagnostics root")
    parser.add_argument("--since", help="UTC ISO-8601 start")
    parser.add_argument("--until", help="UTC ISO-8601 end")
    parser.add_argument("--output", type=Path, help="New ZIP path outside the diagnostics root")
    parser.add_argument("--dry-run", action="store_true", help="Validate arguments without opening the application")
    parser.add_argument("--container-state-stdin", action="store_true",
                        help="Read allowlisted Compose container state JSON from stdin")
    parser.add_argument("--container-state-incomplete", action="store_true",
                        help="Mark missing Compose container state as a bundle gap")
    return parser


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("Interval must use UTC")
    return parsed.astimezone(timezone.utc)


def _no_links(path: Path):
    for part in (path, *path.parents):
        if part.is_symlink() or part.is_junction() or getattr(part.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Output path contains a link")


def _unavailable(_request):
    raise RuntimeError("Offline metadata unavailable")


def _container_state(raw: bytes) -> dict:
    if len(raw) > 8192:
        raise ValueError("Container state exceeds limit")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) - {"backend", "frontend", "postgres", "qdrant", "migrate"}:
        raise ValueError("Invalid container state")
    result = {}
    for service, item in value.items():
        if not isinstance(item, dict) or set(item) != {
                "status", "exit_code", "oom_killed", "started_at", "finished_at", "image_id"}:
            raise ValueError("Invalid container state")
        if type(item["status"]) is not str or item["status"] not in {
                "created", "running", "restarting", "exited", "paused", "dead"}:
            raise ValueError("Invalid container status")
        if type(item["exit_code"]) is not int or not 0 <= item["exit_code"] <= 255 or type(item["oom_killed"]) is not bool:
            raise ValueError("Invalid container termination")
        if not all(isinstance(item[key], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(?:Z|[+-]\d{2}:\d{2})", item[key])
                   for key in ("started_at", "finished_at")):
            raise ValueError("Invalid container timestamps")
        if not isinstance(item["image_id"], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", item["image_id"]):
            raise ValueError("Invalid image identity")
        result[service] = item
    return result


def _collect(root: Path, output: Path, since: datetime, until: datetime,
             containers: dict | None = None, containers_incomplete: bool = False) -> int:
    from app.models.diagnostics import BundleRequest
    from app.services.diagnostics.bundle import build_bundle
    from app.services.diagnostics.sanitize import encode_event
    from app.services.diagnostics.schema import DiagnosticLimits
    from app.services.diagnostics.snapshot import collect_snapshot
    from app.services.diagnostics.store import DiagnosticStore, WriterActiveError

    limits = DiagnosticLimits()
    if not root.is_dir() or not output.parent.is_dir() or output.is_relative_to(root):
        return 2
    try:
        _no_links(root)
        _no_links(output.parent)
        if output.exists() or output.is_symlink():
            return 2
        request = BundleRequest(from_utc=since, to_utc=until)
    except (ValueError, OSError):
        return 2
    try:
        store = DiagnosticStore(root, limits)
        store.open()
    except WriterActiveError:
        return 3
    except (OSError, ValueError):
        return 3
    try:
        # Keep the complete output allowance available on its own filesystem.
        try:
            if shutil.disk_usage(output.parent).free < limits.bundle_bytes + limits.min_free_bytes:
                return 4
        except OSError:
            return 4
        complete = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(output, flags, 0o660)
        except FileExistsError:
            return 2
        except OSError:
            return 3
        try:
            event = encode_event({
                "schema_version": 1, "event_id": str(uuid4()), "boot_id": str(uuid4()),
                "timestamp_utc": datetime.now(timezone.utc).isoformat(), "component": "backend",
                "level": "INFO", "event_code": "offline_export_requested", "origin": "server",
            })
            if not store.append(event, stream="baseline"):
                return 4
            snapshot = collect_snapshot(request, cutoff_at=datetime.now(timezone.utc),
                                        store=store, metadata_provider=_unavailable)
            try:
                snapshot.runtime = {**snapshot.runtime, "containers": containers or {}}
                if not containers or containers_incomplete:
                    snapshot.gaps += ("container_state_unavailable",)
                    snapshot.partial = True
                temporary = store.root / "bundles" / f"offline-{uuid4()}.zip"
                built = build_bundle(snapshot, temporary, limits, collection_mode="offline",
                                     audit_reconciliation="pending_sql")
                try:
                    with os.fdopen(fd, "wb", buffering=0) as destination:
                        fd = -1
                        with built.path.open("rb") as source:
                            shutil.copyfileobj(source, destination, length=65536)
                        destination.flush()
                        os.fsync(destination.fileno())
                    if output.stat().st_size != built.size_bytes:
                        return 4
                    complete = True
                    return 0
                finally:
                    built.path.unlink(missing_ok=True)
            finally:
                snapshot.release()
        except Exception:
            return 4
        finally:
            if fd >= 0:
                os.close(fd)
            # A partially copied archive is never a valid deliverable.
            if not complete:
                output.unlink(missing_ok=True)
    finally:
        store.close()


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    root = Path(os.path.abspath(args.root))
    if not args.since or not args.until or not args.output:
        print("--since, --until and --output are required", file=sys.stderr)
        return 2
    try:
        since, until = _utc(args.since), _utc(args.until)
        if not since < until or until - since > timedelta(days=7):
            return 2
        output = Path(os.path.abspath(args.output))
        if args.dry_run:
            if not root.is_dir() or not output.parent.is_dir() or output.is_relative_to(root) or output.exists():
                return 2
            _no_links(root)
            _no_links(output.parent)
            print("Offline diagnostics: arguments accepted; no application modules loaded")
            return 0
        containers = None
        if args.container_state_stdin:
            containers = _container_state(sys.stdin.buffer.read(8193))
        return _collect(root, output, since, until, containers, args.container_state_incomplete)
    except (ValueError, OSError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
