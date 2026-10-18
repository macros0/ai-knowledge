import test from "node:test";
import assert from "node:assert/strict";
import { dictionaryComparison, applyDictionaryComparison } from "../src/lib/referenceDictionary.mjs";
import { reconcileReferenceDraft } from "../src/lib/referenceEditing.mjs";

test("dictionary comparison preserves remote keys and local edits including plural objects", () => {
  const base = {name: "Old", plural: {one: "one", other: "many"}, removed: "old"};
  const local = {...base, name: "Mine", plural: {other: "many", one: "one"}};
  delete local.removed;
  const remote = {...base, added: "Colleague"};
  const comparison = dictionaryComparison(base, local, remote);
  assert.equal(comparison.fields.includes("plural"), false);
  const {merged, conflicts} = reconcileReferenceDraft(comparison);
  assert.deepEqual(conflicts, []);
  const result = applyDictionaryComparison(remote, merged);
  assert.deepEqual(result, {name: "Mine", plural: {one: "one", other: "many"}, added: "Colleague"});
  assert.equal(local.name, "Mine");
});

test("empty string and absent dictionary key remain distinct choices", () => {
  const comparison = dictionaryComparison({key: "before"}, {key: ""}, {});
  const {conflicts} = reconcileReferenceDraft(comparison);
  assert.equal(conflicts.length, 1);
  assert.deepEqual(applyDictionaryComparison({}, {key: conflicts[0].local}), {key: ""});
  assert.deepEqual(applyDictionaryComparison({}, {key: conflicts[0].remote}), {});
});
