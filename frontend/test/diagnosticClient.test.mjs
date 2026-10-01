import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { ApiError, getDocumentStats, chat } from "../src/lib/api.js";
import { readChatStream } from "../src/lib/chatStream.mjs";
import { DiagnosticClient } from "../src/lib/diagnosticClient.mjs";

test("HTTP API error carries validated server reference from header or body", async (t) => {
  const previous = globalThis.fetch; t.after(() => { globalThis.fetch = previous; });
  for (const source of ["header", "body"]) {
    const id = randomUUID();
    globalThis.fetch = async () => Response.json({ detail: "private", code: "internal_error",
      ...(source === "body" ? { request_id: id } : {}) }, { status: 500,
      headers: source === "header" ? { "x-request-id": id } : {} });
    await assert.rejects(getDocumentStats(), (error) => error instanceof ApiError && error.requestId === id && !error.localReportId);
  }
});

test("network failure has explicitly local reference and never a fake server ID", async (t) => {
  const previous = globalThis.fetch; t.after(() => { globalThis.fetch = previous; });
  globalThis.fetch = async () => { throw new TypeError("CANARY_NETWORK"); };
  await assert.rejects(getDocumentStats(), (error) => error instanceof ApiError && error.requestId === null
    && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(error.localReportId));
});

test("stream failure after HTTP 200 keeps its reference through chat API wrapper", async (t) => {
  const id = randomUUID(), previous = globalThis.fetch; t.after(() => { globalThis.fetch = previous; });
  globalThis.fetch = async () => new Response(JSON.stringify({ type: "error", code: "conflict", status: 409, request_id: id }) + "\n",
    { headers: { "x-request-id": id } });
  await assert.rejects(chat("question", [], 5, "hybrid", null, "", true, "all", () => {}), (error) => error.requestId === id && error.code === "conflict");
});

test("truncated or malformed stream keeps safe header ID and cancels its reader", async () => {
  const id = randomUUID();
  for (const content of ["{bad}\n", '{"type":"delta","text":"part"}\n']) {
    await assert.rejects(readChatStream(new Response(content, { headers: { "x-request-id": id } })), (error) => error.requestId === id);
  }
});

test("untrusted reference text is discarded before displaying or copying", () => {
  const error = new ApiError("failed", { status: 500, requestId: "CANARY\npassword" });
  assert.equal(error.requestId, null);
  assert.equal(error.localReportId, null);
});

test("browser reports require opt-in and never send messages, DOM, URLs or names", async () => {
  const sent = [];
  const reporter = new DiagnosticClient({ send: async (event) => { sent.push(event); }, route: () => "/admin/diagnostics", buildId: "abcdef123" });
  const error = new Error("CANARY_DOCUMENT");
  error.stack = "Error: CANARY_DOCUMENT\n at CANARY_NAME (https://private?token=CANARY:2:3)\n at fn (https://public/_next/static/chunks/abcdef123456.js:12:4)";
  assert.equal(reporter.report(error), false);
  const participationId = randomUUID();
  reporter.activate({ participation_id: participationId, expires_at: new Date(Date.now() + 300000).toISOString() });
  assert.equal(reporter.report(error), true);
  await reporter.flush();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].event_code, "browser_error");
  assert.equal(sent[0].participation_id, participationId);
  assert.equal(reporter.participationId(), participationId);
  assert.ok(!JSON.stringify(sent).includes("CANARY"));
  assert.deepEqual(sent[0].frames.at(-1), { module: "frontend/_next/abcdef123456.js", function: "client", line: 12 });
  reporter.deactivate();
});

test("offline browser queue is bounded and expires monotonically with no retry after deadline", async () => {
  let wall = Date.now(), mono = 0, attempts = 0;
  const reporter = new DiagnosticClient({ send: async () => { attempts++; throw new TypeError("offline"); }, now: () => wall, monotonic: () => mono });
  reporter.activate({ participation_id: randomUUID(), expires_at: new Date(wall + 300000).toISOString() });
  for (let i = 0; i < 1000; i++) reporter.report(new Error("CANARY"));
  assert.ok(reporter.status().queued <= 20);
  assert.ok(reporter.status().queued_bytes <= 80 * 1024);
  assert.ok(reporter.status().dropped > 0);
  await reporter.flush(); assert.equal(attempts, 1);
  wall -= 3600000; mono += 301000;
  await reporter.flush();
  assert.equal(attempts, 1);
  assert.equal(reporter.status().queued, 0);
  assert.equal(reporter.status().active, false);
});

test("ingest rejection revokes reporter and does not report its own failure", async () => {
  let attempts = 0;
  const reporter = new DiagnosticClient({ send: async () => { attempts++; throw new ApiError("private", { status: 403 }); } });
  reporter.activate({ participation_id: randomUUID(), expires_at: new Date(Date.now() + 300000).toISOString() });
  reporter.report(new Error("CANARY"));
  await reporter.flush();
  assert.equal(attempts, 1);
  assert.equal(reporter.report(new Error("CANARY")), false);
  assert.equal(reporter.status().queued, 0);
});
