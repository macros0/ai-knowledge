import test from "node:test";
import assert from "node:assert/strict";
import { createReferenceMergeAdapter } from "../src/lib/referenceMergeLease.mjs";

const source = {resource_type: "glossary_term", resource_id: "1"};
const target = {...source, resource_id: "2"};
const status = epoch => ({state: "editing", lease_required: true, lease_epoch: epoch,
  server_now: "2026-10-18T00:00:00Z", expires_at: "2026-10-18T00:03:00Z", idle_expires_at: "2026-10-18T00:10:00Z"});

test("merge borrows the open source session and releases only the target", async () => {
  const acquired = [], released = [], renewed = [];
  const adapter = createReferenceMergeAdapter({
    getReferenceEditStatus: async () => ({state: "available", lease_required: true}),
    acquireReferenceEditLocks: async resources => {acquired.push(resources); return {resources: resources.map((resource, i) => ({...resource, ...status(i + 4)}))};},
    releaseReferenceEditLock: async (resource, body) => released.push({resource, body}),
    renewReferenceEditLock: async (resource, body) => {renewed.push({resource, body}); return status(5);},
  }, {source, target, getSourceSession: () => ({edit_token: "s".repeat(64), lease_epoch: 4})});
  await adapter.acquire({edit_token: "t".repeat(64)});
  assert.equal(acquired.length, 1);
  assert.equal(acquired[0][0].lease_epoch, 4);
  assert.equal(adapter.editSessions()[1].edit_token, "s".repeat(64));
  assert.equal(adapter.editSessions()[2].lease_epoch, 5);
  await adapter.renew({activity_sequence: 3});
  assert.equal(renewed.length, 1);
  assert.equal(renewed[0].resource.resource_id, "2");
  await adapter.release();
  assert.equal(released.length, 1);
  assert.equal(released[0].resource.resource_id, "2");
});

test("a refused acquire-many creates no releasable owned sessions", async () => {
  const released = [];
  const adapter = createReferenceMergeAdapter({getReferenceEditStatus: async () => ({lease_required: true}),
    acquireReferenceEditLocks: async () => {throw Object.assign(new Error("busy"), {code: "reference_edit_locked"});},
    releaseReferenceEditLock: async (...args) => released.push(args),
  }, {source, target});
  await assert.rejects(adapter.acquire({edit_token: "t".repeat(64)}));
  await adapter.release();
  assert.equal(released.length, 0);
});

test("malformed acquire-many aggregate releases acquired target but never borrowed source", async () => {
  const released = [];
  const adapter = createReferenceMergeAdapter({getReferenceEditStatus: async () => ({lease_required: true}),
    acquireReferenceEditLocks: async resources => ({resources: resources.map(resource => ({...resource,
      ...status(4), expires_at: "invalid"}))}),
    releaseReferenceEditLock: async (resource, body) => released.push({resource, body}),
  }, {source, target, getSourceSession: () => ({edit_token: "s".repeat(64), lease_epoch: 3})});
  await assert.rejects(adapter.acquire({edit_token: "t".repeat(64)}));
  assert.equal(released.length, 1);
  assert.equal(released[0].resource.resource_id, "2");
  await adapter.release();
  assert.equal(released.length, 1);
});
