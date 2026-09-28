// Server-only module. Importing it does not start workers or touch the filesystem.
import { randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import { constants } from "node:fs";
import * as fs from "node:fs/promises";
import path from "node:path";
import { currentRequestId } from "./requestContext.mjs";
import { encodeServerEvent, safeServerException, validRequestId } from "./diagnosticSchema.mjs";

// Next may bundle instrumentation and route handlers as separate module copies
// in the same worker. A process-wide slot keeps one writer for both copies.
const recorderSlot = Symbol.for("okf.diagnostics.serverRecorder");
const currentRecorder = () => globalThis[recorderSlot] || null;
export const setServerRecorder = (recorder) => { globalThis[recorderSlot] = recorder; };
export const emitServerEvent = (eventCode, fields = {}, exception = null) => currentRecorder()?.emit(eventCode, fields, exception) || false;
export const serverRecorderStatus = () => currentRecorder()?.status() || { running: false, storage_degraded: true };

export class DiagnosticServerRecorder {
  constructor({ root, controlPath, budgetBytes = 20 * 1048576, segmentBytes = 5 * 1048576,
    minFreeBytes = 2 * 1024 * 1048576, queueSize = 256, baselineSeconds = 7 * 86400,
    captureSeconds = 86400, baselineEnabled = true, now = Date.now, monotonic = () => performance.now() } = {}) {
    this.root = root ? path.resolve(root) : null;
    this.controlPath = controlPath ? path.resolve(controlPath) : null;
    this.limits = { budgetBytes, segmentBytes, minFreeBytes, queueSize, baselineSeconds, captureSeconds };
    if (Object.values(this.limits).some((value) => !Number.isSafeInteger(value) || value < 0)
      || !budgetBytes || !segmentBytes || segmentBytes > budgetBytes || !queueSize || !baselineSeconds || !captureSeconds) throw new Error("Invalid diagnostic limits");
    this.now = now; this.monotonic = monotonic; this.baselineEnabled = baselineEnabled;
    this.owner = randomUUID(); this.bootId = randomUUID(); this.running = false;
    this.queue = []; this.dedup = new Map(); this.sessions = {}; this.control = null;
    this.controlFingerprint = null; this.chain = Promise.resolve(); this.scheduled = false;
    this.statusFingerprint = null;
    this.counts = { written: 0, dropped: 0, invalid: 0, repeats: 0, expired_queue: 0 };
    this.degraded = false;
  }

  async _checkAbsolute(filename) {
    const absolute = path.resolve(filename), base = path.parse(absolute).root;
    let current = base;
    for (const part of absolute.slice(base.length).split(path.sep).filter(Boolean)) {
      current = path.join(current, part);
      try {
        if ((await fs.lstat(current)).isSymbolicLink()) throw new Error("Unsafe diagnostic path");
      } catch (error) { if (error.code !== "ENOENT") throw error; }
    }
    return absolute;
  }

  async _safe(filename) {
    const absolute = path.resolve(filename), relative = path.relative(this.root, absolute);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error("Unsafe diagnostic path");
    return this._checkAbsolute(absolute);
  }

  async _inventory(directory = this.root) {
    await this._safe(directory);
    const result = [];
    for (const entry of await fs.readdir(directory, { withFileTypes: true })) {
      const filename = await this._safe(path.join(directory, entry.name));
      if (entry.isDirectory()) result.push(...await this._inventory(filename));
      else {
        const info = await fs.lstat(filename);
        if (!info.isFile()) throw new Error("Unsafe diagnostic object");
        result.push({ filename, size: info.size, mtime: info.mtimeMs });
      }
    }
    return result;
  }

  async _ownsMarker() {
    const filename = await this._safe(path.join(this.root, "writer.json"));
    if ((await fs.stat(filename)).size > 4096) return false;
    return JSON.parse(await fs.readFile(filename, "utf8")).owner === this.owner;
  }

  async _admit(bytes) {
    if (!await this._ownsMarker()) throw new Error("Diagnostic writer ownership lost");
    let items = await this._inventory();
    let used = items.reduce((sum, item) => sum + item.size, 0);
    const baseline = items.filter((item) => path.dirname(item.filename) === path.join(this.root, "events", "baseline") && /^\d{13}_[a-f0-9-]{36}\.jsonl$/.test(path.basename(item.filename))).sort((a, b) => a.mtime - b.mtime);
    for (const item of baseline) {
      if (used + bytes <= this.limits.budgetBytes) break;
      await fs.unlink(await this._safe(item.filename)); used -= item.size;
    }
    if (used + bytes > this.limits.budgetBytes) throw new Error("Diagnostic quota exceeded");
    const available = await fs.statfs(this.root);
    if (Number(available.bavail) * Number(available.bsize) - bytes < this.limits.minFreeBytes) throw new Error("Diagnostic disk reserve reached");
  }

  async _atomic(name, value) {
    const encoded = JSON.stringify(value), bytes = Buffer.byteLength(encoded);
    await this._admit(bytes);
    const destination = await this._safe(path.join(this.root, name));
    const temporary = await this._safe(path.join(this.root, `${name}.${this.owner}.${randomUUID()}.tmp`));
    try {
      const handle = await fs.open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW || 0), 0o660);
      try { await handle.writeFile(encoded); await handle.sync(); } finally { await handle.close(); }
      await fs.rename(temporary, destination);
    } finally { await fs.unlink(temporary).catch(() => {}); }
  }

  _serialize(callback) {
    const task = this.chain.then(callback);
    this.chain = task.catch(() => { this.degraded = true; });
    return task;
  }

  async start() {
    if (this.running) return true;
    try {
      if (!this.root) return false;
      await this._checkAbsolute(this.root);
      await fs.mkdir(this.root, { recursive: true, mode: 0o2770 });
      const marker = await this._safe(path.join(this.root, "writer.json"));
      const handle = await fs.open(marker, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW || 0), 0o660);
      try { await handle.writeFile(JSON.stringify({ owner: this.owner, boot_id: this.bootId })); await handle.sync(); } finally { await handle.close(); }
      // The writer is ours now. Remove only old metadata temp files; a stale
      // marker from another process is never claimed automatically.
      for (const name of await fs.readdir(this.root)) {
        if (/^(?:status|sessions)\.json\.(?:tmp|[a-f0-9-]{36}\.[a-f0-9-]{36}\.tmp)$/.test(name)) {
          await fs.unlink(await this._safe(path.join(this.root, name)));
        }
      }
      await fs.mkdir(await this._safe(path.join(this.root, "events")), { recursive: true, mode: 0o2770 });
      const sessionsPath = await this._safe(path.join(this.root, "sessions.json"));
      try {
        if ((await fs.stat(sessionsPath)).size > 65536) throw new Error("Oversized session metadata");
        const saved = JSON.parse(await fs.readFile(sessionsPath, "utf8"));
        for (const [id, expiry] of Object.entries(saved)) if (validRequestId(id) && Number.isFinite(expiry)) this.sessions[id] = expiry;
      } catch (error) { if (error.code !== "ENOENT") throw error; }
      this.running = true;
      await this.maintenance();
      await this.refreshControl();
      this.timer = setInterval(() => {
        void this.refreshControl().catch(() => {});
        void this._serialize(async () => { await this._flushDue(); await this._persistStatus(); }).catch(() => {});
      }, 1000);
      this.timer.unref();
      this.cleanupTimer = setInterval(() => { void this.maintenance().catch(() => {}); }, 60000);
      this.cleanupTimer.unref();
      if (this.baselineEnabled) {
        const revision = process.env.OKF_BUILD_REVISION || "unknown";
        this.emit("server_started", { build_id: /^(?:unknown|[a-f0-9]{7,64})$/.test(revision) ? revision : "unknown" });
      }
      return true;
    } catch {
      this.degraded = true; this.running = false;
      // Only our own successfully claimed marker may be released. Never take over stale markers.
      if (this.root) try { if (await this._ownsMarker()) await fs.unlink(path.join(this.root, "writer.json")); } catch { /* fail safe */ }
      return false;
    }
  }

  _active() {
    if (!this.control || this.now() >= this.control.deadline * 1000 || this.monotonic() >= this.control.monotonicExpiry) return null;
    return this.control.session_id;
  }

  async refreshControl() {
    if (!this.running || !this.controlPath) return;
    try {
      await this._checkAbsolute(this.controlPath);
      if ((await fs.stat(this.controlPath)).size > 4096) throw new Error("Oversized control");
      const value = JSON.parse(await fs.readFile(this.controlPath, "utf8"));
      if (value.schema_version !== 1 || !validRequestId(value.boot_id) || typeof value.active !== "boolean" || !Number.isFinite(value.lease_until)) throw new Error("Invalid control");
      const fingerprint = JSON.stringify(value);
      if (fingerprint === this.controlFingerprint) return;
      this.controlFingerprint = fingerprint;
      const previous = this.control?.session_id;
      this.control = null;
      if (value.active && validRequestId(value.session_id) && ["interface", "system"].includes(value.scope)
        && Number.isFinite(value.deadline) && value.deadline > this.now() / 1000 && value.lease_until > this.now() / 1000) {
        this.control = { session_id: value.session_id, deadline: value.deadline,
          monotonicExpiry: this.monotonic() + Math.min(10000, (value.lease_until * 1000 - this.now()), value.deadline * 1000 - this.now()) };
        this.sessions[value.session_id] = value.deadline * 1000 + this.limits.captureSeconds * 1000;
      }
      if (previous && previous !== this.control?.session_id) this.sessions[previous] = this.now() + this.limits.captureSeconds * 1000;
      if (previous || this.control) await this._serialize(() => this._atomic("sessions.json", this.sessions));
    } catch { this.control = null; this.counts.invalid++; }
  }

  emit(eventCode, fields = {}, exception = null) {
    if (!this.running) return false;
    try {
      const sessionId = this._active();
      const level = exception || eventCode.endsWith("failed") ? "ERROR" : eventCode === "request_finished" && fields.http_status >= 400 ? fields.http_status >= 500 ? "ERROR" : "WARN" : "INFO";
      const baseline = this.baselineEnabled && (["ERROR", "WARN"].includes(level) || ["server_started", "server_stopped", "diagnostic_gap"].includes(eventCode));
      if (!baseline && !sessionId) return false;
      // Caller may provide correlation only. Identity/component/clock are owned here.
      if (["schema_version", "event_id", "boot_id", "component", "origin", "timestamp_utc", "event_code", "level", "diagnostic_session_id"].some((key) => Object.hasOwn(fields, key))) { this.counts.invalid++; return false; }
      const requestId = currentRequestId();
      const event = { schema_version: 1, event_id: randomUUID(), timestamp_utc: new Date(this.now()).toISOString(),
        boot_id: this.bootId, component: "frontend", origin: "server", level, event_code: eventCode,
        ...(requestId ? { request_id: requestId } : {}), ...fields,
        ...(exception ? safeServerException(exception) : {}), ...(sessionId ? { diagnostic_session_id: sessionId } : {}) };
      const encoded = encodeServerEvent(event);
      if (!encoded) { this.counts.invalid++; return false; }
      if (this.queue.length >= this.limits.queueSize) { this.counts.dropped++; return false; }
      this.queue.push({ event, encoded, sessionId, baseline });
      if (!this.scheduled) {
        this.scheduled = true;
        setImmediate(() => { this.scheduled = false; void this._serialize(() => this._drain()).catch(() => {}); });
      }
      return true;
    } catch { this.counts.invalid++; return false; }
  }

  async _append(encoded, stream) {
    const bytes = Buffer.byteLength(encoded);
    if (bytes > this.limits.segmentBytes) throw new Error("Diagnostic event exceeds segment");
    await this._admit(bytes);
    const directory = await this._safe(path.join(this.root, "events", stream));
    await fs.mkdir(directory, { recursive: true, mode: 0o2770 });
    const paths = (await fs.readdir(directory)).filter((name) => /^\d{13}_[a-f0-9-]{36}\.jsonl$/.test(name)).sort();
    let filename = paths.length ? path.join(directory, paths.at(-1)) : null;
    if (!filename || (await fs.stat(await this._safe(filename))).size + bytes > this.limits.segmentBytes) filename = path.join(directory, `${this.now()}_${randomUUID()}.jsonl`);
    const handle = await fs.open(await this._safe(filename), constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND | (constants.O_NOFOLLOW || 0), 0o660);
    try { await handle.writeFile(encoded); } finally { await handle.close(); }
    this.counts.written++;
  }

  async _write(encoded, stream) {
    if (stream !== "baseline" && stream !== this._active()) { this.counts.expired_queue++; return; }
    try { await this._append(encoded, stream); } catch { this.counts.dropped++; this.degraded = true; }
  }

  async _drain() {
    while (this.queue.length) {
      const item = this.queue.shift(), streams = item.baseline ? ["baseline"] : [];
      if (item.sessionId) streams.push(item.sessionId);
      for (const stream of streams) {
        if (stream !== "baseline" && stream !== this._active()) { this.counts.expired_queue++; continue; }
        if (["WARN", "ERROR"].includes(item.event.level)) {
          const { event_id: _eventId, timestamp_utc: _stamp, ...fingerprint } = item.event;
          const key = JSON.stringify([stream, fingerprint]);
          const prior = this.dedup.get(key);
          if (prior && this.monotonic() - prior.started < 1000) { prior.repeats++; prior.last = item.event.timestamp_utc; this.counts.repeats++; continue; }
          if (prior) await this._summary(prior);
          this.dedup.set(key, { event: item.event, stream, started: this.monotonic(), last: item.event.timestamp_utc, repeats: 0 });
          if (this.dedup.size > 256) {
            const retiredKey = this.dedup.keys().next().value;
            await this._summary(this.dedup.get(retiredKey)); this.dedup.delete(retiredKey);
          }
        }
        await this._write(item.encoded, stream);
      }
    }
  }

  async _summary(entry) {
    if (!entry.repeats) return;
    const encoded = encodeServerEvent({ ...entry.event, event_id: randomUUID(), timestamp_utc: entry.last,
      event_code: "repeat_summary", source_event_code: entry.event.event_code,
      first_timestamp_utc: entry.event.timestamp_utc, last_timestamp_utc: entry.last, counts: { repeats: entry.repeats } });
    if (encoded) await this._write(encoded, entry.stream);
    else this.counts.invalid++;
  }

  async _flushDue(all = false) {
    for (const [key, entry] of this.dedup) if (all || this.monotonic() - entry.started >= 1000) { this.dedup.delete(key); await this._summary(entry); }
  }

  async flush() {
    await new Promise((resolve) => setImmediate(resolve));
    await this.chain;
  }

  async maintenance() {
    if (!this.running) return false;
    return this._serialize(async () => {
      for (const item of await this._inventory()) {
        if (!/^\d{13}_[a-f0-9-]{36}\.jsonl$/.test(path.basename(item.filename))) continue;
        const stream = path.basename(path.dirname(item.filename));
        if (path.dirname(path.dirname(item.filename)) !== path.join(this.root, "events")) continue;
        const expiry = this.sessions[stream] ?? item.mtime + this.limits.captureSeconds * 1000;
        const expired = stream === "baseline" ? item.mtime < this.now() - this.limits.baselineSeconds * 1000
          : validRequestId(stream) && stream !== this._active() && expiry <= this.now();
        if (expired) await fs.unlink(await this._safe(item.filename));
      }
      for (const [id, expiry] of Object.entries(this.sessions)) if (expiry <= this.now()) delete this.sessions[id];
      await this._atomic("sessions.json", this.sessions);
      await this._persistStatus(true);
    });
  }

  async _persistStatus(force = false) {
    const value = { schema_version: 1, boot_id: this.bootId, ...this.status() };
    const fingerprint = JSON.stringify(value);
    if (!force && fingerprint === this.statusFingerprint) return;
    await this._atomic("status.json", value);
    this.statusFingerprint = fingerprint;
  }

  async stop(timeoutMs = 5000) {
    clearInterval(this.timer); clearInterval(this.cleanupTimer);
    if (!this.running) return false;
    this.control = null;
    this.emit("server_stopped");
    this.running = false;
    let timer;
    const finished = await Promise.race([
      this.flush().then(() => this._serialize(async () => {
        await this._flushDue(true);
        await this._persistStatus();
      })).then(() => true).catch(() => false),
      new Promise((resolve) => { timer = setTimeout(() => resolve(false), timeoutMs); }),
    ]);
    clearTimeout(timer);
    if (finished) try {
      if (await this._ownsMarker()) { await fs.unlink(path.join(this.root, "writer.json")); return true; }
    } catch { this.degraded = true; }
    return false;
  }

  status() { return { running: this.running, storage_degraded: this.degraded, ...this.counts, queued: this.queue.length }; }
}

export async function initializeServerDiagnostics() {
  if (currentRecorder()) return currentRecorder();
  const baselineEnabled = !["false", "0"].includes((process.env.DIAGNOSTICS_BASELINE_ENABLED || "true").toLowerCase());
  const recorder = new DiagnosticServerRecorder({ root: process.env.OKF_FRONTEND_DIAGNOSTICS_DIR,
    controlPath: process.env.OKF_DIAGNOSTICS_CONTROL_PATH, baselineEnabled });
  setServerRecorder(recorder);
  await recorder.start();
  installServerDiagnosticsShutdown(recorder);
  return recorder;
}

export function installServerDiagnosticsShutdown(recorder, proc = process) {
  if (proc.__okfDiagnosticsShutdownInstalled) return;
  proc.__okfDiagnosticsShutdownInstalled = true;
  let stopping = null;
  const stop = () => stopping ||= recorder.stop(5000);
  proc.on("message", (message) => {
    if (message?.type !== "okf-diagnostics-stop") return;
    void stop().then((released) => proc.send?.({ type: released ? "okf-diagnostics-stopped" : "okf-diagnostics-stop-failed" })).catch(() => {
      proc.send?.({ type: "okf-diagnostics-stop-failed" });
    });
  });
}
