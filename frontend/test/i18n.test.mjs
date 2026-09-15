import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  DEFAULT_LOCALE,
  SUPPORTED_LOCALES,
  normalizeLocale,
  detectLocale,
  detectBrowserLocale,
  getMessages,
  translate,
  translatePlural,
  createTranslator,
  pluralForm,
  formatDate,
  formatDateTime,
  resolveServerLocale,
  readLocaleCookie,
  setLocaleCookie,
} from "../src/i18n/core.js";
import { bootScript } from "../src/i18n/boot.js";
import { lookupTitleKey, makeTitle } from "../src/i18n/titles.js";
import locales from "../src/i18n/locales/index.js";
import ru from "../src/i18n/locales/ru.js";
import en from "../src/i18n/locales/en.js";

test("LocaleProvider imports the DEFAULT_LOCALE it uses for the server cookie fallback", () => {
  const provider = readFileSync(
    fileURLToPath(new URL("../src/i18n/LocaleContext.jsx", import.meta.url)),
    "utf-8"
  );
  assert.match(provider, /import\s*\{[^}]*\bDEFAULT_LOCALE\b[^}]*\}\s*from\s*"\.\/core";/s);
});

test("SUPPORTED_LOCALES содержит ru, en, de, fr", () => {
  assert.ok(SUPPORTED_LOCALES.includes("ru"));
  assert.ok(SUPPORTED_LOCALES.includes("en"));
  assert.ok(SUPPORTED_LOCALES.includes("de"));
  assert.ok(SUPPORTED_LOCALES.includes("fr"));
});

test("normalizeLocale клампит невалидные значения в дефолт", () => {
  assert.equal(normalizeLocale("en"), "en");
  assert.equal(normalizeLocale("EN"), "en");
  assert.equal(normalizeLocale("en-US"), "en");
  assert.equal(normalizeLocale("en_US"), "en");
  assert.equal(normalizeLocale("ru"), "ru");
  assert.equal(normalizeLocale("de"), "de");
  assert.equal(normalizeLocale("fr"), "fr");
  assert.equal(normalizeLocale("fr-FR"), "fr");
  assert.equal(normalizeLocale("es"), DEFAULT_LOCALE); // не в манифесте
  assert.equal(normalizeLocale(null), DEFAULT_LOCALE);
  assert.equal(normalizeLocale(""), DEFAULT_LOCALE);
});

test("detectLocale: первый ПОДДЕРЖАННЫЙ кандидат, порядок списка решает", () => {
  assert.equal(detectLocale(["en-US", "ru"]), "en");
  assert.equal(detectLocale(["de-DE", "ru"]), "de");
  assert.equal(detectLocale(["fr-FR", "ru"]), "fr");
  assert.equal(detectLocale(["de"]), "de");
  assert.equal(detectLocale(["fr"]), "fr");
  // Русский первым кандидатом побеждает: стандартный Chrome на русской системе
  // шлёт ["ru-RU","ru","en-US","en"] — раньше такой пользователь получал en.
  assert.equal(detectLocale(["ru", "en"]), "ru");
  assert.equal(detectLocale(["ru-RU", "ru", "en-US", "en"]), "ru");
  assert.equal(detectLocale(["ru-RU", "de", "fr"]), "ru");
  // Неподдержанные кандидаты пропускаются, а не клампятся в дефолт.
  assert.equal(detectLocale(["es", "en"]), "en");
  assert.equal(detectLocale(["es-ES", "pt", "de-DE"]), "de");
  assert.equal(detectLocale(["ru"]), DEFAULT_LOCALE);
  assert.equal(detectLocale(["es"]), "en");
  assert.equal(detectLocale([]), "en");
  assert.equal(detectLocale(null), "en");
});

test("detectLocale использует одиночный язык браузера и английский fallback", () => {
  assert.equal(detectLocale(["es-ES", "en-US"]), "en");
  assert.equal(detectLocale(["ja-JP"]), "en");
});

test("detectBrowserLocale учитывает navigator.language после списка предпочтений", () => {
  assert.equal(detectBrowserLocale({ languages: [], language: "de-DE" }), "de");
  assert.equal(detectBrowserLocale({ languages: ["es-ES"], language: "fr-FR" }), "fr");
  assert.equal(detectBrowserLocale({ language: "ja-JP" }), "en");
});

test("detectLocale совпадает с boot.js: <html lang> и язык UI не расходятся", () => {
  // boot.js исполняется до гидратации и ставит <html lang> своим перебором —
  // литералы там продублированы, поэтому расхождение ловим тестом.
  const bootLang = (langs) => {
    const documentElement = { lang: "" };
    const win = { localStorage: { getItem: () => null }, navigator: { languages: langs } };
    new Function("window", "document", bootScript())(win, { documentElement });
    return documentElement.lang;
  };
  for (const langs of [
    ["ru-RU", "ru", "en-US", "en"],
    ["ru", "en"],
    ["en-US", "ru"],
    ["es", "en"],
    ["de-DE", "ru"],
    ["fr-FR", "en"],
    ["es"],
    [],
  ]) {
    assert.equal(bootLang(langs), detectLocale(langs), `расхождение на ${JSON.stringify(langs)}`);
  }
});

