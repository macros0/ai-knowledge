import test from "node:test";
import assert from "node:assert/strict";
import { parseCaptureControl, selectCapture } from "../src/lib/diagnosticPolicy.mjs";

const boot = "22222222-2222-4222-8222-222222222222";
const session = "11111111-1111-4111-8111-111111111111";
const now = 2_000_000;
const policy = { level: "standard", version: 1, aggregate_interval_ms: 5000,
  success_limit_per_second: 100, trace_limit_per_second: 20,
  slow_limit_per_second: 20, max_inflight_traces: 512,
  slow_thresholds_ms: { http_search: 1000, qdrant_db: 2000,
    embeddings_proxy: 2000, llm_chat: 5000, pdf: 10000 } };
const control = (capturePolicy = policy) => ({ schema_version: 2, boot_id: boot, revision: 1,
  active: true, session_id: session, scope: "interface", deadline: now / 1000 + 300,
  lease_until: now / 1000 + 10, policy: capturePolicy });

test("v2 policy validates exact safe fields; v1 cannot activate capture", () => {
  assert.equal(parseCaptureControl({ ...control(), schema_version: 1 }, now), null);
  assert.equal(parseCaptureControl(control({ ...policy, secret: "CANARY" }), now), null);
  assert.equal(parseCaptureControl(control({ ...policy, level: "unknown" }), now), null);
  assert.equal(parseCaptureControl(control(), now)?.policy.level, "standard");
});

test("expired projection remains structurally validated without extending capture", () => {
  const expiredAt = now + 11000;
  assert.equal(parseCaptureControl(control(), expiredAt), null);
  assert.equal(parseCaptureControl(control(), expiredAt, { allowExpired: true })?.session_id, session);
  assert.equal(parseCaptureControl({ ...control(), schema_version: 1 }, expiredAt, { allowExpired: true }), null);
  assert.equal(parseCaptureControl(control({ ...policy, secret: "CANARY" }), expiredAt, { allowExpired: true }), null);
});

test("standard aggregates successful proxy calls and preserves failures", () => {
  const active = parseCaptureControl(control(), now);
  assert.equal(selectCapture(active, "request_finished", { http_status: 200, duration_ms: 25 }).action, "aggregate");
  assert.equal(selectCapture(active, "request_finished", { http_status: 503, duration_ms: 25 }).action, "record");
  assert.equal(selectCapture(active, "proxy_failed", { duration_ms: 25 }).action, "record");
});
