import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {transformSync} from 'next/dist/build/swc/index.js';

const require=createRequire(import.meta.url);
const code=transformSync(readFileSync(new URL('../src/components/ChatTimeline.jsx',import.meta.url),'utf8'),{
 filename:'ChatTimeline.jsx',jsc:{parser:{syntax:'ecmascript',jsx:true},target:'es2020',transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'},
}).code;
const compiledModule={exports:{}};
runInNewContext(code+'\nmodule.exports.TimelineTurn = TimelineTurn;',{
 module:compiledModule,exports:compiledModule.exports,require(id){
  if(id==='react')return {...React,useRef:value=>({current:value}),useState:value=>[value,()=>{}],useEffect:()=>{},useLayoutEffect:()=>{}};
  if(id==='@/i18n/LocaleContext')return {useI18n:()=>({t:key=>key})};
  if(id==='next/link'||id==='./ChatRequestNavigation')return function Stub(){return null;};
  if(id==='./ChatHistoryShared')return {fmtDate:()=>'',HistoryMessage:()=>null};
  if(id.startsWith('@/lib/'))return {};
  return require(id);
 },
});
function preview(){
 const calls=[];
 const tree=compiledModule.exports.TimelineTurn({turn:{key:'turn',messages:[]},previewHeight:300,
  expanded:false,pending:false,onToggle:()=>calls.push('toggle'),onInteract:()=>calls.push('interact'),onExpand:()=>calls.push('expand')});
 const node=React.Children.toArray(tree.props.children).find(child=>child.props?.className?.includes('chat-turn-preview'));
 return {calls,node};
}
test('opening source fragments expands the surrounding dialog before layout grows',()=>{
 const {calls,node}=preview();
 node.props.onClickCapture({target:{closest:()=>({parentElement:{open:false,matches:()=>true}})}});
 assert.deepEqual(calls,['expand']);
});
test('closing a source group does not collapse or toggle the surrounding dialog',()=>{
 const {calls,node}=preview();
 node.props.onClickCapture({target:{closest:()=>({parentElement:{open:true,matches:()=>true}})}});
 assert.deepEqual(calls,['interact']);
});
test('ordinary content interaction retains existing reader behavior',()=>{
 const {calls,node}=preview();
 node.props.onClickCapture({target:{closest:()=>null}});
 assert.deepEqual(calls,['interact']);
});
