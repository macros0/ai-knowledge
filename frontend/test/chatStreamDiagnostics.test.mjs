import test from "node:test";
import assert from "node:assert/strict";
import { readChatStream } from "../src/lib/chatStream.mjs";

const requestId = "01234567-89ab-4cde-8f01-23456789abcd";

test("sources and progress survive before a correlated stream error", async () => {
  const parts = [
    { type: "sources", sources: [{ source_index: 1 }] },
    { type: "progress", phase: "generation" },
    { type: "error", code: "internal_error", status: 503, request_id: requestId },
  ];
  const encoder = new TextEncoder();
  const response = { body: new ReadableStream({ start(controller) {
    for (const part of parts) controller.enqueue(encoder.encode(JSON.stringify(part) + "\n"));
    controller.close();
  } }) };
  const sources = [], progress = [];
  await assert.rejects(() => readChatStream(response, () => {}, value => sources.push(value), {
    onProgress: value => progress.push(value.phase),
  }), error => error.requestId === requestId && error.status === 503);
  assert.deepEqual(sources, [[{ source_index: 1 }]]);
  assert.deepEqual(progress, ["generation"]);
});
