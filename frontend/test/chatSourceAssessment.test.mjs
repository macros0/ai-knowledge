import test from 'node:test';
import assert from 'node:assert/strict';
import {assessmentView, readAssessmentPreference, writeAssessmentPreference, applyAssessmentOutcome, historyAssessmentMessage, withoutAssessmentOptions} from '../src/lib/chatSourceAssessment.mjs';
import {applyAnswerEvent} from '../src/lib/chatAnswerState.mjs';
import {retryRequestOptions} from '../src/lib/chatComposer.mjs';

test('preference keeps explicit off and survives failed storage',()=>{
  const storage={getItem:()=> 'false',setItem:()=>{throw Error('blocked');}};
  assert.equal(readAssessmentPreference(storage,true),false);
  assert.equal(readAssessmentPreference({getItem:()=>null},true),true);
  assert.equal(readAssessmentPreference({getItem:()=>{throw Error();}},false),false);
  assert.doesNotThrow(()=>writeAssessmentPreference(storage,true));
});
test('assessment view distinguishes unavailable and partial from rejection',()=>{
  assert.equal(assessmentView({status:'completed',decision:'reject'}),'chat.assessment.reject');
  assert.equal(assessmentView({status:'completed',decision:'uncertain'}),'chat.assessment.uncertain');
  assert.equal(assessmentView({status:'unavailable'}),'chat.assessment.unavailable');
  assert.equal(assessmentView({status:'disabled'}),null);
  assert.equal(assessmentView(undefined),null);
});
test('reject collapses initially and manual opening survives late result',()=>{
  const outcome={status:'completed',decision:'reject'};
  const message={role:'assistant',sourcesOpen:true};
  assert.equal(applyAssessmentOutcome(message,outcome).sourcesOpen,false);
  assert.equal(applyAssessmentOutcome({...message,sourcesTouched:true},outcome).sourcesOpen,true);
});
test('late assessment for a different attempt cannot replace current state',()=>{
  const items=[{role:'assistant',attemptId:'new',sourcesOpen:true}];
  const event={type:'progress',phase:'source_assessment',attemptId:'old',source_assessment:{status:'completed',decision:'reject'}};
  assert.equal(applyAnswerEvent(items,event),items);
  const updated=applyAnswerEvent(items,{...event,attemptId:'new'});
  assert.equal(updated[0].sourceAssessment.decision,'reject');
  const sources=applyAnswerEvent(updated,{type:'sources',attemptId:'new',sources:[{doc_id:'a'}]});
  assert.equal(sources[0].sourcesOpen,false);
});
test('retry carries explicit off while old history defers to current preference',()=>{
  assert.equal(retryRequestOptions({requestAssessSources:false}).requestAssessSources,false);
  assert.equal(retryRequestOptions({}).requestAssessSources,undefined);
});
test('continue without assessment preserves the entire original request',()=>{
  const message={requestAssessSources:true,requestUseGlossary:false,requestMailMode:'exclude',requestTags:['HR'],requestDocIds:['a'],requestSearchDepth:200,responseMode:'full'};
  const options=withoutAssessmentOptions(message);
  assert.equal(options.requestAssessSources,false);
  assert.deepEqual(options.requestDocIds,['a']);
  assert.deepEqual(options.requestTags,['HR']);
  assert.equal(options.requestUseGlossary,false);
  assert.equal(options.requestMailMode,'exclude');
});
test('history projection keeps saved outcome and replay request unchanged',()=>{
  const m={role:'assistant',content:'answer',retrieval_metadata:{source_assessment:{status:'completed',decision:'reject'},source_assessment_replay:{requested_enabled:false},chat_request:{assess_sources:false,mail_mode:'only',tags:['X'],response_mode:'full',search_depth:100,use_glossary:false},answer_attempt:{id:'old',query:'Question',mode:'full',status:'completed'}}};
  const result=historyAssessmentMessage(m,'session');
  assert.equal(result.requestAssessSources,false);
  assert.equal(result.sourceAssessment.decision,'reject');
  assert.equal(result.requestMailMode,'only');
  assert.equal(result.requestSessionId,'session');
});

test('editing a selected-source question performs fresh retrieval with preserved assessment choice',async()=>{
  const {editAssessmentOptions}=await import('../src/lib/chatSourceAssessment.mjs');
  const message={query:'old',requestAssessSources:false,requestSourceSelection:{attempt_id:'old',indexes:[2]},requestTags:['HR'],requestDocIds:['a']};
  const actual=editAssessmentOptions(message);
  assert.equal(actual.requestSourceSelection,undefined);
  assert.equal(actual.requestAssessSources,false);
  assert.deepEqual(actual.requestDocIds,['a']);
});
test('selected-source replay targets the original message session',async()=>{
  const {selectedAssessmentOptions}=await import('../src/lib/chatSourceAssessment.mjs');
  const actual=selectedAssessmentOptions({attemptId:'old',requestSessionId:'session-a',selectedSourceIndexes:[2],requestAssessSources:false});
  assert.equal(actual.requestSessionId,'session-a');
  assert.deepEqual(actual.requestSourceSelection,{attempt_id:'old',indexes:[2]});
  assert.equal(actual.requestAssessSources,false);
});


test('retrieved sources stay open and intact while assessment is running',()=>{
  const sources=[{source_index:1,doc_id:'a',title:'Source A',selectable:true}];
  let state=[{role:'assistant',attemptId:'attempt',sources:[]}];
  state=applyAnswerEvent(state,{type:'sources',attemptId:'attempt',sources});
  state=applyAnswerEvent(state,{type:'progress',attemptId:'attempt',phase:'source_assessment',status:'running'});
  assert.equal(state[0].sourcesOpen,true);
  assert.deepEqual(state[0].sources,sources);
  assert.equal(assessmentView(state[0].sourceAssessment),'chat.assessment.running');
  const updated=applyAnswerEvent(state,{type:'progress',attemptId:'attempt',phase:'source_assessment',source_assessment:{status:'completed',decision:'allow'}});
  assert.equal(updated[0].sourcesOpen,true);
  assert.equal(updated[0].sources,state[0].sources);
});

test('explicit source selection disables assessment without changing saved preference',async()=>{
  const {selectedAssessmentOptions}=await import('../src/lib/chatSourceAssessment.mjs');
  const message={attemptId:'old',selectedSourceIndexes:[1,7],requestAssessSources:true};
  const actual=selectedAssessmentOptions(message);
  assert.equal(actual.requestAssessSources,false);
  assert.equal(message.requestAssessSources,true);
  assert.deepEqual(actual.requestSourceSelection,{attempt_id:'old',indexes:[1,7]});
});
