# Chat Mail Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** добавить три режима источников чата с наследованием почтового происхождения всеми потомками письма и доказуемой фильтрацией контекста, sources и evidence авторства.

**Architecture:** каноническое дерево одного документа/поколения → вычисленный payload Qdrant → общий pre-filter → проверка SQL в согласованном снимке → проверка каждого компонента контекста. Все writers и публикация старых candidates поддерживают один контракт; существующий корпус обновляется отдельной миграцией двух полей без пересчёта векторов.

**Tech Stack:** Python/FastAPI/Pydantic, SQLAlchemy/PostgreSQL, Qdrant, React/Next.js, pytest, node:test; существующие зависимости проекта.

**Spec:** [ТЗ от 27.09.2026](../specs/2026-09-27-chat-mail-filter-design.md). Прочитать полностью перед выполнением. Этот документ детализирует и заменяет задачи [первоначального плана](2026-09-26-chat-mail-filter.md); при конфликте требований действует ТЗ, включая уже принятое D-01.

**Статус:** подготовлен план; продуктовый код, данные, конфигурация и работающие сервисы при планировании не изменялись. Проверены исходники, но состояние живого корпуса и результаты будущих тестов не подтверждены. Все чекбоксы ниже относятся к будущему выполнению.

## Global Constraints

- «всё, что внутри письма, считаем частью письма»: только `parent_source_id`, `kind == "mail"` или `metadata.get("mail") is True`; никаких выводов из расширения, текста, тегов, LLM или `container_source_id`.
- `MailMode = Literal["all", "exclude", "only"]`, default `all`; `MailScope = Literal["mail", "document", "unknown"]`. D-01: `unknown` доступен только в `all`.
- Payload: `mail_scope`, `mail_scope_version: 1`; индексы `keyword`, `integer`. В строгом режиме обязательны оба поля и точное значение версии.
- Ключ дерева — документ и согласованный снимок поколения; natural key концепта `(doc_id, slug)`, чанка `(doc_id, chunk_index)`. Кэш только внутри операции.
- Классификатор O(S), память O(S); batch SQL без запроса на каждую точку. Новые SQL-колонки, внешние зависимости и долговременный cache не нужны.
- Не менять point IDs, векторы, slug, source IDs/spans, координаты чанков, RRF-веса и лимиты контекста. `top_k` остаётся числом блоков после merge.
- Не расширять задачу до импорта, новых парсеров, OCR, генерации, ремонта старого происхождения, фильтра `/search`, списка документов или сохранения предпочтения.
- `all` сохраняет прежнюю семантику. Отдельное ожидаемое отличие — graph больше не обходит tags/locale; его объяснить в baseline-отчёте.
- Ошибка SQL не разрешает строгий ответ по payload. Пустой разрешённый результат: HTTP 200, `sources: []`, локализованный ответ без генерации LLM; повтор в `all` запрещён.
- Основной и измерительный контуры — отдельные процессы, настройки и отчёты. Apply только при остановленных writers соответствующего контура.
- Выпуск: backend → миграция и приёмка → UI. Отдельного feature flag нет. Новый UI со старым backend недопустим.
- В логах нет query, письма, темы, адресов, metadata, DSN, секретов или полного payload. Фильтр не является разграничением доступа к документам.
- Локально pytest запускать из `backend`; frontend через `node`, без сломанных npm/npx shims. Проверки не используют пользовательские БД как тестовые fixtures.

## Review Focus

1. Подмена содержимого при прежнем `root/0`: candidate, старое active tree и публикация между чтениями — задачи 3, 5, 6, 9.
2. Пропущенный большой chunk и скрытые обходные пути: identity проверяется даже без загрузки текста; review, fragment и authorship не обходят режим — задачи 5–6.
3. Ошибочное исключение по payload не видно среди возвращённых hits: аудит сравнивает индекс с SQL в обе стороны и считает отсутствующие точки — задачи 7, 9.
4. Повтор старого ответа после смены select, pending или ошибки: исходный snapshot сохраняется, отсутствие поля означает `all` — задача 8.
5. Частичный apply, недоступный индекс, старый ready candidate и старый клиент после отката: не публиковать неверное поколение и не ослаблять явно строгий запрос — задачи 3, 7, 10.

## Карта изменений и порядок

Все пути в документе относительно корня репозитория. Существующие имена проверены на 27.09.2026; новые интерфейсы ниже являются решением плана, а не утверждением об их наличии.

