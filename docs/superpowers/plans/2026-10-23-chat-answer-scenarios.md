# Сценарии ответа — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Рекомендуемый способ здесь — последовательное выполнение в текущем чате; подагентов запускать только после выбора этого способа пользователем.

**Goal:** Дать пользователю создавать личные/общие сценарии ответа, проверять их и применять из верхней панели чата с автоматическим обновлением и сохранением версии у ответа.

**Architecture:** Каталог и неизменяемые версии хранятся в двух SQL-таблицах. Сервер фиксирует доступную актуальную версию в истории попытки и передаёт её в существующий полный механизм ответа; preview использует тот же путь без сохранения истории. Новый UI и снимки из истории не зависят от существования записи каталога.

**Tech Stack:** Текущие FastAPI/Pydantic, SQLAlchemy/Alembic, PostgreSQL/SQLite, React/Next.js, NDJSON, pytest и Node test runner; новых runtime-зависимостей для функции нет. Отдельно согласовано обновление существующего Next.js до 16.4.2.

**Spec:** [Проект изменения](../specs/2026-10-23-chat-answer-scenarios-design.md). Прочитать оба документа перед реализацией. Статус: утверждён и реализован в изолированной ветке `codex/chat-answer-scenarios`; локальная приёмка завершена. Фактические проверки, уточнения интерфейсов и ограничения: [отчёт приёмки](../reports/2026-10-23-chat-answer-scenarios-acceptance.md).

## Состояние исполнения на 23.10.2026

- [x] Задача 1: wire-схемы, две таблицы, Alembic/companion; SQLite и отдельная PostgreSQL.
- [x] Задача 2: каталог, visibility, авторство, CAS, аудит и копия.
- [x] Задача 3: core/literal payload, direct и multi-pass, бюджеты и защищённое авторство.
- [x] Задача 4: immutable snapshot, latest для следующей попытки, история, NDJSON, selected sources и ошибки.
- [x] Задача 5: независимый preview без истории, общий лимит/допуск и валидация входа.
- [x] Задача 6: picker/manager/editor/badge, защита черновика, RU/EN; весь frontend suite и сборка.
- [x] Задача 7: локальная приёмка и отчёт завершены: backend 4826 passed/109 skipped, frontend 721 passed/1 skipped, PostgreSQL 4 passed, backup/restore, реальная модель, роли, руководства/PDF, gate/build. Непроверенные внешние/ручные пункты явно перечислены в отчёте.
- [x] Дополнительно согласованное обновление Next.js: отдельный локальный коммит `f613f12`; audit 0, gate/build PASS.

Детальные шаги ниже сохраняют первоначальный план; фактические имена тестов объединены в тематические наборы, а отклонения записаны в отчёте. Для preview сохранена существующая оркестрация API с общим request-local context/rate boundary вместо широкого переноса attempt lifecycle. Непроверенные пункты браузера/внешнего CI не считаются выполненными по косвенным тестам.

## Global Constraints

- Изменяется подготовка ответа, а не генерация концептов или формула поиска/индексации.
- Режим `scenario` использует внутренний `full`; клиент передаёт только `scenario_id`, последнюю версию выбирает сервер на каждом новом запросе.
- Личный по умолчанию; общий доступен всем аутентифицированным пользователям. Редактирование/удаление автором или администратором для общего сценария; чужие личные недоступны администратору. Копия всегда личная.
- Название 1–120, описание 0–1000, инструкции 1–12000 символов после trim краёв; список default 50/max 100; q max 200.
- Начальный снимок неизменяем и записывается вместе с attempt до поиска/LLM; историю не связывать FK с удаляемым каталогом.
- Точный текст сценария не является гарантией побитового воспроизведения LLM-ответа. Не сохранять дополнительно полный документный промпт.
- Обязательные правила достоверности/прав доступа сохраняются; инструкции пользователя не исполняются как код и не форматируются шаблонизатором.
- `AUTH_PROVIDER=disabled`: функция недоступна. `CHAT_SCENARIOS_ENABLED=false` отключает новые операции, но не чтение снимков истории.
- Preview не пишет историю/каталог, использует обычный chat rate-limit, admission и LLM scheduler ровно один раз.
- Существующие modes, legacy API, selected-source semantics, настройки и история не должны регрессировать.
- RU/EN и backend UI-манифесты обновляются до pytest. Прочитать `frontend/AGENTS.md` и относящиеся к реализации локальные Next.js docs до изменения UI-кода.
- Сохранить чужие изменения. На момент планирования изменены `backend/pyproject.toml` и `docs/superpowers/specs/2026-10-21-audit-storage-modes-design.md`; перед исполнением проверить состояние повторно.
- Для каждого этапа проверки сначала на изолированных тестовых данных. Рабочие миграции — только после резервной копии и в согласованном окне.

