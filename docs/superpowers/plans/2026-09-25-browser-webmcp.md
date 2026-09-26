# Browser WebMCP Implementation Plan — усиленная изоляция

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use superpowers:subagent-driven-development only if the user chooses delegation. Steps use checkbox syntax for tracking.

**Goal:** Реализовать четыре браузерных инструмента с минимальным добавочным риском при отключении, серверным контролем при включении и доказательным обоснованием для ИБ.

**Architecture:** При off backend не импортирует и не подключает feature router, frontend bootstrap не загружает feature module. При on инструменты обращаются только к /api/webmcp с проверкой пользователя и серверных бюджетов. Обычные API-контракты и frontend api.js сохраняются; узкое извлечение retrieval отдельно проверяется сравнением до/после.

**Tech Stack:** Python 3.12, FastAPI/Pydantic/SQLAlchemy, PostgreSQL/SQLite и Qdrant, Next.js 16/React 19, pytest/node:test, native WebMCP. Без новых production-зависимостей.

**Spec:** [Усиленный проект решения](../specs/2026-09-25-browser-webmcp-design.md).

Редакция 2 от 25.09.2026 полностью заменяет прежние инструкции этого файла. Статус: план, реализация не начата. Новые API, файлы, тесты и команды проектируются; будущие проверки не объявляются выполненными. SECURITY.md будет обновлён вместе с реализацией и фактическими доказательствами, а не заявлением о безопасности ещё отсутствующего кода.

## Global Constraints

- WEBMCP_ENABLED=false по умолчанию. On требует непустой WEBMCP_ALLOWED_USER_IDS с точными User.user_id; AUTH_PROVIDER=disabled несовместим с on.
- При off нет /api/webmcp routes/OpenAPI entries, импортов feature backend modules и инициализации feature services.
- Fresh off page: нет загрузки/исполнения feature chunks, modelContext calls, tools и /api/webmcp traffic. Минимальный bootstrap и запрос существующих settings допустимы.
- Общий frontend/src/lib/api.js и обычные SearchRequest/SearchHit/SearchResponse не менять ради WebMCP.
- Auth, proxy, CORS, CSRF, cookie и security headers не ослаблять.
- Поиск: 1–20 hits (default 5); список: 1–50 документов (default 20), offset <=10000.
- Источник: страница <=12000 code points; полный источник <=250000 code points и <=1048576 UTF-8 bytes; SHA-256 для продолжения.
- Backend JSON и конечный tool envelope каждый <=65536 UTF-8 bytes. Не обрезать сериализованную JSON-строку.
- Server defaults: 60 запросов/мин на пользователя, 300 глобально; concurrency 2/8; выдача 4194304/67108864 bytes за 3600 секунд.
- Квоты атомарные, общие для вкладок/сессий по user_id и backend-процессу. In-memory reset при restart документируется. Только single-worker/single-replica.
- Клиент: максимум 2 calls и timeout 30000 мс на весь вызов. Нет автоматического retry.
- Никаких pipeline, ensure_chunks, LLM generation, exports и изменения документов; search embeddings остаются частью текущего retrieval.
- Без staging/FS fallback, произвольных URL, DB migrations и переиндексации.
- Сохранить чужие изменения; без git add ., reset/stash общего checkout. Evidence — tests/artifacts/webmcp, temporary data — tests/tmp.
- Windows: Next через node, host frontend 16300/backend 18000. Production — выбранный OKF_RUNTIME_ENV_FILE, без NEXT_PUBLIC-копии флага.

## Review Focus

1. Off скрывает tools, но оставляет route/import-time работу: задача 3, O01–O04.
2. Сборщик заранее загружает provider или late import монтируется после logout: задачи 8/10, O05/O06/L05.
3. Извлечение retrieval меняет ordinary response, ранжирование или rate limit: задача 4, B01–B04.
4. Несколько вкладок обходят bytes/concurrency, а abort освобождает слот до конца server work: задача 5, Q01–Q07.
5. ИБ получает необоснованное «риска нет», либо read-only путают с отсутствием утечки: задачи 1/9/12, A01–A05 и пакет доказательств.

## Последовательность и допустимые изменения

0 baseline → 1 native/доверие → 2 конфигурация → 3 hard-off backend → 4 retrieval → 5 бюджеты → 6 endpoints → 7 transport → 8 bootstrap/controller → 9 tools → 10 UI → 11 CI/приёмка → 12 SECURITY.md/ИБ/review → 13 выпуск/откат.

Каждая кодовая задача: failing behavioral test → минимальное изменение → targeted tests → diff review. Полные suites на итоговом gate; повторяются после относящихся к ним исправлений. Коммитить только завершённые изменения в согласованной ветке, push/merge/deploy — в разрешённом scope. План не запускает реализацию, агентов или live-изменения.

Общий retrieval проходит собственный review: flag не откатывает общий код. При расхождении ordinary API с baseline интеграция останавливается. Backend off gate обязателен независимо от поддержки native WebMCP; отсутствие native API не оправдывает пропуск off-проверок.

## Карта файлов

