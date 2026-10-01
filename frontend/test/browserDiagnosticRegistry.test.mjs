import test from "node:test";
import assert from "node:assert/strict";
import { createBrowserDiagnosticRegistry } from "../src/lib/browserDiagnosticRegistry.mjs";

test("a root error can be reported after the React layout unmounts", async () => {
  const calls = [];
  const client = { active: true, deactivate() { this.active = false; },
    report(error, options) { if (!this.active) return false; calls.push([error, options]); return true; },
    async flush() { calls.push("flush"); } };
  const registry = createBrowserDiagnosticRegistry(() => client);
  registry.forUser("admin-1");
  assert.equal(registry.reportRootError(new Error("secret"), "local-id"), true);
  await Promise.resolve();
  assert.equal(calls.length, 2);
  registry.forUser(null);
  assert.equal(registry.reportRootError(new Error("later"), "other"), false);
});
