import test from "node:test";
import assert from "node:assert/strict";
import { resolveToastDuration } from "../src/lib/toastPolicy.mjs";
test("errors persist while explicit durations retain callers intent",()=>{
 assert.equal(resolveToastDuration("error"),0);
 assert.equal(resolveToastDuration("success"),5000);
 assert.equal(resolveToastDuration("warning",8000),8000);
 assert.equal(resolveToastDuration("success",0),0);
});
