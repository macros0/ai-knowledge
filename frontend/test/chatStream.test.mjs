import test from "node:test";
import assert from "node:assert/strict";
import { readChatStream } from "../src/lib/chatStream.mjs";

function response(parts) {
  const encoded = new TextEncoder().encode(parts.join(""));
  return { body: new ReadableStream({ start(c) {
    for (let i = 0; i < encoded.length; i += 3) c.enqueue(encoded.slice(i, i + 3));
    c.close();
  } }) };
}

test("UTF-8 deltas survive arbitrary network boundaries and final result wins", async () => {
  const deltas = [];
  const result = await readChatStream(response([
    '{"type":"ping"}\n', '{"type":"delta","text":"Привет"}\n',
    '{"type":"result","data":{"answer":"Привет [1].","sources":[]}}\n',
  ]), text => deltas.push(text));
  assert.deepEqual(deltas, ["Привет"]);
  assert.equal(result.answer, "Привет [1].");
});

test("closed connection cannot turn a partial answer into success", async () => {
  await assert.rejects(readChatStream(response(['{"type":"delta","text":"часть"}\n'])),
    /incomplete/i);
});

test("structured error preserves code and status for localized UI", async () => {
  await assert.rejects(readChatStream(response([
    '{"type":"error","code":"conflict","status":409}\n',
  ])), e => e.code === "conflict" && e.status === 409);
});

test("retrieved sources arrive before generation error", async () => {
  const seen = [];
  await assert.rejects(readChatStream(response([
    '{"type":"sources","sources":[{"title":"Нужный справочник","in_model_context":false}]}\n',
    '{"type":"error","code":"dependency_unavailable","status":503}\n',
  ]), () => {}, sources => seen.push(sources)), e => e.code === "dependency_unavailable");
  assert.deepEqual(seen, [[{ title: "Нужный справочник", in_model_context: false }]]);
});