| Файлы | Ответственность |
|---|---|
| **Новый** `backend/app/services/mail_scope.py`; `source_store.py` | Чистая классификация, scope записей, batch-адаптер ORM |
| `backend/app/services/vector_store.py` | Запись полей, patch/readback, строгая проверка индексов, все поисковые ветки |
| `backend/app/services/pipeline.py`, `canonical_reindex.py`, `canonical_repair.py` | Передача дерева того же поколения при каждой записи |
| `backend/app/services/generation_publication.py` | Приведение старого candidate до commit публикации |
| `backend/app/services/retrieval_hydration.py` | Канонические identity, поколение и scope в одном снимке |
| `backend/app/services/context_builder.py`, `authorship_evidence.py` | Изоляция компонентов и повторная проверка evidence |
| `backend/app/models/schemas.py`, `backend/app/api/chat.py` | Контракт запроса, передача режима, пустые ответы, metadata истории |
| **Новый** `backend/scripts/backfill_mail_scope.py` | Потоковый audit/apply и отчёт миграции |
| `frontend/src/context/ChatContext.js`, `components/ChatPanel.jsx`, `lib/api.js` | Состояние select и snapshot каждого запроса/повтора |
| **Новый** `frontend/src/lib/chatMailFilter.mjs` | Малый node-тестируемый helper выбора режима повтора |
| `frontend/src/i18n/locales/ru.js`, `en.js`; `backend/app/i18n/ui_keys.json`, `ui_en.json` | Точные строки ТЗ; backend JSON генерируются штатным скриптом |
| **Новые** `backend/tests/test_mail_scope.py`, `test_mail_scope_backfill.py`, `test_chat_mail_filter.py`; `frontend/test/chatMailFilter.test.mjs` | Новые контракты и отрицательные проверки |
| Существующие тесты из задач 2–9; **новые** probes в `backend/test_scripts/` | Регрессии writers/search, реальные Qdrant/PostgreSQL, измерения |
| `.github/workflows/ci.yml`; **новый** `docs/CHAT_MAIL_FILTER.md` | CI и инструкция сопровождения/выпуска |
| **Новые при выполнении** `docs/superpowers/reports/<date>-chat-mail-filter-<contour>.md` | Фактические результаты каждого контура |

Зависимости: **0 → 1 → 2 → 3; 1+2 → 4 → 5 → 6; 1+2+3 → 7; 6 → 8; 2–8 → 9 → 10**. Задачи 4–7 не выкатывать частично. Разделение на коммиты служит ревью, а не означает возможность независимого включения небезопасного промежуточного состояния.

Каждая кодовая задача выполняется циклом: тест поведения → подтверждённый RED → реализация → GREEN и регрессии → ревью diff → узкий коммит. При текущем запросе выполняется только подготовка плана; реализация, коммиты, push и live apply не выполняются.

## Задача 0. Зафиксировать базу, границы и стенды

**Files:** читать `AGENTS.md`, ТЗ, текущий план, `.github/workflows/ci.yml`, `backend/pyproject.toml`; создать при реализации `docs/superpowers/reports/<date>-chat-mail-filter-baseline.md`.

**Interfaces:** результат — SHA базы, состав изменённых файлов, версия зависимостей, воспроизводимый синтетический corpus и baseline `all`; имена живых измерительных ресурсов не угадываются.

- [ ] Проверить `git status --short`, ветку и SHA; сохранить пользовательские изменения. Для кода подготовить отдельный checkout через штатные worktree-инструменты, если текущий занят; доступные ТЗ и этот план должны попасть в рабочее окружение, даже если ещё не закоммичены.
- [ ] Снять перечень callers: `rg -n 'index_concepts\(|index_chunks\(' backend/app backend/scripts`; текущие production callers — pipeline, canonical reindex, canonical repair. Fixture `seed_backup_fixture.py` без provenance остаётся явным `unknown`. Проверить также прямые upsert/set_payload/overwrite_payload и wrappers backfill, чтобы не оставить альтернативный writer.
- [ ] Зафиксировать baseline существующими тестами раздела «Команды» и probes на выделенном корпусе: `all` без tags/locale, `all` с ними, обычный/авторский запрос, glossary on/off. Записать известные сбои окружения отдельно от дефектов; не объявлять упавший baseline пройденным.
- [ ] Подготовить маркерный corpus: обычный root, обычный сосед, root EML/MSG, DOCX → письмо → DOCX → PDF/письмо, письмо в таблице, unknown. Общий термин запроса не содержит уникальных маркеров источников.
- [ ] Подготовить отдельные тестовые PostgreSQL и Qdrant ресурсы. Для этапа 10 составить безопасный паспорт каждого live-контура из действующей конфигурации: профиль, коллекция, обезличенная БД, runtime env; секреты не копировать в отчёт.

**Выход:** база сравнения воспроизводима; задачи не зависят от неизвестного состояния пользовательской БД. Документацию baseline включать в первый относящийся к ней коммит.

## Задача 1. Единый классификатор и адаптер происхождения

**Files:** создать `backend/app/services/mail_scope.py`, `backend/tests/test_mail_scope.py`; изменить `backend/app/services/source_store.py`.

**Interfaces:**

```python
# mail_scope.py — без I/O
MAIL_SCOPE_VERSION = 1
MailScope = Literal["mail", "document", "unknown"]
MailMode = Literal["all", "exclude", "only"]
def classify_mail_scope(source_id: str | None, sources: list[dict]) -> MailScope: ...
def build_mail_scope_map(sources: list[dict]) -> dict[str, MailScope]: ...
def mail_scope_allowed(scope: MailScope, mail_mode: MailMode) -> bool: ...
def build_record_mail_scopes(
    sources: list[dict], concepts: list[dict], chunks: list[dict],
) -> tuple[list[MailScope], list[MailScope]]: ...
# source_store.py — использует сессию caller, не открывает собственную
def fetch_source_trees(session, doc_ids: set[str]) -> dict[str, list[dict]]: ...
```

Concept dict содержит `source_id`, `chunk_index`; chunk dict — `source_id`, `chunk_index`. Выходные списки строго соответствуют порядку входных записей. ORM/candidate callers нормализуют записи, не теряя исходные индексы. Вложенный словарь результатов SQL ключуется `doc_id`; отсутствующее дерево представлено пустым списком. Адаптер отдаёт `source_id`, `parent_source_id`, `kind`, `metadata` из `metadata_json`; эти raw-данные не становятся публичным `source_path`.