| Файл | Ответственность |
|---|---|
| backend/app/config.py, .env.example, deploy/production/{bundled,external}.env.example | flag, allowlist, quota defaults |
| backend/app/api/settings.py, backend/app/models/schemas.py | только effective capability в ChatSettingsOut; не SearchHit |
| backend/app/main.py | локальный conditional import/include |
| backend/app/api/webmcp.py, backend/app/models/webmcp.py (новые) | три endpoints и строгие DTO |
| backend/app/services/search_execution.py (новый), backend/app/api/search.py | узкое извлечение retrieval с прежним ordinary response |
| backend/app/services/webmcp_{access,limits,source}.py (новые) | access gate, accounting, SQL bounded read |
| backend/app/error_codes.py, backend/app/i18n/ui_{keys,en}.json | новые безопасные codes, старые не менять |
| frontend/src/lib/webmcpBootstrap.mjs (новый) | маленький loader без static feature imports |
| frontend/src/components/WebMCPBootstrap.jsx, WebMCPProvider.jsx (новые), frontend/src/app/layout.js | bootstrap и отдельно dynamic provider |
| frontend/src/lib/webmcp/{http,contracts,adapter,controller,tools}.mjs (новые) | изолированная feature logic |
| frontend/src/i18n/locales/{ru,en}.js | labels/errors |
| backend/tests/test_webmcp_{config,disabled,search,limits,api,source}.py (новые) | backend gates |
| frontend/test/webmcp{Bootstrap,Http,Contracts,Adapter,Controller,Tools}.test.mjs (новые) | frontend unit contracts |
| tests/scripts/frontend/webmcp-browser-checklist.md (новый) | воспроизводимая native/off/on приёмка |
| tests/artifacts/webmcp/{acceptance,off-comparison,security-evidence}.md (при проверках) | доказательства для разработки и ИБ |
| .github/workflows/ci.yml | отдельный blocking off gate |
| SECURITY.md | модель угроз, гарантии off, остаточные риски, security change log |
| docs/WEBMCP.md (новый), docs/PRODUCTION_DEPLOYMENT.md, tests/README.md | операторские процедуры и evidence |

Не менять общий frontend api.js, AuthContext, Next proxy и ordinary document endpoints. Независимые нужные исправления этих компонентов оформлять отдельно с собственным scope/review.

## Общие интерфейсы

~~~text
search_execution.execute_search(req: SearchRequest, settings: Settings) -> SearchExecution
SearchExecution: query, rows, expansion_status, applied_terms.
rows: title/content/tags/filepath/doc_id/source_slug/score/point_type/chunk_index.
Обычные HTTP auth/admission и SearchResponse serializer остаются в api/search.py.

webmcp_access.require_webmcp_user(request, settings) -> User
Переиспользует require_user; затем проверяет global flag и allowlist.

webmcp_limits.WebMcpLimiter(clock):
  admit(user_id) -> Reservation
Reservation: finish(actual_bytes), fail(), release_execution().
admit атомарно резервирует request + server slot + 65536 bytes.
finish/fail завершают byte accounting ровно один раз.
release_execution — только после фактического server completion.

webmcp_source.read_source_page(req: SourcePageRequest) -> SourcePageOut
webmcp_source.slice_source(text, offset, limit) -> dict
SourceRef: doc_id, kind=concept|chunk, source_slug? либо chunk_index?
SourcePageRequest: SourceRef + offset=0, limit=12000, content_sha256?
SourcePageOut: ref, content, offset, next_offset, total_chars, content_sha256, truncated

webmcpBootstrap.createBootstrap({loadSettings, loadModule, onModule, onOff})
  -> {refresh(identity), dispose()}
loadModule только при подтверждённой identity и capability===true.
onModule принимает результат только актуального generation.

http.createWebMcpHttp({fetchImpl, readCsrfToken})
  -> {loadSettings, loadIdentity, searchKnowledge, listDocuments, readSource}
Все методы принимают {signal}; произвольного URL аргумента нет.

contracts.validateSourceRef(input) -> SourceRef
contracts.validateToolInput(name, input) -> normalized input
contracts.sourceHref(ref) -> внутренний маршрут
adapter.detectWebMcp(documentObject) -> ModelContext | null
adapter.registerTools(context, descriptors, signal) -> Promise<void>

controller.createWebMcpController({loadSettings, loadIdentity, detectContext, makeTools, onStatus})
  -> {refresh(), run(execute), dispose()}
execute(signal) -> Promise<{data, commit?: () => void}>
run: preflight → execute → final identity/generation/signal guard → commit → envelope.
makeTools(run) -> descriptors.
tools.createTools({api, navigate, run, t}) -> ToolDescriptor[]
~~~

Это декларации контрактов, не пустые реализации. ToolResult: {ok:true,data} либо {ok:false,error:{code,message,retryable}}. Backend сериализует успешный DTO один раз, учитывает эти bytes и отправляет тот же body. Callback commit никогда не сериализуется.

## Задача 0. Baseline и изоляция разработки

**Файлы:** читать AGENTS.md/frontend instructions/CI/tests README; при исполнении создать acceptance и off-comparison.

**Вход → выход:** checkout → точная база сравнения и сохранённый перечень чужих изменений.

