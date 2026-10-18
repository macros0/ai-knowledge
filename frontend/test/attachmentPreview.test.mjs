import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync,readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {transformSync} from 'next/dist/build/swc/index.js';
import {setImmediate} from 'node:timers/promises';
import {componentHarness} from './support/componentHarness.mjs';
import * as processingScope from '../src/lib/processingScope.mjs';
const require=createRequire(import.meta.url);

test('preview renders literal cell text, coordinates and an explicit limited notice',()=>{
  const file=new URL('../src/components/AttachmentPreviewDialog.jsx',import.meta.url);
  assert(existsSync(file),'attachment preview component exists');
  const compiled=transformSync(readFileSync(file,'utf8'),{
    filename:'AttachmentPreviewDialog.jsx',jsc:{parser:{syntax:'ecmascript',jsx:true},target:'es2020',
      transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'},}).code;
  const mod={exports:{}};
  runInNewContext(compiled,{module:mod,exports:mod.exports,require(id){
    if(id==='@/lib/api')return {};
    if(id==='@/lib/processingScope.mjs')return processingScope;
    if(id==='@/i18n/LocaleContext')return {useI18n:()=>({t:key=>key})};
    if(id==='./Modal')return ()=>null;
    return require(id);
  }});
  const html=renderToStaticMarkup(React.createElement(mod.exports.AttachmentPreviewTable,{
    data:{columns:['A','B'],rows:[{number:9,cells:['<script>alert(1)</script>','=1+2']}],limited:true},
    t:key=>key,
  }));
  assert(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));
  assert(!html.includes('<script>'));
  assert(html.includes('=1+2'));
  assert(html.includes('scope.previewLimited'));
  assert(html.includes('scope.previewTable'));
  assert.match(html,/>9<\/th>/);
  const text=renderToStaticMarkup(React.createElement(mod.exports.AttachmentPreviewContent,{
    data:{kind:'text',text:'<script>literal</script>',metadata:{subject:'<img src=x>'},limited:false},t:key=>key}));
  assert(text.includes('&lt;script&gt;'));assert(!text.includes('<script>'));assert(!text.includes('<img'));
});

const t=key=>key;
for(const kind of ['text','archive'])test(`non-Excel ${kind} preview has no sheet controls or Excel-only advice`,async()=>{
  const data=kind==='text'?{kind,text:'<script>literal</script>',metadata:{subject:'Subject'},limited:false}:
    {kind,entries:[{name:'folder/file.docx',size:123}],limited:false};
  const mounted=previewDialog(async()=>data,{node:{source_key:'generic-'+kind,kind:kind==='text'?'docx':'archive',display_name:'object.bin'}});
  mounted.render();await setImmediate();mounted.render();
  assert(!mounted.find(n=>n.type==='select'));
  assert(!mounted.find(n=>n.props?.children==='scope.previewRawValues'));
  assert(mounted.find(n=>n.props?.data?.kind===kind));
  mounted.dispose();
});

const previewData=sheet=>({sheet_index:sheet,sheets:[0,1,2].map(index=>({index,name:`Sheet ${index}`})),
  columns:['A'],rows:[{number:1,cells:[`sheet ${sheet}`]}],limited:false});

test('preview does not repeat the discovered title, technical filename or format',async()=>{
  const mounted=previewDialog(async()=>({...previewData(0),title:'Actual workbook title',title_source:'properties'}),
    {node:{source_key:'title-heading',kind:'xlsx',display_name:'_____Microsoft_Excel.xlsx'}});
  mounted.render();await setImmediate();mounted.render();
  assert(!mounted.find(n=>n.type==='p'&&n.props.className==='attachment-preview-name'));
  assert(!mounted.find(n=>n.type==='p'&&n.props.className==='source-location-note'&&Array.isArray(n.props.children)&&n.props.children.some(child=>child==='_____Microsoft_Excel.xlsx'||child==='XLSX')));
  assert(mounted.find(n=>n.type==='select'),'Sheet selection remains available');
  mounted.dispose();
});

test('preview does not show a duplicate name block while loading',()=>{
  const mounted=previewDialog(async()=>previewData(0),{node:{source_key:'known-heading',kind:'xlsx',
    display_name:'object.xlsx',title:'Inventory title',title_source:'properties'}});
  mounted.render();
  assert(!mounted.find(n=>n.type==='p'&&n.props.className==='attachment-preview-name'));
  mounted.dispose();
});
function previewDialog(load,props={}){
  return componentHarness(new URL('../src/components/AttachmentPreviewDialog.jsx',import.meta.url),{
    '@/lib/api':{getAttachmentPreview:load,friendlyApiError:err=>err.message},
    '@/lib/processingScope.mjs':processingScope,
    '@/i18n/LocaleContext':{useI18n:()=>({t})},'./Modal':()=>null,
  },{docId:'doc',reviewId:'review',node:{source_key:'excel',display_name:'table.xlsx'},
    included:false,onClose:()=>{},...props});
}

test('preview always explains raw Excel values even when the sample is complete',()=>{
  const mounted=previewDialog(async()=>previewData(0));mounted.render();
  assert(mounted.find(n=>n.props?.children==='scope.previewRawValues'));
  mounted.dispose();
});

test('sheet switching coalesces pending work and never shows a stale sheet',async()=>{
  const calls=[];const mounted=previewDialog((_doc,_key,_review,sheet)=>new Promise((resolve)=>calls.push({sheet,resolve})));
  mounted.render();await setImmediate();assert.equal(calls.length,1);
  calls[0].resolve(previewData(0));await setImmediate();mounted.render();
  mounted.find(n=>n.type==='select').props.onChange({target:{value:'1'}});mounted.render();await setImmediate();
  assert.equal(calls.length,2);
  mounted.find(n=>n.type==='select').props.onChange({target:{value:'2'}});mounted.render();await setImmediate();
  assert.equal(calls.length,2,'the old server worker must finish before another starts');
  calls[1].resolve(previewData(1));await setImmediate();mounted.render();await setImmediate();
  assert.equal(calls.length,3);assert.equal(calls[2].sheet,2);
  assert(!mounted.find(n=>n.props?.data?.sheet_index===1));
  calls[2].resolve(previewData(2));await setImmediate();mounted.render();
  assert.equal(mounted.find(n=>n.props?.data).props.data.sheet_index,2);
  mounted.dispose();
});

test('sheet loading keeps the preview viewport mounted without displaying the previous sheet',async()=>{
  const calls=[];let closes=0;
  const mounted=previewDialog((_doc,_key,_review,sheet)=>new Promise(resolve=>calls.push({sheet,resolve})),
    {onClose:()=>{closes++;}});
  mounted.render();await setImmediate();
  calls[0].resolve(previewData(0));await setImmediate();mounted.render();
  const viewport=()=>mounted.find(n=>n.props?.className==='attachment-preview-content');
  assert(viewport(),'the preview needs a persistent viewport to avoid collapsing to the underlying dialog');
  assert.equal(viewport().props['aria-busy'],false);
  mounted.find(n=>n.type==='select').props.onChange({target:{value:'1'}});mounted.render();await setImmediate();
  assert.equal(viewport().props['aria-busy'],true);
  assert(mounted.find(n=>n.props?.role==='status'&&n.props.children==='scope.previewLoading'));
  assert(!mounted.find(n=>n.props?.data),'old sheet content must not appear under the new sheet name');
  assert.equal(closes,0);
  calls[1].resolve(previewData(1));await setImmediate();mounted.render();
  assert.equal(viewport().props['aria-busy'],false);
  assert.equal(mounted.find(n=>n.props?.data).props.data.sheet_index,1);
  mounted.dispose();
});

test('close and reopen joins the same running preview instead of starting a second worker',async()=>{
  const calls=[];const mounted=previewDialog(()=>new Promise(resolve=>calls.push(resolve)));
  mounted.render();await setImmediate();mounted.remount();await setImmediate();
  assert.equal(calls.length,1);
  calls[0](previewData(0));await setImmediate();mounted.render();
  assert.equal(mounted.find(n=>n.props?.data).props.data.sheet_index,0);
  mounted.dispose();
});

test('409 offers review refresh and does not retry the obsolete review',async()=>{
  let calls=0,refreshes=0;
  const mounted=previewDialog(async()=>{calls++;throw Object.assign(new Error('Changed review'),{status:409});},
    {onReviewChanged:()=>{refreshes++;}});
  mounted.render();await setImmediate();mounted.render();
  const button=mounted.find(n=>n.type==='button'&&n.props.children==='scope.previewRefresh');
  assert(button);button.props.onClick();assert.equal(refreshes,1);assert.equal(calls,1);
  mounted.dispose();
});

test('changing original while a request finishes uses the new source and review',async()=>{
  const calls=[];const mounted=previewDialog((doc,key,review,sheet)=>new Promise(resolve=>calls.push({doc,key,review,sheet,resolve})));
  mounted.render();await setImmediate();
  mounted.render({docId:'doc',reviewId:'new-review',node:{source_key:'new-excel',display_name:'new.xlsx'},onClose:()=>{}});
  await setImmediate();assert.equal(calls.length,1);
  calls[0].resolve(previewData(0));await setImmediate();
  assert.equal(calls[1].key,'new-excel');assert.equal(calls[1].review,'new-review');
  calls[1].resolve(previewData(0));await setImmediate();mounted.dispose();
});

for(const status of [429,422])test(`preview can retry HTTP ${status} without an automatic request loop`,async()=>{
  let calls=0;
  const mounted=previewDialog(async()=>{if(++calls===1)throw Object.assign(new Error('Try again'),{status});return previewData(0);});
  mounted.render();await setImmediate();mounted.render();
  assert.equal(calls,1);
  mounted.find(n=>n.type==='button'&&n.props.children==='scope.previewRetry').props.onClick();mounted.render();
  await setImmediate();mounted.render();
  assert.equal(calls,2);assert.equal(mounted.find(n=>n.props?.data).props.data.sheet_index,0);
  mounted.dispose();
});
