import { test } from "node:test";
import assert from "node:assert/strict";
import {
  KNOWN_SOURCE_LOCALES,
  buildLocaleOptions,
  facetOptions,
  languageLabel,
} from "../src/lib/sourceLocales.mjs";

test("KNOWN_SOURCE_LOCALES — lowercase ISO-коды без дублей", () => {
  assert.ok(KNOWN_SOURCE_LOCALES.length >= 30);
  const set = new Set(KNOWN_SOURCE_LOCALES);
  assert.equal(set.size, KNOWN_SOURCE_LOCALES.length, "есть дубликаты");
  for (const code of KNOWN_SOURCE_LOCALES) {
    assert.match(code, /^[a-z]{2}$/, `некорректный код: ${code}`);
  }
});

test("без текущего значения и активных — полный известный список", () => {
  const opts = buildLocaleOptions(null, [], "en");
  assert.equal(opts.length, KNOWN_SOURCE_LOCALES.length);
  assert.ok(opts.every((o) => o.code && o.label));
});

test("текущее значение вне списков всё равно включено первым", () => {
  const opts = buildLocaleOptions("zz", [], "en");
  assert.equal(opts[0].code, "zz");
  assert.ok(opts.some((o) => o.code === "zz"));
});

test("текущее значение дедуплицируется с известным списком", () => {
  const opts = buildLocaleOptions("ru", [], "en");
  assert.equal(opts[0].code, "ru");
  const ruCount = opts.filter((o) => o.code === "ru").length;
  assert.equal(ruCount, 1);
  assert.equal(opts.length, KNOWN_SOURCE_LOCALES.length);
});

test("активная локаль получает имя с бэкенда и не дублируется", () => {
  const opts = buildLocaleOptions(null, [{ code: "ru", name: "Русский" }], "en");
  const ru = opts.find((o) => o.code === "ru");
  assert.equal(ru.label, "Русский");
  assert.equal(opts.filter((o) => o.code === "ru").length, 1);
});

test("активная локаль вне известного списка включается", () => {
  const opts = buildLocaleOptions(null, [{ code: "eo", name: "Esperanto" }], "en");
  assert.ok(opts.some((o) => o.code === "eo" && o.label === "Esperanto"));
});

test("languageLabel возвращает локализованное имя", () => {
  assert.equal(languageLabel("ru", "en"), "Russian");
  assert.equal(languageLabel("de", "ru"), "немецкий");
});

test("facetOptions сопоставляет null → unknown и добавляет count", () => {
  const opts = facetOptions([{ code: "ru", count: 18 }, { code: null, count: 2 }], "en");
  assert.deepEqual(opts, [
    { code: "ru", label: "Russian", count: 18 },
    { code: "unknown", label: "unknown", count: 2 },
  ]);
});

test("facetOptions нижний регистр кода", () => {
  const opts = facetOptions([{ code: "ES", count: 1 }], "en");
  assert.equal(opts[0].code, "es");
  assert.equal(opts[0].count, 1);
});

test("facetOptions пустой ввод → пустой список", () => {
  assert.deepEqual(facetOptions([], "en"), []);
  assert.deepEqual(facetOptions(undefined, "en"), []);
});
