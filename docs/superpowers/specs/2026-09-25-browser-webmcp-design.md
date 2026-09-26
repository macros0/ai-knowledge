# Браузерный WebMCP: проект решения с усиленной изоляцией

Дата: 25.09.2026. Редакция 2 по результатам оценки безопасности. Статус: проект для реализации; изменены только документы.

## Цель и границы

Агент в браузере с открытым AI Knowledge Service получает четыре инструмента: search_knowledge, list_documents, read_source, open_source. Основной переключатель — WEBMCP_ENABLED; значение по умолчанию false. Для включённого режима дополнительно требуется явный список разрешённых пользователей.

HTTP MCP-сервера, загрузки, удаления, экспорта, изменения тегов, перегенерации и дополнительного LLM-чата в первом выпуске нет. open_source меняет только навигацию вкладки.

Главное требование редакции 2: отключённая функция не публикует новые маршруты, не загружает исполняемый frontend-модуль WebMCP и не меняет обычные API-контракты. Это уменьшает добавочный риск, но не доказывает эквивалентность полному отсутствию кода.

## Исходное состояние и изменения проекта

Проверено в checkout 25.09.2026:

- Settings и get_settings кешируются. Runtime env применяется после restart/recreate backend.
- /api/settings защищён общим require_user; ChatContext получает настройки один раз.
- api.js содержит общий transport, CSRF и обработку ошибок.
- Обычный SearchHit не содержит doc_id/source_slug; merged результаты поиска уже содержат эти идентификаторы.
- GET /documents/{doc_id}/chunks вызывает ensure_chunks: этот путь не подходит для чтения без побочных эффектов.
- Концепты и чанки хранятся в SQL. Некоторые обычные text endpoints имеют staging fallback.
- Backend и существующие лимитеры рассчитаны на один процесс/реплику.
- Изменения Outlook и загрузки выполняются отдельно; они не входят в эту задачу.

Эта редакция заменяет первоначальное решение: общий api.js и обычный SearchHit не расширяются ради WebMCP. Все обращения инструментов к данным идут через отдельную условно подключаемую REST-группу /api/webmcp. Она использует существующие сервисы, но имеет собственные ограничения и сокращённые ответы.

## Архитектура

~~~mermaid
flowchart LR
  Config[Конфигурация backend] --> Boot[Условное подключение router]
  Config --> Settings[GET /api/settings: capability пользователя]
  Settings --> Bootstrap[Минимальный frontend bootstrap]
  Bootstrap -->|только true| Module[Динамический импорт WebMCPProvider]
  Agent[Агент браузера] --> Module
  Module --> Transport[Изолированный transport WebMCP]
  Transport --> Gate[Сессия + allowlist + feature gate + бюджеты]
  Gate --> Search[POST /api/webmcp/search]
  Gate --> List[GET /api/webmcp/documents]
  Gate --> Source[GET /api/webmcp/source]
  Search --> Retrieval[Общий retrieval без смены обычного SearchResponse]
  List --> Registry[Существующий список активных документов]
  Source --> SQL[Ограниченное SQL-чтение]
~~~

Для получения search metadata допускается только узкое извлечение существующего retrieval-кода в общий сервис. Это изменение действует и при off, поэтому проходит отдельную дифференциальную проверку до/после. Авторизация, обычный rate limit, порядок выдачи, поля и ошибки /api/search сохраняются. Другие общие подсистемы не рефакторятся.

## Конфигурация

Основные параметры:

~~~dotenv
WEBMCP_ENABLED=false
WEBMCP_ALLOWED_USER_IDS=[]
~~~

Allowlist содержит точные стабильные User.user_id, а не display name, роль из параметра запроса или имя агента. Пустой список означает отсутствие разрешённых пользователей; включённая конфигурация с пустым списком или AUTH_PROVIDER=disabled отклоняется при старте. В dev используются явные simulation-пользователи; анонимного исключения нет. В production действуют существующие требования к SSO.

Защищённый /api/settings возвращает только webmcp_enabled как эффективную capability текущего пользователя: global flag AND user_id in allowlist, после текущей проверки блокировки/сессии. Не раскрывает allowlist, внутренние квоты или список других пользователей.

Опциональные настраиваемые пределы с безопасными значениями по умолчанию:

