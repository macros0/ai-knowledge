"""Sequential full HTTP measurements on a disposable simulation stack.

Only synthetic search terms and numeric results are persisted. The caller seeds
1 or 100 documents and selects the corresponding --corpus-size label.
"""

import argparse
import ctypes
import platform
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import threading
import time

if __package__:
    from .probe_diagnostics_http import Client
else:
    from probe_diagnostics_http import Client


DEFAULT_PROJECT = "okf-diag-levels-20260929"


def _containers(project):
    return {role: f"{project}-{role}-1" for role in ("backend", "frontend")}
QUERIES = (
    "Synthetic diagnostic restore drill",
    "synthetic document restore",
    "diagnostic drill document",
)


def _container_pid(name):
    result = subprocess.run(["docker", "inspect", "--format", "{{.State.Pid}}", name],
                            capture_output=True, text=True, check=True)
    return int(result.stdout)


def _image_ids(containers):
    return {role: subprocess.run(["docker", "inspect", "--format", "{{.Image}}", name],
                                 capture_output=True, text=True, check=True).stdout.strip()
            for role, name in containers.items()}


def _rss_bytes(pid, proc_root=Path("/proc")):
    try:
        for line in (proc_root / str(pid) / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _same_vm(left, right):
    """KCMP_VM compares address spaces, not command lines or shared pages."""
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "amd64"}:
        return None
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(ctypes.c_long(312), ctypes.c_int(left), ctypes.c_int(right),
                          ctypes.c_int(1), ctypes.c_ulong(0), ctypes.c_ulong(0))
    return None if result < 0 else result == 0


