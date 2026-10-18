import test from "node:test";
import assert from "node:assert/strict";
import { reconcileReferenceDraft, chunkReferenceCards } from "../src/lib/referenceEditing.mjs";

const fields = ["number", "name", "module"];
const base = { number: "100", name: "Original", module: "PY", version: 1 };

test("development conflict preserves remote name and local number", () => {
  const result = reconcileReferenceDraft({ base, local: { ...base, number: "101" },
    remote: { ...base, name: "Colleague", version: 2 }, fields });
  assert.deepEqual(result.merged, { number: "101", name: "Colleague", module: "PY" });
  assert.deepEqual(result.conflicts, []);
  assert.equal(base.version, 1);
});

test("same field conflict requires an explicit choice", () => {
  const result = reconcileReferenceDraft({ base, local: { ...base, name: "Local" },
    remote: { ...base, name: "Remote", version: 2 }, fields });
  assert.deepEqual(result.conflicts, [{ field: "name", base: "Original", local: "Local", remote: "Remote" }]);
  assert.equal(result.merged.name, "Local");
  assert.equal(Object.hasOwn(result.merged, "version"), false);
});

test("identical edits do not need a conflict decision", () => {
  const result = reconcileReferenceDraft({ base, local: { ...base, name: "Same" },
    remote: { ...base, name: "Same", version: 2 }, fields });
  assert.equal(result.merged.name, "Same");
  assert.deepEqual(result.conflicts, []);
});

test("repeated conflict never upgrades the old draft version", () => {
  const local = { ...base, name: "Local" };
  reconcileReferenceDraft({ base, local, remote: { ...base, name: "Remote", version: 2 }, fields });
  const repeated = reconcileReferenceDraft({ base, local, remote: { ...base, name: "Newer", version: 3 }, fields });
  assert.equal(local.version, 1);
  assert.equal(repeated.conflicts[0].remote, "Newer");
});

test("empty module and an absent module mean the same editable value", () => {
  const result = reconcileReferenceDraft({ base: { ...base, module: null }, local: { ...base, module: "" },
    remote: { ...base, module: "PT" }, fields });
  assert.equal(result.merged.module, "PT");
  assert.deepEqual(result.conflicts, []);
});

test("batching preserves every locale of each card and every selected card", () => {
  const rows = Array.from({ length: 103 }, (_, index) => ({ tag_id: index + 1, locale: "en" }));
  rows.push({ tag_id: 1, locale: "de" }, { tag_id: 103, locale: "fr" });
  const batches = chunkReferenceCards(rows);
  assert.deepEqual(batches.map(batch => new Set(batch.map(item => item.tag_id)).size), [100, 3]);
  assert.deepEqual(batches[0].filter(item => item.tag_id === 1).map(item => item.locale), ["en", "de"]);
  assert.equal(batches.flat().length, rows.length);
});
