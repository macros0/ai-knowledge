# Hybrid Attachment Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Автоматически обрабатывать небольшие вложения, запрашивать выбор тяжёлых файлов во всём дереве писем/ZIP/7z/RAR, явно показывать состав и быстро исключать уже обработанные вложения без повторной генерации оставшегося документа.

**Architecture:** Ограниченный preflight строит дерево источников; durable review и snapshot управляют разбором, генерацией и публикацией. Для строгого сокращения опубликованного состава отдельная операция копирует сохранённые результаты и готовые векторы в новый candidate, затем атомарно публикует его. Индексы чанков и привязки оставшихся концептов сохраняются; исключённые чанки сохраняются в отдельном неиндексируемом архиве.

**Tech Stack:** Существующие Python/FastAPI, SQLAlchemy/Alembic, Qdrant, doc-parser и React/Next.js; новые архивные зависимости `py7zr`, `rarfile` и проверенный 7-Zip backend для RAR.

**Spec:** [Подробное ТЗ](../specs/2026-10-17-hybrid-attachment-processing-design.md). Исполнитель читает оба документа. Реализация находится в локальном `main`; актуальное состояние приведено ниже. Изменения этой задачи не закоммичены и не отправлены. T1–T10 — исторический план; подшаги T11–T15 сохраняют исходную программу исправлений. Незаполненные исторические чекбоксы не заменяют результаты в [приёмке](../../acceptance/2026-10-17-hybrid-attachments.md). [Отчёт внедрения](../../acceptance/2026-10-17-hybrid-attachments-rollout.md) описывает первоначальный перенос и migration063. Перед работой проверить HEAD и незакоммиченные изменения.

**Актуально на 18.10.2026:** T16–T26 реализованы в локальном main, migration064 применена, backend перезапущен. По отдельной команде пользователя опубликовано fast_exclude:142 активных чанка/1716 концептов,2767 архивных чанков/2909 сохранённых;28.87с admission→publish. Хэши оставшихся результатов и векторов совпали. Затронутые backend193passed/1skip, frontend542passed/1skip, webpack PASS, lint0errors/2existingwarnings. Это функциональное включение для локальной проверки; A50 НЕ принят: диагностическая копия превысила30с,10-run p95 и ресурсная приёмка остаются открытыми. Исключённые чанки сохраняются вне активного поиска; оставшиеся записи не перенумеровываются. Commit/push не выполнялись.

## Итог выполнения на 17.10.2026

- [x] T1–T4: оценка, ZIP/7z/RAR, durable review и policy-aware parser.
- [x] T5–T7: pipeline/publication, API/admission/dedup, recovery/maintenance/export/downloads.
- [x] T8: RU/EN UI, persistent выбор, active/pending metadata, reader/editor и360px browser proof.
- [x] T9: read-only пользовательский пример, ресурсные замеры и неизменённые performance gates.
- [x] Исторический T10 до предпросмотра: 4288 backend passed/47 skips в Linux; 499 parser passed; 506 frontend passed/1 skip; сборки/линтеры/audit; A01–A24 evidence и runbook. Эти результаты не переносятся автоматически на текущий checkout.
- [x] Рабочий локальный main: копия БД/schema reconciliation, migration063, SSO Admin UI и flag включены; [отчёт внедрения](../../acceptance/2026-10-17-hybrid-attachments-rollout.md). Это исторический результат внедрения; последующий полный пересчёт отменён по команде пользователя, быстрые изменения состава ещё не реализованы.
- [x] Предпросмотр: API, ограниченный XLSX reader, UI и реальная SSO-проверка листов; focused backend 64 passed/7 skips, frontend 509 passed/1 skip, webpack build и targeted lint. Review и active generation не изменены.
- История до T14: первый полный backend запуск упёрся в PermissionError при collection старых `tests/tmp`; после исключения tmp найден `test_upload_records_document_upload` с утечкой включённого flag из локальной `.env`. Исправление изоляции и новые результаты приведены в T14 ниже; первоначальные неуспешные логи сохранены.
- [x] T11: сохранение выбора до expand, сохранение имени вложения, whitelist реально поддерживаемых форматов. Новые сбои сначала воспроизведены; focused backend12/parser25/frontend8 прошли.
- [x] T12: лёгкий Windows spawn bootstrap, диагностика и parser regressions43 passed/7 platform skips. Live SSO: HTTP p95 659–686 ms по20 samples, UI p95 до1097 ms, первое открытие1167 ms; изоляция сохранена.
- [x] T13: постоянное RU/EN пояснение сырых значений, объединение запросов листов, reopen/stale review/409, refresh с сохранением локальных решений. Независимое ревью нашло дополнительный submit→PUT409 сброс; две новые проверки409/network RED→GREEN. Полный frontend523 passed/1 skip, финальная webpack build прошла.
- [x] T15: originals переживают publish/review replacement/cleanup и новую генерацию для ZIP/7z/RAR; рекомендации сохраняются отдельно от decisions. Проверены DELETE, hash mismatch, запрет записи в active; relative DATA_DIR закрыт отдельной регрессией и финальным Linux delta-набором.
- [x] T14: изоляция test flags и EMBEDDING_* исправлена; evidence записано. Полный parser499 passed/1 skip, frontend523 passed/1 skip, ESLint0 errors/36 прежних warnings. Linux полный source snapshot:4314 passed/47 skipped/1 failure из-за защитного free-space reserve в тестовом tmpfs. Повтор этого теста на достаточном диске и fresh-process набор после финального delta:64 passed. Это совокупность проверок, а не единый зелёный full-run. Полный Windows suite не завершён; одна группа1431 passed/18 skipped/2 failures из-за env leakage, затем весь соответствующий модуль16 passed после исправления. Причины и границы перечислены в приёмке.
- [ ] Быстрое исключение T16–T24 не реализовано. Пользователь запустил полную генерацию и явно отменил её; отмена завершена, candidate отсутствует. Действует прежний результат:2909 чанков/4483 концепта. Контрольный результат нового пути:142 активных/2767 архивных чанков и1716 прежних концептов без изменения их привязок. Исключение в рабочем документе ещё не применено.

## Замечания ревью от 17.10.2026

Ниже сохранена исходная диагностика до исправлений. R1–R5 и дополнительные lifecycle-контракты закрыты реализацией и evidence T11–T15; прежние формулировки «не подтверждено» относятся к моменту ревью.

| ID / приоритет | Подтверждение и последствие | Исправление / приёмка |
|---|---|---|
| R1 / P2 | `DocumentProcessingScopeDialog.jsx:54–56` отправляет expand без dirty selection; `apply()` заменяет галочки серверными. Локальное включение Excel теряется при раскрытии другой ветки. Подтверждено по коду, интерактивный regression пока отсутствует. | T11 / A28 |
| R2 / P2 | `diagnostic_entrypoint.py:8` импортирует diagnostics runtime; Windows spawn повторяет тяжёлые imports. API 4,89–5,37 с, пустой worker 4,98–5,23 с, XLSX только 4–56 мс. | T12 / A27 |
| R3 / P2 | `document_processing_scope.py:168` передаёт имя root при branch expansion. Синтетический probe переименовал `actual-table.xlsx` в `decision.eml`; это имя затем используется при download. | T11 / A29 |
| R4 / P2 | `attachment_inventory.py:175–177` приравнивает распознавание к поддержке. Синтетический EML→PPTX: supported=true, small, included=true при отсутствии PPTX parser. | T11 / A30 |
| R5 / P2 | `xlsx_preview.py:118–133` показывает сырые значения без предупреждения: дата→46312, 15%→0.15, 000042→42, TRUE→1; limited=false. Режим отображения не описан и может вводить в заблуждение. | T13 / A31 |

Дополнительный подтверждённый путь риска: AbortController отменяет fetch, но синхронный endpoint продолжает worker; быстрые смены листа могут занять оба parser slots и дать 429 для последнего листа. Нагрузочное воспроизведение в этом ревью не выполнялось; T13/A26 должны его покрыть. Исходные latency-артефакты: `tests/tmp/profile_attachment_preview.json` и `tests/tmp/preview-entrypoint-imports.log` (локальные, не добавлять в Git).

Разрывы контрактов: сохранение уже извлечённых excluded originals после publish/cleanup не подтверждено; рекомендации aggregate_budget теряются после Save; старое ТЗ обещало отсутствующие inventory digest/список duplicates в durable JSON и иной DELETE. В ТЗ JSON и DELETE согласованы с реализацией, а сохранение originals и рекомендаций оставлено обязательной доработкой T15. Отсутствие этих сценариев в прежних тестах не обозначать как PASS.

### Новые пробелы, подтверждённые проверкой кода

| ID / приоритет | Подтверждение и последствие | Доработка |
|---|---|---|
| R6 / P1 | `processing_admission.confirm_processing_scope` вызывает `Pipeline.regenerate` для любой опубликованной версии; нет reuse результатов | T16–T20: отдельные анализ/apply, никогда не fallback на confirm |
| R7 / P1 | В legacy sources нет надёжного source_key; одинаковые SHA не различают физические вхождения | T16: проверка root/parent/locator/occurrence и provenance; ambiguous/mixed блокируют |
| R8 / P1 | `canonical_repair` запрещает scope-aware изменения и заново считает embeddings; `clone_generation_tables` копирует все таблицы | T17–T18: отдельная фильтрация активного контента, архив чанков, reuse vectors |
| R9 / P1 | `Pipeline.cancel_update` при отсутствии своего Future может начать cleanup, пока отдельный fast worker пишет | T19: общий учёт владельца, durable cancel/lease, порционные блокировки и restart recovery |
| R10 / P2 | Cleanup удаляет retired generation; request receipt только в поколении не переживёт повтор запроса после очистки | T19: отдельная durable таблица receipts и атомарный publish результата |
| R11 / P2 | `DocumentList.jsx` вызывает кнопку состава без проверки наличия выбора | T20: available/none/unknown из batched metadata, без preflight в списке |
| R12 / P1 | Простое удаление концептов оставит excluded chunk/табличные точки; перенумерация сломает ссылки, чтение архива backfill вернёт исключённое | T17–T18/T21: активный manifest отдельно от архива, неизменные indices, coverage всех потребителей |

## Global Constraints

