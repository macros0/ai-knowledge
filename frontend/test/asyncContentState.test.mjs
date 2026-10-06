import test from "node:test";
import assert from "node:assert/strict";
import { resolveAsyncContentState } from "../src/lib/asyncContentState.mjs";
test("first request and first failure are distinct from successful emptiness", () => {
  assert.equal(resolveAsyncContentState({ pending:true }), "loading");
  assert.equal(resolveAsyncContentState({ error: new Error() }), "error");
  assert.equal(resolveAsyncContentState({ hasLoaded:true }), "empty");
  assert.equal(resolveAsyncContentState({ hasLoaded:true, hasFilters:true }), "filtered-empty");
});
test("refresh and refresh failure retain existing rows", () => {
  assert.equal(resolveAsyncContentState({ hasData:true, pending:true }), "refreshing");
  assert.equal(resolveAsyncContentState({ hasData:true, error: new Error() }), "stale-error");
  assert.equal(resolveAsyncContentState({ hasData:true, hasLoaded:true }), "ready");
});
