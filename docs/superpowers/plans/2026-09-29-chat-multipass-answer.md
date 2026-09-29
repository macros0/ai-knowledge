# Три режима чата без продолжения остановленного ответа — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать супербыстрый список документов, быстрый ответ в один проход и полный ответ по всем найденным фрагментам; остановленная попытка запускается заново с нуля.

**Architecture:** Общий retrieval сразу отдаёт полный список источников. Один NDJSON запрос выбирает нулевой, одиночный или последовательный многошаговый LLM-путь; промежуточные факты живут только в памяти worker. В истории сохраняются сообщение, режим, источники и результат/статус, без сохранения порций для продолжения.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic при расширении ChatMessage, PostgreSQL/SQLite, существующий LiteLLM клиент/планировщик, Next.js/React, NDJSON, pytest, node:test.

**Spec:** [Три режима ответа чата](../specs/2026-09-29-chat-multipass-answer-design.md).

## Global Constraints

- Этот план заменяет прежний план с chat_answer_runs, долговременными checkpoint, retention 7 дней и Continue. Не реализовывать эти элементы.
- `response_mode=documents|fast|full`; подписи UI «Найти источники / Быстрый ответ / Полное исследование» и короткие описания дословно из спецификации. Новый UI по умолчанию full, как в предыдущем предложении. Старый API без поля сохраняет прежний путь; capability сообщает новые режимы.
- sources одинаковы при одинаковом фиксированном поиске во всех режимах. Нет среза chat top_k или раннего суммарного лимита для списка; ограничения поисковых веток и индивидуальных выдержек сохраняются. Порог 70% не используется.
- documents: 0 generation/tokenizer вызовов и без слота генерации. fast: не более 1 LLM-вызова, без скрытых completion retries. full: последовательная обработка всех допустимых единиц, затем объединение, либо один прямой ответ, если всё помещается.
- Полный бюджет: `input_tokens + output_token_reserve + 256 <= context_window_tokens`, дополнительно CHAT_MAX_CONTEXT_CHARS. Счётчик соответствует модели и шаблону. Неизвестный бюджет блокирует только генерацию, источники сохраняются.
- Остановка по кнопке/hidden/pagehide/уходу/новому отправленному вопросу окончательная. Blur и набор текста не останавливают. «Запустить заново» создаёт новый ID и новый поиск; после возврата нет автозапуска.
- Порции/факты не записываются в БД/файлы. История и обычные права доступа сохраняются. Фокус и прокрутка не меняются от событий потока.
- Текущий LLM timeout 180 секунд — на вызов; клиентский idle timeout 210 секунд — на тишину потока, heartbeat каждые 5 секунд. Не отключать конечные server timeout ради heartbeat.
- Reranker — [отдельная задача](../backlog/2026-09-29-rag-reranker-evaluation.md). Не добавлять новые модели/индексы/очереди/SSE.
- Общий грязный checkout: только относящиеся к задаче пути/хунки, не git add .; тестовые миграции только на отдельной базе.

## Review Focus

- Поздний delta/result старого вопроса не меняет новый ответ (задачи 4–5).
- Остановка одновременно с commit: один терминальный результат, никакого следующего LLM-вызова (3–4).
- Полная строка таблицы/контекст письма не теряются; одинаковый текст с разным авторством не дедуплицируется (2).
- Поиск работает при недоступном генераторе; fast не вызывает скрытый второй completion (1–2, 4).
- Поток дольше 210 секунд живёт при прогрессе, зависший LLM завершается по своему таймауту (4, 6).

## Интерфейсы и файлы

`backend/app/services/chat_answer_types.py`: `ResponseMode=Literal['documents','fast','full']`; `RetrievedChat(query, blocks, sources, retrieval_metadata)`; `EvidenceSpan(text, source_indices, offsets, kind, partial)`; `EvidenceBatch(batch_id, spans, input_tokens)`; `EvidenceFact(text, support[{source_index,quote}])`; `AttemptRef(attempt_id, session_id, assistant_message_id)`.

