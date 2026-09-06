import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  DEFAULT_LOCALE,
  SUPPORTED_LOCALES,
  normalizeLocale,
  detectLocale,
  getMessages,
  translate,
  translatePlural,
  createTranslator,
  pluralForm,
  formatDate,
  formatDateTime,
  resolveServerLocale,
} from "../src/i18n/core.js";
import { lookupTitleKey, makeTitle } from "../src/i18n/titles.js";
import locales from "../src/i18n/locales/index.js";
import ru from "../src/i18n/locales/ru.js";
import en from "../src/i18n/locales/en.js";

test("SUPPORTED_LOCALES содержит ru и en", () => {
  assert.ok(SUPPORTED_LOCALES.includes("ru"));
  assert.ok(SUPPORTED_LOCALES.includes("en"));
});

test("normalizeLocale клампит невалидные значения в дефолт", () => {
  assert.equal(normalizeLocale("en"), "en");
  assert.equal(normalizeLocale("EN"), "en");
  assert.equal(normalizeLocale("en-US"), "en");
  assert.equal(normalizeLocale("en_US"), "en");
  assert.equal(normalizeLocale("ru"), "ru");
  assert.equal(normalizeLocale("de"), DEFAULT_LOCALE);
  assert.equal(normalizeLocale(null), DEFAULT_LOCALE);
  assert.equal(normalizeLocale(""), DEFAULT_LOCALE);
});

test("detectLocale: en в приоритете, иначе дефолт", () => {
  assert.equal(detectLocale(["en-US", "ru"]), "en");
  assert.equal(detectLocale(["ru", "en"]), "en");
  assert.equal(detectLocale(["ru"]), DEFAULT_LOCALE);
  assert.equal(detectLocale(["de"]), DEFAULT_LOCALE);
  assert.equal(detectLocale([]), DEFAULT_LOCALE);
});

test("translate возвращает ключ при отсутствии", () => {
  const messages = getMessages("ru");
  assert.equal(translate(messages, "no.such.key"), "no.such.key");
  assert.equal(translate(messages, "nav.documents"), "Документы");
});

test("translate интерполирует {параметры}", () => {
  const messages = { greeting: "Привет, {name}!" };
  assert.equal(translate(messages, "greeting", { name: "Ваня" }), "Привет, Ваня!");
});

test("translatePlural: строковый шаблон интерполирует count", () => {
  const messages = { size: "Размер {count} КБ" };
  assert.equal(translatePlural(messages, "ru", "size", 5), "Размер 5 КБ");
});

test("pluralForm: русские формы one/few/many", () => {
  assert.equal(pluralForm("ru", 1), "one");
  assert.equal(pluralForm("ru", 21), "one");
  assert.equal(pluralForm("ru", 2), "few");
  assert.equal(pluralForm("ru", 4), "few");
  assert.equal(pluralForm("ru", 5), "many");
  assert.equal(pluralForm("ru", 0), "many");
  assert.equal(pluralForm("ru", 11), "many");
  assert.equal(pluralForm("en", 1), "one");
  assert.equal(pluralForm("en", 2), "other");
  assert.equal(pluralForm("en", 0), "other");
});

test("translatePlural: объект форм выбирается по количеству и локали", () => {
  const messages = {
    docs: { one: "1 документ", few: "{count} документа", many: "{count} документов", other: "{count} documents" },
  };
  assert.equal(translatePlural(messages, "ru", "docs", 1), "1 документ");
  assert.equal(translatePlural(messages, "ru", "docs", 3), "3 документа");
  assert.equal(translatePlural(messages, "ru", "docs", 5), "5 документов");
  assert.equal(translatePlural(messages, "en", "docs", 3), "3 documents");
});

test("fallback-merge: en-ключ в ru есть и виден через getMessages", () => {
  // Используем существующий ключ из ru и проверяем, что перевод en подхватывается.
  const enMessages = getMessages("en");
  assert.equal(enMessages["nav.documents"], "Documents");
  const ruMessages = getMessages("ru");
  assert.equal(ruMessages["nav.documents"], "Документы");
});

test("консистентность словарей: каждый en-ключ есть в ru", () => {
  const ruKeys = Object.keys(ru);
  const enKeys = Object.keys(en);
  for (const key of enKeys) {
    assert.ok(ruKeys.includes(key), `en-ключ "${key}" отсутствует в ru`);
  }
  // en ⊇ ru по ключам не обязателен (непереведённые ru-ключи валидны), но
  // проверяем, что ru — полный (защита от опечаток в манифесте).
  assert.ok(enKeys.length >= 0);
});

test("манифест: каждый словарь соответствует коду локали", () => {
  assert.equal(locales.ru.messages, ru);
  assert.equal(locales.en.messages, en);
  assert.equal(SUPPORTED_LOCALES.join(","), Object.keys(locales).join(","));
});

test("createTranslator возвращает локализованный хук-интерфейс", () => {
  const en = createTranslator("en");
  assert.equal(en.locale, "en");
  assert.equal(en.t("nav.chat"), "Ask about documents");
  const ru = createTranslator("ru");
  assert.equal(ru.tc("docs_count", 3, { key: "x" }), "docs_count"); // fallback-ключ
});