- [ ] Добавить параметризованные C01–C08. Ключевые assertions: `mail_scope_allowed("unknown", "exclude") is False`, `mail_scope_allowed("unknown", "only") is False`, `mail_scope_allowed("unknown", "all") is True`; `metadata.mail=1/"true"` не mail; цикл после mail даёт unknown; исправная соседняя ветка остаётся document; duplicate/root corruption делают всё дерево unknown.
- [ ] Добавить C09 и `test_record_scopes_preserve_order_and_reject_source_mismatch`: concept без source не наследует chunk; разные непустые source ID → unknown только для этого concept. Отсутствие самого chunk не превращает доказанный concept source в источник другого текста. Проверить разреженные chunk indices и одинаковые IDs в двух документах.
- [ ] Выполнить `python -m pytest -q tests/test_mail_scope.py` из backend выбранным Python; подтвердить ожидаемый RED по отсутствующей логике.
- [ ] Реализовать итеративный обход с мемоизацией. Проверить единственный корректный root и уникальность IDs до классификации; полный путь валидируется до возврата mail. Не использовать 32-уровневый display path для вычисления scope.
- [ ] Проверить глубокое дерево без RecursionError и повторных полных обходов; SQL-адаптер читает деревья batch, не по одной точке. Выполнить те же тесты и Ruff, затем коммит `feat: classify canonical mail source scope` только файлов задачи.

## Задача 2. Payload и все writers

**Files:** `backend/app/services/vector_store.py`, `pipeline.py`, `canonical_reindex.py`, `canonical_repair.py`; тесты `backend/tests/test_mail_scope.py`, `test_generation_pipeline.py`, `test_generation_reindex.py`, `test_generation_comment_repair.py`, `test_generation_attachment_backfill.py`, `test_generation_metadata.py`.

**Interfaces:** существующие `index_concepts`/`index_chunks` получают keyword-only `mail_scopes: list[MailScope] | None = None`; возвраты и прежние аргументы сохраняются. Добавить `VectorStore.patch_mail_scopes(scopes_by_point_id: dict[str, MailScope]) -> int` и `VectorStore.ensure_mail_scope_indexes() -> None` для задач 3/7. Patch обновляет только два поля конкретных IDs, bounded batches, `wait=True`, проверяет readback; ошибка поднимается caller. Проверка индексов читает реальную schema и требует keyword/integer; исключения не подавляются.

- [ ] Добавить I01–I03: точные payload concept/chunk, list length mismatch даёт ValueError до первого upsert, отсутствие списка даёт unknown/v1, недопустимое значение списка не записывается. Поддельный `metadata.mail_scope` игнорируется. Повторы/пустой список и несмежные chunk indices не смещают scope.
- [ ] Добавить assertions для patch: unchanged пропускается, readback mismatch/missing ID вызывает ошибку, все остальные поля и vectors неизменны; схема индекса действительно прочитана после создания. `ensure_mail_scope_indexes` не заменяет silent startup helper для остальных полей.
- [ ] Запустить целевые тесты, зафиксировать RED, затем добавить поля в payload обеих точек, `PAYLOAD_INDEX_FIELDS` и `RETRIEVAL_PAYLOAD_FIELDS`.
- [ ] В pipeline вычислять оба списка из `source_rows`, `chunk_rows`, `okf_docs` одного candidate. При отсутствии доказанного дерева использовать unknown. В reindex читать дерево под существующим `lock_generation_read`; в repair передать `proposal["sources"]` в `_index_candidate`, не брать active SQL tree вместо proposal.
- [ ] Проверить metadata-only операции tags/dev_tags/locale/deleted: поля сохранены. Повторить инвентаризацию всех writers из задачи 0; полноформатные writers пересчитывают, patch старых полей сохраняет scope. Legacy fixtures без дерева сознательно unknown.
- [ ] Выполнить целевые тесты и Ruff; ревью соответствия порядка списков и generation; коммит `feat: write mail scope in all indexing paths`.

## Задача 3. Безопасная публикация старых и новых candidates

**Files:** `backend/app/services/generation_publication.py`; при необходимости для сохранения повторного завершения `pipeline.py`, `canonical_repair.py`; тесты `backend/tests/test_generation_publication_service.py`, `test_generation_pipeline.py`, `test_generation_reindex.py`.

**Interfaces:** публичная сигнатура `publish_prepared_document` прежняя. Потребляет verified `publication.json`, `build_record_mail_scopes`, `patch_mail_scopes`; вычисляет mapping только для проверенных `prepared["point_ids"]` с теми же генераторами point IDs, что writer. Patch не вызывает embedding/LLM.

- [ ] Добавить I02/I04: тот же `root/0` меняет document↔mail между поколениями; старый ready candidate без полей получает правильный v1 до видимости; `sources is None` использует сохраняемое дерево, пустое/недоказанное дерево даёт unknown.
- [ ] Добавить `test_publication_scope_patch_failure_keeps_ready_candidate`: сбой на втором батче/readback сохраняет прежние canonical rows/active pointer и ready candidate для retry; повтор успешно публикует. Повтор уже опубликованного generation сохраняет прежний no-op. Проверить и вызывающие pipeline/repair, чтобы их cleanup не переводил восстанавливаемый ready в abandoned.
- [ ] Выполнить целевые тесты и получить RED; затем вставить вычисление/patch/readback в ту же заблокированную транзакцию публикации перед её commit.
- [ ] Учесть существующий порядок: `publish_generation` уже меняет pointer и делает `flush`, но commit происходит на выходе `session_scope`. Исключение patch обязано откатывать всю SQL-транзакцию; не добавлять промежуточный commit и не глотать исключение. Qdrant-изменения candidate могут остаться, повтор их завершает идемпотентно.
- [ ] Сопоставить manifest point IDs с ожидаемыми concept/chunk identities, включая разрешённое отсутствие chunk-точек при выключенной индексации чанков. Не классифицировать чужие или отсутствующие IDs по active tree.
- [ ] Выполнить GREEN, затем PG-гонки задачи 9 перед выпуском; коммит `fix: validate mail scope before generation publication`.

