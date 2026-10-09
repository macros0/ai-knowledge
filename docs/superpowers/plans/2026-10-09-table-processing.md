# Улучшение обработки таблиц — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for native execution, or superpowers:subagent-driven-development if the user selects delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Устранить сбой широких таблиц, сохранять структуру Excel и обеспечить проверяемые поиск и ссылки на ячейки.

**Architecture:** Каноническая таблица отделена от производных поисковых фрагментов. Компактный классификатор выбирает способ извлечения; значения переносятся программно. Публикация, доступ и восстановление используют существующие поколения документа.

**Tech Stack:** Python, docparser/openpyxl/OOXML, Pydantic, SQLAlchemy + PostgreSQL/SQLite, Alembic, Qdrant, LiteLLM, pytest, Next.js.

**Spec:** [Проект решения](../specs/2026-10-09-table-processing-design.md).

**Статус R1 (2026-10-09):** пользователь разрешил T0–T2 и работу в `main`. Компактный классификатор и постоянные отчёты реализованы; два live-probe A приняты при прежних 512 токенах. T0: инструменты и локальный корпус заморожены, но исходные шапки и полный набор generation/resource metrics ещё не приняты. Локальная проверка и ограничения: [отчёт R1](../reports/2026-10-09-table-processing-r1.md). R2/R3, миграция корпуса, commit/push/deploy не выполнялись.

## Global Constraints

- Excel включает вложения в DOCX; общие механизмы применяются к распознанным таблицам DOCX/PDF. OCR/новое распознавание PDF не входят.
- Оригиналы не изменяются. Контракты прозы, замечаний, mail source_id, тегов и scoped search сохраняются.
- Строгая обработка JSON-массивов и настоящей неполноты не ослабляется.
- Повторные физические строки сохраняются. Повторная выдача одного фрагмента и разные одинаковые строки различаются по идентификаторам.
- Опубликованная версия доступна до успешной атомарной публикации кандидата.
- Перегенерация, изменение индекса, миграция схемы и миграция корпуса — отдельные операции. Никакого массового обновления при старте сервиса.
- Реальные файлы и подробные отчёты остаются локально; в git только синтетические fixtures и обезличенные агрегаты.
- Формулы/макросы/внешние ссылки/код модели не исполняются. ACL, trash, source scope распространяются на таблицы.
- Новые retention/access границы отражаются в SECURITY.md в том же изменении.
- Чужие изменения сохраняются. Commit/push/deploy — отдельная команда, только относящиеся к задаче файлы.
- До commit обязателен node scripts/check-project.mjs. До frontend-изменений — локальные Next.js docs по frontend/AGENTS.md. Быстрые тесты не называются полным CI.

## Review Focus

1. Сложная шапка может стать данными или потерять название колонки — T4.
2. Blank, 0, False, ведущие нули и отсутствующий результат формулы могут смешаться — T3/T4.
3. Широкая строка/длинная ячейка может обрезаться при embedding/context — T6/T7.
4. Старый ответ может показать координаты другого поколения — T5/T7/T8.
5. Частичная публикация/delete/restore может сделать доступной неверную таблицу — T5/T9.

## Проверенные факты и границы baseline

Контроль A: 316×344 и 172×397; оба live-probe дали length на 512 токенах при перечислении description_cols. В БД сохранены все 344/397 строковых концептов с точным совпадением содержимого ПОСЛЕ текущего парсинга. Это не эталон исходной шапки: у второго блока подписаны лишь 9/172 колонок. После восстановления шапки законно меняется число строковых концептов.

Проверенные точки интеграции: doc-parser/src/docparser/{xlsx_parser,blocks,source_model,parser}.py; backend/app/services/{field_table,llm_profiles,llm_client,source_chunking,okf_generator,pipeline,generation_store,generation_publication,generation_files,generation_artifacts,concept_store,chunk_store,context_builder,vector_store}.py; backend/app/db/models.py; backend/app/models/schemas.py.

## Выпуски и зависимости

T0 → T1 → T2 = R1, самостоятельное исправление классификатора и диагностики.
T3 → T4 → T5 → T6 → T7 → T8 → T9 = R2, структура и поисковая обработка, опирающиеся на R1.
T10 = R3, отдельный проект точных операций после приёмки R2.

Каждая задача: зафиксировать падающие проверки → минимальная реализация → focused checks → приёмка задачи. Релизные/дорогие проверки выполняются на границе выпуска. Коммиты — только после отдельной команды пользователя.

## T0. Корпус и независимые эталоны