- До подтверждения тяжёлого состава нет LLM-вызовов, таблиц кандидата, эмбеддингов и поисковых точек этого запуска.
- При первоначальной/полной обработке исключение применяется до подробного разбора, включая upload-preview для похожести. При быстром исключении разбора вообще нет: используются опубликованные данные.
- Выбор не изменяется при resume или изменении конфигурационных порогов.
- ZIP, 7z и RAR входят в первую версию; вложенные письма и архивы обходятся рекурсивно.
- Новый snapshot содержит `container_max_depth=6`: root имеет глубину 0, источники на глубинах 1–6 допускаются, попытка спуститься на 7 даёт видимый `skipped_depth`.
- Пределы: 1000 source nodes, payload 50 MiB, суммарное извлечение 200 MiB, 10000 archive entries, compression ratio 1000:1; более строгие ограничения формата сохраняются.
- Стартовые мягкие пороги: rows 500, cells 20000, text bytes 524288, expanded bytes 8388608, file bytes 10485760; суммарно rows 1000, cells 40000, text bytes 1048576, file bytes 20971520.
- Preflight: 15 секунд и 33554432 прочитанных распакованных байта. Общий parser semaphore и memory limit обязательны.
- Для нового окружения `ATTACHMENT_REVIEW_ENABLED=false` до приёмки; в рабочем локальном main он уже включён по команде пользователя. Выключение флага не отменяет уже сохранённую policy; тестовые settings не должны наследовать рабочую `.env`.
- `doc_id VARCHAR(16)`, snapshot schema version 1, `NULL` старого snapshot читается как `legacy_all`.
- Пользователь командой «Продолжай» разрешил выполнение T11–T15. Работа идёт в main, backend/frontend перезапущены после проверки отсутствия активной обработки. Дополнительная SQL-миграция и перегенерация пользовательского корпуса не требуются. Commit/push по-прежнему запрещены пользователем.
- По действующей команде пользователя продолжать в локальном `main`, сохраняя несвязанные изменения. Не повторять уже выполненный перенос, migration063 и включение flag.
- Коммиты — только при отдельной команде пользователя, с явным набором файлов; `git add .` запрещён. Checkpoints задач ниже означают проверенные локальные результаты, а не автоматический commit.
- T16–T24: fast_exclude/reuse_only дают ноль parser/LLM/classifier/embedder/detection calls. Delta вызывает только недостающие этапы workset, не регенерирует retained sources. Ни один режим не вызывает полную regenerate при отказе.
- Оставшиеся `slug`, `chunk_index`, текст, source spans и время генерации неизменны; indices могут иметь пробелы. Исключённые chunks/concepts/tables/vectors сохраняются в `source-cache/<cache_id>/`, не участвуют в активном SQL-поиске/индексе/экспорте знаний. При повторном включении reuse выполняется по подтверждённым завершённым этапам; порядок отображения отделён от IDs.
- Новый active появляется только после полной проверки candidate. Векторные batches≤256; preview TTL15мин; worker максимум1 на процесс без очереди; lease60с/renewal20с. Новые targets A50: p95 preview≤5с/apply≤30с на изолированной копии,10 запусков после прогрева; пока не измерены.
- Миграция новой таблицы T19 только запланирована;063 не менять. В этой редакции не запускать проверки, меняющие рабочий документ, и не применять новую миграцию к рабочей БД.

## Review Focus

- Проверка похожести и mail fingerprint могут незаметно прочитать excluded Excel до генерации: тесты T6 должны наблюдать вызовы parser, а не только конечные чанки.
- Вложенный MSG → solid RAR → XLSX требует наследования бюджетов и остановки внешнего helper: тесты T2/T4/T10 проверяют цепочку и оставшиеся процессы.
- Другой пользователь/вкладка может поменять review во время confirm или потери ответа: тесты T3/T6 проверяют CAS, request ID и отсутствие второго worker.
- Active badge может переключиться до publication либо cancel восстановить не тот scope: тесты T5/T8 проверяют два поколения и ответы API в обе стороны гонки.
- Reindex/backfill/repair и старые snapshots способны вернуть excluded текст: тесты T7/T10 проверяют SQL, Qdrant и final sources после повторного обслуживания.
- Dirty selection, branch identity и распознанный формат без parser: T11 проверяет реальные переходы UI/API, а не только статический рендер.
- Windows bootstrap, быстрые переключения, stale review и освобождение slots: T12/T13 измеряют путь штатного entrypoint; AbortController и автономный worker probe сами по себе недостаточны.
- Pending download не доказывает доступ к original после publish/cleanup; рекомендации и пользовательские решения проверяются отдельно в T15.
- Для T16–T24 дополнительно: одинаковые вложения разных родителей (T16), archive попадает в reindex (T18), поздняя запись worker после cancel (T19), потерянный ответ после publish/cleanup (T19), ссылки на chunk после исключения (T17/T20). Для каждого ниже предусмотрена отдельная проверка.

## Карта компонентов и порядок

| Задача | Компонент | Зависит от | Результат |
|---|---|---|---|
| T1 | Типы источников, оценка, рекомендации | — | Детерминированный ограниченный estimator |
| T2 | ZIP/7z/RAR adapters и поставка | T1 types | Проверенное извлечение под общим бюджетом |
| T3 | Durable review и миграция | T1 types | CAS и snapshot storage |
| T4 | Policy-aware parser и supervisor | T1, T2 | Excluded содержимое не разбирается |
| T5 | Pipeline, staging, publication | T3, T4 | Состав привязан к поколению |
| T6 | Upload, review API, admission, dedup | T1–T5 | Полный backend flow ожидания/подтверждения |
| T7 | Обслуживание, export, downloads, recovery | T3–T6 | Исключения устойчивы ко всем повторным путям |
| T8 | UI выбора и отображения состава | T6 contracts | Пользовательский сценарий RU/EN |
| T9 | Пороговая калибровка и ресурсные проверки | T1–T8 | Измеренные пороги/границы |
| T10 | Сквозная приёмка и rollout | T1–T9 | Проверенный release candidate и инструкция |
| T11 | Выбор, идентичность ветки, поддержка форматов | База T1–T10 | R1/R3/R4; A28–A30 |
| T12 | Лёгкий bootstrap изолированного preview worker | Текущий preview | R2; A27 |
| T13 | Preview lifecycle, точность и ошибки | T11, T12 | R5; A25/A26/A31 |
| T15 | Originals и рекомендации после публикации | T11 | A33–A35 |
| T14 | Тестовая изоляция и финальная повторная приёмка | T11–T13, T15 | A25–A35 и регрессия A01–A24 |
| T16 | Анализ разницы состава и доказуемая идентичность | Текущие scope/source contracts | A38/A39/A43, immutable план изменения |
| T17 | Candidate из сохранённых данных и архив чанков | T16 | A36/A40/A41/A48, стабильные привязки |
| T18 | Reuse векторов и проверки публикации/обслуживания | T16, T17 | A42/A46, точный активный состав |
| T19 | Migration/API, admission, worker/cancel/recovery | T16–T18 | A44/A45/A49, durable операция |
| T20 | Fast apply UI, counts и доступность кнопки | T19 contracts | A47, явное отделение полной генерации |
| T22 | Durable cache завершённых этапов | T16–T19 | A52–A54/A58, повторное включение без повторной работы |
| T23 | Delta генерация и resume | T19, T22, T24 | A51/A53/A56/A57, обработка только недостающего |
| T24 | Порядок и стабильные идентификаторы | T17, T19, T22 | A55/A59, новые данные на месте без смены старых IDs |
| T21 | Сквозная приёмка и инструкция поставки | T16–T20, T22–T24 | A36–A59, весь сценарий изменения состава |

T1–T10 сохраняют первоначальную разбивку файлов; пометка «create» в историческом шаге не означает, что файл сейчас отсутствует. Проверить существующие файлы перед изменением. Для доработок порядок: T11 → T12 → T13 → T15 → T14; включённый flag не заменяет приёмку.

Новый порядок после отдельной команды на реализацию: T16 → T17 → T18 → T19 → T20 → T22 → T24 → T23 → T21. Выполнение inline через `superpowers:executing-plans`, без создания новых чатов/агентов. Это продолжение существующего плана, не повтор T1–T15.

## Рабочая среда и команды

Из `backend/` использовать `.venv\Scripts\python.exe`, не системный Python. Для doc-parser из `doc-parser/` — `..\backend\.venv\Scripts\python.exe`. Pytest basetemp направлять в `tests/tmp/pytest-runs/attachment-scope-<задача>`; параллельные запуски не должны делить каталог.

Frontend: из `frontend/` выполнять `node --test ...`, `node node_modules/eslint/bin/eslint.js ...`, `node node_modules/next/dist/bin/next build`. npm/npx shims на этой машине не использовать для этих команд. Перед изменением frontend прочитать подходящие локальные руководства в `frontend/node_modules/next/dist/docs/` согласно `frontend/AGENTS.md`.

Модульные тесты используют временную SQLite, синтетические документы, stub LLM и embeddings. Проверки PostgreSQL выполняются на отдельной тестовой БД, Qdrant — на отдельной коллекции/инстансе с уникальным именем. Продакшен/локальный рабочий корпус не используется как sandbox.

Для актуального полного backend запуска исключить артефакты из collection: `.venv\Scripts\python.exe -m pytest tests/ --ignore=tests/tmp -q --basetemp=../tests/tmp/pytest-runs/attachment-review-full`. Не удалять чужие tmp-каталоги ради зелёного collection. Перед запуском выполнить T14: явные test settings и fixtures для обеих веток flag. Одного глобального `ATTACHMENT_REVIEW_ENABLED=false` недостаточно для проверки функции. Frontend production build на этой машине: `node node_modules/next/dist/bin/next build --webpack`.

### T1. Контракт источников, ограниченная оценка и рекомендации

**Files — create:**

- `doc-parser/src/docparser/attachment_policy.py` — dataclasses, source key и effective selection.
- `doc-parser/src/docparser/attachment_inventory.py` — единый обход контейнеров и метрики.
- `doc-parser/src/docparser/attachment_assessment.py` — пороги, суммирование и рекомендации.
- `doc-parser/tests/test_attachment_assessment.py`.
- `doc-parser/tests/test_attachment_inventory.py`.
- `doc-parser/tests/attachment_fixtures.py` — генерируемые синтетические документы.

**Files — modify:** `doc-parser/src/docparser/source_model.py`, `doc-parser/src/docparser/__init__.py`, `backend/app/config.py`.

**Interfaces:**

- `AssessmentLimits` — все именованные значения из §5 ТЗ; `TraversalLimits(max_depth, max_nodes, max_payload_bytes, max_total_bytes)` — hard limits §6.1.
- `AttachmentNode` — `source_key`, parent key, locator, name, kind, supported, metrics, metric precision, assessment, `children_complete`; root не входит в selectable nodes.
- `AttachmentInventory` — `schema_version`, исходный SHA, assessor/parser versions, nodes, completeness и bytes/time counters.
- `ScopeSelection` — список `SourceDecision` и `requires_review`; `SourceDecision` содержит свойства §8 ТЗ.
- `inspect_attachment_inventory(path: Path, filename: str, *, limits: AssessmentLimits, traversal: TraversalLimits, expanded_source_keys: frozenset[str] = frozenset()) -> AttachmentInventory`.
- `recommend_scope(inventory: AttachmentInventory, limits: AssessmentLimits) -> ScopeSelection`.
- `make_source_key(source_file_hash: str, locator_chain: tuple[str, ...]) -> str`.

- [ ] Создать тесты порогов 499/500 строк и 19999/20000 ячеек; lower_bound после остановки, пустого форматированного хвоста, скрытых/formula-only ячеек, широкого текста, root XLSX и inline images. Проверять отсутствие вызовов `parse_document`, generator и TableSink при оценке.
- [ ] Создать тест суммы пяти файлов по 250 строк: review включён, выключены ровно два источника по детерминированному порядку, оставшиеся 750 строк ниже 1000. Отдельно проверить unknown и контейнер без двойного суммирования детей.
- [ ] Запустить из `doc-parser/`: `..\backend\.venv\Scripts\python.exe -m pytest tests/test_attachment_assessment.py tests/test_attachment_inventory.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t1`. Зафиксировать ожидаемые FAIL до реализации.
- [ ] Реализовать типы, стабильные keys, streaming XLSX/Office/mail/PDF оценку и saturating counters. Контент не сохранять в result; inventory ограничить лимитом узлов. Версию estimator начать с `attachment-assessment-v1`.
- [ ] Повторить команду: PASS. Проверить воспроизводимость JSON при повторном чтении одного файла и независимость ключей от имён-дубликатов и выключенных соседей.

**Checkpoint:** A01, A06, A07 и часть A08/A09 доказаны синтетически; пороги пока проектные, калибровка T9 впереди.

### T2. Рекурсивные ZIP, 7z и RAR и зависимости

**Files — create:**

- `doc-parser/src/docparser/archive_reader.py` — общий structured interface.
- `doc-parser/src/docparser/archive_zip.py`, `archive_7z.py`, `archive_rar.py` — adapters.
- `doc-parser/tests/test_archive_reader.py`, `test_archive_safety.py`.
- `doc-parser/tests/fixtures/archives/README.md` — происхождение и лицензии маленьких RAR fixtures.
- `backend/scripts/check_archive_runtime.py` — smoke-проверка поддерживаемых codecs без подключения к БД.
- `docs/ARCHIVE_ATTACHMENTS.md` — ограничения и runtime-поставка.

