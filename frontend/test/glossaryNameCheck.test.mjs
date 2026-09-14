import test from "node:test";
import assert from "node:assert/strict";
import * as ui from "../src/lib/glossaryUi.mjs";
import { checkGlossaryAliases } from "../src/lib/api.js";
import * as api from "../src/lib/api.js";

test("name preflight debounces and ignores a cancelled in-flight response", async (t) => {
  assert.equal(typeof ui.scheduleGlossaryCheck, "function");
  t.mock.timers.enable({apis: ["setTimeout"]});
  let resolve;
  const results = [];
  const request = {value: "PA30", kind: "sap_transaction", termId: 7};
  const cancel = ui.scheduleGlossaryCheck(() => new Promise((done) => { resolve = done; }), request, (result) => results.push(result));
  t.mock.timers.tick(349);
  assert.equal(resolve, undefined);
  t.mock.timers.tick(1);
  assert.equal(typeof resolve, "function");
  cancel();
  resolve({conflicts: [{term_id: 8}]});
  await Promise.resolve();
  assert.deepEqual(results, []);
});

test("full draft preflight sends all fields and optional edited owner to conflicts/check", async () => {
  assert.equal(typeof api.checkGlossaryConflicts, "function");
  const originalFetch = globalThis.fetch;
  let sent;
  globalThis.fetch = async (url, init) => {
    sent = {url, body: JSON.parse(init.body)};
    return new Response('{"conflicts":[],"redundant_forms":[]}', {headers: {"Content-Type": "application/json"}});
  };
  try {
    await api.checkGlossaryConflicts({kind: "sap_infotype", original_name: "Personnel", infotype_number: "0003", aliases: [{alias: "PA30"}]}, 7);
    assert.ok(sent.url.endsWith("/admin/glossary/conflicts/check"));
    assert.deepEqual(sent.body, {term_id: 7, kind: "sap_infotype", original_name: "Personnel", infotype_number: "0003", aliases: [{alias: "PA30"}]});
  } finally { globalThis.fetch = originalFetch; }
});

test("only the current name, owner and draft context can block a save", () => {
  assert.equal(typeof ui.currentGlossaryCheck, "function");
  const request = {value: "IT0003", termId: 7, kind: "sap_infotype", infotypeNumber: "0003", revision: 2};
  const result = {key: ui.glossaryCheckKey(request), conflicts: [{rule_id: 1}]};
  assert.equal(ui.currentGlossaryCheck(result, request), result);
  for (const patch of [{value: "PA30"}, {kind: "sap_transaction"}, {infotypeNumber: "0004"}, {termId: 8}, {revision: 3}]) {
    assert.equal(ui.currentGlossaryCheck(result, {...request, ...patch}), null);
  }
});

test("name check API forwards draft classification while legacy alias calls remain compatible", async () => {
  const originalFetch = globalThis.fetch;
  const payloads = [];
  globalThis.fetch = async (_url, init) => {
    payloads.push(JSON.parse(init.body));
    return new Response('{"conflicts":[]}', {headers: {"Content-Type": "application/json"}});
  };
  try {
    await checkGlossaryAliases(["IT0003"], 7, {kind: "sap_infotype", infotype_number: "0003"});
    await checkGlossaryAliases(["PA30"]);
    assert.deepEqual(payloads, [
      {aliases: ["IT0003"], term_id: 7, kind: "sap_infotype", infotype_number: "0003"},
      {aliases: ["PA30"], term_id: null},
    ]);
  } finally { globalThis.fetch = originalFetch; }
});
