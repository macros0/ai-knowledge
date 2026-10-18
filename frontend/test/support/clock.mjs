export function clock() {
  let now = 0, serial = 0;
  const pending = new Map();
  return { now: () => now, setTimer: (fn, ms) => { const id = ++serial; pending.set(id, { fn, time: now + ms }); return id; },
    clearTimer: id => pending.delete(id),
    advance(ms) { const end = now + ms;
      while (true) { const next = [...pending].sort((a, b) => a[1].time - b[1].time)[0];
        if (!next || next[1].time > end) break;
        pending.delete(next[0]); now = next[1].time; next[1].fn(); }
      now = end;
    }, pending };
}
