import test from "node:test";
import assert from "node:assert/strict";
import {buildChatFilterSummary} from "../src/lib/chatFilterSummary.mjs";
test("summary covers every active hard filter and keeps default mail visibility",()=>{
 const result=buildChatFilterSummary({tags:["SAP","HR"],moduleFilter:"PA",sourceLocale:"de",mailMode:"exclude",searchScopeEnabled:true,searchScopeDocuments:[{doc_id:"a"}]});
 assert.deepEqual(result.map(x=>x.id),["scope","tag:SAP","tag:HR","module","locale","mail"]);
 assert.equal(result[0].params.count,1);assert.equal(result.at(-1).key,"chat.mailModeExclude");
 const all=buildChatFilterSummary({});assert.deepEqual(all.map(x=>x.key),["ux.scopeAll","docs.allLocales","chat.mailModeAll"]);
});
