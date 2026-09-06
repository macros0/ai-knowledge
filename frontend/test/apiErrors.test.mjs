import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, friendlyApiError } from "../src/lib/api.js";

test("friendlyApiError: 401/403 — сессия/права", () => {
  for (const st of [401, 403]) {
    const msg = friendlyApiError(new ApiError("Forbidden", { status: st }));
    assert.ok(!/HTTP/.test(msg), `не сырой текст для ${st}`);
    assert.ok(msg.length > 8);
  }
});

test("friendlyApiError: 404 — раздел на старой версии backend", () => {
  const msg = friendlyApiError(new ApiError("Not Found", { status: 404 }));
  assert.match(msg, /backend/i);
});

test("friendlyApiError: status 0 (сеть) — недоступен", () => {
  const msg = friendlyApiError(new ApiError("timeout", { status: 0 }));
  assert.match(msg, /недоступен/i);
});

test("friendlyApiError: обычная строка-сообщение сохраняется", () => {
  const msg = friendlyApiError(new ApiError("Тег используется документами", { status: 409 }));
  assert.equal(msg, "Тег используется документами");
});

test("friendlyApiError: не-ApiError (TypeError/строка) не падает", () => {
  assert.ok(friendlyApiError(new TypeError("x")).length > 0);
  assert.ok(friendlyApiError(null).length > 0);
});
