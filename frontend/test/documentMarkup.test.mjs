import assert from "node:assert/strict";
import test from "node:test";
import { markupDraft, reconcileMarkupRecovery, saveDocumentMarkup } from "../src/lib/documentMarkup.mjs";

const initial = { id: "doc-1", tags: ["ЭЛН", "SAP"], development_id: 7, source_locale: "ru" };

function harness({ fail, tagDevelopment = 7, warning = false } = {}) {
  const calls = [];
  let document = { ...initial, tags: [...initial.tags] };
  const write = async (field, args, values) => {
    calls.push([field, ...args]);
    if (fail === field) throw new Error("offline");
    document = { ...document, ...values };
    return { ...document, dev_tags_sync_pending: warning && field === "tags" };
  };
  return {
    calls,
    api: {
      updateDocumentTags: (id, tags, locale) => write("tags", [id, tags, locale], { tags, development_id: tagDevelopment }),
      setDocumentDevelopment: (id, developmentId, confirmed) => write("development", [id, developmentId, confirmed], { development_id: developmentId }),
      setDocumentSourceLocale: (id, locale) => write("source_locale", [id, locale], { source_locale: locale }),
    },
  };
}

test("draft preserves unknown values and separates the tag language", () => {
  assert.deepEqual(markupDraft({ tags: ["Тег"], development_id: 99, source_locale: "xx" }, "en"),
    { tags: ["Тег"], developmentId: 99, sourceLocale: "xx", canonicalLocale: "en" });
  assert.deepEqual(markupDraft({}, "ru"), { tags: [], developmentId: null, sourceLocale: null, canonicalLocale: "ru" });
});

test("unchanged markup and reordered tags make no writes", async () => {
  const { api, calls } = harness();
  const result = await saveDocumentMarkup({ docId: initial.id, baseline: initial,
    draft: { ...markupDraft(initial, "en"), tags: ["SAP", "ЭЛН"] }, api });
  assert.deepEqual(calls, []);
  assert.deepEqual(result.applied, []);
  assert.equal(result.error, null);
});

test("writes canonical tags first, explicit development second, document language last", async () => {
  const { api, calls } = harness({ tagDevelopment: 90 });
  const applied = [];
  const result = await saveDocumentMarkup({ docId: initial.id, baseline: initial,
    draft: { tags: ["Новый тег"], canonicalLocale: "de", developmentId: 12, sourceLocale: "en" },
    developmentTouched: true, api, onApplied: (change) => applied.push(change.field) });
  assert.deepEqual(calls, [["tags", "doc-1", ["Новый тег"], "de"], ["development", "doc-1", 12, true], ["source_locale", "doc-1", "en"]]);
  assert.deepEqual(result.applied, applied);
  assert.equal(result.document.development_id, 12);
});

test("explicitly choosing the previous development overrides tag reconciliation", async () => {
  const { api, calls } = harness({ tagDevelopment: 90 });
  await saveDocumentMarkup({ docId: initial.id, baseline: initial,
    draft: { ...markupDraft(initial, "ru"), tags: ["90"] }, developmentTouched: true, api });
  assert.deepEqual(calls.at(-1), ["development", "doc-1", 7, true]);
});

test("untouched development keeps the server's automatic tag reconciliation", async () => {
  const { api, calls } = harness({ tagDevelopment: 90 });
  const result = await saveDocumentMarkup({ docId: initial.id, baseline: initial,
    draft: { ...markupDraft(initial, "ru"), tags: ["90"] }, api });
  assert.equal(calls.length, 1);
  assert.equal(result.document.development_id, 90);
});

test("clears tags, development and source locale using the API's empty values", async () => {
  const { api, calls } = harness();
  await saveDocumentMarkup({ docId: initial.id, baseline: initial,
    draft: { tags: [], canonicalLocale: "ru", developmentId: null, sourceLocale: null }, developmentTouched: true, api });
  assert.deepEqual(calls, [["tags", "doc-1", [], "ru"], ["development", "doc-1", null, false], ["source_locale", "doc-1", null]]);
});

