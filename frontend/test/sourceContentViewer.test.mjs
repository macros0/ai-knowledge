import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {transformSync} from 'next/dist/build/swc/index.js';
import * as sourceTree from '../src/lib/sourceTree.mjs';
const require=createRequire(import.meta.url);
test('full document initially renders its first chunk and retains every later chunk placeholder',()=>{
 const viewers=[];
 const compiled=transformSync(readFileSync(new URL('../src/components/SourceContentViewer.jsx',import.meta.url),'utf8'),{
 filename:'SourceContentViewer.jsx',jsc:{parser:{syntax:'ecmascript',jsx:true},target:'es2020',transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'},}).code;
 const mod={exports:{}};
 runInNewContext(compiled,{module:mod,exports:mod.exports,require(id){
  if(id==='@/lib/sourceTree.mjs')return sourceTree;
  if(id==='@/i18n/LocaleContext')return {useI18n:()=>({t:(key,p)=>key+JSON.stringify(p??{})})};
  if(id==='./MailMetadata')return ()=>null;
  if(id==='@/components/ContentViewer')return function Viewer(p){viewers.push(p);return React.createElement('p',null,p.text);};
  return require(id);
 }});
 const chunks=Array.from({length:111},(_,chunk_index)=>({chunk_index,content:`chunk ${chunk_index}`}));
 const html=renderToStaticMarkup(React.createElement(mod.exports.default,{docId:'doc',chunks,sources:[]}));
 assert.deepEqual(viewers.map(p=>p.text),['chunk 0']);
 assert.equal((html.match(/class="source-content-section"/g)||[]).length,111);
 assert.match(html,/data-chunk-index="110"/);
});
