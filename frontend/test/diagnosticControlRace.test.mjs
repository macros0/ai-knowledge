import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import path from "node:path";
import os from "node:os";
import { randomUUID } from "node:crypto";
import { syncBuiltinESMExports } from "node:module";
import { DiagnosticServerRecorder } from "../src/lib/diagnosticServer.mjs";

const policy = { level: "standard", version: 1, aggregate_interval_ms: 5000,
  success_limit_per_second: 100, trace_limit_per_second: 20,
  slow_limit_per_second: 20, max_inflight_traces: 512,
  slow_thresholds_ms: { http_search: 1000, qdrant_db: 2000,
    embeddings_proxy: 2000, llm_chat: 5000, pdf: 10000 } };

for (const scenario of ["older stop", "older start", "older read failure"]) {
  test(`a delayed control poll cannot apply ${scenario} after a newer poll`, { timeout: 5000 }, async (t) => {
    const base = await fs.mkdtemp(path.join(os.tmpdir(), "okf-control-race-"));
    const root = path.join(base, "spool"), controlPath = path.join(base, "capture.json");
    const boot = randomUUID(), session = randomUUID(), now = Date.now() / 1000;
    const active = (revision) => ({ schema_version: 2, boot_id: boot, revision, active: true,
      session_id: session, scope: "interface", deadline: now + 300, lease_until: now + 10, policy });
    const inactive = (revision) => ({ schema_version: 2, boot_id: boot, revision, active: false,
      lease_until: now + 10 });
    const older = scenario === "older start" ? active(1) : inactive(0);
    const newer = scenario === "older start" ? inactive(2) : active(1);
    await fs.writeFile(controlPath, JSON.stringify(older));
    const recorder = new DiagnosticServerRecorder({ root, controlPath, minFreeBytes: 0 });
    const readFile = fs.readFile;
    let release, reached;
    const paused = new Promise((resolve) => { release = resolve; });
    const checkpoint = new Promise((resolve) => { reached = resolve; });
    t.after(async () => {
      release(); fs.readFile = readFile; syncBuiltinESMExports();
      await recorder.stop(); await fs.rm(base, { recursive: true, force: true });
    });
    assert.equal(await recorder.start(), true);
    clearInterval(recorder.timer);
    let first = true;
    fs.readFile = async (...args) => {
      const value = await readFile(...args);
      if (args[0] === recorder.controlPath && first) {
        first = false; reached(); await paused;
        if (scenario === "older read failure") throw Object.assign(new Error("synthetic"), { code: "EIO" });
      }
      return value;
    };
    syncBuiltinESMExports();
    const delayed = recorder.refreshControl();
    await checkpoint;
    await fs.writeFile(controlPath, JSON.stringify(newer));
    await recorder.refreshControl();
    const invalid = recorder.status().invalid;
    release(); await delayed;
    assert.equal(recorder._active(), newer.active ? session : null);
    assert.equal(recorder.status().invalid, invalid);
    if (newer.active) {
      for (let i = 0; i < 16; i++) assert.equal(recorder.emit("request_finished", {
        route_template: "/api/search", http_status: 200, duration_ms: 12 }), true);
      await recorder.flush();
      const directory = path.join(root, "events", session);
      const events = (await Promise.all((await fs.readdir(directory)).map((name) =>
        readFile(path.join(directory, name), "utf8")))).join("").trim().split("\n").map(JSON.parse);
      assert.equal(events.find((event) => event.event_code === "success_aggregate").counts.count, 16);
    }
  });
}
