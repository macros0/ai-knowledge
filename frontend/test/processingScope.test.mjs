import test from 'node:test';
import assert from 'node:assert/strict';
import {effectiveSelection,toggleSourceSelection,scopeBadgeModel,scopeTreeSources} from '../src/lib/processingScope.mjs';

test('badge total excludes unsupported entries from old and new summaries',()=>{
  assert.equal(scopeBadgeModel({excluded:2,total:32,unsupported:28,count_complete:true},null).total,4);
  assert.equal(scopeBadgeModel({counts_version:2,excluded:2,total:4,unsupported:28,count_complete:true},null).total,4);
});

const nodes=[{source_key:'mail',parent_key:'root',kind:'mail',supported:true,children_complete:true},
  {source_key:'archive',parent_key:'mail',kind:'archive',supported:true,children_complete:true},
  {source_key:'heavy',parent_key:'archive',kind:'xlsx',supported:true,children_complete:true},
  {source_key:'light',parent_key:'mail',kind:'xlsx',supported:true,children_complete:true}];

test('included mail body survives an excluded heavy descendant',()=>{
  assert.deepEqual([...effectiveSelection(nodes,new Set(['mail','archive','light']))],['mail','archive','light']);
  const chosen=toggleSourceSelection(nodes,new Set(['mail','archive','light']),'heavy',true);
  assert(chosen.has('heavy') && chosen.has('mail') && chosen.has('archive'));
  const removed=toggleSourceSelection(nodes,chosen,'archive',false);
  assert(removed.has('mail') && removed.has('light') && !removed.has('heavy'));
});

test('unknown subtrees cannot be silently selected',()=>{
  const unknown={source_key:'unknown',parent_key:'root',supported:true,assessment:'unknown',children_complete:false};
  assert.equal(effectiveSelection([unknown],new Set(['unknown'])).size,0);
});

test('active badge is distinct from pending selection',()=>{
  const model=scopeBadgeModel({excluded:2,total:5,count_complete:true},{state:'submitted',summary:{excluded:4}});
  assert.equal(model.excluded,2);
  assert(model.updating && model.showExcluded);
  assert.equal(scopeBadgeModel({excluded:0,total:2,count_complete:true},null).showExcluded,false);
  assert.equal(scopeBadgeModel(null,{state:'draft',requires_action:true}).pendingAction,true);
});

test('inventory keys reuse existing source tree relationships',()=>{
  const sources=scopeTreeSources(nodes);
  assert.equal(sources[2].parent_source_id,'archive');
  assert.equal(sources[0].parent_source_id,'root');
});
