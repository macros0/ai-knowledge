import test from 'node:test';
import assert from 'node:assert/strict';
import {scopeChangeAction,scopeChoiceAvailable} from '../src/lib/processingScope.mjs';

test('fast exclusion, cache restore and delta use separate explicit actions',()=>{
  const capabilities={fast_exclusion:true,cached_restore:true,incremental_generation:true};
  assert.equal(scopeChangeAction({mode:'fast_exclude'},capabilities).endpoint,'apply-exclusions');
  assert.equal(scopeChangeAction({mode:'reuse_only'},capabilities).endpoint,'apply-changes');
  assert.equal(scopeChangeAction({mode:'incremental_generate'},capabilities).endpoint,'apply-changes');
  assert.equal(scopeChangeAction({mode:'noop'},capabilities).action,'noop');
  assert.equal(scopeChangeAction({mode:'noop'},capabilities).endpoint,'apply-exclusions');
});
test('unsupported capability and blocked plans never fall back to full regeneration',()=>{
  for(const mode of ['fast_exclude','reuse_only','incremental_generate','blocked']){
    const action=scopeChangeAction({mode},{});
    assert.equal(action.disabled,true);
    assert.equal(action.endpoint,null);
  }
});
test('scope button requires an actual choice or an existing exclusion',()=>{
  assert.equal(scopeChoiceAvailable('none',null,null),false);
  assert.equal(scopeChoiceAvailable('unknown',null,null),false);
  assert.equal(scopeChoiceAvailable('available',null,null),true);
  assert.equal(scopeChoiceAvailable(undefined,{excluded:2},null),true);
  assert.equal(scopeChoiceAvailable(undefined,{total:0,count_complete:true},null),false);
  assert.equal(scopeChoiceAvailable(undefined,{mode:'legacy_all',count_complete:false},null),true);
  assert.equal(scopeChoiceAvailable('none',{mode:'legacy_all',count_complete:false},null),false);
});
