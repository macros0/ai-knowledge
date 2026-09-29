import test from "node:test";
import assert from "node:assert/strict";
import { groupSourcesByDocument, applyAnswerEvent } from "../src/lib/chatAnswerState.mjs";

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
