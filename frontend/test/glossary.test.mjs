import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  appliedTermsSummary,
  buildGlossaryListParams,
  chunkTermIds,
  glossaryTermDisplay,
  glossarySystemRuleForKind,
  hasPendingGlossaryAlias,
  isCurrentGlossaryResponse,
  keepGlossarySelection,
  translationState,
} from "../src/lib/glossaryUi.mjs";

test("infotype system rule vocabulary is supplied by the server", () => {
  assert.equal(glossarySystemRuleForKind("sap_infotype"), null);
  assert.equal(glossarySystemRuleForKind("sap_transaction"), null);
});

test("chat summarizes an infotype rule without listing generated forms as aliases", () => {
  assert.deepEqual(appliedTermsSummary([{
    canonical: "IT0003",
    display_name: "Payroll Status",
    matched_texts: ["инфо-типа 0003"],
    added_forms: ["IT0003", "инфотип инфотипа инфотипе 0003"],
    saved_alias_forms: ["Payroll Status"],
    system_rule: "sap_infotype",
  }]), [{
    canonical: "IT0003",
    matched: ["инфо-типа 0003"],
    added: ["Payroll Status"],
    displayName: "Payroll Status",
    systemRule: "sap_infotype",
  }]);
});

test("detects a typed new glossary alias before the add action", () => {
  assert.equal(hasPendingGlossaryAlias({ alias: "  новый алиас  " }), true);
  assert.equal(hasPendingGlossaryAlias({ alias: "   " }), false);
  assert.equal(hasPendingGlossaryAlias(null), false);
});

test("buildGlossaryListParams keeps filters server-side and paginates", () => {
  assert.deepEqual(
    buildGlossaryListParams({ query: "pay", kind: "sap_infotype", enabled: true, needsReview: true, page: 2, pageSize: 50 }),
    { q: "pay", kind: "sap_infotype", enabled: true, needs_review: true, limit: 50, offset: 50 },
  );
});

test("chunkTermIds creates sequential backfill batches of ten", () => {
  assert.deepEqual(chunkTermIds([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]), [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10], [11]]);
});

test("translationState distinguishes missing, machine and stale human text", () => {
  const term = { canonical_locale: "de", source_revision: 3, translations: [] };
  assert.equal(translationState(term, "ru").kind, "missing");
  assert.equal(translationState({ ...term, translations: [{ locale: "ru", source_revision: 3, version: 1, is_machine_translated: true } ] }, "ru").kind, "machine_unreviewed");
  assert.equal(translationState({ ...term, translations: [{ locale: "ru", source_revision: 2, version: 2, is_machine_translated: false, reviewed_by: "editor" } ] }, "ru").kind, "human_stale");
});

test("late glossary responses never replace the selected term", () => {
  assert.equal(isCurrentGlossaryResponse("term-2", "term-1"), false);
  assert.equal(isCurrentGlossaryResponse("term-2", "term-2"), true);
  assert.deepEqual(keepGlossarySelection({ id: "term-2" }, { id: "term-1" }), { id: "term-2" });
  assert.deepEqual(keepGlossarySelection({ id: "term-1" }, { id: "term-1", version: 2 }), { id: "term-1", version: 2 });
  assert.deepEqual(keepGlossarySelection(null, { id: "term-1" }), { id: "term-1" });
});

test("glossaryTermDisplay hides the technical identifier", () => {
  assert.deepEqual(glossaryTermDisplay({ canonical: "IT0003", original_name: "Payroll Status", canonical_locale: "und" }), {
    name: "Payroll Status",
    locale: "und",
  });
});

test("stage 7 UI files use the shared locale selector and history metadata", () => {
  const editor = readFileSync(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), "utf8");
  const history = readFileSync(new URL("../src/components/ChatHistoryShared.jsx", import.meta.url), "utf8");
  assert.match(editor, /ReferenceLocaleSelect/);
  assert.match(history, /AppliedTerms/);
});

test("admin glossary panel keeps long editor content vertically scrollable", () => {
  const panel = readFileSync(new URL("../src/components/GlossaryPanel.jsx", import.meta.url), "utf8");
  const styles = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
  assert.match(panel, /className=\"panel admin-panel glossary-panel\"/);
  assert.match(styles, /\.admin-panel\s*\{[^}]*overflow-y:\s*auto/);
});

test("changing a term source locale remounts its translation editor", () => {
  const editor = readFileSync(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), "utf8");
  assert.match(editor, /<GlossaryTranslations\s+key=\{`\$\{term\.id\}:\$\{term\.canonical_locale\}`\}/);
});

test("alias locale is editable through the shared locale selector", () => {
  const editor = readFileSync(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), "utf8");
  assert.match(editor, /<ReferenceLocaleSelect\s+value=\{draft\.locale\}/);
  assert.match(editor, /onChange=\{\(locale\) => updateAliasDraft\(alias\.id, \{ locale \}\)\}/);
  assert.match(editor, /labelKey="admin\.glossary\.aliasLocale"/);
});

test("new alias locale uses the same shared locale selector", () => {
  const editor = readFileSync(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), "utf8");
  assert.match(editor, /<ReferenceLocaleSelect\s+value=\{aliasDraft\.locale \|\| locale\}/);
  assert.match(editor, /onChange=\{\(locale\) => setAliasDraft\(\(v\) => \(\{ \.\.\.v, locale \}\)\)\}/);
  assert.doesNotMatch(editor, /placeholder="locale"/);
});

