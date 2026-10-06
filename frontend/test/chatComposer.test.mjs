import test from "node:test";
import assert from "node:assert/strict";
import { shouldSubmitQuestion, retryRequestOptions, freshSourceSearchOptions, chatNetworkScopeOptions } from "../src/lib/chatComposer.mjs";
test("only unmodified Enter outside IME submits", () => {
  assert.equal(shouldSubmitQuestion({key:"Enter"}), true);
  assert.equal(shouldSubmitQuestion({key:"Enter",shiftKey:true}), false);
  assert.equal(shouldSubmitQuestion({key:"Enter",isComposing:true}), false);
  assert.equal(shouldSubmitQuestion({key:"a"}), false);
});
test("retry snapshots preserve glossary and original scope including false", () => {
  const m={query:"Q", requestUseGlossary:false, requestTags:["HR"], requestDocIds:["a"],requestMailMode:"exclude",requestSearchDepth:200,responseMode:"full"};
  const actual=retryRequestOptions(m);
  assert.equal(actual.requestUseGlossary, false);
  assert.deepEqual(actual.requestTags,["HR"]);
  assert.deepEqual(actual.requestDocIds,["a"]);
  assert.equal(actual.requestSearchDepth,200);
  assert.equal(actual.requestMailMode,"exclude");
});

test("unavailable selected sources can be searched again within original scope", () => {
  const m={requestUseGlossary:false, requestTags:["HR"],requestDocIds:["a"],requestSearchDepth:200,requestSourceSelection:{attempt_id:"old",indexes:[200]},responseMode:"full"};
  const next=freshSourceSearchOptions(m);
  assert.equal(next.requestSourceSelection,undefined);
  assert.equal(next.responseMode,"documents");
  assert.deepEqual(next.requestDocIds,["a"]);
  assert.deepEqual(next.requestTags,["HR"]);
  assert.equal(next.requestSearchDepth,200);
  assert.equal(m.requestSourceSelection.attempt_id,"old");
});

test("selected answer keeps recovery scope in metadata but sends only snapshot", () => {
  const message={requestDocIds:["a"],requestSourceSelection:{attempt_id:"old",indexes:[200]}};
  assert.deepEqual(chatNetworkScopeOptions(message),{sourceSelection:message.requestSourceSelection,searchDocIds:undefined});
  assert.deepEqual(chatNetworkScopeOptions(freshSourceSearchOptions(message)),{sourceSelection:undefined,searchDocIds:["a"]});
});
