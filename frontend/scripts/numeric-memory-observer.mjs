// Test-only passive numeric observer. Never load in production entrypoints.
import * as fs from "node:fs";
import path from "node:path";
import process from "node:process";
import * as v8 from "node:v8";
import { PerformanceObserver } from "node:perf_hooks";
import { setInterval, clearInterval, setTimeout } from "node:timers";

export function sampleRow(role, proc = process, heap = v8, now = Date.now()) {
  if (!["launcher", "next"].includes(role)) throw new Error("invalid role");
  const memory = proc.memoryUsage();
  const stats = heap.getHeapStatistics();
  const uptime = proc.uptime() * 1000;
  const row = { role, pid: proc.pid, at_unix_ms: now, uptime_ms: uptime,
    birth_unix_ms: now - uptime, rss_bytes: memory.rss, heap_used_bytes: memory.heapUsed,
    heap_total_bytes: memory.heapTotal, external_bytes: memory.external,
    array_buffers_bytes: memory.arrayBuffers, physical_heap_bytes: stats.total_physical_size,
    heap_limit_bytes: stats.heap_size_limit, native_contexts: stats.number_of_native_contexts,
    detached_contexts: stats.number_of_detached_contexts };
  for (const [key, value] of Object.entries(row)) {
    if (key !== "role" && (typeof value !== "number" || !Number.isFinite(value) || value < 0)) {
      throw new Error("invalid numeric reading");
    }
  }
  return row;
}

function install(root, role) {
  const fd = fs.openSync(path.join(root, `${role}-${process.pid}.jsonl`), "wx", 0o600);
  let rows = 0;
  let closed = false;
  const gc = { minor_count: 0, major_count: 0, other_count: 0,
    minor_ms: 0, major_ms: 0, other_ms: 0 };
  const write = (type, extra = {}) => {
    if (closed) return;
    if (rows >= 2000) throw new Error("numeric observer row limit");
    fs.writeSync(fd, JSON.stringify({ type, ...sampleRow(role), ...gc, ...extra }) + "\n");
    rows += 1;
  };
  const observer = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      const kind = entry.detail.kind;
      if (![1, 4, 8, 16].includes(kind) || !Number.isFinite(entry.duration) || entry.duration < 0) {
        throw new Error("invalid numeric GC reading");
      }
      const prefix = kind === 1 ? "minor" : kind === 4 ? "major" : "other";
      gc[`${prefix}_count`] += 1;
      gc[`${prefix}_ms`] += entry.duration;
      write("gc", { gc_kind: kind, gc_duration_ms: entry.duration });
    }
  });
  observer.observe({ entryTypes: ["gc"] });
  write("start");
  const timer = setInterval(() => write("sample"), 500);
  timer.unref();
  const stop = () => {
    if (closed) return;
    observer.disconnect();
    clearInterval(timer);
    write("stop");
    closed = true;
    fs.closeSync(fd);
  };
  setTimeout(stop, 300000).unref();
  process.once("exit", stop);
}

if (process.env.OKF_TEST_NUMERIC_MEMORY_DIR) {
  const entry = path.basename(process.argv[1] || "");
  const role = entry === "diagnostics-runner.mjs" ? "launcher" : entry === "server.js" ? "next" : null;
  if (role === null) throw new Error("invalid observer entrypoint role");
  install(process.env.OKF_TEST_NUMERIC_MEMORY_DIR, role);
}
