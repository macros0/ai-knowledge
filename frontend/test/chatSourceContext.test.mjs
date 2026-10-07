import test from "node:test";
import assert from "node:assert/strict";
import { inModelContext } from "../src/lib/chatSourceContext.mjs";

test("coverage flag distinguishes model context from search-only sources", () => {
  assert.equal(inModelContext({ title: "cited", in_model_context: true }), true);
  assert.equal(inModelContext({ title: "search only", in_model_context: false }), false);
  assert.equal(inModelContext({ title: "historical source" }), true);
  assert.equal(inModelContext(null), false);
});
