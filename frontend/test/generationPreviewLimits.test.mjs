import test from "node:test";
import assert from "node:assert/strict";
import { generationPreviewOverLimit } from "../src/lib/generationPreview.mjs";

test("generation limits use server eligible IDs and the configured max, not total selected", () => {
  assert.equal(generationPreviewOverLimit({ requested: 21, eligible_doc_ids: ["ready"], max_docs: 20 }), false);
  assert.equal(generationPreviewOverLimit({ requested: 21, eligible_doc_ids: Array.from({ length: 21 }, (_, i) => String(i)), max_docs: 30 }), false);
  assert.equal(generationPreviewOverLimit({ eligible_doc_ids: ["a", "b"], max_docs: 1 }), true);
  assert.equal(generationPreviewOverLimit(null), false);
});