- [ ] git status --short, git rev-parse HEAD, git diff --cached --name-only; чужой индекс не менять.
- [ ] При исполнении создать согласованную codex/browser-webmcp worktree/ветку. HEAD не включает чужие незакоммиченные изменения: не копировать их автоматически.
- [ ] Зафиксировать Python/Node/browser/lockfiles; прочитать локальные Next guides до JSX.
- [ ] Снять ordinary settings/search/documents/auth contracts, ошибки и headers на synthetic fixtures. SearchResponse сравнивать целиком, не только число hits.
- [ ] Baseline: test_settings.py, test_document_search.py, test_search_filter.py, test_auth.py, test_authz.py и frontend node --test. Existing failures выделить отдельно.
- [ ] Использовать отдельную test DB/корпус; не переключать рабочую среду и не завершать её процессы start-all.

**Приёмка:** в off-comparison есть baseline SHA, команды и фактический вывод.

## Задача 1. Native контракт и политика агента/данных

**Файлы:** browser checklist, acceptance, будущий docs/WEBMCP.md.

**Вход → выход:** спецификация и intended deployment → verified native contract и условия production-on.

- [ ] Перепроверить официальные документы, browser version, secure context, isolation, policies/trial.
- [ ] На synthetic странице выполнить настоящий register/call/unregister:

~~~js
const lifecycle = new AbortController();
await document.modelContext.registerTool({
  name: "okf_probe",
  description: "Return a synthetic value",
  inputSchema: {type: "object", properties: {}, additionalProperties: false},
  execute: async () => ({value: "probe"}),
}, {signal: lifecycle.signal});
// Вызвать через WebMCP-клиента, затем подтвердить отсутствие после abort.
lifecycle.abort();
~~~

- [ ] Проверить active-call cancellation; не подменять native доказательство mocks/полифиллом. При неподдерживаемом browser on-acceptance blocked, off-проверки продолжаются.
- [ ] До production-on заполнить decision: владелец данных, точные разрешённые user IDs, browser/agent/канал установки, local/external processing, допустимый корпус, retention, владелец trial expiry.
- [ ] Название агента, User-Agent или tool argument не являются удостоверением клиента. Если управляемая browser-среда не обеспечивает нужные ограничения, риск описать и оставить off при неприемлемости.
- [ ] Не отправлять корпоративные документы внешним demo-моделям; native acceptance только synthetic.

**Приёмка:** native evidence или честный blocker; global true не заменяет решение о передаче данных.

## Задача 2. Config и capability пользователя

**Файлы:** config.py, ChatSettingsOut, settings.py, env examples, test_webmcp_config.py.

**Вход → выход:** env + server User → effective boolean, без раскрытия allowlist.

- [ ] Failing tests: defaults, true/false/invalid, empty allowlist при on, anonymous mode, member/non-member/blocked user, inconsistent quotas.
- [ ] Default test очищает developer env:

~~~python
from app.config import Settings