| Параметр | Default | Единица и область |
|---|---:|---|
| WEBMCP_REQUESTS_PER_MINUTE | 60 | допущенных запросов/пользователь, окно 60 секунд |
| WEBMCP_GLOBAL_REQUESTS_PER_MINUTE | 300 | допущенных запросов/процесс, окно 60 секунд |
| WEBMCP_MAX_CONCURRENT_PER_USER | 2 | активных серверных операций пользователя |
| WEBMCP_MAX_CONCURRENT_GLOBAL | 8 | активных серверных операций процесса |
| WEBMCP_USER_BYTES_PER_HOUR | 4194304 | 4 MiB успешных JSON-ответов/пользователь, окно 3600 секунд |
| WEBMCP_GLOBAL_BYTES_PER_HOUR | 67108864 | 64 MiB успешных JSON-ответов/процесс, окно 3600 секунд |

Параметры положительные; per-user не превышает global; byte budget не меньше максимального ответа 65536 bytes. Допустимые значения и пределы проверяются при старте. Нельзя передавать quota overrides из browser tool arguments.

Изменение требует restart/recreate backend, без пересборки frontend. Production использует только выбранный OKF_RUNTIME_ENV_FILE. NEXT_PUBLIC-переключателей и независимой копии флага на frontend нет.

## Обязательный контракт режима off

OFF-1. При false app factory не импортирует WebMCP-router/services и не подключает его. В app.routes/OpenAPI нет ни одного /api/webmcp маршрута. Создание приложения не инициализирует quota store, источники или клиентов из WebMCP-модулей.

