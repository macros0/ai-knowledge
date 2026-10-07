import assert from "node:assert/strict";
import test from "node:test";

const recent = await import("../src/lib/chatRecentHistory.mjs").catch(() => ({}));
const turn = (id, content = "Question") => ({ id, session_id: `session-${id}`, messages: [
  {role: "user", content}, {role: "assistant", content: "Answer", sources: []},
] });

test("recent history window fits measured pairs rather than a fixed question count", () => {
  assert.equal(typeof recent.recentTurnStart, "function", "history window is missing");
  const turns = Array.from({length: 8}, (_, i) => ({key: String(i), messages: []}));
  assert.equal(recent.recentTurnStart(turns, {height: 300, width: 700, heights: Object.fromEntries(turns.map(t => [t.key, 100]))}), 2);
  assert.equal(recent.recentTurnStart(turns, {height: 300, width: 700, heights: Object.fromEntries(turns.map(t => [t.key, 250]))}), 6);
  assert.equal(recent.recentTurnStart(turns, {height: 300, width: 700, heights: {"7": 1200}}), 7);
});

test("narrow screens show fewer unmeasured long pairs, never an empty window", () => {
  assert.equal(typeof recent.recentTurnStart, "function");
  const turns = Array.from({length: 8}, (_, i) => ({key: String(i), messages: [{message: {content: "text ".repeat(140)}}]}));
  const wide = recent.recentTurnStart(turns, {height: 400, width: 1000});
  const narrow = recent.recentTurnStart(turns, {height: 400, width: 320});
  assert.ok(narrow > wide);
  assert.ok(narrow < turns.length);
  assert.equal(recent.recentTurnStart([], {height: 0, width: 0}), 0);
});

test("restored turns retain provenance and live indexes without duplicating an active session", () => {
  assert.equal(typeof recent.chatFeedTurns, "function");
  const history = [turn(1), {...turn(2), session_id: "active"}];
  history[0].messages[1].sources = [{source_index: 9, doc_id: "doc", title: "Original"}];
  const messages = [{role: "user", text: "New question"}, {role: "assistant", text: "New answer"}, {role: "user", text: "Next question"}];
  const feed = recent.chatFeedTurns(history, messages, "active");
  assert.equal(feed.length, 3);
  assert.deepEqual(feed.map(t => t.key), ["history:1", "live:0", "live:2"]);
  assert.equal(feed[0].messages[1].message, history[0].messages[1]);
  assert.equal(feed[0].messages[1].message.sources[0].selectable, undefined);
  assert.deepEqual(feed[1].messages.map(m => m.index), [0, 1]);
});

test("recent history loads once, prepends older pages, and de-duplicates by question ID", async () => {
  assert.equal(typeof recent.createRecentHistoryLoader, "function");
  let state;
  const requests = [];
  const loader = recent.createRecentHistoryLoader({onChange: s => {state = s;}, getSessionId: () => "active", fetchPage: async options => {
    requests.push(options);
    return options.beforeId ? {turns: [turn(1), turn(2)], next_before_id: null} : {turns: [turn(2), turn(3)], next_before_id: 2};
  }});
  await loader.load();
  await loader.load();
  assert.equal(requests.length, 1);
  assert.equal(requests[0].excludeSessionId, "active");
  await loader.load({older: true});
  assert.equal(requests[1].beforeId, 2);
  assert.deepEqual(state.turns.map(t => t.id), [1, 2, 3]);
  assert.equal(state.nextBeforeId, null);
  assert.equal(state.status, "ready");
});

test("a late history page cannot resurrect a cleared chat or update an unmounted owner", async () => {
  assert.equal(typeof recent.createRecentHistoryLoader, "function");
  for (const action of ["clear", "dispose"]) {
    let resolve;
    let state;
    const loader = recent.createRecentHistoryLoader({onChange: s => {state = s;}, fetchPage: () => new Promise(r => {resolve = r;})});
    const pending = loader.load();
    loader[action]();
    const before = state;
    resolve({turns: [turn(1)], next_before_id: null});
    await pending;
    assert.equal(state, before);
    if (action === "clear") assert.deepEqual(state.turns, []);
  }
});

test("a question sent during initial loading is excluded from the arriving history", async () => {
  assert.equal(typeof recent.createRecentHistoryLoader, "function");
  let resolve;
  let sessionId = null;
  let state;
  const loader = recent.createRecentHistoryLoader({onChange: s => {state = s;}, getSessionId: () => sessionId, fetchPage: () => new Promise(r => {resolve = r;})});
  const pending = loader.load();
  sessionId = "session-2";
  resolve({turns: [turn(1), turn(2)], next_before_id: null});
  await pending;
  assert.deepEqual(state.turns.map(t => t.id), [1]);
});

test("failed older loads preserve visible history and retry the same cursor", async () => {
  assert.equal(typeof recent.createRecentHistoryLoader, "function");
  let state;
  let fail = true;
  const loader = recent.createRecentHistoryLoader({onChange: s => {state = s;}, fetchPage: async options => {
    if (!options.beforeId) return {turns: [turn(3)], next_before_id: 3};
    assert.equal(options.beforeId, 3);
    if (fail) throw new Error("Network failed");
    return {turns: [turn(1)], next_before_id: null};
  }});
  await loader.load();
  await loader.load({older: true});
  assert.deepEqual(state.turns.map(t => t.id), [3]);
  assert.equal(state.status, "error");
  assert.equal(state.nextBeforeId, 3);
  fail = false;
  await loader.load({older: true});
  assert.deepEqual(state.turns.map(t => t.id), [1, 3]);
});
