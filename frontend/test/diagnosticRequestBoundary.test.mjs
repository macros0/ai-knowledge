import test from "node:test";
import assert from "node:assert/strict";
import { AsyncLocalStorage } from "node:async_hooks";
import * as nodeModule from "node:module";
import { randomUUID } from "node:crypto";
import { currentRequestId } from "../src/lib/requestContext.mjs";
import { resolve } from "./diagnosticNextResolver.mjs";

// Next's bundler resolves extensionless entry points; Node's test runner needs
// the .js suffix. Use the real installed headers/cookies API and request stores.
if (typeof nodeModule.registerHooks === "function") {
  nodeModule.registerHooks({ resolve });
} else {
  nodeModule.register(new URL("./diagnosticNextResolver.mjs", import.meta.url), import.meta.url);
}
globalThis.AsyncLocalStorage = AsyncLocalStorage;
const { workAsyncStorage } = await import("next/dist/server/app-render/work-async-storage.external.js");
const { workUnitAsyncStorage } = await import("next/dist/server/app-render/work-unit-async-storage.external.js");
const { backendFetch } = await import("../src/lib/backendFetch.js");
const { proxy } = await import("../src/proxy.js");

test("SSR inherits header request ID without shared Proxy state and preserves cookies", async (t) => {
  const requestId = randomUUID();
  const previous = globalThis.fetch; t.after(() => { globalThis.fetch = previous; });
  globalThis.fetch = async (_url, init) => {
    assert.equal(init.headers.get("x-request-id"), requestId);
    assert.equal(currentRequestId(), requestId);
    assert.equal(init.headers.get("cookie"), "session=abc=+/; csrf_token=xyz");
    return new Response("ok");
  };
  assert.equal(currentRequestId(), null);
  const response = await workAsyncStorage.run({ route: "/", isStaticGeneration: false }, () => workUnitAsyncStorage.run({
    type: "request", phase: "render", headers: new Headers({ "x-request-id": requestId }),
    cookies: { getAll: () => [{ name: "session", value: "abc=+/" }, { name: "csrf_token", value: "xyz" }] },
  }, () => backendFetch("/api/auth/me")));
  assert.equal(await response.text(), "ok");
  assert.equal(currentRequestId(), null);
});

test("page Proxy replaces external correlation ID and preserves CSP nonce", () => {
  const external = randomUUID();
  const response = proxy({ headers: new Headers({ "x-request-id": external }) });
  const id = response.headers.get("x-request-id");
  assert.ok(id); assert.notEqual(id, external);
  assert.equal(response.headers.get("x-middleware-request-x-request-id"), id);
  const nonce = response.headers.get("x-middleware-request-x-nonce");
  assert.ok(nonce);
  assert.ok(response.headers.get("Content-Security-Policy").includes(`nonce-${nonce}`));
});
