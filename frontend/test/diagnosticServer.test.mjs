import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile, mkdir, stat, symlink, utimes } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { randomUUID } from "node:crypto";
import { EventEmitter } from "node:events";
import { spawn } from "node:child_process";
import { DiagnosticServerRecorder, installServerDiagnosticsShutdown, setServerRecorder } from "../src/lib/diagnosticServer.mjs";
import { sanitizeServerEvent, safeServerException } from "../src/lib/diagnosticSchema.mjs";
import { currentRequestId, withRequestContext } from "../src/lib/requestContext.mjs";
import { GET } from "../src/app/api/[...path]/route.js";

const fixture = JSON.parse(await readFile(new URL("../../tests/fixtures/diagnostics/events-v1.json", import.meta.url)));
const options = { minFreeBytes: 0, budgetBytes: 12000, segmentBytes: 1200 };

async function setup(t, overrides = {}) {
  const root = await mkdtemp(path.join(os.tmpdir(), "okf-diagnostics-"));
  const controlPath = path.join(root, "capture.json");
  const spool = path.join(root, "spool");
  const recorder = new DiagnosticServerRecorder({ root: spool, controlPath, ...options, ...overrides });
  t.after(async () => { setServerRecorder(null); await recorder.stop(); await rm(root, { recursive: true, force: true }); });
  assert.equal(await recorder.start(), true);
  return { root, spool, controlPath, recorder };
}

async function files(root) {
  const result = [];
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const filename = path.join(root, entry.name);
    if (entry.isDirectory()) result.push(...await files(filename));
    else result.push(filename);
  }
  return result;
}

async function events(root) {
  return (await Promise.all((await files(root)).filter((name) => name.endsWith(".jsonl"))
    .map(async (name) => (await readFile(name, "utf8")).trim().split("\n").filter(Boolean).map(JSON.parse)))).flat();
}

async function activate(controlPath, now, sessionId = randomUUID()) {
  await writeFile(controlPath, JSON.stringify({ schema_version: 1, boot_id: randomUUID(), active: true,
    session_id: sessionId, scope: "interface", deadline: now / 1000 + 300, lease_until: now / 1000 + 10 }));
  return sessionId;
}

test("instrumentation and route module instances share the process recorder", async (t) => {
  const first = await import("../src/lib/diagnosticServer.mjs?instance=instrumentation");
  const second = await import("../src/lib/diagnosticServer.mjs?instance=route");
  const seen = [];
  first.setServerRecorder({ emit: (code) => { seen.push(code); return true; },
    status: () => ({ running: true }) });
  t.after(() => first.setServerRecorder(null));
  assert.equal(second.emitServerEvent("proxy_failed", {}), true);
  assert.deepEqual(seen, ["proxy_failed"]);
  assert.equal(second.serverRecorderStatus().running, true);
});

test("shutdown IPC acknowledges release before a normal restart", async (t) => {
  const { recorder, spool } = await setup(t);
  const proc = new EventEmitter();
  const replies = [];
  proc.send = (reply) => replies.push(reply);
  installServerDiagnosticsShutdown(recorder, proc);
  proc.emit("message", { type: "okf-diagnostics-stop" });
  for (let i = 0; i < 100 && !replies.length; i++) await new Promise((resolve) => setTimeout(resolve, 10));
  assert.deepEqual(replies, [{ type: "okf-diagnostics-stopped" }]);
  await writeFile(path.join(spool, "status.json.tmp"), "stale metadata");
  const next = new DiagnosticServerRecorder({ root: spool, ...options });
  assert.equal(await next.start(), true);
  await assert.rejects(stat(path.join(spool, "status.json.tmp")), { code: "ENOENT" });
  await next.stop();
});

