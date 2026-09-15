"""Acceptance benchmark for the physical raw-document export path.

It never creates application Job or Document rows: generated files live in a
unique temporary directory and are removed after ZIP and retrieval checks.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from typing import Any
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.bulk_export import ExportDocument, build_export_parts, partition_documents
from test_scripts.probe_parallel_retrieval import run_parallel_retrieval
from test_scripts.probe_sources import load_cases


_MIB = 1024 * 1024
_PART_LIMIT = 250 * _MIB


def _rss_bytes() -> int:
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        get_memory_info = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        get_memory_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        get_memory_info.restype = wintypes.BOOL
        if get_memory_info(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (FileNotFoundError, OSError, IndexError):
        import resource

        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value) if sys.platform == "darwin" else int(value) * 1024


def _write_sparse(path: Path, size: int, marker: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as target:
        if size:
            target.seek(size - 1)
            target.write(bytes([marker % 256]))


def _generated_documents(root: Path, documents: int, total_bytes: int) -> list[ExportDocument]:
    result: list[ExportDocument] = []
    base, remainder = divmod(total_bytes, documents)
    for index in range(documents):
        size = base + (1 if index < remainder else 0)
        path = root / f"source-{index:04d}.bin"
        _write_sparse(path, size, index)
        stat = path.stat()
        result.append(ExportDocument(f"benchmark-{index:04d}", path.name, path, size, stat.st_mtime_ns))
    return result


def _crc_errors(parts: list[Any]) -> list[str]:
    errors: list[str] = []
    for part in parts:
        with zipfile.ZipFile(part.path) as archive:
            failed = archive.testzip()
        if failed is not None:
            errors.append(f"{part.filename}:{failed}")
    return errors


def _write_result(output: Path, result: dict[str, Any]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--documents", type=int, default=1000)
    parser.add_argument("--total-mb", type=int, default=700)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--retrieval-cases", type=Path, default=Path(__file__).with_name("glossary-probe-cases.json"))
    parser.add_argument("--clients", type=int, default=5)
    parser.add_argument("--queries-per-client", type=int, default=20)
    args = parser.parse_args()
    if args.documents <= 0 or args.total_mb <= 0:
        parser.error("--documents and --total-mb must be positive")

    total_bytes = args.total_mb * _MIB
    work_root = (args.work_root or Path(tempfile.gettempdir())).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    cases = load_cases(args.retrieval_cases)
    baseline = run_parallel_retrieval(cases, state="on", clients=args.clients, queries_per_client=args.queries_per_client)
    rss_baseline = _rss_bytes()
    samples = [rss_baseline]
    sampling = threading.Event()
    barrier = threading.Barrier(2)
    state: dict[str, Any] = {"parts": [], "error": None, "started": None, "finished": None}

    def sample_memory() -> None:
        while not sampling.wait(0.05):
            samples.append(_rss_bytes())

    def builder(documents: list[ExportDocument], building: Path) -> None:
        try:
            # The builder is ready to write before retrieval is released.
            # This makes the timestamp an auditable lower bound for overlap.
            state["started"] = time.monotonic_ns()
            barrier.wait(timeout=30)
            payload_limit = _PART_LIMIT - _MIB
            parts = partition_documents(documents, payload_limit)
            state["parts"] = build_export_parts(1, parts, building, archive_limit_bytes=_PART_LIMIT)
        except BaseException as exc:  # retain diagnostic in artifact
            state["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            state["finished"] = time.monotonic_ns()

    cleanup_ok = False
    with tempfile.TemporaryDirectory(prefix="bulk-export-benchmark-", dir=work_root) as temp_name:
        temp_dir = Path(temp_name).resolve()
        if temp_dir.parent != work_root:
            raise RuntimeError("temporary benchmark directory escaped --work-root")
        source_dir, building_dir = temp_dir / "source", temp_dir / "building"
        inputs = _generated_documents(source_dir, args.documents, total_bytes)
        thread = threading.Thread(target=builder, args=(inputs, building_dir), daemon=True)
        sampler = threading.Thread(target=sample_memory, daemon=True)
        sampling.clear()
        thread.start()
        sampler.start()
        retrieval_start = time.monotonic_ns()
        barrier.wait(timeout=30)
        during = run_parallel_retrieval(cases, state="on", clients=args.clients, queries_per_client=args.queries_per_client)
        retrieval_finished = time.monotonic_ns()
        thread.join()
        sampling.set()
        sampler.join(timeout=1)
        crc_errors = _crc_errors(state["parts"])
        part_sizes = [part.size_bytes for part in state["parts"]]
        elapsed_ms = round((state["finished"] - state["started"]) / 1_000_000, 3) if state["started"] else 0
        full_overlap = bool(state["started"] and state["finished"] and state["started"] <= retrieval_start and state["finished"] >= retrieval_finished)
    cleanup_ok = not temp_dir.exists()
    baseline_p95 = float(baseline["timings"]["p95_ms"])
    during_p95 = float(during["timings"]["p95_ms"])
    degradation = round(((during_p95 - baseline_p95) / baseline_p95) * 100, 3) if baseline_p95 else None
    failures = []
    if state["error"]: failures.append(state["error"])
    if sum(doc.size_bytes for doc in inputs) != total_bytes: failures.append("generated input byte count mismatch")
    if len(inputs) != args.documents: failures.append("generated document count mismatch")
    if any(size > _PART_LIMIT for size in part_sizes): failures.append("ZIP part exceeds 250 MiB")
    if crc_errors: failures.append("ZIP CRC check failed")
    if not cleanup_ok: failures.append("temporary benchmark data was not cleaned")
    if baseline["errors"] or during["errors"]: failures.append("retrieval errors")
    if not full_overlap: failures.append("inconclusive: no full export/retrieval overlap")
    if degradation is None or degradation > 20: failures.append("retrieval p95 degradation exceeds 20%")
    result = {
        "schema_version": 1,
        "input": {"documents": args.documents, "total_bytes": total_bytes},
        "build": {"elapsed_ms": elapsed_ms, "part_sizes_bytes": part_sizes, "crc_errors": crc_errors, "cleanup_ok": cleanup_ok},
        "memory": {"baseline_rss_bytes": rss_baseline, "peak_rss_bytes": max(samples), "delta_rss_bytes": max(samples) - rss_baseline},
        "retrieval": {"baseline": baseline, "during_export": during, "full_overlap": full_overlap, "p95_degradation_pct": degradation},
        "acceptance": {"passed": not failures, "failures": failures},
    }
    _write_result(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
