import test from "node:test";
import assert from "node:assert/strict";
import { diagnosticCoverageSummary } from "../src/lib/diagnostics.mjs";

test("coverage labels distinguish aggregation, sampling, legacy and unknown", () => {
  assert.deepEqual(diagnosticCoverageSummary({ format_version: 2,
    coverage_modes: { baseline: "errors", sessions: { a: "aggregated", b: "sampled", c: "legacy" } } }),
  [{ mode: "errors", count: 1 }, { mode: "aggregated", count: 1 },
   { mode: "sampled", count: 1 }, { mode: "legacy", count: 1 }]);
  assert.deepEqual(diagnosticCoverageSummary({ format_version: 1 }), [{ mode: "legacy", count: 1 }]);
  assert.deepEqual(diagnosticCoverageSummary({ format_version: 2, coverage_modes: { sessions: { a: "CANARY" } } }),
    [{ mode: "unknown", count: 1 }]);
});