**Files:** создать backend/test_scripts/probe_tables.py, backend/tests/test_table_probe.py, doc-parser/tests/test_xlsx_tables.py; расширить doc-parser/tests/fixtures.py. Реальный manifest и отчёты — в игнорируемом data/table-eval/.

**Interfaces:** CLI --manifest PATH --output PATH --mode parse|retrieval|compare; compare дополнительно --baseline PATH --after PATH. Manifest: локальный файл, SHA-256, ожидаемые координаты/значения, вопросы и evidence refs. При нарушении gates ненулевой exit code. В отчёте версии кода/парсера/модели/промпта/настроек без секретов.

- [x] Создать fixtures: обычные 2–5 колонок, 172/316 колонок, merge/многоэтажная шапка, несколько таблиц, пустые/скрытые строки, повторные строки, формулы/ошибки/даты/нули, Markdown/HTML/переносы, длинная ячейка, XLSX внутри DOCX.
- [x] RED test_probe_fails_on_one_missing_cell: удаление одной ожидаемой ячейки из after даёт failure независимо от числа концептов. Проверить отчёт намеренно испорченными данными до доверия зелёному результату.
- [ ] Разметить исходные координаты сложных таблиц по оригиналу отдельно от вывода парсера; сравнить все raw values с OOXML. У спорной шапки записать ожидаемую неоднозначность.
- [x] Заморозить 40 запросов: 20 таблицы (код+свойство, редкое значение, крайняя колонка, сложная шапка), 10 проза, 5 замечания, 5 вложения/mail. У каждого есть обязательное значение/источник.
- [ ] Снять baseline: ячейки и шапки, row/fragment counts, recall кандидатов/контекста, точность значения+ссылки, время, peak RSS, LLM calls/tokens, Qdrant points и диск.

**Приёмка:** детектор замечает пропуск одной ячейки; все проваленные gates остаются в отчёте; реальные документы/значения не коммитятся.

T0 evidence: `data/table-eval/manifest.json` (147 418 заполненных исходных ячеек, 10 листов, 40 вопросов), `parser-oracle-baseline.json`, `retrieval-baseline.json`, `summary.json`. Raw OOXML содержит дополнительно 156 записей с отсутствующим значением/кэшем; это не заполненные эталонные ячейки. 303 отличия текстовой проекции — внешние пробелы; две шапки отличаются в legacy-проекции; корректность шапки остаётся unknown. Candidate recall 38/40, final value+source 29/40. Все 11 retrieval failures сохранены. Полные независимые семантические эталоны сложных шапок и generation peak RSS/token usage не объявляются выполненными.

## T1. Компактный классификатор — R1

**Files:** изменить backend/app/services/{field_table,llm_profiles,llm_client}.py, backend/prompts/okf_table_classifier.md и синхронный fallback backend/app/prompts/okf.py; тесты backend/tests/test_field_table.py, test_llm_client.py, test_okf.py, test_prompt_store.py; создать test_table_classification_contract.py.

**Interfaces:** новый task table_classification; TableClassificationResult(concept_per_row: bool, title_col: int, concept_type: Literal[concept,procedure,reference,note], extraction_mode: Literal[per_row,whole]). LLMClient.chat_json(..., single_object=True, task="table_classification"). Использует существующий classification token budget. Старый task classification совместим. Внутреннее description_cols временно допускается для legacy cache, но не генерируется новой моделью.

- [x] RED test_wide_table_response_schema_has_no_column_list: description_cols отсутствует в схеме/промпте/новом ответе; размер ответа не растёт пропорционально 316 колонкам.
- [x] RED test_table_classification_uses_classification_budget и test_generation_json_remains_strict: новый task не получает случайно generation budget; truncation/trailing JSON концептов остаются ошибкой.
- [x] Перевести вызов/валидацию на новый task. Проверять title_col по реальным колонкам; неверный индекс вызывает явный fallback, не тихую замену на 0.
- [x] Обновить cache version; ключ включает версии, модель, prompt hash и весь отправленный preview, не только первые две строки. Глобально старый кэш не стирать.
- [x] Выполнить focused tests и два точечных live-probe на A без публикации/полной регенерации. Сохранить finish_reason, размер ответа и результат. Если length/timeout остался — исправление не принято.

**Приёмка:** оба probe дают валидный компактный результат; per-row/whole, повторы строк и строгая генерация проходят регрессии. documents.problem вручную не очищается.

## T2. Диагностика после очистки staging — R1

