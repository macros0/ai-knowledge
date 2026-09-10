import { test } from "node:test";
import assert from "node:assert/strict";
import { chat } from "../src/lib/api.js";

test("chat sends the selected UI locale as the response locale", async () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  let request;
  globalThis.window = {
    localStorage: { getItem: () => "ru" },
    navigator: { languages: ["en"] },
    document: { cookie: "" },
  };
  globalThis.fetch = async (_url, init) => {
    request = JSON.parse(init.body);
    return { ok: true, json: async () => ({}) };
  };

  try {
    await chat("ИТ 0003");
    assert.equal(request.locale, "ru");
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.window = originalWindow;
  }
});
