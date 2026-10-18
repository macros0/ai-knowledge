import test from "node:test";
import assert from "node:assert/strict";
import * as helpers from "../src/lib/glossaryConflicts.mjs";
import { conflictSummary, normalizeGlossaryIdentity, resolveRejectedAlias, shouldOfferMerge, buildGlossaryMergeRequest, glossaryDraftPayload } from "../src/lib/glossaryConflicts.mjs";

test("a removed remote alias retains a dirty local draft", () => {
  const previous = [{id: 7, alias: "Old", locale: "ru", auto_expand: false, search_enabled: false}];
  const dirty = {...previous[0], alias: "Mine"};
  const result = helpers.reconcileGlossaryAliasDrafts({7: dirty}, previous, []);
  assert.equal(result[7].alias, "Mine");
  assert.equal(helpers.hasPendingGlossaryEdits({aliases: []}, {}, result, {}), true);
  assert.deepEqual(helpers.reconcileGlossaryAliasDrafts({7: previous[0]}, previous, []), {});
});

test("identity conflicts are normalized and offer merge", () => {
  const error = { code: "glossary_identity_conflict", conflicts: [{ key_value: " Табель ", term_id: 4, field_name: "original_name" }] };
  assert.equal(normalizeGlossaryIdentity("  Табель  "), "табель");
  assert.equal(normalizeGlossaryIdentity("İT0003"), "it0003");
  assert.deepEqual(conflictSummary(error), [{ key: " Табель ", termId: 4, field: "original_name" }]);
  assert.equal(shouldOfferMerge(error), true);
});

test("declining a conflict clears only new input or restores saved value", () => {
  assert.equal(resolveRejectedAlias({ isNew: true, savedAlias: "", draftAlias: "IT0003" }), "");
  assert.equal(resolveRejectedAlias({ isNew: false, savedAlias: "старое", draftAlias: "дубль" }), "старое");
});

test("infotype draft carries an explicit number; other kinds clear it", () => {
  assert.equal(glossaryDraftPayload({kind: "sap_infotype", infotype_number: "0003", original_name: "Payroll"}).infotype_number, "0003");
  assert.equal(glossaryDraftPayload({kind: "business_term", infotype_number: "0003", original_name: "Payroll"}).infotype_number, null);
});

test("draft merge sends a draft and binds commit to the returned preview", () => {
  const draft = {kind: "business_term", original_name: "Draft"};
  const request = buildGlossaryMergeRequest({draft, target: {id: 5, version: 2}, requestId: "stable", selections: {original_name: "target"}, proposal: {digest: "digest", glossary_revision: 7, target: {id: 5, version: 3}}});
  assert.equal(request.source_term_id, undefined);
  assert.deepEqual(request.draft, draft);
  assert.equal(request.target_version, 3);
  assert.equal(request.preview_digest, "digest");
  assert.equal(request.expected_revision, 7);
  assert.equal(request.request_id, "stable");
});

test("saved-card merge carries its pending source edit with the saved version", () => {
  const sourceEdit = {kind: "business_term", original_name: "Source", aliases: [{alias: "Changed"}]};
  const request = buildGlossaryMergeRequest({source: {id: 1, version: 3}, sourceEdit,
    target: {id: 2, version: 4}, requestId: "stable", selections: {original_name: "target"}});
  assert.deepEqual(request.source_edit, sourceEdit);
  assert.equal(request.source_term_id, 1);
  assert.equal(request.source_version, 3);
});

test("merge preview cannot silently rebase an unsaved source edit", () => {
  const request = buildGlossaryMergeRequest({source: {id: 1, version: 3, reference_revision: 5},
    sourceEdit: {original_name: "My stale edit"}, target: {id: 2, version: 1, reference_revision: 1},
    proposal: {source: {id: 1, version: 4, reference_revision: 6}, target: {id: 2, version: 2, reference_revision: 2}, digest: "d", glossary_revision: 7}});
  assert.equal(request.source_version, 3);
  assert.equal(request.expected_reference_revisions[1], 5);
  assert.equal(request.expected_reference_revisions[2], 2);
});

test("pending disabled state survives source edit and requires an explicit merge choice", () => {
  const edit = glossaryDraftPayload({kind: "business_term", original_name: "Name", enabled: false});
  assert.equal(edit.enabled, false);
  assert.equal(helpers.defaultGlossaryMergeChoice(false, true), "");
  assert.equal(helpers.defaultGlossaryMergeChoice(true, false), "");
  assert.equal(helpers.defaultGlossaryMergeChoice(false, false), "target");
});

test("declining a create name conflict preserves an unrelated alias", () => {
  assert.equal(helpers.rejectConflictingCreateAlias("Separate", [{key: "name"}]), "Separate");
  assert.equal(helpers.rejectConflictingCreateAlias("  MY   Alias ", [{key: "my alias"}]), "");
});

test("refresh after saving one section preserves other source and alias edits", () => {
  const before = {original_name: "Saved", enabled: true, aliases: [{id: 1, alias: "One", locale: "en", auto_expand: false, search_enabled: true}]};
  const after = {...before, original_name: "Saved name", version: 2};
  const draft = {original_name: "Saved name", enabled: false};
  assert.deepEqual(helpers.reconcileGlossaryDraft(draft, before, after), {original_name: "Saved name", enabled: false});
  const rows = {1: {...before.aliases[0], alias: "Pending row"}};
  assert.equal(helpers.reconcileGlossaryAliasDrafts(rows, before.aliases, after.aliases)[1].alias, "Pending row");
  assert.equal(helpers.hasPendingGlossaryEdits(after, draft, rows, {}), true);
});

test("successful source save clears dirty state while alias flag edits remain guarded", () => {
  const term = {kind: "sap_transaction", original_name: "PA30", enabled: false, aliases: [{id: 1, alias: "Maintain", auto_expand: false, search_enabled: true}]};
  assert.equal(helpers.hasPendingGlossaryEdits(term, {kind: "sap_transaction", original_name: "PA30", enabled: false}, {1: term.aliases[0]}, {}), false);
  assert.equal(helpers.hasPendingGlossaryEdits(term, {}, {1: {...term.aliases[0], search_enabled: false}}, {}), true);
});

test("acknowledging a saved draft accepts server normalization but preserves newer typing", () => {
  const submitted = {original_name: " New ", original_description: "text"};
  const updated = {original_name: "New", original_description: "text"};
  assert.deepEqual(helpers.reconcileGlossaryDraft(submitted, submitted, updated), {original_name: "New", original_description: "text"});
  assert.deepEqual(helpers.reconcileGlossaryDraft({...submitted, original_description: "newer"}, submitted, updated), {original_name: "New", original_description: "newer"});
});

test("merge target search queries the full list and discards late responses", async () => {
  const pending = [];
  const search = helpers.createGlossaryTargetSearch((params) => new Promise((resolve) => pending.push({params, resolve})));
  const old = search("old");
  const current = search("outside current page");
  assert.deepEqual(pending[1].params, {q: "outside current page", limit: 50, offset: 0});
  pending[1].resolve({terms: [{id: 101}], total: 1});
  assert.deepEqual(await current, {terms: [{id: 101}], total: 1});
  pending[0].resolve({terms: [{id: 1}], total: 1});
  assert.equal(await old, null);
  const abandoned = search("other source");
  search.invalidate();
  pending[2].resolve({terms: [{id: 102}], total: 1});
  assert.equal(await abandoned, null);
});
