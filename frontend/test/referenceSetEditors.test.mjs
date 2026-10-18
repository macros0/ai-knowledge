import test from "node:test";
import assert from "node:assert/strict";
import {componentHarness} from "./support/componentHarness.mjs";

const DictionaryDialog = () => null;
const flush = () => new Promise(setImmediate);

function fixture(state = "editing") {
  let snapshot = {locale: "zz", version: 1, reference_revision: 1, data: {"nav.documents": "Original"}, note: ""};
  const calls = [], noop = () => null, t = key => key;
  const ui = componentHarness(new URL("../src/components/UiDictionaryEditor.jsx", import.meta.url), {
    "@/lib/api": {getUiDictionaryAdmin: async () => snapshot, uiDictionaryHistory: async () => [],
      importUiDictionary: async (code, body) => {calls.push(body); return {applied: body.confirm,
        reference_revision: snapshot.reference_revision, active_version: snapshot.version, preview_digest: "a".repeat(64),
        preview: {total: 1, added: [], removed: [], unchanged: ["nav.documents"]}};}, friendlyApiError: () => "Error"},
    "@/lib/apiToast.mjs": {apiToast: () => ({})}, "./Toast": {useToast: () => ({showToast() {}})},
    "@/i18n/LocaleContext": {useI18n: () => ({t})},
    "@/lib/uiDictExport.mjs": {downloadDict() {}, effectiveDictionary() {}, exportFilename() {}, enSourceDictionary() {}},
    "@/hooks/useReferenceEditSession": {useReferenceEditSession: () => ({state, status: null,
      markActivity() {}, getMutationHeaders: () => ({"X-Reference-Edit-Epoch": "4"})})},
    "./ReferenceEditNotice": noop, "./ReferenceDictionaryConflictDialog": DictionaryDialog, "./Modal": noop,
  }, {locale: "zz"}, {window: {confirm: () => true}, navigator: {clipboard: {writeText: async () => {}}}});
  const button = key => ui.find(node => node.type === "button" && node.props.children === key);
  return {...ui, calls, button, setSnapshot: value => {snapshot = value;}};
}

test("dictionary Apply uses the exact reviewed payload, revision, active version and lease", async () => {
  const ui = fixture();
  try {
    ui.render(); await flush(); ui.render();
    ui.find(node => node.type === "textarea").props.onChange({target: {value: '{"nav.documents":"Mine"}'}}); ui.render();
    await ui.button("admin.uictl.preview").props.onClick(); await flush(); ui.render();
    await ui.button("admin.uictl.apply").props.onClick(); await flush(); ui.render();
    const write = ui.calls.find(body => body.confirm);
    assert.equal(write.expected_reference_revision, 1);
    assert.equal(write.expected_active_version, 1);
    assert.equal(write.preview_digest, "a".repeat(64));
    assert.equal(write.headers["X-Reference-Edit-Epoch"], "4");
    assert.equal(write.data["nav.documents"], "Mine");
  } finally {ui.dispose();}
});

test("changed dictionary input invalidates preview and a lost lease refuses a dispatched Apply", async () => {
  for (const state of ["editing", "lost"]) {
    const ui = fixture(state);
    try {
      ui.render(); await flush(); ui.render();
      await ui.button("admin.uictl.preview").props.onClick(); await flush(); ui.render();
      ui.find(node => node.type === "textarea").props.onChange({target: {value: '{"nav.documents":"Later"}'}}); ui.render();
      await ui.button("admin.uictl.apply").props.onClick(); await flush();
      assert.equal(ui.calls.filter(body => body.confirm).length, 0);
    } finally {ui.dispose();}
  }
});