`chat_retrieval.py` — общий поиск; `chat_evidence.py` — безопасная подготовка/упаковка; `chat_token_budget.py` — подсчёт; `chat_answer_engine.py` — обработка выбранного режима. Существующие `chat_history.py`, `chat_stream.py`, `api/chat.py` отвечают за историю, транспорт и admission. Не создавать отдельный durable run store.

Frontend: `lib/chatAnswerState.mjs` — адресованные события и группировка документов; `lib/chatAnswerController.mjs` — один fetch, cancel и restart; `components/ChatAnswerControls.jsx` — режим/остановка/новый запуск. Existing ChatPanel остаётся представлением.

## Task 1: Контракт и общий набор источников

**Files:** создать `backend/app/services/chat_answer_types.py`, `chat_retrieval.py`, `backend/tests/test_chat_modes.py`; изменить `backend/app/models/schemas.py`, `api/chat.py`, `api/settings.py`, `services/context_builder.py`, `services/retrieval_hydration.py`.

**Interfaces:** `ChatRequest.response_mode: ResponseMode | None=None`; `ChatRequest.attempt_id: UUID | None=None`; `ChatSettingsOut.response_modes: list[ResponseMode]`; `retrieve_chat(req, user, settings, *, all_retrieved: bool) -> RetrievedChat`; `merge_and_format(..., limit_total_chars: bool=True)`; `format_context(..., source_indices: list[int] | None=None)`. ChatResponse возвращает AttemptRef и выбранный режим для нового пути. Sources дополняются необязательными `source_index`, `submitted_parts`, `completed_parts`, `parts_total`, `cited`, `partial`; старые значения совместимы с историей.

- [ ] Написать тесты `test_modes_share_all_sources` (IDs одинаковы при всех трёх режимах); `test_legacy_top_k_unchanged`; `test_mail_modes_filter_before_generation`; `test_sources_survive_small_context_and_llm_failure`; `test_legacy_source_defaults`.
- [ ] Из backend выполнить `.venv/Scripts/python.exe -m pytest -q tests/test_chat_modes.py --basetemp=../tests/tmp/pytest-runs/chat-modes -p no:cacheprovider`; проверить FAIL из-за отсутствующих интерфейсов.
- [ ] Выделить существующий retrieval без смены фильтров/RRF; новые режимы не режут набор по chat top_k/общему char cap, нумерация стабильна. Супербыстрый возвращает sources и локализованное число уникальных документов; не вызывает authorship/LLM/tokenizer. API без режима остаётся прежним.
- [ ] Повторить новый файл плюс существующие test_chat.py, test_context_builder.py, test_chat_mail_filter.py, test_chat_locale.py с отдельным basetemp. Проверить PASS; коммит `feat: add three chat response modes and common retrieval` только по файлам задачи.

## Task 2: Контекст, один проход и полный ответ

**Files:** создать `backend/app/services/chat_evidence.py`, `chat_token_budget.py`, `chat_answer_engine.py`, `backend/prompts/chat_evidence.md`, `chat_synthesis.md`, `backend/tests/test_chat_evidence.py`, `test_chat_token_budget.py`, `test_chat_answer_engine.py`; изменить `services/authorship_evidence.py`, `services/citation.py`, PromptStore/defaults и при необходимости точечно `llm_client.py`.

**Interfaces:** `prepare_evidence(retrieved) -> list[EvidenceSpan]`; `ChatTokenCounter.measure(messages, model) -> TokenMeasurement(input_tokens,context_window_tokens,fingerprint)`; `pack_evidence(spans, *, mode, render_messages, counter, output_reserve, max_context_chars) -> list[EvidenceBatch]`; `generate_answer(retrieved, *, mode, settings, cancel, emit) -> ChatResponse`; `validate_facts(raw, batch) -> list[EvidenceFact]`. В fast pack возвращает максимум одну порцию и информацию о неполном охвате; full возвращает все порции или явную ошибку.

