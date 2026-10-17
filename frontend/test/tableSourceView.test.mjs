import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {transformSync} from 'next/dist/build/swc/index.js';
import {sourceHref} from '../src/lib/chatSourceLinks.mjs';

const require = createRequire(import.meta.url);
const ref = {generation_id:'a'.repeat(32),source_id:'root/0',table_id:'b'.repeat(32),row_number:7,columns:[2,316]};

test('table source link pins the generation, row and original columns', () => {
  const link = sourceHref({doc_id:'doc',point_type:'concept',source_slug:'legacy',table_refs:[ref]});
  const parsed = new URL(link,'http://localhost');
  assert.equal(parsed.pathname, `/documents/doc/tables/${ref.table_id}`);
  assert.equal(parsed.searchParams.get('generation_id'), ref.generation_id);
  assert.equal(parsed.searchParams.get('row'), '7');
  assert.equal(parsed.searchParams.get('columns'), '2,316');
});

test('cell viewer renders literal HTML, original coordinates and honest ambiguity', () => {
  const file = new URL('../src/components/TableSourceView.jsx', import.meta.url);
  assert.ok(existsSync(file), 'table viewer is missing');
  const code = transformSync(readFileSync(file,'utf8'), {filename:'TableSourceView.jsx',jsc:{parser:{syntax:'ecmascript',jsx:true},target:'es2022',transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'}}).code;
  const compiled = {exports:{}};
  runInNewContext(code,{module:compiled,exports:compiled.exports,require(id) {
    if(id === '@/i18n/LocaleContext')return {useI18n:()=>({t:key=>key})};
    if(id === '@/lib/tableSourceStatus.mjs')return {tableStatusKey: q => q.header_status === 'ambiguous' ? 'tables.headerAmbiguous' : 'tables.coverageUnknown'};
    if(id === '@/lib/api')return {};
    return require(id);
  }});
  const initialData = {descriptor:{sheet_name:'Sheet',cell_range:{min_row:1,max_row:7,min_column:1,max_column:316},columns:[{index:2,letter:'B',header_path:['Code']}]},quality:{complete:true,header_status:'ambiguous',row_count:7,cell_count:10},rows:{columns:[{index:2,letter:'B',header_path:['Code']}],rows:[{row_number:7,cells:{2:{address:'B7',display_text:'<script>alert(1)</script>',state:'value'}}}],projection:true,continuation:[],next_column:3,next_row:null}};
  const html = renderToStaticMarkup(React.createElement(compiled.exports.default,{docId:'doc',tableRefs:[ref],initialData}));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('B7'));
  assert.ok(html.includes('A1:LD7'));
  assert.ok(html.includes('tables.headerAmbiguous'));
  assert.ok(html.includes('tables.projection'));
  assert.ok(html.includes('tables.moreColumns'));
  assert.ok(!html.includes('provider'));
});

test('source link includes every referenced column on the selected physical row', () => {
  const parsed = new URL(sourceHref({doc_id:'doc', table_refs:[{...ref,columns:[2]}, {...ref,columns:[316]}, {...ref,row_number:8,columns:[1]}]}),'http://localhost');
  assert.equal(parsed.searchParams.get('columns'), '2,316');
});

test('cell continuation link preserves exact text range',()=>{
 const parsed=new URL(sourceHref({doc_id:'doc',table_refs:[{...ref,columns:[316],text_start:20000,text_end:21500}]}),'http://localhost');
 assert.equal(parsed.searchParams.get('text_start'),'20000');
 assert.equal(parsed.searchParams.get('text_end'),'21500');
});


function compileTableView(file, overrides) {
 const code=transformSync(readFileSync(new URL(file,import.meta.url),'utf8'),{filename:file,jsc:{parser:{syntax:'ecmascript',jsx:true},target:'es2022',transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'}}).code;
 const mod={exports:{}};
 runInNewContext(code,{module:mod,exports:mod.exports,URLSearchParams,require:id=>id in overrides?overrides[id]:require(id)});
 return mod.exports.default;
}

const longRef={...ref,row_number:2,columns:[2],text_start:1000,text_end:5000};
const longDescriptor={descriptor:{sheet_name:'Sheet',cell_range:{min_row:1,max_row:3,min_column:1,max_column:2}},quality:{complete:true}};
function continuationRows(options) {
 // A2 is short; B2 is long; B3 is short. This is the API range contract.
 if(Number(options.text_start)>0 && (Number(options.limit)!==1 || String(options.columns)!=='2')) throw new Error('HTTP 416: offset outside neighbour');
 return {columns:[{index:2,letter:'B',header_path:['Text']}],rows:[{row_number:2,cells:{2:{address:'B2',display_text:'TARGET-TAIL',state:'value'}}}],projection:true,continuation:[],next_row:3,next_column:null};
}

test('continuation link does not apply one cell offset to other refs on that row',()=>{
 const url=new URL(sourceHref({doc_id:'doc',table_refs:[longRef,{...ref,row_number:2,columns:[1]}]}),'http://localhost');
 assert.equal(url.searchParams.get('columns'),'2');
});

test('server table page loads the continuation of one cell despite short neighbours',async()=>{
 const calls=[];
 const Page=compileTableView('../src/app/documents/[docId]/tables/[tableId]/page.js',{
  'next/link':({children})=>React.createElement('a',null,children),
  '@/components/TableSourceView':({initialData})=>React.createElement('div',null,initialData.rows.rows[0].cells['2'].display_text),
  '@/i18n/server':{serverTranslator:async()=>({t:key=>key})},
  '@/lib/backendFetch':{backendFetch:async path=>{
   const url=new URL(path,'http://localhost');calls.push(url);
   try{const data=url.pathname.endsWith('/rows')?continuationRows(Object.fromEntries(url.searchParams)):longDescriptor;return {ok:true,json:async()=>data};}
   catch{return {ok:false};}
  }},
 });
 const url=new URL(sourceHref({doc_id:'doc',table_refs:[longRef]}),'http://localhost');
 const tree=await Page({params:Promise.resolve({docId:'doc',tableId:ref.table_id}),searchParams:Promise.resolve(Object.fromEntries(url.searchParams))});
 assert.match(renderToStaticMarkup(tree),/TARGET-TAIL/);
 const query=calls.find(url=>url.pathname.endsWith('/rows')).searchParams;
 assert.equal(query.get('limit'),'1');assert.equal(query.get('columns'),'2');
 assert.equal(query.get('generation_id'),ref.generation_id);assert.equal(query.get('source_id'),ref.source_id);
});

test('client table viewer loads a continuation, then neighbouring rows without the offset',async()=>{
 const states=[],effects=[],calls=[];let cursor=0;
 const View=compileTableView('../src/components/TableSourceView.jsx',{
  'react':{...React,useState:initial=>{const i=cursor++;if(!(i in states))states[i]=initial;return [states[i],v=>{states[i]=typeof v==='function'?v(states[i]):v;}];},useEffect:fn=>effects.push(fn)},
  '@/i18n/LocaleContext':{useI18n:()=>({t:key=>key})},
  '@/lib/tableSourceStatus.mjs':{tableStatusKey:()=> 'tables.coverageVerified',mergeTablePages:(a,b)=>({...b,rows:[...a.rows,...b.rows]})},
  '@/lib/api':{getDocumentTable:async()=>longDescriptor,getDocumentTableRows:async(doc,table,gen,options)=>{calls.push({doc,table,gen,...options});return continuationRows(options);}},
 });
 function render(){cursor=0;return View({docId:'doc',tableRefs:[longRef]});}
 render();effects.splice(0).forEach(fn=>fn());await new Promise(resolve=>setImmediate(resolve));
 const tree=render();assert.match(renderToStaticMarkup(tree),/TARGET-TAIL/);
 assert.equal(calls[0].limit,1);assert.equal(calls[0].columns,'2');assert.equal(calls[0].gen,ref.generation_id);
 function findButton(node){if(!node||typeof node!=='object')return null;if(node.type==='button'&&node.props.children==='tables.moreRows')return node;return React.Children.toArray(node.props?.children).map(findButton).find(Boolean);}
 const more=findButton(tree);assert.ok(more);await more.props.onClick();
 assert.equal(calls[1].from_row,3);assert.equal(calls[1].text_start||0,0);
});
