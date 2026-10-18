import test from "node:test";
import assert from "node:assert/strict";
import { createEditorLeaseSession } from "../src/lib/editorLeaseSession.mjs";
import { clock } from "./support/clock.mjs";
import {referenceErrorLosesAccess} from "../src/lib/editorLeaseAdapters.mjs";

const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const status = (now = 0) => ({ state: "editing", lease_epoch: 2,
  server_now: new Date(now).toISOString(), expires_at: new Date(now + 180000).toISOString(),
  idle_expires_at: new Date(now + 600000).toISOString(), reference_revision: 1 });

test("acquired lease stays unwritable until fresh snapshot completes; slow refresh cannot extend expiry", async () => {
  const time = clock(), released = []; let ready;
  const session = createEditorLeaseSession({...time, token: "a".repeat(64), onChange() {},
    onAcquired: () => new Promise(resolve => {ready = resolve;}),
    adapter: {acquire: async () => status(), release: async body => released.push(body)}});
  const pending = session.start(); await flush();
  assert.equal(session.snapshot().state, "acquiring");
  assert.equal(session.mutationContext(), null);
  time.advance(180001); ready(); await pending;
  assert.equal(session.snapshot().state, "lost");
  assert.equal(session.mutationContext(), null);
  assert.equal(released.length, 1);
});

test("disposed snapshot refresh cannot update a newly opened editor", async () => {
  const time = clock(); let complete; let applied = false;
  const session = createEditorLeaseSession({...time, token: "a".repeat(64), onChange() {},
    onAcquired: async (_status, isCurrent) => {await new Promise(resolve => {complete = resolve;}); applied = isCurrent();},
    adapter: {acquire: async () => status(), release: async () => {}}});
  const pending = session.start(); await flush(); session.dispose(); complete(); await pending;
  assert.equal(applied, false);
  assert.equal(session.mutationContext(), null);
});

test("a write rejection immediately blocks saving while a content conflict retains the lease", async () => {
  assert.equal(referenceErrorLosesAccess({code: "version_conflict", status: 409}), false);
  assert.equal(referenceErrorLosesAccess({code: "reference_write_busy", status: 503}), false);
  assert.equal(referenceErrorLosesAccess({code: "reference_edit_lock_lost", status: 409}), true);
  assert.equal(referenceErrorLosesAccess({status: 403}), true);
  const session = createEditorLeaseSession({...clock(), token: "a".repeat(64), onChange() {},
    adapter: {acquire: async () => status(), release: async () => {}}});
  await session.start();
  session.reportError({code: "reference_edit_lock_lost"});
  assert.equal(session.snapshot().state, "lost");
  assert.equal(session.mutationContext(), null);
});

test("an invalid acquired status releases only that acquired generation", async () => {
  const released = [];
  const session = createEditorLeaseSession({token: "a".repeat(64), onChange() {},
    adapter: {acquire: async () => ({...status(), expires_at: "invalid"}), release: async value => released.push(value)}});
  await session.start();
  assert.equal(session.snapshot().state, "lost");
  assert.equal(released.length, 1);
  assert.equal(released[0].lease_epoch, 2);
});

test("session renews once per minute and idle expiry retains draft through callback", async () => {
  const time = clock(), renewals = [], states = [], released = [];
  let expired = 0;
  const session = createEditorLeaseSession({ ...time, token: "a".repeat(64),
    adapter: { acquire: async () => status(time.now()), renew: async body => {
      renewals.push(body); return { ...status(time.now()), idle_expires_at: new Date(600000).toISOString() }; },
      release: async body => released.push(body) },
    onChange: value => states.push(value), onExpire: () => expired++ });
  await session.start();
  assert.equal(session.snapshot().state, "editing");
  time.advance(60000); await flush();
  assert.equal(renewals.length, 1); assert.equal(renewals[0].activity_sequence, 0);
  for (let i = 0; i < 9; i++) { time.advance(60000); await flush(); }
  assert.equal(expired, 1); assert.equal(session.snapshot().state, "lost");
  assert.equal(released.length, 1); assert.equal(time.pending.size, 0);
  assert.equal(states.at(-1).state, "lost");
});

test("disposed pending acquire releases only its own session", async () => {
  const time = clock(), released = []; let complete;
  const session = createEditorLeaseSession({ ...time, token: "a".repeat(64),
    adapter: { acquire: () => new Promise(resolve => { complete = resolve; }), release: async body => released.push(body) },
    onChange() {} });
  const pending = session.start(); session.dispose(); complete(status()); await pending;
  assert.equal(released.length, 1); assert.equal(released[0].edit_token, "a".repeat(64));
  assert.equal(released[0].lease_epoch, 2); assert.equal(time.pending.size, 0);
});

test("failed renewal disables mutation context and does not acquire again", async () => {
  const time = clock(); let acquisitions = 0;
  const session = createEditorLeaseSession({ ...time, token: "a".repeat(64),
    adapter: { acquire: async () => { acquisitions++; return status(); }, renew: async () => { throw new Error("offline"); }, release: async () => {} },
    onChange() {} });
  await session.start(); time.advance(60000); await flush();
  assert.equal(session.snapshot().state, "lost"); assert.equal(session.mutationContext(), null);
  assert.equal(acquisitions, 1); session.dispose();
});