test("boot.js использует navigator.language, если список предпочтений пуст", () => {
  const documentElement = { lang: "" };
  const win = {
    localStorage: { getItem: () => null },
    navigator: { languages: [], language: "de-DE" },
  };
  new Function("window", "document", bootScript())(win, { documentElement });
  assert.equal(documentElement.lang, "de");
});

test("boot.js сохраняет ранее выбранную локаль поверх языка браузера", () => {
  const documentElement = { lang: "" };
  const win = {
    localStorage: { getItem: () => "en" },
    navigator: { languages: ["ru-RU"], language: "ru-RU" },
  };
  new Function("window", "document", bootScript())(win, { documentElement });
  assert.equal(documentElement.lang, "en");
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

test("консистентность словарей: ключи каждой локали ⊆ ru", () => {
  const ruKeys = Object.keys(ru);
  for (const code of SUPPORTED_LOCALES) {
    const messages = locales[code].messages;
    for (const key of Object.keys(messages)) {
      assert.ok(ruKeys.includes(key), `${code}-ключ "${key}" отсутствует в ru`);
    }
  }
});

test("en полон: каждый ключ ru переведён (en — язык-источник для de/fr)", () => {
  // Обратная сторона проверки выше. Забытый ключ в en.js не ломает сборку —
  // getMessages молча подставит РУССКУЮ строку из фолбэка, и она утечёт в
  // en/de/fr UI и в ui_en.json (автосид словаря новой локали).
  const missing = Object.keys(ru).filter((k) => en[k] === undefined);
  assert.deepEqual(missing, [], `нет en-перевода для ключей: ${missing.join(", ")}`);
});

test("манифест: каждый словарь соответствует коду локали", () => {
  assert.equal(locales.ru.messages, ru);
  assert.equal(locales.en.messages, en);
  // de/fr — ЗАГЛУШКИ фазы 2: строки ссылаются на en-объект (без файла de.js/fr.js).
  assert.equal(locales.de.messages, en);
  assert.equal(locales.fr.messages, en);
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
  assert.equal(lookupTitleKey("/admin/glossary"), "titles.adminGlossary");
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
  assert.equal(resolveServerLocale(null, "de-DE,de;q=0.8"), "de");
  assert.equal(resolveServerLocale(null, null), "en");
  assert.equal(resolveServerLocale("en", null), "en");
  assert.equal(resolveServerLocale("fr", "en-US"), "fr");
  assert.equal(resolveServerLocale("es", "en-US"), "en");
  assert.equal(resolveServerLocale("es", null), "en");
  assert.equal(resolveServerLocale(null, "es-ES,ja-JP"), "en");
});

test("cookie okf.locale — канал языка к серверу: пишется и читается обратно", () => {
  // LocaleProvider синхронизирует cookie на маунте, чтобы `display`-имена тегов
  // и SSR приходили на языке UI, а не на дефолтном ru.
  const win = { document: { cookie: "" } };
  assert.equal(readLocaleCookie(win), null); // cookie нет — сервер языка не знает
  setLocaleCookie("de", win);
  assert.match(win.document.cookie, /okf\.locale=de/);
  assert.match(win.document.cookie, /max-age=31536000/); // год, обновляется на каждом маунте
  // Браузер отдаёт при чтении только пары "k=v; k=v".
  assert.equal(readLocaleCookie({ document: { cookie: "theme=dark; okf.locale=fr" } }), "fr");
  assert.equal(readLocaleCookie({ document: { cookie: "okf.locale=es" } }), null); // чужой код
  assert.equal(readLocaleCookie({ document: { cookie: "okf.localeX=en" } }), null); // не наш ключ
  assert.equal(readLocaleCookie({}), null); // cookie недоступен — не падаем
});

test("полный словарь: plural-формы по модели локали", () => {
  // ru — CLDR one/few/many (+other допустим).
  for (const [key, value] of Object.entries(ru)) {
    if (typeof value === "object" && value !== null) {
      assert.ok(value.one !== undefined, `${key} не имеет формы one`);
      assert.ok(value.few !== undefined, `${key} не имеет формы few`);
      assert.ok(value.many !== undefined, `${key} не имеет формы many`);
    }
  }
  // en и de/fr (de.messages === en === fr.messages) — CLDR one/other.
  for (const code of ["en", "de", "fr"]) {
    for (const [key, value] of Object.entries(locales[code].messages)) {
      if (typeof value === "object" && value !== null) {
        assert.ok(value.one !== undefined, `${key} (${code}) не имеет формы one`);
        assert.ok(value.other !== undefined, `${key} (${code}) не имеет формы other`);
      }
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

test("manifest drift: ui_en.json соответствует en.js (источник автосида)", () => {
  // ui_en.json — полный en-словарь для автосида en-копии при активации языка
  // (фаза 2). Расхождение ломает seed_english_copy — перегенерируйте:
  // node scripts/export-ui-keys.mjs
  const enPath = fileURLToPath(
    new URL("../../backend/app/i18n/ui_en.json", import.meta.url)
  );
  const enJson = JSON.parse(readFileSync(enPath, "utf-8"));
  assert.deepEqual(enJson, en, "ui_en.json расходится с en.js");
});
