import test from "node:test";
import assert from "node:assert/strict";
import { uploadWithReview } from "../src/lib/uploadReview.mjs";
import { uploadDocument } from "../src/lib/api.js";

const conflict = (code = "similar_document") => Object.assign(new Error(code), {
  code, data: { duplicates: { level2: [{ doc: { id: "existing", filename: "original.docx" }, jaccard: 1 }], level3: [] } },
});

test("similar upload waits for a decision and sends consent only on confirmation", async () => {
  const calls = [];
  let decide;
  const done = uploadWithReview({ name: "revision.docx" }, async (allow) => {
    calls.push(allow);
    if (!allow) throw conflict();
    return { id: "accepted", has_duplicates: true };
  }, () => new Promise((resolve) => { decide = resolve; }));
  await new Promise(setImmediate);
  assert.deepEqual(calls, [false]);
  decide(true);
  assert.deepEqual(await done, { id: "accepted", has_duplicates: true });
  assert.deepEqual(calls, [false, true]);
});

test("cancel skips only the current file so the next file can upload", async () => {
  const results = [];
  const calls = [];
  for (const name of ["revision", "next"]) {
    results.push(await uploadWithReview({ name }, async (allow) => {
      calls.push([name, allow]);
      if (name === "revision") throw conflict();
      return { id: name };
    }, async () => false));
  }
  assert.deepEqual(results, [null, { id: "next" }]);
  assert.deepEqual(calls, [["revision", false], ["next", false]]);
});

test("identical files cannot be forced and unexpected errors stay errors", async () => {
  let calls = 0;
  const result = await uploadWithReview({ name: "same" }, async () => {
    calls += 1;
    throw conflict("duplicate");
  }, async (review) => { assert.equal(review.exact, true); return true; });
  assert.equal(result, null);
  assert.equal(calls, 1);
  await assert.rejects(uploadWithReview({}, async () => { throw new Error("offline"); },
    () => assert.fail("must not ask to override an error")), /offline/);
});

test("upload API sends explicit consent only for the confirmed request", async () => {
  const original = globalThis.fetch;
  const forms = [];
  globalThis.fetch = async (_url, init) => {
    forms.push(init.body);
    return new Response(JSON.stringify({ id: "new" }), { status: 200 });
  };
  try {
    await uploadDocument(new File(["bytes"], "revision.docx"));
    await uploadDocument(new File(["bytes"], "revision.docx"), ["tag"], {
      allowSimilar: true, developmentId: 7, canonicalLocale: "ru",
    });
    assert.equal(forms[0].get("allow_similar"), null);
    assert.equal(forms[1].get("allow_similar"), "true");
    assert.equal(forms[1].get("development_id"), "7");
    assert.equal(forms[1].get("canonical_locale"), "ru");
    assert.deepEqual(forms[1].getAll("tags"), ["tag"]);
  } finally { globalThis.fetch = original; }
});
