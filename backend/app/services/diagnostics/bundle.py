"""Fixed-entry, streaming diagnostics ZIP. Inputs are validated twice."""
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

from .sanitize import _encode_validated_event, sanitize_event
from .schema import DiagnosticLimits, MAX_EVENT_BYTES


class BundleTooLarge(RuntimeError):
    code = "diagnostic_bundle_too_large"


class BundleInputChanged(RuntimeError):
    code = "diagnostic_bundle_input_changed"


_ENTRIES = (
    "summary.txt", "manifest.json", "events/backend.jsonl", "events/frontend.jsonl",
    "events/browser.jsonl", "snapshots/runtime.json", "snapshots/operations.json",
)
_COMPONENTS = ("backend", "frontend", "browser")


@dataclass(frozen=True)
class BuiltBundle:
    path: Path
    filename: str
    size_bytes: int
    sha256: str
    manifest: dict


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _safe_lines(paths, *, counts=None, component_filter=None):
    for path in paths:
        with path.open("rb") as handle:
            while line := handle.readline(MAX_EVENT_BYTES + 1):
                if not line.endswith(b"\n"):
                    if counts is not None:
                        counts["truncated"] = counts.get("truncated", 0) + 1
                    while len(line) > MAX_EVENT_BYTES and not line.endswith(b"\n"):
                        line = handle.readline(MAX_EVENT_BYTES + 1)
                        if not line:
                            break
                    continue
                try:
                    raw = json.loads(line)
                    if component_filter is not None and (
                        not isinstance(raw, dict) or raw.get("component") != component_filter
                    ):
                        continue
                    event = sanitize_event(raw)
                except (ValueError, TypeError, UnicodeDecodeError):
                    event = None
                if event is None:
                    if counts is not None:
                        counts["invalid"] = counts.get("invalid", 0) + 1
                    continue
                yield event["component"], _encode_validated_event(event)


def _file_hash(path):
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def estimate_bundle_upper(snapshot) -> int:
    input_bytes = sum(path.stat().st_size for path in snapshot.paths)
    return 65536 + input_bytes * 2 + len(_json_bytes(snapshot.runtime)) + len(_json_bytes(snapshot.operations))


