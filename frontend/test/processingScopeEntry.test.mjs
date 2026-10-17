import test from 'node:test';
import assert from 'node:assert/strict';
import {componentHarness} from './support/componentHarness.mjs';
import * as scope from '../src/lib/processingScope.mjs';

const Badge=()=>null;
const Dialog=()=>null;
function entry(props){
  return componentHarness(new URL('../src/components/DocumentProcessingScopeButton.jsx',import.meta.url),{
    '@/i18n/LocaleContext':{useI18n:()=>({t:key=>key})},
    '@/lib/processingScope.mjs':scope,
    './ProcessingScopeBadge':Badge,'./DocumentProcessingScopeDialog':Dialog,
  },{docId:'doc',...props});
}

for(const props of [
  {summary:{excluded:1,total:3,count_complete:true}},
  {pending:{requires_action:true}},
  {summary:{excluded:1,total:3},pending:{requires_action:true}},
])test(`single list entry uses the interactive badge: ${JSON.stringify(props)}`,()=>{
  const badge=entry({...props,badge:true});badge.render();
  const control=badge.find(node=>node.type===Badge);
  assert(control);
  assert(!badge.find(node=>node.type==='button'),'Do not render a second trigger alongside the badge');
  control.props.onOpen();badge.render();
  assert(badge.find(node=>node.type===Dialog),'The badge itself opens the editor');
  badge.dispose();
});

for(const props of [
  {availability:'available',summary:{excluded:0,total:3,count_complete:true}},
  {summary:{mode:'legacy_all',count_complete:false}},
])test(`documents without a badge keep a button in the same entry: ${JSON.stringify(props)}`,()=>{
  const fallback=entry({...props,badge:true});fallback.render();
  const button=fallback.find(node=>node.type==='button');
  assert(button);button.props.onClick();fallback.render();
  assert(fallback.find(node=>node.type===Dialog));fallback.dispose();
});

test('standalone viewer/upload entry remains available even when a list badge would be shown',()=>{
  const standalone=entry({pending:{requires_action:true}});standalone.render();
  assert(standalone.find(node=>node.type==='button'));standalone.dispose();
  const none=entry({availability:'none',badge:true});assert.equal(none.render(),null);none.dispose();
});

test('changing the entry from button to badge preserves the open editor instance',()=>{
  const props={docId:'doc',badge:true,availability:'available',summary:{excluded:0,total:3}};
  const mounted=entry(props);mounted.render();
  const button=mounted.find(node=>node.type==='button');assert(button);
  button.props.onClick();mounted.render();
  assert(mounted.find(node=>node.type===Dialog));
  mounted.render({...props,summary:{excluded:1,total:3,count_complete:true}});
  assert(mounted.find(node=>node.type===Badge));
  assert(!mounted.find(node=>node.type==='button'));
  assert(mounted.find(node=>node.type===Dialog),'Keep the editor when its trigger changes appearance');
  mounted.dispose();
});