## Задача 4. Один pre-filter для dense, BM25 и graph

**Files:** `backend/app/services/vector_store.py`; тесты `backend/tests/test_vector_store_retrieval.py`, `test_generation_search.py`.

**Interfaces:** `search_composite(..., mail_mode: MailMode = "all")`; `_build_search_filter` сохраняет существующие позиционные аргументы и добавляет keyword-only `mail_mode="all"`; `_graph_expansion(ranked_lists, *, search_filter: qm.Filter)` получает обязательный готовый фильтр. Неизвестный внутренний режим → ValueError, даже если hits пусты.

- [ ] Добавить Q01/Q02/Q03/Q04/Q06: матрица 3 search modes × 3 mail modes × concept/chunk; все комбинации tags/dev_tags, locale с unknown, deleted/generation. Проверять состав запроса каждой ветки, а не только конечный список mock-результатов.
- [ ] Закрепить `all` без mail-условий; `exclude` требует document **и** v1, `only` — mail **и** v1. Missing/wrong-version/unknown не проходят строгие режимы. Invalid mode не становится all.
- [ ] После RED добавить условия через AND, не меняя внутренние OR tags/locale. Graph: `Filter(must=[search_filter, slug_condition])` до scroll limit, без повторного вызова `_build_search_filter(None)` и извлечения отдельных полей.
- [ ] Сохранить RRF-веса, rank penalty и лимиты. В регрессии `/search`/старых callers default all; graph исправление tags/locale явно отделить от mail-фильтра.
- [ ] Выполнить GREEN; Q02 дополнительно повторить на реальном Qdrant в задаче 9 (40 mail выше подходящего document), коммит `feat: prefilter mail sources in every retrieval branch`.

## Задача 5. Каноническая проверка hits без потери оптимизации текстов

**Files:** `backend/app/services/retrieval_hydration.py`; тесты `backend/tests/test_retrieval_hydration.py`, `test_generation_search.py`, `test_mail_rag_provenance.py`.

**Interfaces:** `load_visible_retrieval_hits(..., mail_mode: MailMode = "all") -> tuple[list, dict]`; внутренний `_enrich_retrieval_hits_in_session` принимает такой же режим и возвращает отфильтрованный список, который caller обязан присвоить. Старый `enrich_retrieval_hits(hits)` сохраняет all.

После проверки hit несёт канонические `source_id`, `chunk_index`, `mail_scope`, `generation_id` и внутренний `_canonical_verified=True`. Последнее выставляется только после чтения identity, не копируется из Qdrant. Перед обработкой очищать любые входные служебные утверждения и provenance; исходные mail-поля сохранять отдельно только для сравнения в диагностике. Отсутствие generation допустимо как legacy лишь при active=None; это отличается от отсутствия доказательства чтения.

- [ ] Добавить Q05/H02/H03/H05/C09: payload document → SQL mail отбрасывается в exclude; обратное расхождение фиксируется аудитом; fake source/path/fragment/scope/verified не становятся каноническими. Source и chunk index концепта восстанавливаются из SQL.
- [ ] Добавить `test_skipped_chunk_text_still_checks_identity_and_scope`: metadata читаются для всех нужных chunk keys, включая связанные с concepts, даже когда `_chunk_pairs_needed_for_merge` не требует большого content. Противоположный/неизвестный source и отсутствие canonical row отбрасываются в строгом режиме.
- [ ] После RED разделить batch identity-read и text-read. Под существующими locks/snapshot загрузить identities и raw trees, вычислить scopes, отфильтровать, затем читать только нужный разрешённый текст/пути/фрагменты. Не удерживать locks на LLM-вызове.
- [ ] Для exclude не возвращать mail_fragment и mail-узлы пути; only разрешает allowlist-навигацию через внешний документ. Fragment — только canonical chunk того же source/generation, никогда родительский текст. Для all сохранить текущую семантику и порядок.
- [ ] Добавить структурированное событие `chat_mail_scope_filter`: режим, candidates/allowed/rejected, причины unknown, payload mismatch, classification time. Логи без текста/metadata; поиск ничего не пишет в индекс.
- [ ] Проверить batch SQL query count на 1 и 100 hits одного документа: рост не линейный по hits; проверить недоступную БД без payload fallback. GREEN и коммит `fix: validate canonical mail provenance during hydration`.

## Задача 6. Контекст, авторство, API, история и серверная локализация

**Files:** `backend/app/services/context_builder.py`, `authorship_evidence.py`, `backend/app/models/schemas.py`, `backend/app/api/chat.py`; создать `backend/tests/test_chat_mail_filter.py`; расширить `test_context_builder.py`, `test_authorship_evidence.py`, `test_chat_history.py`, `test_mail_rag_provenance.py`, `test_mail_privacy.py`. Добавить строки ТЗ §9 в `frontend/src/i18n/locales/ru.js`, `en.js`, перегенерировать backend `ui_keys.json`, `ui_en.json`.

**Interfaces:**

