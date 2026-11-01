import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {runInNewContext} from "node:vm";
import {transformSync} from "next/dist/build/swc/index.js";

function dictionary(load, now) {
  const compiled = transformSync(readFileSync(new URL("../src/lib/tagDictionary.js", import.meta.url), "utf8"), {
    jsc: {parser: {syntax: "ecmascript"}, target: "es2020"}, module: {type: "commonjs"},
  }).code;
  const mod = {exports: {}};
  runInNewContext(compiled, {module: mod, exports: mod.exports, performance: {now},
    require(id) {
      if (id === "react") return {useSyncExternalStore: (_subscribe, snapshot) => snapshot()};
      if (id === "./api") return {listTags: load};
      throw new Error("Unexpected module");
    }});
  return mod.exports;
}
const flush = () => new Promise(resolve => setImmediate(resolve));

test("derived tag counts refresh after TTL even without a new catalog revision", async () => {
  let clock = 0, calls = 0, count = 1;
  const tags = dictionary(async () => {calls++; return [{id: 1, canonical_text: "tag", count}];}, () => clock);
  tags.refreshTagDictionaryIfStale(); assert.equal(calls, 0);
  tags.useTagDictionary(); tags.useTagDictionary(); await flush();
  assert.equal(calls, 1); assert.equal(tags.useTagDictionary()[0].count, 1);
  count = 2; clock = 30_001;
  tags.refreshTagDictionaryIfStale(); tags.useTagDictionary(); await flush();
  assert.equal(calls, 2); assert.equal(tags.useTagDictionary()[0].count, 2);
});

test("an obsolete lookup response cannot replace a refreshed generation", async () => {
  const replies = [];
  const tags = dictionary(() => new Promise(resolve => replies.push(resolve)), () => 0);
  tags.useTagDictionary(); tags.bumpTagVersion(); tags.useTagDictionary();
  replies[1]([{id: 1, canonical_text: "new"}]); await flush();
  replies[0]([{id: 1, canonical_text: "old"}]); await flush();
  assert.equal(tags.useTagDictionary()[0].canonical_text, "new");
});
