import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {componentHarness} from './support/componentHarness.mjs';
import * as scope from '../src/lib/chatSearchScope.mjs';
import {createTranslator} from '../src/i18n/core.js';

const t = createTranslator('ru').t;
const sources = [
  {doc_id:'a', filename:'Old.docx', title:'Old fragment', selectable:false},
  {doc_id:'a', filename:'Old.docx', title:'Another fragment'},
  {doc_id:'b', filename:'Deleted.docx', title:'Deleted fragment'},
];
const items = [{doc_id:'a', filename:'Current.docx', available:true}, {doc_id:'b', filename:null, available:false}];
const tick = async () => {await new Promise(resolve => setImmediate(resolve));};

function setup() {
  const requests = [];
  const context = {searchScopeDocuments:[], searchScopeEnabled:false, sourceView:'documents', setSourceView:() => {}};
  context.setSearchScopeDocuments = value => {context.searchScopeDocuments = typeof value === 'function' ? value(context.searchScopeDocuments) : value;};
  context.setSearchScopeEnabled = value => {context.searchScopeEnabled = value;};
  const ChatSources = () => null;
  const props = {sources, open:false, onToggle:() => {}};
  const harness = componentHarness(new URL('../src/components/ChatHistorySources.jsx', import.meta.url), {
    '@/context/ChatContext':{useChat:() => context},
    '@/lib/chatSearchScope.mjs':scope,
    '@/lib/api':{friendlyApiError:error => error.message, getChatSearchScope:ids => new Promise((resolve,reject) => requests.push({ids:[...ids],resolve,reject}))},
    '@/i18n/LocaleContext':{useI18n:() => ({t})},
    './ChatSources':{__esModule:true, default:ChatSources},
    'next/link':{__esModule:true, default:({children,...rest}) => React.createElement('a',rest,children)},
  }, props, {RangeError});
  const draw = extra => harness.render({...props,...extra});
  const selection = () => harness.find(node => node.type === ChatSources).props.documentSelection;
  const button = key => harness.find(node => node.type === 'button' && node.props.children === t(key));
  return {harness,context,requests,draw,selection,button};
}

test('history batches unique documents on expansion and adds current available versions to shared scope', async () => {
  const h = setup();
  h.draw();
  assert.equal(h.requests.length,0);
  h.draw({open:true});
  assert.deepEqual(h.requests.map(request => request.ids),[['a','b']]);
  assert.equal(h.button('chat.scopeAddSelected'),undefined);
  h.requests[0].resolve(items);
  await tick(); h.draw({open:true});
  h.selection().onSelect(['a','b'],true);
  h.draw({open:true});
  assert.deepEqual([...h.selection().selectedIds],['a']);
  h.context.searchScopeDocuments = [{doc_id:'other',filename:'Other.docx'}];
  h.draw({open:true});
  h.button('chat.scopeAddSelected').props.onClick();
  assert.deepEqual(JSON.parse(JSON.stringify(h.context.searchScopeDocuments)),[
    {doc_id:'other',filename:'Other.docx'}, {doc_id:'a',filename:'Current.docx'},
  ]);
  assert.equal(h.context.searchScopeEnabled,true);
  h.draw({open:true});
  assert.ok(h.harness.find(node => node.props?.role === 'status' && node.props.children === t('chat.scopeAdded',{count:1})));
  assert.ok(h.harness.find(node => node.props?.href === '/chat'));
  h.harness.dispose();
});

test('availability errors permit retry and never mark documents as unavailable', async () => {
  const h = setup();
  h.draw({open:true}); h.requests[0].reject(new Error('Offline'));
  await tick(); h.draw({open:true});
  assert.equal(h.selection().documents.length,0);
  assert.equal(h.button('chat.scopeAddSelected'),undefined);
  assert.ok(h.harness.find(node => node.props?.role === 'alert'));
  h.button('chat.scopeRefresh').props.onClick(); h.draw({open:true});
  assert.equal(h.requests.length,2);
  h.requests[1].resolve(items); await tick(); h.draw({open:true});
  assert.equal(h.selection().documents.length,2);
  h.harness.dispose();
});

test('closing and reopening rechecks availability and ignores a late closed-list response', async () => {
  const h = setup();
  let tree = h.draw({open:true});
  tree.props.onToggle(false); h.draw({open:false});
  tree = h.draw({open:true});
  h.requests[1].resolve([{doc_id:'a',available:false},{doc_id:'b',available:false}]);
  await tick(); h.draw({open:true});
  h.requests[0].resolve(items); await tick(); h.draw({open:true});
  assert.ok(h.selection().documents.every(document => document.available === false));
  h.harness.dispose();
});

test('a changed source list cannot use availability from the previous answer', async () => {
  const h = setup();
  h.draw({open:true}); h.requests[0].resolve(items); await tick(); h.draw({open:true});
  h.selection().onSelect(['a'],true); h.draw({open:true});
  h.draw({open:true,sources:[{doc_id:'c',title:'New answer'}]});
  assert.equal(h.selection().documents.length,0);
  assert.equal(h.button('chat.scopeAddSelected'),undefined);
  assert.deepEqual(h.requests[1].ids,['c']);
  h.harness.dispose();
});

test('scope capacity errors keep the existing scope and show the limit', async () => {
  const h = setup();
  h.context.searchScopeDocuments = Array.from({length:500},(_,i) => ({doc_id:`existing-${i}`,filename:'Existing.docx'}));
  h.draw({open:true}); h.requests[0].resolve(items); await tick(); h.draw({open:true});
  h.selection().onSelect(['a'],true); h.draw({open:true});
  h.button('chat.scopeAddSelected').props.onClick(); h.draw({open:true});
  assert.equal(h.context.searchScopeDocuments.length,500);
  assert.equal(h.context.searchScopeEnabled,false);
  assert.ok(h.harness.find(node => node.props?.role === 'status' && node.props.children === t('chat.scopeLimit',{max:500})));
  h.harness.dispose();
});

test('administrative history stays read-only without checking or selecting documents', () => {
  const h = setup();
  h.draw({open:true,documentSelectionEnabled:false});
  assert.equal(h.requests.length,0);
  assert.equal(h.selection(),undefined);
  h.harness.dispose();
});
