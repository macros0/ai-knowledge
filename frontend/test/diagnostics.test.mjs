import test from "node:test";
import assert from "node:assert/strict";
import { diagnosticActions, diagnosticBundleRequest, diagnosticGapKey, diagnosticReasonKey, diagnosticStatusKey, remainingSeconds, updateDiagnosticView } from "../src/lib/diagnostics.mjs";

test("stopped, expired, degraded and unknown status have distinct actions", () => {
  const base = { capabilities: { capture: true, bundle: true, download: true,
    capture_levels: ["standard", "detailed"], policy_version: 1 }, runtime: { available: true },
    session: { session: null }, recorder: { storage_degraded: false } };
  assert.equal(diagnosticActions(base).canStart, true);
  assert.equal(diagnosticActions({ ...base, capabilities: { capture: true } }).canStart, false);
  assert.equal(diagnosticActions({ ...base, session: { session: { status: "active" } } }).canStop, true);
  assert.equal(diagnosticActions({ ...base, session: { session: { status: "stopped", stop_reason: "expired" } } }).sessionState, "expired");
  assert.equal(diagnosticActions({ ...base, recorder: { storage_degraded: true } }).canStart, false);
  assert.equal(diagnosticActions(null).sessionState, "unknown");
  assert.equal(diagnosticActions(base, { stale: true }).canStart, false);
  assert.equal(diagnosticActions({ ...base, capabilities: { ...base.capabilities, download: false } }).canDownload, false);
});

test("countdown follows server clock and elapsed monotonic time", () => {
  const start = "2026-09-27T10:00:00Z";
  assert.equal(remainingSeconds("2026-09-27T10:00:10Z", start, 3000), 7);
  assert.equal(remainingSeconds("2026-09-27T10:00:10Z", start, 12000), 0);
  assert.equal(remainingSeconds(null, start, 0), null);
});

test("failed refresh preserves selected capture scope but marks state stale", () => {
  const prior = { selectedScope: "document", status: { session: { session: null } }, stale: false };
  const next = updateDiagnosticView(prior, { failed: true });
  assert.equal(next.selectedScope, "document");
  assert.equal(next.stale, true);
  assert.equal(diagnosticActions(next.status, { stale: next.stale }).sessionState, "unknown");
});

test("known status codes map to translation keys without echoing unknown input", () => {
  assert.equal(diagnosticStatusKey("ready"), "diagnostics.bundle.ready");
  assert.equal(diagnosticStatusKey("CANARY_PRIVATE"), "diagnostics.bundle.unknown");
  assert.equal(diagnosticReasonKey("storage_low"), "diagnostics.reason.storage_low");
  assert.equal(diagnosticReasonKey("CANARY_PRIVATE"), "diagnostics.reason.unknown");
  assert.equal(diagnosticGapKey("invalid_input"), "diagnostics.gap.invalid_input");
  assert.equal(diagnosticGapKey("recorder_loss"), "diagnostics.gap.recorder_loss");
  assert.equal(diagnosticGapKey("CANARY_PRIVATE"), "diagnostics.gap.unknown");
});

test("session bundle excludes a previously selected time period", () => {
  const filters = { from_utc: "2026-09-27T00:00:00Z", to_utc: "2026-09-27T01:00:00Z", request_id: "reference" };
  assert.deepEqual(diagnosticBundleRequest(filters, "session-id"), {
    request_id: "reference", session_id: "session-id",
  });
  assert.deepEqual(diagnosticBundleRequest(filters, ""), filters);
});