test("partial failure stops later writes and retains confirmed changes and warnings", async () => {
  const { api, calls } = harness({ fail: "development", warning: true });
  const result = await saveDocumentMarkup({ docId: initial.id, baseline: initial,
    draft: { tags: ["Новый"], canonicalLocale: "ru", developmentId: 12, sourceLocale: "en" }, developmentTouched: true, api });
  assert.deepEqual(result.applied, ["tags"]);
  assert.deepEqual(result.warnings, ["dev_tags_sync_pending"]);
  assert.equal(result.failedField, "development");
  assert.equal(result.error.message, "offline");
  assert.equal(result.document.tags[0], "Новый");
  assert.equal(calls.length, 2);
});

test("retry with the acknowledged baseline does not repeat the saved tags", async () => {
  const draft = { tags: ["Новый"], canonicalLocale: "ru", developmentId: 12, sourceLocale: "en" };
  const first = harness({ fail: "development" });
  const result = await saveDocumentMarkup({ docId: initial.id, baseline: initial, draft, developmentTouched: true, api: first.api });
  const retry = harness();
  await saveDocumentMarkup({ docId: initial.id, baseline: result.document, draft, developmentTouched: true, api: retry.api });
  assert.deepEqual(retry.calls.map(([field]) => field), ["development", "source_locale"]);
});

test("the operation snapshot survives server responses changing other fields", async () => {
  const { api, calls } = harness({ tagDevelopment: 12 });
  const draft = { tags: ["Новый"], canonicalLocale: "ru", developmentId: 12, sourceLocale: "en" };
  await saveDocumentMarkup({ docId: initial.id, baseline: initial, draft, developmentTouched: true, api,
    onApplied: () => { draft.developmentId = 99; draft.sourceLocale = "de"; } });
  assert.deepEqual(calls.at(-2), ["development", "doc-1", 12, true]);
  assert.deepEqual(calls.at(-1), ["source_locale", "doc-1", "en"]);
});

test("successful readback after a lost development response clears the pending choice", () => {
  const draft = { ...markupDraft(initial, "ru"), developmentId: 12, sourceLocale: "en" };
  const current = { ...initial, development_id: 12, development_confirmed_by: "editor" };
  const recovery = reconcileMarkupRecovery({ document: current, draft, failedField: "development", developmentTouched: true });
  assert.equal(recovery.developmentTouched, false);
  assert.deepEqual(recovery.applied, ["development"]);
  assert.equal(recovery.draft.sourceLocale, "en");
});

test("readback does not acknowledge an unconfirmed development or discard an explicit override", () => {
  const draft = { ...markupDraft(initial, "ru"), tags: ["12"], developmentId: 12 };
  const recovery = reconcileMarkupRecovery({ document: { ...initial, tags: ["12"], development_id: 12 },
    draft, failedField: "development", developmentTouched: true });
  assert.equal(recovery.developmentTouched, true);
  assert.deepEqual(recovery.applied, []);
});

test("lost tag response readback acknowledges tags and adopts automatic development only if untouched", () => {
  const document = { ...initial, tags: ["12"], development_id: 12 };
  const draft = { ...markupDraft(initial, "ru"), tags: ["12"] };
  const automatic = reconcileMarkupRecovery({ document, draft, failedField: "tags", developmentTouched: false });
  assert.deepEqual(automatic.applied, ["tags"]);
  assert.equal(automatic.draft.developmentId, 12);
  const explicit = reconcileMarkupRecovery({ document, draft, failedField: "tags", developmentTouched: true });
  assert.equal(explicit.draft.developmentId, 7);
  assert.equal(explicit.developmentTouched, true);
});

test("readback acknowledges disassociation and manual locale but keeps failed edits", () => {
  const draft = { ...markupDraft(initial, "ru"), developmentId: null, sourceLocale: "en" };
  assert.deepEqual(reconcileMarkupRecovery({ document: { ...initial, development_id: null }, draft,
    failedField: "development", developmentTouched: true }).applied, ["development"]);
  assert.deepEqual(reconcileMarkupRecovery({ document: { ...initial, source_locale: "en", source_locale_source: "manual" },
    draft, failedField: "source_locale" }).applied, ["source_locale"]);
  const failed = reconcileMarkupRecovery({ document: initial, draft, failedField: "source_locale" });
  assert.deepEqual(failed.applied, []);
  assert.equal(failed.draft.sourceLocale, "en");
});
