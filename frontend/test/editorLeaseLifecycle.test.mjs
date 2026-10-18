import test from "node:test";
import assert from "node:assert/strict";
import {createEditorLeaseSession} from "../src/lib/editorLeaseSession.mjs";
import {createScopeLeaseAdapter} from "../src/lib/editorLeaseAdapters.mjs";
import {clock} from "./support/clock.mjs";

const flush=async()=>{for(let i=0;i<15;i++)await Promise.resolve();};
const live=(time)=>({state:"editing",server_now:new Date(time).toISOString(),
  expires_at:new Date(time+180000).toISOString(),idle_expires_at:new Date(600000).toISOString()});

test("late scope acquire releases only its token and cannot refresh a replacement dialog",async()=>{
  let resolveAcquire,refreshed=0;const releases=[];
  const adapter=createScopeLeaseAdapter({acquireProcessingScopeEditLock:()=>new Promise(resolve=>{resolveAcquire=resolve;}),
    releaseProcessingScopeEditLock:async(_id,body)=>releases.push(body)},"doc");
  const session=createEditorLeaseSession({...clock(),adapter,token:"c".repeat(64),onChange(){},onAcquired(){refreshed++;}});
  const pending=session.start();session.dispose();resolveAcquire(live(0));await pending;
  assert.equal(refreshed,0);assert.equal(session.mutationContext(),null);
  assert.deepEqual(releases,[{edit_token:"c".repeat(64)}]);
});

test("an applying document exposes no mutation context and never sends a cancellation",async()=>{
  let cancelled=0;
  const adapter=createScopeLeaseAdapter({acquireProcessingScopeEditLock:async()=>({state:"applying"}),
    releaseProcessingScopeEditLock:async()=>{cancelled++;}},"doc");
  const session=createEditorLeaseSession({...clock(),adapter,token:"d".repeat(64),onChange(){}});
  await session.start();assert.equal(session.snapshot().state,"applying");
  assert.equal(session.mutationContext(),null);session.dispose();await flush();
  assert.equal(cancelled,0);
});

test("late scope renewal cannot restore a disposed editor or run its expiration callback",async()=>{
  const time=clock();let complete,closed=0;
  const adapter=createScopeLeaseAdapter({acquireProcessingScopeEditLock:async()=>live(0),
    renewProcessingScopeEditLock:()=>new Promise(resolve=>{complete=resolve;}),
    releaseProcessingScopeEditLock:async()=>{}},"doc");
  const session=createEditorLeaseSession({...time,adapter,token:"e".repeat(64),onChange(){},onExpire(){closed++;}});
  await session.start();time.advance(60000);await flush();session.dispose();
  complete(live(60000));await flush();time.advance(900000);
  assert.equal(session.mutationContext(),null);assert.equal(closed,0);assert.equal(time.pending.size,0);
});

test("a slow fresh snapshot cannot postpone the authoritative idle close",async()=>{
  const time=clock();let ready,closed=0;
  const response={...live(0),expires_at:new Date(60000).toISOString(),idle_expires_at:new Date(60000).toISOString()};
  const adapter=createScopeLeaseAdapter({acquireProcessingScopeEditLock:async()=>response,
    releaseProcessingScopeEditLock:async()=>{}},"doc");
  const session=createEditorLeaseSession({...time,adapter,token:"f".repeat(64),onChange(){},
    onAcquired:()=>new Promise(resolve=>{ready=resolve;}),onExpire(){closed++;}});
  const pending=session.start();await flush();time.advance(61000);ready();await pending;
  assert.equal(closed,1);assert.equal(session.mutationContext(),null);session.dispose();
});
