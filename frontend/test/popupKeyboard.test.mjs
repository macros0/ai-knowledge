import test from "node:test";
import assert from "node:assert/strict";
import { consumePopupEscape } from "../src/lib/popupKeyboard.mjs";

test("Escape closes an open popup without reaching the surrounding dialog", () => {
  const calls = [];
  const event = { key: "Escape", preventDefault: () => calls.push("default"), stopPropagation: () => calls.push("propagation") };
  assert.equal(consumePopupEscape(event, true), true);
  assert.deepEqual(calls, ["default", "propagation"]);
  calls.length = 0;
  assert.equal(consumePopupEscape(event, false), false);
  assert.deepEqual(calls, []);
  assert.equal(consumePopupEscape({ key: "Enter" }, true), false);
});
