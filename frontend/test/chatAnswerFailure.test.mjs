import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { runInNewContext } from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { transformSync } from "next/dist/build/swc/index.js";
import * as answerState from "../src/lib/chatAnswerState.mjs";
import * as renderState from "../src/lib/chatRenderState.mjs";
import * as assessment from "../src/lib/chatSourceAssessment.mjs";
import * as composer from "../src/lib/chatComposer.mjs";
import * as selection from "../src/lib/chatSourceSelection.mjs";
import * as api from "../src/lib/api.js";
import { createTranslator } from "../src/i18n/core.js";
import ru from "../src/i18n/locales/ru.js";

const require = createRequire(import.meta.url);
const t = createTranslator("ru", ru).t;
const requestId = "55e65780-97df-43ec-bc83-1d5f18b6fc91";
const draft = "Расчёт профвзносов: удержание из заработной платы [1].";
const sources = [{ source_index: 1, doc_id: "document", in_model_context: false }];
function compile(name, resolve, globals = {}, exportName = "default") {
  const source = readFileSync(new URL(`../src/components/${name}.jsx`, import.meta.url), "utf8");
  const code = transformSync(source, { filename: `${name}.jsx`,
    jsc: { parser: { syntax: "ecmascript", jsx: true }, target: "es2022",
      transform: { react: { runtime: "automatic" } } }, module: { type: "commonjs" } }).code;
  const mod = { exports: {} };
  runInNewContext(code, { module: mod, exports: mod.exports, require: resolve, ...globals });
  return mod.exports[exportName];
}
function find(node, predicate) {
  if (!node || typeof node !== "object") return null;
  if (predicate(node)) return node;
  for (const child of [node.props?.children].flat(Infinity)) {
    const result = find(child, predicate);
    if (result) return result;
  }
  return null;
}
function panelHarness(chat, responseMode = "fast") {
  let messages = [];
  const frames = new Map();
  let frame = 0;
  const stub = () => null;
  const ChatComposer = () => null;
  const ctx = {
    queuedAssessmentRetry: null, assessSources: true, messages, tags: [], pending: false,
    selectedMode: "hybrid", searchDepth: 40, sessionId: null, scrollPositionRef: {current:null},
    mailMode: "all", useGlossary: false, searchScopeDocuments: [], searchScopeEnabled: false,
    responseMode, selectedTopK: 5, showCustom: false, customValue: "",
    showCustomDepth: false, customDepthValue: "", moduleFilter: "", devFilter: "", sourceLocale: "",
    searchSettingsOpen: false, draftQuery: "", sourceView: "documents",
    recentHistory: { turns: [], status: "ready" }, historyLoader: {}, timelineView: {}, MODE_LABELS: {},
    settings: { source_assessment: {available:true, sample_size:5}, response_modes: ["documents", "fast", "full"],
      search_modes:["hybrid"], search_depth_presets:[40], top_k_presets:[5],
      top_k_default:5, top_k_min:1, top_k_max:10, search_depth_max:500 },
    setMessages(update) { messages = typeof update === "function" ? update(messages) : update; },
  };
  const context = new Proxy(ctx, { get(target, key) { return target[key] ?? (key.startsWith("set") ? stub : undefined); } });
  const modules = {
    "@/lib/chatAnswerState.mjs": answerState, "@/lib/chatRenderState.mjs": {...renderState, createChatDeltaBuffer: options => renderState.createChatDeltaBuffer({...options, schedule:cb=>{frames.set(++frame,cb);return frame;}, cancel:id=>frames.delete(id)})},
    "@/lib/chatSourceAssessment.mjs": assessment, "@/lib/chatComposer.mjs": composer,
    "@/lib/chatSourceSelection.mjs": selection,
    "@/lib/sourceLocales.mjs": { facetOptions: () => [] },
    "@/lib/chatSearchScope.mjs": {scopeRequestIds:()=>undefined},
    "@/lib/chatRecentHistory.mjs": {chatFeedTurns:()=>[]},
    "@/lib/chatFilterSummary.mjs": {buildChatFilterSummary:()=>[]},
    "@/lib/chatMailFilter.mjs": {},
    "@/lib/api": {...api, chat, cancelChatAttempt:async()=>{}},
    "@/context/ChatContext": {useChat:()=>context},
    "@/context/AuthContext": {useAuth:()=>({user:{username:"synthetic"},mode:"disabled",loading:false})},
    "@/i18n/LocaleContext": {useI18n:()=>({t,locale:"ru"})},
  };
  const Panel = compile("ChatPanel", id => {
    if (id === "react") return {...React, useState:initial=>[initial,stub], useRef:initial=>({current:initial}),
      useEffect:stub, useCallback:fn=>fn, useMemo:fn=>fn()};
    if (modules[id]) return modules[id];
    if (id === "./ChatComposer") return ChatComposer;
    if (id.startsWith("./") || id === "next/link") return stub;
    return require(id);
  }, {crypto:{randomUUID:()=>"attempt"}, AbortController,
    requestAnimationFrame:cb=>{frames.set(++frame,cb);return frame;}, cancelAnimationFrame:id=>frames.delete(id)});
  const tree = Panel();
  return {send:find(tree, n=>n.type === ChatComposer).props.onSubmit, messages:()=>messages, frames, tree, stop:find(tree, n=>Boolean(n.props?.renderMessage)).props.renderMessage({role:"assistant"}, 0).props.onStop};
}
for (const code of ["chat_evidence_invalid", "dependency_unavailable"]) {
  test(`${code} retains every received delta and sources, with a separate failure notice`, async () => {
    const h = panelHarness(async (...args) => {
      args[9](sources);
      args[8](draft.slice(0, 18));
      args[10].onProgress({type:"progress",phase:"generation"}); // flush the first part
      args[8](draft.slice(18)); // error arrives before the next animation frame
      throw new api.ApiError("PRIVATE_PROVIDER_TEXT", {code,status:code === "chat_evidence_invalid" ? 422 : 503,requestId});
    });
    await h.send("Расчёт профвзносов");
    const answer = h.messages().at(-1);
    assert.equal(answer.text, draft);
    assert.equal(answer.failed, true);
    assert.equal(answer.hasStreamedText, true);
    assert.equal(answer.failureNotice, t("chat.errorPrefix", {message:t(`apiError.${code}`)}));
    assert.equal(answer.requestId, requestId);
    assert.deepEqual(answer.sources, sources);
    assert.equal(h.frames.size, 0);
    assert.equal(answer.retryable, true);
    assert.ok(!JSON.stringify(answer).includes("PRIVATE_PROVIDER_TEXT"));
  });
}
test("failure before the first token shows an error without retaining the thinking placeholder", async () => {
  const h = panelHarness(async()=>{throw new api.ApiError("PRIVATE", {code:"chat_evidence_invalid",status:422});});
  await h.send("question");
  const answer = h.messages().at(-1);
  assert.equal(answer.text, "");
  assert.equal(answer.hasStreamedText, undefined);
  assert.ok(answer.failureNotice);
});
test("a successful final result still replaces provisional text with the verified answer", async () => {
  const h = panelHarness(async(...args)=>{args[8]("temporary");return {answer:"Verified [1]",sources};});
  await h.send("question");
  assert.equal(h.messages().at(-1).text, "Verified [1]");
  assert.equal(h.messages().at(-1).failureNotice, undefined);
});
test("the real message view renders the retained answer, warning, request reference and restart action", () => {
  const View = compile("ChatMessageView", id => {
    if (id === "@/i18n/LocaleContext") return {useI18n:()=>({t})};
    if (id === "@/lib/chatAnswerState.mjs") return answerState;
    if (id === "@/lib/chatSourceSelection.mjs") return selection;
    if (id === "./ChatAnswer") return function DraftAnswer({text}) { return React.createElement("p",null,text); };
    if (id === "./ErrorReference") return function Reference({requestId}) { return React.createElement("code",null,requestId); };
    if (id === "./icons") return {CheckIcon:()=>null,CopyIcon:()=>null};
    if (id.startsWith("./") || id === "next/link") return ()=>null;
    return require(id);
  });
  const html = renderToStaticMarkup(React.createElement(View, {message:{role:"assistant",text:draft,
    hasStreamedText:true,failed:true,retryable:true,failureNotice:"Ошибка проверки",requestId},index:0}));
  assert.ok(html.includes(draft));
  assert.ok(html.includes('role="alert"'));
  assert.ok(html.includes("Ошибка проверки"));
  assert.ok(html.includes(t("chat.retainedAnswerNotice")));
  assert.ok(html.includes(requestId));
  assert.ok(html.includes(t("chat.restartAnswer")));
});