def build_bundle(snapshot, destination: Path, limits: DiagnosticLimits, *,
                 collection_mode: str = "online", audit_reconciliation: str = "complete",
                 reserved_upper: int | None = None) -> BuiltBundle:
    if collection_mode not in {"online", "offline"} or audit_reconciliation not in {"complete", "pending_sql"}:
        raise ValueError("Invalid diagnostic collection metadata")
    store = snapshot.store
    destination = store.safe_path(destination)
    if destination.parent != store.root / "bundles" or destination.exists():
        raise ValueError("Invalid diagnostics bundle destination")
    for path in snapshot.paths:
        store.safe_path(path)
    source_sizes = {path: path.stat().st_size for path in snapshot.paths}
    input_bytes = sum(source_sizes.values())
    runtime = _json_bytes(snapshot.runtime)
    operations = _json_bytes(snapshot.operations)
    # DEFLATE can expand incompressible input. Reserve a complete worst-case
    # snapshot+ZIP allowance before opening any output, including headers.
    upper = 65536 + input_bytes * 2 + len(runtime) + len(operations)
    if upper > limits.bundle_bytes:
        raise BundleTooLarge()
    if reserved_upper is not None and (type(reserved_upper) is not int or upper > reserved_upper):
        raise BundleInputChanged()
    reservation = store.reserve(upper) if reserved_upper is None else None
    try:
        raw_hashes = {path: _file_hash(path) for path in snapshot.paths}
        counts = dict(snapshot.counts)
        hashes = {component: sha256() for component in _COMPONENTS}
        sizes = {component: 0 for component in _COMPONENTS}
        coverage = {component: {"from_utc": None, "to_utc": None} for component in _COMPONENTS}
        frontend_build_ids = set()
        revalidated = 0
        for component, encoded in _safe_lines(snapshot.paths, counts=counts):
            if component not in hashes:
                counts["invalid"] = counts.get("invalid", 0) + 1
                continue
            hashes[component].update(encoded)
            sizes[component] += len(encoded)
            event = json.loads(encoded)
            stamp = event["timestamp_utc"]
            if component == "frontend" and event.get("build_id"):
                frontend_build_ids.add(event["build_id"])
            first = coverage[component]["from_utc"]
            last = coverage[component]["to_utc"]
            coverage[component]["from_utc"] = min(first, stamp) if first else stamp
            coverage[component]["to_utc"] = max(last, stamp) if last else stamp
            revalidated += 1
        counts["events"] = revalidated
        rejected = counts.get("invalid", 0) > 0 or counts.get("truncated", 0) > 0
        partial = snapshot.partial or rejected
        gaps = list(snapshot.gaps)
        if rejected and "invalid_input" not in gaps:
            gaps.append("invalid_input")
        summary = (
            "OKF diagnostics v1\n"
            f"Cutoff UTC: {snapshot.cutoff_at.isoformat()}\n"
            f"Events: {revalidated}\n"
            f"Partial: {'yes' if partial else 'no'}\n"
        ).encode("ascii", errors="replace")
        checksums = {
            "summary.txt": sha256(summary).hexdigest(),
            "snapshots/runtime.json": sha256(runtime).hexdigest(),
            "snapshots/operations.json": sha256(operations).hexdigest(),
        }
        checksums.update({f"events/{name}.jsonl": digest.hexdigest() for name, digest in hashes.items()})
        revision = os.environ.get("OKF_BUILD_REVISION", "unknown")
        if not re.fullmatch(r"[a-f0-9]{7,40}", revision):
            revision = "unknown"
        manifest = {
            "format_version": 1,
            "backend_schema_version": 1,
            "frontend_build_ids": sorted(frontend_build_ids) or ["unknown"],
            "collection_mode": collection_mode,
            "audit_reconciliation": audit_reconciliation,
            "build_revision": revision,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cutoff_at": snapshot.cutoff_at.isoformat(),
            "partial": partial,
            "gaps": gaps,
            "components": list(_COMPONENTS),
            "counts": counts,
            "counter_scopes": {"recorder": "backend_process", "frontend": "frontend_process"},
            "checksums": checksums,
            "limits": {"bundle_bytes": limits.bundle_bytes, "event_bytes": MAX_EVENT_BYTES},
            "coverage_bytes": {f"events/{key}.jsonl": value for key, value in sizes.items()},
            "coverage_utc": coverage,
        }
        try:
            with ZipFile(destination, mode="x", compression=ZIP_DEFLATED, compresslevel=1, allowZip64=False) as archive:
                archive.writestr(_ENTRIES[0], summary)
                archive.writestr(_ENTRIES[1], _json_bytes(manifest))
                for component in _COMPONENTS:
                    digest = sha256()
                    with archive.open(f"events/{component}.jsonl", "w", force_zip64=False) as target:
                        for _, encoded in _safe_lines(snapshot.paths, component_filter=component):
                            target.write(encoded)
                            digest.update(encoded)
                            if archive.fp.tell() > limits.bundle_bytes:
                                raise BundleTooLarge()
                    if digest.hexdigest() != checksums[f"events/{component}.jsonl"]:
                        raise BundleInputChanged()
                archive.writestr(_ENTRIES[5], runtime)
                archive.writestr(_ENTRIES[6], operations)
            # Component hashes cover exported valid events. Also check the raw
            # snapshot so late malformed/unknown-component lines cannot hide.
            for path, original_size in source_sizes.items():
                store.safe_path(path)
                if path.stat().st_size != original_size or _file_hash(path) != raw_hashes[path]:
                    raise BundleInputChanged()
            if destination.stat().st_size > limits.bundle_bytes:
                raise BundleTooLarge()
            with ZipFile(destination) as archive:
                if archive.namelist() != list(_ENTRIES) or archive.testzip() is not None:
                    raise BundleInputChanged()
            return BuiltBundle(destination, destination.name, destination.stat().st_size,
                               _file_hash(destination), manifest)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
    finally:
        if reservation is not None:
            reservation.release()
