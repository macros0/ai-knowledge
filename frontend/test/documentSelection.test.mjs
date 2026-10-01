import test from "node:test";
import assert from "node:assert/strict";
import { selectionScopeKey, shouldApplySelectionResult } from "../src/lib/documentSelection.mjs";

test("selection scope changes with filters and immediate search input, not page/sort/group", () => {
  const base = { searchInput: "SAP", status: "done", uploader: "alice" };
  const key = selectionScopeKey(base);
  assert.equal(selectionScopeKey({ ...base, page: 2, sort: "name", group: "tag" }), key);
  for (const filter of ["searchInput", "status", "uploader", "tag", "development", "locale", "from", "to", "module", "problem"]) {
    assert.notEqual(selectionScopeKey({ ...base, [filter]: "changed" }), key, filter);
  }
  assert.equal(selectionScopeKey({ status: "done", searchInput: "SAP", uploader: "alice" }), key);
});

test("late selection responses cannot restore old filters or a manually cleared selection", () => {
  assert.equal(shouldApplySelectionResult("old", "new", 1, 1), false);
  assert.equal(shouldApplySelectionResult("same", "same", 1, 2), false);
  assert.equal(shouldApplySelectionResult("same", "same", 2, 2), true);
});
