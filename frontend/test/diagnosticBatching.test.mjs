import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { randomUUID } from "node:crypto";
import { DiagnosticServerRecorder } from "../src/lib/diagnosticServer.mjs";

test("drain writes its admitted batch before producers can refill it during summary IO", async () => {
  let mono = 2000, refills = 0;
  const recorder = new DiagnosticServerRecorder({ monotonic: () => mono });
  const event = { level: "ERROR", event_code: "proxy_failed" };
  const item = { event, encoded: "safe\n", baseline: true };
  recorder.queue.push(item);
  for (let i = 0; i < 256; i++) recorder.dedup.set(`prior-${i}`, { started: 0, repeats: 1 });
  recorder._streamWritable = () => true;
  recorder._summary = async () => {
    await Promise.resolve();
    mono += 1100;
    if (++refills <= 3) recorder.queue.push({ ...item, event: { ...event, request_id: randomUUID() } });
  };
  const batches = [];
  recorder._appendBatch = async (lines) => { batches.push([...lines]); };
  await recorder._drain();
  assert.equal(batches[0].length, 1);
  assert.equal(recorder.queue.length, 1);
});

test("blocked drain has only one scheduled writer even while producers keep emitting", async () => {
  const recorder = new DiagnosticServerRecorder();
  recorder.running = true;
  let release, scheduled = 0;
  const blocked = new Promise((resolve) => { release = resolve; });
  recorder._drain = async () => { await blocked; recorder.queue.splice(0); };
  const serialize = recorder._serialize.bind(recorder);
  recorder._serialize = (callback) => { scheduled++; return serialize(callback); };
  try {
    for (let i = 0; i < 6; i++) {
      assert.equal(recorder.emit("request_finished", { route_template: "/api/search",
        http_status: 500, duration_ms: 10 }), true);
      await new Promise((resolve) => setImmediate(resolve));
    }
    assert.equal(scheduled, 1);
  } finally { release(); await recorder.chain; }
});

for (const level of ["standard", "detailed"]) test(`${level} slow success budget aggregates overflow and preserves errors independently`, async () => {
  let mono = 0;
  const recorder = new DiagnosticServerRecorder({ monotonic: () => mono, now: () => 2000000 });
  recorder.running = true;
  recorder.scheduled = true; // Inspect admission without a filesystem writer.
  recorder.control = { active: true, session_id: randomUUID(), revision: 1,
    deadline: 2300, lease_until: 2010, monotonicExpiry: 10000,
    policy: { ...policy, level, slow_limit_per_second: 1 } };
  const fields = { route_template: "/api/search", http_status: 200, duration_ms: 1200 };
  for (let i = 0; i < 3; i++) assert.equal(recorder.emit("request_finished", fields), true);
  assert.equal(recorder.queue.length, 1);
  assert.equal(recorder.status().sampled_out_slow, 2);
  assert.equal([...recorder.aggregates.values()][0].counts.count, 2);
  assert.equal(recorder.emit("request_finished", { ...fields, http_status: 503 }), true);
  assert.equal(recorder.queue.length, 2);
  mono = 1000;
  assert.equal(recorder.emit("request_finished", fields), true);
  assert.equal(recorder.queue.length, 3);
});

const policy = { level: "standard", version: 1, aggregate_interval_ms: 5000,
  success_limit_per_second: 100, trace_limit_per_second: 20,
  slow_limit_per_second: 20, max_inflight_traces: 512,
  slow_thresholds_ms: { http_search: 1000, qdrant_db: 2000,
    embeddings_proxy: 2000, llm_chat: 5000, pdf: 10000 } };

