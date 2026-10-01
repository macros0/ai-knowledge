import test from "node:test";
import assert from "node:assert/strict";
import { runDocumentUploadBatch, createDocumentUploadRunner } from "../src/lib/documentUploadBatch.mjs";

const file = (name) => ({ name });
const conflict = (code) => Object.assign(new Error(code), { code, data: {} });

test("batch uploads sequentially and continues after a declined similar or exact duplicate", async () => {
  const calls = [], progress = [], uploaded = [];
  const result = await runDocumentUploadBatch([file("a.pdf"), file("b.pdf"), file("c.pdf"), file("skip.txt")], {
    uploadOne: async (item, allow) => {
      calls.push([item.name, allow]);
      if (item.name === "a.pdf") throw conflict("similar_document");
      if (item.name === "b.pdf") throw conflict("duplicate");
      return { id: "c" };
    },
    reviewUpload: async (review) => review.exact,
    onUploaded: (doc) => uploaded.push(doc.id),
    onProgress: (value) => progress.push(value),
  });
  assert.deepEqual(calls, [["a.pdf", false], ["b.pdf", false], ["c.pdf", false]]);
  assert.deepEqual(uploaded, ["c"]);
  assert.equal(result.uploadedCount, 1);
  assert.deepEqual(result.skipped, ["skip.txt"]);
  assert.deepEqual(progress.at(-1), { completed: 3, total: 3 });
});

test("storage full stops remaining files and retains the failure", async () => {
  const calls = [];
  const summary = await runDocumentUploadBatch([file("a.pdf"), file("b.pdf")], {
    uploadOne: async (item) => { calls.push(item.name); throw conflict("storage_full"); },
  });
  assert.deepEqual(calls, ["a.pdf"]);
  assert.equal(summary.storageFull, true);
  assert.equal(summary.failed[0].file, "a.pdf");
});

test("runner snapshots parameters, blocks overlap, and keeps waiting when the upload view closes", async () => {
  const params = { tags: ["original"], developmentId: 7, canonicalLocale: "ru" };
  let decide;
  const calls = [];
  const runner = createDocumentUploadRunner({
    uploadDocument: async (item, tags, options) => {
      calls.push([item.name, [...tags], { ...options }]);
      if (item.name === "a.pdf" && !options.allowSimilar) throw conflict("similar_document");
      return { id: item.name };
    },
    reviewUpload: () => new Promise((resolve) => { decide = resolve; }),
  });
  const pending = runner.start([file("a.pdf"), file("b.pdf")], params);
  await new Promise(setImmediate);
  params.tags.push("later"); params.developmentId = 9;
  assert.equal(await runner.start([file("extra.pdf")], params), null);
  // Only the view closes: the controller stays mounted and review is still pending.
  assert.equal(calls.length, 1);
  decide(true);
  assert.equal((await pending).uploadedCount, 2);
  assert.deepEqual(calls.map((call) => call[1]), [["original"], ["original"], ["original"]]);
  assert.equal(calls.at(-1)[2].developmentId, 7);
  assert.equal(calls.at(-1)[2].allowSimilar, false);
});

test("unmounting the page stops the remaining batch", async () => {
  let active = true;
  const calls = [];
  await runDocumentUploadBatch([file("a.pdf"), file("b.pdf")], {
    isActive: () => active,
    uploadOne: async (item) => { calls.push(item.name); active = false; return { id: "a" }; },
  });
  assert.deepEqual(calls, ["a.pdf"]);
});
