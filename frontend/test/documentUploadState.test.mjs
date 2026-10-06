import test from "node:test";
import assert from "node:assert/strict";
import {retryableUploadItems,updateUploadItem} from "../src/lib/documentUploadState.mjs";
test("retry includes only retained failed uncertain and not sent files",()=>{
 const items=[{id:"a",state:"uploaded"},{id:"b",state:"skipped"},{id:"c",state:"failed"},{id:"d",state:"uncertain"},{id:"e",state:"not-sent"},{id:"f",state:"failed"}];
 assert.deepEqual(retryableUploadItems(items,new Map([["a",{}],["b",{}],["c",{}],["d",{}],["e",{}]])).map(x=>x.id),["c","d","e"]);
});
test("successful retry clears previous error and keeps other rows",()=>{
 const rows=[{id:"a",state:"failed",errorCode:"timeout",requestId:"old"},{id:"b",state:"uploaded",docId:"b"}];
 const next=updateUploadItem(rows,"a","uploaded",{docId:"a"});
 assert.equal(next[0].errorCode,undefined);assert.equal(next[0].requestId,undefined);assert.equal(next[0].docId,"a");assert.deepEqual(next[1],rows[1]);
});
