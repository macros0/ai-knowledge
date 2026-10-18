import test from "node:test";
import assert from "node:assert/strict";
import { componentHarness } from "./support/componentHarness.mjs";
import * as conflicts from "../src/lib/glossaryConflicts.mjs";
import { translationState } from "../src/lib/glossaryUi.mjs";

const Dialog = () => null;
const original = { id: 7, kind: "business_term", original_name: "Original", original_description: "",
  canonical_locale: "ru", enabled: true, version: 1, reference_revision: 1, aliases: [], translations: [] };
const remote = { ...original, original_name: "Colleague", version: 2, reference_revision: 2 };
const flush = () => new Promise(setImmediate);

function fixture(state = "editing", saveResponse = null) {
  const writes = [];
  const t = key => key;
  const noop = () => null;
  const props = { term: original, canManage: true, onSaved() {} };
  const ui = componentHarness(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), {
    "@/lib/api": { getGlossaryTerm: async () => remote, updateGlossarySource: async (id, body) => {
      writes.push(body);
      if (saveResponse) return saveResponse(body);
      if (body.version === 1) throw Object.assign(new Error("stale"), { code: "version_conflict" });
      return { ...remote, ...body, version: 3, reference_revision: 3 };
    } },
    "@/lib/apiToast.mjs": { apiToast: () => ({}) },
    "@/i18n/LocaleContext": { useI18n: () => ({ t, locale: "ru" }) },
    "./Toast": { useToast: () => ({ showToast() {} }) },
    "./ReferenceLocaleSelect": noop, "./GlossaryTranslations": noop, "./GlossaryPreview": noop,
    "./GlossaryAliasCheck": { default: noop, GlossaryConflicts: noop, GlossaryCheckResult: noop,
      useGlossaryAliasCheck: () => ({ conflicts: [] }) },
    "@/lib/glossaryConflicts.mjs": conflicts, "./GlossaryMergeDialog": noop,
    "./ReferenceConflictDialog": Dialog, "./ReferenceEditNotice": noop,
    "@/hooks/useReferenceEditSession": { useReferenceEditSession: () => ({ state, status: null,
      markActivity() {}, getMutationHeaders: () => ({}), acquire() {} }) },
  }, props, { document: { activeElement: null }, navigator: { clipboard: { writeText: async () => {} } } });
  const save = () => ui.find(node => node.type === "button" && node.props.children === "admin.glossary.save");
  return { ...ui, writes, save, props };
}

test("glossary keeps the original write revision when new props arrive over a dirty draft", async () => {
  const ui = fixture();
  try {
    ui.render(); ui.render();
    ui.find(node => node.type === "input" && node.props.value === "Original").props.onChange({ target: { value: "Mine" } });
    ui.render({ ...ui.props, term: remote }); ui.render({ ...ui.props, term: remote });
    await ui.save().props.onClick(); await flush(); ui.render({ ...ui.props, term: remote });
    assert.equal(ui.writes[0].version, 1);
    assert.equal(ui.writes[0].expected_reference_revision, 1);
    const dialog = ui.find(node => node.type === Dialog);
    assert.ok(dialog);
    assert.equal(dialog.props.local.original_name, "Mine");
    assert.equal(dialog.props.base.original_name, "Original");
    dialog.props.onConfirm({ ...remote, original_name: "Mine" }); ui.render({ ...ui.props, term: remote });
    assert.equal(ui.writes.length, 1);
    await ui.save().props.onClick();
    assert.equal(ui.writes[1].version, 2);
    assert.equal(ui.writes[1].original_name, "Mine");
  } finally { ui.dispose(); }
});

test("glossary source save refuses a dispatched click after losing the lease", async () => {
  const ui = fixture("lost");
  try { ui.render(); ui.render(); await ui.save().props.onClick(); assert.equal(ui.writes.length, 0); }
  finally { ui.dispose(); }
});

test("a late save response preserves input queued after the submitted source snapshot", async () => {
  let complete;
  const ui = fixture("editing", () => new Promise(resolve => {complete = resolve;}));
  try {
    ui.render();ui.render();
    ui.find(node => node.type === "input" && node.props.value === "Original").props.onChange({target:{value:"Submitted"}});
    ui.render();
    const pending = ui.save().props.onClick();ui.render();
    assert.equal(ui.find(node => node.type === "fieldset").props.disabled,true);
    // A queued input event may arrive before React commits the disabled state.
    ui.find(node => node.type === "input" && node.props.value === "Submitted").props.onChange({target:{value:"Later input"}});
    ui.render();
    const saved={...original, original_name:"Submitted", version:2, reference_revision:2};
    complete(saved);await pending;ui.render({...ui.props,term:saved});ui.render({...ui.props,term:saved});
    assert.ok(ui.find(node => node.type === "input" && node.props.value === "Later input"));
    assert.equal(ui.writes.length,1);
  } finally {ui.dispose();}
});