## Review Focus

1. Публикация/редактирование одновременно с запросом: один неизменяемый snapshot и линейный порядок допуска; тесты задач 2 и 4.
2. Multi-pass отвечает иначе, чем direct: scenario/core должны попасть в нужные фактические вызовы и их расчёт бюджета; тесты задачи 3.
3. Повтор/selected sources/assessment fallback теряют scenario_id или выполняют лишний поиск; тесты задач 4 и 6.
4. Preview или запоздалый сетевой ответ портят основной чат/новый черновик, обходят лимиты; тесты задач 5 и 6.
5. Удаление/снятие публикации и история создают утечку чужих инструкций либо ломают старый ответ; тесты задач 2, 4 и 6, двухпользовательская приёмка задачи 7.

## Карта файлов и границ

| Зона | Создать | Изменить |
|---|---|---|
| Данные | `backend/app/services/answer_scenario_schema.py`, `backend/scripts/migrate_answer_scenarios.py`, новый Alembic revision | `backend/app/db/models.py` |
| Каталог | `backend/app/models/answer_scenarios.py`, `backend/app/services/answer_scenarios.py`, `backend/app/api/answer_scenarios.py` | `backend/app/main.py`, `backend/app/config.py`, `backend/app/api/settings.py`, `backend/app/api/errors.py`, `backend/app/models/schemas.py`, `.env.example` |
| Промпты | `backend/app/services/chat_prompt_builder.py`, `backend/prompts/chat_scenario_core.md` | `backend/app/prompts/okf.py`, `backend/app/prompts/store.py`, `backend/app/api/chat.py` |
| Выполнение/preview | `backend/app/services/chat_execution.py` | `backend/app/api/chat.py`, `backend/app/services/chat_history.py`, `backend/app/services/chat_stream.py`, `backend/app/api/answer_scenarios.py` |
| UI | `frontend/src/components/ChatScenarioPicker.jsx`, `AnswerScenarioManager.jsx`, `AnswerScenarioEditor.jsx`, `ChatScenarioBadge.jsx`; `frontend/src/lib/chatScenarios.mjs` | `frontend/src/context/ChatContext.js`, `ChatPanel.jsx`, `ChatMessageView.jsx`, `ChatHistoryShared.jsx`, `frontend/src/lib/api.js`, `chatStream.mjs`, `chatAnswerState.mjs`, `chatComposer.mjs`, `chatSourceSelection.mjs`, `chatSourceAssessment.mjs`, `chatRecentHistory.mjs`, `frontend/src/app/globals.css` |
| Контракты/документация | Тесты перечислены по задачам | `backend/app/services/audit.py`, `audit_catalog.py`, `frontend/src/i18n/locales/ru.js`, `en.js`, `backend/app/i18n/ui_keys.json`, `ui_en.json`, `SECURITY.md`, пользовательские руководства |

В строке UI имена без полного пути относятся к последней указанной директории того же типа (`components` или `lib`). Большую общую реорганизацию `chat.py` не делать: выделить только общий admission/контроль сохранения, необходимый preview и обычному сценарию. `select_batches` получает уже `full`, поэтому поддержка внешнего `scenario` в его алгоритме не нужна.

## Task 1: Схема данных и согласованный wire-контракт

**Files:** новые `backend/app/models/answer_scenarios.py`, `backend/app/services/answer_scenario_schema.py`, `backend/scripts/migrate_answer_scenarios.py`, Alembic revision с описанием `answer_scenarios`; изменения `backend/app/db/models.py`, `backend/app/models/schemas.py`, `backend/app/config.py`, `.env.example`.

**Tests:** `backend/tests/test_answer_scenario_schema.py`, `backend/tests/test_answer_scenario_migration.py` (создать).

**Interfaces:**

