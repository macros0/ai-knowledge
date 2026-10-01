import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, bulkPreview, previewInterruptedDocuments } from "../src/lib/api.js";

test("generation preview rejects an old backend response instead of crashing the dialog", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    requested: 1, matched: 1, documents: [{ id: "a", status: "paused" }], missing: [],
  }), { status: 200 }));
  await assert.rejects(bulkPreview(["a"], "resume"), (err) => err instanceof ApiError && err.status === 404);
  // The existing delete preview remains compatible with older backends.
  assert.equal((await bulkPreview(["a"])).matched, 1);
});

test("interrupted recovery distinguishes an empty preview from a failed check and reads a fresh preview", async (t) => {
  const data = [
    { eligible_doc_ids: [], skipped: [], documents: [], max_docs: 1000 },
    { eligible_doc_ids: ["a"], skipped: [], documents: [{ id: "a" }], max_docs: 1000 },
  ];
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    if (calls > 2) return new Response(JSON.stringify({ detail: "unavailable" }), { status: 503 });
    return new Response(JSON.stringify(data[calls - 1]), { status: 200 });
  });
  assert.deepEqual((await previewInterruptedDocuments()).eligible_doc_ids, []);
  assert.deepEqual((await previewInterruptedDocuments()).eligible_doc_ids, ["a"]);
  await assert.rejects(previewInterruptedDocuments(), (error) => error instanceof ApiError && error.status === 503);
});

test("generation preview retains eligible IDs and skip reasons from the server", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    eligible_doc_ids: ["a"], skipped: [{ doc_id: "b", error_code: "not_resumable" }],
    max_docs: 20, documents: [{ id: "a", status: "paused" }, { id: "b", status: "done" }],
  }), { status: 200 }));
  const preview = await bulkPreview(["a", "b"], "resume");
  assert.deepEqual(preview.eligible_doc_ids, ["a"]);
  assert.deepEqual(preview.skipped, [{ doc_id: "b", error_code: "not_resumable" }]);
});
