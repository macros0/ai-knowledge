import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { createDiagnosticTransport } from "../src/lib/diagnosticTransport.mjs";
import { DiagnosticServerRecorder } from "../src/lib/diagnosticServer.mjs";
import { GET } from "../src/app/api/[...path]/route.js";

const token = "private-token-".repeat(4);
const event = () => ({ schema_version: 1, event_id: randomUUID(), boot_id: randomUUID(),
  timestamp_utc: new Date().toISOString(), component: "frontend", origin: "server",
  level: "ERROR", event_code: "render_failed", exception_type: "Error" });
const options = (fetchImpl) => ({ baseUrl: "https://backend.invalid", token, instanceId: randomUUID(), fetchImpl });

test("transport posts sanitized events with internal identity and stable batch ID", async () => {
  const calls = [];
  const transport = createDiagnosticTransport(options(async (url, init) => {
    calls.push({ url, init }); return Response.json({ accepted: 1, duplicate: false });
  }));
  const batch = { batch_id: randomUUID(), events: [event()] };
  assert.deepEqual(await transport.sendBatch(batch), { accepted: 1, duplicate: false });
  assert.equal(JSON.parse(calls[0].init.body).batch_id, batch.batch_id);
  assert.equal(calls[0].init.headers.Authorization, `Bearer ${token}`);
  assert.equal(calls[0].init.redirect, "error");
  assert.equal(calls[0].url, "https://backend.invalid/internal/diagnostics/events");
  assert.ok(!JSON.stringify(transport).includes(token)); transport.close();
});

test("expired control is rejected and failures do not expose response content", async () => {
  const transport = createDiagnosticTransport(options(async () => Response.json({ active: true, deadline: 0 })));
  assert.equal(await transport.getControl(), null); transport.close();
});

test("invalid events and oversized batches never reach fetch", async () => {
  let calls = 0;
  const transport = createDiagnosticTransport(options(async () => { calls++; }));
  assert.equal(await transport.sendBatch({ batch_id: randomUUID(), events: [{ ...event(), message: "private" }] }), null);
  assert.equal(await transport.sendBatch({ batch_id: randomUUID(), events: Array.from({ length: 101 }, event) }), null);
  assert.equal(calls, 0); transport.close();
});

test("timeout bounds even fetch implementations that ignore AbortSignal", async () => {
  const transport = createDiagnosticTransport({ ...options(() => new Promise(() => {})), timeoutMs: 20 });
  assert.equal(await transport.sendBatch({ batch_id: randomUUID(), events: [event()] }), null);
  transport.close();
});

test("close stops sends and control requests", async () => {
  let calls = 0;
  const transport = createDiagnosticTransport(options(async () => { calls++; }));
  transport.close();
  assert.equal(await transport.getControl(), null);
  assert.equal(await transport.sendBatch({ batch_id: randomUUID(), events: [event()] }), null);
  assert.equal(calls, 0);
});

test("recorder publishes via internal transport without shared event files", async () => {
  const batches = [];
  const recorder = new DiagnosticServerRecorder({ transport: {
    sendBatch: async (batch) => { batches.push(batch); return { accepted: batch.events.length }; },
  } });
  await recorder._appendBatch([JSON.stringify(event()) + "\n"], "baseline");
  assert.equal(batches.length, 1);
  assert.equal(recorder.counts.written, 1);
});

test("public proxy refuses paths escaping its API prefix", async () => {
  const original = globalThis.fetch;
  let fetched = false;
  globalThis.fetch = async () => { fetched = true; return new Response(); };
  try {
    const response = await GET(new Request("http://frontend/api/probe"), {
      params: Promise.resolve({ path: ["..", "internal", "diagnostics", "control"] }),
    });
    assert.equal(response.status, 400);
    assert.equal(fetched, false);
  } finally { globalThis.fetch = original; }
});

test("unsuccessful responses cancel their bodies", async () => {
  let cancelled = false;
  const transport = createDiagnosticTransport(options(async () => ({ ok: false,
    body: { cancel: async () => { cancelled = true; } } })));
  await transport.getControl();
  assert.equal(cancelled, true); transport.close();
});

test("baseline copy survives expiry of the original capture", async () => {
  const batches = [];
  const recorder = new DiagnosticServerRecorder({ transport: {
    sendBatch: async (batch) => { batches.push(batch); return { accepted: batch.events.length }; },
  } });
  await recorder._appendBatch([JSON.stringify({ ...event(), diagnostic_session_id: randomUUID() })], "baseline");
  assert.equal(batches.length, 1);
  assert.equal(batches[0].events[0].diagnostic_session_id, undefined);
});

test("memory admission is bounded at one thousand queued events", async () => {
  const transport = createDiagnosticTransport({ ...options(() => new Promise(() => {})), timeoutMs: 10 });
  const sends = Array.from({ length: 10 }, () => transport.sendBatch({
    batch_id: randomUUID(), events: Array.from({ length: 100 }, event),
  }));
  assert.equal(await transport.sendBatch({ batch_id: randomUUID(), events: [event()] }), null);
  transport.close();
  await Promise.all(sends);
});
