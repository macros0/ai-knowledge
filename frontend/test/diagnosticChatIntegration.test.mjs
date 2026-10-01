import test from "node:test";
import assert from "node:assert/strict";
import contract from "../src/lib/diagnosticContract.json" with { type: "json" };
import { routeTemplate } from "../src/lib/diagnosticSchema.mjs";

test("merged chat codes and routes preserve safe diagnostic contract", () => {
  for (const code of ["chat_budget_unavailable", "chat_answer_truncated", "chat_evidence_invalid",
    "chat_sources_changed", "chat_summary_too_large", "chat_evidence_too_large",
    "chat_search_scope_empty", "chat_search_scope_unavailable"]) {
    assert.ok(contract.errorCodes.includes(code));
  }
  assert.equal(routeTemplate("/api/chat/search-scope"), "/api/chat/search-scope");
  assert.equal(routeTemplate("/api/chat/attempts/CANARY_PRIVATE/cancel"), "/api/chat/attempts/{attempt_id}/cancel");
  assert.equal(routeTemplate("/api/chat/attempts/CANARY_PRIVATE/cancel?secret=x"), "/unknown");
});
