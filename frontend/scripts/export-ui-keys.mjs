// Экспорт канонического набора ключей UI-словаря из ru.js в JSON-манифест,
// который бэкенд использует для валидации загружаемых runtime-переводов
// (Этап 7 фаза C): ключи ⊆ канонических, а {param}-плейсхолдеры совпадают.
//
// Вывод: backend/app/i18n/ui_keys.json — { "key": ["param1", ...], ... }.
// Плейсхолдер "count" (неявный для плюралов) в манифест НЕ входит.
//
// Запуск: node scripts/export-ui-keys.mjs   (из frontend/)
// Дрейф ловится тестом test/i18n.test.mjs (сравнение ru.js с манифестом).
import { writeFileSync, mkdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const messages = (
  await import(pathToFileURL(resolve(here, "../src/i18n/locales/ru.js")).href)
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
for (const key of Object.keys(messages).sort()) {
  manifest[key] = paramsOf(messages[key]);
}

const out = resolve(here, "../../backend/app/i18n/ui_keys.json");
mkdirSync(dirname(out), { recursive: true });
writeFileSync(out, JSON.stringify(manifest, null, 2) + "\n", "utf-8");
console.log(`ui_keys.json: ${Object.keys(manifest).length} ключей -> ${out}`);