test("restored failed history renders the draft with its failure status and diagnostic reference", () => {
  const History = compile("ChatHistoryShared", id => {
    if (id === "@/i18n/LocaleContext") return {useI18n:()=>({t})};
    if (id === "@/context/ChatContext") return {useChat:()=>({})};
    if (id === "@/lib/api") return api;
    if (id === "@/lib/chatAnswerState.mjs") return answerState;
    if (id === "@/lib/chatSources") return {};
    if (id === "./MarkdownViewer") return function Markdown({text}) { return React.createElement("p",null,text); };
    if (id === "./ErrorReference") return function Reference({requestId}) { return React.createElement("code",null,requestId); };
    if (id.startsWith("./")) return ()=>null;
    return require(id);
  }, {}, "HistoryMessage");
  const html = renderToStaticMarkup(React.createElement(History, {m:{role:"assistant",content:draft,
    retrieval_metadata:{answer_attempt:{status:"failed",mode:"fast",retained_draft:true,error_code:"chat_evidence_invalid",request_id:requestId}}}}));
  assert.ok(html.includes(draft));
  assert.ok(html.includes(t("chat.retainedAnswerNotice")));
  assert.ok(html.includes(t("apiError.chat_evidence_invalid")));
  assert.ok(html.includes(requestId));
});


test("Find sources hides assessment controls and sends assessment off without changing the saved preference", async () => {
  let options;
  const h = panelHarness(async (...args) => {
    options = args[10];
    return {answer:"Найдено документов: 1",sources};
  }, "documents");
  assert.equal(find(h.tree, n=>n.props?.className === "chat-assessment-preference"), null);
  await h.send("question");
  assert.equal(options.assessSources, false);
  assert.equal(h.messages().at(-1).requestAssessSources, true);
});

test("stopping a streamed answer retains received text instead of replacing it with the stop notice", async () => {
  const h = panelHarness(async (...args) => {
    args[8](draft);
    await new Promise((resolve, reject) => args[10].signal.addEventListener("abort",()=>reject(new Error("aborted")),{once:true}));
  });
  const sending = h.send("question");
  h.stop();
  await sending;
  const answer = h.messages().at(-1);
  assert.equal(answer.text, draft);
  assert.equal(answer.stopped, true);
  assert.equal(answer.failureNotice, t("chat.answerStopped"));
  assert.equal(h.frames.size, 0);
});
