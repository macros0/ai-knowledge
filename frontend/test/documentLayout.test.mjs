import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import * as documentLayout from "../src/lib/documentLayout.mjs";
import { buildCompactDocumentMeta, countActiveDocumentFilters } from "../src/lib/documentLayout.mjs";

test("partial generation action uses resume and only appears for recoverable completed documents", async () => {
  const source = await readFile(new URL("../src/components/DocumentList.jsx", import.meta.url), "utf8");
  assert.match(source, /doc\.status === "done" && doc\.partial_chunks\?\.length > 0/);
  assert.match(source, /docs\.repairPartialBtn/);
});

test("attachment-only source warning uses a compact icon with a tooltip", async () => {
  const source = await readFile(new URL("../src/components/DocumentList.jsx", import.meta.url), "utf8");
  assert.match(source, /doc\.problem === "attachment_partial_result"/);
  assert.match(source, /className="doc-source-warning-icon"/);
  assert.match(source, /title=\{problemText\}/);
  assert.match(source, /aria-label=\{problemText\}/);
});

test("a detected development is an actionable clarification choice", async () => {
  const list = await readFile(new URL("../src/components/DocumentList.jsx", import.meta.url), "utf8");
  const picker = await readFile(new URL("../src/components/DevelopmentPicker.jsx", import.meta.url), "utf8");

  assert.match(list, /suggestion=\{doc\.development_suggestion\}/);
  assert.doesNotMatch(list, /doc\.development_suggestion && !doc\.development_id && \(\s*<span className="dev-suggestion">/);
  assert.match(picker, /dev-picker-trigger\$\{suggestion \? " dev-picker-trigger-suggestion"/);
  assert.match(picker, /setQuery\(suggestionNumber\)/);
});

test("document source tree receives a highlighted source to expand its ancestors", async () => {
  const listPage = await readFile(new URL("../src/app/documents/[docId]/okf/page.js", import.meta.url), "utf8");
  const sources = await readFile(new URL("../src/components/DocumentSources.jsx", import.meta.url), "utf8");
  assert.match(listPage, /focusedSourceId=\{focusedSourceId\}/);
  assert.match(sources, /sourceAncestors\(loadedSources, focusedSourceId\)/);
  assert.match(sources, /visibleSourceIds\(sources, expandedSourceIds\)/);
});

test("full document keeps one scrollable area around collapsible source content", async () => {
  const fulltext = await readFile(new URL("../src/app/documents/[docId]/fulltext/page.js", import.meta.url), "utf8");
  const styles = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");
  assert.match(fulltext, /<SourceContentViewer/);
  assert.match(styles, /\.source-content-viewer\s*\{[^}]*flex:\s*1;[^}]*min-height:\s*0;[^}]*overflow-y:\s*auto;/);
});

test("plain chunk view uses the collapsible source content viewer", async () => {
  const chunkPage = await readFile(new URL("../src/app/documents/[docId]/chunks/[chunkIndex]/page.js", import.meta.url), "utf8");
  assert.match(chunkPage, /import SourceContentViewer from "@\/components\/SourceContentViewer"/);
  assert.match(chunkPage, /\/fulltext\/chunks/);
  assert.match(chunkPage, /\/sources/);
  assert.match(chunkPage, /<SourceContentViewer[\s\S]*chunks=\{\[chunk\]\}/);
});

test("compact document metadata omits empty optional fields", () => {
  const meta = buildCompactDocumentMeta({
    progress: "Генерация чанка 1 из 1",
    tags: [],
    development: null,
    locale: null,
    uploader: "demo.admin",
    date: "12.09.2026",
  });

  assert.deepEqual(meta, [
    { key: "progress", value: "Генерация чанка 1 из 1" },
    { key: "uploader", value: "demo.admin" },
    { key: "date", value: "12.09.2026" },
  ]);
});

test("compact document metadata preserves tags, development and locale", () => {
  const meta = buildCompactDocumentMeta({
    progress: null,
    tags: ["SAP", "review"],
    development: "0706",
    locale: "ru",
    uploader: null,
    date: null,
  });

  assert.deepEqual(meta, [
    { key: "tags", value: "SAP, review" },
    { key: "development", value: "0706" },
    { key: "locale", value: "RU" },
  ]);
});

test("active filter count ignores default values", () => {
  assert.equal(countActiveDocumentFilters({
    problemOnly: false,
    module: "",
    development: "0706",
    tag: "",
    locale: "ru",
    dateFrom: "",
    dateTo: "",
  }), 2);
});

test("active filter count includes primary filters", () => {
  assert.equal(countActiveDocumentFilters({
    search: "contract",
    uploader: "",
    status: "done",
    problemOnly: false,
    module: "",
    development: null,
  }), 2);
});

test("resetDocumentFilters clears every filter and selects all uploaders", () => {
  assert.equal(typeof documentLayout.resetDocumentFilters, "function");
  assert.deepEqual(documentLayout.resetDocumentFilters(), {
    searchInput: "",
    search: "",
    chosenUploader: "",
    problemOnly: false,
    moduleFilter: "",
    devFilter: null,
    tagFilter: "",
    statusFilter: "",
    dateFrom: "",
    dateTo: "",
    localeFilter: "",
    page: 0,
  });
});

test("documents filter button counts primary filters so reset stays available", async () => {
  const source = await readFile(new URL("../src/components/DocumentList.jsx", import.meta.url), "utf8");
  const countBlock = source.match(/const activeFilterCount = countActiveDocumentFilters\(\{([\s\S]*?)\}\);/)?.[1] ?? "";

  assert.match(countBlock, /search:/);
  assert.match(countBlock, /uploader: selectedUploader/);
  assert.match(countBlock, /status:/);
  assert.match(source, /disabled=\{activeFilterCount === 0 && chosenUploader !== null\}/);
});

test("document cards place file metadata below the title", async () => {
  const styles = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");
  const baseStyles = styles.split("@media", 1)[0];
  assert.match(baseStyles, /\.doc-primary\s*\{[\s\S]*?display:\s*contents/);
  assert.match(baseStyles, /\.doc-primary-meta\s*\{[\s\S]*?grid-row:\s*2/);
});

test("document card metadata spans beneath the action controls", async () => {
  const styles = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");
  const baseStyles = styles.split("@media", 1)[0];
  assert.match(baseStyles, /\.document-item\s*\{[\s\S]*?display:\s*grid/);
  assert.match(baseStyles, /\.doc-main\s*\{[\s\S]*?display:\s*contents/);
  assert.match(baseStyles, /\.doc-primary\s*\{[\s\S]*?display:\s*contents/);
  assert.match(baseStyles, /\.doc-primary-meta\s*\{[\s\S]*?grid-column:\s*2\s*\/\s*-1/);
  assert.match(baseStyles, /\.doc-meta-line\s*\{[\s\S]*?grid-column:\s*2\s*\/\s*-1/);
});
