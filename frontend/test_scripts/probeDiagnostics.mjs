// Synthetic Node spool memory probe. Run with --expose-gc for comparable RSS.
import { randomUUID } from "node:crypto";
import { spawnSync } from "node:child_process";
import { mkdir, readdir, stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DiagnosticServerRecorder } from "../src/lib/diagnosticServer.mjs";

const ownPath = fileURLToPath(import.meta.url);
const mode = process.argv[2];

async function measure(selected, root) {
  await mkdir(root, { recursive: true });
  const controlPath = path.join(root, "capture.json");
  const sessionId = randomUUID();
  const controlBootId = randomUUID();
  let lastControl = 0;
  const refreshLease = async () => {
    const now = Date.now() / 1000;
    await writeFile(controlPath, JSON.stringify({ schema_version: 1, boot_id: controlBootId,
      active: true, session_id: sessionId, scope: "interface",
      deadline: now + 300, lease_until: now + 10 }));
    lastControl = Date.now();
  };
  if (selected === "capture") await refreshLease();
  const recorder = new DiagnosticServerRecorder({
    root: path.join(root, "spool"), controlPath: selected === "capture" ? controlPath : undefined,
    minFreeBytes: 0, budgetBytes: 20 * 1048576,
  });
  if (!await recorder.start()) throw new Error("Node diagnostic spool did not start");
  global.gc?.();
  const rssBefore = process.memoryUsage().rss;
  let rssPeak = rssBefore;
  for (let i = 0; i < 2000; i++) {
    recorder.emit(selected === "capture" ? "request_finished" : "proxy_failed",
      selected === "capture" ? { http_status: 200 } : { http_status: 502, error_code: "network_error" });
    if (i % 20 === 19) {
      await recorder.flush();
      if (selected === "capture" && Date.now() - lastControl >= 5000) {
        await refreshLease();
        await recorder.refreshControl();
      }
      rssPeak = Math.max(rssPeak, process.memoryUsage().rss);
    }
  }
  await recorder.stop();
  global.gc?.();
  const rssAfter = process.memoryUsage().rss;
  const files = async (dir) => {
    const result = [];
    for (const name of await readdir(dir, { withFileTypes: true })) {
      const target = path.join(dir, name.name);
      result.push(...(name.isDirectory() ? await files(target) : [target]));
    }
    return result;
  };
  let usedBytes = 0;
  for (const filename of await files(path.join(root, "spool"))) usedBytes += (await stat(filename)).size;
  if (usedBytes > 20 * 1048576) throw new Error("Node spool exceeded quota");
  return { mode: selected, events_attempted: 2000, rss_before: rssBefore,
    rss_peak: rssPeak, rss_after: rssAfter, rss_peak_delta: rssPeak - rssBefore,
    used_bytes: usedBytes, recorder: recorder.status() };
}

if (mode === "baseline" || mode === "capture") {
  const result = await measure(mode, path.resolve(process.argv[3]));
  process.stdout.write(`${JSON.stringify(result)}\n`);
} else if (mode === "--output") {
  const output = path.resolve(process.argv[3]);
  const root = path.resolve(process.argv[4]);
  const result = { environment: "isolated Node diagnostic spool; synthetic events; no Next server",
    node: process.version, at_utc: new Date().toISOString() };
  for (const selected of ["baseline", "capture"]) {
    const child = spawnSync(process.execPath, ["--expose-gc", ownPath, selected, path.join(root, selected)],
      { encoding: "utf8", timeout: 120000 });
    if (child.status !== 0) throw new Error(`Node ${selected} probe failed: ${child.stderr}`);
    result[selected] = JSON.parse(child.stdout.trim());
  }
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, `${JSON.stringify(result, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify({ baseline_peak_delta: result.baseline.rss_peak_delta,
    capture_peak_delta: result.capture.rss_peak_delta })}\n`);
} else {
  throw new Error("Usage: node test_scripts/probeDiagnostics.mjs --output FILE ROOT");
}
