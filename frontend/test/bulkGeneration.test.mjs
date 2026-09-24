import assert from "node:assert/strict";
import test from "node:test";
import { ApiError, bulkPreview } from "../src/lib/api.js";

test("generation preview rejects an old backend response instead of crashing the dialog", async (t) => {
  t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({
    requested: 1, matched: 1, documents: [{ id: "a", status: "paused" }], missing: [],
  }), { status: 200 }));
  await assert.rejects(bulkPreview(["a"], "resume"), (err) => err instanceof ApiError && err.status === 404);
  // The existing delete preview remains compatible with older backends.
  assert.equal((await bulkPreview(["a"])).matched, 1);
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
