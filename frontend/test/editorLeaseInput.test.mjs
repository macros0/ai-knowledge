import test from "node:test";
import assert from "node:assert/strict";
import {createEditorLeaseSession} from "../src/lib/editorLeaseSession.mjs";
import {clock} from "./support/clock.mjs";

async function setup(idleRemaining=600000) {
  const time=clock(), notifications=[];
  const status={state:"editing",lease_epoch:1,server_now:new Date(0).toISOString(),
    expires_at:new Date(180000).toISOString(),idle_expires_at:new Date(idleRemaining).toISOString()};
  const session=createEditorLeaseSession({...time,token:"a".repeat(64),onChange:value=>notifications.push(value),
    adapter:{acquire:async()=>status,renew:()=>new Promise(()=>{}),release:async()=>{}}});
  await session.start();notifications.length=0;
  return {time,session,notifications};
}

test("trusted input does not synchronously publish React state during the input capture phase",async()=>{
  const {time,session,notifications}=await setup();
  session.activity({isTrusted:true});
  assert.equal(notifications.length,0,"capture must let the controlled input finish before notifying React");
  time.advance(0);
  assert.equal(notifications.length,1);
  assert.equal(notifications[0].state,"editing");
  session.dispose();
});

test("typing dismisses the warning after the input event and still renews the idle deadline",async()=>{
  const {time,session,notifications}=await setup(60000);
  assert.equal(session.snapshot().countdown,60);
  session.activity({isTrusted:true});
  assert.equal(session.snapshot().countdown,null);
  assert.equal(notifications.length,0);
  time.advance(0);
  assert.equal(notifications[0].countdown,null);
  time.advance(60000);
  assert.equal(session.snapshot().state,"editing");
  session.dispose();
});

test("closing an editor cancels the deferred activity notification",async()=>{
  const {time,session,notifications}=await setup();
  session.activity({isTrusted:true});session.dispose();time.advance(0);
  assert.equal(notifications.length,0);
  assert.equal(time.pending.size,0);
});
