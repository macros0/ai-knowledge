"""Same-input prepare/ZIP algorithm control with live synthetic HTTP workload.

Run only on the disposable stack. Private files never become API downloads;
this control does not prove publication audit or replace the online ZIP matrix.
"""
import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import random
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4
from zipfile import ZipFile

from probe_diagnostics import _series
from probe_diagnostics_http import Client
from probe_diagnostics_http_matrix import QUERIES, _phase_distributions, _response_fingerprint
from app.models.diagnostics import BundleRequest
from app.services.diagnostics.bundle_queue import DiagnosticBundleQueue
from app.services.diagnostics.sanitize import encode_event
from app.services.diagnostics.schema import DiagnosticLimits
from app.services.diagnostics import snapshot as snapshot_api
from app.services.diagnostics.store import DiagnosticStore


def _stream_hash(handle):
    digest = sha256()
    for block in iter(lambda: handle.read(65536), b""):
        digest.update(block)
    return digest.hexdigest()


def seed(store, records):
    # Identical timestamp/UUIDs/bytes in current and immutable original controls.
    stamp = datetime(2026, 9, 30, tzinfo=timezone.utc)
    source = store.root / "events" / "baseline" / "fixed.jsonl"
    pending = bytearray()
    digest = sha256()
    for number in range(records):
        encoded = encode_event({
            "schema_version": 1, "event_id": str(UUID(int=number + 2)),
            "request_id": str(UUID(int=number + records + 2)), "boot_id": str(UUID(int=1)),
            "timestamp_utc": stamp.isoformat(), "component": "backend", "origin": "server",
            "level": "ERROR", "event_code": "operation_failed", "error_code": "internal_error",
            "stage": "search", "doc_id": "1" * 16,
        })
        pending.extend(encoded)
        digest.update(encoded)
        if len(pending) >= 65536:
            store.write_bytes(source, bytes(pending), append=True)
            pending.clear()
    if pending:
        store.write_bytes(source, bytes(pending), append=True)
    return stamp, source.stat().st_size, digest.hexdigest()


