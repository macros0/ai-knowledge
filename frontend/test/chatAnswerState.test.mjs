import test from "node:test";
import assert from "node:assert/strict";
import { groupSourcesByDocument, applyAnswerEvent, searchLimitWarning } from "../src/lib/chatAnswerState.mjs";

test("groups every fragment under one document without losing source order", () => {
  const sources = [
    { doc_id: "a", title: "A1", filename: "A.docx", source_index: 1 },
    { doc_id: "b", title: "B1", filename: "B.docx", source_index: 2 },
    { doc_id: "a", title: "A2", filename: "A.docx", source_index: 3 },
  ];
  const groups = groupSourcesByDocument(sources);
  assert.deepEqual(groups.map((group) => group.doc_id), ["a", "b"]);
  assert.deepEqual(groups[0].sources.map((source) => source.source_index), [1, 3]);
});

test("late event cannot rewrite a newer answer", () => {
  const messages = [
    { role: "assistant", attemptId: "old", text: "Stopped" },
    { role: "assistant", attemptId: "new", text: "New" },
  ];
  assert.deepEqual(applyAnswerEvent(messages, { attemptId: "old", type: "delta", text: "late" }), messages);
});

test("source refresh preserves manual collapse", () => {
  const messages = [{ role: "assistant", attemptId: "a", sourcesOpen: false, sourcesTouched: true, sources: [] }];
  const result = applyAnswerEvent(messages, { attemptId: "a", type: "sources", sources: [{ doc_id: "a" }] });
  assert.equal(result[0].sourcesOpen, false);
  assert.equal(result[0].sources.length, 1);
});

test("retrieval limit warning survives generation progress and source updates", () => {
  let messages = [{ role: "assistant", attemptId: "a", sources: [] }];
  messages = applyAnswerEvent(messages, {
    attemptId: "a", type: "progress", phase: "retrieval",
    search_depth: 100, search_limit_reached: true,
  });
  assert.equal(messages[0].searchLimitReached, true);
  assert.equal(messages[0].requestSearchDepth, 100);
  assert.equal(messages[0].progress, undefined);
  messages = applyAnswerEvent(messages, { attemptId: "a", type: "progress", phase: "generation" });
  messages = applyAnswerEvent(messages, { attemptId: "a", type: "sources", sources: [{ doc_id: "x" }] });
  assert.equal(messages[0].searchLimitReached, true);
});

test("a capped candidate search with eight fragments does not claim the 200-fragment limit was reached", () => {
  let messages = [{ role: "assistant", attemptId: "a", sources: [] }];
  messages = applyAnswerEvent(messages, {
    attemptId: "a", type: "progress", phase: "retrieval",
    search_depth: 200, search_limit_reached: true,
  });
  messages = applyAnswerEvent(messages, { attemptId: "a", type: "sources",
    sources: Array.from({ length: 8 }, (_, index) => ({ source_index: index + 1 })) });
  const message = messages[0];
  assert.equal(searchLimitWarning(message.searchLimitReached, message.sources.length, message.requestSearchDepth), null);
});

test("a warning names the result limit only when the displayed fragments fill it", () => {
  assert.equal(searchLimitWarning(true, 200, 200), "chat.searchLimitReached");
  assert.equal(searchLimitWarning(true, 500, 500), "chat.searchLimitReachedMax");
  assert.equal(searchLimitWarning(true, 8, 500), null);
  assert.equal(searchLimitWarning(true, 0, 200), null);
  assert.equal(searchLimitWarning(false, 8, 200), null);
  assert.equal(searchLimitWarning(false, 200, 200), null);
});