test("production runner survives a normal SIGTERM and starts again on the same spool", { skip: process.platform === "win32" }, async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "okf-diagnostics-restart-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const spool = path.join(root, "spool");
  const fixture = path.join(root, "worker.mjs");
  const recorderUrl = new URL("../src/lib/diagnosticServer.mjs", import.meta.url).href;
  const runnerUrl = new URL("../scripts/diagnostics-runner.mjs", import.meta.url).href;
  await writeFile(fixture, `import { DiagnosticServerRecorder, installServerDiagnosticsShutdown } from ${JSON.stringify(recorderUrl)};\n`
    + `const recorder = new DiagnosticServerRecorder({root: ${JSON.stringify(spool)}, minFreeBytes: 0});\n`
    + `if (!await recorder.start()) process.exit(3); installServerDiagnosticsShutdown(recorder); setInterval(() => {}, 1000);\n`);
  const run = () => spawn(process.execPath, ["--input-type=module", "-e",
    `import { runServer } from ${JSON.stringify(runnerUrl)}; runServer(${JSON.stringify(fixture)});`],
    { stdio: ["ignore", "pipe", "pipe"] });
  const waitForMarker = async () => {
    for (let i = 0; i < 200; i++) {
      try { await stat(path.join(spool, "writer.json")); return; } catch { await new Promise((resolve) => setTimeout(resolve, 20)); }
    }
    throw new Error("Writer did not start");
  };
  for (let attempt = 0; attempt < 2; attempt++) {
    const runner = run();
    t.after(() => { if (!runner.killed) runner.kill("SIGKILL"); });
    await waitForMarker();
    const exited = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Runner did not exit")), 9000);
      runner.once("exit", (result) => { clearTimeout(timeout); resolve(result); });
    });
    runner.kill("SIGTERM");
    const code = await exited;
    assert.equal(code, 143);
    await assert.rejects(stat(path.join(spool, "writer.json")), { code: "ENOENT" });
  }
});

test("shared schema rejects content and accepts backend/frontend/browser fixtures", () => {
  for (const raw of fixture.valid) {
    assert.ok(sanitizeServerEvent(raw));
    for (const changes of fixture.invalid_changes) assert.equal(sanitizeServerEvent({ ...raw, ...changes }), null);
  }
  const error = new Error("CANARY_PRIVATE");
  error.stack = "Error: CANARY_PRIVATE\n    at request (C:/deployment/frontend/src/lib/backendFetch.js:12:4)\n    at secret (https://private/CANARY.js?password=CANARY:2:3)";
  const safe = safeServerException(error);
  assert.equal(safe.exception_type, "Error");
  assert.deepEqual(safe.frames[0], { module: "frontend/src/lib/backendFetch.js", function: "server", line: 12 });
  assert.equal(safe.frames[1].module, "external_frame");
  assert.ok(!JSON.stringify(safe).includes("CANARY"));
});

test("async request contexts remain isolated and reset on failure", async () => {
  const first = randomUUID(), second = randomUUID();
  const result = await Promise.all([first, second].map((requestId) => withRequestContext({ requestId }, async () => {
    await new Promise((resolve) => setImmediate(resolve));
    return currentRequestId();
  })));
  assert.deepEqual(result, [first, second]);
  await assert.rejects(withRequestContext({ requestId: first }, async () => { throw new Error("failed"); }));
  assert.equal(currentRequestId(), null);
});

test("bounded Node spool rotates and excludes private payloads", async (t) => {
  const { recorder, spool } = await setup(t);
  for (let i = 0; i < 40; i++) {
    assert.equal(recorder.emit("proxy_failed", { request_id: randomUUID(), http_status: 502,
      route_template: "/unknown", error_code: "network_error" }, new Error("CANARY_BODY")), true);
    await recorder.flush();
  }
  const paths = await files(spool);
  const total = (await Promise.all(paths.map(async (name) => (await stat(name)).size))).reduce((a, b) => a + b, 0);
  assert.ok(total <= 12000);
  const segments = paths.filter((name) => name.endsWith(".jsonl"));
  assert.ok(segments.length > 1);
  for (const filename of segments) {
    const content = await readFile(filename, "utf8");
    assert.ok(Buffer.byteLength(content) <= 1200);
    assert.ok(!content.includes("CANARY"));
    for (const line of content.trim().split("\n")) assert.ok(sanitizeServerEvent(JSON.parse(line)));
  }
});

