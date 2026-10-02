import test from "node:test";
import assert from "node:assert/strict";
import { createChatDeltaBuffer, reconcileChatSources } from "../src/lib/chatRenderState.mjs";
import { applyAnswerEvent } from "../src/lib/chatAnswerState.mjs";

function harness() {
  const frames = new Map();
  const flushed = [];
  let id = 0;
  const buffer = createChatDeltaBuffer({
    onFlush: (text) => flushed.push(text),
    schedule: (callback) => { frames.set(++id, callback); return id; },
    cancel: (frame) => frames.delete(frame),
  });
  return { buffer, frames, flushed };
}

test("stream bursts share one frame and preserve exact text and Unicode", () => {
  const { buffer, frames, flushed } = harness();
  const chunks = Array.from({ length: 100 }, (_, index) => `${index}:Привет 📚\n[1] `);
  chunks.forEach((text) => buffer.append(text));
  assert.equal(frames.size, 1);
  [...frames.values()][0]();
  assert.deepEqual(flushed, [chunks.join("")]);
  buffer.flush();
  assert.equal(flushed.length, 1);
});

test("manual flush invalidates an old frame without consuming the next burst", () => {
  const { buffer, frames, flushed } = harness();
  buffer.append("first");
  const staleFrame = [...frames.values()][0];
  buffer.flush();
  buffer.append("second");
  staleFrame();
  assert.deepEqual(flushed, ["first"]);
  buffer.flush();
  assert.deepEqual(flushed, ["first", "second"]);
});

test("stop, error, final result or unmount discard pending and reject late callbacks", () => {
  for (const reason of ["stop", "error", "result", "unmount"]) {
    const { buffer, frames, flushed } = harness();
    buffer.append(reason);
    const staleFrame = [...frames.values()][0];
    buffer.discard(); buffer.append("late"); buffer.flush(); staleFrame();
    assert.deepEqual(flushed, []);
    assert.equal(frames.size, 0);
  }
});

test("unchanged JSON sources retain their array and nested row objects", () => {
  const sources = [{ source_index: 1, doc_id: "a", source_path: ["root", "sheet"], tags: ["SAP"], coverage: { batch: 1 } }];
  assert.equal(reconcileChatSources(sources, JSON.parse(JSON.stringify(sources))), sources);
  const next = reconcileChatSources(sources, [...JSON.parse(JSON.stringify(sources)), { source_index: 2, doc_id: "b" }]);
  assert.equal(next[0], sources[0]);
  assert.equal(next.length, 2);
});

test("coverage, paths, tags, removed and future fields always refresh the row", () => {
  const source = { source_index: 1, doc_id: "a", tags: ["old"], source_path: ["root"], in_model_context: false, future: { flag: false } };
  for (const patch of [{ tags: ["new"] }, { source_path: ["sheet"] }, { in_model_context: true }, { future: { flag: true } }, { new_field: 1 }]) {
    const incoming = { ...source, ...patch };
    const next = reconcileChatSources([source], [incoming]);
    assert.notEqual(next[0], source);
    assert.deepEqual(next[0], incoming);
  }
  const { future: _removed, ...incoming } = source;
  assert.deepEqual(reconcileChatSources([source], [incoming]), [incoming]);
  assert.deepEqual(reconcileChatSources([source], []), []);
});

test("incoming order is authoritative while unchanged identities remain reusable", () => {
  const before = [{ source_index: 1, doc_id: "a" }, { source_index: 2, doc_id: "b" }];
  const after = reconcileChatSources(before, [structuredClone(before[1]), structuredClone(before[0])]);
  assert.equal(after[0], before[1]); assert.equal(after[1], before[0]);
});

test("legacy history and equal source IDs in different documents keep complete paths", () => {
  const before = [{ doc_id: "a", source_id: "root", source_path: ["root", "old"] }, { doc_id: "b", source_id: "root", source_path: ["root"] }];
  const incoming = [{ ...before[0], source_path: ["root", "new"] }, structuredClone(before[1])];
  const after = reconcileChatSources(before, incoming);
  assert.deepEqual(after, incoming);
  assert.notEqual(after[0], before[0]);
  assert.equal(after[1], before[1]);
});

test("duplicate source event is a state no-op and manual collapse survives updates", () => {
  const sources = [{ source_index: 1, doc_id: "a" }];
  const messages = [{ role: "assistant", attemptId: "current", sources, sourcesTouched: true, sourcesOpen: false }];
  assert.equal(applyAnswerEvent(messages, { type: "sources", attemptId: "current", sources: structuredClone(sources) }), messages);
  const changed = applyAnswerEvent(messages, { type: "sources", attemptId: "current", sources: [{ ...sources[0], in_model_context: true }] });
  assert.equal(changed[0].sourcesOpen, false);
  assert.equal(changed[0].sources[0].in_model_context, true);
  assert.equal(applyAnswerEvent(changed, { type: "sources", attemptId: "old", sources: [] }), changed);
});