test("new glossary term locale uses the shared locale selector", () => {
  const panel = readFileSync(new URL("../src/components/GlossaryPanel.jsx", import.meta.url), "utf8");
  assert.match(panel, /<ReferenceLocaleSelect\s+value=\{newTerm\.canonical_locale\}/);
  assert.match(panel, /onChange=\{\(canonicalLocale\) => setNewTerm\(\(v\) => \(\{ \.\.\.v, canonical_locale: canonicalLocale \}\)\)\}/);
  assert.doesNotMatch(panel, /placeholder=\{t\("admin\.glossary\.localePlaceholder"\)\}/);
});

test("unknown alias locale is not displayed as the UI locale", () => {
  const selector = readFileSync(new URL("../src/components/ReferenceLocaleSelect.jsx", import.meta.url), "utf8");
  assert.match(selector, /const selectedLocale = value \|\| "und";/);
});

test("new reference forms default to the current UI locale", () => {
  const developmentPanel = readFileSync(new URL("../src/components/DevelopmentPanel.jsx", import.meta.url), "utf8");
  const documentsPanel = readFileSync(new URL("../src/components/DocumentsPanel.jsx", import.meta.url), "utf8");
  const selectionBar = readFileSync(new URL("../src/components/SelectionBar.jsx", import.meta.url), "utf8");

  assert.match(developmentPanel, /const \[originLocale, setOriginLocale\] = useState\(locale\);/);
  assert.match(developmentPanel, /const \[moduleLocale, setModuleLocale\] = useState\(locale\);/);
  assert.match(documentsPanel, /const \[tagLocale, setTagLocale\] = useState\(locale\);/);
  assert.match(selectionBar, /const \[tagLocale, setTagLocale\] = useState\(locale\);/);
});

test("glossary source fields and enabled state share an explicit save", () => {
  const editor = readFileSync(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), "utf8");
  assert.match(editor, /updateGlossarySource\(term\.id, \{[^}]*canonical_locale: canonicalLocale, enabled[, }]/s);
  assert.match(editor, /onChange=\{\(e\) => setEnabled\(e\.target\.checked\)\}/);
  assert.doesNotMatch(editor, /const toggleEnabled/);
});

test("glossary editor explains which sections need explicit saving", () => {
  const editor = readFileSync(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), "utf8");
  const translations = readFileSync(new URL("../src/components/GlossaryTranslations.jsx", import.meta.url), "utf8");
  assert.match(editor, /admin\.glossary\.sourceSaveHint/);
  assert.match(editor, /admin\.glossary\.aliasSaveHint/);
  assert.match(translations, /admin\.glossary\.translationSaveHint/);
});

test("existing alias fields are drafts until their row is explicitly saved", () => {
  const editor = readFileSync(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), "utf8");
  assert.match(editor, /const \[aliasDrafts, setAliasDrafts\]/);
  assert.match(editor, /value=\{draft\.alias\}/);
  assert.match(editor, /onClick=\{\(\) => saveAlias\(alias\)\}/);
  assert.doesNotMatch(editor, /onBlur=\{\(e\) => canManage && .*saveAlias/);
});

test("new alias draft is cleared only after a successful save", () => {
  const editor = readFileSync(new URL("../src/components/GlossaryEditor.jsx", import.meta.url), "utf8");
  assert.match(editor, /return true;\s*\}\s*catch \(err\)/s);
  assert.match(editor, /return false;\s*\}\s*finally/);
  assert.match(editor, /const saved = await apply\(\(\) => addGlossaryAlias/);
  assert.match(editor, /if \(saved\) setAliasDraft\(emptyAlias\)/);
});

test("chat explains when the global glossary expansion flag is disabled", () => {
  const chat = readFileSync(new URL("../src/components/ChatPanel.jsx", import.meta.url), "utf8");
  assert.match(chat, /settings\.glossary_query_expansion_enabled/);
  assert.match(chat, /chat\.glossary\.disabled/);
  assert.match(chat, /disabled=\{!settings\.glossary_query_expansion_enabled\}/);
});

test("admin glossary explains when saved terms are not used by search", () => {
  const panel = readFileSync(new URL("../src/components/GlossaryPanel.jsx", import.meta.url), "utf8");
  assert.match(panel, /getChatSettings/);
  assert.match(panel, /searchSettings\??\.glossary_query_expansion_enabled/);
  assert.match(panel, /admin\.glossary\.expansionDisabled/);
});

test("layout exposes the active knowledge profile from runtime settings", () => {
  const layout = readFileSync(new URL("../src/app/layout.js", import.meta.url), "utf8");
  const profile = readFileSync(new URL("../src/components/KnowledgeProfile.jsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
  assert.match(layout, /<KnowledgeProfile\s*\/>/);
  assert.match(profile, /knowledge_profile/);
  assert.match(profile, /glossary_query_expansion_enabled/);
  assert.match(profile, /role="status"/);
  assert.match(profile, /nav\.knowledgeProfileTitle/);
  assert.match(profile, /nav\.glossaryEnabled/);
  assert.match(profile, /nav\.glossaryDisabled/);
  assert.match(profile, /nav\.glossaryUnknown/);
  assert.match(css, /\.topbar\s*\{[\s\S]*?flex-wrap:\s*wrap/);
  assert.match(css, /\.brand\s*\{[\s\S]*?min-width:\s*0/);
});

test("glossary warning has its own explanatory translation key", () => {
  const ru = readFileSync(new URL("../src/i18n/locales/ru.js", import.meta.url), "utf8");
  const en = readFileSync(new URL("../src/i18n/locales/en.js", import.meta.url), "utf8");
  assert.equal((ru.match(/"admin\.glossary\.disabled"\s*:/g) || []).length, 1);
  assert.equal((en.match(/"admin\.glossary\.disabled"\s*:/g) || []).length, 1);
  assert.match(ru, /"admin\.glossary\.expansionDisabled"\s*:\s*".*глоссарий.*поиске/);
  assert.match(en, /"admin\.glossary\.expansionDisabled"\s*:\s*".*glossary.*search/);
});