- `ChatRequest.mail_mode: Literal["all", "exclude", "only"] = "all"`.
- `merge_and_format(..., mail_mode: MailMode = "all")`; строгая группировка `(doc_id, chunk_index, source_id)`, all — прежняя.
- `filter_mail_scope_blocks(blocks: list[dict], *, mail_mode: MailMode) -> list[dict]` в context_builder: защитный финальный контроль до format_context/ChatSource.
- `load_authorship_evidence(merged, max_chars, *, mail_mode: MailMode = "all")`; `answer_authorship(query, merged, llm, *, locale, max_chars, mail_mode: MailMode = "all")`.
- Каждый merged block сохраняет канонические scope/generation/verified и `_mail_components`: список identity/scopes всех участвовавших hits, включая review/fragment. В строгом режиме непроверенный/несогласованный компонент исключает блок целиком. Служебные поля не сериализовать в публичный response или prompt.

- [ ] Добавить A01–A04/Q07/H01/H04/H07: отсутствующее поле=all; null/число/bool/массив/неизвестное/`ONLY` → 422 до тяжёлых вызовов; режим доходит в каждый путь. Проверить пустоту после hydration и отдельно после merge/финального контроля: нет LLM, sources=[], RU/EN ответ по req.locale, ни одного retry all.
- [ ] Добавить assertions маркеров в фактических аргументах LLM и serialized sources: review без лексического совпадения всё равно фильтруется по scope, union-tags только разрешённых hits, нумерация непрерывна после удаления блоков, source N соответствует context N. `only` для вложенного DOCX не содержит текст письма-родителя.
- [ ] Добавить H06/R02: после merge опубликовать поколение с другим текстом/происхождением и прежним source ID; authorship evidence старого блока пусто. Проверить также deleted/source mismatch и отсутствие авторского evidence без расширения поиска.
- [ ] После RED передать mail_mode через весь chat pipeline и выполнить финальную проверку блоков после существующих преобразований, перед format_context и построением sources. Если блок отбрасывается, не пытаться «очистить» его предложения эвристически.
- [ ] В authorship новой сессией взять `lock_generation_read` до чтения, перепроверить visibility, active generation, natural key, source ID и scope; batch-чтения только для согласованных блоков. Освободить locks до LLM. Сохранить индексы evidence исходных блоков и общий max_chars.
- [ ] Добавить `mail_mode` в `retrieval_metadata` при schema_version=1; старые записи не менять. Для чтения старого отсутствия принять all; ChatResponse/ChatSource не расширять.
- [ ] Внести **все семь точных пар RU/EN из §9 ТЗ**, выполнить `node scripts/export-ui-keys.mjs` из frontend. В обоих ранних пустых ответах chat использовать `chat.noSourcesMailFilter` для strict и прежний `chat.noSources` для all; точный RU/EN fallback должен работать до обновления runtime-словарей.
- [ ] GREEN, i18n drift checks, Ruff; коммит `feat: enforce chat mail mode across context and authorship`.

## Задача 7. Metadata-only миграция и двусторонний аудит

**Files:** создать `backend/scripts/backfill_mail_scope.py`, `backend/tests/test_mail_scope_backfill.py`, `docs/CHAT_MAIL_FILTER.md`; использовать helpers задач 1–3.

**Interfaces:** CLI ТЗ §10 без дополнительных обязательных аргументов: mutually exclusive `--dry-run`/`--apply`, default dry-run, `--batch-size` 1..1000/default 256, `--doc-id`. `main(argv: list[str] | None = None) -> int`; stdout — безопасный JSON-отчёт, stderr — обезличенная диагностика; exit 0/1/2 согласно ТЗ. Настройки через `get_settings` текущего процесса.

- [ ] Добавить M01–M06: no-flag/dry-run не вызывают **никаких** mutation API (create collection/index/table, set/clear/overwrite payload, upsert, embeddings, ensure_chunks, parser). Ошибка отсутствующей коллекции не создаёт её. Некорректные CLI аргументы → 2 до I/O.
- [ ] Добавить повтор apply/interrupt, --doc-id, nonactive/legacy, deleted/orphan/missing canonical/invalid identity/broken tree, каноническая строка без точки. Проверить изоляцию клиентов двух контуров spy-тестом: обработка первого не вызывает запись второго.
- [ ] После RED реализовать page scroll `with_vectors=False`, минимальные identity/mail поля, server-side doc-id filter. На странице batch загрузить SQL identities и деревья; проверять active поколение под lock. Не держать целый корпус или глобальный set point IDs в памяти.
- [ ] Активные и orphan-точки классифицировать по ТЗ; nonactive считать `skipped_nonactive`, не сопоставлять active tree. Отдельный обратный проход по canonical rows постранично формирует ожидаемые IDs и делает bounded retrieve: считать `missing_active_points`, учитывая настройки индексации чанков. Это выявляет потерю полноты, которую scroll не покажет.
- [ ] Apply: проверить/создать два индекса, реально прочитать schema, patch только отличающихся конкретных IDs через helper задачи 2. Retry до 3 попыток для network/429/5xx; прочие 4xx без retry. Не маскировать readback/index ошибки; независимые батчи могут продолжиться, итог exit=1 при любой операционной ошибке.
- [ ] JSON содержит все counters ТЗ §10.3, включая отдельные concept/chunk, причины unknown, readback_errors и missing_active_points; категории причин могут пересекаться, mail/document/unknown внутри классифицированного множества — нет. Выводить алгоритм/version/time и безопасный target fingerprint, не DSN.
- [ ] Проверить до/после IDs, count, vectors и payload минус два поля на fixture. Повторный dry-run после apply: `would_change=0`, `updated=0`; unknown не означает ошибку исполнения или автоматическое разрешение выпуска.
- [ ] В инструкции описать остановку всех writers, входные env, безопасную сверку контура, dry-run/apply/readback/repeat, restart и rollback задачи 10. GREEN и коммит `feat: audit and backfill mail scope payloads`.

