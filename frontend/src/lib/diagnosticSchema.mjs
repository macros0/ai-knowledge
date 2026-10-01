import contract from "./diagnosticContract.json" with { type: "json" };
import { validRequestId } from "./diagnosticIdentifiers.mjs";
export { validRequestId } from "./diagnosticIdentifiers.mjs";

const opaque = /^(?:[a-f0-9]{16}|[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/;
const asset = /^frontend\/_next\/[a-f0-9]{8,64}\.js$/;
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
const sets = Object.fromEntries(Object.entries(contract).filter(([, value]) => Array.isArray(value)).map(([key, value]) => [key, new Set(value)]));
const nativeStackGetter = Object.getOwnPropertyDescriptor(new Error(), "stack")?.get;

export function routeTemplate(value) {
  if (typeof value !== "string") return "/unknown";
  if (sets.routes.has(value)) return value;
  // Match complete known paths; never preserve arbitrary segments or query values.
  for (const template of sets.routes) {
    if (!template.includes("{")) continue;
    const expression = template.split(/(\{[^}]+\})/).map((part) => part.startsWith("{") ? "[^/?#]+" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("");
    if (new RegExp(`^${expression}$`).test(value)) return template;
  }
  return "/unknown";
}

function frames(value, component) {
  if (!Array.isArray(value)) return null;
  const result = [];
  for (const frame of value.slice(0, 32)) {
    if (!record(frame) || Object.keys(frame).sort().join(",") !== "function,line,module") return null;
    if (typeof frame.module !== "string" || typeof frame.function !== "string" || !Number.isInteger(frame.line) || frame.line < 0 || frame.line > 100000) return null;
    if (component === "backend") {
      if (!/^[A-Za-z_][A-Za-z0-9_]{0,100}$/.test(frame.function)) return null;
      if (frame.module !== "external_frame" && !sets.backendModules.has(frame.module)) return null;
    } else {
      if (!["server", "client", "external"].includes(frame.function)) return null;
      if (frame.module !== "external_frame" && !asset.test(frame.module) && !(component === "frontend" && sets.frontendModules.has(frame.module))) return null;
    }
    result.push({ module: frame.module, function: frame.function, line: frame.line });
  }
  return result;
}

export function sanitizeServerEvent(raw) {
  try {
    if (!record(raw) || ![1, 2].includes(raw.schema_version) || typeof raw.event_code !== "string") return null;
    const eventFields = raw.schema_version === 1 ? contract.events : { ...contract.events, ...contract.eventsV2 };
    if (!Object.hasOwn(eventFields, raw.event_code)) return null;
    const allowed = new Set([...contract.base, ...eventFields[raw.event_code]]);
    if (Object.keys(raw).some((key) => !allowed.has(key))) return null;
    if (raw.event_code === "success_aggregate" && ("request_id" in raw || "operation_id" in raw)) return null;
    if (!["backend", "frontend", "browser"].includes(raw.component)
      || !["INFO", "WARN", "ERROR"].includes(raw.level) || !["server", "client_reported"].includes(raw.origin)
      || (raw.component === "browser") !== (raw.origin === "client_reported")) return null;
    if (!validRequestId(raw.event_id) || !validRequestId(raw.boot_id)) return null;
    for (const key of ["request_id", "operation_id", "diagnostic_session_id"]) if (key in raw && !validRequestId(raw[key])) return null;
    for (const key of ["doc_id", "generation_id"]) if (key in raw && (typeof raw[key] !== "string" || !opaque.test(raw[key]))) return null;
    const result = { ...raw };
    for (const key of ["timestamp_utc", "first_timestamp_utc", "last_timestamp_utc", "window_start_utc", "window_end_utc"]) {
      if (key !== "timestamp_utc" && !(key in raw)) continue;
      const value = raw[key];
      if (typeof value !== "string" || value.length > 40 || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$/.test(value) || !Number.isFinite(Date.parse(value))) return null;
      const normalized = new Date(value).toISOString();
      if (normalized.slice(0, 19) !== value.slice(0, 19)) return null;
      result[key] = normalized;
    }
    for (const key of ["duration_ms", "chunk_index", "retry_index", "http_status"]) {
      if (!(key in raw)) continue;
      if (!Number.isFinite(raw[key]) || raw[key] < 0 || raw[key] > 1e12 || (key !== "duration_ms" && !Number.isInteger(raw[key]))) return null;
    }
    if ("http_status" in raw && (raw.http_status < 100 || raw.http_status > 599)) return null;
    const enums = { error_code: sets.errorCodes, exception_type: sets.exceptionTypes,
      route_template: sets.routes, stage: sets.stages, dependency: sets.dependencies,
      http_method: new Set(["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
      dependency_status: new Set(["ok", "down", "rate_limited", "unknown"]),
      source_event_code: new Set(Object.keys(eventFields).filter((key) => key !== "repeat_summary")),
      outcome: new Set(raw.event_code === "operation_summary" ? ["success", "cancelled"] : ["success"]) };
    for (const [key, allowedValues] of Object.entries(enums)) if (key in raw && !allowedValues.has(raw[key])) return null;
    if ("build_id" in raw && (typeof raw.build_id !== "string" || !/^(?:unknown|[a-f0-9]{7,64})$/.test(raw.build_id))) return null;
    if ("counts" in raw) {
      const allowedCounts = raw.event_code === "success_aggregate" ? sets.aggregateCounts : sets.counts;
      if (!record(raw.counts) || Object.entries(raw.counts).some(([key, value]) =>
        !allowedCounts.has(key) || !Number.isInteger(value) || value < 0 || value > 1e12)) return null;
      if (raw.event_code === "success_aggregate" &&
          (Object.keys(raw.counts).length !== sets.aggregateCounts.size || raw.counts.count < 1 ||
           contract.aggregateBucketKeys.reduce((sum, key) => sum + raw.counts[key], 0) !== raw.counts.count ||
           raw.counts.duration_max_us > raw.counts.duration_sum_us)) return null;
    }
    if (raw.event_code === "repeat_summary" && (!enums.source_event_code.has(raw.source_event_code)
      || !result.first_timestamp_utc || !result.last_timestamp_utc || result.first_timestamp_utc > result.last_timestamp_utc
      || !Object.hasOwn(raw.counts || {}, "repeats"))) return null;
    if (raw.event_code === "success_aggregate" &&
        (!result.window_start_utc || !result.window_end_utc || result.window_start_utc > result.window_end_utc ||
         !Object.hasOwn(raw, "counts") || raw.outcome !== "success")) return null;
    if (raw.event_code === "operation_summary" &&
        (!["stage", "duration_ms", "counts", "outcome"].every((key) => Object.hasOwn(raw, key)))) return null;
    if ("frames" in raw) {
      result.frames = frames(raw.frames, raw.component);
      if (!result.frames) return null;
    }
    return result;
  } catch {
    return null;
  }
}

export function encodeServerEvent(raw) {
  const safe = sanitizeServerEvent(raw);
  if (!safe) return null;
  let encoded = JSON.stringify(safe) + "\n";
  while (new TextEncoder().encode(encoded).length > 8192 && safe.frames?.length) {
    safe.frames.pop(); encoded = JSON.stringify(safe) + "\n";
  }
  return new TextEncoder().encode(encoded).length <= 8192 ? encoded : null;
}

export function safeServerException(error) {
  const result = { exception_type: "UnknownError", frames: [] };
  try {
    for (const constructor of [Error, TypeError, ReferenceError, RangeError, SyntaxError, URIError, EvalError, AggregateError]) {
      if (error && Object.getPrototypeOf(error) === constructor.prototype) { result.exception_type = constructor.name; break; }
    }
    // No message, cause, args, source text or arbitrary function names are persisted.
    const descriptor = Object.getOwnPropertyDescriptor(error, "stack");
    const stack = descriptor && "value" in descriptor ? descriptor.value
      : descriptor?.get && descriptor.get === nativeStackGetter ? descriptor.get.call(error) : null;
    if (typeof stack !== "string" || stack.length > 65536) return result;
    for (const entry of stack.split("\n").slice(1, 33)) {
      let frameModule = "external_frame", line = 0;
      const coordinate = /:(\d+):(\d+)\)?$/.exec(entry);
      if (coordinate) {
        const prefix = entry.slice(0, coordinate.index).replaceAll("\\", "/");
        const index = prefix.lastIndexOf("frontend/src/");
        const candidate = index < 0 ? "" : prefix.slice(index);
        if (sets.frontendModules.has(candidate)) { frameModule = candidate; line = Math.min(100000, Number(coordinate[1])); }
        const chunk = /\/_next\/(?:static\/chunks\/)?([a-f0-9]{8,64})\.js$/.exec(prefix);
        if (chunk) { frameModule = `frontend/_next/${chunk[1]}.js`; line = Math.min(100000, Number(coordinate[1])); }
      }
      result.frames.push({ module: frameModule, function: frameModule === "external_frame" ? "external" : "server", line });
    }
  } catch { /* Hostile getters and non-Error thrown values do not escape. */ }
  return result;
}