test("translation retains its observed version and target across source changes", async () => {
  const Select = () => null, noop = () => null, writes = [];
  const initial = {...original, translations: [{locale: "en", display_name: "Observed", version: 1, source_revision: 1}], source_revision: 1};
  const changed = {...remote, source_revision: 2, canonical_locale: "en", translations: [{locale: "en", display_name: "Remote", version: 2}]};
  const props = {term: initial, onSaved() {}, session: {state: "editing", getMutationHeaders: () => ({})}};
  const ui = componentHarness(new URL("../src/components/GlossaryTranslations.jsx", import.meta.url), {
    "@/lib/api": {listActiveLocales: async () => [], glossaryTranslation: async (id, locale, body) => {writes.push({locale, ...body}); return changed;}, getGlossaryTerm: async () => changed},
    "@/lib/apiToast.mjs": {apiToast: () => ({})}, "@/lib/glossaryUi.mjs": {translationState},
    "@/i18n/core": {SUPPORTED_LOCALES: ["en", "ru"]}, "@/i18n/LocaleContext": {useI18n: () => ({t: key => key, locale: "ru"})},
    "./Toast": {useToast: () => ({showToast() {}})}, "./SearchableSelect": Select,
    "@/lib/sourceLocales.mjs": {languageLabel: value => value}, "./ReferenceConflictDialog": noop,
  }, props);
  try {
    ui.render(); await flush(); ui.render(); ui.render();
    ui.find(node => node.type === "input").props.onChange({target: {value: "My draft"}}); ui.render();
    ui.render({...props, term: changed}); ui.render({...props, term: changed});
    assert.equal(ui.find(node => node.type === Select).props.value, "en");
    await ui.find(node => node.type === "button" && node.props.children === "admin.glossary.save").props.onClick();
    assert.equal(writes[0].translation_version, 1);
    assert.equal(writes[0].source_revision, 1);
    assert.equal(writes[0].locale, "en");
  } finally {ui.dispose();}
});

test("translation target cannot switch during save or conflict comparison", async () => {
  const Select = () => null; let rejectWrite;
  const initial = {...original, source_revision: 1, translations: [
    {locale: "en", display_name: "English", version: 1}, {locale: "fr", display_name: "Français", version: 1}]};
  const changed = {...initial, reference_revision: 2, translations: [
    {locale: "en", display_name: "New English", version: 2}, {locale: "fr", display_name: "Nouveau français", version: 2}]};
  const props = {term: initial, onSaved() {}, session: {state: "editing", getMutationHeaders: () => ({})}};
  const ui = componentHarness(new URL("../src/components/GlossaryTranslations.jsx", import.meta.url), {
    "@/lib/api": {listActiveLocales: async () => [], glossaryTranslation: () => new Promise((_resolve, reject) => {rejectWrite = reject;}), getGlossaryTerm: async () => changed},
    "@/lib/apiToast.mjs": {apiToast: () => ({})}, "@/lib/glossaryUi.mjs": {translationState},
    "@/i18n/core": {SUPPORTED_LOCALES: ["en", "fr", "ru"]}, "@/i18n/LocaleContext": {useI18n: () => ({t: key => key, locale: "ru"})},
    "./Toast": {useToast: () => ({showToast() {}})}, "./SearchableSelect": Select,
    "@/lib/sourceLocales.mjs": {languageLabel: value => value}, "./ReferenceConflictDialog": Dialog,
  }, props, {window: {confirm: () => true}});
  try {
    ui.render(); await flush(); ui.render(); ui.render();
    ui.find(node => node.type === "input").props.onChange({target: {value: "My English"}}); ui.render();
    const pending = ui.find(node => node.type === "button" && node.props.children === "admin.glossary.save").props.onClick(); ui.render();
    const select = ui.find(node => node.type === Select);
    assert.equal(select.props.disabled, true);
    select.props.onChange("fr"); ui.render();
    assert.equal(ui.find(node => node.type === Select).props.value, "en");
    rejectWrite(Object.assign(new Error("stale"), {code: "version_conflict"})); await pending; ui.render();
    const dialog = ui.find(node => node.type === Dialog);
    assert.equal(dialog.props.local.display_name, "My English");
    assert.equal(dialog.props.remote.display_name, "New English");
    ui.find(node => node.type === Select).props.onChange("fr"); ui.render();
    assert.equal(ui.find(node => node.type === Select).props.value, "en");
  } finally {ui.dispose();}
});
