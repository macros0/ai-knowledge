import test from "node:test";
import assert from "node:assert/strict";
import { groupSourcesByDocument, applyAnswerEvent, searchLimitWarning } from "../src/lib/chatAnswerState.mjs";
import * as answerState from "../src/lib/chatAnswerState.mjs";

test("rating view preserves citations and stable ties without mutating source evidence", () => {
  assert.equal(typeof answerState.sourcesForView, "function");
  const sources = Object.freeze([
    Object.freeze({ doc_id: "a", title: "First", source_index: 1, score: 0.2 }),
    Object.freeze({ doc_id: "b", title: "Best", source_index: 2, score: 1 }),
    Object.freeze({ doc_id: "a", title: "Equal", source_index: 3, score: 1 }),
    Object.freeze({ doc_id: "c", title: "Legacy", score: 0.5 }),
    Object.freeze({ doc_id: "d", title: "Missing", score: null }),
  ]);
  const ranked = answerState.sourcesForView(sources, "rating");
  assert.deepEqual(ranked.map(s => s.title), ["Best", "Equal", "Legacy", "First", "Missing"]);
  assert.deepEqual(ranked.map(s => s.display_index), [2, 3, 4, 1, 5]);
  assert.equal(ranked[2].source_index, undefined);
  assert.deepEqual(answerState.sourcesForView(sources, "documents").map(s => s.title), ["First", "Best", "Equal", "Legacy", "Missing"]);
  assert.equal(sources[0].display_index, undefined);
});

test("source view stored in the answer survives streaming and manual collapse", () => {
  assert.equal(typeof answerState.changeChatSourceView, "function");
  const selection = [2];
  const messages = [{ role: "assistant", attemptId: "a", selectedSourceIndexes: selection,
    sourceGroupsOpen: { doc: false }, sourcesOpen: true, sources: [{ source_index: 2, score: 1 }] }];
  const ranked = answerState.changeChatSourceView(messages, 0, "rating");
  const streamed = applyAnswerEvent(ranked, { attemptId: "a", type: "delta", text: "Answer [2]" });
  assert.equal(streamed[0].sourceView, "rating");
  assert.equal(streamed[0].selectedSourceIndexes, selection);
  assert.deepEqual(streamed[0].sourceGroupsOpen, { doc: false });
  assert.equal(answerState.changeChatSourceView(ranked, 0, "rating"), ranked);
  assert.equal(answerState.changeChatSourceView(ranked, 0, "invalid"), ranked);
  assert.equal(answerState.changeChatSourceView(ranked, 9, "documents"), ranked);
});

test("explicit document view stays pinned when the chat preference changes elsewhere", () => {
  const messages = [{ role: "assistant", attemptId: "a", sources: [{ source_index: 1 }] }];
  const grouped = answerState.changeChatSourceView(messages, 0, "documents");
  assert.equal(grouped[0].sourceView, "documents");
  assert.equal(answerState.changeChatSourceView(grouped, 0, "documents"), grouped);
});

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

test("grouped sources preserve all 500 stable citation indexes including search-only",()=>{
 const sources=Array.from({length:500},(_,i)=>({doc_id:`doc-${i%8}`,source_index:i+1,in_model_context:i<40}));
 const groups=groupSourcesByDocument(sources);
 assert.equal(groups.length,8);
 assert.equal(groups.reduce((n,g)=>n+g.sources.length,0),500);
 assert.deepEqual(groups.flatMap(g=>g.sources.map(s=>s.source_index)).sort((a,b)=>a-b),Array.from({length:500},(_,i)=>i+1));
 assert.equal(groups.flatMap(g=>g.sources).filter(s=>!s.in_model_context).length,460);
});
