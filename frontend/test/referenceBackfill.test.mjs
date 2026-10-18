import test from "node:test";
import assert from "node:assert/strict";
import {componentHarness} from "./support/componentHarness.mjs";
import {chunkTermIds} from "../src/lib/glossaryUi.mjs";

const flush = () => new Promise(setImmediate);
test("backfill shows skipped work separately and processes glossary beyond its first page", async () => {
  const batches = [], pages = [], busy = [];
  const terms = Array.from({length: 101}, (_, i) => ({id: i + 1, canonical_locale: "ru", translations: []}));
  const t = (key, args) => args ? `${key}:${JSON.stringify(args)}` : key;
  const ui = componentHarness(new URL("../src/components/BackfillControl.jsx", import.meta.url), {
    "@/lib/api": {getChatSettings: async () => ({translation_provider: "llm"}),
      translationPending: async () => ({tags: 3}), pendingGlossaryTranslations: async () => ({pending: {eligible_for_backfill: 101}}),
      backfillTranslations: async () => ({result: {tags: {created: 1, updated: 2, skipped_locked: 3, failed: 4}}}),
      listGlossary: async ({offset}) => {pages.push(offset); return {terms: terms.slice(offset, offset + 100), total: 101};},
      backfillGlossaryTranslations: async (_locale, batch) => {batches.push(batch); return {created: batch.length, skipped_locked: 0};}},
    "@/lib/glossaryUi.mjs": {chunkTermIds}, "@/lib/apiToast.mjs": {apiToast: () => ({})},
    "@/i18n/LocaleContext": {useI18n: () => ({t})}, "./Toast": {useToast: () => ({showToast() {}})},
  }, {locale: "en", onBusyChange: value => busy.push(value)});
  try {
    ui.render(); await flush(); ui.render();
    const pending = ui.find(node => node.type === "button").props.onClick(); ui.render();
    assert.equal(ui.find(node => node.type === "input").props.disabled, true);
    await pending; ui.render();
    assert.deepEqual(pages, [0, 100]);
    assert.equal(batches.flat().length, 101);
    assert.ok(ui.find(node => node.type === "p" && typeof node.props.children === "string" && node.props.children.includes('"skipped":3')));
    assert.ok(busy.includes(true));
  } finally {ui.dispose();}
});
