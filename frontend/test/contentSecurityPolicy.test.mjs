import assert from "node:assert/strict";
import test from "node:test";

import { buildContentSecurityPolicy, createNonce } from "../src/lib/contentSecurityPolicy.mjs";

function directives(policy) {
  return Object.fromEntries(policy.split("; ").map((part) => {
    const [name, ...values] = part.split(" ");
    return [name, values];
  }));
}

test("scripts run only with this response's nonce", () => {
  const csp = directives(buildContentSecurityPolicy("abc123"));
  assert.deepEqual(csp["script-src"], ["'self'", "'nonce-abc123'", "'strict-dynamic'"]);
  assert.ok(!csp["script-src"].includes("'unsafe-inline'"));
  assert.ok(!csp["script-src"].includes("'unsafe-eval'"));
});

test("dev mode allows eval for React debugging only", () => {
  const csp = directives(buildContentSecurityPolicy("abc123", { dev: true }));
  assert.ok(csp["script-src"].includes("'unsafe-eval'"));
});

test("no external images, framing, plugins or base rewriting", () => {
  const csp = directives(buildContentSecurityPolicy("abc123"));
  assert.deepEqual(csp["img-src"], ["'self'", "blob:", "data:"]);
  assert.deepEqual(csp["connect-src"], ["'self'"]);
  assert.deepEqual(csp["frame-ancestors"], ["'none'"]);
  assert.deepEqual(csp["object-src"], ["'none'"]);
  assert.deepEqual(csp["base-uri"], ["'self'"]);
});

test("nonce is required and unique per request", () => {
  assert.throws(() => buildContentSecurityPolicy(""));
  const first = createNonce();
  assert.notEqual(first, createNonce());
  assert.match(first, /^[A-Za-z0-9+/=]{24,}$/);
});