**Files:** создать backend/app/services/table_quality.py и backend/tests/test_table_quality.py; изменить gen_quality.py, field_table.py, pipeline.py, generation_artifacts.py, problem_codes.py и pipeline tests.

**Interfaces:** TableQualityReport(table_ref, method, cause_code, input_rows, covered_rows, input_cells, covered_cells, header_status, schema_version). Неизвестные counts — null. write_table_quality_report(generation_bundle: Path, reports: list[TableQualityReport]) -> Path создаёт table-quality.json, включённый в artifact manifest.

- [x] RED test_quality_report_survives_staging_cleanup, test_row_coverage_does_not_claim_header_correctness, test_report_contains_no_cell_text.
- [x] Коды: classifier_output_truncated, classifier_timeout, classifier_invalid_result, header_ambiguous, formula_result_missing, content_omitted. Provider details не попадают в публичный отчёт.
- [x] Legacy-поток измеряет только доступные Markdown-строки; исходные ячейки до T3 неизвестны. Без старого отчёта показывается отсутствие подробностей, не ретроспективное «полное покрытие».
- [x] Записывать отчёт до publication manifest, проверять hash. Реальная неполнота имеет приоритет над фактом резервного метода. До T8 не снимать существующее warning на основании одного row count.

**Приёмка R1:** сбой понятен после завершения задания; отчёт не теряется вместе со staging. Этот выпуск не требует миграции всего корпуса.

## T3. Структурная модель и потоковое извлечение Excel

**Files:** создать doc-parser/src/docparser/{table_model,xlsx_structure}.py; изменить xlsx_parser.py, source_model.py, parser.py, __init__.py; тесты test_xlsx_tables.py, test_parse.py, test_attachments.py, test_archive_guard.py.

**Interfaces:** dataclasses из spec. TableSink.begin_table(descriptor), append_rows(table_id, rows: list[TableRow]), finish_table(table_id, summary). Добавочные keyword-параметры table_sink=None в ParseContext и parse_document_result. Вложения используют общий sink со своим source_id. Парсер не импортирует backend/SQLAlchemy.

- [ ] RED test_excel_cells_keep_original_coordinates, test_nested_xlsx_keeps_source_id, test_blank_zero_false_are_distinct, test_table_sink_is_optional.
- [ ] Итерационно разбирать диапазоны/merge/форматы, освобождать XML-элементы. Существующие ZIP limits сохраняются; число/размер merge и ячеек ограничиваются до разрастания памяти.
- [ ] Сохранять raw/display/type/number format/formula/cache state; форматированные пустые хвосты не считать данными, удалённые заполненные ячейки не терять.
- [ ] Sink передаёт до 256 строк и до 1 MiB сериализованных данных в пакете; особо длинная строка идёт отдельным пакетом под общим лимитом парсера, без молчаливого усечения. В Block.meta нет полной копии cells.
- [ ] Ввести table_schema_version=1, поднять актуальную parser version. Старые checkpoints не смешивать с новой разметкой.
- [ ] Проверить legacy API, вложения, отмену и сбой чтения: незавершённый sink не публикуется как полная таблица.

**Приёмка:** исходные ячейки имеют координаты/типы; превышение предела даёт явную диагностику/ошибку, а не усечённый done.

## T4. Шапки и несколько таблиц на листе

**Files:** создать doc-parser/src/docparser/table_headers.py, doc-parser/tests/test_table_headers.py; изменить xlsx_structure.py, xlsx_parser.py.

**Interfaces:** normalize_headers(descriptor, sample_rows: list[TableRow], merges: list[CellRange]) -> HeaderDecision(header_rows, columns, method: explicit|structural|unresolved, warnings). detect_table_regions(sheet_index: int, declared_ranges: list[CellRange], occupied_cells: Iterable[CellAddress], merges: list[CellRange]) -> list[CellRange]; CellAddress (row, column) и CellRange определены в table_model (лист и исходные min/max row/column).

- [ ] RED: merge в несколько уровней, название отчёта перед шапкой, пустая A1, повторные имена, пустая разделительная колонка, повторные шапки внутри данных.
- [ ] Явный Excel Table приоритетен. Для обычного листа анализировать до 8 начальных непустых строк области; это кандидаты, а не восемь автоматически удаляемых строк. Использовать merge/геометрию/типы совместно.
- [ ] Путь заголовков строится сверху вниз с координатами; дубли различаются буквами колонок. Неопределённый заголовок остаётся координатным с warning.
- [ ] RED test_ambiguous_header_keeps_all_rows: спорные строки не удаляются. Вертикальные merge данных не размножают значение без связи с исходным якорем.
- [ ] Сверить шапки A по оригиналу: все границы merge, левая/средняя/правая части. Зафиксировать реальные строки данных после отделения шапки.

