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
test("background status polling keeps the loaded list ready", () => {
  assert.equal(resolveAsyncContentState({ hasData:true, hasLoaded:true, pending:true, background:true }), "ready");
  assert.equal(resolveAsyncContentState({ pending:true, background:true }), "loading");
});
test("manual refresh and background failures remain visible", () => {
  assert.equal(resolveAsyncContentState({ hasData:true, hasLoaded:true, pending:true, background:false }), "refreshing");
  assert.equal(resolveAsyncContentState({ hasData:true, hasLoaded:true, background:true, error:new Error("poll failed") }), "stale-error");
});