- [ ] Тесты: `test_budget_includes_full_prompt` — окно 1000 допускает 500+244+256, отклоняет 501+244+256; `test_unknown_tokenizer_preserves_sources`; `test_fast_one_call_no_hidden_retry`; `test_fast_skips_oversized_unit_then_tries_next`; `test_full_covers_every_span`; `test_same_mail_text_different_sender_not_deduplicated`; `test_table_rows_keep_headers`; `test_documents_never_touch_generator` (LLM/tokenizer/scheduler заглушки падают при обращении).
- [ ] Запустить три новых pytest файла из backend с отдельным basetemp; ожидаемый FAIL. Реализовать полные единицы текста, только точную безопасную дедупликацию и сохранение исходных номеров/интервалов. Не создавать LLM-компрессию в fast. Если fast не вместил ни одной единицы — ответ с источниками без LLM; full в такой ситуации сообщает невозможность полного охвата.
- [ ] Для local_qwen использовать доверенные /props, /apply-template, /tokenize той же модели/options с HTTP timeout 5 секунд и request-local cache. Перед каждым LLM-вызовом пересчитать полный бюджет. Standard provider требует известного совместимого tokenizer/window; неизвестное не угадывать. Секреты и текст запросов не логировать.
- [ ] Реализовать direct fast/full-single, full map → reduce → final через существующий interactive клиент. Map возвращает факты с точными цитатами; validate_facts отвергает чужой номер/цитату и непустой неверный JSON. Reduce обязан уменьшать токенный размер, иначе `chat_summary_too_large`. В full при обрезании разделять только текущую порцию; неделимая → `chat_evidence_too_large`. На невалидность максимум один repair на порцию. Каждому вызову задать новый request_scope deadline, общий cancel сохранять.
- [ ] Добавить `test_six_maps_then_final`, `test_cancel_stops_before_next_batch`, `test_restart_retrieves_and_maps_from_zero`, `test_reduce_must_shrink`, `test_bad_quote_is_rejected`, `test_full_single_window_one_call`. В authorship сохранить детерминированную сборку и точные канонические цитаты; fast не более одного запроса, full все порции. Промежуточный on_text отключён; стримить только конечный ответ.
- [ ] Запустить новые тесты и существующие citation/authorship/mail regressions, найденные через rg --files backend/tests; PASS. Коммит `feat: implement single-pass and full-context chat generation`.

## Task 3: История попытки без хранения промежуточных порций

**Files:** изменить `backend/app/services/chat_history.py`, `db/models.py`, `models/schemas.py`, `backend/tests/test_chat_history.py`; создать forward migration после актуальной Alembic head и `backend/tests/test_chat_attempt_history.py`.

**Interfaces:** `begin_attempt(req,user) -> AttemptRef`; `save_attempt_sources(ref,user,sources,metadata) -> None`; `finish_attempt(ref,user,*,status,answer,sources) -> bool`; `cancel_attempt(ref,user) -> bool`; `attempt_is_active(ref,user) -> bool`. В ChatMessage добавить nullable unique `attempt_id` только для assistant. Режим/статус/error и безопасные метаданные хранить в существующем retrieval_metadata с отдельным ключом answer_attempt; скрытые факты/полный prompt туда не помещаются. Статусы истории: incomplete/completed/stopped/failed. Null attempt_id/отсутствие metadata — прежняя история.

- [ ] Тесты `test_begin_writes_one_pair`, `test_duplicate_attempt_does_not_start_again`, `test_cancel_and_finish_have_one_winner`, `test_cancel_preserves_sources`, `test_no_intermediate_facts_in_database`, `test_process_loss_leaves_incomplete_not_running`, `test_new_attempt_does_not_reuse_previous_work`, `test_foreign_attempt_forbidden`.
- [ ] Запустить новый файл с отдельным basetemp; FAIL. Реализовать транзакции с блокировкой session/message, уникальный attempt_id и идемпотентные terminal updates. begin сохраняет incomplete до поиска; sources обновляются после retrieval. Финализация обновляет то же сообщение, а не дописывает пару. Новый вопрос помечает старую незавершённую попытку stopped. Повтор того же attempt_id не вызывает LLM; новый повтор использует новый UUID.
- [ ] Отмена известного attempt_id проверяет владельца и фиксирует stopped; active worker замечает это не позднее следующей проверки (до вызова/commit и во время stream с интервалом <=1 секунды). Сбой процесса оставляет incomplete; восстановление history не запускает работу и не требует lease/expiry. Проверки видимости и поколения выполняются при активном чтении/финальной публикации, без протокола продолжения.
- [ ] Проверить миграцию upgrade/downgrade/upgrade на отдельной SQLite базе и гонки на отдельной PostgreSQL базе, затем history tests. Не менять пользовательскую БД до проверки. Коммит `feat: persist chat attempt results and final cancellation`.

