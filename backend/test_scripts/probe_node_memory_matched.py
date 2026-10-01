"""Bounded matched-age numeric memory experiment on the disposable CT102 project."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
from zipfile import ZipFile

from probe_diagnostics_http import Client
from probe_diagnostics_http_matrix import (
    QUERIES, _capture_search_counts, _container_pid, _tree_members, _tree_rss_sample,
    _wait_bundle, _worker_phase,
)

PROJECT = "okf-diag-integration-20261001"
EXPECTED = {"backend": "sha256:17dbaf92358dbbac7b82f731448194bc984183413ec1f748ad7d49a6d73cdce5",
            "frontend": "sha256:ba0f7a01c178201160b43f87cc66f60dd74e808d588d06f445fa87010627b266"}


def docker_json(role):
    return json.loads(subprocess.check_output(["docker", "inspect", f"{PROJECT}-{role}-1"], text=True))[0]


def read_node_rows(directory):
    rows = []
    for file in sorted(directory.glob("*.jsonl")):
        for line in file.read_text().splitlines():
            row = json.loads(line)
            assert row["role"] in {"next", "launcher"} and row["type"] in {"start", "sample", "gc", "stop"}
            assert all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for key, value in row.items()
                       if key not in {"role", "type"})
            rows.append(row)
    return rows


def smaps(pid):
    values = {}
    for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"Rss", "Pss", "Private_Clean", "Private_Dirty", "Anonymous", "Swap"}:
            values[key] = int(value.split()[0]) * 1024
    nspid = next(line.partition(":")[2].split() for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                 if line.startswith("NSpid:"))
    return {"linux_pid": pid, "namespace_pid": int(nspid[-1]), **values}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-name", choices=["memory-matched-20261001", "memory-matched-20261001-v2"],
                        default="memory-matched-20261001")
    args = parser.parse_args()
    root = args.root.resolve()
    assert root == Path("/opt/okf-diag-integration-20261001")
    out = root / args.output_name
    out.mkdir(mode=0o700)
    deadline = time.monotonic() + 1800
    compose = ["docker", "compose", "--project-name", PROJECT, "--env-file", str(root / "runtime-private.env"),
               "-f", str(root / "docker-compose.yml"), "-f", str(root / "compose-integration.json")]
    result = {"diagnostic_only": True, "hypothesis": "RSS reflects heap expansion and natural GC history, rather than diagnostic retention",
              "source_revision": "2a4a0e0", "images": EXPECTED, "fresh_frontend_each_case": True,
              "forced_gc": False, "inspector": False, "concurrency": 8, "start_age_seconds": 10,
              "warmup_seconds": 30, "measurement_seconds": 60, "idle_seconds": 60, "rows": []}
    active = None
    admin = None
    done = None
    sampler = None
    try:
        order = [(False, False), (True, False), (False, True), (True, True)]
        cases = order + list(reversed(order))
        for index, (capture, zip_work) in enumerate(cases):
            if time.monotonic() >= deadline:
                raise TimeoutError("bounded experiment deadline")
            directory = out / f"case-{index + 1:02d}"
            directory.mkdir(); os.chown(directory, 1000, 1000)
            override = {"services": {"frontend": {"environment": {
                "NODE_OPTIONS": "--import=/numeric-memory/numeric-memory-observer.mjs",
                "OKF_TEST_NUMERIC_MEMORY_DIR": "/numeric-output"}, "volumes": [
                str(root / "frontend/scripts/numeric-memory-observer.mjs") + ":/numeric-memory/numeric-memory-observer.mjs:ro",
                str(directory) + ":/numeric-output"]}}}
            override_path = out / "observer-compose.json"
            override_path.write_text(json.dumps(override) + "\n")
            with (out / "setup-private.log").open("a") as log:
                subprocess.run(compose + ["-f", str(override_path), "up", "--no-build", "--no-deps", "--force-recreate", "-d", "frontend"],
                               check=True, cwd=root, stdout=log, stderr=subprocess.STDOUT)
            startup_deadline = time.monotonic() + 12
            first = None
            while time.monotonic() < startup_deadline:
                values = read_node_rows(directory)
                first = next((r for r in values if r["role"] == "next"), None)
                if first:
                    break
                time.sleep(.1)
            if first is None:
                raise RuntimeError("observer handshake unavailable")
            birth = first["birth_unix_ms"] / 1000
            for role, image in EXPECTED.items():
                assert docker_json(role)["Image"] == image
            frontend = docker_json("frontend")
            assert frontend["Config"]["Cmd"] == ["node", "diagnostics-runner.mjs"]
            while time.time() < birth + 8:
                try:
                    with urllib.request.urlopen("http://127.0.0.1:18085/health", timeout=1) as health:
                        assert health.status == 200
                        health.read()
                    break
                except (OSError, AssertionError):
                    time.sleep(.1)
            else:
                raise RuntimeError("HTTP startup readiness exceeded matched-age budget")
            pids = {role: _container_pid(f"{PROJECT}-{role}-1") for role in EXPECTED}
            admin = Client("http://127.0.0.1:18085"); admin.login(f"diag.bench{index + 4:02d}")
            clients = [Client("http://127.0.0.1:18085") for _ in range(8)]
            for client in clients:
                client.login("demo.user")
            if capture:
                active = admin.call("POST", "/api/admin/diagnostics/sessions",
                                    {"scope": "system", "capture_level": "standard", "minutes": 5})["id"]
            fingerprints = {}
            calls = 0
            counter_lock = threading.Lock()
            def one(client_index):
                nonlocal calls
                with counter_lock:
                    query_index = calls % 3; calls += 1
                started = time.perf_counter_ns()
                response = clients[client_index].call("POST", "/api/search", {
                    "query": QUERIES[query_index], "locale": "en", "use_glossary": False,
                    "dense": False, "bm25": True, "top_k": 5})
                elapsed = (time.perf_counter_ns() - started) / 1_000_000
                assert response["hits"]
                digest = sha256(json.dumps(response, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                with counter_lock:
                    assert fingerprints.setdefault(query_index, digest) == digest
                return elapsed
            linux_rows = []; observer_errors = []; done = threading.Event()
            def observe():
                try:
                    while not done.wait(.25):
                        tree = _tree_rss_sample(pids["frontend"])
                        phase = _worker_phase(pids["backend"])
                        linux_rows.append({"age_seconds": time.time() - birth, "tree_rss_bytes": tree["total"],
                                           "unknown_comparisons": tree["unknown_comparisons"],
                                           "processes": [smaps(pid) for pid in _tree_members(pids["frontend"], Path("/proc"))],
                                           "backend_phase": phase[0] if phase else None})
                except Exception as exc:
                    observer_errors.append(type(exc).__name__)
            sampler = threading.Thread(target=observe, daemon=True); sampler.start()
            if time.time() > birth + 10:
                raise RuntimeError("matched-age setup exceeded 10 seconds")
            time.sleep(max(0, birth + 10 - time.time()))
            bundle = {}; builder = None
            def build():
                try:
                    now = datetime.now(timezone.utc)
                    body = {"session_id": active} if active else {"from_utc": (now - timedelta(minutes=2)).isoformat(), "to_utc": now.isoformat()}
                    bundle["submit_age"] = time.time() - birth
                    bundle["id"] = admin.call("POST", "/api/admin/diagnostics/bundles", body)["id"]
                    bundle.update(_wait_bundle(admin, bundle["id"], poll_interval=3, timeout=45))
                    bundle["ready_age"] = time.time() - birth
                except Exception as exc:
                    bundle["failure_type"] = type(exc).__name__
            latencies = []
            with ThreadPoolExecutor(max_workers=8) as pool:
                while time.time() < birth + 40:
                    list(pool.map(one, range(8)))
                warm_calls = calls
                measurement_start_age = time.time() - birth
                measurement_until = max(birth + 100, time.time() + 60)
                if zip_work:
                    builder = threading.Thread(target=build, daemon=True); builder.start()
                while time.time() < measurement_until:
                    latencies.extend(pool.map(one, range(8)))
            measurement_end_age = time.time() - birth
            assert len(latencies) >= 1000
            assert measurement_end_age - measurement_start_age >= 60
            if builder:
                builder.join(timeout=45)
                assert not builder.is_alive() and bundle.get("status") == "ready"
            time.sleep(max(0, birth + measurement_end_age + 60 - time.time()))
            done.set(); sampler.join(timeout=2); assert not observer_errors
            node_rows = read_node_rows(directory)
            assert {r["role"] for r in node_rows} == {"next", "launcher"}
            if active:
                admin.call("POST", "/api/admin/diagnostics/sessions/" + active + "/stop", {})
                captured = _capture_search_counts(root / "diagnostics", active)
                assert all(number == calls for number in captured.values())
            else:
                captured = None
            row = {"case": index + 1, "capture": "standard" if capture else "off", "zip": zip_work,
                   "warmup_requests": warm_calls, "requests": len(latencies), "search_calls": calls,
                   "measurement_start_age": measurement_start_age, "measurement_end_age": measurement_end_age,
                   "idle_end_age": time.time() - birth,
                   "response_fingerprints": fingerprints, "capture_counts": captured,
                   "p95_ms": sorted(latencies)[int(.95 * (len(latencies) - 1))], "bundle": bundle if zip_work else None,
                   "node_samples": node_rows, "linux_samples": linux_rows, "observer_errors": observer_errors}
            if zip_work:
                content = admin.call("GET", "/api/admin/diagnostics/bundles/" + bundle["id"] + "/download")
                with ZipFile(BytesIO(content)) as archive:
                    assert archive.testzip() is None
                    manifest = json.loads(archive.read("manifest.json"))
                    assert all(sha256(archive.read(name)).hexdigest() == digest for name, digest in manifest["checksums"].items())
                row["bundle"].update(crc=True, sha256=sha256(content).hexdigest(), manifest_events=manifest["counts"]["events"])
                admin.call("DELETE", "/api/admin/diagnostics/bundles/" + bundle["id"])
            result["rows"].append(row)
            (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps({"case": index + 1, "capture": row["capture"], "zip": zip_work,
                              "requests": len(latencies), "node_samples": len(node_rows),
                              "frontend_peak_rss_bytes": max(r["tree_rss_bytes"] for r in linux_rows),
                              "next_peak_heap_used_bytes": max(r["heap_used_bytes"] for r in node_rows if r["role"] == "next"),
                              "next_major_gc": max(r["major_count"] for r in node_rows if r["role"] == "next")}), flush=True)
            active = None
        result["complete"] = True
    except BaseException as exc:
        result["complete"] = False; result["failure_type"] = type(exc).__name__
        raise
    finally:
        if done:
            done.set()
        if sampler:
            sampler.join(timeout=2)
        if active and admin:
            try:
                admin.call("POST", "/api/admin/diagnostics/sessions/" + active + "/stop", {})
            except Exception as exc:
                result["capture_cleanup_failure"] = type(exc).__name__
        (out / "results.json").write_text(json.dumps(result, indent=2) + "\n")
        with (out / "stop-private.log").open("w") as log:
            subprocess.run(compose + ["stop", "--timeout", "30"], cwd=root, stdout=log, stderr=subprocess.STDOUT, check=True)
        print("memory_experiment_complete=" + str(result.get("complete")), flush=True)


if __name__ == "__main__":
    main()
