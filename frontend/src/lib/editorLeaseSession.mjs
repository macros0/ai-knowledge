import { createEditorIdleTimer } from "./editorIdle.mjs";

export function createEditorToken() {
  return Array.from(crypto.getRandomValues(new Uint8Array(32)), byte => byte.toString(16).padStart(2, "0")).join("");
}

/** One opening of one editor. Server transport lives in an adapter; drafts live outside. */
export function createEditorLeaseSession({ adapter, token = createEditorToken(), onChange, onAcquired,
  onExpire = () => {}, onLost = () => {}, now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let stopped = false, started = false, state = "acquiring", status = null, countdown = null;
  let heartbeat = null, transportTimer = null, idle = null, renewal = null, sequence = 0;
  let expires = 0, serverIdle = 0, lastRenew = 0;
  const snapshot = () => ({ state, status, countdown });
  let handlingActivity = false, activityNotification = null;
  const emit = () => {
    if (stopped) return;
    // Activity handlers run in React's input capture phase. Publishing state
    // there restores the controlled value before onChange can read the edit.
    // Notify after the browser finishes input; update lease deadlines now.
    if (handlingActivity) {
      if (activityNotification === null) activityNotification = setTimer(() => {
        activityNotification = null; emit();
      }, 0);
      return;
    }
    onChange(snapshot());
  };
  const context = () => status?.lease_required === false ? {} : adapter.context
    ? adapter.context(status, token) : { edit_token: token, lease_epoch: status?.lease_epoch };
  const release = () => (status?.lease_epoch || (adapter.context && status?.state === "editing")) && status.lease_required !== false
    ? Promise.resolve(adapter.release(context())).catch(() => {}) : Promise.resolve();
  const stopTimers = () => { clearTimer(heartbeat); clearTimer(transportTimer); clearTimer(activityNotification); activityNotification = null; idle?.stop(); };
  const lose = (expired = false, error = null) => {
    if (stopped || state !== "editing") return;
    stopTimers(); state = "lost"; countdown = null;
    if (error) status = { ...status, code: error.code };
    emit(); release();
    if (expired) onExpire(); else onLost(error);
  };
  const applyStatus = (next, receivedAt = now()) => {
    status = next;
    if (next.lease_required === false) { expires = Infinity; return; }
    const server = Date.parse(next.server_now);
    const remaining = Date.parse(next.expires_at) - server;
    const idleRemaining = Date.parse(next.idle_expires_at) - server;
    if (!Number.isFinite(remaining) || !Number.isFinite(idleRemaining) ||
        receivedAt + Math.min(remaining, idleRemaining) <= now()) {
      throw Object.assign(new Error("Expired editor session"), { code: "reference_edit_lock_lost",
        idleExpired: Number.isFinite(idleRemaining) && receivedAt + idleRemaining <= now() });
    }
    expires = receivedAt + remaining; serverIdle = receivedAt + idleRemaining;
    clearTimer(transportTimer); transportTimer = setTimer(() => lose(now() >= serverIdle), expires - now());
  };
  const scheduleHeartbeat = () => {
    clearTimer(heartbeat);
    if (!stopped && state === "editing" && status?.lease_required !== false) {
      heartbeat = setTimer(async () => { await renew(); scheduleHeartbeat(); }, 60000);
    }
  };
  const renew = () => {
    if (stopped || state !== "editing" || status?.lease_required === false) return Promise.resolve();
    if (now() >= expires) { lose(now() >= serverIdle); return Promise.resolve(); }
    if (renewal) return renewal;
    const sent = sequence; lastRenew = now();
    renewal = Promise.resolve().then(() => adapter.renew({ ...context(), activity_sequence: sent }))
      .then(next => { if (!stopped && state === "editing") { applyStatus(next); emit(); } })
      .catch(error => lose(false, error)).finally(() => {
        renewal = null;
        if (!stopped && state === "editing" && sequence > sent) renew();
      });
    return renewal;
  };
  return {
    async start() {
      if (started || stopped) return;
      started = true; emit();
      try {
        const next = await adapter.acquire({ edit_token: token });
        const receivedAt = now();
        status = next;
        if (stopped) { await release(); return; }
        await onAcquired?.(next, () => !stopped);
        if (stopped) return;
        applyStatus(next, receivedAt); state = "editing";
        if (next.lease_required !== false) idle = createEditorIdleTimer({ now, setTimer, clearTimer, initialDeadline: serverIdle,
          onCountdown(value) { countdown = value; emit(); }, onExpire: () => lose(true),
          onActivity(value) { sequence = value;
            if (now() - lastRenew >= 30000 || serverIdle - now() <= 120000) renew(); } });
        emit(); scheduleHeartbeat();
      } catch (error) {
        await release();
        if (stopped) return;
        stopTimers(); state = error.code === "scope_change_in_progress" ? "applying"
          : error.code === "reference_edit_locked" || error.code === "scope_edit_locked" ? "blocked" : "lost";
        status = error.data || { code: error.code }; emit();
        if (error.idleExpired) onExpire();
      }
    },
    snapshot,
    activity(event) {
      if (event?.isTrusted === false) return false;
      handlingActivity = true;
      try { return idle?.activity() ?? false; }
      finally { handlingActivity = false; }
    },
    renewNow: renew,
    reportError: error => lose(false, error),
    mutationContext() { if (state !== "editing" || stopped) return null;
      if (now() >= expires) { lose(now() >= serverIdle); return null; } return context(); },
    dispose() { if (stopped) return; stopped = true; stopTimers(); release(); },
  };
}
