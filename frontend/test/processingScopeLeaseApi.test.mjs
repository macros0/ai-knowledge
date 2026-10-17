import test from 'node:test';
import assert from 'node:assert/strict';
import {acquireProcessingScopeEditLock,renewProcessingScopeEditLock,releaseProcessingScopeEditLock} from '../src/lib/api.js';

for(const action of [acquireProcessingScopeEditLock,renewProcessingScopeEditLock,releaseProcessingScopeEditLock]){
  test(`${action.name} cannot hang beyond fifteen seconds`,async t=>{
    t.mock.timers.enable({apis:['setTimeout']});let signal;
    t.mock.method(globalThis,'fetch',(_url,options)=>{
      signal=options.signal;
      return new Promise((_resolve,reject)=>signal.addEventListener('abort',()=>reject(new DOMException('aborted','AbortError'))));
    });
    const pending=action('doc',{edit_token:'1'.repeat(64)}).catch(error=>error);
    t.mock.timers.tick(14999);assert.equal(signal.aborted,false);
    t.mock.timers.tick(1);assert.equal(signal.aborted,true);
    assert.equal((await pending).status,0);
  });
}
