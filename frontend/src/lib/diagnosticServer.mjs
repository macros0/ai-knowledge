// Server-only module. Importing it does not start workers or touch the filesystem.
import { randomUUID } from "node:crypto";
import { performance } from "node:perf_hooks";
import { constants } from "node:fs";
import * as fs from "node:fs/promises";
import path from "node:path";
import { currentRequestId } from "./requestContext.mjs";
import { encodeServerEvent, routeTemplate, safeServerException, validRequestId } from "./diagnosticSchema.mjs";
import { parseCaptureControl, selectCapture } from "./diagnosticPolicy.mjs";

// Next may bundle instrumentation and route handlers as separate module copies
// in the same worker. A process-wide slot keeps one writer for both copies.
const recorderSlot = Symbol.for("okf.diagnostics.serverRecorder");
const currentRecorder = () => globalThis[recorderSlot] || null;
const aggregateRequestFields = new Set(["route_template", "http_method", "http_status", "duration_ms"]);
export const setServerRecorder = (recorder) => { globalThis[recorderSlot] = recorder; };
export const emitServerEvent = (eventCode, fields = {}, exception = null) => currentRecorder()?.emit(eventCode, fields, exception) || false;
export const serverRecorderStatus = () => currentRecorder()?.status() || { running: false, storage_degraded: true };

