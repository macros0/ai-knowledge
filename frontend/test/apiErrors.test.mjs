import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import {
  ApiError,
  apiErrorKey,
  friendlyApiError,
  getHealth,
  getOkfContent,
  listTags,
  serviceMessageKey,
} from "../src/lib/api.js";
import { csrfTokenFromDocument } from "../src/lib/api.js";
import * as api from "../src/lib/api.js";

test("csrf cookie helper is safe in SSR without document", () => {
  assert.equal(csrfTokenFromDocument(), null);
  assert.equal(csrfTokenFromDocument({ cookie: "csrf_token=abc%2F123" }), "abc/123");
});
import ru from "../src/i18n/locales/ru.js";
import en from "../src/i18n/locales/en.js";
import { createTranslator } from "../src/i18n/core.js";

// friendlyApiError теперь принимает переводчик: тексты живут в словарях, иначе
// англоязычный пользователь получал бы русские литералы из api.js.
const t = createTranslator("ru").t;

test("friendlyApiError: 401/403 — сессия/права", () => {
  for (const st of [401, 403]) {
    const msg = friendlyApiError(new ApiError("Forbidden", { status: st }), t);
    assert.ok(!/HTTP/.test(msg), `не сырой текст для ${st}`);
    assert.ok(msg.length > 8);
  }
});

test("friendlyApiError: 404 — раздел на старой версии backend", () => {
  const msg = friendlyApiError(new ApiError("Not Found", { status: 404 }), t);
  assert.match(msg, /Обновите страницу/);
});

test("friendlyApiError: status 0 (сеть) — недоступен", () => {
  const msg = friendlyApiError(new ApiError("timeout", { status: 0 }), t);
  assert.match(msg, /связи/i);
});

// fetch кидает TypeError, когда backend не поднят. Без обёртки наружу уходило
// браузерное «Failed to fetch» мимо словарей, а ключ backendUnreachable был мёртв.
async function withDeadNetwork(fn) {
  const original = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new TypeError("Failed to fetch");
  };
  try {
    return await fn();
  } finally {
    globalThis.fetch = original;
  }
}

function assertBackendUnreachable(err) {
  assert.ok(err instanceof ApiError, "сетевой сбой не обёрнут в ApiError");
  assert.equal(err.status, 0);
  assert.equal(err.code, undefined); // без кода — ключ выбирается по статусу
  assert.equal(apiErrorKey(err).key, "apiError.backendUnreachable");
  assert.equal(friendlyApiError(err, t), ru["apiError.backendUnreachable"]);
  assert.equal(
    friendlyApiError(err, createTranslator("en").t),
    en["apiError.backendUnreachable"]
  );
  return true;
}

test("сетевой сбой оборачивается в ApiError(status 0) → backendUnreachable", async () => {
  await withDeadNetwork(() => assert.rejects(listTags(), assertBackendUnreachable));
});

test("вызовы мимо /api (health, текст OKF) обёрнуты так же", async () => {
  // getHealth ходит в корневой /health, getOkfContent читает text(), а не json():
  // оба раньше звали сырой fetch и при недоступном backend отдавали «Failed to fetch».
  await withDeadNetwork(async () => {
    await assert.rejects(getHealth(), assertBackendUnreachable);
    await assert.rejects(getOkfContent(7, "doc.okf"), assertBackendUnreachable);
  });
});