- Pydantic `ScenarioCreate(name, description='', instructions, visibility='private')`; `ScenarioUpdate` добавляет `expected_version: int >= 1`; `ScenarioCopy(expected_version)`.
- `ScenarioSummary(id, owner_id, owner_username, visibility, version, name, description, created_at, updated_at, can_edit)`; `ScenarioOut` добавляет instructions/hash.
- Frozen `ScenarioSnapshot` содержит поля из spec §6; `ScenarioExecution(status, model_id=None, prompt_fingerprint=None)`. Здесь же определить внутренний `ScenarioInstructions` Protocol с read-only `name`, `description`, `instructions`; snapshot и черновик удовлетворяют ему структурно.
- `ChatRequest.response_mode`/`ChatResponse.response_mode` допускают `scenario`; request принимает `scenario_id: UUID | None`; response — nullable snapshot/execution и `preview: bool=False`; `ChatSettingsOut.scenarios_available: bool=False`.
- `upgrade_answer_scenario_schema(connection) -> None` обслуживает Alembic и companion. Для dev companion предусмотреть dry-run по умолчанию и `--apply`.

- [ ] Добавить тесты `test_scenario_request_requires_id`, `test_non_scenario_rejects_id`, `test_selected_scenario_requires_session`, `test_scenario_limits`: проверить точные лимиты 120/1000/12000, пустые инструкции, лишние поля owner/version, сочетание selection и doc_ids.
- [ ] Добавить тесты миграции: чистая БД, повторный upgrade, БД после create_all без актуального Alembic, несовместимые таблицы; строки chat_messages до/после совпадают; FK/unique version работают; применять миграцию только к тестовой SQLite/отдельной PostgreSQL.
- [ ] Запустить из `backend`: `.\.venv\Scripts\python.exe -m pytest tests/test_answer_scenario_schema.py tests/test_answer_scenario_migration.py -q`. До реализации ожидается отсутствие новых типов/таблиц.
- [ ] Реализовать модели и индексы spec §6, проверки request/response, config flag. Генерировать Alembic revision от проверенного актуального head; отметить его фактический путь в этом плане. Не менять существующие таблицы чатов.
- [ ] Повторить тесты; ожидается PASS. Проверить diff миграции: только новые таблицы/индексы, без unrelated DDL.

## Task 2: Каталог, авторизация, CAS и аудит

**Files:** новые `backend/app/services/answer_scenarios.py`, `backend/app/api/answer_scenarios.py`; изменения `backend/app/main.py`, `backend/app/api/settings.py`, `backend/app/api/errors.py`, `backend/app/services/audit.py`, `backend/app/services/audit_catalog.py`, `SECURITY.md`.

**Tests:** создать `backend/tests/test_answer_scenarios.py`; дополнить `backend/tests/test_audit.py`, `test_audit_event.py`.

**Interfaces (services):**

- `list_scenarios(user: User, *, scope: str, q: str, limit: int, offset: int) -> ScenarioListOut`.
- `get_scenario(scenario_id: str, user: User) -> ScenarioOut`.
- `create_scenario(user: User, data: ScenarioCreate) -> ScenarioOut`.
- `update_scenario(scenario_id: str, user: User, data: ScenarioUpdate) -> ScenarioOut`.
- `copy_scenario(scenario_id: str, user: User, expected_version: int) -> ScenarioOut`.
- `delete_scenario(scenario_id: str, user: User, expected_version: int) -> None`.
- `resolve_scenario_snapshot(session: Session, scenario_id: str, user: User) -> ScenarioSnapshot`: вызывается внутри короткой транзакции допуска, не открывает глобальный cache.

