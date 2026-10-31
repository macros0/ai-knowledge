// Server-only transport. Credentials remain in closures, never in status/events.
import { parseCaptureControl } from "./diagnosticPolicy.mjs";
import { sanitizeServerEvent, validRequestId } from "./diagnosticSchema.mjs";

const MAX_BYTES = 256 * 1024;

export function createDiagnosticTransport({ baseUrl, token, instanceId,
  fetchImpl = fetch, now = Date.now, timeoutMs = 2000 }) {
  const base = new URL(baseUrl);
  if (!["http:", "https:"].includes(base.protocol) || base.username || base.password
    || base.search || base.hash || base.pathname !== "/" || !validRequestId(instanceId)
    || typeof token !== "string" || token.length < 32 || /[\r\n]/.test(token)
    || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 2000) {
    throw new Error("Invalid internal diagnostics configuration");
  }
  let closed = false, queued = 0, tail = Promise.resolve();
  const controllers = new Set();

  async function request(path, body, maxBytes = MAX_BYTES) {
    if (closed) return null;
    const controller = new AbortController();
    controllers.add(controller);
    let timer;
    const timeout = new Promise((resolve) => {
      timer = setTimeout(() => { controller.abort(); resolve(null); }, timeoutMs);
    });
    const operation = (async () => {
      const response = await fetchImpl(new URL(path, base).href, {
        method: body ? "POST" : "GET", headers: { Authorization: `Bearer ${token}`,
          ...(body ? { "Content-Type": "application/json" } : {}) },
        ...(body ? { body } : {}), signal: controller.signal, redirect: "error", cache: "no-store",
      });
      if (!response.ok || !response.body) {
        void response.body?.cancel().catch(() => {});
        return null;
      }
      const reader = response.body.getReader();
      let bytes = 0;
      const chunks = [];
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          bytes += value.byteLength;
          if (closed || controller.signal.aborted || bytes > maxBytes) {
            void reader.cancel().catch(() => {}); return null;
          }
          chunks.push(value);
        }
        return JSON.parse(Buffer.concat(chunks).toString("utf8"));
      } finally { reader.releaseLock(); }
    })().catch(() => null);
    try { return await Promise.race([operation, timeout]); }
    finally { clearTimeout(timer); controllers.delete(controller); }
  }

  return {
    async getControl() {
      const value = await request("/internal/diagnostics/control", null, 4096);
      const parsed = closed ? null : parseCaptureControl(value, now());
      return parsed ? { ...parsed, lease_until: value.lease_until } : null;
    },
    async sendBatch(batch) {
      if (closed || !validRequestId(batch?.batch_id) || !Array.isArray(batch.events)
        || !batch.events.length || batch.events.length > 100 || queued + batch.events.length > 1000) return null;
      const events = batch.events.map(sanitizeServerEvent);
      if (events.some((value) => !value || value.component !== "frontend" || value.origin !== "server")) return null;
      const body = JSON.stringify({ instance_id: instanceId, batch_id: batch.batch_id, events });
      if (Buffer.byteLength(body) > MAX_BYTES) return null;
      queued += events.length;
      const operation = tail.then(async () => {
        const value = await request("/internal/diagnostics/events", body);
        return value && value.accepted === events.length && typeof value.duplicate === "boolean"
          ? { accepted: value.accepted, duplicate: value.duplicate } : null;
      }).finally(() => { queued -= events.length; });
      tail = operation.catch(() => null);
      return operation;
    },
    close() { closed = true; for (const controller of controllers) controller.abort(); },
  };
}