## Задача 8. UI и snapshot режима отправки/повтора

**Files:** `frontend/src/context/ChatContext.js`, `frontend/src/components/ChatPanel.jsx`, `frontend/src/lib/api.js`; создать `frontend/src/lib/chatMailFilter.mjs`, `frontend/test/chatMailFilter.test.mjs`. CSS `frontend/src/app/globals.css` менять только если существующая строка фильтров не обеспечивает перенос.

**Interfaces:** `mailMode/setMailMode` в ChatContext, initial `all`; `chat(query, tags=[], topK=5, mode="hybrid", sessionId=null, sourceLocale="", useGlossary=true, mailMode="all")`; `retryMailMode(message)` в helper возвращает `message.requestMailMode ?? "all"`. Сообщение хранит `requestMailMode` с момента формирования pending-placeholder, на success, error и повторе.

- [ ] Добавить U01–U03/A03: helper сериализует mail_mode всегда, старые callers=all; repeat берёт snapshot, а не текущий select. Fetch mock с управляемым завершением показывает: изменение select не меняет уже отправленное тело запроса. `retryMailMode({}) === "all"`; новое сообщение после изменения отправляет новое значение.
- [ ] Подтвердить RED, реализовать helper и state. `startNewChat` очищает messages/session, но сохраняет mailMode; provider remount/reload даёт all; переход внутри provider и открытие истории не меняют текущий выбор. Не добавлять localStorage/cookie/DB preference.
- [ ] В обоих обработчиках ChatPanel фиксировать requestMailMode **до await**, сохранять на ошибке и в новом результате повтора. Новый обычный запрос берёт current state; повтор старого сообщения без поля использует all.
- [ ] Добавить select «Источники» с тремя точными вариантами ТЗ, видимой label/accessible name, `aria-describedby` для подсказок. Общая подсказка всегда видна, unknown hint — только strict. Select во время pending может менять следующий запрос, но не текущий.
- [ ] GREEN node-тестов, lint/build; U01/U02/U04 обязательно подтвердить в браузере (React lifecycle, клавиатура, 320px/узкий экран, RU/EN, pending/retry/error/history/viewer). Проверки текста JSX сами по себе не доказывают поведение компонента.
- [ ] Коммит `feat: add chat mail source selector and request snapshots`; артефакт frontend пока не выкатывать на live до задачи 10.

## Задача 9. Интеграции, регрессии, CI и бюджет времени

**Files:** создать `backend/test_scripts/probe_chat_mail_filter.py`, `backend/test_scripts/probe_chat_mail_filter_pg.py`, `backend/test_scripts/benchmark_chat_mail_filter.py`; расширить `.github/workflows/ci.yml` job интеграции; отчёт `docs/superpowers/reports/<date>-chat-mail-filter-validation.md`.

**Interfaces probes:** отдельные env `MAIL_FILTER_TEST_DATABASE_URL`, `MAIL_FILTER_TEST_QDRANT_URL`, `MAIL_FILTER_TEST_COLLECTION`; без явных test-target переменных probes завершаются ошибкой и не используют production defaults. Коллекция/БД принадлежат test job, имеют префикс `mail_filter_test_`; очистка только созданных данным запуском ресурсов. DSN/секреты не выводить. Retrieval benchmark работает без генерации ответа.

- [ ] Реальный Qdrant: выполнить Q01–Q06, M05 и R01 на fixture collection; 40 более высоких mail hits и document ниже доказывают pre-filter до top-k. Проверить реальные nested OR/AND, integer/keyword schema и обе точки concept/chunk; mock-запросов недостаточно.
- [ ] Реальный PostgreSQL: два соединения и синхронизационные barriers для H05/H06/I04. Запустить публикацию между search/hydration и merge/authorship; ожидать согласованный старый снимок или отбрасывание, никогда новый чужой текст. Проверить rollback patch failure и отсутствие locks во время LLM.
- [ ] В CI сохранить backend/frontend/doc-parser/audit/docker jobs. Добавить отдельный job с disposable PostgreSQL/Qdrant, версиями из текущей deployment-конфигурации, установкой уже используемых зависимостей и запуском двух probes. Не подключать внешние сервисы в герметичный unit job, не отключать coverage или существующие гейты. Контентные проверки CI через `git grep`, не через runner-зависимый `rg`.
- [ ] Прогнать все команды ниже один раз после согласования кода; повторять только затронутые проверки после новых правок. В отчёте отличать PASS/FAIL/SKIP/not run; отсутствие тестовой БД — непройденный integration gate.
- [ ] Измерить одинаковый corpus/config/index: 10 прогревочных и 100 измерительных прогонов **на каждый режим**, фиксированное чередование all/exclude/only, без генерации LLM. Baseline записать до изменения на том же стенде; после — те же query set/условия. Записать p50/p95 retrieval, отдельно classification/hydration, SQL count, candidates, context chars.
- [ ] Критерий: дополнительный p95 classification/hydration ≤ `max(50 мс, 20% baseline retrieval p95)`. Не выдавать выигрыш из-за уменьшенного числа hits за стоимость классификатора: отдельно сравнить гидратацию одинакового фиксированного набора кандидатов. Превышение → профилирование и исправление либо явно принятое отклонение до выпуска.
- [ ] Проверить privacy по logs/LLM arguments и границы функции. При выявлении изменения свойств безопасности из AGENTS.md обновить конкретный раздел `SECURITY.md` с механизмом и отрицательным тестом; не объявлять mail filter ACL.
- [ ] Провести итоговое ревью относительно ТЗ и трассировки ниже, устранить блокирующие findings, выполнить затронутые проверки. Коммит `test: verify mail filter integration and release gates`.