test("standard capture combines successful requests while retaining distinct errors", async (t) => {
  const base = await mkdtemp(path.join(os.tmpdir(), "okf-policy-"));
  const root = path.join(base, "spool"), controlPath = path.join(base, "capture.json");
  const recorder = new DiagnosticServerRecorder({ root, controlPath, minFreeBytes: 0 });
  t.after(async () => { await recorder.stop(); await rm(base, { recursive: true, force: true }); });
  assert.equal(await recorder.start(), true);
  const sessionId = randomUUID(), now = Date.now();
  await writeFile(controlPath, JSON.stringify({ schema_version: 2, boot_id: randomUUID(), revision: 1,
    active: true, session_id: sessionId, scope: "interface", deadline: now / 1000 + 300,
    lease_until: now / 1000 + 10, policy }));
  await recorder.refreshControl();
  for (let i = 0; i < 100; i++) assert.equal(recorder.emit("request_finished", {
    route_template: "/api/search", http_status: 200, duration_ms: 12 }), true);
  const invalidBefore = recorder.status().invalid;
  assert.equal(recorder.emit("request_finished", { route_template: "/api/search",
    http_status: 200, duration_ms: 12, secret: "CANARY_PRIVATE" }), false);
  assert.equal(recorder.status().invalid, invalidBefore + 1);
  assert.equal(recorder.emit("request_finished", { route_template: "/api/search", http_status: 503,
    duration_ms: 12 }), true);
  recorder.control = null; // Accepted aggregates and errors finish after manual stop.
  await recorder.flush();
  const directory = path.join(root, "events", sessionId);
  const files = (await readdir(directory)).filter((name) => name.endsWith(".jsonl"));
  const events = (await Promise.all(files.map((name) => readFile(path.join(directory, name), "utf8"))))
    .join("").trim().split("\n").map(JSON.parse);
  assert.equal(events.filter((event) => event.event_code === "request_finished").length, 1);
  assert.equal(events.filter((event) => event.event_code === "success_aggregate").length, 1);
  assert.equal(events.find((event) => event.event_code === "success_aggregate").counts.count, 100);
});

test("standard aggregate admitted before lease renewal is still written", async (t) => {
  let mono = 0;
  const base = await mkdtemp(path.join(os.tmpdir(), "okf-policy-renew-"));
  const root = path.join(base, "spool"), controlPath = path.join(base, "capture.json");
  const recorder = new DiagnosticServerRecorder({ root, controlPath, minFreeBytes: 0,
    monotonic: () => mono });
  t.after(async () => { await recorder.stop(); await rm(base, { recursive: true, force: true }); });
  assert.equal(await recorder.start(), true);
  const sessionId = randomUUID(), now = Date.now();
  await writeFile(controlPath, JSON.stringify({ schema_version: 2, boot_id: randomUUID(), revision: 1,
    active: true, session_id: sessionId, scope: "interface", deadline: now / 1000 + 300,
    lease_until: now / 1000 + 10, policy }));
  await recorder.refreshControl();
  recorder.control.monotonicExpiry = 100;
  assert.equal(recorder.emit("request_finished", { route_template: "/api/search",
    http_status: 200, duration_ms: 12 }), true);
  mono = 200;
  recorder.control.monotonicExpiry = 10000;
  await recorder.flush();
  const directory = path.join(root, "events", sessionId);
  const names = (await readdir(directory)).filter((name) => name.endsWith(".jsonl"));
  const events = (await Promise.all(names.map((name) => readFile(path.join(directory, name), "utf8"))))
    .join("").trim().split("\n").map(JSON.parse);
  assert.equal(events.filter((event) => event.event_code === "success_aggregate").length, 1);
  assert.equal(recorder.status().expired_queue, 0);
});


test("Node admission uses cached inventory and batches captured lines", async (t) => {
  const base = await mkdtemp(path.join(os.tmpdir(), "okf-batch-"));
  const root = path.join(base, "spool"), controlPath = path.join(base, "capture.json");
  const recorder = new DiagnosticServerRecorder({ root, controlPath, minFreeBytes: 0 });
  t.after(async () => { await recorder.stop(); await rm(base, { recursive: true, force: true }); });
  assert.equal(await recorder.start(), true);
  const sessionId = randomUUID(), now = Date.now();
  await writeFile(controlPath, JSON.stringify({ schema_version: 2, boot_id: randomUUID(), revision: 1,
    active: true, session_id: sessionId, scope: "interface", deadline: now / 1000 + 300,
    lease_until: now / 1000 + 10, policy: { ...policy, level: "detailed", trace_limit_per_second: 100 } }));
  await recorder.refreshControl();
  let inventoryCalls = 0, appendCalls = 0;
  const inventory = recorder._inventory.bind(recorder), append = recorder._append.bind(recorder);
  recorder._inventory = async (...args) => { inventoryCalls++; return inventory(...args); };
  recorder._append = async (...args) => { appendCalls++; return append(...args); };
  for (let i = 0; i < 50; i++) assert.equal(recorder.emit("request_finished", {
    route_template: "/api/search", http_status: 200, duration_ms: 10 }), true);
  await recorder.flush();
  assert.equal(inventoryCalls, 0);
  assert.ok(appendCalls <= 2);
  const directory = path.join(root, "events", sessionId);
  const files = (await readdir(directory)).filter((name) => name.endsWith(".jsonl"));
  const events = (await Promise.all(files.map((name) => readFile(path.join(directory, name), "utf8"))))
    .join("").trim().split("\n").map(JSON.parse);
  assert.equal(events.length, 50);
});