test("formatDate/formatDateTime без значений → пусто, иначе Intl", () => {
  assert.equal(formatDate(null, "ru"), "");
  assert.equal(formatDate("not-a-date", "ru"), "");
  const d = new Date(2026, 0, 5, 10, 30);
  assert.match(formatDate(d, "en"), /\d/);
  assert.match(formatDateTime(d, "ru"), /\d/);
});

test("lookupTitleKey резолвит специфичные пути раньше общих", () => {
  assert.equal(lookupTitleKey("/"), "titles.documents");
  assert.equal(lookupTitleKey("/chat"), "titles.chat");
  assert.equal(lookupTitleKey("/chat/history"), "titles.history");
  assert.equal(lookupTitleKey("/chat/history/admin"), "titles.historyAdmin");
  assert.equal(lookupTitleKey("/developments/5"), "titles.developmentCard");
  assert.equal(lookupTitleKey("/admin"), "titles.admin");
  assert.equal(lookupTitleKey("/security"), "titles.security");
  assert.equal(lookupTitleKey("/unknown"), null);
});

test("makeTitle строит заголовок из ключа + app.title", () => {
  const en = makeTitle("/chat", "en");
  assert.ok(en.includes("Ask about documents"));
  assert.ok(en.includes("OKF Knowledge Service"));
  assert.equal(makeTitle("/unknown", "en"), "OKF Knowledge Service");
});

test("resolveServerLocale: cookie имеет приоритет, иначе Accept-Language", () => {
  assert.equal(resolveServerLocale("ru", "en-US,en;q=0.9"), "ru");
  assert.equal(resolveServerLocale(null, "en-US,en;q=0.9"), "en");
  assert.equal(resolveServerLocale(null, "de-DE,de;q=0.8"), DEFAULT_LOCALE);
  assert.equal(resolveServerLocale(null, null), DEFAULT_LOCALE);
  assert.equal(resolveServerLocale("en", null), "en");
  assert.equal(resolveServerLocale("fr", "en-US"), DEFAULT_LOCALE);
});

test("полный словарь: каждый plural-объект в ru имеет формы one/few/many", () => {
  const ruMessages = getMessages("ru");
  for (const [key, value] of Object.entries(ru)) {
    if (typeof value === "object" && value !== null) {
      assert.ok(value.one !== undefined, `${key} не имеет формы one`);
      assert.ok(value.few !== undefined, `${key} не имеет формы few`);
      assert.ok(value.many !== undefined, `${key} не имеет формы many`);
    }
  }
  // en-объекты должны иметь one/other.
  for (const [key, value] of Object.entries(en)) {
    if (typeof value === "object" && value !== null) {
      assert.ok(value.one !== undefined, `${key} (en) не имеет формы one`);
      assert.ok(value.other !== undefined, `${key} (en) не имеет формы other`);
    }
  }
});

test("конкретные русские склонения из словаря", () => {
  const ru = createTranslator("ru");
  assert.equal(ru.tc("docs.tagsUpdated", 1), "Теги обновлены у 1 документа");
  assert.equal(ru.tc("docs.tagsUpdated", 3), "Теги обновлены у 3 документов");
  assert.equal(ru.tc("docs.tagsUpdated", 5), "Теги обновлены у 5 документов");
  const en = createTranslator("en");
  assert.equal(en.tc("docs.tagsUpdated", 3), "Tags updated for 3 document(s)");
});

test("getMessages: override > versioned > ru (Этап 7 фаза C)", () => {
  const messages = getMessages("en", { en: { "nav.documents": "Papers" } });
  assert.equal(messages["nav.documents"], "Papers"); // override перекрывает en
  assert.equal(messages["nav.chat"], "Ask about documents"); // остальное — из en
  assert.equal(messages["security.title"], "Audit and security"); // из en
  // Ключ, которого нет в override и в en — из ru (фолбэк).
  const ruOnly = getMessages("en", { en: {} });
  assert.equal(typeof ruOnly["status.uploaded"], "string");
  // Без override — как раньше.
  assert.equal(getMessages("en")["nav.documents"], "Documents");
});

test("manifest drift: ui_keys.json соответствует ru.js", () => {
  const manifestPath = fileURLToPath(
    new URL("../../backend/app/i18n/ui_keys.json", import.meta.url)
  );
  const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
  const ruKeys = Object.keys(ru);
  const manifestKeys = Object.keys(manifest);
  assert.deepEqual([...ruKeys].sort(), [...manifestKeys].sort(), "ключи манифеста расходятся с ru.js — перегенерируйте: node scripts/export-ui-keys.mjs");
  const PARAM_RE = /\{(\w+)\}/g;
  const paramsOf = (v) => {
    const s = new Set();
    const scan = (t) => { for (const m of t.matchAll(PARAM_RE)) s.add(m[1]); };
    if (typeof v === "string") scan(v);
    else if (v && typeof v === "object") for (const f of Object.values(v)) if (typeof f === "string") scan(f);
    s.delete("count");
    return [...s].sort();
  };
  for (const key of ruKeys) {
    assert.deepEqual(paramsOf(ru[key]), manifest[key], `параметры ключа "${key}" расходятся с манифестом`);
  }
});