**Files — modify:** `doc-parser/src/docparser/archive_guard.py`, `doc-parser/pyproject.toml`, `backend/Dockerfile`, `backend/app/config.py`, `THIRD_PARTY_NOTICES.md`, `SECURITY.md`.

**Interfaces:**

- `ArchiveEntry` — entry key/locator, original display path, type, declared sizes, encryption/volume flags, supported method; каталоги не создают source nodes.
- `open_archive(path: Path, *, format: str, limits: TraversalLimits, helper_path: Path | None) -> ArchiveReader` — context manager.
- `ArchiveReader.entries() -> Iterator[ArchiveEntry]` — bounded structured listing.
- `ArchiveReader.extract_selected(entry_keys: frozenset[str], sink_factory: Callable[[ArchiveEntry], BinaryIO]) -> ExtractionReport` — controlled spool; report возвращает actual byte counts и typed per-entry errors.
- Adapters не вызывают document parser; обход T1 и parser T4 используют эти интерфейсы и общий lifetime budget.

- [ ] Добавить маленькие ZIP/7z fixtures и RAR3/RAR5 stored/compressed/solid fixtures с зафиксированным происхождением. Тесты параметризовать по трём форматам; отсутствие RAR backend в acceptance-среде означает FAIL, не skip.
- [ ] Проверить selective extraction, Unicode, два одинаковых basename в разных каталогах, archive-in-archive, MSG-in-archive, CRC failure, encryption, missing volume и отказ codec. Задать тесты обхода `../`, drive/UNC/ADS, symlink/hardlink, дублей нормализованного пути и огромного вывода.
- [ ] Запустить из `doc-parser/`: `..\backend\.venv\Scripts\python.exe -m pytest tests/test_archive_reader.py tests/test_archive_safety.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t2`; ожидается FAIL новых функций до реализации.
- [ ] Реализовать ZIP через `zipfile`; 7z через `py7zr` с quota-aware spool `WriterFactory`; RAR через `rarfile` и явно выбранный 7-Zip backend. Все реальные пути генерируются приложением; команды используют argv, shell=False. Настроить backend в пределах worker, без конкурентной мутации глобального backend settings.
- [ ] Включить adapters в обход T1. OOXML распознавать до generic ZIP; не добавлять архивы в список корневых upload extensions. Содержимое unsupported файлов сохранять только в разрешённом оригинале.
- [ ] Проверить точные доступные версии и лицензии при реализации, закрепить прошедшие тест версии `py7zr`/`rarfile` и version+SHA256 дистрибутива helper в инструкции/сборке. В образе гарантировать наличие RAR codec; тест только `7z --help` недостаточен. Runtime ничего не скачивает.
- [ ] Повторить тесты и `..\backend\.venv\Scripts\python.exe ../backend/scripts/check_archive_runtime.py` из `doc-parser/`; ожидаются успешное чтение содержимого каждого формата и проверенные hashes. Повторить smoke в финальном Linux-образе под непривилегированным пользователем; сохранить фактические версии.

**Checkpoint:** A21–A24 на уровне adapters; общий supervisor lifetime проверяется в T4. Коммерческая лицензия на создание RAR не требуется как скрытый prerequisite: использовать законно распространяемые небольшие read fixtures, источник указать.

### T3. Durable review, immutable snapshot и миграция

**Files — create:** `backend/app/services/processing_scope.py`, `backend/app/models/processing_scope.py`, `backend/alembic/versions/<new_revision>_document_processing_scope.py`, `backend/tests/test_processing_scope.py`, `backend/tests/test_processing_scope_migration.py`.

`<new_revision>` — единственный намеренно генерируемый идентификатор: получить штатно после проверки текущего Alembic head; не копировать parent из старого плана.

**Files — modify:** `backend/app/db/models.py`, `backend/app/models/schemas.py`, `backend/app/services/staging.py`, `backend/app/services/generation_files.py`, `backend/tests/test_schema_drift.py`.

**Interfaces:**

- Pydantic `ProcessingScopeSnapshot`, `ProcessingReviewOut`, `ProcessingScopeSummary` — точные поля §8–9 ТЗ, schema_version=1.
- `create_review(session, doc_id: str, inventory: AttachmentInventory, *, base_generation_id: str | None, actor: str) -> DocumentProcessingReview`.
- `save_review_selection(session, doc_id: str, review_id: str, expected_revision: int, selected_source_keys: frozenset[str], *, actor: str) -> DocumentProcessingReview`.
- `snapshot_from_review(review: DocumentProcessingReview) -> ProcessingScopeSnapshot`.
- `scope_for_generation(session, doc_id: str, generation_id: str | None) -> ProcessingScopeSnapshot | None`; None имеет только legacy semantics.
- `processing_review_paths(settings, doc_id: str, review_id: str) -> Path` в `generation_files.py` — проверенный `uploads/<doc_id>/processing-review/<review_id>` с existing link/path guards, для T6 admission и T7 downloads/cleanup.
- Сервис принимает открытую transaction, сам её не коммитит; порядок блокировок начинается с `lock_document_write`.

- [ ] Написать тесты: NULL legacy, source SHA обязателен при dedup=false, CAS conflict, чужой review, source key другого документа, выключенный предок, ancestor exclusion, user-included heavy, неизменяемый generation snapshot после редактирования draft.
- [ ] Проверить schema migration на свежей и предшествующей SQLite и отдельном PostgreSQL: doc_id=16, unique request ID, nullable legacy columns, FK/purge; add/drop только новых объектов на тестовой БД. Тест существующих runtime-данных не запускать на рабочей БД.
- [ ] Запустить из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_processing_scope.py tests/test_processing_scope_migration.py tests/test_schema_drift.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t3`; получить FAIL нового контракта.
- [ ] Реализовать таблицу и поля §8, CAS, snapshot hash по каноническому JSON, отказ при неизвестной schema version, сериализацию staging scope hash и безопасные review paths. Не обновлять существующие поколения массовым scan.
- [ ] Добавить также `Document.signature_scope_hash` и `DocumentStaging.processing_scope_hash` из §8, хранение similarity token hash/expiry; убедиться, что validation lease и similarity consent используют разные поля и не заменяют друг друга.
- [ ] Повторить тесты: PASS; `.venv\Scripts\python.exe -m alembic heads` — один head. Записать actual parent/new revision в отчёт.

**Checkpoint:** A11/A17 на уровне storage; новая схема ещё не применена к рабочей БД.

### T4. Выбор до рекурсивного parsing и supervisor для полного дерева

**Files — modify:** `doc-parser/src/docparser/parser.py`, `source_model.py`, `embedded.py`, `docx_parser.py`, `xlsx_parser.py`, `pdf_parser.py`, `eml_parser.py`, `msg_parser.py`; `backend/app/services/parser_supervisor.py`, `backend/app/services/parser_limits.py`.

**Files — create:** `doc-parser/tests/test_processing_policy.py`, `backend/tests/test_attachment_preflight_supervisor.py`.

**Interfaces:**

- `ParserProcessingPolicy` в `attachment_policy.py`: source_file_hash, source keys/decisions, expected mapping, traversal limits; `is_included(source_key: str) -> bool` учитывает предков.
- `ParseContext(..., processing_policy: ParserProcessingPolicy | None = None)`; None сохраняет старые границы и форматные возможности.
- `parse_document_supervised(..., processing_policy: dict | None = None) -> SupervisedParseResult` — расширение существующей сигнатуры.
- `inspect_attachments_supervised(path, filename, *, limits, traversal, expanded_source_keys=(), timeout_seconds, max_memory_mb, max_concurrent) -> AttachmentInventory` — тот же semaphore/process protection, отдельный bounded result.

- [ ] Написать spy-тест, который падает, если excluded XLSX дошёл до `parse_xlsx`, TableSink или блока содержимого. Включённый root сохраняет текст, таблицы, комментарии и картинки. Marker excluded не индексируется.
- [ ] Проверить DOCX → MSG/EML → ZIP/7z/RAR → XLSX; отдельно архив → письмо → архив. Подтвердить неизменность key соседей, различие тела письма и детей, границы depth6/depth7, счётчик nodes/bytes без сброса. Сохранить существующие mail_import_enabled запреты.
- [ ] Добавить тесты timeout/OOM и зависшего внешнего helper: родительский supervisor должен остановить весь process tree и освободить semaphore. Неполный inventory возвращает unknown; потеря worker не превращается в empty/all-small.
- [ ] Запустить из `doc-parser/`: `..\backend\.venv\Scripts\python.exe -m pytest tests/test_processing_policy.py tests/test_attachments.py tests/test_container_attachment_limits.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t4-parser`; из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_attachment_preflight_supervisor.py tests/test_parser_supervisor.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t4-supervisor`. Зафиксировать FAIL новых сценариев.
- [ ] Вынести общий admission источника до раскрытия payload. Зарегистрировать исключённый узел/marker `excluded_by_policy`; сохранить ограниченный original либо container reference. Применить общий TraversalLimits во всех ранних проверках EML/MSG, включая OLE storage, без второго старого depth guard.
- [ ] Реализовать обе supervisor операции и typed result validation, ограничить ответы, временные файлы и окружение helper. Повторить тесты: PASS на Windows и Linux для process-tree кейсов.

**Checkpoint:** A02/A03/A08/A09/A21–A24 на parser boundary. Нельзя подменять выбор фильтром уже построенных чанков.

### T5. Snapshot в pipeline, staging и атомарной публикации

**Files — modify:** `backend/app/services/pipeline.py`, `source_chunking.py`, `table_pipeline.py`, `staging.py`, `generation_store.py`, `generation_artifacts.py`, `generation_publication.py`, `document_update.py`, `parse_diagnostics.py`.

**Files — create:** `backend/tests/test_generation_processing_scope.py`.

**Interfaces:**

- Расширить `Pipeline.ingest`/`Pipeline.regenerate` keyword `processing_scope: ProcessingScopeSnapshot | None = None`, `scope_request_id: str | None = None`; resume получает snapshot только из существующей попытки.
- `validate_resume_processing_scope(manifest: dict, snapshot: ProcessingScopeSnapshot | None) -> None` — alongside existing parser/source-file guards.
- `assert_generation_scope(session, doc_id: str, generation_id: str, snapshot: ProcessingScopeSnapshot) -> None` — в `processing_scope.py`, проверяет source ownership у всех SQL-derived rows перед ready.
- Publication snapshot/hash включён в `publication.json` и `processing-scope.json` бандла; активный summary выводится через generation pointer, не через latest draft.

