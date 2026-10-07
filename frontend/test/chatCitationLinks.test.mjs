import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {runInNewContext} from 'node:vm';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {transformSync} from 'next/dist/build/swc/index.js';
import {inModelContext} from '../src/lib/chatSourceContext.mjs';
import * as links from '../src/lib/chatSourceLinks.mjs';

const require=createRequire(import.meta.url);
const mod={exports:{}};
const code=transformSync(readFileSync(new URL('../src/lib/chatSources.jsx',import.meta.url),'utf8'),{
 filename:'chatSources.jsx',jsc:{parser:{syntax:'ecmascript',jsx:true},target:'es2020',
 transform:{react:{runtime:'automatic'}}},module:{type:'commonjs'},
}).code;
runInNewContext(code,{module:mod,exports:mod.exports,require:id=>{
 if(id==='next/link')return function Link({children,...props}){return React.createElement('a',props,children);};
 if(id==='./chatSourceContext.mjs')return {inModelContext};
 if(id==='./chatSourceLinks.mjs')return links;
 return require(id);
}});
const CiteLink=mod.exports.CiteLink;
function render(sources,index){return renderToStaticMarkup(React.createElement(CiteLink,{sources,href:`#cite-${index}`},`[${index}]`));}
for(const inContext of [false,true,undefined])test(`known source citation navigates with context flag ${inContext}`,()=>{
 const source={doc_id:'doc',source_slug:'calculation',title:'Расчёт',in_model_context:inContext};
 const html=render([source],1);
 assert.ok(html.includes('href="/documents/doc/okf/calculation.md"'),html);
 assert.ok(html.includes('>[1]</a>'),html);
});
test('missing citation index remains text and is never mapped to another source',()=>{
 const html=render([{doc_id:'doc',source_slug:'calculation'}],41);
 assert.ok(!html.includes('href='));
 assert.ok(html.includes('[41]'));
});
test('known chunk citation opens its exact chunk even before coverage is confirmed',()=>{
 const html=render([{doc_id:'doc',point_type:'chunk',chunk_index:24,in_model_context:false}],1);
 assert.ok(html.includes('href="/documents/doc/chunks/24"'),html);
});