## Task 4: Единый NDJSON поток и реальная отмена

**Files:** изменить `backend/app/services/chat_stream.py`, `api/chat.py`, `services/llm_profiles.py`, `services/llm_client.py` только в необходимых местах; создать `backend/tests/test_chat_attempt_stream.py`, расширить `tests/test_chat_stream.py`.

**Interfaces:** для новых режимов POST /api/chat/stream выполняет одну попытку; POST /api/chat/attempts/{attempt_id}/cancel отменяет её идемпотентно, с обычными auth/CSRF. Stream events start/sources/progress/delta/result/error имеют AttemptRef и event_seq. Progress содержит phase/batches_done/batches_total/coverage. API step/pause/resume не создаются.

- [ ] Тесты `test_sources_precede_tokenizer_and_llm`, `test_full_stream_outlives_single_call_budget`, `test_each_call_still_times_out`, `test_disconnect_closes_upstream`, `test_cancel_before_first_token`, `test_cancel_between_batches`, `test_late_final_cannot_replace_stopped`, `test_documents_bypass_generation_admission`.
- [ ] Запустить тестовые файлы; FAIL. Снять внешний единый LLM deadline только для нового многошагового пути; каждый вызов получает существующий бюджет 180 секунд из настроек. Retrieval/preparation остаются ограничены бюджетом подготовки, равным текущему chat timeout. Heartbeat 5 секунд не продлевает эти server deadlines.
- [ ] Связать disconnect/явную отмену с тем же threading.Event и DB-статусом истории; проверять cancel до каждого следующего вызова, при ожидании слота и перед commit. Нельзя считать worker остановленным только по cancel asyncio.to_thread. Дождаться реального закрытия upstream для освобождения GPU слота. Full не запускает параллельные completions; rate limit считается на один вопрос, обычный inflight limit сохраняется.
- [ ] Закрытие страницы до start event отменяет fetch; worker фиксирует stopped в finally. Если HTTP cancel не дошёл, disconnect всё равно запрещает последующие порции. Повтор сети автоматически не создаёт новую попытку. В публичных ошибках только безопасный локализуемый код.
- [ ] Проверить stream/API/history regressions и mock provider с закрытием upstream; PASS. Коммит `feat: stream cancellable multi-pass chat attempts`.

## Task 5: Три режима и кнопка нового запуска

**Files:** создать `frontend/src/lib/chatAnswerState.mjs`, `chatAnswerController.mjs`, `components/ChatAnswerControls.jsx`, `frontend/test/chatAnswerState.test.mjs`, `chatAnswerController.test.mjs`; изменить `lib/api.js`, `lib/chatStream.mjs`, `context/ChatContext.js`, `components/ChatPanel.jsx`, `components/ChatHistoryShared.jsx`, `lib/chatSourceContext.mjs`, `lib/chatSources.jsx`, locales ru/en, при необходимости globals.css.

**Interfaces:** `applyAnswerEvent(messages,event) -> messages`; `groupSourcesByDocument(sources) -> groups`; `createChatAnswerController({api,onEvent}) -> {start(request),stop(),restart(request),dispose()}`. update адресуется assistant_message_id/attempt_id, event_seq защищает от дублей. restart создаёт новый UUID и вызывает start с исходным вопросом/режимом/фильтрами; resume отсутствует. api поддерживает внешний AbortSignal.

