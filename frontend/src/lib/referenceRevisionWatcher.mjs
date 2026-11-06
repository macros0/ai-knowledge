// Content-free shared epochs; one request/timer, with explicit lifecycle cleanup.
export function createReferenceWatcher({load, onChange, onPoll = () => {}, initialVersion = null,
  isVisible = () => true, pollMs = 30_000,
  setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = id => clearTimeout(id)}) {
  let version = initialVersion, pending = null, controller = null, timer = null;
  let stopped = false, enabled = true;
  const valid = value => typeof value === "string" && /^(0|[1-9][0-9]{0,18})$/.test(value);
  if (!Number.isSafeInteger(pollMs) || pollMs < 1000 || (version !== null && !valid(version))) {
    throw new TypeError("Invalid reference watcher configuration");
  }
  function refresh() {
    if (timer !== null) {clearTimer(timer); timer = null;}
    if (stopped || !isVisible()) return Promise.resolve();
    if (pending) return pending;
    controller = new AbortController();
    pending = (async () => {
      try {
        const data = await load(controller.signal);
        if (stopped || !isVisible()) return;
        if (data?.enabled === false) {enabled = false; onPoll(); return;}
        const next = data?.revisions?.references;
        if (data?.enabled !== true || !valid(next)) throw new TypeError("Invalid reference revision");
        enabled = true;
        if (version !== next) {version = next; onChange(next);}
        if (!stopped) onPoll();
      } catch (error) {
        if ([401, 403, 404].includes(error?.status)) enabled = false;
      }
    })().finally(() => {
      pending = null;
      if (!stopped && enabled && isVisible()) timer = setTimer(() => {void refresh();}, pollMs);
    });
    return pending;
  }
  return {refresh, stop() {
    stopped = true;
    if (timer !== null) {clearTimer(timer); timer = null;}
    controller?.abort();
  }};
}