- [ ] Создать тесты первого запуска и двух поколений: содержимое A активно, кандидат B выключает один источник, до publish доступен A; после publish доступен B; ошибка/cancel возвращает A без смены badge.
- [ ] Проверить исключения в обычных chunks, direct table concepts, table rows/projections, источниках locale/dev detection, итоговых payload. Внутренний административный marker не участвует в LLM/поиске/сигнатуре.
- [ ] Проверить resume hash mismatch, изменение конфигурации после enqueue, scope feature flag=false после подтверждения и legacy NULL snapshot. Верификатор должен блокировать намеренно подложенный excluded row.
- [ ] Запустить из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_generation_processing_scope.py tests/test_generation_pipeline.py tests/test_generation_publication_service.py tests/test_document_update_api.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t5`; зафиксировать FAIL новых тестов.
- [ ] Связать snapshot с candidate до исполнения source parsing. При автоматическом пути без review создать тот же тип snapshot. Пропустить policy через оба parsing paths, table/source chunking и publication integrity; запретить смену списка источников в живом кандидате.
- [ ] Повторить тесты: PASS. Проверить, что отложенная физическая очистка прежних Qdrant points не делает их видимыми после переключения generation.

**Checkpoint:** A03–A05/A14/A15, опора для API.

### T6. Upload-preflight, API выбора, similarity и единственный допуск

**Files — create:** `backend/app/api/document_processing_scope.py`, `backend/app/services/processing_admission.py`, `backend/tests/test_processing_scope_api.py`, `backend/tests/test_processing_scope_admission.py`.

**Files — modify:** `backend/app/api/documents.py`, `backend/app/main.py`, `backend/app/services/deduplication.py`, `backend/app/services/mail_identity.py`, `backend/app/services/pipeline.py`, `backend/app/services/registry.py`, `backend/app/error_codes.py`, `backend/app/services/audit.py`.

**Interfaces:**

- Новый router реализует GET/review PUT/POST/DELETE/confirm из §9, подключается в existing application; схемы T3.
- `prepare_upload_scope(path: Path, filename: str, settings) -> tuple[AttachmentInventory, ScopeSelection]` — до существующего глубокого similarity parse.
- `confirm_processing_scope(doc_id: str, review_id: str, revision: int, request_id: str, *, actor: str, is_admin: bool, similarity_token: str | None = None) -> ProcessingAdmissionResult`.
- `ProcessingAdmissionResult`: HTTP-state `accepted|already_accepted|validating|similar|scope_changed`, document/review metadata и scoped similarity token при необходимости.

- [ ] Написать тесты лёгкого пути без диалога, heavy 202/durable ожидания и dedup=false; в ожидании запретить вызовы LLM/embed/index/table parse и удержание worker slot.
- [ ] Добавить тесты exact duplicate pending, existing similarity409, выбранного состава при similarity, scoped token после редактирования, mail fingerprint без excluded bytes, неизвестного ключа/чужого review, viewer/editor/admin и корзины.
- [ ] Добавить concurrency/failure tests: два confirm с одним request ID, тот же ID с другим составом, повтор после потери ответа, активный validation lease, истёкший lease, queue full, crash после durable admission до submit. Assert ровно один worker либо повторяемый draft; нет потери исходника/выбора.
- [ ] Запустить из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_api.py tests/test_processing_scope_admission.py tests/test_upload_similarity.py tests/test_mail_upload_admission.py tests/test_pipeline_queue.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t6`; сохранить FAIL новых тестов.
- [ ] Перестроить upload admission вокруг preflight: лёгкий путь сохраняет существующий ответ; heavy регистрирует документ и SHA/review под существующим admission lock, не запускает ingest. Split коротких SQL операций и долгого parse реализовать явно.
- [ ] Реализовать CAS/token/lease и повторяемый переход submitted. Confirm связывает snapshot/request ID с existing `_start` serialization; повтор проверяет и pending row, и `DocumentGeneration.scope_request_id`. Изменение inventory через expand обновляет revision и возвращает scope_changed до enqueue.
- [ ] Добавить audit actions `document_processing_scope_updated`, `document_processing_scope_confirmed`, обновить перечень допустимых action types/локализацию и `SECURITY.md`. Перед pytest обновить UI-dictionary keys, затронутые новыми кодами.
- [ ] Повторить тесты: PASS. Сохранить примеры JSON light/heavy/active+pending/conflict без реального содержимого документов.

**Checkpoint:** A01/A02/A10–A13; API работает без UI.

### T7. Recovery, bulk, обслуживание, оригиналы и metadata API

**Files — modify:** `backend/app/services/registry.py`, `bulk_generation.py`, `job_queue.py`, `pipeline.py`, `canonical_reindex.py`, `canonical_repair.py`, `generation_cleanup.py`, `generation_files.py`, `source_store.py`, `attachment_store.py`; `backend/app/api/documents.py`, `backend/app/models/schemas.py`.

**Files — create:** `backend/tests/test_processing_scope_maintenance.py`, `backend/tests/test_processing_scope_downloads.py`.

**Interfaces:**

- `load_processing_scope_summaries(session, doc_ids: list[str]) -> dict[str, ProcessingScopeSummary]` в `processing_scope.py` — batch lookup активных snapshots.
- `processing_scope_review_required` — общий bulk skip code; recovery ожидания не использует `STALE_STATUSES`.
- `GET .../processing-scope/attachments/{source_key}` — только уже сохранённые controlled artifacts; source key проверяется относительно конкретного review/doc.
- Review paths и root проверки берутся из `processing_review_paths` T3; lifecycle удаления добавляется здесь.

- [ ] Написать тесты startup waiting/review lease recovery, bulk regenerate/resume с waiting docs, существующего saved scope и старого legacy regenerate, который обнаружил тяжёлое и требует действия.
- [ ] Проверить `Pipeline.wait_for` и итог bulk для `awaiting_attachments`: завершение ожидания worker возвращает requires_action без многочасового ожидания terminal done; метрики очереди не включают время пользовательского выбора.
- [ ] Написать тест с уникальным термином excluded source: reindex, chunks backfill, repair и повторный restart не возвращают термин. Неадаптированный maintenance path должен возвращать `scope_not_supported`, а не молча включать всё.
- [ ] Проверить original download до/после publication, container-only и unknown archive branch, чужой source key, canceled review, trash/purge. Export original сохраняет байты; OKF export содержит snapshot/hash.
- [ ] Запустить из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_maintenance.py tests/test_processing_scope_downloads.py tests/test_generation_reindex.py tests/test_generation_attachment_backfill.py tests/test_attachments_api.py tests/test_document_sources_api.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t7`; ожидается FAIL до подключения policy.
- [ ] Реализовать snapshot lookup всех maintenance путей, корректный отказ для неадаптируемого режима, batch summaries без N+1, downloadable artifacts и export. Не начинать распаковку/генерацию из GET.
- [ ] Повторить тесты: PASS; измерить число SQL-запросов list endpoint на 1 и 100 документов: фиксированное число дополнительных batch-запросов, не +100.

**Checkpoint:** A15–A19 и отсутствие обходных путей исключения.

### T8. Выбор дерева вложений и видимый состав документа

**Files — create:** `frontend/src/components/DocumentProcessingScopeDialog.jsx`, `frontend/src/components/ProcessingScopeBadge.jsx`, `frontend/src/lib/processingScope.mjs`, `frontend/test/processingScope.test.mjs`, `frontend/test/processingScopeFlow.test.mjs`.

**Files — modify:** `frontend/src/lib/api.js`, `sourceTree.mjs`, `documentUploadBatch.mjs`, `documentUploadState.mjs`; `frontend/src/hooks/useDocumentUpload.js`; `frontend/src/components/DocumentUploadPanel.jsx`, `DocumentList.jsx`, `SourceContentViewer.jsx`, `BulkGenerationModal.jsx`; `frontend/src/app/documents/[docId]/fulltext/page.js`, `frontend/src/app/globals.css`; `frontend/src/i18n/locales/ru.js`, `en.js`, `backend/app/i18n/ui_keys.json`, `ui_en.json`.

**Interfaces:**

- API wrappers `getProcessingScope(docId)`, `createProcessingScopeReview(docId, body)`, `saveProcessingScopeReview(docId, body)`, `confirmProcessingScope(docId, body)`, `cancelProcessingScopeReview(docId, body)`.
- `DocumentProcessingScopeDialog({docId, open, onClose, onUpdated})`; серверный review — источник истины, dirty selection локальный только до PUT.
- `ProcessingScopeBadge({summary, onOpen})`; summary принадлежит active generation, pending отображается отдельной строкой.
- `effectiveSelection(nodes, selectedKeys)` и `scopeBadgeModel(active, pending)` — чистые функции для semantic UI tests.

- [ ] Добавить тесты effective selection для mail body/archive children/ancestor exclusion, промежуточной галочки, неполной ветки и counts. Проверить текст «исключено» только для policy exclusions, отсутствие badge при нуле и корректный active/pending после конфликта.
- [ ] Проверить batch upload: heavy202 получает состояние `awaiting-attachments`, освобождает File reference, увеличивает uploaded count и не останавливает следующий файл; после refresh review загружается с backend. Состояние ожидающего не входит в retry failed uploads.
- [ ] Из `frontend/` выполнить `node --test test/processingScope.test.mjs test/processingScopeFlow.test.mjs test/documentUploadBatch.test.mjs test/documentUploadState.test.mjs`; получить FAIL новых сценариев.
- [ ] Реализовать интерфейс §11, переиспользовать дерево/источники. Нельзя отключать root; кнопка confirm блокируется только во время запроса, не из-за мягкого порога. Для busy existing candidate доступно чтение состава без редактирования.
- [ ] Обработать 409 scope_changed/stale/similar, потерянный ответ GET-refresh и сохранение draft; после ответа сервера не перетирать новый review старым запросом. Внести status filter «Ожидает выбора вложений» и pending action для готовых документов.
- [ ] Повторить node-тесты, затем `node --test` и `node node_modules/eslint/bin/eslint.js src/components/DocumentProcessingScopeDialog.jsx src/components/ProcessingScopeBadge.jsx src/lib/processingScope.mjs`; ожидается PASS/0 errors. Проверить существующие uploadReview/i18n/sourceTree тесты.

**Checkpoint:** A18 и пользовательский flow; браузерная сквозная приёмка T10 ещё обязательна.

### T9. Калибровка порогов и ресурсные ограничения

**Files — create:** `backend/scripts/inspect_attachment_scope.py`, `backend/test_scripts/probe_attachment_scope.py`, `backend/tests/test_attachment_scope_probe.py`.

**Files — update after measurements:** оба документа ТЗ/плана при изменении чисел; `docs/ARCHIVE_ATTACHMENTS.md`.

**Interfaces:**

- `inspect_attachment_scope.py --file <path> --output <json>` — read-only относительно исходника, БД и корпуса; только supervised inventory/recommendation, без LLM и публикации.
- `probe_attachment_scope.py --fixtures <directory> --output <json>` — изолированные результаты по форматам, bytes/time/RSS и calls counters. Customer filenames/content не коммитятся в fixtures/reports.

- [ ] Написать contract test: CLI не импортирует pipeline для запуска jobs, пишет только явно указанный отчёт, не меняет SHA оригинала и не создаёт document/table rows или Qdrant points.
- [ ] Из `backend/` выполнить `.venv\Scripts\python.exe -m pytest tests/test_attachment_scope_probe.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-t9`; сначала FAIL, затем реализовать и получить PASS.
- [ ] Идентифицировать фактический пользовательский документ по текущим данным, а не по предположению из числа 2909. Выполнить inspect на доступном исходнике; получить дерево «source → строки/ячейки/байты → рекомендация» и отдельно показать вклад вложенных писем/архивов. Не отменять и не перезапускать его текущую генерацию в рамках inspect.
- [ ] Проверить набор: без вложений, лёгкие 1/5/20 файлов, 500+ строк, широкая таблица, форматированный пустой хвост, ZIP/solid7z/RAR, неизвестный/encrypted и depth6/7. Зафиксировать совпадение включённого контента и стабильность рекомендаций.
- [ ] Выполнить 10 baseline/10 after проходов каждого лёгкого синтетического fixture после одного прогрева в одной среде; принять добавленное p95 время preflight <=1 секунда на файл. Для тяжёлых доказать завершение worker за 15 секунд плюс не более 2 секунд teardown, RAM не выше configured parser limit и отсутствие оставшихся helper. Превышение фиксируется как failed gate, не исправляется переименованием лимита.
- [ ] Если пороги дают очевидные ложные массовые отключения или пропуск пользовательского тяжёлого Excel, записать причину и пересмотреть числа синхронно в spec/config/tests. Не объявлять пороги калиброванными по одному примеру или только file size.

**Checkpoint:** A20 и измерения производительности. Если реальный исходник недоступен, сохранить синтетический результат и отдельно оставить A20 непроверенным; не выдавать его за целевую приёмку.

### T10. Сквозная приёмка и подготовка внедрения

**Files — create:** `backend/tests/test_processing_scope_end_to_end.py`, `docs/superpowers/reports/2026-10-17-hybrid-attachment-processing-acceptance.md` (отчёт создаётся при исполнении, не заранее с зелёными статусами).

**Files — modify:** `docs/ARCHIVE_ATTACHMENTS.md`, `docs/PRODUCTION_DEPLOYMENT.md`, `docs/DEVELOPMENT_CHECKS.md`, `SECURITY.md`, `THIRD_PARTY_NOTICES.md`.

- [ ] Написать E2E fixture: root + письмо с полезным телом + архив с тяжёлым Excel и лёгким DOCX; для ZIP/7z/RAR параметризовать один сценарий. Проверить сначала ожидание, затем selected parse, SQL+Qdrant, dense/BM25/hybrid sources, новую генерацию, cancel/failure и reindex. Имена/контент полностью синтетические.
- [ ] Добавить failure-injection кейсы на publication race, новый child после expand, hard limit/unsupported backend и миграцию с legacy данными. Каждый A01–A24 из ТЗ должен иметь ссылку на конкретный тест/ручное evidence в отчёте.
- [ ] Выполнить targeted E2E в изолированной БД/коллекции: `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_end_to_end.py -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-e2e` из `backend/`. Ожидается PASS всех трёх форматов; no-skip для archive runtime.
- [ ] Выполнить полный backend suite из `backend/`: `.venv\Scripts\python.exe -m pytest tests/ -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-full-backend`; полный doc-parser suite из `doc-parser/`: `..\backend\.venv\Scripts\python.exe -m pytest tests/ -q --basetemp=../tests/tmp/pytest-runs/attachment-scope-full-parser`. Указать отдельно сервисозависимые проверки и точную причину каждого skip.
- [ ] Выполнить из `frontend/` `node --test`, `node node_modules/eslint/bin/eslint.js .`, `node node_modules/next/dist/bin/next build`; Ruff backend/doc-parser по проектной конфигурации. Перед разрешённым commit из корня — `node scripts/check-project.mjs`, без обхода hooks и audit/network ошибок.
- [ ] Проверить фактический UI в браузере: одиночная и пакетная загрузка, закрытие диалога/refresh, выбор внутри письма и архива, обе локали, reader/editor, keyboard/focus, ширина 360 px (`scrollWidth === innerWidth`), active/pending badges и доступ к оригиналу. Записать версию работающего backend/frontend; build alone не является браузерной приёмкой.
- [ ] Подготовить migration runbook: backup, фактический Alembic head/schema drift, upgrade на копии PostgreSQL/SQLite, flag off, smoke, включение гибридного допуска. Поведение rollback flag и совместимой версии описать отдельно; не выполнять рабочую миграцию без команды на внедрение.
- [ ] Сохранить результаты: SHA кода, версии archive adapters/helper и их hashes, тестовые команды/exit codes, ресурсные измерения, A01–A24 matrix, ограничения и unrun checks. Только после этого предлагать включение флага на целевой среде.

**Checkpoint:** Локальная реализация готова при зелёных тестах; целевое внедрение и принятие текущего пользовательского документа имеют отдельные явно записанные статусы.

## Оставшиеся работы после ревью

Пункты ниже открыты. Для каждого исправления сначала воспроизвести дефект отдельным meaningful regression, затем внести минимальное изменение и выполнить связанный набор проверок. Не отмечать задачу закрытой только по изменённому ТЗ.

### T11. Не терять выбор и свойства вложений

**Modify:** `frontend/src/components/DocumentProcessingScopeDialog.jsx`, `frontend/test/processingScopeFlow.test.mjs`, `backend/app/api/document_processing_scope.py`, `backend/tests/test_processing_scope_api.py`, `backend/tests/test_processing_scope_downloads.py`, `doc-parser/src/docparser/attachment_inventory.py`, `doc-parser/tests/test_attachment_inventory.py`, `doc-parser/tests/test_unsupported_attachments.py`.

- [ ] R1: интерактивно включить ранее исключённый Excel, в том числе кнопкой preview; раскрыть unknown-соседа. Assert: локальный выбор сохранён, новый тяжёлый потомок выключен, generation не стартовала. Проверить 409 на сохранении и expansion, потерю ответа и повтор.
- [ ] Перед `createProcessingScopeReview(...expand_source_keys)` сравнить локальный effective selection с серверным; если изменён — вызвать существующий `saveProcessingScopeReview` с CAS, затем использовать возвращённые review ID/revision. При ошибке не вызывать expansion и не очищать локальные решения. Общую последовательность использовать без дублирования submit-логики.
- [ ] R3: передавать исходное `node.display_name` при branch вместо `document.filename`; проверить unknown→expand для XLSX и архива, стабильные key/parent/locator, русские и одинаковые имена; GET download должен иметь правильный Content-Disposition и прежний fingerprint.
- [ ] R4: определять `supported` через явный набор разрешённых parser/container форматов с учётом отключённых возможностей; распознавание PPTX не является поддержкой. Проверить EML/DOCX/ZIP→PPTX: unsupported counts/reason, отсутствие selected parse и возможность original/container download.
- [ ] Из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_api.py tests/test_processing_scope_downloads.py -q --basetemp=../tests/tmp/pytest-runs/attachment-review-t11`. Из `doc-parser/`: `..\backend\.venv\Scripts\python.exe -m pytest tests/test_attachment_inventory.py tests/test_unsupported_attachments.py -q --basetemp=../tests/tmp/pytest-runs/attachment-review-t11-parser`. Из `frontend/`: `node --test test/processingScopeFlow.test.mjs test/processingScope.test.mjs test/attachmentPreview.test.mjs`.