// Resolve operator-owned links above a location (macOS /var and /tmp are links)
// while keeping the final component unresolved, so a linked spool or control
// file is still refused. Missing ancestors are created later by mkdir.
async function canonicalLocation(filename) {
  const absolute = path.resolve(filename), missing = [path.basename(absolute)];
  let parent = path.dirname(absolute);
  for (;;) {
    try { return path.join(await fs.realpath(parent), ...missing); }
    catch (error) {
      if (error.code !== "ENOENT" || path.dirname(parent) === parent) throw error;
      missing.unshift(path.basename(parent));
      parent = path.dirname(parent);
    }
  }
}

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
    this.queue = []; this.dedup = new Map(); this.aggregates = new Map(); this.sessions = {}; this.control = null;
    this.rates = new Map(); this.rateIdentity = null;
    this.ledger = new Map(); this.currentSegments = new Map();
    this.controlFingerprint = null; this.chain = Promise.resolve(); this.scheduled = false;
    this.statusFingerprint = null;
    this.counts = { written: 0, dropped: 0, invalid: 0, repeats: 0, expired_queue: 0,
      intentional_aggregated: 0, intentional_sampled: 0, sampled_out_slow: 0, aggregate_overflow: 0 };
    this.degraded = false;
    this.startedAtUtc = new Date(this.now()).toISOString();
  }

  // Links are refused from base downward; base is canonicalized in start().
  async _checkAbsolute(filename, base) {
    const absolute = path.resolve(filename), relative = path.relative(base, absolute);
    if (relative === ".." || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error("Unsafe diagnostic path");
    let current = base;
    for (const part of ["", ...relative.split(path.sep).filter(Boolean)]) {
      current = path.join(current, part);
      try {
        if ((await fs.lstat(current)).isSymbolicLink()) throw new Error("Unsafe diagnostic path");
      } catch (error) { if (error.code !== "ENOENT") throw error; }
    }
    return absolute;
  }

  async _safe(filename) {
    return this._checkAbsolute(filename, this.root);
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

  async _reconcileInventory() {
    const items = await this._inventory();
    this.ledger = new Map(items.map((item) => [item.filename, item.size]));
    this.currentSegments.clear();
    for (const item of items) {
      const directory = path.dirname(item.filename);
      if (path.dirname(directory) !== path.join(this.root, "events")
        || !/^\d{13}_[a-f0-9-]{36}\.jsonl$/.test(path.basename(item.filename))) continue;
      const stream = path.basename(directory), previous = this.currentSegments.get(stream);
      if (!previous || item.filename > previous) this.currentSegments.set(stream, item.filename);
    }
  }

  async _ownsMarker() {
    const filename = await this._safe(path.join(this.root, "writer.json"));
    if ((await fs.stat(filename)).size > 4096) return false;
    return JSON.parse(await fs.readFile(filename, "utf8")).owner === this.owner;
  }

  async _admit(bytes) {
    if (!await this._ownsMarker()) throw new Error("Diagnostic writer ownership lost");
    let used = [...this.ledger.values()].reduce((sum, size) => sum + size, 0);
    const baselineDirectory = path.join(this.root, "events", "baseline");
    const baseline = [...this.ledger].filter(([filename]) => path.dirname(filename) === baselineDirectory
      && /^\d{13}_[a-f0-9-]{36}\.jsonl$/.test(path.basename(filename)))
      .sort(([left], [right]) => left.localeCompare(right));
    for (const [filename, size] of baseline) {
      if (used + bytes <= this.limits.budgetBytes) break;
      await fs.unlink(await this._safe(filename));
      this.ledger.delete(filename);
      if (this.currentSegments.get("baseline") === filename) this.currentSegments.delete("baseline");
      used -= size;
    }
    if (used + bytes > this.limits.budgetBytes) throw new Error("Diagnostic quota exceeded");
    const available = await fs.statfs(this.root);
    if (Number(available.bavail) * Number(available.bsize) - bytes < this.limits.minFreeBytes)
      throw new Error("Diagnostic disk reserve reached");
  }

  async _atomic(name, value) {
    const encoded = JSON.stringify(value), bytes = Buffer.byteLength(encoded);
    await this._admit(bytes);
    const destination = await this._safe(path.join(this.root, name));
    const temporary = await this._safe(path.join(this.root, `${name}.${this.owner}.${randomUUID()}.tmp`));
    try {
      const handle = await fs.open(temporary, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | (constants.O_NOFOLLOW || 0), 0o660);
      try { await handle.writeFile(encoded); await handle.sync(); } finally { await handle.close(); }
      this.ledger.set(temporary, bytes);
      await fs.rename(temporary, destination);
      this.ledger.delete(temporary);
      this.ledger.set(destination, bytes);
    } finally { await fs.unlink(temporary).catch(() => {}); this.ledger.delete(temporary); }
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
      this.root = await canonicalLocation(this.root);
      if (this.controlPath) this.controlPath = await canonicalLocation(this.controlPath);
      await this._checkAbsolute(this.root, this.root);
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
      await this._reconcileInventory();
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
    if (!this.control || this.now() >= this.control.deadline * 1000
      || this.now() >= this.control.lease_until * 1000
      || this.monotonic() >= this.control.monotonicExpiry) return null;
    return this.control.session_id;
  }

  async refreshControl() {
    if (!this.running || !this.controlPath) return;
    const ticket = {};
    this.controlRefreshTicket = ticket;
    try {
      await this._checkAbsolute(this.controlPath, path.dirname(this.controlPath));
      if ((await fs.stat(this.controlPath)).size > 4096) throw new Error("Oversized control");
      const value = JSON.parse(await fs.readFile(this.controlPath, "utf8"));
      if (this.controlRefreshTicket !== ticket) return;
      const fingerprint = JSON.stringify(value);
      if (fingerprint === this.controlFingerprint) return;
      const parsed = parseCaptureControl(value, this.now(), { allowExpired: true });
      if (!parsed) throw new Error("Incompatible diagnostic control");
      const previous = this.control?.session_id;
      if (previous && (previous !== parsed.session_id || this.control?.revision !== parsed.revision))
        await this._serialize(() => this._flushAggregates(previous));
      if (this.controlRefreshTicket !== ticket) return;
      this.controlFingerprint = fingerprint;
      this.control = null;
      let changed = false;
      if (parsed.active && parsed.lease_until * 1000 > this.now() && parsed.deadline * 1000 > this.now()) {
        this.control = { ...parsed, monotonicExpiry: this.monotonic() + Math.min(10000,
          (parsed.lease_until * 1000 - this.now()), parsed.deadline * 1000 - this.now()) };
        const expiry = parsed.deadline * 1000 + this.limits.captureSeconds * 1000;
        if (this.sessions[parsed.session_id] !== expiry) {
          this.sessions[parsed.session_id] = expiry; changed = true;
        }
      }
      if (previous && previous !== this.control?.session_id) {
        this.sessions[previous] = this.now() + this.limits.captureSeconds * 1000; changed = true;
      }
      if (changed) await this._serialize(() => this._atomic("sessions.json", this.sessions));
    } catch (error) {
      if (this.controlRefreshTicket !== ticket) return;
      this.control = null;
      this.controlFingerprint = null;
      if (error?.code !== "ENOENT") this.counts.invalid++;
    }
  }

  emit(eventCode, fields = {}, exception = null) {
    if (!this.running) return false;
    try {
      const sessionId = this._active();
      const leaseExpiry = sessionId ? this.control.monotonicExpiry : null;
      const level = exception || eventCode.endsWith("failed") ? "ERROR" : eventCode === "request_finished" && fields.http_status >= 400 ? fields.http_status >= 500 ? "ERROR" : "WARN" : "INFO";
      const baseline = this.baselineEnabled && (["ERROR", "WARN"].includes(level) || ["server_started", "server_stopped", "diagnostic_gap"].includes(eventCode));
      if (!baseline && !sessionId) return false;
      if (sessionId && !baseline) {
        const decision = selectCapture(this.control, eventCode, fields,
          { selected: () => this._selectDetailed(), slowSelected: () => this._selectRate("slow",
            this.control.policy.slow_limit_per_second) });
        if (decision.action === "omit") { this.counts.intentional_sampled++; return false; }
        if (decision.action === "aggregate") {
          if (eventCode !== "request_finished" || Object.keys(fields).some((key) => !aggregateRequestFields.has(key))
            || !Number.isInteger(fields.http_status) || fields.http_status < 200 || fields.http_status >= 400
            || !Number.isFinite(fields.duration_ms) || fields.duration_ms < 0 || fields.duration_ms > 1e9
            || (fields.http_method && !["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"].includes(fields.http_method))) {
            this.counts.invalid++; return false;
          }
          this._observeAggregate(sessionId, fields, leaseExpiry);
          this.counts.intentional_aggregated++;
          if (decision.reason === "slow_limit") this.counts.sampled_out_slow++;
          return true;
        }
      }
      // Caller may provide correlation only. Identity/component/clock are owned here.
      if (["schema_version", "event_id", "boot_id", "component", "origin", "timestamp_utc", "event_code", "level", "diagnostic_session_id"].some((key) => Object.hasOwn(fields, key))) { this.counts.invalid++; return false; }
      const requestId = currentRequestId();
      const event = { schema_version: 1, event_id: randomUUID(), timestamp_utc: new Date(this.now()).toISOString(),
        boot_id: this.bootId, component: "frontend", origin: "server", level, event_code: eventCode,
        ...(requestId ? { request_id: requestId } : {}), ...fields,
        ...(exception ? safeServerException(exception) : {}), ...(sessionId ? { diagnostic_session_id: sessionId } : {}) };
      const encoded = encodeServerEvent(event);
      if (!encoded) { this.counts.invalid++; return false; }
      const important = baseline || ["WARN", "ERROR"].includes(level);
      const reserve = Math.min(32, Math.floor(this.limits.queueSize / 8));
      if (this.queue.length >= this.limits.queueSize - (important ? 0 : reserve)) {
        this.counts.dropped++; return false;
      }
      this.queue.push({ event, encoded, sessionId, baseline, leaseExpiry });
      this._scheduleDrain();
      return true;
    } catch { this.counts.invalid++; return false; }
  }

  _selectDetailed() {
    if (this.control?.policy.level !== "detailed") return false;
    return this._selectRate("trace", this.control.policy.trace_limit_per_second);
  }

  _selectRate(name, limit) {
    const identity = `${this.control.session_id}|${this.control.revision}`;
    if (identity !== this.rateIdentity) { this.rates.clear(); this.rateIdentity = identity; }
    const second = Math.floor(this.monotonic() / 1000);
    const previous = this.rates.get(name);
    const used = previous?.second === second ? previous.used + 1 : 1;
    this.rates.set(name, { second, used });
    return used <= limit;
  }

  _scheduleDrain() {
    if (this.scheduled) return;
    this.scheduled = true;
    setImmediate(() => {
      void this._serialize(() => this._drain()).catch(() => {}).finally(() => {
        this.scheduled = false;
        if (this.queue.length) this._scheduleDrain();
      });
    });
  }

  _observeAggregate(sessionId, fields, leaseExpiry) {
    const route = routeTemplate(fields.route_template);
    const duration = Number.isFinite(fields.duration_ms) && fields.duration_ms >= 0
      ? Math.min(1e12, Math.round(fields.duration_ms * 1000)) : 0;
    const key = `${sessionId}|${route}`;
    let item = this.aggregates.get(key);
    if (!item) {
      item = { sessionId, route, leaseExpiry, start: new Date(this.now()).toISOString(), end: null,
        started: this.monotonic(), counts: { count: 0, duration_sum_us: 0,
          duration_max_us: 0, le_10ms: 0, le_50ms: 0, le_250ms: 0,
          le_1000ms: 0, le_5000ms: 0, le_30000ms: 0, gt_30000ms: 0 } };
      this.aggregates.set(key, item);
    }
    item.leaseExpiry = Math.max(item.leaseExpiry, leaseExpiry);
    if (item.counts.duration_sum_us + duration > 1e12 || item.counts.count >= 1e12) {
      this.counts.aggregate_overflow++;
      this._serialize(async () => {
        await this._flushAggregates(sessionId);
        this._observeAggregate(sessionId, { route_template: route, duration_ms: duration / 1000 }, leaseExpiry);
      }).catch(() => { this.counts.dropped++; this.degraded = true; });
      return;
    }
    item.counts.count++;
    item.counts.duration_sum_us += duration;
    item.counts.duration_max_us = Math.max(item.counts.duration_max_us, duration);
    const bounds = [10000, 50000, 250000, 1000000, 5000000, 30000000];
    const names = ["le_10ms", "le_50ms", "le_250ms", "le_1000ms", "le_5000ms", "le_30000ms", "gt_30000ms"];
    item.counts[names[bounds.findIndex((limit) => duration <= limit) === -1 ? 6 : bounds.findIndex((limit) => duration <= limit)]]++;
    item.end = new Date(this.now()).toISOString();
  }

  async _flushAggregates(sessionId = null, dueOnly = false) {
    for (const [key, item] of this.aggregates) {
      if (sessionId && item.sessionId !== sessionId) continue;
      if (dueOnly && this.monotonic() - item.started < (this.control?.policy.aggregate_interval_ms ?? 5000)) continue;
      this.aggregates.delete(key);
      const encoded = encodeServerEvent({ schema_version: 2, event_id: randomUUID(),
        timestamp_utc: item.end, boot_id: this.bootId, component: "frontend",
        origin: "server", level: "INFO", event_code: "success_aggregate",
        diagnostic_session_id: item.sessionId, route_template: item.route,
        outcome: "success", window_start_utc: item.start, window_end_utc: item.end,
        counts: item.counts });
      if (encoded) await this._write(encoded, item.sessionId, item.leaseExpiry);
      else this.counts.invalid++;
    }
  }

  async _appendBatch(lines, stream) {
    const directory = await this._safe(path.join(this.root, "events", stream));
    await fs.mkdir(directory, { recursive: true, mode: 0o2770 });
    let pending = [], pendingBytes = 0;
    const flush = async () => {
      if (!pending.length) return;
      await this._admit(pendingBytes);
      let filename = this.currentSegments.get(stream);
      let size = filename ? this.ledger.get(filename) : undefined;
      if (size === undefined || size + pendingBytes > this.limits.segmentBytes) {
        filename = path.join(directory, `${this.now()}_${randomUUID()}.jsonl`);
        size = 0; this.currentSegments.set(stream, filename);
      }
      const handle = await fs.open(await this._safe(filename), constants.O_WRONLY | constants.O_CREAT | constants.O_APPEND | (constants.O_NOFOLLOW || 0), 0o660);
      try {
        await handle.writeFile(pending.join(""));
        const actual = (await handle.stat()).size;
        if (actual !== size + pendingBytes) throw new Error("Short diagnostic write");
        this.ledger.set(filename, actual);
        this.counts.written += pending.length;
      } catch (error) {
        await handle.truncate(size).catch(() => {});
        this.ledger.set(filename, size);
        throw error;
      } finally { await handle.close(); }
      pending = []; pendingBytes = 0;
    };
    for (const line of lines) {
      const bytes = Buffer.byteLength(line);
      if (bytes > this.limits.segmentBytes) throw new Error("Diagnostic event exceeds segment");
      if (pendingBytes + bytes > Math.min(65536, this.limits.segmentBytes)) await flush();
      pending.push(line); pendingBytes += bytes;
    }
    await flush();
  }

  async _append(encoded, stream) { await this._appendBatch([encoded], stream); }

  _streamWritable(stream, leaseExpiry) {
    // Admission happened in emit() while capture was active. A manual stop
    // revokes new admissions, but accepted items may finish within their
    // original or renewed same-session lease and the retention window.
    return stream === "baseline" || (Number.isFinite(this.sessions[stream])
      && this.now() < this.sessions[stream]
      && ((Number.isFinite(leaseExpiry) && this.monotonic() < leaseExpiry)
        || this._active() === stream));
  }

  async _write(encoded, stream, leaseExpiry = null) {
    if (!this._streamWritable(stream, leaseExpiry)) { this.counts.expired_queue++; return; }
    try { await this._append(encoded, stream); } catch { this.counts.dropped++; this.degraded = true; }
  }

  async _drain() {
    // Detach once before any IO await: producers can only refill the bounded queue.
    const admitted = this.queue.splice(0);
    const batches = new Map();
    for (const item of admitted) {
      const streams = item.baseline ? ["baseline"] : [];
      if (item.sessionId) streams.push(item.sessionId);
      for (const stream of streams) {
        if (!this._streamWritable(stream, item.leaseExpiry)) { this.counts.expired_queue++; continue; }
        if (["WARN", "ERROR"].includes(item.event.level)) {
          const { event_id: _eventId, timestamp_utc: _stamp, ...fingerprint } = item.event;
          const key = JSON.stringify([stream, fingerprint]);
          const prior = this.dedup.get(key);
          if (prior && this.monotonic() - prior.started < 1000) {
            prior.repeats++; prior.last = item.event.timestamp_utc;
            prior.leaseExpiry = Math.max(prior.leaseExpiry ?? -Infinity, item.leaseExpiry ?? -Infinity);
            this.counts.repeats++; continue;
          }
          if (prior) await this._summary(prior);
          this.dedup.set(key, { event: item.event, stream, leaseExpiry: item.leaseExpiry,
            started: this.monotonic(), last: item.event.timestamp_utc, repeats: 0 });
          if (this.dedup.size > 256) {
            const retiredKey = this.dedup.keys().next().value;
            await this._summary(this.dedup.get(retiredKey)); this.dedup.delete(retiredKey);
          }
        }
        if (!batches.has(stream)) batches.set(stream, []);
        batches.get(stream).push(item.encoded);
      }
    }
    for (const [stream, lines] of batches) {
      const writtenBefore = this.counts.written;
      try { await this._appendBatch(lines, stream); }
      catch {
        this.counts.dropped += Math.max(0, lines.length - (this.counts.written - writtenBefore));
        this.degraded = true;
      }
    }
  }

  async _summary(entry) {
    if (!entry.repeats) return;
    const encoded = encodeServerEvent({ ...entry.event, event_id: randomUUID(), timestamp_utc: entry.last,
      event_code: "repeat_summary", source_event_code: entry.event.event_code,
      first_timestamp_utc: entry.event.timestamp_utc, last_timestamp_utc: entry.last, counts: { repeats: entry.repeats } });
    if (encoded) await this._write(encoded, entry.stream, entry.leaseExpiry);
    else this.counts.invalid++;
  }

  async _flushDue(all = false) {
    await this._flushAggregates(null, !all);
    for (const [key, entry] of this.dedup) if (all || this.monotonic() - entry.started >= 1000) { this.dedup.delete(key); await this._summary(entry); }
  }

  async flush() {
    do {
      await new Promise((resolve) => setImmediate(resolve));
      await this.chain;
    } while (this.scheduled || this.queue.length);
    await this._serialize(() => this._flushAggregates());
  }

  async maintenance() {
    if (!this.running) return false;
    return this._serialize(async () => {
      await this._reconcileInventory();
      for (const [filename, size] of this.ledger) {
        const item = { filename, size, mtime: Number(path.basename(filename).slice(0, 13)) };
        if (!/^\d{13}_[a-f0-9-]{36}\.jsonl$/.test(path.basename(item.filename))) continue;
        const stream = path.basename(path.dirname(item.filename));
        if (path.dirname(path.dirname(item.filename)) !== path.join(this.root, "events")) continue;
        const expiry = this.sessions[stream] ?? item.mtime + this.limits.captureSeconds * 1000;
        const expired = stream === "baseline" ? item.mtime < this.now() - this.limits.baselineSeconds * 1000
          : validRequestId(stream) && stream !== this._active() && expiry <= this.now();
        if (expired) {
          await fs.unlink(await this._safe(item.filename));
          this.ledger.delete(item.filename);
          if (this.currentSegments.get(stream) === item.filename) this.currentSegments.delete(stream);
        }
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
    this.control = null;
    if (finished) try {
      if (await this._ownsMarker()) { await fs.unlink(path.join(this.root, "writer.json")); return true; }
    } catch { this.degraded = true; }
    return false;
  }

  status() { return { running: this.running, storage_degraded: this.degraded,
    started_at_utc: this.startedAtUtc, ...this.counts, queued: this.queue.length }; }
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