def _process_alive(pid, proc_root=Path("/proc")):
    try:
        state = (proc_root / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()[0]
        return state not in {"Z", "X"}
    except FileNotFoundError:
        return False
    except (OSError, IndexError):
        return None


def _address_space_rss(pid, members, *, rss_reader=_rss_bytes, vm_compare=_same_vm,
                       process_alive=_process_alive):
    """Conservative RSS sum, deduplicating only proven identical address spaces.

    Bracket child reads with VM comparisons: exec can change the VM while RSS
    is read. On a transition reread the now-distinct child. Unknown comparisons
    retain raw RSS; they never classify a real worker as an empty process.
    """
    def read(member):
        reading = rss_reader(member)
        if reading is None:
            reading = rss_reader(member)
        if reading is None:
            if member == pid:
                raise RuntimeError("Container RSS unavailable")
            if process_alive(member) is not False:
                raise RuntimeError("Child RSS unavailable")
        return reading

    representatives = {}
    raw = {}
    unknown = shared = 0
    for member in [pid, *sorted(set(members) - {pid})]:
        representative = None
        initial = {}
        for other in representatives:
            initial[other] = vm_compare(other, member)
        reading = read(member)
        if reading is None:  # Confirmed exited/zombie child, not an unreadable live worker.
            continue
        value = reading
        raw[member] = value
        for other, before in initial.items():
            if before is None:
                unknown += 1
            elif before:
                after = vm_compare(other, member)
                if after is True:
                    representative = other
                    break
                if after is None:
                    unknown += 1
                else:
                    value = read(member) or 0
        if representative is None:
            representatives[member] = value
        else:
            representatives[representative] = max(representatives[representative], value)
            shared += 1
    return {"total": sum(representatives.values()),
            "descendants": sum(value for member, value in representatives.items() if member != pid),
            "raw_total": sum(raw.values()),
            "raw_descendants": sum(value for member, value in raw.items() if member != pid),
            "unknown_comparisons": unknown, "shared_vm_members": shared}


def _tree_members(pid, proc_root):
    pending = [pid]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        try:
            children = (proc_root / str(current) / "task" / str(current) / "children").read_text().split()
            pending.extend(int(value) for value in children)
        except (OSError, ValueError):
            pass
    return seen


def _tree_rss_sample(pid, proc_root=Path("/proc")):
    return _address_space_rss(pid, _tree_members(pid, proc_root),
                              rss_reader=lambda member: _rss_bytes(member, proc_root),
                              process_alive=lambda member: _process_alive(member, proc_root))


def _tree_rss_bytes(pid, proc_root=Path("/proc")):
    sample = _tree_rss_sample(pid, proc_root)
    return sample["total"], sample["descendants"]


def _cgroup_rss_sample(pid, *, proc_root=Path("/proc"), cgroup_root=Path("/sys/fs/cgroup")):
    """Count every container process, even when the PID tree omits a child."""
    try:
        group = next(line.split("::", 1)[1] for line in
                     (proc_root / str(pid) / "cgroup").read_text().splitlines()
                     if line.startswith("0::"))
        directory = (cgroup_root / group.lstrip("/")).resolve()
        if not directory.is_relative_to(cgroup_root.resolve()):
            raise ValueError("Cgroup outside root")
        members = {int(value) for value in (directory / "cgroup.procs").read_text().split()}
        if pid not in members:
            raise ValueError("Container PID missing from cgroup")
    except (OSError, ValueError, StopIteration):
        members = _tree_members(pid, proc_root)
    return _address_space_rss(pid, members, rss_reader=lambda member: _rss_bytes(member, proc_root),
                              process_alive=lambda member: _process_alive(member, proc_root))


def _cgroup_rss_bytes(pid, *, proc_root=Path("/proc"), cgroup_root=Path("/sys/fs/cgroup")):
    sample = _cgroup_rss_sample(pid, proc_root=proc_root, cgroup_root=cgroup_root)
    return sample["total"], sample["descendants"]


def _container_cpu_seconds(pid):
    try:
        cgroup = next(line.split("::", 1)[1] for line in
                      Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                      if line.startswith("0::"))
        stat = (Path("/sys/fs/cgroup") / cgroup.lstrip("/") / "cpu.stat").read_text()
        return int(next(line.split()[1] for line in stat.splitlines()
                        if line.startswith("usage_usec "))) / 1_000_000
    except (OSError, ValueError, StopIteration):
        return None


def _spool_bytes(root):
    if root is None:
        return None
    return sum(path.stat().st_size for path in root.rglob("*.jsonl") if path.is_file())


def _frontend_status(root):
    if root is None:
        return {}
    try:
        value = json.loads((root / "frontend" / "status.json").read_text())
        return {key: number for key, number in value.items() if type(number) is int}
    except (OSError, ValueError):
        return {}


def _percentile(samples, p):
    return samples[math.ceil(p * len(samples)) - 1]


def _response_fingerprint(response):
    return sha256(json.dumps(response, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False).encode("utf-8")).hexdigest()


def _phase_distributions(intervals, phases):
    result = {}
    for name in ("prepare", "zip"):
        selected = [phase for phase in phases if phase["phase"] == name]
        samples = sorted(duration for start, finish, duration in intervals if any(
            start < phase["finished"] and finish > phase["started"] for phase in selected))
        result[name] = {"requests": len(samples), "observed_seconds": sum(
            phase["finished"] - phase["started"] for phase in selected)}
        if samples:
            result[name].update(p50_ms=statistics.median(samples), p95_ms=_percentile(samples, .95),
                                p99_ms=_percentile(samples, .99), max_ms=samples[-1])
    return result


def _worker_phase(pid):
    try:
        group = next(line.split("::", 1)[1] for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
                     if line.startswith("0::"))
        root = Path("/sys/fs/cgroup").resolve()
        directory = (root / group.lstrip("/")).resolve()
        if not directory.is_relative_to(root):
            return None
        members = (directory / "cgroup.procs").read_text().split()
        for member in members:
            tokens = Path(f"/proc/{int(member)}/cmdline").read_bytes().split(b"\0")
            for phase, module in (("prepare", b"app.services.diagnostics.prepare_worker"),
                                  ("zip", b"app.services.diagnostics.bundle_worker")):
                if module in tokens:
                    return phase, int(member)
    except (OSError, ValueError, StopIteration):
        pass
    return None


def _host_sample():
    sample = {"at_monotonic": time.perf_counter()}
    try:
        values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
        sample.update(cpu_total_ticks=sum(values), cpu_idle_ticks=values[3] + values[4],
                      cpu_steal_ticks=values[7])
        for line in Path("/proc/pressure/cpu").read_text().splitlines():
            name, *fields = line.split()
            sample["cpu_pressure_" + name + "_avg10"] = float(dict(field.split("=") for field in fields)["avg10"])
    except (OSError, ValueError, IndexError, KeyError):
        sample["unavailable"] = True
    return sample


def _frontend_expiry(root, session_id):
    try:
        return json.loads((root / "frontend" / "sessions.json").read_text()).get(session_id)
    except (OSError, ValueError):
        return None


def _wait_frontend_session(root, session_id, *, previous=None):
    if root is None:
        return None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        expiry = _frontend_expiry(root, session_id)
        if expiry is not None and (previous is None or expiry != previous):
            return expiry
        time.sleep(0.05)
    raise RuntimeError("Frontend did not acknowledge synthetic session transition")


def _workload_slot(corpus_size, number):
    if corpus_size == 100:
        slot = number % 10
        if slot == 9:
            return "/api/settings", None
        return "/api/search", QUERIES[(0, 0, 0, 0, 1, 1, 1, 2, 2)[slot]]
    return "/api/search", QUERIES[number % len(QUERIES)]


def _capture_search_counts(root, session_id, *, include_settings=False):
    totals = dict.fromkeys(("backend_requests", "frontend_requests", "backend_operations", "backend_qdrant"), 0)
    if include_settings:
        totals.update(backend_settings=0, frontend_settings=0)
    for directory in (root / "backend" / "events" / session_id, root / "events" / session_id,
                      root / "frontend" / "events" / session_id):
        for path in directory.glob("*.jsonl"):
            with path.open("rb") as handle:
                for line in handle:
                    event = json.loads(line)
                    if event.get("diagnostic_session_id") != session_id:
                        continue
                    code, component = event.get("event_code"), event.get("component")
                    aggregate = code == "success_aggregate"
                    count = event.get("counts", {}).get("count", 0) if aggregate else 1
                    if (event.get("route_template") == "/api/search" and component in {"backend", "frontend"}
                            and (aggregate or code == "request_finished")):
                        totals[component + "_requests"] += count
                    if (include_settings and event.get("route_template") == "/api/settings"
                            and component in {"backend", "frontend"} and (aggregate or code == "request_finished")):
                        totals[component + "_settings"] += count
                    if component == "backend" and event.get("stage") == "search" and (
                            aggregate or code in {"operation_finished", "operation_summary"}):
                        totals["backend_operations"] += count
                    if component == "backend" and event.get("dependency") == "qdrant" and (
                            aggregate or code == "dependency_call_finished"):
                        totals["backend_qdrant"] += count
    return totals


def _wait_bundle(client, bundle_id, *, poll_interval=3.0, timeout=30.0,
                 clock=time.perf_counter, sleep=time.sleep):
    if not math.isfinite(poll_interval) or poll_interval <= 0 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Bundle observer interval and timeout must be positive and finite")
    deadline = clock() + timeout
    observation = {"poll_interval_seconds": poll_interval, "poll_requests": 0}
    while clock() < deadline:
        rows = client.call("GET", "/api/admin/diagnostics/bundles").get("items", [])
        observation["poll_requests"] += 1
        row = next((item for item in rows if item["id"] == bundle_id), None)
        if row and row["status"] == "building" and "building_started_monotonic" not in observation:
            observation["building_started_monotonic"] = clock()
        if row and row["status"] in {"ready", "failed"}:
            observation.update(status=row["status"], size_bytes=row.get("size_bytes"))
            return observation
        sleep(max(0, min(poll_interval, deadline - clock())))
    return {**observation, "status": "timeout"}


def _worker_overlap(intervals, phases):
    return sum(any(start < phase["finished"] and finish > phase["started"]
                   for phase in phases if phase["phase"] in {"prepare", "zip"})
               for start, finish, _ in intervals)


def measure(base_url, *, concurrency, warmup_seconds, measurement_seconds, min_requests,
            mode, repeat, corpus_size, spool_root=None, build_bundle=False,
            actor_username="demo.admin", stop_before_bundle=False, containers=None,
            bundle_poll_interval=3.0, retain_timelines=False):
    containers = containers or _containers(DEFAULT_PROJECT)
    admin = Client(base_url)
    admin.login(actor_username)
    bundle_client = Client(base_url) if build_bundle else None
    if bundle_client is not None:
        bundle_client.login(actor_username)
    clients = [Client(base_url) for _ in range(concurrency)]
    for client in clients:
        client.login()
    query_number = 0
    search_number = 0
    query_lock = threading.Lock()
    search_body = lambda query: {"query": query, "locale": "en", "use_glossary": False,
                                 "dense": False, "bm25": True, "top_k": 5}
    fingerprints = {query: _response_fingerprint(clients[0].call(
        "POST", "/api/search", search_body(query))) for query in QUERIES}
    if corpus_size == 100:
        fingerprints["GET /api/settings"] = _response_fingerprint(clients[0].call("GET", "/api/settings"))

    def one(index):
        nonlocal query_number, search_number
        with query_lock:
            route, query = _workload_slot(corpus_size, query_number)
            query_number += 1
            if query is not None:
                search_number += 1
        started = time.perf_counter()
        result = (clients[index].call("POST", route, search_body(query)) if query is not None
                  else clients[index].call("GET", route))
        finished = time.perf_counter()
        if query is not None and not result.get("hits"):
            raise RuntimeError("Synthetic indexed search returned no hits")
        if _response_fingerprint(result) != fingerprints[query if query is not None else "GET /api/settings"]:
            raise RuntimeError("Synthetic search response differs from baseline fingerprint")
        return (started, finished, (finished - started) * 1000)

    session_id = None
    session_active = False
    if mode != "baseline":
        session = admin.call("POST", "/api/admin/diagnostics/sessions", {
            "scope": "system", "capture_level": mode, "minutes": 5,
        })
        session_id = session["id"]
        session_active = True
    frontend_expiry = _wait_frontend_session(spool_root, session_id) if session_id else None
    pids = {role: _container_pid(name) for role, name in containers.items()}
    peaks = {role: 0 for role in containers}
    peaks["backend_child"] = 0
    measured_peaks = dict.fromkeys(peaks, 0)
    raw_peaks = dict.fromkeys(peaks, 0)
    rss_accounting = {role: {"unknown_comparisons": 0, "shared_vm_members": 0} for role in containers}
    measurement_started = threading.Event()
    phases = []
    host_samples = []
    done = threading.Event()

    rss_errors = []

    def sample_rss_loop():
        while not done.wait(0.02):
            for role, pid in pids.items():
                rss_sample = (_cgroup_rss_sample(pid) if role == "backend" else _tree_rss_sample(pid))
                total, descendants = rss_sample["total"], rss_sample["descendants"]
                raw_peaks[role] = max(raw_peaks[role], rss_sample["raw_total"])
                for key in rss_accounting[role]:
                    rss_accounting[role][key] += rss_sample[key]
                if role == "backend":
                    raw_peaks["backend_child"] = max(raw_peaks["backend_child"], rss_sample["raw_descendants"])
                peaks[role] = max(peaks[role], total)
                if measurement_started.is_set():
                    measured_peaks[role] = max(measured_peaks[role], total)
                if role == "backend":
                    peaks["backend_child"] = max(peaks["backend_child"], descendants)
                    if measurement_started.is_set():
                        measured_peaks["backend_child"] = max(measured_peaks["backend_child"], descendants)
            current = _worker_phase(pids["backend"]) if build_bundle else None
            now = time.perf_counter()
            if not host_samples or now - host_samples[-1]["at_monotonic"] >= 1:
                host_samples.append(_host_sample())
            if phases and not phases[-1].get("closed"):
                phases[-1]["finished"] = now
                if current != (phases[-1]["phase"], phases[-1]["pid"]):
                    phases[-1]["closed"] = True
            if current and (not phases or phases[-1].get("closed")):
                phases.append({"phase": current[0], "pid": current[1], "started": now, "finished": now})

    def sample_rss():
        try:
            sample_rss_loop()
        except Exception as exc:
            rss_errors.append(exc)

    sampler = threading.Thread(target=sample_rss, daemon=True)
    sampler.start()
    bundle = {}
    builder = None

    def build_once():
        bundle["started_monotonic"] = time.perf_counter()
        now = datetime.now(timezone.utc)
        body = ({"session_id": session_id} if session_id else {
            "from_utc": (now - timedelta(minutes=2)).isoformat(),
            "to_utc": now.isoformat()})
        try:
            submitted = bundle_client.call("POST", "/api/admin/diagnostics/bundles", body)
            bundle["id"] = submitted["id"]
            bundle.update(_wait_bundle(bundle_client, submitted["id"],
                                       poll_interval=bundle_poll_interval))
        except Exception as exc:
            bundle["status"] = "error"
            bundle["error_type"] = type(exc).__name__
            bundle["exception"] = exc  # In-memory cause only; never serialized in results.
        finally:
            bundle["finished_monotonic"] = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            deadline = time.monotonic() + warmup_seconds
            while time.monotonic() < deadline:
                list(pool.map(one, range(concurrency)))
            captured_requests, captured_searches = query_number, search_number
            if stop_before_bundle and session_id:
                admin.call("POST", f"/api/admin/diagnostics/sessions/{session_id}/stop", {})
                session_active = False
                _wait_frontend_session(spool_root, session_id, previous=frontend_expiry)
            recorder_before = admin.call("GET", "/api/admin/diagnostics/status").get("recorder", {})
            cpu_before = {role: _container_cpu_seconds(pid) for role, pid in pids.items()}
            bytes_before = _spool_bytes(spool_root)
            frontend_before = _frontend_status(spool_root)
            started = time.perf_counter()
            measurement_started.set()
            if build_bundle:
                builder = threading.Thread(target=build_once, daemon=True)
                builder.start()
            intervals = []
            while (time.perf_counter() - started < measurement_seconds or
                   len(intervals) < min_requests):
                intervals.extend(pool.map(one, range(concurrency)))
            elapsed = time.perf_counter() - started
            cpu_after = {role: _container_cpu_seconds(pid) for role, pid in pids.items()}
            bytes_after = _spool_bytes(spool_root)
            frontend_after = _frontend_status(spool_root)
        status = admin.call("GET", "/api/admin/diagnostics/status")
    finally:
        if builder is not None:
            builder.join(timeout=65)
        done.set()
        sampler.join(timeout=2)
        if session_active:
            admin.call("POST", f"/api/admin/diagnostics/sessions/{session_id}/stop", {})
            _wait_frontend_session(spool_root, session_id, previous=frontend_expiry)
    if rss_errors:
        raise RuntimeError("RSS observation failed") from rss_errors[0]
    samples = sorted(row[2] for row in intervals)
    recorder_after = status.get("recorder", {})
    recorder_delta = {key: value - recorder_before[key] for key, value in recorder_after.items()
                      if type(value) is int and type(recorder_before.get(key)) is int}
    frontend_delta = {key: value - frontend_before[key] for key, value in frontend_after.items()
                      if type(frontend_before.get(key)) is int}
    if build_bundle and (builder.is_alive() or bundle.get("status") != "ready"):
        raise RuntimeError(f"Synthetic ZIP failed: {bundle.get('status', 'unfinished')}") from bundle.get("exception")
    overlap = _worker_overlap(intervals, phases) if build_bundle else 0
    expected_requests = captured_searches if stop_before_bundle else search_number
    expected_settings = ((captured_requests - captured_searches) if stop_before_bundle
                         else query_number - search_number)
    capture_counts = (_capture_search_counts(spool_root, session_id, include_settings=corpus_size == 100)
                      if spool_root and session_id else None)
    expected_counts = {key: (expected_settings if key.endswith("_settings") else expected_requests)
                       for key in capture_counts} if capture_counts is not None else None
    result = {
        "mode": mode, "repeat": repeat, "corpus_size": corpus_size,
        "concurrency": concurrency, "warmup_seconds": warmup_seconds,
        "elapsed_seconds": elapsed, "requests": len(samples),
        "p50_ms": statistics.median(samples), "p95_ms": _percentile(samples, .95),
        "p99_ms": _percentile(samples, .99), "max_ms": samples[-1],
        "throughput_per_second": len(samples) / elapsed,
        "peak_rss_bytes": peaks,
        "raw_peak_rss_bytes": raw_peaks,
        "rss_accounting": {"method": "KCMP_VM unique address spaces; unknown retains raw RSS", "roles": rss_accounting},
        "measurement_peak_rss_bytes": measured_peaks,
        "response_fingerprints": fingerprints, "response_equality_pass": True,
        "host_samples": host_samples,
        "slowest_intervals": [{"started": start, "finished": finish, "duration_ms": duration}
                              for start, finish, duration in sorted(intervals, key=lambda row: row[2])[-20:]],
        "capture_search_counts": capture_counts,
        "expected_capture_search_calls": expected_requests if session_id else None,
        "expected_capture_settings_calls": expected_settings if session_id else None,
        "workload_distribution": "90% search (4/3/2 queries), 10% settings" if corpus_size == 100 else "search (1/1/1 queries)",
        "capture_counts_pass": (capture_counts == expected_counts
                                if capture_counts is not None else None),
        "cpu_seconds": {role: (cpu_after[role] - cpu_before[role]
                              if cpu_before[role] is not None and cpu_after[role] is not None
                              else None) for role in pids},
        "spool_bytes_growth": (bytes_after - bytes_before
                               if bytes_after is not None and bytes_before is not None else None),
        "recorder_delta": recorder_delta,
        "frontend_delta": frontend_delta,
        "recorder": recorder_after,
        "session_id": session_id,
        "stopped_before_bundle": stop_before_bundle,
        "bundle": ({"status": bundle["status"], "size_bytes": bundle["size_bytes"],
                    "build_seconds": bundle["finished_monotonic"] - bundle["started_monotonic"],
                    "observed_building": any(phase["phase"] in {"prepare", "zip"} for phase in phases),
                    "api_observed_building": "building_started_monotonic" in bundle,
                    "poll_interval_seconds": bundle["poll_interval_seconds"],
                    "poll_requests": bundle["poll_requests"],
                    "overlapping_requests": overlap,
                    "phase_latencies": _phase_distributions(intervals, phases),
                    "phases": phases} if build_bundle else None),
    }
    if retain_timelines:
        result["request_intervals"] = [{"started": start, "finished": finish, "duration_ms": duration}
                                       for start, finish, duration in intervals]
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:18084")
    parser.add_argument("--corpus-size", type=int, choices=(1, 100), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument("--measurement-seconds", type=int, default=60)
    parser.add_argument("--min-requests", type=int, default=1000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--concurrency", type=int, choices=(1, 8), action="append")
    parser.add_argument("--spool-root", type=Path)
    parser.add_argument("--with-bundle", action="store_true")
    parser.add_argument("--bundle-poll-interval", type=float, default=3.0,
                        help="UI cadence is 3 seconds; faster polling is a separate diagnostic workload")
    parser.add_argument("--stopped-bundle", action="store_true")
    parser.add_argument("--admin-prefix", default="diag.bench")
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--retain-timelines", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.bundle_poll_interval) or args.bundle_poll_interval <= 0:
        parser.error("--bundle-poll-interval must be positive and finite")
    if args.stopped_bundle and not args.with_bundle:
        parser.error("--stopped-bundle requires --with-bundle")
    rows = []
    containers = _containers(args.project)
    image_ids = _image_ids(containers)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    admin = Client(args.base_url)
    admin.login()
    active = admin.call("GET", "/api/admin/diagnostics/status")["session"]["session"]
    if active:
        admin.call("POST", f"/api/admin/diagnostics/sessions/{active['id']}/stop", {})
    for repeat in range(args.repeats):
        for concurrency in (args.concurrency or (1, 8)):
            if args.with_bundle:
                reference = measure(args.base_url, concurrency=concurrency,
                                    warmup_seconds=args.warmup_seconds,
                                    measurement_seconds=args.measurement_seconds,
                                    min_requests=args.min_requests, mode="baseline",
                                    repeat=repeat + 1, corpus_size=args.corpus_size,
                                    spool_root=args.spool_root, containers=containers,
                                    retain_timelines=args.retain_timelines)
                reference["bundle_reference"] = True
                rows.append(reference)
                args.output.write_text(json.dumps({"schema_version": 2, "image_ids": image_ids,
                                                   "rows": rows},
                                                  indent=2, sort_keys=True) + "\n")
                print(f"corpus={args.corpus_size} repeat={repeat + 1} c={concurrency} "
                      f"mode=baseline-reference p95_ms={reference['p95_ms']:.3f} "
                      f"requests={reference['requests']}", flush=True)
            modes = ["standard", "detailed"] if args.stopped_bundle else ["baseline", "standard", "detailed"]
            random.Random(2911 + repeat * 2 + concurrency).shuffle(modes)
            for mode in modes:
                row = measure(args.base_url, concurrency=concurrency,
                              warmup_seconds=args.warmup_seconds,
                              measurement_seconds=args.measurement_seconds,
                              min_requests=args.min_requests, mode=mode,
                              repeat=repeat + 1, corpus_size=args.corpus_size,
                              spool_root=args.spool_root, build_bundle=args.with_bundle,
                              bundle_poll_interval=args.bundle_poll_interval,
                              stop_before_bundle=args.stopped_bundle,
                              actor_username=(f"{args.admin_prefix}{sum(bool(r.get('bundle')) for r in rows) + 1:02d}"
                                              if args.with_bundle else "demo.admin"),
                              containers=containers, retain_timelines=args.retain_timelines)
                rows.append(row)
                args.output.write_text(json.dumps({"schema_version": 2, "image_ids": image_ids,
                                                   "rows": rows},
                                                  indent=2, sort_keys=True) + "\n")
                print(f"corpus={args.corpus_size} repeat={repeat + 1} c={concurrency} "
                      f"mode={mode} p95_ms={row['p95_ms']:.3f} requests={row['requests']}",
                      flush=True)


if __name__ == "__main__":
    main()