- [ ] Написать `test_private_hidden_from_other_user`, `test_public_read_only_for_non_owner`, `test_viewer_can_create_and_publish`, `test_copy_is_private_independent_v1`, `test_unpublish_revokes_new_reads`, `test_deleted_id_indistinguishable_from_private`.
- [ ] Написать `test_update_cas_conflict`, `test_visibility_change_increments_version`, `test_noop_keeps_version`, `test_concurrent_save_has_one_winner`, `test_copy_rejects_stale_source_version`. Конкурентный тест выполнить на PostgreSQL, остальные также на SQLite.
- [ ] Написать `test_disabled_identity_has_no_scenario_capability`, `test_feature_disabled_blocks_mutations`, `test_catalog_pagination_search_no_private_leak`, `test_audit_omits_user_text_and_rolls_back_on_failure`. В security-тестах включить существующие middleware/CSRF, а не только прямые вызовы функций.
- [ ] Запустить из `backend`: `.\.venv\Scripts\python.exe -m pytest tests/test_answer_scenarios.py tests/test_audit.py tests/test_audit_event.py -q`; подтвердить ожидаемые новые failures до реализации.
- [ ] Реализовать API spec §7, CAS и snapshot-resolution, короткие транзакции. Обновить capabilities settings через текущую identity; добавить только фиксированные error codes. Сообщения не раскрывают чужие личные метаданные.
- [ ] Зарегистрировать audit create/update/publish/unpublish/copy/delete с ID/версией/видимостью; использовать текущий `audit.record_in_session` в транзакции изменения ресурса, не отдельный commit `AuditService.append`. Обновить соответствующие разделы SECURITY, включая viewer и retained snapshots. Повторить тесты, ожидается PASS.

## Task 3: Единая политика промптов для direct и multi-pass

**Files:** новый `backend/app/services/chat_prompt_builder.py`, `backend/prompts/chat_scenario_core.md`; изменения `backend/app/prompts/okf.py`, `backend/app/prompts/store.py`, `backend/app/api/chat.py`.

**Tests:** создать `backend/tests/test_chat_scenario_prompts.py`; дополнить `backend/tests/test_chat_answer_modes.py`, `backend/tests/test_chat_token_budget.py`, `backend/tests/test_authorship_evidence.py`.

**Interfaces:**

- Frozen `ScenarioPromptSet(core_system: str, map_system: str, reduce_system: str, synthesis_system: str, fingerprint: str)` — фактические строки фиксируются на один запуск.
- `build_scenario_prompts(query: str, locale: str, settings: Settings) -> ScenarioPromptSet`.
- `scenario_user_payload(*, query: str, instructions: str, context: str | None = None, facts: list[dict] | None = None) -> str`: JSON/явные поля, без template evaluation пользовательского текста.
- `_answer_mode(..., scenario: ScenarioInstructions | None = None, scenario_prompts: ScenarioPromptSet | None = None) -> str`: keyword-only параметры. Внутри `generation_mode='full'` при `req.response_mode=='scenario'`; внешний режим в metadata не заменяется. В preview валидный внутренний `full` использует тот же переданный набор инструкций.

- [ ] Добавить тест `test_direct_scenario_uses_core_and_literal_instructions`: `{locale}`, фигурные скобки, XML-like delimiters и multiline в пользовательских инструкциях переданы буквально; обычный `_chat_system_prompt` неизменён.
- [ ] Добавить `test_multipass_map_and_synthesis_receive_scenario`: принудительно минимум два пакета, проверить фактические вызовы mock LLM, валидируемые факты и допустимые `[N]`; reduce сохраняет source/quote и не выдаёт пользовательскую JSON-схему вместо внутренней.
- [ ] Добавить `test_budget_counts_scenario_on_every_relevant_call`, `test_prompt_snapshot_stable_across_hot_reload`, `test_invalid_citations_still_rejected`, `test_authorship_returns_protected_status`. Проверить длинную инструкцию, без тихого усечения; empty evidence не превращается в выдуманный ответ.
- [ ] Запустить из `backend`: `.\.venv\Scripts\python.exe -m pytest tests/test_chat_scenario_prompts.py tests/test_chat_answer_modes.py tests/test_chat_token_budget.py tests/test_authorship_evidence.py -q`.
- [ ] Реализовать обязательный слой и зарегистрированный fallback с синхронным текстом; отделить только правила формы от инвариантов. Обычные режимы сохраняют прежние шаблоны/runtime override. Авторство остаётся защищённым форматом с явной меткой.
- [ ] Повторить проверки; отдельно убедиться, что они проверяют переданные в LLM строки и budgets, а не только существование builder-функций.

## Task 4: Фиксация версии, история, NDJSON и все повторные пути

**Files:** изменения `backend/app/api/chat.py`, `backend/app/services/chat_history.py`, `backend/app/services/chat_stream.py`, `backend/app/models/schemas.py`.

**Tests:** создать `backend/tests/test_chat_scenarios.py`; дополнить `test_chat_attempt_history.py`, `test_chat_selected_sources.py`, `test_chat_retained_draft.py`, `test_chat_stream.py`, `test_chat_source_assessment.py`.

