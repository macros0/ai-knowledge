import { test } from "node:test";
import assert from "node:assert/strict";
import { filterDocuments, globToRegExp, sortDocuments, SORT_OPTIONS } from "../src/lib/docFilter.mjs";

function ids(docs) {
  return docs.map((d) => d.id);
}

test("globToRegExp: * и ? превращаются в wildcard, остальное экранируется", () => {
  assert.equal(globToRegExp("a*").test("abc"), true);
  assert.equal(globToRegExp("a?c").test("abc"), true);
  assert.equal(globToRegExp("a?c").test("ac"), false);
  assert.equal(globToRegExp("a.b").test("axb"), false);
});

test("sortDocuments: date_desc — новые сначала, date_asc — наоборот", () => {
  const docs = [
    { id: "old", filename: "old.docx", created_at: "2026-01-01T00:00:00Z" },
    { id: "new", filename: "new.docx", created_at: "2026-06-01T00:00:00Z" },
    { id: "mid", filename: "mid.docx", created_at: "2026-03-01T00:00:00Z" },
  ];
  assert.deepEqual(ids(sortDocuments(docs, SORT_OPTIONS.date_desc)), ["new", "mid", "old"]);
  assert.deepEqual(ids(sortDocuments(docs, SORT_OPTIONS.date_asc)), ["old", "mid", "new"]);
});

test("sortDocuments: name_asc/name_desc — localeCompare без учёта регистра (кириллица)", () => {
  const docs = [
    { id: "g", filename: "Гамма.docx" },
    { id: "a", filename: "альфа.docx" },
    { id: "b", filename: "Бета.docx" },
  ];
  assert.deepEqual(ids(sortDocuments(docs, SORT_OPTIONS.name_asc)), ["a", "b", "g"]);
  assert.deepEqual(ids(sortDocuments(docs, SORT_OPTIONS.name_desc)), ["g", "b", "a"]);
});

test("sortDocuments: uploader — null всегда в конец независимо от направления", () => {
  const docs = [
    { id: "admin", uploaded_by: "demo.admin", created_at: "2026-01-01T00:00:00Z" },
    { id: "none", uploaded_by: null, created_at: "2026-01-02T00:00:00Z" },
    { id: "editor", uploaded_by: "demo.editor", created_at: "2026-01-03T00:00:00Z" },
  ];
  assert.deepEqual(ids(sortDocuments(docs, SORT_OPTIONS.uploader_asc)), ["admin", "editor", "none"]);
  assert.deepEqual(ids(sortDocuments(docs, SORT_OPTIONS.uploader_desc)), ["editor", "admin", "none"]);
});

test("sortDocuments: tie-breaker по created_at desc при равных значениях поля", () => {
  const docs = [
    { id: "a1", uploaded_by: "demo.editor", created_at: "2026-01-01T00:00:00Z" },
    { id: "a2", uploaded_by: "demo.editor", created_at: "2026-06-01T00:00:00Z" },
    { id: "a3", uploaded_by: "demo.editor", created_at: "2026-03-01T00:00:00Z" },
  ];
  // все равны по uploader → порядок определяется только датой (новые сначала),
  // а не исходным порядком массива.
  assert.deepEqual(ids(sortDocuments(docs, SORT_OPTIONS.uploader_asc)), ["a2", "a3", "a1"]);
});

test("sortDocuments: не мутирует входной массив", () => {
  const docs = [
    { id: "old", created_at: "2026-01-01T00:00:00Z" },
    { id: "new", created_at: "2026-06-01T00:00:00Z" },
  ];
  const before = ids(docs);
  sortDocuments(docs, SORT_OPTIONS.date_desc);
  assert.deepEqual(ids(docs), before);
});

test("filterDocuments: маска по имени файла", () => {
  const docs = [
    { filename: "report.docx" },
    { filename: "notes.docx" },
    { filename: "report_final.docx" },
  ];
  assert.equal(filterDocuments(docs, { mask: "report*" }).length, 2);
  assert.equal(filterDocuments(docs, { mask: "report.docx" }).length, 1);
});
