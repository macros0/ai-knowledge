const BUNDLE_STATES = new Set(["queued", "building", "ready", "failed", "deleted", "expired"]);
const STOP_REASONS = new Set(["manual", "expired", "disabled", "size_limit", "storage_low", "storage_error", "server_restarted"]);
const GAPS = new Set(["recorder_loss", "recorder_status_unavailable", "frontend_loss",
  "frontend_unavailable", "frontend_status_unavailable", "frontend_status_stale",
  "metadata_unavailable", "invalid_input", "container_state_unavailable"]);

export function remainingSeconds(expiresAt, serverNow, elapsedMs = 0) {
  const expires = Date.parse(expiresAt || ""), now = Date.parse(serverNow || "");
  if (!Number.isFinite(expires) || !Number.isFinite(now) || !Number.isFinite(elapsedMs)) return null;
  return Math.max(0, Math.ceil((expires - now - Math.max(0, elapsedMs)) / 1000));
}

export function diagnosticActions(status, { stale = false } = {}) {
  if (!status || stale || status.runtime?.available === false) {
    return { sessionState: "unknown", canStart: false, canStop: false, canBundle: false, canDownload: false };
  }
  const active = status.session?.session;
  const last = status.session?.last_session;
  const storageDegraded = status.recorder?.storage_degraded || status.quota?.storage_degraded;
  const auditDegraded = status.session?.audit_pending || status.session?.audit_gap;
  const sessionState = active?.status === "active" ? "active"
    : active?.status === "starting" ? "starting"
      : (active || last)?.stop_reason === "expired" ? "expired" : "stopped";
  return {
    sessionState,
    canStart: !!status.capabilities?.capture && !active && !storageDegraded && !auditDegraded,
    canStop: !!active && ["active", "starting"].includes(active.status),
    canBundle: !!status.capabilities?.bundle && !storageDegraded && !auditDegraded,
    canDownload: !!status.capabilities?.download,
  };
}

export function updateDiagnosticView(previous, result) {
  if (result?.failed) return { ...previous, stale: true };
  return { ...previous, status: result?.status ?? null, stale: false };
}

export function diagnosticStatusKey(value) {
  return `diagnostics.bundle.${BUNDLE_STATES.has(value) ? value : "unknown"}`;
}

export function diagnosticReasonKey(value) {
  return `diagnostics.reason.${STOP_REASONS.has(value) ? value : "unknown"}`;
}
export function diagnosticGapKey(value) {
  return `diagnostics.gap.${GAPS.has(value) ? value : "unknown"}`;
}
export function diagnosticBundleRequest(filters, sessionId) {
  if (!sessionId) return filters;
  const coordinates = { ...filters };
  delete coordinates.from_utc;
  delete coordinates.to_utc;
  return { ...coordinates, session_id: sessionId };
}