**Interfaces:**

- `begin_attempt(..., response_mode: str, *, scenario_id: str | None = None) -> AttemptRef`: расширить текущую функцию; внутри той же session transaction вызвать `resolve_scenario_snapshot`, записать snapshot вместе с placeholder. Повтор того же attempt_id не перечитывает latest, не изменяет снимок и не запускает LLM второй раз.
- `read_attempt_scenario(ref: AttemptRef, user: User) -> ScenarioSnapshot | None`: возвращает сохранённый снимок с проверкой владельца истории.
- `set_scenario_execution(ref: AttemptRef, user: User, execution: ScenarioExecution) -> None`: меняет только execution, не snapshot.
- `progress {phase:'scenario', scenario: snapshot, scenario_execution:{status:'accepted'}}` после start; `ChatResponse` повторяет итоговые snapshot/execution. Ранние return (no sources/assessment reject) тоже сохраняют метаданные.

- [ ] Добавить `test_running_v1_next_request_v2`, `test_unpublish_after_accept_keeps_snapshot`, `test_update_delete_resolution_races`, `test_reused_attempt_never_rewrites_snapshot`. Для race использовать барьеры/транзакции, не произвольные sleep.
- [ ] Добавить `test_selected_scenario_uses_saved_sources_without_retrieval`, `test_ordinary_selected_sources_keep_full`, `test_scenario_stale_source_rejected_before_llm`, `test_assessment_retry_preserves_scenario_id`.
- [ ] Добавить `test_stop_and_retained_draft_keep_scenario`, `test_history_snapshot_survives_delete`, `test_old_history_has_no_fake_scenario`, `test_stream_metadata_before_sources_and_error`, `test_request_mode_stays_scenario_after_full_engine`.
- [ ] Запустить из `backend`: `.\.venv\Scripts\python.exe -m pytest tests/test_chat_scenarios.py tests/test_chat_attempt_history.py tests/test_chat_selected_sources.py tests/test_chat_retained_draft.py tests/test_chat_stream.py tests/test_chat_source_assessment.py -q`.
- [ ] Реализовать snapshot до тяжёлой работы; расширить source-selection allowlists и убрать захардкоженный ответ `full` только там, где нужен `scenario`. Проверить все присваивания retrieval_metadata на сохранение snapshot.
- [ ] Обновить сохранённый `chat_request` для replay: ID сценария, без клиентского snapshot/version. Fingerprint запроса для attempt conflict включает scenario_id, а реальный snapshot остаётся серверным. Повторить тесты до PASS.

## Task 5: Preview через общий обработчик без записи в историю

**Files:** новый `backend/app/services/chat_execution.py`; изменения `backend/app/api/chat.py`, `backend/app/api/answer_scenarios.py`, `backend/app/models/answer_scenarios.py`, `backend/app/services/chat_stream.py`.

**Tests:** создать `backend/tests/test_answer_scenario_preview.py`; дополнить `test_chat_diagnostics_integration.py`.

**Interfaces:**

- `ScenarioPreviewRequest(draft: ScenarioCreate, request: ScenarioPreviewSearch)`, где `ScenarioPreviewSearch` содержит query/locale и те же поисковые поля ChatRequest, но не session_id/attempt_id/source_selection/scenario_id/response_mode. Не принимать неизвестные поля.
- `ChatRunContext`: серверный контекст с `persist_history: bool`, `attempt_ref: AttemptRef | None`, `scenario: ScenarioInstructions | None`, `scenario_prompts: ScenarioPromptSet | None` и `scenario_execution: ScenarioExecution | None`. Публичной моделью API не является.
- `run_chat_request(req: ChatRequest, user: User, *, answer: Callable, preview: ScenarioCreate | None = None) -> ChatResponse` в `chat_execution.py`: общий admission/rate-limit, attempt lifecycle и вызов переданного `answer(req, user, settings, *, run: ChatRunContext)`. Сервис не импортирует API-модуль; `chat.py` передаёт свой `_answer` как callback, избегая циклической зависимости. `_answer_selected` и `_answer_mode` получают соответствующие поля `run`.
- Preview-маршрут преобразует разрешённые поисковые поля в валидный `ChatRequest(response_mode='full')` без session/attempt/selection/scenario ID; черновик передаётся отдельным `preview`. Нормальный маршрут не принимает этот аргумент от клиента. В preview ответ помечается `response_mode='scenario'`, `scenario=null`, `preview=true`; версию «0» не создавать. Поле `persist_history=false` пропускает обе ветки сохранения, включая legacy `store_turn`.

