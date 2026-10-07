import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {transformSync} from 'next/dist/build/swc/index.js';
import {splitLargeTables} from '../src/lib/largeTableSplit.mjs';
import {sourceSpansToLineRanges,splitRawSourceSpans} from '../src/lib/sourceHighlight.mjs';
const require=createRequire(import.meta.url);
const compiled=transformSync(readFileSync(new URL('../src/components/ContentViewer.jsx',import.meta.url),'utf8'),{
 filename:'ContentViewer.jsx',jsc:{parser:{syntax:'ecmascript',jsx:true},target:'es2020',transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'},
}).code;
function mount(props){
 const slots=[],viewers=[],effects=[];let cursor=0,tree;
 const hooks={...React,useMemo(fn){return fn();},useEffect(fn){effects.push(fn);},useState(initial){
  const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;
  return [slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;}];
 }};
 const mod={exports:{}};
 runInNewContext(compiled,{module:mod,exports:mod.exports,require(id){
  if(id==='react')return hooks;
  if(id==='@/i18n/LocaleContext')return {useI18n:()=>({t:(key,p)=>key+JSON.stringify(p??{}),tc:(key,count)=>key+count})};
  if(id==='@/lib/largeTableSplit')return {splitLargeTables};
  if(id==='@/lib/sourceHighlight.mjs')return {sourceSpansToLineRanges,splitRawSourceSpans};
  if(id==='./MarkdownViewer')return function Viewer(p){viewers.push(p);return React.createElement('p',null,p.text);};
  return require(id);
 }});
 function find(node,predicate){if(!node||typeof node!=='object')return null;if(predicate(node))return node;
  for(const child of React.Children.toArray(node.props?.children)){const found=find(child,predicate);if(found)return found;}return null;}
 return {render(){cursor=0;viewers.length=0;effects.length=0;tree=mod.exports.default(props);return renderToStaticMarkup(tree);},
 effects(){effects.splice(0).forEach(fn=>fn());},get viewers(){return viewers;},
 clickRemainder(){find(tree,n=>n.type==='button'&&n.props.children?.startsWith?.('content.hideRemainingRows') || n.props.children?.startsWith?.('content.showRemainingRows')).props.onClick();},
 toggleFirst(open){const details=find(tree,n=>n.type==='details');assert.ok(details);details.props.onToggle({currentTarget:{open}});},
 };
}
const header='| H | Value |',sep='| --- | --- |';
const rows=Array.from({length:700},(_,i)=>`| ${i} | evidence row ${i} |`);
const text=[header,sep,...rows].join('\n');
const quote='evidence row 650',start=text.indexOf(quote);
test('source evidence opens only its table segment, with other segments deferred',()=>{
 const view=mount({text,docId:'doc',sourceSpans:[{start,end:start+quote.length}]});
 view.render();view.effects();view.render();
 assert.equal(view.viewers.length,2);
 assert.ok(view.viewers.some(p=>p.text.includes(quote)));
 assert.ok(view.viewers.every(p=>!p.text.includes('evidence row 450')));
 view.toggleFirst(true);view.render();
 assert.equal(view.viewers.length,3);
 assert.ok(view.viewers.some(p=>p.text.includes('evidence row 450')));
});

test('automatically opened evidence can be hidden and reopened without expanding other rows',()=>{
 const view=mount({text,docId:'doc',sourceSpans:[{start,end:start+quote.length}]});
 view.render();assert.equal(view.viewers.length,2);
 view.clickRemainder();view.render();assert.equal(view.viewers.length,1);
 view.clickRemainder();view.render();assert.equal(view.viewers.length,2);
});
test('highlighting the repeated header does not expand every table segment',()=>{
 const view=mount({text,docId:'doc',sourceSpans:[{start:2,end:3}]});
 view.render();view.effects();view.render();assert.equal(view.viewers.length,1);
});
