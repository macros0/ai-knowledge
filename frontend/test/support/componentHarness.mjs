import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {transformSync} from 'next/dist/build/swc/index.js';

const require=createRequire(import.meta.url);

// Exercise the component's real callbacks/effects with controlled network boundaries.
export function componentHarness(file,modules={},initialProps={},globals={}) {
  const slots=[],effects=[];
  let cursor=0,props=initialProps,tree;
  const hooks={...React,
    useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;
      return [slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];},
    useRef(initial){const i=cursor++;return slots[i]??={current:initial};},
    useId(){const i=cursor++;return `test-${i}`;},
    useMemo(fn,deps){const i=cursor++,previous=slots[i];
      if(!previous||!deps?.every((value,n)=>Object.is(value,previous.deps?.[n])))slots[i]={deps,value:fn()};
      return slots[i].value;},
    useCallback(fn,deps){return hooks.useMemo(()=>fn,deps);},
    useEffect(fn,deps){const i=cursor++,previous=slots[i];
      if(previous&&deps?.every((value,n)=>Object.is(value,previous.deps?.[n])))return;
      previous?.cleanup?.();slots[i]={deps};effects.push(()=>{slots[i].cleanup=fn();});},
  };
  const compiled=transformSync(readFileSync(file,'utf8'),{
    filename:String(file),jsc:{parser:{syntax:'ecmascript',jsx:true},target:'es2020',
      transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'},}).code;
  const mod={exports:{}};
  runInNewContext(compiled,{module:mod,exports:mod.exports,AbortController,crypto:globalThis.crypto,
    setInterval,clearInterval,setTimeout,clearTimeout,
    ...globals,
    require(id){return id==='react'?hooks:Object.hasOwn(modules,id)?modules[id]:require(id);}});
  function render(nextProps=props){props=nextProps;cursor=0;tree=mod.exports.default(props);
    while(effects.length)effects.shift()();return tree;}
  function walk(predicate,node){if(!node)return undefined;
    if(Array.isArray(node)){for(const child of node){const result=walk(predicate,child);if(result)return result;}return;}
    if(typeof node!=='object')return;
    if(predicate(node))return node;
    return walk(predicate,node.props?.children)||walk(predicate,node.props?.footer)||walk(predicate,node.props?.sessionNotice);
  }
  function dispose(){for(const slot of slots)slot?.cleanup?.();}
  return {render,find:predicate=>walk(predicate,tree),dispose,
    remount(nextProps=props){dispose();slots.length=0;effects.length=0;return render(nextProps);}};
}