- [ ] Добавить `test_preview_no_database_history_or_versions`, `test_preview_respects_document_visibility_and_scope`, `test_preview_matches_saved_execution_payload`, `test_preview_rejects_session_and_selection`.
- [ ] Добавить `test_preview_uses_shared_rate_limit_once`, `test_preview_busy_and_cancel_release_slots`, `test_preview_no_instruction_text_in_diagnostics`, `test_preview_does_not_cancel_chat_attempt`.
- [ ] Запустить из `backend`: `.\.venv\Scripts\python.exe -m pytest tests/test_answer_scenario_preview.py tests/test_chat_diagnostics_integration.py -q`.
- [ ] Выделить минимальную общую orchestration-функцию без копирования большого `_answer_mode`; маршруты чата делегируют ей. Явно пропускать store_turn/begin_attempt в preview, сохраняя проверки доступа и финального состояния источников. Поток preview не привязан к попытке чата; отмена через disconnect.
- [ ] Проверить, что обычный sync `/chat` и `/chat/stream` продолжают применять rate/admission ровно один раз. Повторить тесты до PASS.

## Task 6: Панель, редактор, preview и снимки в UI

**Files:** UI-файлы из карты; все новые компоненты лежат в `frontend/src/components/`; helper `frontend/src/lib/chatScenarios.mjs`; RU/EN и генерируемые манифесты.

**Tests:** создать `frontend/test/chatScenarios.test.mjs`, `frontend/test/answerScenarioEditor.test.mjs`; дополнить `chatComposer.test.mjs`, `chatSourceSelection.test.mjs`, `chatSourceAssessment.test.mjs`, `chatStreamModes.test.mjs`, `chatRecentHistory.test.mjs`.

**Interfaces:**

- ChatContext: `selectedScenarioId`, `setSelectedScenarioId`, актуальная карточка/статус доступности; состояние редактора локально в manager, не в ленте.
- `requestScenarioId` — стабильное поле frontend requestOptions; network mapping → `scenario_id` только для mode `scenario`; `message.scenario` — server snapshot, не текущий объект каталога.
- API wrappers: `listAnswerScenarios(params)`, `getAnswerScenario(id)`, `createAnswerScenario(data)`, `updateAnswerScenario(id,data)`, `deleteAnswerScenario(id,expectedVersion)`, `copyAnswerScenario(id,expectedVersion)`, `previewAnswerScenario(body,options)` с signal и NDJSON callbacks.
- `ChatScenarioBadge({scenario, execution})` одинаково используется в live/history. В preview надпись «Черновик», не persisted badge.

- [ ] Написать тесты helpers: переключение режимов удаляет ID из обычного запроса, replay сохраняет только ID, «новый поиск» очищает ID, выбранные источники сохраняют scenario без нового scope, old history не получает выдуманную версию.
- [ ] Написать тесты редактора/state: private default, пределы строк, dirty-текст при 409, stale preview ignored, cancel отдельный, readonly чужого общего, независимая копия, сообщение при unpublish, очистка кэша при смене identity.
- [ ] Запустить из `frontend`: `node --test test/chatScenarios.test.mjs test/answerScenarioEditor.test.mjs test/chatComposer.test.mjs test/chatSourceSelection.test.mjs test/chatSourceAssessment.test.mjs test/chatStreamModes.test.mjs test/chatRecentHistory.test.mjs`.
- [ ] Реализовать компактный picker/manager/editor по spec §4. Встроить keyboard/focus/aria по текущим modal-паттернам; не создавать новый глобальный UI framework и не менять существующие кнопки/фильтры без необходимости.
- [ ] Подключить preview отдельным controller/request generation ID; несохранённый текст и пробный ответ не кладутся в localStorage или основной ChatContext. Показать actual server version на live ответе сразу после scenario progress.
- [ ] Обновить все message/history adapters, selected-source/retry/assessment helpers и final response merge. Снимок показывать без запроса актуального каталога; unknown metadata в старых сообщениях игнорировать безопасно.
- [ ] Обновить `ru.js` и `en.js`, включая API errors/audit labels и пояснение protected_authorship. Из `frontend` выполнить `node scripts/export-ui-keys.mjs` до backend pytest. Повторить frontend tests, ожидается PASS.

