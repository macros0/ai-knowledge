import test from "node:test";
import assert from "node:assert/strict";
import {createScopeLeaseAdapter, createReferenceLeaseAdapter} from "../src/lib/editorLeaseAdapters.mjs";
import {createEditorLeaseSession} from "../src/lib/editorLeaseSession.mjs";
import {clock} from "./support/clock.mjs";

const flush = async () => {for (let i=0;i<20;i++) await Promise.resolve();};
const status = (now, scope=false) => ({state:"editing", server_now:new Date(now).toISOString(),
  expires_at:new Date(Math.min(now+180000,600000)).toISOString(), idle_expires_at:new Date(600000).toISOString(),
  ...(scope?{}:{lease_epoch:3,reference_revision:1})});

test("scope transport preserves the document protocol without an invented epoch", async () => {
  const calls=[];
  const adapter=createScopeLeaseAdapter({getProcessingScope:async()=>({edit_lock:{state:"available"}}),
    acquireProcessingScopeEditLock:async(id,body)=>{calls.push([id,body]);return status(0,true);},
    renewProcessingScopeEditLock:async(id,body)=>{calls.push([id,body]);return status(1000,true);},
    releaseProcessingScopeEditLock:async(id,body)=>calls.push([id,body])}, "doc");
  const session=createEditorLeaseSession({...clock(),adapter,token:"a".repeat(64),onChange(){}});
  await session.start();
  assert.deepEqual(session.mutationContext(),{edit_token:"a".repeat(64)});
  await session.renewNow();session.dispose();await flush();
  assert.equal(calls.length,3);
  assert.deepEqual(calls.map(call=>Object.keys(call[1]).sort()),
    [["edit_token"],["activity_sequence","edit_token"],["edit_token"]]);
});

test("scope and reference share heartbeat and idle deadlines while keeping their release contexts", async () => {
  for(const scope of [false,true]){
    const time=clock(),renewals=[],releases=[];let expired=0;
    const api=scope?{
      acquireProcessingScopeEditLock:async()=>status(time.now(),true),
      renewProcessingScopeEditLock:async(_id,body)=>{renewals.push(body);return status(time.now(),true);},
      releaseProcessingScopeEditLock:async(_id,body)=>releases.push(body),
    }:{getReferenceEditStatus:async()=>({lease_required:true}),
      acquireReferenceEditLock:async()=>status(time.now()),
      renewReferenceEditLock:async(_resource,body)=>{renewals.push(body);return status(time.now());},
      releaseReferenceEditLock:async(_resource,body)=>releases.push(body)};
    const adapter=scope?createScopeLeaseAdapter(api,"doc"):createReferenceLeaseAdapter(api,{type:"tag",id:1});
    const session=createEditorLeaseSession({...time,adapter,token:"b".repeat(64),onChange(){},onExpire(){expired++;}});
    await session.start();
    for(let i=0;i<10;i++){time.advance(60000);await flush();}
    assert.equal(expired,1);assert.equal(session.snapshot().state,"lost");
    assert.equal(session.mutationContext(),null);assert.equal(releases.length,1);
    assert.ok(renewals.length>0);assert.ok(renewals.every(body=>body.activity_sequence===0));
    assert.equal("lease_epoch" in releases[0],!scope);
  }
});

test("server time controls scope expiry despite a client clock skew, including the compatible duration response", async () => {
  for(const withServerNow of [true,false]){
    const time=clock();time.advance(3600000);
    const response={...status(0,true),idle_remaining_seconds:600};
    if(!withServerNow)delete response.server_now;
    const adapter=createScopeLeaseAdapter({acquireProcessingScopeEditLock:async()=>response,
      releaseProcessingScopeEditLock:async()=>{}},"doc");
    const session=createEditorLeaseSession({...time,adapter,token:"f".repeat(64),onChange(){}});
    await session.start();assert.equal(session.snapshot().state,"editing");
    time.advance(179999);assert.ok(session.mutationContext());
    time.advance(1);assert.equal(session.mutationContext(),null);
    session.dispose();
  }
});