**Checkpoint:** A28–A30; UI тест выполняет callbacks/переходы, не ограничивается SSR или поиском строк в исходнике.

### T12. Убрать лишние 5 секунд запуска worker

**Modify:** `backend/app/diagnostic_entrypoint.py`, `backend/tests/test_diagnostics_lifecycle.py`, `backend/tests/test_attachment_preview.py`; при необходимости `backend/app/services/parser_supervisor.py`, `backend/tests/test_parser_supervisor.py`. **Create:** `backend/tests/test_attachment_preview_bootstrap.py` для изолированного subprocess regression без реального корпуса/LLM.

- [ ] Зафиксировать текущую цепочку `diagnostic_entrypoint → diagnostics.runtime → health → LiteLLM/Qdrant`; subprocess, запущенный как штатный Windows entrypoint и создающий spawn worker, должен воспроизвести стоимость импорта. В функциональном тесте проверять отсутствие тяжёлых ненужных модулей в child; абсолютные latency thresholds оставить измерительному gate, не flaky unit test.
- [ ] Перенести импорт diagnostics runtime внутрь `main()` либо сделать эквивалентный лёгкий bootstrap. Recorder по-прежнему устанавливается до `app.main`, ошибки запуска записываются, shutdown останавливает runtime. Не удалять диагностику и не менять backend worker count.
- [ ] Проверить, что spawn по-прежнему устанавливает OS limits до чтения недоверенного XLSX, использует общий semaphore и корректно освобождает slot при timeout/ошибке. Не обходить supervisor ради скорости.
- [ ] Из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_attachment_preview_bootstrap.py tests/test_diagnostics_lifecycle.py tests/test_parser_supervisor.py tests/test_attachment_preview.py -q --basetemp=../tests/tmp/pytest-runs/attachment-review-t12`.
- [ ] Повторить измерения §11.1 ТЗ на штатном entrypoint: малый файл и два листа большого, по 20 HTTP samples после одного прогрева, отдельный cold start, 10 UI действий. Записать p50/p95/max, bootstrap и XLSX timing, hash проверенного кода/файла без содержимого клиента. Критерии p95 HTTP ≤1,5 с, UI ≤2 с — новые непроверенные цели; старые T9 gates не повышать. Изолированный облегчённый worker ~0,5 с — гипотеза достижимости, не итоговый результат.

**Checkpoint:** A27 и diagnostics regressions. Для применения исправления нужен только контролируемый restart backend после проверки отсутствия активной обработки, новая миграция/регенерация не требуется.

### T13. Довести контракт и жизненный цикл предпросмотра

**Modify:** `frontend/src/components/AttachmentPreviewDialog.jsx`, `frontend/src/components/DocumentProcessingScopeDialog.jsx`, `frontend/src/lib/api.js`, `frontend/test/attachmentPreview.test.mjs`, `frontend/test/processingScopeFlow.test.mjs`, `backend/tests/test_attachment_preview.py`, RU/EN словари и backend UI dictionary. При необходимости `doc-parser/src/docparser/xlsx_preview.py`, preview endpoint и supervisor.

- [ ] R5: добавить постоянное RU/EN предупреждение о сырых значениях, датах/долях/ведущих нулях/boolean и о cached формулах; оно не зависит от `limited`. Не расширять задачу до воспроизведения оформления Excel. Fixtures: даты в книгах 1900/1904, проценты, zero-padded код, boolean, формула с/без cache, HTML в строке.
- [ ] Проверить границы: 30 непустых строк, A–T, 500 символов, 256 листов, XML budgets и shared strings после исчерпания worksheet budget; пустой лист и данные только за T различаются. Показанное значение остаётся React text, формулы/ссылки не исполняются.
- [ ] Организовать не более одного незавершённого server preview на диалог: пока запрос выполняется, сохранять последний выбранный лист и объединять промежуточные переключения. Не отменять fetch при каждом выборе с немедленным запуском нового worker. При закрытии прекращать отправку; активный worker завершается под deadline либо подтверждённо отменяется. Повторное открытие до завершения прежнего запроса не создаёт дубль для того же original/листа.
- [ ] Интерактивные тесты с управляемыми задержками: лист 1→2→3, ответ первого приходит поздно, закрытие/reopen, retry после 429/422, смена review, 409 с возвратом и обновлением состава без потери dirty selection. Проверить число запросов, последний показанный sheet, отсутствие stale таблицы, focus return и reader без кнопки изменения.
- [ ] Backend проверки A25/A26: included/excluded внутри EML/MSG и каждого ZIP/7z/RAR, no auth, чужой key/review, fingerprint mismatch, файл исчез/заменён, invalid sheet, resource/isolation error. Assert: review revision/hash, active/candidate и счётчики не меняются; ни LLM, ни embedding/index не вызваны. Не отключать изоляцию для live проверки.
- [ ] Из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_attachment_preview.py tests/test_attachment_preflight_supervisor.py tests/test_parser_supervisor.py -q --basetemp=../tests/tmp/pytest-runs/attachment-review-t13`. Из `frontend/`: `node --test test/attachmentPreview.test.mjs test/processingScopeFlow.test.mjs`. Дополнить реальным браузером RU/EN, 360 px, клавиатура, медленный ответ и занятые slots.

**Checkpoint:** A25/A26/A31. Существующий тест только `AttachmentPreviewTable` и успешный production build не закрывают жизненный цикл диалога.

Дополнение после пользовательской проверки: устранён скачок высоты preview при смене
листа. Постоянный viewport50vh сохраняется при loading/error; прежняя таблица скрыта.
Новая регрессия RED→GREEN; focused16 passed, полный frontend524 passed/1 прежний skip,
targeted ESLint0 errors/warnings. В реальном SSO UI геометрия и фокус неизменны на пяти
листах и при360px; evidence добавлено в отчёт приёмки. Production build после этого
отдельного JSX/CSS исправления не повторялась.

### T15. Сохранить оригиналы и рекомендации между поколениями

**Modify:** `backend/app/services/processing_scope.py`, `backend/app/services/processing_admission.py`, `backend/app/services/pipeline.py`, `backend/app/services/generation_files.py`, `backend/app/api/document_processing_scope.py`, `backend/tests/test_processing_scope.py`, `backend/tests/test_processing_scope_api.py`, `backend/tests/test_processing_scope_downloads.py`, `backend/tests/test_generation_processing_scope.py`, `backend/tests/test_processing_scope_end_to_end.py`; API/UI summary по необходимости. Не добавлять новые SQL-поля без подтверждённой необходимости.