test("Node loss counters reach the status file before the next support bundle", async (t) => {
  const { recorder, spool } = await setup(t, { queueSize: 1 });
  for (let i = 0; i < 20; i++) recorder.emit("proxy_failed", { http_status: 502, error_code: "network_error" });
  assert.ok(recorder.status().dropped > 0);
  let status;
  for (let i = 0; i < 30; i++) {
    status = JSON.parse(await readFile(path.join(spool, "status.json"), "utf8"));
    if (status.dropped > 0) break;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  assert.ok(status.dropped > 0);
});

test("idle Node spool refreshes its status heartbeat", async (t) => {
  const { recorder, spool } = await setup(t);
  await recorder.flush();
  await recorder.maintenance();
  const filename = path.join(spool, "status.json");
  const old = new Date(Date.now() - 180000);
  await utimes(filename, old, old);
  await recorder.maintenance();
  assert.ok((await stat(filename)).mtimeMs > old.getTime() + 120000);
});

test("capture lease expires monotonically despite wall clock rollback", async (t) => {
  let wall = Date.now(), mono = 0;
  const { recorder, spool, controlPath } = await setup(t, { now: () => wall, monotonic: () => mono });
  const sessionId = await activate(controlPath, wall);
  await recorder.refreshControl();
  assert.equal(recorder.emit("request_finished", { http_status: 200 }), true);
  await recorder.flush();
  wall -= 3600000; mono += 11000;
  await recorder.refreshControl();
  assert.equal(recorder.emit("request_finished", { http_status: 200 }), false);
  await recorder.flush();
  assert.equal((await events(spool)).filter((event) => event.diagnostic_session_id === sessionId).length, 1);
});

test("capture revocation drops queued detail without deleting baseline", async (t) => {
  let mono = 0;
  const { recorder, spool, controlPath } = await setup(t, { monotonic: () => mono });
  await activate(controlPath, Date.now()); await recorder.refreshControl();
  assert.equal(recorder.emit("request_finished", { http_status: 200 }), true);
  // Expire before yielding to the writer. A projection update across processes
  // is allowed the documented ten-second lease, not immediate synchronous revocation.
  mono = 11000;
  await writeFile(controlPath, JSON.stringify({ schema_version: 1, boot_id: randomUUID(), active: false, lease_until: Date.now() / 1000 + 10 }));
  await recorder.refreshControl(); await recorder.flush();
  assert.equal((await events(spool)).filter((event) => event.event_code === "request_finished").length, 0);
  assert.equal(recorder.emit("proxy_failed", { error_code: "network_error" }), true);
  await recorder.flush();
  assert.equal((await events(spool)).at(-1).event_code, "proxy_failed");
});

test("second writer and stale crash marker refuse takeover", async (t) => {
  const { recorder, spool } = await setup(t);
  const second = new DiagnosticServerRecorder({ root: spool, ...options });
  assert.equal(await second.start(), false);
  await second.stop();
  assert.equal(recorder.emit("proxy_failed", {}), true); await recorder.flush();
  await recorder.stop();
  await writeFile(path.join(spool, "writer.json"), JSON.stringify({ owner: randomUUID() }));
  assert.equal(await second.start(), false);
});

test("writer permissions failure never escapes business operation", async (t) => {
  const { recorder, spool } = await setup(t);
  await mkdir(path.join(spool, "events", "baseline"), { recursive: true });
  await rm(path.join(spool, "events", "baseline"), { recursive: true });
  await writeFile(path.join(spool, "events", "baseline"), "blocked");
  assert.equal(recorder.emit("proxy_failed", {}), true);
  await recorder.flush();
  assert.ok(recorder.status().dropped > 0);
  assert.equal(recorder.status().storage_degraded, true);
});

test("queue overflow and repeated errors remain bounded", async (t) => {
  const { recorder, spool } = await setup(t, { queueSize: 4 });
  for (let i = 0; i < 1000; i++) recorder.emit("proxy_failed", { error_code: "network_error" });
  assert.ok(recorder.status().queued <= 4);
  assert.ok(recorder.status().dropped > 0);
  await recorder.flush(); await recorder.stop();
  const recorded = await events(spool);
  assert.ok(recorded.filter((event) => ["proxy_failed", "repeat_summary"].includes(event.event_code)).length <= 2);
  assert.ok(recorded.some((event) => event.event_code === "repeat_summary" && event.counts.repeats > 0));
});

test("API proxy retains safe failure and its reference when backend is down", async (t) => {
  const { recorder, spool } = await setup(t);
  setServerRecorder(recorder);
  const previous = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("CANARY_URL_PASSWORD"); };
  t.after(() => { globalThis.fetch = previous; });
  const response = await GET(new Request("http://localhost/api/search?token=CANARY", {
    headers: { "x-request-id": "00000000-0000-4000-8000-000000000001" },
  }), { params: Promise.resolve({ path: ["search"] }) });
  assert.equal(response.status, 502);
  const body = await response.json();
  assert.equal(body.request_id, response.headers.get("x-request-id"));
  assert.notEqual(body.request_id, "00000000-0000-4000-8000-000000000001");
  assert.ok(!JSON.stringify(body).includes("CANARY"));
  await recorder.flush();
  const recorded = (await events(spool)).find((event) => event.event_code === "proxy_failed");
  assert.equal(recorded.request_id, body.request_id);
  assert.equal(recorded.route_template, "/api/search");
});

