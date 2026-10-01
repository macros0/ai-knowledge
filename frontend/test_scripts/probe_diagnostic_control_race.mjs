// Deterministic mechanism check. The unguarded reference is a code variant,
// not an assertion that this scheduling happened in the historical CT run.
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { randomUUID, createHash } from "node:crypto";
import { syncBuiltinESMExports } from "node:module";
import { DiagnosticServerRecorder } from "../src/lib/diagnosticServer.mjs";

const sourceUrl = new URL("../src/lib/diagnosticServer.mjs", import.meta.url);
const source = await fs.readFile(sourceUrl, "utf8");
const reference = source.replace(/      if \(this\.controlRefreshTicket !== ticket\) return;\r?\n/g, "");
assert.notEqual(reference, source, "The reference must actually remove the stale-poll guard");
const absoluteReference = reference.replace(/from "(\.\/[^\"]+)"/g,
  (_, relative) => `from "${new URL(relative, sourceUrl).href}"`);
const Reference = (await import(`data:text/javascript;base64,${Buffer.from(absoluteReference).toString("base64")}`)).DiagnosticServerRecorder;
const policy = { level: "standard", version: 1, aggregate_interval_ms: 5000,
  success_limit_per_second: 100, trace_limit_per_second: 20, slow_limit_per_second: 20,
  max_inflight_traces: 512, slow_thresholds_ms: { http_search: 1000, qdrant_db: 2000,
    embeddings_proxy: 2000, llm_chat: 5000, pdf: 10000 } };

async function reproduce(Recorder) {
  const base = await fs.mkdtemp(path.join(os.tmpdir(), "okf-count-gap-"));
  const root = path.join(base, "spool"), controlPath = path.join(base, "capture.json");
  const boot = randomUUID(), session = randomUUID(), now = Date.now() / 1000;
  const older = { schema_version: 2, boot_id: boot, revision: 0, active: false, lease_until: now + 10 };
  const newer = { ...older, revision: 1, active: true, session_id: session,
    scope: "interface", deadline: now + 300, policy };
  await fs.writeFile(controlPath, JSON.stringify(older));
  const recorder = new Recorder({ root, controlPath, baselineEnabled: false, minFreeBytes: 0 });
  const readFile = fs.readFile;
  let release, reached;
  const paused = new Promise((resolve) => { release = resolve; });
  const checkpoint = new Promise((resolve) => { reached = resolve; });
  let delayed;
  try {
    assert.equal(await recorder.start(), true);
    clearInterval(recorder.timer);
    clearInterval(recorder.cleanupTimer);
    let first = true;
    fs.readFile = async (...args) => {
      const value = await readFile(...args);
      if (args[0] === controlPath && first) { first = false; reached(); await paused; }
      return value;
    };
    syncBuiltinESMExports();
    delayed = recorder.refreshControl();
    await checkpoint;
    await fs.writeFile(controlPath, JSON.stringify(newer));
    await recorder.refreshControl();
    assert.equal(recorder._active(), session);
    const emit = () => recorder.emit("request_finished", {
      route_template: "/api/search", http_status: 200, duration_ms: 12 });
    for (let i = 0; i < 7312; i++) assert.equal(emit(), true);
    release(); await delayed;
    const activeAfterLatePoll = recorder._active() === session;
    let admitted = 7312;
    // Two c=8 batches arriving between the delayed poll and the next timer tick.
    for (let batch = 0; batch < 2; batch++) for (let i = 0; i < 8; i++) admitted += Number(emit());
    await recorder.flush();
    let captured = 0;
    const directory = path.join(root, "events", session);
    for (const filename of await fs.readdir(directory)) {
      for (const line of (await readFile(path.join(directory, filename), "utf8")).trim().split("\n")) {
        const event = JSON.parse(line);
        if (event.event_code === "success_aggregate" && event.route_template === "/api/search") captured += event.counts.count;
      }
    }
    const status = recorder.status();
    return { expected: 7328, admitted, captured, active_after_late_poll: activeAfterLatePoll,
      dropped: status.dropped, invalid: status.invalid, expired_queue: status.expired_queue };
  } finally {
    release(); await delayed;
    fs.readFile = readFile; syncBuiltinESMExports();
    await recorder.stop();
    assert.equal(path.dirname(base), path.resolve(os.tmpdir()));
    assert.ok(path.basename(base).startsWith("okf-count-gap-"));
    await fs.rm(base, { recursive: true, force: true });
  }
}

const unguarded = await reproduce(Reference);
const guarded = await reproduce(DiagnosticServerRecorder);
assert.equal(unguarded.captured, 7312);
assert.equal(guarded.captured, 7328);
for (const result of [unguarded, guarded]) {
  assert.equal(result.dropped + result.invalid + result.expired_queue, 0);
}
console.log(JSON.stringify({ schema_version: 1,
  scope: "controlled stale-poll mechanism; historical attribution unconfirmed",
  source_sha256: createHash("sha256").update(source).digest("hex"), unguarded, guarded }, null, 2));
