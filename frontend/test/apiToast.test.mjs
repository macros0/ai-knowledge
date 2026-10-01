import test from "node:test";
import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { ApiError } from "../src/lib/api.js";
import { apiToast } from "../src/lib/apiToast.mjs";

test("toast metadata follows its own failed request across overlapping calls", () => {
  const first = randomUUID(), second = randomUUID();
  const translate = (key) => ({ "apiError.internal_error": "A safe message" })[key] || key;
  const left = apiToast(new ApiError("CANARY", { status: 500, requestId: first }), translate, { type: "error" });
  const right = apiToast(new ApiError("CANARY", { status: 500, requestId: second }), translate, { type: "error" });
  assert.equal(left.message, "A safe message");
  assert.equal(left.options.requestId, first);
  assert.equal(right.options.requestId, second);
  assert.ok(!JSON.stringify([left, right]).includes("CANARY"));
});

test("network toast labels a local reference while preserving actions", () => {
  const action = { label: "retry", onClick: () => {} };
  const payload = apiToast(new ApiError("CANARY", { status: 0 }), (key) => key, { action });
  assert.equal(payload.options.requestId, null);
  assert.match(payload.options.localReportId, /^[a-f0-9-]{36}$/);
  assert.equal(payload.options.action, action);
});
