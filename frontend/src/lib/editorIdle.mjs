export const EDITOR_IDLE_MS = 600000;
export const EDITOR_WARNING_MS = 60000;

/** The caller decides whether expiration closes an editor or retains its draft. */
export function createEditorIdleTimer({ onCountdown, onExpire, onActivity = () => {},
  now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout,
  idleMs = EDITOR_IDLE_MS, warningMs = EDITOR_WARNING_MS, initialDeadline = now() + idleMs }) {
  let deadline = initialDeadline, timer = null, stopped = false, sequence = 0;
  const tick = () => {
    clearTimer(timer);
    if (stopped) return;
    const remaining = deadline - now();
    if (remaining <= 0) { stopped = true; timer = null; onCountdown(0); onExpire(); return; }
    const warning = remaining <= warningMs;
    onCountdown(warning ? Math.ceil(remaining / 1000) : null);
    timer = setTimer(tick, warning ? Math.min(1000, remaining) : remaining - warningMs);
  };
  tick();
  return {
    activity() {
      if (stopped) return false;
      if (now() >= deadline) { tick(); return false; }
      deadline = now() + idleMs; onActivity(++sequence); tick(); return true;
    },
    stop() { stopped = true; clearTimer(timer); timer = null; },
  };
}
