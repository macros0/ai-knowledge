import test from "node:test";
import assert from "node:assert/strict";
import {scenarioNetworkOptions,scenarioDraft,previewSearch} from "../src/lib/chatScenarios.mjs";
import {retryRequestOptions,freshSourceSearchOptions} from "../src/lib/chatComposer.mjs";
import {selectedAssessmentOptions,historyAssessmentMessage} from "../src/lib/chatSourceAssessment.mjs";
import {selectableSources} from "../src/lib/chatSourceSelection.mjs";
import {applyAnswerEvent} from "../src/lib/chatAnswerState.mjs";

test("only scenario sends id; retry retains id while new source search clears it",()=>{
 const message={responseMode:"scenario",requestScenarioId:"a"};
 assert.deepEqual(scenarioNetworkOptions(message),{scenarioId:"a"});
 assert.deepEqual(scenarioNetworkOptions({...message,responseMode:"fast"}),{});
 assert.equal(retryRequestOptions(message).requestScenarioId,"a");
 assert.equal(freshSourceSearchOptions(message).requestScenarioId,undefined);
});
test("selected fragments preserve scenario and historical snapshots remain independent",()=>{
 const message={responseMode:"scenario",requestScenarioId:"a",attemptId:"one",selectedSourceIndexes:[1],sources:[{source_index:1,selectable:true}]};
 assert.equal(selectedAssessmentOptions(message).responseMode,"scenario");
 assert.equal(selectedAssessmentOptions(message).requestScenarioId,"a");
 assert.equal(selectableSources(message).length,1);
 const saved=historyAssessmentMessage({retrieval_metadata:{scenario:{id:"a",version:1,instructions:"old"},answer_attempt:{mode:"scenario"}}},"session");
 assert.equal(saved.requestScenarioId,"a");assert.equal(saved.scenario.version,1);
});
test("scenario progress survives later events and draft preview excludes history",()=>{
 const messages=[{role:"assistant",attemptId:"one",text:""}];
 const event={type:"progress",phase:"scenario",attemptId:"one",scenario:{id:"a",version:2},scenario_execution:{status:"accepted"}};
 const next=applyAnswerEvent(messages,event);
 assert.equal(applyAnswerEvent(next,{type:"delta",attemptId:"one",text:"x"})[0].scenario.version,2);
 const draft=scenarioDraft();assert.equal(draft.visibility,"private");
 const search=previewSearch({requestSessionId:"secret",requestDocIds:["d"],requestSearchDepth:40,requestMailMode:"only"},"Question","en");
 assert.equal(search.session_id,undefined);assert.equal(search.mail_mode,"only");assert.deepEqual(search.search_doc_ids,["d"]);
});
