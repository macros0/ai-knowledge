import { validRequestId } from "./diagnosticIdentifiers.mjs";

const policyKeys = ["aggregate_interval_ms", "level", "max_inflight_traces",
  "slow_limit_per_second", "slow_thresholds_ms", "success_limit_per_second",
  "trace_limit_per_second", "version"];
const thresholdKeys = ["embeddings_proxy", "http_search", "llm_chat", "pdf", "qdrant_db"];
const exactKeys = (value, keys) => value && typeof value === "object" && !Array.isArray(value)
  && Object.keys(value).sort().join("|") === [...keys].sort().join("|");
const positive = (value, max) => Number.isSafeInteger(value) && value > 0 && value <= max;

export function parseCaptureControl(value, now = Date.now(), { allowExpired = false } = {}) {
  if (!value || value.schema_version !== 2 || !validRequestId(value.boot_id)
    || !Number.isSafeInteger(value.revision) || value.revision < 0
    || typeof value.active !== "boolean" || !Number.isFinite(value.lease_until)
    || (!allowExpired && value.lease_until <= now / 1000)
    || value.lease_until > now / 1000 + 11) return null;
  if (!value.active) return { active: false, revision: value.revision, boot_id: value.boot_id };
  const policy = value.policy;
  if (!validRequestId(value.session_id) || !["interface", "system"].includes(value.scope)
    || !Number.isFinite(value.deadline) || (!allowExpired && value.deadline <= now / 1000)
    || !exactKeys(policy, policyKeys) || !["standard", "detailed"].includes(policy.level)
    || policy.version !== 1 || !positive(policy.aggregate_interval_ms, 60000)
    || !positive(policy.success_limit_per_second, 100000)
    || !positive(policy.trace_limit_per_second, 100000)
    || !positive(policy.slow_limit_per_second, 100000)
    || !positive(policy.max_inflight_traces, 100000)
    || !exactKeys(policy.slow_thresholds_ms, thresholdKeys)
    || thresholdKeys.some((key) => !positive(policy.slow_thresholds_ms[key], 3600000))) return null;
  return { active: true, boot_id: value.boot_id, revision: value.revision,
    session_id: value.session_id, scope: value.scope, deadline: value.deadline,
    lease_until: value.lease_until, policy: structuredClone(policy) };
}

export function selectCapture(control, eventCode, facts, traceState = null) {
  if (!control?.active) return { action: "omit", reason: "no_capture" };
  if (eventCode.endsWith("failed") || eventCode === "browser_error" || eventCode === "diagnostic_gap"
    || (Number.isInteger(facts.http_status) && facts.http_status >= 400)) {
    return { action: "record", reason: "important" };
  }
  if (eventCode === "request_finished") {
    const duration = facts.duration_ms;
    if (Number.isFinite(duration) && duration >= control.policy.slow_thresholds_ms.http_search) {
      const allowed = typeof traceState?.slowSelected === "function"
        ? traceState.slowSelected() : traceState?.slowSelected !== false;
      return allowed ? { action: "record", reason: "slow" } : { action: "aggregate", reason: "slow_limit" };
    }
    if (control.policy.level === "standard") return { action: "aggregate", reason: "standard_success" };
    const selected = typeof traceState?.selected === "function" ? traceState.selected() : traceState?.selected;
    if (selected) return { action: "record", reason: "selected_trace" };
    return { action: "aggregate", reason: "sampled_out_trace" };
  }
  return { action: "record", reason: "other" };
}