### Команды проверок

Команды будущей реализации; новые файлы должны уже существовать. Перед pytest из корня создать `tests/tmp/pytest-runs` через `New-Item -ItemType Directory -Force tests/tmp/pytest-runs`. Не запускать параллельно pytest с общим basetemp. Далее cwd **backend**, Python локальной venv:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_mail_scope.py tests/test_mail_scope_backfill.py tests/test_chat_mail_filter.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_vector_store_retrieval.py tests/test_retrieval_hydration.py tests/test_context_builder.py tests/test_authorship_evidence.py tests/test_chat_history.py tests/test_generation_pipeline.py tests/test_generation_publication_service.py tests/test_generation_reindex.py tests/test_generation_search.py tests/test_generation_metadata.py tests/test_generation_comment_repair.py tests/test_generation_attachment_backfill.py tests/test_mail_rag_provenance.py tests/test_mail_privacy.py
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m pytest -q --cov=app --cov-report=term-missing --cov-fail-under=85
```

Cwd **frontend**:

```powershell
node scripts/export-ui-keys.mjs
node --test test/chatMailFilter.test.mjs test/chatLocale.test.mjs test/i18n.test.mjs
node --test
node node_modules/eslint/bin/eslint.js .
node node_modules/next/dist/bin/next build
```

Cwd **backend**, только с выделенными test-target env задачи 9:

```powershell
.\.venv\Scripts\python.exe test_scripts/probe_chat_mail_filter.py
.\.venv\Scripts\python.exe test_scripts/probe_chat_mail_filter_pg.py
.\.venv\Scripts\python.exe test_scripts/benchmark_chat_mail_filter.py
```

Успех: exit=0, все заявленные тесты PASS, coverage ≥85%, lint/build без ошибок; probes сообщают ноль запрещённых маркеров/расхождений. SKIP внешних проверок не заменяет их прохождение. Linux CI — все обязательные jobs и новая интеграция на фактическом SHA релиза.

## Задача 10. Выпуск, приёмка двух контуров и откат

**Files:** завершить `docs/CHAT_MAIL_FILTER.md`; отдельные отчёты `docs/superpowers/reports/<date>-chat-mail-filter-main.md` и `<date>-chat-mail-filter-measurement.md`. Имена отчётов не задают реальные имена БД/коллекций.

**Interfaces:** к выпуску допускается backend, содержащий **вместе** все новые writers, публикацию, фильтрацию и backfill. UI-артефакт собран, но становится доступен после data/acceptance gates. Работа на каждом контуре проходит полный цикл самостоятельно.

- [ ] Gate релиза: задачи 1–9 GREEN, отчёт baseline/validation приложен, нет блокирующих findings. До live-изменений подготовить конкретный список версий/targets/команд и окно обслуживания в пределах разрешённого пользователем внедрения; при запросе только плана этот этап не выполнять.
- [ ] Развернуть backend с новым контрактом при прежнем UI. Если backend/frontend обычно поставляются одним compose-релизом, сохранить старый frontend image на первом шаге и заменить его после миграции. Проверить version/health и явный strict API smoke; старые клиенты продолжают all.
- [ ] Dry-run отдельным процессом каждого контура: сверить `DATABASE_URL`, `QDRANT_URL`, `QDRANT_COLLECTION`, `KNOWLEDGE_PROFILE` с паспортом, используя безопасные идентификаторы. Передавать env штатным launcher/Compose; не использовать корневой .env как production-конфигурацию. Сохранить отчёт и причины unknown, missing points, legacy/candidates.
- [ ] Gate происхождения: подтвердить схему source-привязок доступного корпуса. Известные смешанные/недоказанные legacy-привязки нельзя объявить document только по корректному root. Зафиксировать ограничения полноты; если безопасно разрешить строгие режимы нельзя, остановить их выпуск и вынести repair в отдельную задачу, без скрытого regenerate/reparse.
- [ ] В maintenance window остановить и дождаться writers: upload/queue, resume/regenerate, canonical reindex/repair, publication, purge, фоновые backfills. Read-only chat допустим; автоматический startup writer не должен возобновиться. Проверить остановку фактически, не только отправку команды.
- [ ] Сохранить контрольные выборки/агрегаты IDs/count/payload/vectors и текущих поколений. Выполнить `python scripts/backfill_mail_scope.py --apply --batch-size 256` в env **одного** контура, readback и проверку schema; затем `--dry-run` в том же окне. Требовать `would_change=0`, ноль failed/readback/index errors; nonactive и missing_active_points должны быть явно объяснены.
- [ ] Возобновить writers; проверить новую загрузку, resume, regenerate, reindex и публикацию ready candidate, созданного до обновления. Все новые/включённые точки имеют правильный v1. Повторить после backend restart. Во втором контуре выполнить отдельный тот же цикл, не копируя payload/doc IDs первого.
- [ ] На тестовом corpus и будущей UI-сборке выполнить браузерную приёмку U01–U04: три режима × dense/bm25/hybrid; tags/разработка/язык; RU/EN; pending/change/retry/no-glossary/error; новый чат/remount/история; viewer/source links. Для локального стенда UI `http://127.0.0.1:16300`, backend `http://127.0.0.1:18000`; реальные контуры используют свои проверенные URLs.
- [ ] Проверить сетевые request bodies и реальные synthetic context/evidence/sources. Критерий — ноль запрещённых маркеров во всех ветках; фраза ответа модели сама по себе не доказательство. Не включать логирование настоящей переписки.
- [ ] После прохождения обоих контуров выпустить UI, проверить отсутствие старых backend-инстансов в маршруте, smoke три режима и retry. При частичной готовности контуров UI не открывать для непрошедшего приёмку контура; общий UI ждёт оба.
- [ ] Оформить итог отдельно по контурам: дата/SHA/backend+frontend versions, безопасный config fingerprint, команды/exit codes, числа до/после, причины unknown/missing/skipped, CI, screenshots/network evidence синтетики, p50/p95, подтверждение writers/restart, остаточные ограничения и решение о выпуске.
- [ ] Проверить откат на тестовом стенде: сначала убрать новый UI/возможность новых strict-запросов, сохранить совместимый backend для pending и старых вкладок. Явный only/exclude никогда не преобразовывать в all. Для полного backend rollback обновить/перезагрузить клиентов либо временно закрыть чат; убедиться, что новый клиент не попадёт на старый API.
- [ ] При откате оставить mail payload/indexes; SQL/историю/файлы/векторы не менять. Ошибка классификатора → отчёт, исправление/проверка, повторный apply; не удалять корпус. Если ошибка означает риск неверного strict-ответа, временно ограничить чат до исправления/координированного отката, не ослаблять режим.

