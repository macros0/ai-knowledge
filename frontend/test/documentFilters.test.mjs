import assert from "node:assert/strict";
import test from "node:test";
import * as filters from "../src/lib/documentLayout.mjs";

test("returning to documents restores the saved filters when the URL has no filters", () => {
  assert.equal(typeof filters.resolveDocumentFilterParams, "function");
  const params = filters.resolveDocumentFilterParams(new URLSearchParams(), "q=SAP&uploader=&status=done&locale=ru&module=PY&dev=18&tag=review&problem=1&from=2026-09-01&to=2026-09-30&sort=name_asc&group=development");
  assert.equal(params.get("q"), "SAP");
  assert.equal(params.get("uploader"), "");
  assert.equal(params.get("status"), "done");
  assert.equal(params.get("locale"), "ru");
  assert.equal(params.get("module"), "PY");
  assert.equal(params.get("dev"), "18");
  assert.equal(params.get("tag"), "review");
  assert.equal(params.get("problem"), "1");
  assert.equal(params.get("from"), "2026-09-01");
  assert.equal(params.get("to"), "2026-09-30");
  assert.equal(params.get("sort"), "name_asc");
  assert.equal(params.get("group"), "development");
});

test("an explicit document filter link takes precedence over the saved view", () => {
  assert.equal(typeof filters.resolveDocumentFilterParams, "function");
  const params = filters.resolveDocumentFilterParams(new URLSearchParams("tag=review"), "q=SAP&status=done&locale=ru");
  assert.equal(params.toString(), "tag=review");
});

test("upload-prefill links restore filters without treating upload parameters as filters", () => {
  assert.equal(typeof filters.resolveDocumentFilterParams, "function");
  const params = filters.resolveDocumentFilterParams(new URLSearchParams("upload_dev=18&upload_module=PY"), "status=done");
  assert.equal(params.toString(), "status=done");
});

test("a reset view stays cleared on return including the uploader default", () => {
  assert.equal(typeof filters.resolveDocumentFilterParams, "function");
  const params = filters.resolveDocumentFilterParams(new URLSearchParams(), "uploader=");
  assert.equal(params.get("uploader"), "");
  assert.equal(params.get("status"), null);
  assert.equal(params.get("q"), null);
});

test("returning from trash uses the latest input rather than the list's stale debounce URL", () => {
  const params = filters.resolveDocumentFilterParams(new URLSearchParams("q=OLD&status=done"), "q=NEW&status=done", "q=OLD&status=done");
  assert.equal(params.get("q"), "NEW");
  assert.equal(params.get("status"), "done");
});

test("clearing search immediately before opening trash does not restore the old search", () => {
  const params = filters.resolveDocumentFilterParams(new URLSearchParams("q=OLD&status=done"), "status=done", "q=OLD&status=done");
  assert.equal(params.get("q"), null);
  assert.equal(params.get("status"), "done");
});

test("a new explicit filter URL still overrides an earlier list URL and saved input", () => {
  const params = filters.resolveDocumentFilterParams(new URLSearchParams("q=LINK"), "q=NEW&status=done", "q=OLD&status=done");
  assert.equal(params.toString(), "q=LINK");
});
