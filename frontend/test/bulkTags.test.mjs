import test from "node:test";
import assert from "node:assert/strict";
import { buildBulkTagChange } from "../src/lib/bulkTags.mjs";
import { bulkUpdateTags } from "../src/lib/api.js";

test("bulk tag change contains independent deltas and the tag language", () => {
  assert.deepEqual(buildBulkTagChange({ addTag: " new ", removeTag: "old", canonicalLocale: "ru" }), {
    add: ["new"], remove: ["old"], canonicalLocale: "ru",
  });
  assert.deepEqual(buildBulkTagChange({ removeTag: "old" }), { add: [], remove: ["old"], canonicalLocale: undefined });
  assert.equal(buildBulkTagChange({}), null);
  assert.equal(buildBulkTagChange({ addTag: "same", removeTag: " same " }), null);
});

test("one bulk tags request carries the ID snapshot and both deltas", async (t) => {
  const ids = ["a", "b"];
  const requests = [];
  t.mock.method(globalThis, "fetch", async (_url, init) => {
    requests.push(JSON.parse(init.body));
    return new Response(JSON.stringify({ updated: ids }), { status: 200 });
  });
  await bulkUpdateTags([...ids], buildBulkTagChange({ addTag: "new", removeTag: "old", canonicalLocale: "ru" }));
  assert.deepEqual(requests, [{ doc_ids: ["a", "b"], add: ["new"], remove: ["old"], canonical_locale: "ru" }]);
});