**Условия остановки выпуска:** небезопасная legacy-provenance; незавершённый backfill/readback/index; обход режима в любом context/evidence; сломанный retry snapshot; необъяснённая регрессия all; незавершённая проверка гонок или превышение бюджета без решения; смешанные версии UI/backend. Ошибка одного контура не разрешает запись в другой.

## Трассировка требований и Definition of Done

| ТЗ / тесты | Задачи | Доказательство |
|---|---|---|
| §1–5, C01–C09 | 0–1, 5 | Чистая классификация, strict unknown, canonical identities |
| §6, I01–I03 | 2 | Все writers, candidate tree, сохранение metadata |
| §6.1, I04 | 3, 9 | Старый ready, rollback/retry, PostgreSQL race |
| §7.1–7.2, A01–A04 | 4–6, 8 | 422/default, совместимые callers, история и snapshot |
| §7.3, Q01–Q04, Q06 | 4, 9 | Общий фильтр до limit, реальная Qdrant-матрица |
| §7.4, Q05/Q07 | 5–7 | SQL контроль, аудит обратного mismatch, пустой ответ |
| §8, H01–H04/H07 | 5–6 | Компоненты, review/fragment/path, финальный guard |
| §8.1, H05–H06 | 3, 5–6, 9 | Snapshot/locks и args авторского LLM |
| §9, U01–U04 | 6, 8, 10 | Точные RU/EN, lifecycle, browser/network |
| §10, M01–M06 | 2–3, 7, 9–10 | No-op dry-run, идемпотентность, invariant и изоляция |
| §11–12, R01–R02 | 0, 5–6, 9 | Baseline all, privacy, p50/p95, все гейты |
| §13–15 | 10 | Два отдельных отчёта выпуска и проверенный rollback |

- [ ] Все перечисленные сценарии покрыты, обязательные проверки фактически пройдены; тесты без сервисов не выдаются за доказательство PostgreSQL/Qdrant.
- [ ] Активные доступные точки — корректный v1 либо явно unknown; nonactive защищены при публикации; отсутствующие точки и ограничения корпуса не скрыты.
- [ ] Строгий context/sources/evidence содержит только допустимые источники, включая каждый дочерний и вспомогательный компонент.
- [ ] В all нет изменений mail-ranking/grouping; graph tags/locale исправление отдельно объяснено.
- [ ] Реальные результаты обоих контуров, выпуск UI и откат документированы; задача планирования не помечает эти пункты выполненными.

## Оценка и передача в разработку

Порядок величины для одного разработчика: подготовка/классификация 0,5–1 день; writers/publication 1–2; retrieval/hydration/context/authorship/API 2–3; backfill/UI 1,5–2,5; интеграции/CI/измерения 1–2; приёмка и выпуск двух контуров 1–2. Всего ориентировочно **7–13 рабочих дней** при доступных стендах и исправной provenance. Это оценка объёма, не срок обязательства; отдельно влияют длительность CI, обслуживание двух контуров и выявленные дефекты.

Ремонт старого происхождения, online backfill при невозможности остановить writers и новые парсеры в оценку не входят. Прежняя оценка 1–2 дня не покрывает уточнённый объём ТЗ с поколениями, гонками, авторством и двумя контурами.

Рекомендуемый способ реализации — последовательное выполнение в одной рабочей ветке с проверкой интерфейсов после задач 3 и 6 и итоговым ревью: задачи тесно связаны единым контрактом происхождения. При выборе subagent-driven выполнения задачи сохраняют тот же порядок зависимостей; одновременно редактировать общие vector_store/hydration файлы нельзя. Выбор способа исполнения выполняется при переходе от планирования к реализации.
