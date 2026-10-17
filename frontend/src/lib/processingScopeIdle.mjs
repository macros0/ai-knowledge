export const SCOPE_EDITOR_IDLE_MS=10*60*1000;
export const SCOPE_EDITOR_WARNING_MS=60*1000;

/** Activity belongs to this editor opening; heartbeats never call activity(). */
export function createScopeIdleTimer({onCountdown,onExpire,onActivity=()=>{},
  now=Date.now,setTimer=setTimeout,clearTimer=clearTimeout,
  idleMs=SCOPE_EDITOR_IDLE_MS,warningMs=SCOPE_EDITOR_WARNING_MS}) {
  let deadline=now()+idleMs,timeout=null,closed=false,sequence=0;
  function update(){
    clearTimer(timeout);
    if(closed)return;
    const remaining=deadline-now();
    if(remaining<=0){closed=true;timeout=null;onCountdown(0);onExpire();return;}
    const warning=remaining<=warningMs;
    onCountdown(warning?Math.ceil(remaining/1000):null);
    timeout=setTimer(update,warning?Math.min(1000,remaining):remaining-warningMs);
  }
  update();
  return {
    activity(){
      if(closed)return false;
      if(now()>=deadline){update();return false;}
      deadline=now()+idleMs;onActivity(++sequence);update();return true;
    },
    stop(){closed=true;clearTimer(timeout);timeout=null;},
  };
}
