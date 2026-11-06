import test from "node:test";
import assert from "node:assert/strict";
import {componentHarness} from "./support/componentHarness.mjs";
import {createReferenceWatcher} from "../src/lib/referenceRevisionWatcher.mjs";
import * as sourceLocales from "../src/lib/sourceLocales.mjs";

const flush = () => new Promise(resolve => setImmediate(resolve));
function browser() {
  const document = new EventTarget(); document.visibilityState = "visible";
  document.documentElement = {}; document.title = "";
  const window = new EventTarget(); window.document = document; window.navigator = {};
  return {window, document, Event};
}

test("remote reference revision refreshes seeded overrides and shared lookups", async () => {
  const globals = browser(); let revision = "1", label = "new", watcher, bumps = 0, refreshes = 0, events = 0, failed = false;
  globals.window.addEventListener("okf:locales-changed", () => {events++;});
  const router = {refresh() {refreshes++;}}, noop = value => value;
  const ui = componentHarness(new URL("../src/i18n/LocaleContext.jsx", import.meta.url), {
    "next/navigation": {usePathname: () => "/", useRouter: () => router},
    "./core": {DEFAULT_LOCALE: "ru", AUTO_FALLBACK_LOCALE: "en", readStored: () => "ru",
      detectBrowserLocale: () => "ru", normalizeLocale: noop, readLocaleCookie: () => "ru",
      setLocaleCookie() {}, setStored() {}, formatDate: noop, formatDateTime: noop,
      formatMailDateTime: noop, formatNumber: noop,
      createTranslator: (locale, dictionaries) => ({t: key => dictionaries[locale]?.[key] || "base", tc: noop})},
    "./titles": {makeTitle: () => "title"},
    "@/lib/api": {getUiDictionary: async () => {if (failed) throw new Error("offline"); return {label};},
      getConfigRevisions: async () => ({enabled: true, revisions: {references: revision}})},
    "@/lib/tagDictionary": {bumpTagVersion() {bumps++;}, refreshTagDictionaryIfStale() {}},
    "@/lib/referenceRevisionWatcher.mjs": {createReferenceWatcher(options) {
      watcher = createReferenceWatcher({...options, setTimer: () => 1, clearTimer() {}}); return watcher;
    }},
  }, {initialOverrides: {ru: {label: "old"}}}, globals, {exportName: "LocaleProvider"});
  try {
    ui.render(); await flush(); ui.render(); await flush();
    assert.equal(ui.render().props.value.t("label"), "new");
    revision = "2"; label = "newer";
    await watcher.refresh(); ui.render(); await flush();
    assert.equal(ui.render().props.value.t("label"), "newer");
    assert.equal(bumps, 2); assert.equal(refreshes, 2); assert.equal(events, 2);
    failed = true; globals.window.dispatchEvent(new Event("focus"));
    ui.render(); await flush(); ui.render();
    assert.equal(ui.render().props.value.t("label"), "base");
    failed = false; label = "recovered";
    await watcher.refresh(); ui.render(); await flush();
    assert.equal(ui.render().props.value.t("label"), "recovered");
  } finally {ui.dispose();}
  globals.window.dispatchEvent(new Event("focus"));
  assert.equal(bumps, 3);
});

test("reference language picker refreshes remote options without replacing its selected value", async () => {
  const globals = browser(); let calls = 0, active = [{code: "ru", name: "Русский"}];
  const Select = () => null;
  const ui = componentHarness(new URL("../src/components/ReferenceLocaleSelect.jsx", import.meta.url), {
    "@/i18n/LocaleContext": {useI18n: () => ({t: key => key, locale: "ru"})},
    "@/lib/api": {listActiveLocales: async () => {calls++; return active;}},
    "@/lib/sourceLocales.mjs": sourceLocales, "./SearchableSelect": Select,
  }, {value: "ru", onChange() {}}, globals);
  try {
    ui.render(); await flush(); ui.render();
    active = [...active, {code: "de", name: "Deutsch"}];
    globals.window.dispatchEvent(new Event("okf:locales-changed"));
    ui.render(); await flush(); ui.render();
    assert.equal(calls, 2);
    ui.find(node => node.type === "button").props.onClick(); ui.render();
    const select = ui.find(node => node.type === Select);
    assert.equal(select.props.value, "ru");
    assert.equal(select.props.options.some(option => option.value === "de"), true);
  } finally {ui.dispose();}
});
