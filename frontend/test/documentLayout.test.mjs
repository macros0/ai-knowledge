import test from "node:test";
import assert from "node:assert/strict";
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