def run(root, output, *, records, repeats, concurrencies, warmup_seconds,
        measurement_seconds, min_requests, base_url, reference_version="current", diagnostic_aba=False,
        retain_timelines=False):
    if diagnostic_aba and (reference_version != "current" or repeats != 1 or concurrencies != [1]):
        raise ValueError("Diagnostic A/B/A requires current images, one repeat, concurrency one")
    if root.exists():
        raise FileExistsError("Use a new private fixed-input root")
    rows = []
    admin = Client(base_url)
    admin.login()
    with DiagnosticStore(root, DiagnosticLimits(min_free_bytes=0)) as store:
        stamp, input_bytes, input_hash = seed(store, records)
        queue = DiagnosticBundleQueue(store)
        queue._running = True  # Private worker supervisor; no SQL submit/publication.
        clients = [Client(base_url) for _ in range(max(concurrencies))]
        for client in clients:
            client.login()
        search = lambda query: {"query": query, "locale": "en", "dense": False,
                                "bm25": True, "use_glossary": False, "top_k": 5}
        fingerprints = {query: _response_fingerprint(clients[0].call("POST", "/api/search", search(query)))
                        for query in QUERIES}
        try:
            for repeat in range(1, repeats + 1):
                for concurrency in concurrencies:
                    cases = [("baseline", False), ("baseline", True), ("standard", True), ("detailed", True)]
                    if reference_version == "original":
                        cases = cases[:2]
                    tail = cases[1:]
                    random.Random(313 + repeat + concurrency).shuffle(tail)
                    cases = cases[:1] + tail
                    labelled_cases = [(mode, with_zip, None, None) for mode, with_zip in cases]
                    if diagnostic_aba:
                        labelled_cases = []
                        for level in ("standard", "detailed"):
                            labelled_cases.extend((mode, False, level + "-capture", position)
                                                  for mode, position in (("baseline", "A1"), (level, "B"),
                                                                         ("baseline", "A2")))
                            labelled_cases.extend((level, zipped, level + "-zip", position)
                                                  for zipped, position in ((False, "A1"), (True, "B"),
                                                                           (False, "A2")))
                    for mode, with_zip, experiment, position in labelled_cases:
                        case_started = time.perf_counter()
                        case_started_utc = datetime.now(timezone.utc).isoformat()
                        print(json.dumps({"event": "case_started", "mode": mode, "with_zip": with_zip,
                                          "experiment": experiment, "position": position,
                                          "at_monotonic": case_started}), flush=True)
                        session = admin.call("POST", "/api/admin/diagnostics/sessions", {
                            "scope": "system", "minutes": 5, "capture_level": mode}) if mode != "baseline" else None
                        if session:
                            time.sleep(2)  # Allow the one-second frontend control poll to acknowledge start.
                        counter = 0
                        lock = threading.Lock()
                        built = {}
                        phases = []
                        builder = None

                        def request_once():
                            nonlocal counter
                            with lock:
                                index = counter % concurrency
                                query = QUERIES[counter % len(QUERIES)]
                                counter += 1
                            result = clients[index].call("POST", "/api/search", search(query))
                            if not result.get("hits") or _response_fingerprint(result) != fingerprints[query]:
                                raise RuntimeError("Fixed-input workload search differs from baseline")
                            return SimpleNamespace(status_code=200)

                        def build_once():
                            job = str(uuid4())
                            snapshot = None
                            destination = store.root / "bundles" / (job + ".part")
                            try:
                                started = time.perf_counter()
                                phase_start = started
                                def on_child(child):
                                    queue._child = child
                                options = {"cutoff_at": datetime.now(timezone.utc), "store": store,
                                           "metadata_provider": lambda _: ({"status": "unknown", "dependencies": {}}, [])}
                                prepare = getattr(snapshot_api, "collect_prepared_snapshot", None)
                                if prepare is None:
                                    snapshot = snapshot_api.collect_snapshot(
                                        BundleRequest(from_utc=stamp - timedelta(seconds=1)), **options)
                                else:
                                    snapshot = prepare(BundleRequest(from_utc=stamp - timedelta(seconds=1)),
                                                       job_id=job, on_child=on_child, **options)
                                phase_end = time.perf_counter()
                                phases.append({"phase": "prepare", "started": phase_start, "finished": phase_end})
                                with snapshot.paths[0].open("rb") as handle:
                                    prepared_hash = _stream_hash(handle)
                                if prepared_hash != input_hash or snapshot.counts["events"] != records:
                                    raise RuntimeError("Fixed input changed during preparation")
                                phase_start = time.perf_counter()
                                result = queue._build_isolated(snapshot, destination, job)
                                phases.append({"phase": "zip", "started": phase_start, "finished": time.perf_counter()})
                                with ZipFile(destination) as archive:
                                    if archive.testzip() is not None:
                                        raise RuntimeError("Fixed-input ZIP CRC failed")
                                    with archive.open("events/backend.jsonl") as handle:
                                        actual_hash = _stream_hash(handle)
                                if actual_hash != input_hash or result.manifest["counts"]["events"] != records:
                                    raise RuntimeError("Fixed-input ZIP content differs")
                                built.update(status="ready", input_bytes=input_bytes, input_sha256=input_hash,
                                             events=records, output_bytes=result.size_bytes,
                                             elapsed_seconds=time.perf_counter() - started, crc_pass=True)
                            except Exception as exc:
                                built.update(status="failed", error_type=type(exc).__name__)
                            finally:
                                queue._child = None
                                if snapshot:
                                    snapshot.release()
                                store.delete_tree(destination)

                        def start_builder():
                            nonlocal builder
                            builder = threading.Thread(target=build_once, daemon=True)
                            builder.start()

                        try:
                            measured = _series(request_once, warmup_seconds, min_requests,
                                               measurement_seconds=measurement_seconds, concurrency=concurrency,
                                               on_samples_start=start_builder if with_zip else None,
                                               child_pid=lambda: getattr(queue._child, "pid", None))
                            if builder:
                                builder.join(timeout=300)
                            if with_zip and (builder.is_alive() or built.get("status") != "ready"):
                                raise RuntimeError("Fixed-input private build did not complete")
                        finally:
                            if session:
                                admin.call("POST", f"/api/admin/diagnostics/sessions/{session['id']}/stop", {})
                                time.sleep(2)
                        intervals = [(start, end, (end - start) * 1000) for start, end in measured.pop("_intervals")]
                        if diagnostic_aba or retain_timelines:
                            measured.update(experiment=experiment, position=position,
                                            case_started_monotonic=case_started, case_started_utc=case_started_utc,
                                            issued_requests_with_warmup=counter,
                                            session_id=session["id"] if session else None,
                                            request_intervals=[{"started": start, "finished": end,
                                                                "duration_ms": duration}
                                                               for start, end, duration in intervals],
                                            phases=phases)
                        measured.update(mode=mode, repeat=repeat, concurrency=concurrency,
                                        with_zip=with_zip, bundle=built if with_zip else None,
                                        phase_latencies=_phase_distributions(intervals, phases),
                                        input_bytes=input_bytes, input_sha256=input_hash,
                                        response_equality_pass=True)
                        rows.append(measured)
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_text(json.dumps({"schema_version": 1,
                            "warmup_seconds": warmup_seconds, "measurement_seconds": measurement_seconds,
                            "minimum_request_count": min_requests, "records": records,
                            "build_revision": os.getenv("OKF_BUILD_REVISION", "unknown"),
                            "reference_version": reference_version,
                            "diagnostic_aba": diagnostic_aba,
                            "retain_timelines": retain_timelines,
                            "scope": "private same-input algorithm control; no API publication",
                            "rss_scope": "private supervisor and child, not live backend/frontend trees",
                            "phase_scope": "supervised function intervals including parent setup",
                            "rows": rows}, indent=2, sort_keys=True) + "\n")
                        print(json.dumps({key: measured[key] for key in
                                          ("mode", "repeat", "concurrency", "with_zip", "p95_ms")}), flush=True)
        finally:
            queue.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--records", type=int, default=20000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, choices=(1, 8), action="append")
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--measurement-seconds", type=int, default=60)
    parser.add_argument("--min-requests", type=int, default=1000)
    parser.add_argument("--base-url", default="http://127.0.0.1:18084")
    parser.add_argument("--reference-version", choices=("current", "original"), default="current")
    parser.add_argument("--diagnostic-aba", action="store_true",
                        help="Bounded cause diagnosis only; retain timelines and bracketing controls, not SLA")
    parser.add_argument("--retain-timelines", action="store_true",
                        help="Preserve every measured request interval without changing acceptance cases")
    args = parser.parse_args()
    if not 100 <= args.records <= 50000 or args.repeats < 1:
        parser.error("Use 100..50000 synthetic records and at least one repeat")
    run(args.root, args.output, records=args.records, repeats=args.repeats,
        concurrencies=args.concurrency or [1, 8], warmup_seconds=args.warmup_seconds,
        measurement_seconds=args.measurement_seconds, min_requests=args.min_requests,
        base_url=args.base_url, reference_version=args.reference_version, diagnostic_aba=args.diagnostic_aba,
        retain_timelines=args.retain_timelines)