**Приёмка:** однозначные fixtures имеют точные пути; неоднозначные сохраняют данные и честный статус.

## T5. Хранение поколений и миграция схемы

**Files:** изменить backend/app/db/models.py, backend/app/models/schemas.py, services/{generation_store,generation_publication,generation_artifacts,generation_files,pipeline,concept_store,chunk_store}.py; создать services/table_store.py, tests/test_table_store.py и Alembic revision add_document_tables от актуального head. Расширить generation integrity/publication/deletion/backup tests.

**Interfaces:** DocumentTable PK (doc_id,generation_id,table_id): source_id, schema_version, descriptor JSON, quality JSON. DocumentTableRow PK (...,row_number): sparse cells JSON. FK связан с существующим generation lifecycle. Nullable JSON table_refs в OkfConcept/DocumentChunk и совместимых схемах. GenerationTableSink реализует T3 ограниченными транзакциями. TableRef объявляется в models/schemas.py; качество R1 использует временный locator (source_id, chunk_index, table_ordinal), помеченный как legacy, без выдуманной ссылки на ячейки. Сборку legacy locator и нового TableRef описать раздельными вариантами модели отчёта.

- [ ] RED: candidate невидим, поколения не смешиваются, повторы строк сохраняются, resume идемпотентен, частичная публикация сохраняет прежний active.
- [ ] Проверить upgrade на пустой БД и копии актуальной схемы PostgreSQL/SQLite. Известное dev create_all/Alembic расхождение сначала разбирать на копии; не stamp/ALTER рабочую БД вслепую.
- [ ] get_table_rows(doc_id, generation_id, table_id, row_numbers) вызывается после проверки доступа/публикации; внутренний writer явно выбирает candidate. Table ID не используется как файловый путь.
- [ ] Проверять counts/digests таблиц до переключения существующего active pointer; готовность DB и Qdrant обязательна. Candidate failure не влияет на старую публикацию.
- [ ] Cleanup/delete/trash/backup/restore учитывают новые строки. Старый snapshot не перенаправляется на новый generation; удалённое поколение явно unavailable.
- [ ] Portable OKF export/import: versioned tables manifest + rows JSONL, checksums, без абсолютных путей. Проверить полный цикл восстановления.

**Приёмка:** legacy читается без обязательного backfill; fault injection не публикует половину таблицы; restore возвращает данные и координаты.

## T6. Компактные проекции и широкие строки

**Files:** создать backend/app/services/{table_projection,table_classification}.py, backend/tests/test_table_projection.py; изменить source_chunking.py, okf_generator.py, field_table.py, embedder.py, config.py, concept_store.py, chunk_store.py.

**Interfaces:** build_classification_preview(descriptor, rows, limits) -> ClassificationPreview(text, column_index_map). iter_table_fragments(descriptor, rows, limits) -> Iterator[TableFragment]. TableFragment(fragment_id, table_ref, content, coverage_refs, kind: row|row_part|table_overview). ProjectionLimits содержит независимые бюджеты classification/embedding/context; BudgetEstimator возвращает число токенов и exact|estimated.

- [ ] RED: 316 колонок, пустая середина, ключ справа, сверхдлинная строка/ячейка, повторные строки/заголовки, ведущие нули.
- [ ] Preview: 12 колонок × 3 строки, максимум 160 символов ячейки и 12 000 символов user input. Помечать сокращение; индексы исходные. Проверять title_col по mapping. Это не ограничивает полноту данных.
- [ ] Канонические данные полные. Поисковый текст пропускает только blank; сохраняет 0/False/ошибку формулы, добавляет название листа/таблицы, ключ строки и пути колонок.
- [ ] Широкую строку делить по группам заголовков; ключ повторяется. Длинная ячейка — продолжения с точными диапазонами. Coverage всех частей сравнивается с каноническими ячейками.
- [ ] Снять реальные модельные лимиты в T0; проверять бюджет каждой projection до отправки. При неизвестном токенизаторе фиксировать оценку и запас, при явном отказе — контролируемое дальнейшее разбиение. На молчаливое усечение провайдером не полагаться.
- [ ] Сохранить deterministic large-directory extraction, whole для небольших справочников, legacy DOCX/PDF Markdown fallback. Структурный Excel не прогоняется второй раз через Markdown detector.
- [ ] Провести table_refs через staging, OKF, DB, export/import; количество концептов больше не служит доказательством полноты строки.

