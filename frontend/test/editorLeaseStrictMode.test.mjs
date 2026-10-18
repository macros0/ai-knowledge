import test from "node:test";
import assert from "node:assert/strict";
import {componentHarness} from "./support/componentHarness.mjs";
import {createEditorLeaseSession} from "../src/lib/editorLeaseSession.mjs";

test("effect cleanup and replay before the first microtask acquires only the surviving editor", async () => {
  let acquisitions = 0;
  const releases = [];
  const adapter = {acquire: async () => {
    acquisitions++;
    const now = Date.now();
    return {state: "editing", lease_epoch: acquisitions, server_now: new Date(now).toISOString(),
      expires_at: new Date(now + 180000).toISOString(), idle_expires_at: new Date(now + 600000).toISOString()};
  }, release: async body => releases.push(body)};
  const ui = componentHarness(new URL("../src/hooks/useEditorLeaseSession.js", import.meta.url), {
    "@/lib/editorLeaseSession.mjs": {createEditorLeaseSession},
  }, {adapter, enabled: true}, {document: {addEventListener() {}, removeEventListener() {}}}, {exportName: "useEditorLeaseSession"});
  try {
    ui.render(); ui.remount();
    for (let i = 0; i < 12; i++) await Promise.resolve();
    assert.equal(acquisitions, 1);
    const result = ui.render();
    assert.equal(result.state, "editing");
    assert.ok(result.getEditSession().edit_token);
    assert.equal(releases.length, 0);
  } finally {ui.dispose();}
  assert.equal(releases.length, 1);
});
