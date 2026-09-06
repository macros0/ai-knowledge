import { test } from "node:test";
import assert from "node:assert/strict";
import { canActivate, canDisable } from "../src/lib/localeActions.mjs";

test("canActivate: draft и disabled дают кнопку активации", () => {
  assert.equal(canActivate("draft"), true);
  assert.equal(canActivate("disabled"), true);
  assert.equal(canActivate("active"), false);
  // Будущие/неизвестные статусы не должны открывать активацию.
  assert.equal(canActivate("error"), false);
  assert.equal(canActivate("archived"), false);
  assert.equal(canActivate("pending_review"), false);
  assert.equal(canActivate(null), false);
});

test("canDisable: только active и никогда ru", () => {
  assert.equal(canDisable("active", "en"), true);
  assert.equal(canDisable("active", "ru"), false); // fallback не отключается
  assert.equal(canDisable("draft", "en"), false);
  assert.equal(canDisable("disabled", "en"), false);
});