test("a remote dictionary change opens comparison without silently rebasing the JSON draft", async () => {
  const ui = fixture();
  try {
    ui.render(); await flush(); ui.render();
    ui.find(node => node.type === "textarea").props.onChange({target: {value: '{"nav.documents":"Mine"}'}}); ui.render();
    ui.setSnapshot({locale: "zz", version: 2, reference_revision: 2, data: {"nav.documents": "Colleague"}, note: ""});
    await ui.button("admin.uictl.preview").props.onClick(); await flush(); ui.render();
    const comparison = ui.find(node => node.type === DictionaryDialog);
    assert.ok(comparison);
    assert.equal(comparison.props.base["nav.documents"], "Original");
    assert.equal(comparison.props.local["nav.documents"], "Mine");
    assert.equal(comparison.props.remote["nav.documents"], "Colleague");
    assert.equal(ui.calls.length, 0);
    comparison.props.onConfirm({"nav.documents": "Mine"}); ui.render();
    assert.equal(ui.calls.length, 0);
  } finally {ui.dispose();}
});

function stopwordFixture(state = "editing") {
  const calls = [], noop = () => null, t = key => key;
  const ui = componentHarness(new URL("../src/components/StopwordsEditor.jsx", import.meta.url), {
    "@/lib/api": {listStopwords: async () => [{word: "one", kind: "bm25"}],
      getStopwordSet: async () => ({words: [{word: "one", kind: "bm25"}], reference_revisions: {bm25: 1}}),
      stopwordsHistory: async () => [], addStopword: async (code, body) => {calls.push(body); throw Object.assign(new Error("busy"), {code: "version_conflict"});},
      importStopwords: async (code, body) => {calls.push(body); return {applied: body.confirm, kind: "bm25", reference_revision: 1,
        preview_digest: "b".repeat(64), added: ["mine"], removed: [], unchanged: [], total_after: 2};}},
    "@/lib/apiToast.mjs": {apiToast: () => ({})}, "./Toast": {useToast: () => ({showToast() {}})},
    "@/i18n/LocaleContext": {useI18n: () => ({t})}, "./ReferenceEditNotice": noop, "./Modal": noop,
    "@/hooks/useReferenceEditSession": {useReferenceEditSession: () => ({state, status: null,
      markActivity() {}, getMutationHeaders: () => ({"X-Reference-Edit-Epoch": "5"})})},
  }, {locale: "zz"}, {window: {confirm: () => true}, navigator: {clipboard: {writeText: async () => {}}}});
  const button = key => ui.find(node => node.type === "button" && node.props.children === key);
  return {...ui, calls, button};
}

test("stopword import confirms the reviewed set snapshot and payload", async () => {
  const ui = stopwordFixture();
  try {
    ui.render(); await flush(); ui.render();
    ui.find(node => node.type === "textarea" && node.props.placeholder === "admin.stopwords.importPlaceholder").props.onChange({target: {value: "mine"}}); ui.render();
    await ui.button("admin.stopwords.importPreview").props.onClick(); ui.render();
    await ui.button("admin.stopwords.importApply").props.onClick(); await flush();
    const write = ui.calls.find(body => body.confirm);
    assert.equal(write.expected_reference_revision, 1);
    assert.equal(write.preview_digest, "b".repeat(64));
    assert.equal(write.headers["X-Reference-Edit-Epoch"], "5");
    assert.deepEqual([...write.words], ["mine"]);
  } finally {ui.dispose();}
});

test("a failed stopword add keeps the input and a lost editor cannot write", async () => {
  for (const state of ["editing", "lost"]) {
    const ui = stopwordFixture(state);
    try {
      ui.render(); await flush(); ui.render();
      ui.find(node => node.type === "input" && node.props.placeholder === "admin.stopwords.addPlaceholder").props.onChange({target: {value: "mine"}}); ui.render();
      await ui.button("admin.stopwords.add").props.onClick(); await flush(); ui.render();
      assert.equal(ui.find(node => node.type === "input" && node.props.placeholder === "admin.stopwords.addPlaceholder").props.value, "mine");
      assert.equal(ui.calls.length, state === "lost" ? 0 : 1);
    } finally {ui.dispose();}
  }
});
