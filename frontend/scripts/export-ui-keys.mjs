// Экспорт канонического набора ключей UI-словаря из ru.js в JSON-манифест,
// который бэкенд использует для валидации загружаемых runtime-переводов
// (Этап 7 фаза C): ключи ⊆ канонических, а {param}-плейсхолдеры совпадают.
//
// Вывод 1: backend/app/i18n/ui_keys.json — { "key": ["param1", ...], ... }.
// Плейсхолдер "count" (неявный для плюралов) в манифест НЕ входит.
//
// Вывод 2 (фаза 2, 2026-09-08): backend/app/i18n/ui_en.json — ПОЛНЫЙ en-словарь
// (ключ → значение, включая plural-объекты). Бэкенд использует его для автосида
// en-копии при активации языка без runtime-словаря (ui_dictionary.seed_english_copy):
// английский — международный язык-источник для перевода интерфейса на новые
// европейские языки. Дрейф обоих артефактов ловится test/i18n.test.mjs.
//
// Запуск: node scripts/export-ui-keys.mjs   (из frontend/)
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const localesDir = resolve(here, "../src/i18n/locales");
const ruMessages = (
  await import(pathToFileURL(resolve(localesDir, "ru.js")).href)
).default;
const enMessages = (
  await import(pathToFileURL(resolve(localesDir, "en.js")).href)
).default;

const PARAM_RE = /\{(\w+)\}/g;

function paramsOf(value) {
  const set = new Set();
  const scan = (s) => {
    for (const m of s.matchAll(PARAM_RE)) set.add(m[1]);
  };
  if (typeof value === "string") scan(value);
  else if (value && typeof value === "object") {
    for (const form of Object.values(value)) if (typeof form === "string") scan(form);
  }
  set.delete("count"); // неявный параметр плюрала
  return [...set].sort();
}

const manifest = {};
for (const key of Object.keys(ruMessages).sort()) {
  manifest[key] = paramsOf(ruMessages[key]);
}

const backendI18n = resolve(here, "../../backend/app/i18n");
mkdirSync(backendI18n, { recursive: true });

const keysOut = resolve(backendI18n, "ui_keys.json");
writeFileSync(keysOut, JSON.stringify(manifest, null, 2) + "\n", "utf-8");
console.log(`ui_keys.json: ${Object.keys(manifest).length} ключей -> ${keysOut}`);

const enOut = resolve(backendI18n, "ui_en.json");
writeFileSync(enOut, JSON.stringify(enMessages, null, 2) + "\n", "utf-8");
console.log(`ui_en.json: ${Object.keys(enMessages).length} ключей -> ${enOut}`);
