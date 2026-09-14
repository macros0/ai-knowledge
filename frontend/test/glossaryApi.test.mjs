import test from "node:test";
import assert from "node:assert/strict";

import {
  commitGlossaryMerge,
  createGlossaryRule,
  previewGlossaryMerge,
  updateGlossaryRule,
  previewGlossaryRuleMerge,
  commitGlossaryRuleMerge,
} from "../src/lib/api.js";

test("glossary JSON mutations declare their request content type", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await createGlossaryRule({ name: "IT", number_from: 0, number_to: 9999, prefixes: ["IT"] });
    await updateGlossaryRule(1, { version: 1, name: "IT" });
    await previewGlossaryMerge({ request_id: "a".repeat(36), source_term_id: 1, target_term_id: 2 });
    await commitGlossaryMerge({ request_id: "b".repeat(36), source_term_id: 1, target_term_id: 2 });
    await previewGlossaryRuleMerge({ draft: {name: "New"}, target_rule_id: 1, target_version: 1 });
    await commitGlossaryRuleMerge({ draft: {name: "New"}, target_rule_id: 1, target_version: 1, preview_digest: "digest", expected_revision: 2 });

    assert.equal(calls.length, 6);
    assert.ok(calls[4].url.endsWith("/admin/glossary/rules/merge/preview"));
    assert.ok(calls[5].url.endsWith("/admin/glossary/rules/merge"));
    for (const { init } of calls) {
      assert.equal(new Headers(init.headers).get("content-type"), "application/json");
      assert.equal(typeof init.body, "string");
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});