**Фактическое размещение:** копирование originals вынесено в новый `backend/app/services/processing_scope_originals.py`, вызываемый из pipeline; это сохраняет generation_files как общий слой путей/блокировок. Recommendations добавлены совместимым optional полем `AttachmentInventory`, новые SQL-колонки не добавлялись.

- [ ] Воспроизвести A33 на синтетических ZIP/7z/RAR: отдельно извлечённый Excel доступен в pending; выключить его, опубликовать selected generation, заменить review и выполнить cleanup. Проверять доступ и SHA из active generation, отсутствие содержимого в SQL/Qdrant, изоляцию чужого поколения. Отдельно never-extracted/container_only и cancel/failure кандидата.
- [ ] Переносить уже существующий review original в candidate под проверенным app-generated путём с fingerprint и mapping по source_key; не распаковывать excluded ветки повторно и не добавлять их в parser input. Cleanup review не удаляет опубликованную копию; candidate cleanup не трогает active.
- [ ] Зафиксировать automatic recommendations отдельно от decisions в review inventory/snapshot JSON; возвращать `recommended_excluded_count`. После ручного Save сохраняются `heavy/aggregate_budget` и исходные рекомендации, но effective selection отражает пользователя. Для старого JSON без рекомендаций — явное отсутствие, без пересчёта по сегодняшней конфигурации и изменения selection hash.
- [ ] Сверить DELETE и JSON с уточнённым §8–9: новый документ остаётся draft с прежними решениями, revision растёт, consent сброшен; у готового отменяется pending при прежнем active; validating/submitted дают конфликт. Full inventory хранится в snapshot, отдельный digest не требуется; duplicates передаются в ответе similarity, durable token привязан к selection hash/expiry.
- [ ] Из `backend/`: `.venv\Scripts\python.exe -m pytest tests/test_processing_scope.py tests/test_processing_scope_api.py tests/test_processing_scope_downloads.py tests/test_generation_processing_scope.py tests/test_processing_scope_end_to_end.py -q --basetemp=../tests/tmp/pytest-runs/attachment-review-t15`. Настроить отдельную тестовую Qdrant collection для end-to-end, перечислить все skips; pending-only download не закрывает A33.

**Checkpoint:** A33–A35 и сохранение A03/A19. Если выбран другой original lifecycle, это отдельное изменение требования с явным решением пользователя, а не молчаливое понижение проверки до контейнера.

### T14. Изолировать тестовую среду и принять текущую версию

**Modify:** `backend/tests/conftest.py`, fixtures в `backend/tests/test_audit.py` и scope/preview suites по результату воспроизведения, `docs/acceptance/2026-10-17-hybrid-attachments.md`, `docs/acceptance/2026-10-17-hybrid-attachments-rollout.md`, `docs/ARCHIVE_ATTACHMENTS.md`. Не менять рабочую `.env` для починки тестов.

- [ ] Воспроизвести влияние root `.env` с включённым flag на audit upload. Задать явные settings и tmp uploads в fixtures; ordinary tests используют legacy mode, feature tests явно true. Проверить обе ветки независимо от runtime flag; запретить обращения к рабочей БД/коллекции.
- [ ] После T11–T13/T15 выполнить полный backend suite с `--ignore=tests/tmp` по команде выше, полный doc-parser suite, frontend `node --test`, требуемые lint/dictionaries и webpack build. Сохранить команды, exit codes, число passes/skips и причины. Старый Linux 4288-pass не заменяет этот запуск; неначатые/прерванные проверки остаются незакрытыми.
- [ ] Записать A25–A35 → конкретные тесты/ручные evidence и регрессию A01–A24 в отчёте. Добавить manifest/hash изменённых файлов, поскольку HEAD без незакоммиченного patch не идентифицирует работающую версию.
- [ ] В реальном SSO UI проверить preview, выбор и его сохранение на пользовательском документе без запуска новой генерации. Проверить review/active/candidate до и после, новых чанков/концептов быть не должно. Отдельный этап после подтверждения состава пользователем: selected publication и поиск по исключённому источнику; до этого не утверждать, что старые 2909 чанков уже заменены.
- [ ] Сверить scope JSON, миграцию063 и включённый flag с rollout report read-only. Не повторять stamp/upgrade, не менять corpus и не выполнять commit/push. Актуализировать статусы плана только по результатам проверок.

**Checkpoint:** текущий checkout принят по согласованной матрице; отказанные/непройденные gates и целевая перегенерация перечислены отдельно.

## Новая доработка: изменение состава без пересчёта готовых источников

T16–T20/T22–T26 реализованы; точные результаты RED/GREEN и реальные имена проверок записаны в ledger и отчёте приёмки. Ниже сохранён исходный порядок работ; неподтверждённые целевые проверки нельзя считать выполненными по одному наличию кода. T21 остаётся открытым до фиксации рабочего применения и отдельно — производительности A50. Промежуточный exclude-only UI нельзя объявлять выполнением полного ТЗ §15.

### T16. Анализ состава, идентичность и workset

**Files:** создать `backend/app/services/processing_scope_change.py`, `backend/tests/test_processing_scope_change.py`; изменить `backend/app/models/processing_scope.py`, `backend/app/services/processing_scope.py`.

**Interfaces:** в `processing_scope_change.py` определить `ScopeBaseSnapshot` (active generation, canonical rows, tables/projections, file hashes, cache descriptors), `ScopeChangePlan` (поля preview §14.5 и workset §15.1), `load_scope_base(doc_id: str) -> ScopeBaseSnapshot`, `analyze_scope_change(base: ScopeBaseSnapshot, selection: dict) -> ScopeChangePlan`. Loader читает согласованный снимок под `lock_generation_read`; analyzer не вызывает providers и не пишет candidate. Финальные режимы: `fast_exclude|reuse_only|incremental_generate|noop|blocked`. TTL/receipt выдаёт T19, pure analyzer их не придумывает.

- [ ] Написать `test_occurrence_identity_not_filename_or_sha`, `test_null_and_mixed_provenance_block`, `test_noop_and_mixed_change_modes`: одинаковые bytes в разных родителях не схлопываются; unknown/mixed не принимается за root; добавление никогда не вызывает full regeneration.
- [ ] Написать `test_stage_workset_complete_zero_partial_and_missing_vectors`: completed zero-concepts → reuse; partial stage → недостающая работа, не complete; retained active rows не попадают в workset. До T22 подставлять синтетические cache descriptors.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_change.py -q` из `backend/`, подтвердить падение именно нового контракта, затем реализовать source mapping/closure, diff и этапный анализ.
- [ ] Повторить тот же запуск: PASS; counts неизвестных новых concepts — null. Preview не обещает «без LLM», если workset содержит генерацию. Проверить перестановку siblings, самостоятельный дубликат и legacy оригиналы по §14.2.

**Checkpoint:** A38/A39/A43; pure детерминированный план изменения с причинами отказа, без изменения корпуса.

### T17. Candidate, сохранение результата и отделение архива

**Files:** создать `backend/app/services/processing_scope_candidate.py`, `backend/tests/test_processing_scope_candidate.py`; изменить `backend/app/services/generation_artifacts.py`, `backend/app/services/table_store.py`, `backend/app/services/chunk_projection_store.py`, `backend/app/services/chunk_projection_publication.py`, `backend/app/services/generation_publication.py`. Cache writer/reader T22 подключается по интерфейсу, сначала тестовый in-memory adapter.

**Interfaces:** `build_scope_candidate(base: ScopeBaseSnapshot, plan: ScopeChangePlan, generation_id: str, completed_sources: dict) -> dict` возвращает prepared publication payload; `completed_sources` содержит проверенные restored/generated результаты, при exclude-only пуст. В `table_store.py` добавить `clone_selected_generation_tables(session, doc_id, base_id, generation_id, bundle, source_ids: set[str])`, не менять семантику старого `clone_generation_tables`. Candidate использует `source-cache/<cache_id>/` отдельно от активного bundle, schema_version=1.

- [ ] Написать `test_exclusion_preserves_content_ids_and_provenance`, `test_nested_closure_preserves_parent_mail`, `test_archive_is_not_active_manifest`: сравнивать hashes/slug/indices/source spans/generated_at/model/prompt, а не только counts. Концепты исключения сохраняются для reuse в cache, но отсутствуют в активном concept store.
- [ ] Написать `test_filtered_tables_and_fragment_refs`, `test_quality_relations_signatures_and_gapped_indices`: скрыть только resolved внутренние edges с сохранением suspended_relations для восстановления; исходный текст не править; неизвестные warnings сохранить; signatures строить только по active text. Архив не требует сохранять активные таблицы исключённого источника.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_candidate.py -q`; после ожидаемого RED реализовать фильтрацию, архивный manifest, независимые пути файлов и согласованную publication payload. Не переиспользовать `_index_candidate` из canonical repair, который вызывает embedder.
- [ ] Повторить тесты до PASS. Проверить точные множества активных/архивных identities и отсутствие зависимости от retired каталога после cleanup. Подготовка не меняет active generation.

**Checkpoint:** A36/A40/A41/A48; стабильно сохранённый контент, все производные данные согласованы.

### T18. Готовые векторы, атомарная публикация и обслуживание

**Files:** создать `backend/tests/test_processing_scope_vectors.py`; изменить `backend/app/services/vector_store.py`, `backend/app/services/generation_publication.py`, `backend/app/services/generation_artifacts.py`, `backend/app/services/canonical_reindex.py`, `backend/app/services/chunk_projection_maintenance.py`, `backend/app/services/export_okf.py`, `backend/app/services/chunk_store.py`; расширить `backend/tests/test_processing_scope_maintenance.py`.

**Interfaces:** в `vector_store.py` добавить `copy_generation_points(doc_id: str, base_generation_id: str, generation_id: str, identity_map: dict) -> int` — streaming scroll/retrieve active vectors, точная проверка ownership/content identity, upsert≤256 с новым generation/group identity. Добавить `scope_base_digest(base: ScopeBaseSnapshot) -> str` в модуль T16: канонические записи + versioned tables/projections/cache manifests. T22 предоставляет cache vectors через тот же проверяемый payload contract.