test("actual twenty MiB budget includes metadata and rotates a full baseline", async (t) => {
  const budget = 20 * 1048576;
  const { recorder, spool } = await setup(t, { budgetBytes: budget, segmentBytes: 5 * 1048576 });
  await recorder.flush();
  const directory = path.join(spool, "events", "baseline");
  for (let i = 0; i < 4; i++) await writeFile(path.join(directory, `${Date.now() - 10000 + i}_${randomUUID()}.jsonl`), Buffer.alloc(5 * 1048576, 32));
  recorder.emit("proxy_failed", { request_id: randomUUID() }); await recorder.flush();
  const actual = (await Promise.all((await files(spool)).map(async (filename) => (await stat(filename)).size))).reduce((a, b) => a + b, 0);
  assert.ok(actual <= budget);
  assert.equal(recorder.status().storage_degraded, false);
});

test("stopped capture is removed after one day and baseline after seven days", async (t) => {
  let wall = Date.now();
  const { recorder, spool, controlPath } = await setup(t, { now: () => wall });
  const sessionId = await activate(controlPath, wall); await recorder.refreshControl();
  recorder.emit("request_finished", { http_status: 200 }); await recorder.flush();
  await writeFile(controlPath, JSON.stringify({ schema_version: 1, boot_id: randomUUID(), active: false, lease_until: wall / 1000 + 10 }));
  await recorder.refreshControl();
  wall += 25 * 3600000; await recorder.maintenance();
  assert.ok(!(await events(spool)).some((event) => event.diagnostic_session_id === sessionId));
  assert.ok((await events(spool)).some((event) => event.event_code === "server_started"));
  wall += 7 * 86400000; await recorder.maintenance();
  assert.equal((await events(spool)).length, 0);
});

test("symlink or Windows junction inside spool refuses writing", async (t) => {
  const { recorder, spool, root } = await setup(t);
  await recorder.flush();
  await rm(path.join(spool, "events", "baseline"), { recursive: true, force: true });
  const outside = path.join(root, "private"); await mkdir(outside);
  await symlink(outside, path.join(spool, "events", "baseline"), "junction");
  recorder.emit("proxy_failed", {}); await recorder.flush();
  assert.equal((await readdir(outside)).length, 0);
  assert.equal(recorder.status().storage_degraded, true);
});

test("client abort is distinguished from backend timeout", async (t) => {
  const { recorder, spool } = await setup(t); setServerRecorder(recorder);
  const previous = globalThis.fetch; t.after(() => { globalThis.fetch = previous; });
  globalThis.fetch = async () => { throw new DOMException("CANARY", "TimeoutError"); };
  const timedOut = await GET(new Request("http://local/api/search"), { params: Promise.resolve({ path: ["search"] }) });
  assert.equal(timedOut.status, 504);
  const controller = new AbortController(); controller.abort();
  const cancelled = await GET(new Request("http://local/api/search", { signal: controller.signal }), { params: Promise.resolve({ path: ["search"] }) });
  assert.equal(cancelled.status, 499);
  await recorder.flush();
  const failures = (await events(spool)).filter((event) => event.event_code === "proxy_failed");
  assert.equal(failures.length, 1); assert.equal(failures[0].http_status, 504);
});

test("orphan capture segments expire even when session metadata was lost", async (t) => {
  const { recorder, spool } = await setup(t);
  const directory = path.join(spool, "events", randomUUID()); await mkdir(directory);
  const filename = path.join(directory, `${Date.now() - 25 * 3600000}_${randomUUID()}.jsonl`);
  await writeFile(filename, JSON.stringify(fixture.valid[0]) + "\n");
  const old = new Date(Date.now() - 25 * 3600000); await utimes(filename, old, old);
  await recorder.maintenance();
  assert.equal((await readdir(directory)).length, 0);
});

test("unsafe client frame coordinates never carry arbitrary names or paths", () => {
  const raw = fixture.valid.find((event) => event.component === "browser");
  for (const frame of [
    { module: "frontend/src/lib/backendFetch.js", function: "client", line: 12 },
    { module: "frontend/_next/abcdef123456.js", function: "CANARY", line: 12 },
    { module: "https://host/_next/abcdef123456.js?token=CANARY", function: "client", line: 12 },
  ]) assert.equal(sanitizeServerEvent({ ...raw, frames: [frame] }), null);
});
