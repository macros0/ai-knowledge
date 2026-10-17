import test from 'node:test';
import assert from 'node:assert/strict';
import {createScopeIdleTimer} from '../src/lib/processingScopeIdle.mjs';

function fakeClock(){
  let time=0,id=0;const jobs=new Map();
  return {now:()=>time,setTimer:(fn,delay)=>{jobs.set(++id,{fn,at:time+delay});return id;},clearTimer:n=>jobs.delete(n),
    advance(ms){const target=time+ms;let job;
      while((job=[...jobs].filter(([,j])=>j.at<=target).sort((a,b)=>a[1].at-b[1].at)[0])){
        time=job[1].at;jobs.delete(job[0]);job[1].fn();
      }time=target;},setNow(ms){time=ms;},flush(){for(const [id,j] of [...jobs]){jobs.delete(id);j.fn();}},pending:()=>jobs.size};
}

test('warn for the last minute and close once after ten idle minutes',()=>{
  const clock=fakeClock(),warnings=[];let closed=0;
  createScopeIdleTimer({...clock,onCountdown:n=>warnings.push(n),onExpire:()=>closed++});
  clock.advance(9*60000-1);assert.equal(closed,0);assert(!warnings.some(n=>n>0));
  clock.advance(1);assert.equal(warnings.at(-1),60);
  clock.advance(59000);assert.equal(warnings.at(-1),1);assert.equal(closed,0);
  clock.advance(1000);assert.equal(closed,1);clock.advance(60000);assert.equal(closed,1);
  assert.equal(clock.pending(),0);
});

test('real interaction resets warning, background heartbeat does not count as activity',()=>{
  const clock=fakeClock(),warnings=[],activity=[];let closed=0;
  const timer=createScopeIdleTimer({...clock,onCountdown:n=>warnings.push(n),onActivity:seq=>activity.push(seq),onExpire:()=>closed++});
  clock.advance(9*60000);timer.activity();assert.equal(warnings.at(-1),null);
  clock.advance(9*60000);assert.equal(closed,0);assert.equal(warnings.at(-1),60);
  clock.advance(60000);assert.equal(closed,1);assert.deepEqual(activity,[1]);
});

test('delayed background timer checks elapsed time; stopped session cannot close a new dialog',()=>{
  const clock=fakeClock();let closed=0;
  const timer=createScopeIdleTimer({...clock,onCountdown:()=>{},onExpire:()=>closed++});
  clock.setNow(12*60000);clock.flush();assert.equal(closed,1);
  const next=createScopeIdleTimer({...clock,onCountdown:()=>{},onExpire:()=>closed++});
  next.stop();clock.advance(20*60000);assert.equal(closed,1);assert.equal(clock.pending(),0);
  timer.activity();assert.equal(closed,1);
});

test('activity cannot resurrect an expired background session before its delayed timeout runs',()=>{
  const clock=fakeClock();let closed=0,activity=0;
  const timer=createScopeIdleTimer({...clock,onCountdown:()=>{},onExpire:()=>closed++,onActivity:()=>activity++});
  clock.setNow(12*60000);
  assert.equal(timer.activity(),false);
  assert.equal(closed,1);assert.equal(activity,0);assert.equal(clock.pending(),0);
});
