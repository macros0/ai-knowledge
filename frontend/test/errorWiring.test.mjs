// Страж проводки локализации ошибок API.
//
// Механизм (friendlyApiError + apiError.<code> в словарях) был построен
// правильно, но подключён к трём компонентам из 39: остальные показывали
// `err.message` — русский `detail` с бэкенда (см. lib/api.js), и англоязычный
// пользователь получал «Failed to delete: Документ уже находится в корзине».
// Тест ловит именно проводку: сам механизм проверяет apiErrors.test.mjs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../src", import.meta.url));

// Исключение — диагностика JSON, который администратор сам ввёл в редактор:
// она помогает исправить ввод и не содержит ответа провайдера или сервера.
// Держим списком, чтобы новый файл не проскочил молча.
const ALLOWED = new Map([
  ["components/UiDictionaryEditor.jsx", "диагностика JSON.parse из браузера, не ответ API"],
]);

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (/\.(js|jsx)$/.test(name)) out.push(full);
  }
  return out;
}

test("ни один компонент не показывает сырой err.message вместо перевода по коду", () => {
  const offenders = [];
  for (const file of walk(SRC)) {
    // Ключи ALLOWED и сравнение с "lib/api.js" написаны с posix-разделителем;
    // на Windows slice даёт бэкслэши — без нормализации исключения не сработали бы.
    const rel = file.slice(SRC.length + 1).split("\\").join("/");
    if (rel === "lib/api.js" || ALLOWED.has(rel)) continue;
    const text = readFileSync(file, "utf-8");
    text.split("\n").forEach((line, i) => {
      // `.messages.map(...)` не совпадает: \b после message требует не-словарный символ.
      if (/\b(err|error|e)\.message\b/.test(line)) {
        offenders.push(`${rel}:${i + 1}: ${line.trim()}`);
      }
    });
  }
  assert.deepEqual(
    offenders,
    [],
    "ошибка показывается как есть — оберните в friendlyApiError(err, t):\n" +
      offenders.join("\n")
  );
});

test("allowlist не протух: перечисленные файлы существуют и правда содержат err.message", () => {
  for (const [rel, why] of ALLOWED) {
    const text = readFileSync(join(SRC, rel), "utf-8");
    assert.match(text, /\b(err|error|e)\.message\b/, `${rel} больше не исключение (${why}) — уберите из списка`);
  }
});