def test_webmcp_defaults_off(monkeypatch):
    monkeypatch.delenv("WEBMCP_ENABLED", raising=False)
    monkeypatch.delenv("WEBMCP_ALLOWED_USER_IDS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.webmcp_enabled is False
    assert settings.webmcp_allowed_user_ids == []
~~~

- [ ] Добавить bool, allowlist и шесть quota fields из spec. Allowlist trim/dedup, <=1000 записей по <=256 символов; wildcard запрещён.
- [ ] Validator отклоняет on+empty, on+disabled auth, nonpositive quota, user>global, bytes<65536. Off не требует новых секретов.
- [ ] /settings использует require_user и effective flag; прежние fields/errors неизменны. Тесты переопределяют правильные middleware settings и FastAPI dependencies.
- [ ] Env examples: false и []; никаких реальных user IDs.
- [ ] python -m pytest tests/test_webmcp_config.py tests/test_settings.py -q.

**Приёмка:** allowlist authoritative на сервере; старый backend без поля означает off. Коммит: feat: configure scoped WebMCP access.

## Задача 3. Hard-off backend

**Файлы:** main.py, новые api/webmcp.py, services/webmcp_access.py, test_webmcp_disabled.py.

**Вход → выход:** startup flag → conditional feature surface.

- [ ] Tests: routes/OpenAPI/import tracing/direct HTTP при off. В отдельном subprocess запретить import app.api.webmcp/app.services.webmcp_*; create_app с false должен работать.
- [ ] Conditional import внутри app factory, без package __init__ реэкспорта:

~~~python
if settings.webmcp_enabled:
    from app.api.webmcp import router as webmcp_router
    protected.include_router(webmcp_router)
~~~

- [ ] Feature quotas/source clients не создаются при off. Общие settings/model imports не должны транзитивно импортировать feature code.
- [ ] On dependency повторно проверяет require_user, flag и allowlist; client-supplied identity игнорируется/отклоняется.
- [ ] При false проверить /search, /documents, /source внутри /api/webmcp: валидный GET/POST с нужным CSRF получает ordinary route 404. Malformed requests не запускают feature parsing.
- [ ] Глобальная middleware может отказать раньше 404; не отключать её для теста. Spies доказывают отсутствие feature SQL/FS/Qdrant/LLM/quota work.
- [ ] python -m pytest tests/test_webmcp_disabled.py tests/test_webmcp_config.py -q.

**Приёмка:** O01–O04; отсутствие маршрута доказано инвентаризацией, а не только кодом ответа. Коммит: feat: isolate disabled WebMCP backend.

## Задача 4. Retrieval с сохранением ordinary API

**Файлы:** services/search_execution.py, api/search.py, test_webmcp_search.py и existing search tests.

**Вход → выход:** текущий retrieval → shared SearchExecution, прежний ordinary serializer.

- [ ] Golden fixtures: concept, chunk, concept+chunk, review siblings, tags/locale, пустой результат, glossary errors, dependency errors.
- [ ] Зафиксировать serialized ordinary response, scores/order/top_k, status/code/headers и количество search admissions.
- [ ] Перенести только retrieval в execute_search; клиенты остаются lazy. Осознанно обновить monkeypatch targets, assertions не ослаблять.
- [ ] Ordinary endpoint сохраняет require_user, прежний rate limit и SearchHit/SearchResponse; не добавлять туда doc_id/source_slug.
- [ ] Feature serializer получает source metadata из rows; не разбирает filepath. Разные siblings сохраняют свои slug.
- [ ] python -m pytest tests/test_webmcp_search.py tests/test_search_filter.py tests/test_document_search.py tests/test_source_locale_search.py -q; B01–B04 при false и true.
- [ ] Отдельное ревью общего diff: без изменений fusion, embeddings, auth или ошибок.

**Приёмка:** differential baseline проходит. Расхождение блокирует интеграцию независимо от flag. Коммит: refactor: share retrieval without API changes.

## Задача 5. Атомарные серверные бюджеты

**Файлы:** services/webmcp_limits.py, test_webmcp_limits.py. Ordinary RateLimiter не менять ради feature accounting.

**Вход → выход:** server user_id → Reservation либо отказ до чтения.

- [ ] Tests с fake monotonic clock/barriers: user/global requests, slots, bytes, expiry, fail/double finish, две сессии одного user.
- [ ] Под одним lock проверить все пределы и записать admission. При отказе нет частично занятого slot/byte reserve.
- [ ] Резервировать 65536 bytes до чтения; finish заменяет резерв на фактический JSON UTF-8 size. При fail до ответа освобождать bytes, request attempt сохранять.
- [ ] Незавершённый reserve не истекает только от прохождения окна. Completed bytes учитываются 3600 секунд от completion.
- [ ] Disconnect после сформированного success response списывает bytes; недоставку нельзя доказать. Slot освобождается только после реального завершения sync SQL/Qdrant, не browser abort.
- [ ] Определить WebMcpBusy/WebMcpQuotaExceeded в limits module, конвертировать в 429 + Retry-After. Идемпотентные fail/release не делают counters отрицательными.
- [ ] Тест общей concurrency для сессий:

~~~python
import pytest
from app.services.webmcp_limits import WebMcpBusy

def test_sessions_share_user_concurrency(limiter):
    first = limiter.admit("u1")
    second = limiter.admit("u1")
    with pytest.raises(WebMcpBusy):
        limiter.admit("u1")
    first.fail()
    first.release_execution()
    third = limiter.admit("u1")
    second.fail()
    second.release_execution()
    third.fail()
    third.release_execution()
~~~

- [ ] Store ограничен allowlist и окнами; idle entries удаляются, non-member не создаёт state. Restart reset явно задокументирован; multi-replica недопустим до общего store.
- [ ] python -m pytest tests/test_webmcp_limits.py -q.

**Приёмка:** Q01–Q07; quotas охватывают все feature endpoints. Коммит: feat: bound WebMCP server resources.

## Задача 6. Feature API и bounded SQL read

**Файлы:** api/webmcp.py, models/webmcp.py, services/webmcp_source.py, error catalogs, test_webmcp_api.py/test_webmcp_source.py.

**Вход → выход:** строгие DTO → admitted bounded JSON.

- [ ] extra=forbid; запрет url/filepath/user_id/role/quota overrides. doc_id: ^[0-9a-f]{16}$; kind определяет slug либо index. Slug <=512 Unicode-символов, без slash/backslash/control/traversal; chunk index >=0.
- [ ] Offset>=0, limit 1..12000, hash 64 hex, offset>0 требует hash. List offset<=10000/limit<=50/search<=512/status enum; search наследует QueryText/tags normalization и top_k<=20.
- [ ] POST /api/webmcp/search: access → feature admission → existing ordinary search admission → execute_search → feature serializer. GET /documents: access/admission → registry.list_page с явным limit/offset; только doc_id/filename/status.
- [ ] GET /source: проверка активного документа и конкретной записи в том же SQL SELECT. Никаких private helpers documents.py, staging/FS/ensure_chunks fallback.
- [ ] До материализации проверить SQL char length<=250000 и byte length<=1048576. PostgreSQL: octet_length(content); SQLite: length(CAST(content AS BLOB)). Byte guard обязателен для NUL, где SQLite length(text) недостаточно.
- [ ] Если content не вернулся, metadata-only bounded query различает oversized/absent без загрузки blob. Oversized → 413 source_too_large; deleted/absent → 404 source_unavailable.
- [ ] SHA только bounded текста. Hash mismatch →409, пустая строка успешна, offset==len → пустой конец, offset>len →422.
- [ ] Реализовать точные code-point страницы:

~~~python
from hashlib import sha256

def slice_source(text, offset, limit):
    end = min(offset + limit, len(text))
    return dict(
        content=text[offset:end], offset=offset,
        next_offset=end if end < len(text) else None,
        total_chars=len(text), content_sha256=sha256(text.encode("utf-8")).hexdigest(),
        truncated=end < len(text),
    )

def test_unicode_paging():
    first = slice_source("😀ABC", 0, 2)
    second = slice_source("😀ABC", first["next_offset"], 2)
    assert first["content"] == "😀A"
    assert second["content"] == "BC"
    assert second["next_offset"] is None
~~~

- [ ] Serialize once, account/send те же bytes, <=65536. Title<=512/snippet<=300 code points; уменьшать entries/content с корректным next_offset/truncated. Учитывать JSON escaping; raw filepath не возвращать.
- [ ] Тесты R01–R08/S01–S04/T01–T02: spies ловят writes/LLM/job admission/ensure_chunks, oversized+NUL до hydration, реальные require_user/blocked paths.
- [ ] python -m pytest tests/test_webmcp_api.py tests/test_webmcp_source.py tests/test_auth.py tests/test_authz.py tests/test_trash.py -q.

**Приёмка:** server limits действуют без frontend, ordinary DTO прежние. Коммит: feat: add gated WebMCP data endpoints.

## Задача 7. Изолированный browser transport

**Файлы:** lib/webmcp/http.mjs, webmcpHttp.test.mjs. api.js не меняется.

**Вход → выход:** typed args/signal → fixed same-origin endpoint/error.

- [ ] Fetch tests: URL allowlist, cookie/CSRF, timeout/abort, parse failure, 401/403/404/413/429/503, redirect и cleanup.
- [ ] createWebMcpHttp формирует только три feature paths и control /api/settings,/api/auth/me. credentials=same-origin, cache=no-store, redirect=error. No arbitrary URL/fallback.
- [ ] POST передаёт текущий csrf_token в X-CSRF-Token по existing policy. Изолированный небольшой transport дублирует только необходимую boundary логику, не весь api.js.
- [ ] Parent signal охватывает fetch и body; listener/timer cleanup в finally. cancelled отличается от timeout; общий 30s deadline задаёт controller.
- [ ] Не показывать сырые response bodies; off route 404 безопасно трактовать feature_disabled. Без автоматического повторения.
- [ ] node --test test/webmcpHttp.test.mjs; diff обычного api.js не содержит WebMCP-изменений.

**Приёмка:** transport изолирован и не загружается при fresh off. Коммит: feat: isolate WebMCP transport.

## Задача 8. Bootstrap, adapter и lifecycle controller

**Файлы:** webmcpBootstrap.mjs, adapter.mjs/controller.mjs и соответствующие tests.

**Вход → выход:** confirmed identity/capability → lazy module или off.

- [ ] loadModule spy: false/missing/error/loading →0; true →1; late response после смены identity не принимается.
- [ ] Bootstrap читает существующий settings, без static feature imports/modelContext. Refresh при confirmed identity/focus/visible, coalesce событий, без polling.
- [ ] Native adapter работает по verified API; partial registration отменяет собственный набор, не clearContext.
- [ ] Controller: generation, identity key, own registrations/requests; preflight проверяет /auth/me и effective capability, postflight identity после данных. Backend всё равно проверяет каждый call.
- [ ] execute возвращает {data,commit?}; только final identity/generation/signal guard разрешает commit. Logout/abort исключает навигацию и выдачу старого результата.
- [ ] Общий 30s deadline с preflight; client concurrency2, третий busy. Основная защита нескольких вкладок серверная.
- [ ] Добавить исполняемый off-loader test:

~~~js
import test from "node:test";
import assert from "node:assert/strict";
import {createBootstrap} from "../src/lib/webmcpBootstrap.mjs";

test("off never imports feature code", async () => {
  let imports = 0;
  const gate = createBootstrap({
    loadSettings: async () => ({webmcp_enabled: false}),
    loadModule: async () => { imports++; return {}; },
    onModule: () => assert.fail("must not mount"),
    onOff: () => {},
  });
  await gate.refresh({mode: "simulation", user_id: "u1"});
  assert.equal(imports, 0);
  gate.dispose();
});
~~~

- [ ] node --test test/webmcpBootstrap.test.mjs test/webmcpAdapter.test.mjs test/webmcpController.test.mjs. Race tests через управляемые Promises, не случайный sleep.

**Приёмка:** O05/L01–L08; нет zombie provider/tools. Коммит: feat: gate WebMCP lifecycle.

## Задача 9. Четыре tools и безопасная навигация

**Файлы:** contracts.mjs/tools.mjs, Contracts/Tools tests.

**Вход → выход:** validated arguments → bounded ToolResult.

- [ ] Schema additionalProperties=false и runtime validators совпадают с backend. Reject external URLs/traversal/identity/quota overrides.
- [ ] Все tools используют feature transport; не ordinary search/listDocuments из api.js. Missing SourceRef → source_available=false, без filepath guessing.
- [ ] sourceHref только /documents/{doc_id}/okf/{encoded_slug}.md или /documents/{doc_id}/chunks/{index}; index0 валиден.
- [ ] open_source: readSource(limit=1), затем {data:{href,navigation_requested:true},commit:()=>navigate(href)}. Guard до commit; не обещать уже отрисованную страницу.
- [ ] RU/EN labels/errors, stable names; static descriptions. readOnlyHint только для чтения, untrustedContentHint для document content, без заявления о sandbox/иммунитете модели.
- [ ] TextEncoder(JSON.stringify(result)) <=65536; computed href тоже учитывать, сокращение entries/content корректирует pagination.
- [ ] Synthetic prompt injection остаётся literal data; tools не исполняют инструкции/внешние URL. Native agent reaction проверяется отдельно, unit test не доказывает безопасность модели.
- [ ] node --test test/webmcpContracts.test.mjs test/webmcpTools.test.mjs.

**Приёмка:** нет обхода server gates и произвольной навигации. Коммит: feat: expose bounded knowledge tools.

## Задача 10. UI и отсутствие eager loading

**Файлы:** Bootstrap/Provider JSX, root layout, RU/EN locales.

**Вход → выход:** root → маленький bootstrap, provider только dynamic true.

- [ ] Root статически импортирует только Bootstrap внутри existing AuthProvider/LocaleProvider; AuthContext/ChatContext не рефакторить.
- [ ] Explicit dynamic import только в positive branch. Без barrel/preload механизмов, загружающих feature chunks заранее.
- [ ] SSR/первый render одинаковы, browser globals только в effects. Unsupported native API не считается health failure.
- [ ] False/unmount/user change вызывает dispose; late generation responses отвергаются. Locale change очищает старые descriptors.
- [ ] Production build, fresh empty-cache tab, network/import trace: при false нет feature chunk load/evaluation/modelContext/traffic. Build manifest filename сам по себе не исполнение; prefetch проверяется отдельно.
- [ ] On→off не выгружает JS из памяти, но снимает tools и блокирует новые calls; прямой server route после recreate отсутствует.
- [ ] Login/search/source navigation/RU-EN/StrictMode/HMR проходят без hydration warnings и unhandled rejection.

**Приёмка:** O05/O06/O08/L05 доказаны в production build. Коммит: feat: lazily mount authenticated WebMCP.

## Задача 11. CI, differential regression и native приёмка

**Файлы:** CI, tests, checklist, acceptance/off-comparison/security-evidence.

**Вход → выход:** реализация → actual evidence по каждому ID.

| ID | Сценарий | Ожидание |
|---|---|---|
| O01 | false routes/OpenAPI | нет feature endpoints |
| O02 | false cold import | нет feature imports/services/connections |
| O03 | false valid direct calls | route 404 после допуска middleware, no feature work |
| O04 | false malformed input | no feature parsing/storage/admission |
| O05 | fresh production off page | no chunks/evaluation/modelContext/tools/feature traffic |
| O06 | missing/error settings | import gate closed, UI usable |
| O07 | off baseline auth/API/CSRF | прежние contracts/errors/headers |
| O08 | true→false recreate | no routes/new calls, own tools сняты |
| B01 | ordinary search off/on | exact fields/order/scores/top_k baseline |
| B02 | ordinary auth/rate | сохранены guards и admission |
| B03 | retrieval failures | прежние ordinary errors |
| B04 | common frontend transport/proxy | WebMCP их не изменил |
| C01 | flag/env validation | default off, invalid fail startup |
| C02 | on empty allowlist/anonymous | startup rejected |
| C03 | member/non-member/blocked | effective capability и server denial |
| C04 | same frontend digest | off/on/off без rebuild |
| S01 | filters/max20 | existing retrieval preserved |
| S02 | concept+chunk | правильный primary SourceRef |
| S03 | review sibling | свой slug |
| S04 | legacy missing IDs | no guessed filepath |
| R01 | active/absent/trash | read/404/404, no staging |
| R02 | offset/hash/extra fields | строгие DTO/422 |
| R03 | pipeline/ensure_chunks/FS | не вызываются |
| R04 | huge+NUL SQLite/PG | SQL byte bound до hydration, 413 |
| R05 | emoji long pagination | точные code points |
| R06 | changed content | 409 source_changed |
| R07 | empty/end offset | корректный пустой конец |
| R08 | unavailable/429 | safe error, no retry |
| Q01 | tabs/sessions same user | единые counters |
| Q02 | concurrent budgets | atomic user/global admission |
| Q03 | UTF-8/escaping | actual bytes <=65536 |
| Q04 | fail/disconnect/double finish | корректный accounting |
| Q05 | disconnect sync work | slot до реального completion |
| Q06 | expiry/restart | точный clock/reset documented |
| Q07 | many/non-member keys | bounded state |
| L01 | unsupported browser | ordinary app работает |
| L02 | capability failure | no registration |
| L03 | flag/allowlist revoke | следующий server call denied |
| L04 | login/logout/user change | актуальное поколение |
| L05 | late import/StrictMode/HMR | no zombie tools |
| L06 | partial registration | own-only cleanup |
| L07 | timeout/abort/third call | timeout/cancelled/busy |
| L08 | switch during read | no result leak/navigation |
| T01 | URL/path/identity overrides | rejected |
| T02 | huge metadata/output | bounded valid JSON/pagination |
| T03 | RU/EN | stable names, localized messages |
| A01 | release data policy | owner/agent/corpus/retention записаны |
| A02 | instructions in content | не исполнены кодом tools |
| A03 | spoof agent name/header | не даёт доступ |
| A04 | anonymous agent | no dev exception |
| A05 | SECURITY.md claims | каждое утверждение связано с evidence/ограничением |
| E01 | native search→read→open | правильный источник виден |
| E02 | native discovery/call | agent discovered tools, не прямой callback |
| E03 | rollback | ordinary operations work, routes absent |

- [ ] Blocking backend CI off step: python -m pytest tests/test_webmcp_disabled.py tests/test_webmcp_config.py tests/test_webmcp_search.py -q.
- [ ] Frontend bootstrap gate: node --test test/webmcpBootstrap.test.mjs. O05 пока отдельная обязательная browser acceptance; отсутствие automated runner не обозначать green CI для O05.
- [ ] Явно изолировать env/test DB; off false, on simulation users+allowlist; quota store reset fixture. Не использовать developer .env для доказательств.
- [ ] Выполнить команды отдельно, проверяя exit code:

~~~powershell
# cwd backend, python из .venv
python -m pytest tests/test_webmcp_disabled.py tests/test_webmcp_config.py tests/test_webmcp_search.py tests/test_webmcp_limits.py tests/test_webmcp_api.py tests/test_webmcp_source.py -q
python -m ruff check .
python -m pytest -q --cov=app --cov-report=term-missing --cov-fail-under=85

# cwd frontend, без сломанных npm/npx shims
node --test
node node_modules/eslint/bin/eslint.js .
node node_modules/next/dist/bin/next build

# cwd root
git diff --check
~~~

- [ ] Дождаться обязательных Linux CI/dependency audit/doc-parser/Docker jobs; без external LLM в unit tests.
- [ ] SQL bounds проверить SQLite+PostgreSQL; native on — HTTPS/SSO/proxy пользователя, synthetic corpus, exact browser version.
- [ ] Две вкладки/две сессии одного пользователя и два пользователя: direct HTTP подтверждает quotas и global cap.
- [ ] Off network без polling, latency ordinary retrieval до/после на 30 чередующихся fixed-fixture вызовах; устойчивый p95 regression >5% расследовать, noisy measurement не выдавать за факт.
- [ ] SQL/FS/jobs snapshot: tools не меняют контент и не создают jobs; auth/access service records отделить.
- [ ] Evidence содержит SHA, среду, browser/native setup, команды/exit codes, pass/fail/blocked IDs. Без реальных данных/cookies/tokens.

**Приёмка:** off gate обязателен для любого выпуска; A01/E01/E02 дополнительно для on. Mocks не закрывают native/network cases. Коммит: test: verify WebMCP off and on boundaries.

## Задача 12. SECURITY.md и пакет обоснования для ИБ

**Файлы:** SECURITY.md (существующий, обязательное изменение), tests/artifacts/webmcp/security-evidence.md, docs/WEBMCP.md, docs/PRODUCTION_DEPLOYMENT.md, tests/README.md.

**Вход → выход:** проверенный code diff/evidence → воспроизводимое обоснование безопасности отключённой функции и отдельная оценка on.

- [ ] В SECURITY.md §1 Threat model добавить границы browser agent → frontend → feature API, различие off/on, доверие к identity/документам и отсутствие доказательства личности агента по его имени.
- [ ] В §2 Authentication/authorization описать require_user + global flag + exact user allowlist; anonymous exception отсутствует, blocked user отвергается. Разделить право пользователя читать и допустимость внешней обработки.
- [ ] В §3 Network topology описать условное отсутствие /api/webmcp routes при false, отсутствие нового listener/порта, прежние CORS/CSRF/proxy. Не писать, что наличие 404 само по себе доказывает отсутствие handler.
- [ ] В §4 Configuration добавить defaults, strict validation, restart/recreate, allowlist revoke и необходимость убрать все старые enabled workers.
- [ ] В §5 Data/privacy описать SQL-only read, bounded source/output, user/global quotas, отсутствие записи и правила разрешённого агента/корпуса.
- [ ] В §6 Known limitations раскрыть residual off code/bootstrap/shared retrieval risk, in-memory quota reset, already-loaded JS/data, обычный API вне WebMCP quota, отсутствие иммунитета к prompt injection.
- [ ] В §7 Security change log добавить запись датой фактической реализации: проблема, изменение, отрицательные off tests, on limits и ссылка на security-evidence. Не объявлять planned controls deployed.
- [ ] Добавить подраздел WebMCP disabled-mode assurance. Текст на английском в стиле текущего SECURITY.md; технический смысл:
  «При WEBMCP_ENABLED=false feature router не импортируется/не монтируется, новые endpoints отсутствуют в route table/OpenAPI. Fresh frontend не загружает и не исполняет feature modules. Обычные auth/API contracts сравниваются с baseline. Доказательства относятся к указанной версии и конфигурации; изменение bootstrap/common retrieval и наличие кода в поставке оставляют обычный риск регрессии».
- [ ] Не писать «уязвимостей нет», «риск равен нулю» или «полностью идентично отсутствию кода». Допустимый вывод после зелёных проверок: «для проверенной сборки и конфигурации off не обнаружено нового доступного WebMCP API и автоматически исполняемой feature-логики; перечисленные общие изменения прошли регрессионные проверки».
- [ ] security-evidence содержит матрицу claim → mechanism → negative test → actual result → limitation:

| Тезис для ИБ | Механизм | Доказательство | Граница вывода |
|---|---|---|---|
| Off не открывает новые endpoints | conditional import/include | O01/O03/O04 + route/OpenAPI snapshots | global middleware остаётся |
| Off не запускает feature services | нет imports/initialization | O02 cold subprocess + spies | обычные services работают как прежде |
| Off не исполняет браузерные tools | explicit lazy gate | O05/O06 production network/import trace | минимальный bootstrap и settings остаются |
| Обычные права не расширены | unchanged auth/CSRF/proxy | O07/B02/B04 fixtures | не полный аудит всего приложения |
| Поиск не регрессировал | narrow shared retrieval | B01/B03 exact baseline comparison | sampled scenarios, не доказательство всех входов |
| Выключение реально отзывает новые calls | recreate/drain всех workers | O08/E03 direct calls + browser | уже полученные данные не отзываются |
| Supply chain не расширен | нет новых production dependencies | lockfile/SBOM diff и audit | код самой функции всё равно добавлен |

- [ ] Зафиксировать baseline/head SHA, image digests, runtime config без secrets, tests/exit codes, native browser version, дату проверки и проверяющего. Приложить ссылки на CI run/локальные артефакты только после их существования.
- [ ] Review для ИБ: отдельно отметить доказанные свойства, принятые допущения и неустранённые ограничения. Не объявлять документ согласованным с ИБ до фактического решения; отсутствие замечаний не согласование.
- [ ] Итоговый diff review: route/import graph, common extraction, auth-before-data, quotas, SQL guard, redirects, late imports/session guards. Дополнительный agent reviewer только при разрешённой делегации; иначе self-review и обычный review владельцем.
- [ ] Перед коммитом проверить staged scope/diff --cached --check. Операторская инструкция должна позволять повторить off-проверку без знания истории переписки.

**Приёмка:** SECURITY.md обновлён вместе с кодом; A05 проверен; каждый тезис ИБ имеет реальное evidence и границу применимости. Это обязательное условие выпуска, даже если WebMCP остаётся off. Коммит: docs: substantiate disabled WebMCP security guarantees.

## Задача 13. Выпуск, отзыв, наблюдение и откат

**Вход → выход:** approved code + green off gate + SECURITY evidence + разрешённая среда → проверенное состояние контура.

- [ ] Выпустить false; проверить routes/OpenAPI absence, ordinary login/search/read, fresh browser no feature chunks.
- [ ] До on: single-worker/replica, allowlist, A01 и допустимость агента/корпуса; без решения владельца данных функция off.
- [ ] Synthetic staging true: recreate backend в точном env/project/overlays. Docker restart не перечитывает container env.
- [ ] На том же frontend digest пройти C04/E01/E02/quotas.
- [ ] Репетиция revoke: false либо remove user ID, recreate/drain всех instances. False → route absent; user revoke при global on →403 и capability false.
- [ ] Fresh tab не публикует tools; existing tab снимает на preflight/focus. Для already-admitted server work требуется drain/stop old workers; browser abort недостаточен.
- [ ] Production-on только в согласованном target; deployment command из существующего runbook, без угадывания env/overlays/destination.
- [ ] Первое согласованное окно наблюдения (предложение 24ч): status/code/tool/duration/bytes/quota rejections, без текстов/параметров/токенов. Новые automation/telemetry не создавать автоматически.
- [ ] Немедленное выключение при non-member data, session leakage, quota bypass, off route/feature execution, записи из read tools или ordinary UI regression.
- [ ] Если дефект в общем retrieval/layout/settings, rollback совместимых images: false общий код не откатывает.
- [ ] После rollback O01/O03/O05/O07/E03. БД не откатывать: schema не менялась; уже отданные данные не отзываются.

**Приёмка:** известны target/images/flag/allowlist, evidence rollback и ответственный. Merge/health не заменяют приёмку.

## Сопровождение и Definition of Done

- [ ] OFF-1..OFF-7 spec сопоставлены с O01–O08 и остаточными эффектами.
- [ ] Каждый test ID имеет evidence либо blocked; нет production-on при blocked A01/E01/E02.
- [ ] Baseline ordinary contracts и отдельный review common changes завершены.
- [ ] Прямые HTTP calls и несколько вкладок/сессий подтверждают server gates/budgets.
- [ ] Native on и production-bundle off acceptance проведены.
- [ ] SECURITY.md и security-evidence содержат актуальные, проверенные утверждения; решения ИБ/владельца данных не выдуманы.
- [ ] Владельцы назначены: frontend compatibility, backend access/quota, deployment flag/trial, владелец данных/ИБ — разрешённые условия обработки.
- [ ] Browser/Next/auth/proxy upgrades требуют native probe и O05/O07/O08/L05/L08/E01–E03; новый browser support только по evidence.
- [ ] Инцидент: ограничение feature доступа, synthetic воспроизведение, regression test, повторная приёмка.
- [ ] Новые write tools — отдельный дизайн. Требование физического отсутствия кода — отдельная сборочная опция, не runtime-флаг.

Рекомендуется последовательное исполнение одной сессией из-за связанных gate/quota/serialization/lifecycle контрактов. Этот документ завершает усиление плана; реализация и выпуск не запускаются.