test("в api.js нет сырого fetch мимо общей обёртки", () => {
  // Единственная точка вызова — fetchApi: только она нормализует таймаут,
  // сетевой сбой и не-2xx в ApiError с кодом для словаря.
  const src = readFileSync(fileURLToPath(new URL("../src/lib/api.js", import.meta.url)), "utf-8");
  const calls = src.split("\n").filter((line) => /\bfetch\(/.test(line));
  assert.equal(
    calls.length,
    1,
    `вызовов fetch должно быть ровно один (внутри fetchApi), а не ${calls.length}:\n${calls.join("\n")}`
  );
  // Именно тот, что внутри fetchApi: URL-параметр, а не литерал эндпоинта.
  assert.match(calls[0], /fetch\(url,/);
});

test("friendlyApiError: конфликт без кода объясняется по HTTP-статусу", () => {
  const msg = friendlyApiError(new ApiError("Тег используется документами", { status: 409 }), t);
  assert.equal(msg, ru["apiError.conflict"]);
});

test("friendlyApiError: не-ApiError (TypeError/строка) не падает", () => {
  assert.ok(friendlyApiError(new TypeError("x"), t).length > 0);
  assert.ok(friendlyApiError(null, t).length > 0);
});

// --- Локализация по коду ошибки ---

test("код ошибки превращается в ключ словаря", () => {
  const err = new ApiError("Документ не найден", { status: 404, code: "document_not_found" });
  assert.equal(apiErrorKey(err).key, "apiError.document_not_found");
});

test("недоступная зависимость: ключ по сервису важнее кода", () => {
  const err = new ApiError("Qdrant down", {
    status: 503,
    code: "dependency_unavailable",
    service: "qdrant",
  });
  assert.equal(apiErrorKey(err).key, "apiError.service.qdrant");
});

test("serviceMessageKey не выдумывает ключи для чужих сервисов", () => {
  assert.equal(serviceMessageKey("qdrant"), "apiError.service.qdrant");
  assert.equal(serviceMessageKey("unknown-service"), null);
});

test("англоязычный интерфейс получает английский текст, а не русский detail", () => {
  const err = new ApiError("Недостаточно прав для выполнения операции", {
    status: 403,
    code: "forbidden",
  });
  const enText = friendlyApiError(err, createTranslator("en").t);
  assert.equal(enText, "You do not have permission to perform this operation");
  assert.ok(!/[а-яё]/i.test(enText), `в английском тексте кириллица: ${enText}`);
  assert.equal(friendlyApiError(err, t), "Недостаточно прав для выполнения операции");
});

test("неизвестный клиенту код — безопасное локализованное сообщение", () => {
  const err = new ApiError("Что-то новое сломалось", { status: 400, code: "brand_new_code" });
  assert.equal(friendlyApiError(err, createTranslator("en").t), en["apiError.internal_error"]);
});

test("chunk loading preserves network failures for actionable UI messages", async () => {
  await withDeadNetwork(() => assert.rejects(api.getDocumentChunks("doc-1"), assertBackendUnreachable));
});

test("chunk loading preserves HTTP status and server codes without rendering diagnostics", async () => {
  const original = globalThis.fetch;
  try {
    for (const [status, body, key] of [
      [503, "private gateway diagnostic", "dependency_unavailable"],
      [429, JSON.stringify({ detail: "private provider diagnostic", code: "rate_limited" }), "rate_limited"],
      [500, JSON.stringify({ detail: "private provider diagnostic", code: "internal_error" }), "internal_error"],
    ]) {
      globalThis.fetch = async (url) => {
        assert.equal(url, "/api/documents/doc-1/chunks");
        return new Response(body, { status });
      };
      await assert.rejects(api.getDocumentChunks("doc-1"), (err) => {
        assert.ok(err instanceof ApiError);
        assert.equal(err.status, status);
        assert.equal(friendlyApiError(err, t), t(`apiError.${key}`));
        return true;
      });
    }
    globalThis.fetch = async () => Response.json([{ chunk_index: 0, title: "Chunk" }]);
    assert.deepEqual(await api.getDocumentChunks("doc-1"), [{ chunk_index: 0, title: "Chunk" }]);
  } finally {
    globalThis.fetch = original;
  }
});

test("provider exceptions and legacy stored errors never reach user messages", () => {
  const raw = 'litellm.BadRequestError: OpenrouterException {"provider_name":"DeepInfra","code":"context_length_exceeded","user_id":"private-user"}';
  for (const locale of ["ru", "en"]) {
    const translate = createTranslator(locale).t;
    for (const err of [new Error(raw), new ApiError(raw, { status: 500 }),
      new ApiError(raw, { status: 400, code: "future_code" }),
      new ApiError(raw), null]) {
      assert.equal(friendlyApiError(err, translate), translate("apiError.internal_error"));
    }
    assert.equal(friendlyApiError(new ApiError(raw, { code: "storage_full" }), translate), translate("apiError.storage_full"));
  }
});

test("recoverable errors explain the next action without sending users to support", () => {
  for (const locale of ["ru", "en"]) {
    const translate = createTranslator(locale).t;
    for (const code of ["server_restarted", "job_interrupted", "generation_retrying",
      "generation_timeout", "generation_rate_limited", "processing_unavailable",
      "dependency_unavailable", "timeout", "rate_limited", "operator_rollback"]) {
      const text = friendlyApiError(new ApiError("private provider diagnostic", { code }), translate);
      assert.equal(text, translate(`apiError.${code}`));
      assert.doesNotMatch(text, /технич|support|private|apiError\./i);
    }
    for (const service of ["llm", "ollama", "qdrant"]) {
      const text = friendlyApiError(new ApiError("private provider diagnostic", { code: "dependency_unavailable", service }), translate);
      assert.equal(text, translate(`apiError.service.${service}`));
      assert.doesNotMatch(text, /технич|support|private/i);
    }
  }
  assert.match(friendlyApiError(new ApiError("", { code: "server_restarted" }), t), /Возобновить/);
  assert.match(friendlyApiError(new ApiError("", { code: "generation_retrying" }), t), /автоматически/);
});

test("HTTP failures without a code keep actionable messages and hide raw details", () => {
  for (const [status, code] of [[400, "invalid_request"], [409, "conflict"],
    [413, "file_too_large"], [422, "invalid_request"], [429, "rate_limited"],
    [408, "timeout"], [504, "timeout"], [502, "dependency_unavailable"], [503, "dependency_unavailable"]]) {
    assert.equal(friendlyApiError(new ApiError("private gateway diagnostic", { status }), t), t(`apiError.${code}`));
  }
});

test("все apiError-ключи переведены на оба языка", () => {
  const keys = Object.keys(ru).filter((k) => k.startsWith("apiError."));
  assert.ok(keys.length > 30, `ключей ошибок мало: ${keys.length}`);
  for (const k of keys) {
    assert.ok(typeof en[k] === "string" && en[k].length > 0, `нет перевода en для ${k}`);
    assert.ok(!/[а-яё]/i.test(en[k]), `кириллица в en-переводе ${k}: ${en[k]}`);
  }
});