- [ ] node:test: `three modes are sent explicitly`, `documents shows unique docs and all expandable sources`, `fast keeps excluded sources visible`, `full shows batch progress`, `late delta leaves new answer unchanged`, `restart uses new ID and new search`, `hidden stops and visible blur does not`, `typing does not stop`, `idle timer resets on heartbeat`, `silence times out`, `manual collapse survives progress`.
- [ ] Из frontend выполнить `node --test test/chatAnswerState.test.mjs test/chatAnswerController.test.mjs`; FAIL. Реализовать один поток на попытку. Для нового потока заменить абсолютный CHAT_TIMEOUT_MS на idle watchdog 210000 мс; при любом событии сбрасывать, закрывать reader/таймер в finally. Старые и другие API таймауты не менять.
- [ ] Добавить режимы с точным текстом: «Найти источники» — «Покажет найденные документы и релевантные фрагменты»; «Быстрый ответ» — «Сформирует ответ по наиболее релевантным источникам»; «Полное исследование» — «Проверит все найденные фрагменты и подготовит сводный ответ». Тест `mode_picker_uses_approved_labels_and_descriptions` фиксирует эти строки. Источники видны сразу; fast показывает полный/частичный охват, full — этапы. Кнопка «Остановить» во время работы; после остановки/ошибки «Запустить заново», без Continue. Переключатель режима действует на следующий submit. На старом backend неподдерживаемые режимы не подменяются автоматически.
- [ ] hidden/pagehide/unmount: отправить cancel с keepalive/CSRF, отменить fetch; видимый blur не отменяет. Новая отправка сначала локально останавливает старую попытку; история и поздние события адресуются своим сообщениям. Mount/visible читают историю, но ничего не запускают. Не делать focus/scroll на delta/sources/progress/restart; ручное раскрытие источников сохранять.
- [ ] Локализовать ru/en, выполнить `node scripts/export-ui-keys.mjs`; сохранить посторонние locale/CSS изменения. Проверки: `node --test`, `node node_modules/eslint/bin/eslint.js .`, `node node_modules/next/dist/bin/next build`; PASS. Коммит `feat: add three chat modes and restart controls`.

## Task 6: Приёмка и включение

**Files:** создать `backend/test_scripts/probe_chat_modes.py`, `docs/superpowers/reports/2026-09-29-chat-modes-acceptance.md`; дополнить `docs/LOCAL_LLM.md`, `docs/SEARCH_REPRODUCTION.md`, инструкции прокси только необходимыми изменениями.

- [ ] До прогона определить обязательные факты/источники на тестовом корпусе: справочник с кодами/строками, таблица больше окна, письма с противоположными решениями и разным авторством, дубли, удалённый/недоступный источник, пустой поиск. Probe получает URL/auth из окружения, не пишет секреты/полные переписки; выводит timings/calls/coverage/IDs/ошибки.
- [ ] На фиксированном retrieval сравнить три режима: sources одинаковы, documents=0 LLM, fast<=1 LLM, full охватывает все единицы. Проверить полный режим при маленьком окне, многократное объединение, недопустимые цитаты и неделимую строку. После остановки и нового запуска доказать повтор retrieval и первой порции.
- [ ] На authenticated localhost:16300 проверить три режима, источники до ответа, стабильный фокус, cancel до токена/в порции/в объединении, новый вопрос во время старого, hidden/visible/blur, reload. Проверить реальное освобождение слота и поток дольше 210 секунд через используемый Next-прокси; отдельно проверить production buffering/read timeout по текущей конфигурации.
- [ ] Запрос «Покажи справочник причин больничного листа» all/exclude: сравнить источники, обязательные строки/коды, ссылки, время до списка/первого токена/завершения. Не фиксировать прежние числа источников на изменившемся корпусе и не обещать 1–3 секунды.
- [ ] Целевые backend tests, Ruff по изменённым файлам, отдельная PostgreSQL проверка; frontend проверки повторять только после изменений. Обязательные актуальные CI gates выполнить; внешние блокеры явно указать в отчёте. Приёмка не считается успешной только по красивому ответу LLM.
- [ ] Включить capability после схемы/backend; rollback отключает новые режимы, сохраняя историю. Не запускать миграции на пользовательской БД без подготовки. Коммит приёмки отдельно; push только явно согласованного набора.

## Самопроверка плана

Режимы/единый поиск — задача 1; бюджет/охват/генерация — 2; история без сохранения промежуточной работы — 3; поток/отмена — 4; интерфейс — 5; реальная приёмка — 6. Review Focus покрыт указанными тестами. Старые задачи retention/checkpoint/Continue удалены. Код и продуктовые проверки пока не выполнялись; способ исполнения плана ещё не выбран.
