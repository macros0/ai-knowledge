import test from "node:test";
import assert from "node:assert/strict";
import { readChatStream } from "../src/lib/chatStream.mjs";

test("progress and heartbeat keep a multi-pass stream alive", async () => {
  const encoder = new TextEncoder();
  const response = { body: new ReadableStream({
    async start(controller) {
      controller.enqueue(encoder.encode('{"type":"progress","phase":"generation"}\n'));
      await new Promise((resolve) => setTimeout(resolve, 15));
      controller.enqueue(encoder.encode('{"type":"ping"}\n'));
      await new Promise((resolve) => setTimeout(resolve, 15));
      controller.enqueue(encoder.encode('{"type":"result","data":{"answer":"ready"}}\n'));
      controller.close();
    },
  }) };
  const progress = [];
  const result = await readChatStream(response, () => {}, () => {}, { onProgress: (value) => progress.push(value.phase), idleTimeoutMs: 20 });
  assert.equal(result.answer, "ready");
  assert.deepEqual(progress, ["generation"]);
});

test("silent stream times out", async () => {
  const response = { body: new ReadableStream({ start() {} }) };
  await assert.rejects(() => readChatStream(response, () => {}, () => {}, { idleTimeoutMs: 10 }), /timeout/i);
});