**Приёмка:** coverage всех фрагментов покрывает каждую непустую ячейку/текстовый диапазон; нет превышения принятого бюджета или потери одинаковых строк.

## T7. Поиск и контекст ответа

**Files:** изменить services/{vector_store,context_builder,generation_search}.py, api/{search,chat}.py, models/schemas.py; расширить test_context_builder.py, test_generation_search.py, test_retrieval_source_snapshot.py, test_chat_search_scope.py; создать test_table_retrieval.py.

**Interfaces:** hydrate_table_fragments(hits, scope) -> list[ContextBlock] гидрирует refs из БД в рамках doc/source/generation scope; Qdrant payload содержит минимальные идентификаторы. Snapshot выбранных источников сохраняет точные фрагменты и refs.

- [ ] RED: запрос по крайней правой колонке; одинаковый ключ на разных листах; разные группы одной строки; scoped search исключает запрещённый источник.
- [ ] Сначала замерить существующий BM25+dense+RRF на новом тексте без бонусов таблицам. Для отдельной правки ranking необходимы гипотеза и отдельный baseline/after.
- [ ] Merge/dedup сохраняет разные row_part и не подменяет точный фрагмент гигантским chunk. Ключ строки и запрошенная колонка прослеживаются до финального контекста.
- [ ] Старый selected-source answer использует сохранённые фрагменты, без нового retrieval и без подмены версии ячейки.
- [ ] Считать candidate recall отдельно от final-context recall. Для 20 табличных вопросов нужная ячейка+шапка должны дойти до контекста; для остальных 20 сохраняется весь обязательный baseline evidence.
- [ ] Значение/источник проверять отдельно от recall. Live ответы повторить 3 раза при одинаковых настройках; сохранить каждый неуспех, не скрывать его усреднением.

**Приёмка:** отсутствие смешения колонок/строк и регрессии обязательных прозаических/mail/review источников. Рост recall без правильного значения недостаточен.

## T8. Диагностика и просмотр ячеек

**Files:** изменить backend/app/api/documents.py, models/schemas.py, frontend/src/components/{DocumentList,DocumentSources,SourceLocationView,SourceContentViewer,ChatSources}.jsx, frontend/src/lib/api.js, i18n/locales/{ru,en}.js; создать TableSourceView.jsx; обновить i18n manifests и focused frontend tests по существующей структуре.

**Interfaces:** GET /documents/{doc_id}/tables/{table_id}?generation_id=... — descriptor/quality; GET .../rows?from_row=...&limit=... — диапазон, до 100 строк и до 1 MiB ответа. В oversized-row случае возвращается ограниченная проекция с явным continuation, не полный/молча обрезанный результат. Применяются ACL и generation rules T5.

- [ ] RED: различение «содержимое пропущено», «шапка неоднозначна», «резервный метод, покрытие проверено»; отсутствие отчёта не превращается в успех.
- [ ] Показать лист/таблицу, диапазон, покрытие, причину и действие. Provider errors/tracebacks не выводить.
- [ ] Переход из ответа показывает нужную строку/колонки и позволяет дозагрузить остальные; 316 колонок не рендерятся в каждой карточке.
- [ ] Проверить значения с HTML/скриптом, 403/404 без утечки, диапазон вне таблицы, удалённое поколение/документ.
- [ ] UI acceptance на desktop и 360px: клавиатура/focus, локализация, внутренний table scroll без переполнения страницы. Runtime i18n manifest обновить до pytest/check-project.

**Приёмка:** причина понятна без лога, значение проверяется в конкретной версии источника; legacy UI работает.

## T9. Пилот, миграция корпуса и откат — R2

**Files:** создать backend/scripts/reprocess_table_documents.py, backend/tests/test_reprocess_table_documents.py, docs/TABLE_PROCESSING.md; расширить backup/restore/generation tests и SECURITY.md для новых границ.

**Interfaces:** CLI по умолчанию --dry-run; --doc-ids-file PATH, --target-version VERSION, --limit N; запись только --apply; продолжение --resume-run RUN_ID. Manifest: old/new generation IDs, версии, planned|prepared|verified|published|failed|rolled_back. Повторный запуск не создаёт повторные активные версии.