## Task 7: Интеграционная приёмка, руководства и готовность к выпуску

**Files:** `docs/OKF_User_Guide.md`, `docs/OKF_User_Guide_EN.md`, их PDF; `docs/USER_GUIDE_MAINTENANCE.md`, `SECURITY.md`, относящиеся к сценарию новые скриншоты `docs/assets/user-guide/` и точные копии в `screenshots/`; новый отчёт `docs/superpowers/reports/2026-10-23-chat-answer-scenarios-acceptance.md` с фактической датой проведения, если она другая.

**Interfaces:** утверждённый каталог, общий движок и UI задач 1–6. Эта задача не расширяет функцию дополнительными сценариями/аналитикой.

- [ ] Запустить весь тематический backend набор из предыдущих задач плюс `test_chat.py`, `test_chat_history.py`, `test_chat_locale.py`, `test_chat_search_scope.py`, `test_chat_result_depth.py`, `test_chat_recent_history.py`. Ожидается PASS; отдельно записать фактическую команду, число тестов и пропуски.
- [ ] На изолированной PostgreSQL выполнить upgrade/повтор upgrade/конкурентный CAS и race-допуск; SQLite прогон не объявлять проверкой PostgreSQL. Проверить сценарий старой dev-БД без продвижения её общей Alembic-версии.
- [ ] В браузере с пользователями A/B пройти spec §11: публикация/копия/unpublish/delete, v1→v2 во время генерации, отказ в чужом редактировании, сценарий после перехода между страницами, live/recent/full/admin history, keyboard и 360/768/1440 px.
- [ ] На реальной модели прогнать три сценария и обычный ответ на одинаковых доступных источниках, плюс multi-pass, авторство, недостаток данных и конфликтующие инструкции. Проверить цитаты по оригиналам. Зафиксировать время, пакеты, версию/модель и результат качества без помещения закрытых данных в публичный отчёт. Результаты моков не использовать как утверждение о качестве модели.
- [ ] Дополнить RU/EN руководства реальным UI и ограничениями: следующий запрос использует latest, snapshot не отзывается unpublish, preview не сохраняется, авторство защищено. Собрать PDF штатным `python scripts/build-user-guides.py`, отрендерить и проверить новые страницы по `docs/USER_GUIDE_MAINTENANCE.md`. Снимки в двух директориях должны совпадать по SHA-256.
- [ ] Из корня: `node scripts/check-project.mjs`; `git diff --check`. Из `frontend`: `node node_modules/next/dist/bin/next build` в изолированном checkout/окне, где это не конфликтует с работающим dev server. Не использовать сломанные npm/npx shims.
- [ ] Прогнать полный backend suite и остальные обязательные CI-проверки согласно `docs/DEVELOPMENT_CHECKS.md` при подготовке доставки; не выдавать локальный быстрый набор за полный CI. Если внешняя среда недоступна — явно записать непроверенную часть.
- [ ] Проверить feature-off rollback: новые сценарные операции недоступны, обычный чат и старые снимки читаются. Рабочую миграцию проводить только после отдельного backup/restore proof по spec §10; до этого приёмка использует тестовую БД.
- [ ] Заполнить отчёт, отметить выполненные пункты этого плана и оставшиеся ограничения. Перед коммитом проверить список файлов, включать только относящиеся к задаче изменения; коммит/пуш/PR выполнять в рамках полученной авторизации, не включать посторонние файлы.

## Самопроверка плана

- Согласованные UI/visibility/version решения → задачи 1, 2, 4, 6.
- Правила источников, direct/multi-pass, авторство и budgets → задача 3, реальная модель в 7.
- Snapshot/ошибки/остановка/replay/selected sources → задачи 4 и 6.
- Preview без побочных записей/отдельная отмена → задачи 5 и 6.
- Auth/CAS/audit/disabled identity → задачи 1, 2 и 7.
- Миграция, совместимость, flag-off, документы и визуальная проверка → задачи 1 и 7.

Реализация выполнена последовательно в текущем чате. Результаты проверок и ограничения приведены в связанном отчёте приёмки; этот исходный детальный checklist не подменяет его фактические результаты.
