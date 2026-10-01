import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { sanitizeServerEvent } from "../src/lib/diagnosticSchema.mjs";

const corpus = JSON.parse(await readFile(new URL("../../tests/fixtures/diagnostics/events-v2.json", import.meta.url)));

test("v2 corpus has the same safe boundary as backend", () => {
  for (const event of corpus.valid) {
    assert.ok(sanitizeServerEvent(event), event.event_code);
    if (["success_aggregate", "operation_summary"].includes(event.event_code)) {
      assert.equal(sanitizeServerEvent({ ...event, schema_version: 1 }), null);
    }
    for (const change of corpus.invalid_changes) {
      assert.equal(sanitizeServerEvent({ ...event, ...change }), null);
    }
  }
  const aggregate = corpus.valid[0];
  assert.equal(sanitizeServerEvent({ ...aggregate, counts: { ...aggregate.counts, le_50ms: 2 } }), null);
  assert.equal(sanitizeServerEvent({ ...aggregate, request_id: aggregate.event_id }), null);
});