OFF-2. Прямые запросы к /api/webmcp/* в отключённом приложении получают обычный 404 отсутствующего маршрута. Они не вызывают специфичные validators, quotas, SQL/FS/Qdrant/LLM функции WebMCP. Существующие глобальные middleware авторизации/CSRF могут отработать или отказать раньше; это не обход и не повод ослаблять их ради 404.

OFF-3. В свежей вкладке при false bootstrap не выполняет import WebMCPProvider/adapter/tools/transport, не обращается к modelContext, не регистрирует tools и не посылает /api/webmcp запросы. Запрещён speculative preload этих chunks. Проверяется production bundle/network, а не только mock.

OFF-4. При неизвестной capability, старом backend без поля, 401/403 или сетевой ошибке действует off. Минимальный bootstrap может читать существующий /api/settings после подтверждённого входа и при focus/visible; polling и feature storage work отсутствуют. Эти ограниченные запросы и само поле settings — документированный остаточный эффект.

OFF-5. Обычные SearchRequest/SearchHit/SearchResponse, documents API, общий frontend api.js, auth/proxy и security headers сохраняют прежнее поведение. Допустимы только перечисленные в плане интеграционные изменения, включая отдельно проверяемое извлечение retrieval.

OFF-6. После true → false и recreate всех backend-процессов прямые feature endpoints недоступны. В уже открытой вкладке следующий preflight/focus снимает tools и отменяет свои callbacks. Уже загруженный JS нельзя выгрузить из памяти, уже переданные агенту данные нельзя отозвать. Операции, принятые старым процессом, требуют drain/остановки по процедуре выпуска.

OFF-7. Присутствие файлов/chunks в образе не считается исполнением, но и не является отсутствием кода. Если требуется доказуемое отсутствие модуля в поставке, нужна отдельная сборка без WebMCP; это отдельный scope и не подменяется runtime-флагом.

## Разрешённые агенты и границы доверия

Allowlist разрешает пользователю использовать функцию, но не удостоверяет конкретный агент. Название агента, User-Agent, аргумент tool и произвольный HTTP-header не могут быть основанием выдачи полномочий.

До production-включения владелец данных письменно фиксирует в release-записи: допустимый browser/agent и канал установки, local или external обработку, допустимые категории документов, retention и кто имеет право включать доступ. Для external агента требуется согласованная политика передачи содержимого. При отсутствии такого решения функция остаётся off. Если нельзя ограничить чтение категориями в существующей модели прав, использовать отдельный разрешённый контур/корпус либо не включать функцию.

Не менять CORS/CSRF/SSO/cookie policies и не ослаблять CSP/iframe protection ради WebMCP. Не задавать exposedTo и не разрешать cross-origin tools. Browser policy или Permissions-Policy может быть дополнительной защитой после native проверки, но не заменяет отсутствие backend-маршрутов и не удостоверяет агента.

Контент документов считается недоверенными данными. Описания инструментов статические; untrustedContentHint/readOnlyHint являются подсказками, а не средствами защиты от prompt injection. У сайта нет контроля над тем, что агент сделает с уже полученным текстом.

## Серверный контроль и ограничения чтения

Все три endpoints /api/webmcp требуют require_user, global flag, allowlist, затем общий admission controller. Поиск дополнительно сохраняет существующий search rate limit. Никакого fallback к обычному /api/search или /api/documents при отказе feature endpoint.

Квоты агрегируются по стабильному user_id и по процессу, независимо от вкладки, браузера и новой сессии. Admission атомарно проверяет request budget, concurrency и резерв ответа 65536 bytes. При отказе операции чтения не начинаются. После сериализации резерв заменяется фактическим размером JSON; при ошибке до успешного ответа byte reserve освобождается, попытка запроса остаётся учтённой. При сетевой отмене после формирования ответа списывается его размер, поскольку доставку нельзя надёжно опровергнуть.

Активные серверные операции сохраняют слот до фактического окончания работы. Клиентский AbortSignal сам по себе не останавливает синхронный SQL/Qdrant вызов. Слоты освобождаются в finally; независимые серверные таймауты нужны для запросов к зависимостям.

Хранилище квот ограничено allowlist и окнами, без неограниченного роста по произвольным ключам. В первой версии оно in-memory и совместимо только с существующим single-worker/single-replica deployment. Перезапуск сбрасывает счётчики; это ограничение нагрузки и ускоренной выгрузки, не долговременная DLP-квота. Несколько реплик требуют общего quota store и нового решения до включения.

Фиксированные ограничения:

- Поиск: 1–20 hits (default 5); список: 1–50 документов (default 20), offset <=10000.
- query: после trim 1–8192 code points; tags <=50, каждый 1–128 code points.
- Документный search filter <=512 code points; только поддерживаемые status values.
- Страница источника: 1–12000 code points; SHA-256 продолжения защищает от смешивания версий.
- Источник для чтения: не более 250000 code points и 1048576 UTF-8 bytes. SQL length predicates применяются в том же SELECT, который возвращает content, до материализации в Python. Для PostgreSQL byte guard использует octet_length(content), для SQLite — length(CAST(content AS BLOB)); один length(text) недостаточен при наличии NUL. Превышение → 413 source_too_large; без fallback и без загрузки всего большого текста ради hash.
- Успешный JSON каждого endpoint: <=65536 bytes UTF-8, включая экранирование. Проверяется на backend до отправки; frontend проверяет envelope повторно.
- Timeout клиентского вызова: 30000 мс целиком; не более 2 concurrent calls во вкладке как дополнительный UX-лимит.

Эти пределы распространяются на новые feature endpoints. Пользователь может читать через обычный UI/API согласно прежним правам; WebMCP-квоты не являются запретом выгрузки всего приложения.

## Контракты инструментов и API

SourceRef = {doc_id, kind: concept|chunk, source_slug?, chunk_index?}. Doc ID — 16 lowercase hex. Для concept требуется slug длиной <=512 code points без разделителей путей, управляющих символов и traversal; для chunk — целый index >=0. Готовый URL, filepath, user_id и overrides прав/квот не принимаются.

| Tool | Feature endpoint | Результат |
|---|---|---|
| search_knowledge | POST /api/webmcp/search | hits: SourceRef, title, snippet, score; source_available, truncated |
| list_documents | GET /api/webmcp/documents | doc_id/filename/status, total, next_offset, truncated |
| read_source | GET /api/webmcp/source | content, offsets, total_chars, content_sha256, truncated |
| open_source | GET /api/webmcp/source с limit=1, затем UI navigation | href и navigation_requested=true |

Backend возвращает JSON DTO; transport нормализует ошибки в tool envelope: {ok:true,data} либо {ok:false,error:{code,message,retryable}}. href вычисляется только из проверенного SourceRef и внутренних маршрутов.

Source endpoint читает только существующий SQL-контент активного документа. Проверка активности входит в SQL-чтение; нет staging/FS/pipeline fallback и ensure_chunks. Проверки дают snapshot во время запроса, не гарантию невозможности удаления сразу после ответа.

При offset>0 требуется hash. Несовпадение → source_changed; отсутствующий источник → source_unavailable; пустой сохранённый текст — успешная пустая страница. Сокращение JSON до byte budget корректирует next_offset в code points и выставляет truncated.

Ошибки: feature_disabled, unsupported_browser, authentication_required, permission_denied, invalid_input, source_unavailable, source_changed, source_too_large, rate_limited, quota_exceeded, busy, timeout, cancelled, session_changed, service_unavailable. Не возвращать traceback, SQL, токены, приватные пути или сырой provider response. Автоматических повторов нет.

## Frontend lifecycle

Bootstrap в root layout использует подтверждённую текущую сессию и /api/settings. Только явный true вызывает динамический import. Проверка generation после await не позволяет позднему import смонтировать provider для другого пользователя. При false bootstrap не создаёт feature controller.

Provider управляет собственными registrations/requests, проверяет capability и личность перед вызовом и после чтения. При изменении пользователя, logout, false, unmount или частичной ошибке регистрации отменяется только собственный набор. open_source выполняет navigate только после финального guard.

Изолированный transport принимает только фиксированные same-origin feature paths и контрольные /api/settings, /api/auth/me; использует текущие cookie и CSRF. Общий api.js не меняется. Cross-origin redirect запрещён; responses не кешируются. SSR и первый render одинаковы, browser API не вызывается на сервере.

## Native совместимость и приёмка

Целевая поверхность — document.modelContext; версия браузера, register/call/unregister/cancellation фиксируются фактическим probe. Автоматически подключать navigator-совместимость, SDK или полифилл нельзя. Unsupported browser работает как обычное приложение.

Источники, проверенные при проектировании: [спецификация WebMCP](https://webmachinelearning.github.io/webmcp/), [Chrome WebMCP](https://developer.chrome.com/docs/ai/webmcp), [безопасность инструментов](https://developer.chrome.com/docs/ai/webmcp/secure-tools). Перед выпуском условия native API и trial проверяются заново.

Режим off имеет самостоятельный обязательный CI gate: маршруты/импорты, прямые запросы, отсутствие feature traffic и chunk execution, дифференциальная регрессия обычных API/UI. Нативная приёмка on не заменяет off gate; mock-тесты не заменяют native browser acceptance.

Выпуск: код с false → off gate → разрешённый synthetic staging → native on/off acceptance → согласованное включение. Откат: false + recreate/drain всех backend instances, отсутствие маршрутов, smoke обычных операций. При общей регрессии нужен откат образа — feature flag не откатывает общий код.

## SECURITY.md и обоснование для ИБ

Обязательная часть реализации — актуализация существующего SECURITY.md: §§1–6 описывают угрозы, права, сеть, конфигурацию, данные и остаточные риски WebMCP; §7 фиксирует фактическое изменение и проведённые проверки. Отдельный подраздел disabled-mode assurance связывает каждое утверждение с механизмом и доказательством из tests/artifacts/webmcp/security-evidence.md.

Пакет ИБ содержит baseline/head SHA и image digests, условия запуска, route/OpenAPI и import snapshots, production-browser network/import trace, прямые отрицательные HTTP-тесты, сравнение обычных API/auth/CSRF, dependency/SBOM diff и rollback evidence. Каждый вывод ограничен проверенной версией и конфигурацией; неподтверждённое обозначается blocked, а не passed.

Цель обоснования: показать, что off не добавляет доступных WebMCP endpoints и автоматически исполняемой feature-логики, а неизбежные общие изменения отдельно проверены. Не утверждать «риск равен нулю» или полное тождество отсутствию кода: bootstrap, capability, извлечение retrieval и код в поставке остаются предметом review. Фактическое согласование ИБ и допуск внешней обработки не подразумеваются автоматически.

Обновлённый SECURITY.md и пакет доказательств обязательны для выпуска даже при default false. Пока выполнено только планирование, SECURITY.md не должен объявлять проектируемые механизмы уже внедрёнными.

Схема БД не меняется; миграции, переиндексация и перегенерация не требуются. Код и конфигурация live-среды этим документом не меняются. Подробные задачи: [план разработки](../plans/2026-09-25-browser-webmcp.md).
