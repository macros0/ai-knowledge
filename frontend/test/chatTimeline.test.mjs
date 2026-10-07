import assert from "node:assert/strict";
import test from "node:test";
import {readFileSync} from "node:fs";
import {createRequire} from "node:module";
import {runInNewContext} from "node:vm";
import React from "react";
import {renderToStaticMarkup} from "react-dom/server";
import {transformSync} from "next/dist/build/swc/index.js";
import * as recent from "../src/lib/chatRecentHistory.mjs";
import * as scroll from "../src/lib/chatScrollPosition.mjs";
import {createTranslator} from "../src/i18n/core.js";
import ru from "../src/i18n/locales/ru.js";

const require = createRequire(import.meta.url);
const t = createTranslator("ru", ru).t;
const buttons = [];
const previews = [];
const effects = [];
const pointerEvents = new EventTarget();
let previewOverflow = false;
const runtime = require("react/jsx-runtime");
const capture = create => (type, props, key) => {
  if (type === "button") buttons.push(props);
  if (type === "div" && props.className?.includes("chat-turn-preview")) previews.push(props);
  return create(type, props, key);
};
const compiled = {exports: {}};
const code = transformSync(readFileSync(new URL("../src/components/ChatTimeline.jsx", import.meta.url), "utf8"), {
  filename: "ChatTimeline.jsx", jsc: {parser: {syntax: "ecmascript", jsx: true}, target: "es2020", transform: {react: {runtime: "automatic"}}}, module: {type: "commonjs"},
}).code;
runInNewContext(code, {window: pointerEvents, module: compiled, exports: compiled.exports, require: id => {
  // Seed the overflow reported by ResizeObserver, which SSR cannot run.
  if (id === "react") return {...React,
    useState: initial => React.useState(typeof initial === "boolean" && previewOverflow ? true : initial),
    useEffect: effect => {effects.push(effect);},
  };
  if (id === "react/jsx-runtime") return {...runtime, jsx: capture(runtime.jsx), jsxs: capture(runtime.jsxs)};
  if (id === "@/i18n/LocaleContext") return {useI18n: () => ({t})};
  if (id === "@/lib/chatRecentHistory.mjs") return recent;
  if (id === "@/lib/chatScrollPosition.mjs") return scroll;
  // These children do not own timeline windowing or page-retry behavior.
  if (id === "./ChatHistoryShared") return {fmtDate: value => value, HistoryMessage: () => null};
  if (id === "./ChatRequestNavigation") return () => null;
  if (id === "next/link") return function Link({children, ...props}) { return React.createElement("a", props, children); };
  return require(id);
}});
const turns = Array.from({length: 6}, (_, i) => ({key: `history:${i + 1}`, historical: true, session_id: "session", created_at: "2026-10-07", messages: []}));

function render({previewOverflow: overflow = false, ...extra} = {}) {
  buttons.length = 0;
  previews.length = 0;
  effects.length = 0;
  previewOverflow = overflow;
  return renderToStaticMarkup(React.createElement(compiled.exports.default, {
    turns, recentHistory: {status: "ready", nextBeforeId: null}, pending: false,
    renderMessage: () => null, logRef: {current: null}, scrollPositionRef: {current: null}, messageCount: 0,
    view: {startKey: null, expanded: {}, layout: {height: 300, width: 700, heights: Object.fromEntries(turns.map(turn => [turn.key, 250]))}},
    setView: () => {}, ...extra,
  }));
}

test("collapsed history keeps visible controls interactive and reveals clipped controls on keyboard focus", () => {
  let view = {startKey: "history:5", expanded: {}};
  const html = render({previewOverflow: true, view, turns: turns.slice(4),
    setView: update => {view = update(view);},
  });
  assert.equal(/\sinert(?:[\s=>])/.test(html), false, "The whole preview must not disable its visible links and buttons");
  assert.equal(typeof previews[0].onFocusCapture, "function");
  previews[0].onFocusCapture();
  assert.equal(view.expanded["history:5"], true);
});

test("opening history controls keeps the same turns visible when the source list grows", () => {
  let view = {startKey: null, expanded: {}, layout: {height: 300, width: 700,
    heights: Object.fromEntries(turns.map(turn => [turn.key, 250]))}};
  render({view, setView: update => {view = update(view);}});
  assert.equal(typeof previews[0].onPointerDownCapture, "function");
  previews[0].onPointerDownCapture();
  view.layout.heights["history:5"] = 700;
  view.layout.heights["history:6"] = 400;
  const html = render({view});
  assert.deepEqual([...html.matchAll(/data-chat-turn="([^"]+)"/g)].map(m => m[1]), ["history:5", "history:6"]);
});

test("a pointer click expands the preview after release so its target does not move mid-click", () => {
  let view = {startKey: null, expanded: {}};
  render({previewOverflow: true, turns: turns.slice(4), view,
    setView: update => {view = update(view);},
  });
  const preview = previews[0];
  preview.onPointerDownCapture();
  preview.onFocusCapture();
  assert.equal(view.expanded["history:5"], undefined, "Pointer focus must not move the button before mouseup");
  preview.onPointerUpCapture();
  preview.onClickCapture();
  assert.equal(view.expanded["history:5"], true);
});

test("releasing the mouse outside the preview restores keyboard reveal", () => {
  let view = {startKey: null, expanded: {}};
  render({previewOverflow: true, turns: turns.slice(4), view,
    setView: update => {view = update(view);},
  });
  // Run the real event subscriptions: SSR does not mount effects automatically.
  const cleanups = effects.map(effect => effect());
  try {
    previews[0].onPointerDownCapture();
    pointerEvents.dispatchEvent(new Event("pointerup"));
    previews[0].onFocusCapture();
    assert.equal(view.expanded["history:5"], true);
  } finally {
    cleanups.forEach(cleanup => cleanup?.());
  }
});

test("timeline renders the saved measured window on return instead of an initial-size guess", () => {
  const html = render();
  assert.deepEqual([...html.matchAll(/data-chat-turn="([^"]+)"/g)].map(m => m[1]), ["history:5", "history:6"]);
});

test("retrying an older page reveals it on successful retry", async () => {
  let view = {startKey: "history:5", expanded: {}};
  render({turns: turns.slice(4), view, recentHistory: {status: "error", nextBeforeId: 5},
    setView: update => {view = update(view);},
    onLoadOlder: async ({older}) => {
      assert.equal(older, true);
      return {turns: [{id: 1}], next_before_id: null};
    },
  });
  const retry = buttons.find(button => button.children === t("common.retry"));
  await retry.onClick();
  assert.equal(view.startKey, "history:1");
});

test("initial history retry starts at the latest answer even if the error screen was scrolled up", async () => {
  const area = {scrollTop: 0, scrollHeight: 400, clientHeight: 300,
    getBoundingClientRect: () => ({top: 0}), querySelectorAll: () => []};
  const position = {current: null};
  render({turns: [], logRef: {current: area}, scrollPositionRef: position,
    recentHistory: {status: "error", nextBeforeId: null},
    onLoadOlder: async ({older}) => {assert.equal(older, false); return {turns: [{id: 1}]};},
  });
  await buttons.find(button => button.children === t("common.retry")).onClick();
  area.scrollHeight = 2000;
  scroll.restoreChatViewport(area, position.current);
  assert.equal(area.scrollTop, 2000);
});
