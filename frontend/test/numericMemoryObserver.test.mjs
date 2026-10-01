import test from "node:test";
import assert from "node:assert/strict";
import { sampleRow } from "../scripts/numeric-memory-observer.mjs";

test("numeric observer excludes private process metadata and unknown heap fields", () => {
  const proc = { pid: 18, uptime: () => 3, argv: ["CANARY_PRIVATE"], env: { secret: "CANARY" },
    memoryUsage: () => ({rss: 100, heapUsed: 20, heapTotal: 40, external: 2, arrayBuffers: 1, secret: "CANARY"}) };
  const heap = { getHeapStatistics: () => ({total_physical_size: 30, heap_size_limit: 1000,
    number_of_native_contexts: 1, number_of_detached_contexts: 0, secret: "CANARY"}) };
  const row = sampleRow("next", proc, heap, 5000);
  assert.equal(row.birth_unix_ms, 2000);
  assert.equal(row.rss_bytes, 100);
  assert.equal(row.heap_used_bytes, 20);
  assert.equal(row.role, "next");
  assert.ok(!JSON.stringify(row).includes("CANARY"));
  assert.ok(Object.entries(row).every(([key, value]) => key === "role" || (typeof value === "number" && Number.isFinite(value) && value >= 0)));
});

test("invalid numeric readings fail closed", () => {
  const proc = {pid: 18, uptime: () => 1, memoryUsage: () => ({rss: NaN})};
  assert.throws(() => sampleRow("next", proc), /numeric/);
  assert.throws(() => sampleRow("CANARY_PRIVATE"), /role/);
});
