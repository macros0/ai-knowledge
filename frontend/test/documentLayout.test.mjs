import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import * as documentLayout from "../src/lib/documentLayout.mjs";
import { buildCompactDocumentMeta, countActiveDocumentFilters } from "../src/lib/documentLayout.mjs";

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
  assert.match(countBlock, /uploader:/);
  assert.match(countBlock, /status:/);
  assert.match(source, /disabled=\{activeFilterCount === 0 && chosenUploader !== null\}/);
});
