import { safeServerException, routeTemplate } from "./diagnosticSchema.mjs";
import { validRequestId } from "./diagnosticIdentifiers.mjs";
import { sendDiagnosticBrowserEvent } from "./api.js";

// A queue exists only in this browser's memory, after explicit participation.
export class DiagnosticClient {
  constructor({ send = sendDiagnosticBrowserEvent, route = () => globalThis.location?.pathname || "/unknown",
    buildId = "unknown", now = Date.now, monotonic = () => performance.now() } = {}) {
    this.send = send; this.route = route; this.now = now; this.monotonic = monotonic;
    this.buildId = /^(?:unknown|[a-f0-9]{7,64})$/.test(buildId) ? buildId : "unknown";
    this.queue = []; this.bytes = 0; this.participation = null; this.inflight = false;
    this.dropped = 0; this.invalid = 0;
  }

  activate(value) {
    this.deactivate();
    const expires = Date.parse(value?.expires_at);
    if (!validRequestId(value?.participation_id) || !Number.isFinite(expires)) return false;
    const serverNow = value.server_now ? Date.parse(value.server_now) : this.now();
    const duration = Math.min(3600000, expires - serverNow);
    if (!Number.isFinite(duration) || duration <= 0) return false;
    this.participation = { id: value.participation_id, deadline: this.monotonic() + duration };
    this.lastAttempt = -Infinity;
    return true;
  }

  deactivate() {
    this.participation = null; this.dropped += this.queue.length; this.queue = []; this.bytes = 0;
  }

  _active() {
    if (this.participation && this.monotonic() >= this.participation.deadline) this.deactivate();
    return !!this.participation;
  }

  report(error, { requestId = null, localReportId = null } = {}) {
    if (!this._active()) return false;
    try {
      const eventId = validRequestId(localReportId) ? localReportId : globalThis.crypto?.randomUUID?.();
      if (!eventId) return false;
      const frames = safeServerException(error).frames.map((frame) => /^frontend\/_next\/[a-f0-9]{8,64}\.js$/.test(frame.module)
        ? { ...frame, function: "client" } : { module: "external_frame", function: "external", line: 0 });
      const event = { event_id: eventId, event_code: "browser_error", error_code: "browser_error",
        route_template: routeTemplate(this.route()), build_id: this.buildId, frames,
        ...(validRequestId(requestId) ? { request_id: requestId } : {}) };
      const encoded = JSON.stringify(event);
      const bytes = new TextEncoder().encode(JSON.stringify({ ...event, participation_id: this.participation.id })).length;
      if (bytes > 4096) { this.dropped++; return false; }
      if (this.queue.length >= 20 || this.bytes + bytes > 80 * 1024) { this.dropped++; return false; }
      this.queue.push({ encoded, bytes }); this.bytes += bytes;
      return true;
    } catch { this.invalid++; return false; }
  }

  async flush() {
    if (!this._active() || this.inflight || !this.queue.length || this.monotonic() - this.lastAttempt < 6000) return;
    const participationId = this.participation.id, item = this.queue[0];
    this.inflight = true; this.lastAttempt = this.monotonic();
    try {
      await this.send({ ...JSON.parse(item.encoded), participation_id: participationId });
      // A late response may not remove an event from a newer participation.
      if (this.participation?.id === participationId && this.queue[0] === item) { this.queue.shift(); this.bytes -= item.bytes; }
    } catch (error) {
      if (this.participation?.id === participationId && [401, 403, 410].includes(error?.status)) this.deactivate();
      // Network/429 failures remain queued until expiry. Do not report ingest errors.
    } finally { this.inflight = false; this._active(); }
  }

  status() {
    return { active: this._active(), queued: this.queue.length, queued_bytes: this.bytes, dropped: this.dropped, invalid: this.invalid };
  }

  participationId() {
    return this._active() ? this.participation.id : null;
  }
}
