import { test } from "node:test";
import assert from "node:assert/strict";
import {
  effectiveDictionary,
  ruSourceDictionary,
  exportFilename,
} from "../src/lib/uiDictExport.mjs";

test("effectiveDictionary: override поверх en, ru-fallback для непереведённого", () => {
  // Ключ из en (nav.documents) и ключ, присутствующий в ru, но отсутствующий в en
  // (status.uploaded) — fallback даёт ru-строку.
  const eff = effectiveDictionary("en", { "nav.documents": "Papers" });
  assert.equal(eff["nav.documents"], "Papers");
  assert.equal(typeof eff["status.uploaded"], "string");
  // ru-ключ без en-перевода виден через fallback и НЕ равен undefined.
  assert.ok(eff["nav.documents"] !== undefined);
});

test("effectiveDictionary: без override — встроенный en", () => {
  const eff = effectiveDictionary("en", null);
  assert.equal(eff["nav.documents"], "Documents");
});

test("ruSourceDictionary: полный ru (nav.documents по-русски)", () => {
  assert.equal(ruSourceDictionary()["nav.documents"], "Документы");
});

test("exportFilename: содержит локаль и дату ISO", () => {
  const f = exportFilename("en", "effective");
  assert.match(f, /^ui-dictionary-en-effective-\d{4}-\d{2}-\d{2}\.json$/);
  assert.match(exportFilename("en", "ru-source"), /ru-source-for-en-/);
  assert.match(exportFilename("en", "override"), /-override-/);
});