- [ ] Написать `test_copy_preserves_dense_and_sparse`, `test_missing_foreign_or_partial_vectors_prevent_publish`, `test_base_changed_during_copy`: provider spies запрещают embedder; точный set identities; ручные edits дают409 без потери старой публикации.
- [ ] Написать `test_archive_never_returns_through_reindex_or_export`, `test_post_publish_search_excludes_canary`: выполнить reindex/backfill/export после publish; проверить chunk и concept ветви, table projections, hydration/cache. Canary встречается только в исключённом источнике.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_vectors.py tests/test_processing_scope_maintenance.py -q`, затем реализовать reuse и validation. Для настоящего Qdrant использовать отдельную коллекцию с тестовым doc_id; не менять рабочий индекс.
- [ ] Повторить до PASS. В `publish_prepared_document` сохранить транзакционный переключатель, расширить проверку base digest и active/archive manifests; publication receipt T19 фиксировать в той же транзакции. Cleanup после commit; сбой cleanup не превращает успешный publish в failed.

**Checkpoint:** A42/A46; SQL/файлы/индекс имеют один активный состав, векторы не пересчитаны.

### T19. Durable API, миграция, допуск, отмена и recovery

**Files:** создать `backend/app/services/processing_scope_jobs.py`, `backend/app/services/update_workers.py`, `backend/tests/test_processing_scope_jobs.py`, `backend/tests/test_processing_scope_change_migration.py`; новая миграция `backend/alembic/versions/064b1c2d3e4f_document_scope_changes.py` после проверки head. Изменить `backend/app/db/models.py`, `backend/app/api/document_processing_scope.py`, `backend/app/services/processing_admission.py`, `backend/app/services/pipeline.py`, `backend/app/services/document_update.py`, `backend/app/services/generation_cleanup.py`, `backend/app/services/generation_publication.py`.

**Interfaces:** `create_change_preview(doc_id: str, review_id: str, revision: int, expected_active_generation_id: str, actor_id: str) -> dict`; `apply_scope_change(doc_id: str, plan_id: str, request_id: str, expected_active_generation_id: str, actor_id: str, allowed_modes: set[str]) -> dict`; `get_scope_change(doc_id: str, plan_id: str, actor_id: str) -> dict`. Все в `processing_scope_jobs.py`. API строго §14.5/§15.4. В `update_workers.py` определить общий `register_update_worker`, `request_update_cancel`, `finish_update_worker`, используемый pipeline и fast executor; durable ownership/lease остаётся в БД, локальный Future не является доказательством отсутствия владельца.

- [ ] Написать `test_preview_has_no_candidate`, `test_request_replay_after_cleanup`, `test_cross_endpoint_request_conflict`, `test_busy_worker_rejects_before_reservation`, `test_stale_review_base_and_digest`: по одному receipt/candidate, TTL15мин,409/429 и ACL без fallback.
- [ ] Написать `test_cancel_does_not_cleanup_live_writer`, `test_late_worker_cannot_write_after_recovery`, `test_publish_cancel_race`, `test_live_foreign_lease_survives_restart`: проверить фазы admitted/copying/ready и failure после commit. Document lock охватывает отдельную ограниченную запись файлов/SQL/Qdrant, не весь job. Истёкшая lease сама по себе не разрешает concurrent cleanup.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_jobs.py tests/test_processing_scope_change_migration.py -q`; реализовать receipt-таблицу §14.6, owner token/lease60с/renewal20с, состояния с queued/processing для delta, nullable order-колонки и next_chunk_index из §15.3. Тип ORM концепта — `OkfConcept`; действующую063 не менять.
- [ ] Проверить upgrade на отдельной SQLite и отдельной PG БД: старые records читаются; unique request, doc cascade purge, receipt после generation cleanup; order defaults не требуют regenerating corpus. Повтор запуска миграций не меняет данные. Рабочую БД этой задачей не мигрировать.
- [x] Реализовать executor1/process без очереди для fast/reuse, DB claim, shared cancel и startup reconciliation. `queued` delta имеет отдельную owner lease60с/heartbeat20с до старта worker; отсутствие worker не разрешает захват/cleanup. Wiring delta runner выполняет T23. Смена plan revision не меняет уже принятую работу; publish проверяет актуальность guards.
- [ ] Повторить оба test-модуля до PASS. Результат publish и receipt записываются в одной SQL транзакции; draft после отмены восстанавливается только для принадлежащего операции review, с новой revision.

**Checkpoint:** A44/A45/A49; новая схема и lifecycle проверены изолированно, автоматического перезапуска генерации нет.

### T20. Доступность кнопки и явный выбор действия

Уточнение18.10 выполнено: один компонент входа под названием документа, меняющий вид между интерактивным бейджем и кнопкой. Второй экземпляр из doc-actions удалён; обычный выбор и legacy inspection сохраняются без бейджа, открытый редактор переживает смену вида.7 новых component scenarios, полный frontend555passed/1skip; рабочий список проверен.

**Files:** изменить `backend/app/models/schemas.py`, `backend/app/services/registry.py`, `backend/app/services/processing_scope.py`, `frontend/src/lib/api.js`, `frontend/src/lib/processingScope.mjs`, `frontend/src/components/DocumentProcessingScopeDialog.jsx`, `frontend/src/components/DocumentProcessingScopeButton.jsx`, `frontend/src/components/DocumentList.jsx`, `frontend/src/components/ProcessingScopeBadge.jsx`, `frontend/src/i18n/locales/ru.js`, `frontend/src/i18n/locales/en.js`. Тесты: `backend/tests/test_processing_scope_summary.py`, `frontend/test/processingScopeFlow.test.mjs`, новый `frontend/test/processingScopeChange.test.mjs`.

**Interfaces:** DocumentOut availability/update kind/chunk_counts и scope capabilities ровно §14.7/§15.4. В `processingScope.mjs` добавить `scopeChangeAction(preview, capabilities)` с результатом `{action, endpoint, disabled, reason}`: noop не запускает работу, fast_exclude→apply-exclusions, reuse/delta→apply-changes только при соответствующей capability. Недоступный режим не перенаправляется на confirm.

- [ ] Написать проверки dirty PUT failure/409, preview stale response, повтор apply после потерянного ответа; локальные галочки сохраняются. Проверить отсутствие confirm/regenerate во всех fast/reuse/delta ветвях.
- [ ] Написать проверки root-only DOCX/PDF/XLSX, письмо без вложений, inline image, unsupported-only, unknown legacy, excluded-only и reader; batched summary не вызывает parser/N+1. Кнопка состава доступна только когда есть состав для просмотра/изменения; unknown имеет отдельное действие проверки в деталях.
- [ ] Запустить из `frontend/`: `node --test test/processingScopeChange.test.mjs test/processingScopeFlow.test.mjs`. После RED реализовать action preview, активные/архивные counts, этапы только нужной работы, отдельную полную регенерацию и RU/EN пояснения. Все места вызова scope button проверить через `rg`, включая viewer.
- [ ] Повторить до PASS; backend `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_summary.py -q`. Проверить 360px, клавиатуру, reader, старый backend без capability; staged/excluded badge не выдаёт pending за active.

**Checkpoint:** A47/A48; пользователь видит реальную стоимость действия, пустой кнопки нет.

### T22. Cache завершённых этапов и повторные решения

**Files:** создать `backend/app/services/processing_scope_cache.py`, `backend/tests/test_processing_scope_cache.py`; изменить модули T16/T17, `backend/app/services/generation_artifacts.py`, `backend/app/services/generation_cleanup.py`, `backend/app/services/document_update.py`.

**Interfaces:** `inspect_source_cache(doc_id: str, source: dict) -> dict` возвращает verified stage descriptors для workset; `save_completed_source_stage(doc_id: str, source: dict, stage: str, artifact: dict) -> dict` атомарно пишет durable staging cache; `copy_source_cache(doc_id: str, cache_id: str, generation_id: str) -> dict` переносит проверенные artifacts в candidate. Структура/identity/completion/compatibility — §15.2, никаких secret fields. Активные данные также являются источником reuse; обращаться к архиву только когда нужного stage нет в активном наборе.

- [ ] Написать `test_toggle_five_times_reuses_every_stage`, `test_completed_zero_concepts_is_cache_hit`, `test_cancel_preserves_completed_stages`, `test_partial_manifest_is_not_complete`: сохранить прежние chunks/concepts/vectors/IDs, не вызывать providers повторно, не создавать дубли.
- [ ] Написать `test_fingerprint_and_embedding_space_invalidate_only_dependent_stages`, `test_cache_survives_generation_cleanup_and_review_replacement`, `test_purge_removes_stage_and_published_cache`, `test_disk_failure_preserves_active`: без SHA-only reuse между документами и без молчаливого TTL.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_cache.py -q`; реализовать потоковые JSONL, checksums/round-trip vectors, stage completion, atomic writes и copy. При исключении захватить из активного поколения все завершённые chunks/concepts/tables и их готовые dense/sparse vectors до retired cleanup; одного копирования retained vectors недостаточно. Сохранить прежний source provenance, distinct occurrence keys, поддерживаемые schema. Повреждённые staged данные не считать готовыми.
- [ ] Повторить до PASS; подключить cache adapter T17 и analyzer T16. Повторить их тесты. Проверить, что archive не входит в active content, а failure/cancel cleanup не удаляет готовый staging cache.

**Checkpoint:** A52–A54/A58; многократное изменение выбора не повторяет выполненные этапы.

### T23. Delta execution и resume только недостающей работы

**Files:** создать `backend/app/services/processing_scope_delta.py`, `backend/tests/test_processing_scope_delta.py`; изменить `backend/app/services/pipeline.py`, `backend/app/services/processing_admission.py`, `backend/app/services/processing_scope_jobs.py`, `backend/app/models/processing_scope.py`. Перед изменением parser boundary использовать существующий supervisor из T4, не добавлять обход изоляции.

**Interfaces:** `run_scope_delta(doc_id: str, plan_id: str, workset: dict, owner_token: str) -> dict` в новом модуле; возвращает `completed_sources` T17. `resume_scope_delta(doc_id: str, plan_id: str, owner_token: str) -> dict` читает immutable workset и verified stage cache T22. Pipeline admission передаёт operation kind; общий `regenerate()` не вызывается.

- [ ] Написать `test_only_added_sources_call_providers`, `test_cached_chunks_skip_parser`, `test_missing_vectors_skip_llm`, `test_nested_attachment_does_not_regenerate_parent`, `test_mixed_change_publishes_once`: spies проверяют точные source keys/chunk IDs каждого вызова и отсутствие вызовов для retained sources.
- [ ] Написать `test_resume_uses_completed_stages`, `test_review_edits_do_not_mutate_queued_workset`, `test_cancel_in_parser_llm_index_keeps_old_publication`: completed stage сохраняется в T22, неполный не включается; следующая подтверждённая операция использует всё завершённое.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_delta.py -q`; затем реализовать selective parser policy, ограниченный workset для chunk generation, embeddings только missing identities, durable checkpoint и admission через существующий лимит LLM. Если разбор контейнера обязателен для доступа, разрешить traversal/extraction без генерации уже готового тела/соседей.
- [ ] Собрать через T17 retained+restored+new, проверить через T18, опубликовать через T19. Повторить тесты до PASS. Fast/reuse не ждут LLM-slot; delta не обходит существующие лимиты провайдера. Результаты не публикуются частями.

**Checkpoint:** A51/A53/A56/A57; новый контент добавлен без повторной генерации прежнего.

### T24. Стабильные идентификаторы и правильный порядок

**Files:** создать `backend/app/services/processing_scope_order.py`, `backend/tests/test_processing_scope_order.py`; изменить `backend/app/services/chunk_store.py`, `backend/app/services/concept_store.py`, `backend/app/services/generation_publication.py`, `backend/app/services/export_okf.py`, `frontend/src/components/MarkdownViewer.jsx`, `backend/app/api/documents.py` (list_chunks/list_concepts и chunk navigation), `backend/app/models/schemas.py` (chunk/concept responses). Миграция полей принадлежит T19, второй конкурентной revision не создавать.

**Interfaces:** `reserve_chunk_indices(session, doc_id: str, count: int) -> range` использует `DocumentGenerationState.next_chunk_index`; `assign_scope_display_order(sources: list[dict], chunks: list[dict], concepts: list[dict]) -> None` меняет только display_order по §15.3. Оба в новом модуле. Stable local ordering хранится в cache, новая глобальная display_order вычисляется для каждого состава.

