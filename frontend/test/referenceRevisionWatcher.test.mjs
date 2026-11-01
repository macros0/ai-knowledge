import test from "node:test";
import assert from "node:assert/strict";
import {createReferenceWatcher} from "../src/lib/referenceRevisionWatcher.mjs";

const snapshot = version => ({enabled: true, revisions: {references: version}});
function clock() {
  const pending = new Map(); let sequence = 0;
  return {pending, setTimer(fn) {pending.set(++sequence, fn); return sequence;},
    clearTimer(id) {pending.delete(id);}};
}

test("shared reference epoch invalidates changed lookups once and keeps one poll", async () => {
  const timers = clock(), changes = [], versions = ["1", "1", "2"];
  const watcher = createReferenceWatcher({...timers, load: async () => snapshot(versions.shift()),
    onChange: version => changes.push(version)});
  await watcher.refresh(); await watcher.refresh(); await watcher.refresh();
  assert.deepEqual(changes, ["1", "2"]);
  assert.equal(timers.pending.size, 1);
  watcher.stop(); assert.equal(timers.pending.size, 0);
});

test("overlapping checks coalesce and unmount suppresses even a late ignored-abort response", async () => {
  const timers = clock(), changes = []; let resolve, signal;
  const watcher = createReferenceWatcher({...timers,
    load: incoming => {signal = incoming; return new Promise(done => {resolve = done;});},
    onChange: version => changes.push(version)});
  const first = watcher.refresh(), second = watcher.refresh();
  assert.equal(first, second);
  watcher.stop(); assert.equal(signal.aborted, true);
  resolve(snapshot("2")); await first;
  assert.deepEqual(changes, []); assert.equal(timers.pending.size, 0);
});

test("malformed epochs cannot replace a known good revision", async () => {
  const timers = clock(), changes = [];
  const responses = [snapshot(-1), snapshot("8")];
  const watcher = createReferenceWatcher({...timers, initialVersion: "7",
    load: async () => responses.shift(), onChange: version => changes.push(version)});
  await watcher.refresh(); assert.deepEqual(changes, []);
  await watcher.refresh(); assert.deepEqual(changes, ["8"]);
  watcher.stop();
});

test("legacy or unauthorized backend stays quiet until an explicit recheck", async () => {
  for (const response of [{enabled: false, revisions: {}}, Object.assign(new Error("denied"), {status: 401})]) {
    const timers = clock(); let calls = 0;
    const watcher = createReferenceWatcher({...timers, load: async () => {
      calls++; if (calls > 1) return snapshot("1");
      if (response instanceof Error) throw response;
      return response;
    }, onChange() {}});
    await watcher.refresh(); assert.equal(timers.pending.size, 0);
    await watcher.refresh(); assert.equal(calls, 2); assert.equal(timers.pending.size, 1);
    watcher.stop();
  }
});

test("hidden tabs do not poll and foreground rechecks the latest epoch", async () => {
  const timers = clock(), changes = []; let visible = false, calls = 0;
  const watcher = createReferenceWatcher({...timers, isVisible: () => visible,
    load: async () => {calls++; return snapshot("3");}, onChange: version => changes.push(version)});
  await watcher.refresh(); assert.equal(calls, 0);
  visible = true; await watcher.refresh();
  assert.equal(calls, 1); assert.deepEqual(changes, ["3"]);
  watcher.stop();
});
