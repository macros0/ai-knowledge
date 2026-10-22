import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import {runInNewContext} from "node:vm";
import React from "react";
import {transformSync} from "next/dist/build/swc/index.js";
import * as recent from "../src/lib/chatRecentHistory.mjs";
import * as scroll from "../src/lib/chatScrollPosition.mjs";

const require = createRequire(import.meta.url);
const code = transformSync(readFileSync(new URL("../src/components/ChatTimeline.jsx", import.meta.url), "utf8"), {
  filename: "ChatTimeline.jsx", jsc: {parser: {syntax: "ecmascript", jsx: true}, target: "es2020",
    transform: {react: {runtime: "automatic"}}}, module: {type: "commonjs"},
}).code;

function mount() {
  const effects = [], observers = [];
  let view = {expanded: {}};
  let writes = 0;
  const log = {clientWidth: 700, clientHeight: 300, scrollTop: 0, scrollHeight: 900,
    querySelectorAll: () => [], getBoundingClientRect: () => ({top: 0}),
    addEventListener() {}, removeEventListener() {}};
  const content = {querySelectorAll: () => [{dataset: {chatTurn: "turn"}, getBoundingClientRect: () => ({height: 120})}]};
  const logRef = {current: log};
  const compiled = {exports: {}};
  runInNewContext(code, {module: compiled, exports: compiled.exports,
    ResizeObserver: class {
      constructor(callback) {this.deliver = callback; observers.push(this);}
      observe() {}
      disconnect() {this.disconnected = true;}
    },
    require(id) {
      if (id === "react") return {...React, useRef: current => ({current}),
        useState: initial => [typeof initial === "function" ? initial() : initial, () => {writes++;}],
        useCallback: fn => fn, useLayoutEffect: effect => effects.push(effect)};
      if (id === "@/i18n/LocaleContext") return {useI18n: () => ({t: key => key})};
      if (id === "@/lib/chatRecentHistory.mjs") return recent;
      if (id === "@/lib/chatScrollPosition.mjs") return scroll;
      if (id === "./ChatHistoryShared") return {};
      if (id === "./ChatRequestNavigation" || id === "next/link") return () => null;
      return require(id);
    },
  });
  const tree = compiled.exports.default({turns: [], recentHistory: {status: "ready"}, logRef,
    scrollPositionRef: {current: null}, sessionId: "session", messageCount: 0, view,
    setView: update => {writes++; view = update(view);}});
  const contentRef = tree.props.children[0].props.children.props.ref;
  contentRef.current = content;
  const cleanups = effects.map(effect => effect());
  return {log, logRef, content, contentRef, observers, effects, cleanups,
    get view() {return view;}, get writes() {return writes;}};
}

test("timeline measures attached turns and keeps following a growing answer", () => {
  const h = mount();
  assert.equal(h.view.layout.heights.turn, 132);
  h.log.clientWidth = 800;
  h.log.scrollHeight = 1200;
  h.observers[0].deliver();
  assert.equal(h.view.layout.width, 800);
  assert.equal(h.log.scrollTop, 1200);
  h.cleanups.forEach(cleanup => cleanup?.());
});

test("resize delivery after React clears the content ref does not crash the page", () => {
  const h = mount();
  const writes = h.writes;
  h.contentRef.current = null;
  h.log.clientWidth = 800;
  assert.doesNotThrow(() => h.observers[0].deliver());
  assert.equal(h.writes, writes);
  h.cleanups.forEach(cleanup => cleanup?.());
});

test("cleaned-up observers cannot update layout when effects reconnect", () => {
  const h = mount();
  h.cleanups.forEach(cleanup => cleanup?.());
  assert.equal(h.observers[0].disconnected, true);
  const cleanups = h.effects.map(effect => effect());
  const writes = h.writes;
  h.log.clientWidth = 850;
  h.observers[0].deliver();
  assert.equal(h.writes, writes, "a late callback from the previous effect must do nothing");
  h.observers[1].deliver();
  assert.equal(h.view.layout.width, 850);
  cleanups.forEach(cleanup => cleanup?.());
});
