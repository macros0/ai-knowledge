// Source responses are JSON. Compare every field, including new server fields.
function equalJson(a, b) {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length
    && keys.every((key) => Object.hasOwn(b, key) && equalJson(a[key], b[key]));
}

export function reconcileChatSources(previous = [], incoming = []) {
  const byIndex = new Map(previous.map((source, index) => [source.source_index ?? index, source]));
  const next = incoming.map((source, index) => {
    const old = byIndex.get(source.source_index ?? index);
    return equalJson(old, source) ? old : source;
  });
  return next.length === previous.length && next.every((source, index) => source === previous[index])
    ? previous : next;
}

export function createChatDeltaBuffer({ onFlush, schedule = requestAnimationFrame, cancel = cancelAnimationFrame }) {
  let text = "";
  let frame = null;
  let revision = 0;
  let closed = false;
  function flush() {
    revision += 1;
    if (frame != null) cancel(frame);
    frame = null;
    const pending = text;
    text = "";
    if (!closed && pending) onFlush(pending);
  }
  return {
    append(delta) {
      if (closed || !delta) return;
      text += delta;
      if (frame != null) return;
      const scheduledRevision = revision;
      frame = schedule(() => {
        if (scheduledRevision === revision) flush();
      });
    },
    flush,
    discard() {
      closed = true;
      flush();
    },
  };
}
