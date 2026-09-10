import test from "node:test";
import assert from "node:assert/strict";

import { GET } from "../src/app/api/[...path]/route.js";

test("API proxy preserves repeated Set-Cookie headers", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), "http://127.0.0.1:18001/api/auth/me?next=%2F");
    assert.equal(init.method, "GET");
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: [
        ["content-type", "application/json"],
        ["set-cookie", "csrf_token=abc; Path=/; SameSite=Lax"],
        ["set-cookie", "session=xyz; Path=/; HttpOnly"],
      ],
    });
  };
  const previousBackend = process.env.BACKEND_URL;
  process.env.BACKEND_URL = "http://127.0.0.1:18001";
  try {
    const response = await GET(
      new Request("http://localhost:16301/api/auth/me?next=%2F"),
      { params: Promise.resolve({ path: ["auth", "me"] }) },
    );
    assert.deepEqual(response.headers.getSetCookie(), [
      "csrf_token=abc; Path=/; SameSite=Lax",
      "session=xyz; Path=/; HttpOnly",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
    if (previousBackend === undefined) delete process.env.BACKEND_URL;
    else process.env.BACKEND_URL = previousBackend;
  }
});

test("API proxy does not trust client-controlled forwarding headers", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    for (const name of [
      "forwarded",
      "x-forwarded-for",
      "x-forwarded-host",
      "x-forwarded-port",
      "x-forwarded-proto",
    ]) {
      assert.equal(init.headers.get(name), null);
    }
    return new Response("ok");
  };
  try {
    await GET(
      new Request("http://localhost:16301/api/health", {
        headers: {
          Forwarded: "host=evil.example",
          "X-Forwarded-For": "203.0.113.9",
          "X-Forwarded-Host": "evil.example",
          "X-Forwarded-Port": "443",
          "X-Forwarded-Proto": "https",
        },
      }),
      { params: Promise.resolve({ path: ["health"] }) },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
