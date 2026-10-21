import assert from 'node:assert/strict';
import test from 'node:test';
import {componentHarness} from './support/componentHarness.mjs';
import {createTranslator} from '../src/i18n/core.js';
import {reduceSearchScope} from '../src/lib/chatSearchScope.mjs';

const t = createTranslator('ru').t;

test('editing the saved scope preserves the search mode and lets the user remove or clear documents', () => {
  let scope = {documents:[{doc_id:'a',filename:'A.docx'},{doc_id:'b',filename:'B.docx'}], enabled:false};
  const Modal = () => null;
  const ChatSearchScope = () => null;
  const h = componentHarness(new URL('../src/components/ChatScopeSelector.jsx',import.meta.url), {
    '@/i18n/LocaleContext':{useI18n:() => ({t})},
    './Modal':{__esModule:true,default:Modal},
    './ChatSearchScope':{__esModule:true,default:ChatSearchScope},
  });
  const draw = () => h.render({...scope,onEnabledChange:value => {scope=reduceSearchScope(scope,{type:'enabled',value});},
    onRemove:id => {scope=reduceSearchScope(scope,{type:'documents',value:items=>items.filter(document=>document.doc_id!==id)});},
    onClear:() => {scope=reduceSearchScope(scope,{type:'documents',value:[]});}});
  draw();
  assert.equal(h.find(node=>node.type===Modal),undefined);
  h.find(node=>node.type==='select').props.onChange({target:{value:'edit'}});
  draw();
  assert.equal(scope.enabled,false);
  assert.equal(h.find(node=>node.type==='select').props.value,'all');
  assert.ok(h.find(node=>node.type===Modal));
  let editor = h.find(node=>node.type===ChatSearchScope);
  assert.equal(editor.props.expanded,true);
  editor.props.onRemove('a'); draw();
  editor = h.find(node=>node.type===ChatSearchScope);
  assert.deepEqual(editor.props.documents,[{doc_id:'b',filename:'B.docx'}]);
  h.find(node=>node.type===Modal).props.onClose(); draw();
  h.find(node=>node.type==='select').props.onChange({target:{value:'selected'}}); draw();
  assert.equal(scope.enabled,true);
  h.find(node=>node.type==='select').props.onChange({target:{value:'edit'}}); draw();
  h.find(node=>node.type===ChatSearchScope).props.onClear(); draw();
  assert.equal(scope.documents.length,0);
  assert.equal(scope.enabled,false,'Clearing the scope switches to the whole database');
  assert.equal(h.find(node=>node.type==='select').props.value,'all');
  assert.equal(h.find(node=>node.type==='option' && node.props.value==='edit').props.disabled,true);
  h.dispose();
});

test('scope editor exposes its full document list and removal actions without another disclosure', () => {
  const h = componentHarness(new URL('../src/components/ChatSearchScope.jsx',import.meta.url), {
    '@/i18n/LocaleContext':{useI18n:() => ({t})},
    '@/lib/api':{getChatSearchScope:async()=>[],friendlyApiError:()=>''},
    '@/lib/chatSources':{documentHref:document=>`/documents/${document.doc_id}/okf`},
    'next/link':{__esModule:true,default:()=>null},
    './ChatDisclosureSummary':{__esModule:true,default:()=>null},
  },{}, {window:{addEventListener:()=>{},removeEventListener:()=>{}}});
  const removed=[];let cleared=false;
  h.render({documents:[{doc_id:'a',filename:'A.docx'}],expanded:true,
    onRemove:id=>removed.push(id),onClear:()=>{cleared=true;}});
  assert.equal(h.find(node=>node.type==='details'),undefined);
  h.find(node=>node.type==='button' && node.props['aria-label']===t('chat.scopeRemoveDocument',{name:'A.docx'})).props.onClick();
  assert.deepEqual(removed,['a']);
  h.find(node=>node.type==='button' && node.props.children===t('chat.scopeClear')).props.onClick();
  assert.equal(cleared,true);
  h.dispose();
});