- [ ] Dry-run ищет XLSX и вложения по старому parser/classifier, ширине и шапкам; не только по warning: «успешный» старый документ тоже может иметь неверную шапку.
- [ ] Backup и проверенный restore в отдельные DB/data-dir/Qdrant collection до пилота; отдельно сверить исходные файлы и старые источники чатов.
- [ ] R1 требует повторной классификации для переоценки предупреждения. R2 с новыми шапками требует нового разбора оригинала/проекций; простой reindex этого не исправляет.
- [ ] Пилот: A + обычный справочник + сложная шапка + DOCX со вложением + документ без таблиц. Сначала prepare/verify; только успешный кандидат публикуется.
- [ ] Fault injection до/после DB batch, Qdrant upsert и active switch; resume идемпотентен. Candidate с failed coverage не публиковать.
- [ ] Замороженный T0 corpus обязателен. Предлагаемые performance gates до принятия плана: p95 поиска не хуже baseline более чем на 15%; peak RSS крупнейшего fixture не хуже более чем на 20%; одинаковая нагрузка/настройки, минимум 30 запросов на p95-сценарий. Пороги согласовать до реализации, не менять для окрашивания failed в passed.
- [ ] Измерить диск/rows/fragments/points. Не обещать прежнее число концептов. Выполнить parser/backend suites, frontend tests/build, check-project; перечислить отсутствующие проверки явно.
- [ ] Массовое применение после пилота идёт ограниченными партиями через существующую очередь. Следующая партия после отчёта предыдущей. Мигратор не удаляет оригиналы или действующую публикацию.
- [ ] Откат: отключить новый маршрут для новых заданий; остановить очередь миграции; восстановить согласованные DB+Qdrant+артефакты прежнего поколения. Одного переключения pointer недостаточно: доказать наличие старых концептов/точек, иначе restore backup. Схема аддитивна; destructive downgrade после записи новых данных не выполнять.

**Приёмка R2:** в отчёте раздельно implemented, locally verified, pilot accepted, corpus migrated. Один успешный пилот не обозначает миграцию всего корпуса.

## T10. Точные операции — отдельный проект R3

Это входные требования следующей спецификации, не поручение реализовать в R1/R2.

- [ ] Собрать реальные вопросы с filter/count/distinct/sum/сравнениями; сравнение версий — отдельный явный режим.
- [ ] Спроектировать TableQuery(table_ref, filters, selected_columns, operation, limit). Модель выбирает разрешённые операции; сервер проверяет типы/колонки/ACL и выполняет сам, без произвольного SQL/Python.
- [ ] При неоднозначной таблице, единице или отсутствующем результате формулы уточнять запрос/отказываться от неподтверждённого вычисления.
- [ ] TableQueryResult(value, matched_count, complete, evidence_refs, generation_id). Подсчёт не делается по top_k; неполный набор не выдаётся как полный итог.
- [ ] Отдельная приёмка: полная выборка, типы/единицы/десятичные числа/даты/blank, ограничения ресурсов, координаты, проверяемый экспорт и access tests.

## Команды проверки при реализации

Это будущие команды, в планировании они не выполнялись. Из корня PowerShell использовать текущий doc-parser, а не устаревшую установленную копию:

```powershell
$env:PYTHONPATH = "$PWD/doc-parser/src;$PWD/backend"
.\backend\.venv\Scripts\python.exe -m pytest doc-parser/tests/test_xlsx_tables.py doc-parser/tests/test_table_headers.py -q
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_field_table.py backend/tests/test_table_classification_contract.py backend/tests/test_table_quality.py -q
.\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_table_store.py backend/tests/test_table_projection.py backend/tests/test_table_retrieval.py backend/tests/test_reprocess_table_documents.py -q
.\backend\.venv\Scripts\python.exe -m pytest doc-parser/tests -q
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
node scripts/check-project.mjs
git diff --check
```

Перед полным backend suite проверить docs/DEVELOPMENT_CHECKS.md, изоляцию DB/data и необходимые живые зависимости. Интеграционные проверки не направлять в рабочий корпус. Frontend build — через установленный Node/Next CLI, без сломанных npm/npx shims.

## Перед исполнением

Рекомендация: начать с T0–T2 и review двух live-probe, затем связанный выпуск T3–T9. Ресурсные пороги T9 требуют принятия до baseline/after. R3 — отдельная спецификация после R2. Способ исполнения выбирается отдельно; дополнительные агенты сейчас не запускались.

Самопроверка: предусмотрены типы/формулы, шапки, бюджеты, повторные строки, поколения/ACL, snapshot-ссылки, отчёт после staging, migration/backup/restore и regression/resource gates. Независимый review и выполнение тестов пока не проводились.
