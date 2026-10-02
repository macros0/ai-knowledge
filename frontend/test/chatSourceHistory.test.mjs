import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { transformSync } from "next/dist/build/swc/index.js";
import * as answerState from "../src/lib/chatAnswerState.mjs";
import * as selection from "../src/lib/chatSourceSelection.mjs";
import * as context from "../src/lib/chatSourceContext.mjs";
import * as links from "../src/lib/chatSourceLinks.mjs";
import * as glossary from "../src/lib/glossaryUi.mjs";
import * as identifiers from "../src/lib/diagnosticIdentifiers.mjs";
import { createTranslator } from "../src/i18n/core.js";
import ru from "../src/i18n/locales/ru.js";

const require = createRequire(import.meta.url);
const modules = new Map();
const t = createTranslator("ru", ru).t;
function load(name) {
  if (modules.has(name)) return modules.get(name);
  const code = transformSync(readFileSync(new URL(`../src/components/${name}.jsx`, import.meta.url), "utf8"), {
    filename: `${name}.jsx`, jsc: { parser: { syntax: "ecmascript", jsx: true }, target: "es2020",
      transform: { react: { runtime: "automatic" } } }, module: { type: "commonjs" },
  }).code;
  const compiledModule = { exports: {} };
  const resolve = (id) => {
    if (id === "@/i18n/LocaleContext") return { useI18n: () => ({ t, locale: "ru" }) };
    if (id === "@/lib/chatAnswerState.mjs") return answerState;
    if (id === "@/lib/chatSourceSelection.mjs") return selection;
    if (id === "@/lib/chatSourceContext.mjs") return context;
    if (id === "@/lib/chatSources") return links;
    if (id === "@/lib/glossaryUi.mjs") return glossary;
    if (id === "@/lib/diagnosticIdentifiers.mjs") return identifiers;
    if (id === "next/link") return function Link({ children, ...props }) { return React.createElement("a", props, children); };
    if (["./ChatSources", "./icons", "./ErrorReference", "./AppliedTerms"].includes(id)) return load(id.slice(2));
    if (id === "./ChatAnswer") return { __esModule: true, default: ({ text }) => React.createElement("p", {}, text) };
    return require(id);
  };
  runInNewContext(code, { module: compiledModule, exports: compiledModule.exports, require: resolve });
  modules.set(name, compiledModule.exports);
  return compiledModule.exports;
}
const noop = () => {};
const message = {
  role: "assistant", text: "Answer [200]", attemptId: "history", responseMode: "documents", sourcesOpen: true,
  selectedSourceIndexes: [200], sources: Array.from({ length: 200 }, (_, i) => ({
    source_index: i + 1, doc_id: "doc", source_slug: `source-${i + 1}`, title: `Source ${i + 1}`,
    filename: "Document.docx", point_type: "concept", score: 0.9, selectable: true,
  })),
};
function render(m, isLatest) {
  return renderToStaticMarkup(React.createElement(load("ChatMessageView").default, {
    message: m, index: 0, isLatest, onCopy: noop, onSelect: noop, onAnswerSelected: noop,
    onAddToScope: noop, onRetry: noop, onRepeatWithoutGlossary: noop, onStop: noop, onToggleSources: noop,
  }));
}
test("old automatic source lists defer rows while keeping document and selection actions", () => {
  const html = render(message, false);
  assert.equal((html.match(/class="source-link"/g) ?? []).length, 0);
  assert.ok(html.includes("Document.docx"));
  assert.ok(html.includes(t("chat.answerSelected", { count: 1 })));
});
test("latest answer renders every source including the selected last fragment", () => {
  const html = render(message, true);
  assert.equal((html.match(/class="source-link"/g) ?? []).length, 200);
  assert.ok(html.includes("Source 200"));
  assert.ok(html.includes('checked=""'));
});
test("manual opening of old sources and manual collapse of latest sources take precedence", () => {
  assert.equal((render({ ...message, sourcesTouched: true }, false).match(/class="source-link"/g) ?? []).length, 200);
  assert.equal((render({ ...message, sourcesTouched: true, sourcesOpen: false }, true).match(/class="source-link"/g) ?? []).length, 0);
});

test("opening an automatically collapsed old list records user intent even when stored open is true", () => {
  const items = [message, { ...message, attemptId: "latest" }];
  const changed = answerState.toggleChatSources(items, 0, true);
  assert.notEqual(changed, items);
  assert.equal(changed[0].sourcesTouched, true);
  assert.equal((render(changed[0], false).match(/class="source-link"/g) ?? []).length, 200);
  assert.equal(changed[1], items[1]);
  assert.equal(answerState.toggleChatSources(changed, 0, true), changed);
});