- [ ] Написать `test_insert_middle_source_preserves_existing_ids`, `test_restore_uses_old_indices`, `test_reserved_indices_not_reused_after_cancel`, `test_equal_names_and_nested_sibling_order`, `test_legacy_null_order_fallback`: проверить source tree order и сохранённый порядок концептов внутри chunks.
- [ ] Написать `test_old_link_never_opens_neighbor`, `test_viewer_export_and_toc_share_order`, `test_new_slug_collision_does_not_rename_existing`: исключённая ссылка показывает объяснение, новые chunks после max reserved, одинаковый title не является identity.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_order.py -q`; реализовать reservation отдельно от publish rollback, nullable order round-trip через stores/API/manifest. Найти через `rg` consumers `chunk_index`, `range(total_chunks)`, `enumerate` и offset-навигацию; перевести только порядок на display_order, не идентичность и не ranking поиска. Зафиксировать найденные затронутые пути в отчёте T21.
- [ ] Повторить тесты до PASS; проверить UI+экспорт на источниках A/B/C, исключить B, вернуть B, добавить D между соседями; ссылки A/C и исходные span anchors неизменны. Nullable columns не запускают миграционный пересчёт контента.

**Checkpoint:** A55/A59; сортировка отделена от идентификаторов и не ломает прежние ссылки.

### T21. Сквозная приёмка, ресурсные цели и поставка

**Продолжение производительности18.10:** фазовый профиль выявил ненужные YAML/Markdown при `okf_write_bundles=false` (19с render/48с reread), затем повторный MinHash (71.6млн вызовов), повторный экспорт таблиц и row-by-row SQL restore. Порядок: регрессии→соблюсти существующий DB-only mode→bounded cache подписи по точным bytes/параметрам→пакетный restore без изменения конфликтов→один portable export→полный affected regression/review→10 warm exclude+10 warm reuse без параллельных тестов. A50≤5с preview/≤30с admission→publish не менять; отдельно записать cold, полное время worker и peak RSS. Рабочий состав документа при приёмке не менять.

**Уточнение UX18.10 выполнено:** вход в draft называется «Редактировать вложения»; save зависит от draft diff, review/apply от published diff (baseline — active inventory), bulk от реального изменения. Noop disabled; saved draft/legacy analysis/initial processing/full regeneration сохраняют отдельные правила. RED→GREEN и review fix, полный frontend547pass/1skip, рабочий dev UI без изменения галочек проверен. ТЗ дополнено условиями действий, UI fix не меняет backend performance SHA.

**Files:** создать `backend/tests/test_processing_scope_change_end_to_end.py`; обновить `docs/acceptance/2026-10-17-hybrid-attachments.md`, `docs/acceptance/2026-10-17-hybrid-attachments-rollout.md` и этот план по фактическим результатам. При реализации критерии A36–A59 не заменять прежними PASS.

- [ ] Написать изолированный e2e: root105/1679, excluded2767/2767, siblings19/19+18/18; после apply active142/1716, archived2767, stored chunks2909, все retained hashes/IDs/provenance прежние. Затем включить Excel из cache без providers и повторить несколько переключений; добавить новый источник и проверить provider calls только по нему.
- [ ] Запустить `.venv\Scripts\python.exe -m pytest tests/test_processing_scope_change_end_to_end.py tests/test_processing_scope_change.py tests/test_processing_scope_candidate.py tests/test_processing_scope_vectors.py tests/test_processing_scope_jobs.py tests/test_processing_scope_cache.py tests/test_processing_scope_delta.py tests/test_processing_scope_order.py tests/test_processing_scope_change_migration.py -q --basetemp=../tests/tmp/pytest-runs/scope-change-acceptance` из `backend/`. Real-service проверки используют отдельную PG/коллекцию Qdrant и копию artifacts. Failures/platform skips записывать честно.
- [ ] Пройти A36–A59: ZIP/7z/RAR и письма, duplicates, NULL/mixed, стадии cancel/restart, cache/reindex/export, concurrent edits, роли, 360px, обе локали. Прогнать затронутые старые scope/generation tests, frontend `node --test test/processingScope.test.mjs test/processingScopeFlow.test.mjs test/processingScopeChange.test.mjs`, targeted ESLint и `node node_modules/next/dist/bin/next build --webpack`. Не запускать новую LLM-генерацию реального документа ради теста.
- [x] Измерить A50 на изолированной копии: один прогрев каждого режима +10 warm exclude/10 warm reuse, без параллельных тестов. PASS18.10: p95 preview4.15с/0.68с, admission→publish18.43с/29.08с, полное время22.40с/29.75с, peak953.20MiB, providers0; Qdrant upsert≤256, canonical SQL≤1000/derived restore≤256. Cold reuse36.57с отдельно, пороги не изменены. Дополнительный2turn BASE→restore→exclude проверил retained content/provenance/table_refs/tags/source bytes/payload/numeric vectors. Delta30с не обещаются. Evidence: `docs/acceptance/2026-10-18-scope-performance-metrics.json`.
- [ ] Подготовить rollout: backup, проверка фактического Alembic head/схемы, additive migration на тестовых SQLite/PG, согласованный restart без прерывания jobs, server capability раньше UI. Rollback отключает новые действия и сохраняет уже опубликованный состав/cache; старый reader не должен индексировать archive или терять display_order.
- [ ] На рабочем документе сначала показать review/preview и текущие поколения/counts. Применение — только после отдельной команды пользователя на этот результат. После него проверить SQL/Qdrant/UI/search/export и повторное включение на согласованном примере. Не выдавать локальный синтетический PASS за целевую приёмку.

**Checkpoint:** A36–A59 с evidence и явно отмеченными непроверенными пунктами; никаких commit/push без отдельной команды.

## Проверка покрытия ТЗ

| Требования | Задачи |
|---|---|
| Автоматический лёгкий путь и ожидание тяжёлого | T1, T4, T6, T8 |
| Тяжесть, сумма, unknown, пороги | T1, T9 |
| Письмо → архив → Excel; ZIP/7z/RAR | T2, T4, T8, T10 |
| Выбор до upload-preview, similarity и exact duplicate | T6 |
| Snapshot, staging, resume, atomic publication | T3, T5 |
| Active/pending data, counts, локализация и accessibility | T7, T8, T10 |
| Bulk/recovery/reindex/backfill/export/originals | T7 |
| Изоляция, права, audit, зависимости, новые лимиты глубины | T2, T3, T4, T6, T10 |
| Совместимость, миграция, rollback, приёмка примера | T3, T9, T10 |
| Сохранение dirty selection, branch identity, unsupported — A28–A30 | T11 |
| Предпросмотр, raw values, lifecycle/ошибки — A25/A26/A31 | T13 |
| Скорость настоящего entrypoint и сохранение изоляции — A27 | T12 |
| Originals после cleanup, рекомендации, DELETE/JSON — A33–A35 | T15 |
| Независимость от .env и полный текущий suite — A32 | T14 |
| Быстрое исключение, неизменность результатов, контрольный пример — A36/A37 | T16–T19, T21 |
| Legacy identity, unsafe provenance и выбор режима — A38/A39/A43 | T16 |
| Nested closure, tables/projections, архив и ссылки — A40/A41/A48 | T17, T22, T24 |
| Векторы и отсутствие возврата excluded в поиск — A42/A46 | T18, T21 |
| Idempotency, отмена/гонки/recovery, миграция — A44/A45/A49 | T19 |
| Доступность UI/права/RU/EN — A47 | T20 |
| Измеренные latency/RSS gates — A50 | T21 |
| Добавления, missing stages, mixed update — A51/A53/A56/A57 | T16, T19, T23 |
| Повторные решения, cache validity и исключение из активных путей — A52/A54/A58 | T22 |
| Display order, стабильные IDs, high-water/migration/purge — A55/A59 | T19, T22, T24 |

## Передача к реализации

Базовая функция T1–T10 внедрена локально; исправления T11–T15 выполнены. Новая миграция для этих исправлений не потребовалась. Предпросмотр и выбранная новая публикация — разные этапы приёмки.

Исторические результаты T11–T15 и ограничения полных прогонов записаны в отчёте приёмки. Полная генерация пользовательского документа отменена. После отдельного разрешения применены migration064 и fast_exclude, новый состав опубликован без LLM/embeddings. T25 блокирует другого редактора; T26 предупреждает на9мин и закрывает забытое окно на10мин. Актуальные доказательства находятся в ledger и отчёте приёмки. A50 остаётся открытым и не заменён единичным рабочим результатом28.87с; commit/push запрещены пользователем.
# Дополнение T25: один редактор состава на документ

Основание: уточнение пользователя об одновременном редактировании. Контракт — §16 ТЗ, A60–A64. Зависимости: T19 (durable admission/worker), T20 (UI), новая миграция 064. Не менять уже применённую 063.

1. RED: два пользователя, две вкладки, два параллельных acquire, TTL, поздний release/renew; истёкшая аренда не разрешает изменить текущий review.
2. Добавить `document_scope_edit_locks` с doc_id PK/FK cascade, owner_id/name, token_hash и expires_at в модели и ещё не применённую 064. Acquire/renew/release и каждая запись используют общий document write lock; TTL180s/heartbeat60s. Provider calls не держат SQL-транзакцию.
3. Проверять роль и владение во всех публичных изменениях review, создании/раскрытии inventory, preview и admission. После медленного parsing проверять повторно. Сохранить idempotent replay принятого request независимо от истечения edit lease.
4. UI: readonly второго редактора с именем владельца, предпросмотр доступен; токен отдельный для каждого открытия вкладки, heartbeat/освобождение, явное возобновление с актуальной revision после потери блокировки. Действующая операция блокирует редактирование независимо от edit/worker lease.
5. GREEN: API и component lifecycle, конкурентный SQL тест; SQLite Alembic/schema parity, отдельный PG concurrency и браузерная проверка двух вкладок перед рабочим rollout. Невыполненные PG/UI пункты не считать пройденными по SQLite unit tests.
6. Без commit/push, рабочей регенерации корпуса и изменения документа пользователя до соответствующего rollout gate.

## Дополнение T26: автоматическое закрытие забытого редактора

Основание: пользователь указал, что открытое окно могут забыть. Контракт §16.1, A65–A68.

1. RED: heartbeat без действий не держит аренду дольше600с; новый activity sequence продлевает, повтор прежнего sequence не продлевает. Независимый UI-clock: предупреждение540с, закрытие600с, активность/продолжение сбрасывают, задержанные callbacks и cleanup безопасны.
2. Дополнить ещё не применённую064 полями idle_expires_at/activity_sequence; renew проверяет оба срока под document lock и ограничивает transport lease idle deadline. Повтор acquire не сбрасывает действующую аренду.
3. Подключить таймер к обоим окнам: pointer/key/scroll capture, предупреждение с обратным отсчётом, явное продолжение. Autoclose освобождает edit lease и закрывает preview, не вызывает save/confirm/cancel-update. Отдельно проверить background throttling и закрытие во время запроса.
4. GREEN: сервер, API sequence validation, pure timers и компонентный lifecycle; RU/EN, lint/build, PostgreSQL063→064 и повторная проверка двух вкладок. Рабочий rollout и публикация пользовательского документа учитывать отдельно.

### Уточнение18.10: сначала просмотр, таймаут возвращает в просмотр

- [x] Первый вход показывает active inventory/decisions read-only, сохранённый draft открывается только явным Edit. Без draft создание вызывается только этим действием. Ошибка создания не запускает цикл повторов. Reset возвращает в исходный просмотр.
- [x] Убрать отдельную кнопку Reload, сохранить внутреннюю актуализацию Excel-preview с локальными выборами по стабильным ключам.
- [x] API timeout/HTTP408/504 возвращает в просмотр; следующий Edit сначала читает сервер и не возобновляет submitted/validating как draft. Не повторять save/apply/confirm автоматически; другие ошибки оставляют выбор для исправления.
- [x] Потеря lease возвращает в просмотр и блокирует изменения до явного reacquire. После замечания независимого review добавить идентификатор сессии: поздний save после потери heartbeat не меняет просмотр и не продолжает confirm/preview/expand. Инвалидация также при idle autoclose.
- [x] RED/GREEN: view-first, timeout сохранения/применения и повторное чтение actual revision, submitted read-only, отсутствие Reload, закрытие/повторное открытие, delayed-save/lease-loss для трёх продолжений. Проверка рабочего UI ограничена просмотром без изменения пользовательского выбора; точные итоги в acceptance. Новая миграция и backend restart для этих UI исправлений не требуются. Commit/push запрещены текущим указанием пользователя.
