import test from "node:test";
import assert from "node:assert/strict";

import { GET, POST } from "../src/app/api/[...path]/route.js";

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
    assert.equal(response.headers.get("cache-control"), "no-store");
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

test("API proxy forwards mutation bodies as a stream", async () => {
  const originalFetch = globalThis.fetch;
  const body = new TextEncoder().encode("payload");
  globalThis.fetch = async (_url, init) => {
    assert.equal(init.duplex, "half");
    assert.equal(init.body, requestBody);
    return new Response("ok");
  };
  const requestBody = new ReadableStream({
    start(controller) {
      controller.enqueue(body);
      controller.close();
    },
  });
  try {
    await POST(
      new Request("http://localhost:16301/api/documents", {
        method: "POST",
        body: requestBody,
        duplex: "half",
      }),
      { params: Promise.resolve({ path: ["documents"] }) },
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("API proxy preserves a streaming ZIP response without buffering", async () => {
  const originalFetch = globalThis.fetch;
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(new Uint8Array([0x50, 0x4b, 0x03, 0x04]));
      controller.close();
    },
  });
  globalThis.fetch = async () => new Response(stream, {
    headers: {
      "content-type": "application/zip",
      "content-length": "4",
      "content-disposition": "attachment; filename=part.zip",
      "cache-control": "no-store",
    },
  });
  try {
    const response = await GET(
      new Request("http://localhost:16301/api/jobs/1/export/1"),
      { params: Promise.resolve({ path: ["jobs", "1", "export", "1"] }) },
    );
    assert.equal(response.body, stream);
    assert.equal(response.headers.get("content-length"), "4");
    assert.equal(response.headers.get("content-disposition"), "attachment; filename=part.zip");
    assert.equal(response.headers.get("cache-control"), "no-store");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
