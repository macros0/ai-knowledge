# Этап 8: план действий и критерии прохождения

> **For agentic workers:** использовать `superpowers:executing-plans` для последовательного выполнения; отмечать чекбоксы только после проверки результата.

**Goal:** доказать качество расширения запросов, проверить эксплуатацию и завершить этап 8 с документированным решением о выпуске.

**Architecture:** исправленный probe использует существующий поисковый путь приложения и сохраняет промежуточные результаты. Разметка, неизменный корпус и парные off/on-прогоны дают основание для оценки качества; нагрузка, регрессия и проверка отката завершают приёмку.

**Tech Stack:** Python/pytest, PostgreSQL, Qdrant, Ollama, Next.js/Node.js.

**Spec:** [основной план](2026-09-11-glossary-query-expansion.md), прежде всего §3, §14 задача 8, §15–16 и §18; правила поиска в корневом `AGENTS.md`.

Дата: 11.09.2026. **Итоговое решение от 15.09.2026:** этап 8 завершён.
Владелец принял сохранённые пользовательские примеры и измерительную базу как
достаточную проверку текущего выпуска; строгий относительный performance SLA и
дополнительная предметная разметка исключены из release-gate. Флаг включён на
LXC 102 штатным runtime env, `okf-knowledge.service` перезапущен, `/health`
backend вернул `ok`.

## Общие ограничения

- Для новой инсталляции безопасный пример конфигурации сохраняет
  `GLOSSARY_QUERY_EXPANSION_ENABLED=false`. В релизной конфигурации LXC 102
  флаг включён 15.09.2026; изменение runtime env применяется только после
  перезапуска backend.
- Только query-side изменения: не менять индексный sparse-токенайзер, документы и векторы корпуса.
- Сохранять различие API: search возвращает merged-блоки; chat дополнительно применяет свои фильтры контекста.
- Исторические критерии и результаты сохраняются для последующих измерений. Их
  изменение не переписывает прошлые отчёты; решение от 15.09.2026 отменяет их
  только как блокеры данного выпуска.
- Рабочие PostgreSQL/Qdrant не использовать для синтетической нагрузки, удаления данных и тестов миграций. Fixtures и нагрузочный словарь размещать в отдельной тестовой БД/коллекции.
- Существующие изменения пользователя сохранять. Коммиты, если потребуются при исполнении, включают только проверенные относящиеся к задаче изменения.
- Отсутствие данных или обязательного прогона означает «не проверено» для
  соответствующей метрики; после решения владельца это не блокирует данный
  выпуск, но остаётся основанием для нового измерения при существенной правке поиска.

> **Отметка о завершении:** незакрытые чекбоксы ниже сохранены как исторический
> backlog измерений на 11–14.09.2026. Они не являются открытой работой по этому
> выпуску и не отменяют решение владельца от 15.09.2026.

## Исходные данные и границы доказательств

Есть 42 сценария и локальные `tests/artifacts/stage8/probe-stage8-off.json` / `probe-stage8-on.json`. Все сценарии по умолчанию используют hybrid. Набор содержит ожидания canonical, но не утверждённые релевантные источники. Seed содержит 7 терминов; основной план требует предметно проверенные 10–15.

Старый отчёт показывает 309 исчезнувших групп в 24 сценариях. Comparator игнорирует slug при сравнении групп, а final source_keys приписывают блоку соседние концепты того же чанка. До исправления этих ограничений число 309 служит только отправной точкой диагностики.

Предварительные задержки и отсутствие дрейфа в 10 negative-кейсах полезны как наблюдения. Полный backend-прогон завершился `1130 passed, 1 failed`; отдельный PASS падавшего теста не заменяет успешный полный прогон. Полная матрица, целевой нагрузочный тест и выпуск ещё не приняты.

## 8.1. Сделать результаты probe достоверными

**Файлы:** изменить `backend/test_scripts/probe_sources.py`, `backend/tests/test_probe_sources.py`; при необходимости явной передачи provenance — `backend/app/services/context_builder.py` и его тесты.

- [x] Зафиксировать тестами: два разных slug одного чанка; отдельный sibling-блок; concept+chunk; пустая raw-выдача; выход за final top_k при сохранении в raw; перестановка без потери; несовместимые наборы кейсов; отсутствующий явно заданный файл cases.
- [x] Для каждого final-блока сохранять собственный источник и только фактически включённые в его контент источники. Проверять идентичность по `(doc_id, slug, chunk_index)`; `(doc_id, chunk_index)` оставить отдельной диагностикой групп.
- [x] Сохранять этапы: raw после fusion, visible, merged до top_k, после top_k, final после соответствующих API-фильтров. Не называть raw «всем корпусом»: это ограниченная выборка retrieval.
- [x] Добавить явный вариант `search`/`chat`, записывать mode, разрешённые ветки, top_k, per_branch_top_k, фильтры, QueryPlan и версию схемы артефакта.
- [x] Отклонять сравнение при разных query/mode/filters/списках кейсов или несовместимой схеме. Старый baseline с усечёнными ID допускается только как историческая справка. Пустой raw не заменяет final.
- [x] Сохранять отчёт сравнения в отдельный JSON; существующие baseline не перезаписывать. Любое неожиданное canonical считать ошибкой, включая canonical вне списка forbidden. Проверено на `tests/artifacts/stage8/probe-stage8-ux-loss-report.json`.

**Контрольный пример для теста comparator:**

```python
before = {("doc-1", "main", 0), ("doc-1", "sibling", 0)}
after = {("doc-1", "sibling", 0)}
assert before - after == {("doc-1", "main", 0)}
assert {(d, c) for d, _, c in before} == {(d, c) for d, _, c in after}
```

Этот пример должен проверяться через настоящий comparator: потеря main видна, хотя группа сохранилась. Тест с перестановкой тех же идентичностей должен дать только смену ранга. Тест search не вызывает чатовые фильтры; test chat вызывает их с match_groups.

**Проход:** все перечисленные сценарии покрыты проходящими тестами реальных функций; probe соответствует обоим API; полная идентичность не подменяется группой или названием.

## 8.2. Подготовить отчёт об исчезнувших источниках

**Файлы:** создать `backend/test_scripts/glossary_probe_report.py`, `backend/tests/test_glossary_probe_report.py`, `docs/superpowers/reports/2026-09-11-glossary-stage8-loss-review.md`.

- [x] Реализовать чтение двух совместимых v3-артефактов без обращения к поисковым сервисам. Старые JSON разрешаются только как историческая справка; final provenance в них не принимается.
- [x] Вывести по каждому изменённому источнику query, mode/API, canonical, полную идентичность, заголовок, off/on ранги по этапам, маркеры совпадений и последний этап, где источник присутствовал.
- [x] Разделить события: смена позиции, выход за top_k, фильтрация контекста, отсутствие в ограниченной raw-выборке, смена sibling, новый источник. Для релевантности используются `relevant` / `irrelevant` / `unresolved`.
- [ ] Утвердить содержимое изменённых источников предметным экспертом; инженерная проверка кандидатов и пакет с фрагментами подготовлены в `docs/superpowers/reports/2026-09-11-glossary-stage8-content-review.md` и `docs/superpowers/reports/2026-09-12-glossary-stage8-labeling-packet.md`. Смена заголовка или совпадение числа сами по себе не доказывают релевантность. Актуальный bounded quality report содержит `7951 unresolved` событие из-за отсутствия labels в cases; это не `7951` подтверждённая потеря.
- [x] Проверить генератор отчёта на маленьком fixture с известными событиями: количества и строки совпадают с входными данными.
- [x] Добавить воспроизводимый расчёт `Precision@5` и `Recall@10` поверх `final_blocks`: фиксированный знаменатель для пустых позиций, document recall по точным `doc_id`, sibling `source_keys` и явный `BLOCKED` при неразмеченных источниках. Проверено sanity-fixture и тестом на регрессию off/on.

**Проход:** каждый обнаруженный тип изменения отражён; нет потерянных строк или выдуманного provenance. Подтверждённые потери, нерелевантный шум и неразобранные случаи посчитаны отдельно. Неразобранные случаи допускают переход к подготовке разметки, но блокируют выпуск. Технический расчёт качества готов; текущий matrix-report остаётся `BLOCKED` (`90` positive/compound вариантов), потому что labels не утверждены.

## 8.3. Зафиксировать предметную разметку и матрицу

**Файлы:** изменить `backend/test_scripts/glossary-probe-cases.json`, соответствующий JSON в `backend/seeds/`, `backend/tests/test_probe_sources.py`; создать `docs/superpowers/reports/2026-09-11-glossary-stage8-labels.md`.

- [x] Сохранить все 7 legacy и минимум 40 базовых запросов: 10 positive, 10 negative, 5 compound, 5 multilingual, 5 isolation; один запрос может покрывать несколько категорий.
- [ ] Для positive и compound записать точный expected_canonicals, релевантные doc_id/slug/chunk_index, какие источники обязательны и допустимые эквивалентные источники. Для negative ожидать строго пустое множество canonical.
- [ ] Разметить релевантность блоков из объединённого пула результатов всех режимов для Precision@5. Любой новый неразмеченный блок получает unresolved; до оценки его нужно проверить. При изменении labels пересчитать off и on на одной версии разметки. В актуальном bounded protocol-1 остаются `7951 unresolved` событий до предметной проверки.
- [ ] Проверить 10–15 seed-терминов, исходные определения/языки и разрешённые aliases. Зафиксировать автора и дату предметной проверки; инженерную предварительную разметку явно отличать от утверждённой.
- [x] Добавить реальные fixtures для de-original/en-UI и und-original; слова «de»/«unknown» в названии или тексте запроса не заменяют соответствующие поля данных. Fixture `backend/tests/fixtures/stage8_retrieval_documents.json` содержит явные `source_locale`/`ui_locale`; покрытие проверено тестом `backend/tests/test_stage8_retrieval_fixtures.py`.
- [x] Добавить непустые fixtures для tags/dev_tags, ru/en/unknown, deleted/orphan и чужой истории. Fixture содержит эти поля и soft-deleted/orphan случаи; visibility/filter тест прошёл. Ownership чужой истории проверен существующими API-тестами `test_chat_history.py` с отказом до retrieval.
- [x] Развернуть каждый базовый запрос в dense/BM25/hybrid и search/chat. Для изоляции выполнить матрицу 252 вариантов и с off/on; сравнение UI-языков выполнено при одинаковом query и фильтрах, содержательная полнота изоляционных fixtures ещё не принята.
- [x] До приёмочного after сохранён актуальный manifest: хэши кейсов и labels, seed/snapshot словаря, выбранных рабочих исходников текущей projection-ревизии, версии моделей, настроек, содержимого/версий документов и Qdrant-точек. До/после retrieval-only guard отпечатки совпали (`manifest_guard PASS`, `changed=[]`); артефакты `backend/scripts/stage8-manifest-{before,after,20260912}.json`. `labels.approved=false` отражает, что предметная разметка ещё не утверждена.

**Проход:** техническая часть матрицы выполнена: 42 базовых запроса, все 7 legacy, 252 варианта mode/API до умножения на off/on. Предметная разметка не утверждена; обязательные источники для части positive-кейсов не доказаны текущим корпусом, поэтому пункт 8.3 остаётся открытым.

## 8.4. Выполнить парные прогоны и локализовать причины

**Файлы:** `backend/test_scripts/probe_sources.py`, JSON-артефакты с уникальным run_id, отчёт из 8.2. Исправления приложения — только в локализованном участке с регрессионным тестом.

- [x] Проверена доступность PostgreSQL/Qdrant/Ollama по `127.0.0.1`, manifest сохранён до и после retrieval-only guard; отпечатки БД, glossary, исходников и Qdrant совпали. Артефакты и детали — в `docs/superpowers/reports/2026-09-11-glossary-stage8-benchmark.md`.
- [x] Выполнить off/on для матрицы 8.3. Хэши набора кейсов совпадают; корпус в окне сравнения не менялся по известным артефактам.
- [x] Для ухудшившихся кейсов сопоставлены dense, BM25, hybrid и этап исчезновения. Выполнены локализация visibility/domain-match, безопасный Qdrant payload projection, unified hydration и gRPC-транспорт; average BM25 SLA всё ещё не проходит, а изменение budget/числа алиасов требует утверждённых labels.
- [x] Для подтверждённых задержек локализованы два участка: полный `get_many` при visibility lookup и offset mapping в content domain-match; оба исправлены TDD-тестами. Артефакт диагностики: `docs/superpowers/reports/2026-09-12-glossary-stage8-retrieval-diagnostic.md`.
- [x] После исправлений выполнена полная матрица заново (`probe-stage8-diagnostic2-off/on-20260912.json`), сохранены предыдущие результаты и причины изменений. Эксперимент с `SEARCH_PER_BRANCH_TOP_K=20` показал потерю 252 final-блоков и поэтому не принят.
- [ ] Закрыть оставшуюся задержку `chat/bm25` и после окончательного исправления повторить полный 5-парный протокол.

**Проход:** все пары сопоставимы, для каждого ухудшения есть причина и результат повторной проверки. Нет открытых подтверждённых регрессий и unresolved-случаев, влияющих на критерии ниже.

## 8.5. Принять поисковое качество

**Файлы:** создать `docs/superpowers/reports/2026-09-11-glossary-stage8-acceptance.md`; расчёты и тесты — в модуле отчёта из 8.2.

| Проверка | Критерий PASS |
|---|---|
| Распознавание | Actual canonical строго равны expected; у всех negative — пусто; 0 ложных расширений |
| Off/no-match | В детерминированных тестах векторы, фильтры и ordered IDs идентичны; на живом корпусе 0 необъяснённых изменений |
| Recall@10 документов | Для каждого positive/compound, mode и API: число ожидаемых документов в первых 10 блоках / число ожидаемых документов не ниже off; хотя бы один ожидаемый документ найден, все помеченные обязательными найдены |
| Precision@5 блоков | Число релевантных блоков среди первых 5 / 5 не ниже off для каждого кейса; недостающие позиции дают 0. Дополнительно число нерелевантных блоков не растёт; неразмеченных блоков 0 |
| Legacy | 0 неразобранных полных исчезновений baseline-источников из проверяемой raw-выборки и 0 подтверждённых релевантных потерь. Выход за final top_k оценивается метриками отдельно |
| Изоляция | 0 нарушений tags/dev_tags, source_locales, deleted/visibility/orphan и ownership |
| Контекст | Каждый обязательный сравниваемый термин представлен релевантным источником; общее слово/число не создаёт сильного доменного совпадения; review-сиблинги сохраняют предусмотренную семантику |
| Языки | При смене только UI locale неизменны original/source locale и retrieval-векторы; display меняется корректно, язык ответа соответствует существующему контракту чата |

Это операционализация §15: оценивать каждую пару, не скрывать ухудшение средним. Если эксперт предлагает исключение для конкретного ухудшения, оно требует отдельного записанного решения до выпуска; автоматически такой кейс PASS не получает. Fixtures подтверждают поведение на отсутствующих в рабочем корпусе документах, но не выдаются за доказанный прирост на реальном корпусе.

**Проход:** таблица имеет PASS по каждому обязательному пункту, расчёты воспроизводятся из артефактов и одной версии labels. Для sanity-теста метрик использовать 2 ожидаемых документа, один найденный → Recall@10=0.5; 3 релевантных блока в первых 5 → Precision@5=0.6. Реализация готова в `backend/test_scripts/glossary_probe_report.py`, но corpus quality не принимается до labels.

## 8.6. Проверить задержки и нагрузку

**Файлы:** создать `backend/test_scripts/benchmark_glossary.py`, `backend/tests/test_glossary_benchmark.py`; результаты приложить к отчёту приёмки. Инструментировать существующие snapshot/prepare/vector этапы без альтернативной реализации matcher.

- [x] В отдельной PostgreSQL БД подготовить ровно 1000 терминов и 10000 алиасов, включая совпадающие и несовпадающие запросы. Счётчики до замера подтверждены; стенд `okf_glossary_stage8_bench_20260911b`.
- [x] Измерены DB read, matcher, dense embedding, query sparse, Qdrant, postfilter и общий retrieval отдельно; время генерации ответа не включалось. Query-side результаты — в `docs/superpowers/reports/2026-09-11-glossary-stage8-benchmark.md`, сквозная раздельная instrumentation и API/mode-матрица — в `docs/superpowers/reports/2026-09-12-glossary-stage8-retrieval-diagnostic.md`.
- [x] Протокол: выполнены 2 полных warmup-прохода и 5 измеряемых пар с чередованием порядка off/on; после request-local domain-match cache и Qdrant payload projection сохранены по 210 наблюдений на сторону, avg/p50/p95 по mode/API в `docs/superpowers/reports/2026-09-12-glossary-stage8-retrieval-diagnostic.md`. Authoritative SLA по-прежнему не пройден для `search/bm25` и `chat/bm25` по average.
- [x] Счётчиком SQL подтверждено отсутствие запросов на каждый термин/alias: cold `off` — `0`, cold `on` — фиксированные `5`, warm `on` — `0`; parallel retrieval smoke дал `298/304` SQL statements на `100/100` запросов без ошибок. Подробности и ограничения счётчика — в `docs/superpowers/reports/2026-09-11-glossary-stage8-benchmark.md`.
- [x] Счётчиками внешних вызовов подтверждены все retrieval-ветки: матрица `search/chat × dense/bm25/hybrid` по 42 кейса дала `llm_calls=0` во всех ячейках, embedding calls off/on `42/42` для dense и hybrid и `0/0` для BM25, ошибок `0`. Генерация ответа намеренно не входила в harness; детали в benchmark report.

**Проход:** p95 prepare вместе со snapshot ≤30 мс при 1000/10000; avg и p95 общего retrieval on ≤110% off для каждого mode/API при последовательной нагрузке. При 5 клиентах — 0 ошибок/таймаутов/истощения пула, зафиксированы задержки и SQL. Для параллельной нагрузки отдельный SLA исходной спецификацией не установлен, её нельзя обозначать как доказанную производственную ёмкость.

**Промежуточный результат 11.09.2026:** query-side benchmark на отдельной
PostgreSQL БД дал on p95 `prepare=2.010 мс`, `matcher=1.807 мс`,
`snapshot=0.007 мс`, `query_sparse=0.144 мс` при off/on чередовании 5 пар.
Критерий prepare+snapshot выполнен. Сквозной retrieval, внешние вызовы и
параллельный прогон ещё не приняты.

Дополнительный query-side parallel smoke: 5 клиентов × 20 запросов, off/on по
100 запросов; ошибок `0`, SQL после прогрева `0`, on p95 `2.104 мс`. После
ACL-fix выполнены cold SQL и retrieval-only parallel smoke; результаты
`0/5/0` SQL для cold-off/cold-on/warm-on и `298/304` SQL при `100/100`
параллельных hybrid-запросах зафиксированы в benchmark report. Полный пункт
8.6 остаётся открытым до прохождения последовательного BM25 SLA.

## 8.7. Закрыть регрессию, миграции и откат поведения

**Файлы:** существующие `backend/tests/test_glossary_*.py`, `backend/tests/test_llm_client.py`, `backend/tests/test_schema_drift.py`, `frontend/test/`, `docs/GLOSSARY.md`, отчёт приёмки.

- [x] Локализовать и устранить order-sensitive сбой `test_inflight_over_limit_is_logged`, не исключая тест из полного suite. Причина — Alembic `fileConfig` отключал application loggers; после `disable_existing_loggers=False` полный backend suite зелёный.
- [x] Выполнить команды регрессии из §15.3 основного плана: backend suite, frontend `node --test`, ESLint затронутых компонентов, production build в отдельной рабочей копии без вмешательства в активный `.next`.
  Актуальные проверки: backend suite — `1192 passed, 6 warnings`; frontend `node --test` — `108 passed, 0 failures`; ESLint затронутых компонентов — PASS. Production build — PASS (`next build`, Next.js 16.3.4, 15 маршрутов). Дефолтный Turbopack в копии не запускался из-за ограничения на junction `node_modules`.
- [x] На отдельной PostgreSQL БД подтвердить одну Alembic head, upgrade с чистого состояния и обеих исходных веток, companion при drift, повторный запуск без потери данных. Проверить PostgreSQL-гонки и атомарный аудит; SQLite-прогон их не заменяет. Проверены clean/repeat upgrade, обе исходные ветки до merge, одна head `b2c3d4e5f6a7`, companion отказ на намеренном drift и PostgreSQL CAS/audit; детали в `docs/superpowers/reports/2026-09-11-glossary-stage8-migration-smoke.md`.
- [x] В тестовом окружении отключение термина подтверждено `test_old_plan_is_immutable_when_term_is_disabled`; свежие процессы прочитали глобальный флаг `False` без override и `True` с `GLOSSARY_QUERY_EXPANSION_ENABLED=true`. Рабочая конфигурация не изменялась; детали в `docs/superpowers/reports/2026-09-12-glossary-stage8-rollback-roles.md`.
- [x] Роли admin/editor/user, CSRF, preview, переводы/backfill и старые сообщения покрыты API/backend suite; свежий targeted run — `14 passed, 3 warnings`, актуальный полный suite — `1192 passed, 6 warnings, 0 failures`. Браузерный rollout smoke и включение флага не выполнялись до предметной приёмки.

**Проход:** обязательные backend/frontend проверки завершились без failures; PostgreSQL-проверки выполнены, а не пропущены; мутации атомарно аудируются; ручные переводы сохранены; оба способа отключения подтверждены. Downgrade с удалением таблиц не входит в штатный откат и на рабочей БД не выполняется.

## 8.8. Выпуск и отметка выполнения

**Файлы:** штатная конфигурация окружения выпуска, `docs/GLOSSARY.md`, оба файла плана и итоговый отчёт.

- [x] Сформирован pre-release audit с явными `PASS/BLOCKED` по 8.1–8.8 и
  ссылками на evidence: `docs/superpowers/reports/2026-09-12-glossary-stage8-acceptance.md`.
- [ ] Проверить, что 8.1–8.7 закрыты доказательствами; записать PASS/BLOCKED по каждому критерию, ссылки на артефакты, ревизию кода и утверждение labels/seed. Evidence/status записаны в `docs/superpowers/reports/2026-09-12-glossary-stage8-acceptance.md` для базовой ревизии `abcd880352229d210800cc1c130119ce79e95ea8`; labels/seed ещё не утверждены, поэтому пункт остаётся открытым.
- [ ] После приёмки включить флаг штатной конфигурацией в согласованном окружении и перезапустить backend. Подготовка этого плана сама по себе не запускает выпуск.
- [ ] После рестарта выполнить smoke через реальные API/UI: alias, no-match, запрещённая аббревиатура, сравнение двух терминов, unknown source locale, фильтр языка, история и отключение записи. Проверить, что флаг действительно подхвачен приложением.
- [ ] При провале smoke выключить глобальный флаг с рестартом, подтвердить восстановление поведения, записать причину и оставить этап 8 открытым.
- [ ] При PASS обновить чекбоксы задачи 8, общий статус в начале основного плана и строку этапа 8 в §17.2. Добавить дату, фактические команды/результаты, местоположение артефактов и конечное состояние флага.

**Проход:** все технические и предметные критерии приняты, включение и smoke успешны, откат проверен, отметки в плане согласованы. Если техническая приёмка завершена, но выпуск ещё не выполнен, статус — «приёмка пройдена, ожидает выпуска», а не «этап 8 завершён».

## Отметка текущей работы

- [x] 11.09.2026: составлен план завершения этапа 8 с действиями, артефактами и критериями прохождения.
- [x] В основном плане исправлены преждевременные отметки; предварительные наблюдения отделены от завершённой приёмки.
- [x] 11.09.2026: исправлена provenance probe, добавлен v3 comparator/report и выполнена матрица 252×off/on.
- [x] 11.09.2026: добавлен список кандидатов для предметной разметки; все изменения пока `unresolved`.
- [x] 11.09.2026: исправлен Alembic logging drift; промежуточный полный backend suite после UX-изменений — `1144 passed, 6 warnings`.
- [x] 11.09.2026: после оптимизации snapshot/matcher полный backend suite — `1147 passed, 6 warnings`; query-side benchmark `1000/10000` выполнил p95 prepare-бюджет.
- [x] 12.09.2026: после оптимизации visibility lookup и content domain-match полный backend suite — `1149 passed, 7 warnings`; затронутый Stage 8 набор — `54 passed`.
- [x] 12.09.2026: подтверждены узкие места BM25 postfilter, выполнены TDD-исправления и diagnostic2 full matrix `252` вариантов; подробности в `docs/superpowers/reports/2026-09-12-glossary-stage8-retrieval-diagnostic.md`.
- [x] 12.09.2026: формальный performance-протокол после 2 warmup-проходов и 5 чередующихся пар сохранён в `probe-stage8-protocol-*`; dense/hybrid проходят, `search/bm25` и `chat/bm25` остаются FAIL по SLA.
- [x] 12.09.2026: добавлен request-local cache для повторных glossary-match в chat и синхронизирован probe; контрольный протокол `probe-stage8-finalcache-*` подтвердил отсутствие acceptance failures, но BM25 average/p50 всё ещё не проходят SLA.
- [x] 12.09.2026: после согласованного ACL-fix выполнены cold SQL counter (`cold off=0`, `cold on=5`, `warm on=0`) и retrieval-only parallel smoke (`5×20`, off/on по 100 запросов, ошибок и acceptance failures `0`, embedding calls `100/100`); артефакт `tests/artifacts/stage8/probe-stage8-parallel-retrieval-20260912.json`.
- [x] 12.09.2026: в том же артефакте добавлена полная матрица external-call counters (`search/chat × dense/bm25/hybrid`, 42 кейса × off/on): `0` ошибок и LLM-вызовов, embedding calls без роста on относительно off (`42/42` dense/hybrid, `0/0` BM25).
- [x] 12.09.2026: добавлен безопасный Qdrant payload projection без `content` (контент гидрируется из PostgreSQL); новая полная матрица `252` кейса на off/on дала `acceptance_failures=[]`, но average BM25 SLA остался FAIL. Регрессионные тесты — `backend/tests/test_vector_store_retrieval.py`.
- [x] 12.09.2026: после Qdrant payload projection полный backend suite — `1152 passed, 7 warnings`; профильные Stage 8 tests — `66 passed`.
- [x] 12.09.2026: graph expansion дополнительно изолирован для `search/bm25`: непустой результат только в `1/42` кейсах, систематической причиной BM25 latency не является; graph-поведение без labels не менялось.
- [x] 12.09.2026: выполнен trade-off лимита добавляемых алиасов `0/1/2/4`; лимиты `0/1/2` меняют retrieval source identities (до `532` raw-потерь относительно default `4`) и отклонены без утверждённых relevance labels. Default не менялся; артефакт — `tests/artifacts/stage8/probe-stage8-budget-tradeoff-20260912.json`.
- [x] 12.09.2026: добавлен hermetic fixture `backend/tests/fixtures/stage8_retrieval_documents.json` для `de`-original/`en`-UI, `und`-original, locale/tag/dev_tag, deleted/orphan; fixture и locale/filter regression — `14 passed`.
- [x] 12.09.2026: полный backend suite после добавления fixtures завершён: `1156 passed, 7 warnings` за `9:13`; новые Stage 8 tests включены в общий прогон.
- [x] 12.09.2026: подготовлен пакет предметной разметки с фрагментами `okf_concepts` и точными source identities: `docs/superpowers/reports/2026-09-12-glossary-stage8-labeling-packet.md`.
- [x] 12.09.2026: в отчёт добавлены Precision@5/Recall@10 и сравнение off/on; matrix `252` вариантов воспроизводимо даёт `BLOCKED` для `90` positive/compound вариантов при отсутствии labels, без ложного PASS.
- [x] 12.09.2026: рекомендации по закрытию предметного labels-блокера и BM25 SLA зафиксированы в `docs/superpowers/reports/2026-09-12-glossary-stage8-recommendations.md`; production defaults и feature flag не менялись.
- [x] 12.09.2026: после добавления quality-метрик полный backend suite повторно завершён `1159 passed, 7 warnings`; новых регрессий не обнаружено.
- [x] 12.09.2026: после request-local lexical cache и обновления probe mock полный backend suite завершён `1160 passed, 7 warnings`; ускорение SLA ещё не заявлено без повторного benchmark-протокола.
- [x] 12.09.2026: gRPC cache3-протокол после 2 warmup/5 пар завершён на 210 наблюдениях на сторону для каждого API/mode; dense/hybrid PASS, BM25 остаётся FAIL по average и p95 (`search +74.49%/+13.62%`, `chat +106.88%/+38.30%`). Локальный транспорт включён через `QDRANT_PREFER_GRPC=true`; glossary labels и production feature flag не менялись.
- [x] 12.09.2026: добавлена unified retrieval hydration для совместного чтения canonical текста концептов и чанков одним SQL-сеансом; targeted hydration/retrieval tests — `100 passed`, полный backend suite — `1161 passed, 7 warnings`. Отдельный `chat/bm25` diagnostic на 210 наблюдениях на сторону не заменяет authoritative cache3 и не закрывает BM25 average SLA.
- [x] 12.09.2026: после включения локального Qdrant gRPC (`QDRANT_PREFER_GRPC=true`, `QDRANT_GRPC_PORT=16334`) полный backend suite повторно завершён `1162 passed, 7 warnings`; gRPC cache3 сохранил `acceptance_failures=[]`, но BM25 average SLA остался FAIL.
- [x] 12.09.2026: добавлена условная hydration-оптимизация для multi-topic групп: raw-текст чанка не читается, если merge использует непустой canonical primary-концепт; fallback для пустого контента/заголовка покрыт тестами. Профильная проверка — `4 passed`, расширенный Stage 8 набор — `136 passed, 4 warnings`.
- [x] 12.09.2026: harnessfix4 после оптимизации (2 warmup + 5 чередующихся пар, 210 наблюдений на сторону) сохранил `acceptance_failures=[]`; обе BM25-пары остаются FAIL по average (`search +75.21%`, `chat +106.69%`), а p95 составляет `+16.70%` и `+33.37%` соответственно (только chat p95 выше порога). Повторяющиеся предупреждения о Qdrant↔`document_chunks` оказались false positive intentional hydration skip; read-only inventory дал `0/0` отсутствующих ключей, логирование исправлено тестом.
- [x] 12.09.2026: варианты решения по относительному/абсолютному BM25 SLA оформлены в `docs/superpowers/reports/2026-09-12-glossary-stage8-sla-decision.md`; критерий самостоятельно не изменён.
- [x] 12.09.2026: категориальный разбор harnessfix4 подтвердил, что BM25 overhead сосредоточен в positive/compound/multilingual/filter-isolation кейсах с расширенным raw-пулом; negative/legacy без расширения почти не деградируют. Артефакт: `tests/artifacts/stage8/probe-stage8-harnessfix4-bm25-breakdown-20260912.json`. Изменение alias-budget, sparse-весов или лимита кандидатов отложено до labels.
- [x] 12.09.2026: подготовлен машинный черновик предметной разметки по 15 positive/compound кейсам: 209 уникальных top-5 кандидатов с точной identity, наблюдениями API/mode и excerpt из canonical БД; labels не назначались. Артефакт: `tests/artifacts/stage8/probe-stage8-labeling-candidates-harnessfix4-20260912.json`.
- [x] 12.09.2026: добавлен request-local cache токенов query для `matched_terms`/`title_matched_terms`; контекстный набор — `79 passed`, расширенный Stage 8 набор — `136 passed, 4 warnings`. Harnessfix5 показал небольшое снижение chat context-filter, но BM25 SLA не закрыл; его summary и breakdown сохранены в `backend/scripts/probe-stage8-harnessfix5-*.json`.
- [x] 12.09.2026: сравнение harnessfix4/harnessfix5 по 2520 case-run подтвердило `0` изменений source identity, `kind`, title и acceptance после query-token cache.
- [x] 12.09.2026: добавлен read-only валидатор `backend/test_scripts/validate_stage8_labels.py` и 4 regression-теста; текущий файл cases корректно даёт `BLOCKED` (`15` quality-кейсов, `0` complete), labels не назначались.
- [x] 12.09.2026: visibility lookup и canonical hydration объединены в одну DB-сессию для `/search`, `/chat` и probe; полный 2-warmup/5-pair протокол на 210 наблюдениях на сторону сохранил `acceptance_failures=[]` и дал `search/bm25 +72.31%/+16.77%`, `chat/bm25 +103.88%/+32.16%` (avg/p95), но SLA остаётся FAIL. Сравнение со старым protocol-1: `0` изменений финальных identity/kind/title для off/on; артефакт `tests/artifacts/stage8/probe-stage8-combined-hydration-summary-20260912.json`.
- [x] 12.09.2026: после refactor обновлены mock-тесты API/probe; затронутый Stage 8/retrieval набор завершён `141 passed, 4 warnings`, регрессий не обнаружено.
- [x] 12.09.2026: полный backend suite после bounded hydration завершён `1176 passed, 6 warnings` за 8:56; падений нет, warnings — dependency deprecations и финальный gRPC channel warning.
- [x] 12.09.2026: bounded canonical hydration ограничила SQL-выборку границами ответа (`4000/6000` символов); новый 2-warmup/5-pair протокол сохранил `acceptance_failures=[]` и `0` semantic identity differences, но BM25 SLA остался FAIL (`search +76.17%/+10.39%`, `chat +108.55%/+26.16%`, avg/p95). Затронутые тесты — `142 passed, 4 warnings`; артефакт `tests/artifacts/stage8/probe-stage8-bounded-hydration-summary-20260912.json`.
- [x] 12.09.2026: выполнен SLA recheck по единому критерию плана (+10% для average и p95): `3/6` пар PASS; `search/bm25` FAIL average/p95, `chat/bm25` FAIL average/p95, `chat/hybrid` FAIL p95 (`+11.78%`). Артефакт `tests/artifacts/stage8/probe-stage8-bounded-hydration-sla-recheck-20260912.json`; критерий и release gate не изменялись.
- [x] 12.09.2026: quality report пересчитан на bounded protocol-1 текущего кода: `acceptance_failures=[]`, `90 BLOCKED` и `162 NOT_APPLICABLE` из-за отсутствия утверждённых labels; артефакт `tests/artifacts/stage8/probe-stage8-bounded-hydration-quality-report-20260912.json`.
- [x] 12.09.2026: категориальный BM25-разбор bounded-протокола подтвердил, что рост raw-пула сосредоточен в positive/compound/multilingual/filter-isolation, а negative/legacy не меняются; в chat compound/multilingual дополнительно растёт context-filter. Артефакт `tests/artifacts/stage8/probe-stage8-bounded-hydration-bm25-breakdown-20260912.json`; изменение пула отложено до labels.
- [x] 12.09.2026: SLA decision note синхронизирована с последним bounded-протоколом; исторические harnessfix5 цифры помечены как historical, критерий плана `+10%/+10%` явно восстановлен, SLA gate остаётся открытым (`3/6` пар PASS).
- [x] 12.09.2026: валидатор labels и quality report исправлены для источников-чанков с `slug=null`/пустым slug-сегментом; добавлены регрессионные тесты, профильный набор завершён `27 passed`.
- [x] 12.09.2026: quality report пересоздан уже текущей версией normalizer/validator из тех же bounded off/on-артефактов; результат воспроизводимо остался `BLOCKED` (`90` quality comparisons, `162` not applicable, `7951` unresolved change-events), labels не назначались.
- [x] 12.09.2026: добавлен независимый `stage8_sla_validator.py` с TDD-покрытием; он сверяет исходные значения summary/recheck, строгие average/p95 лимиты и итоги `3/6`, CLI завершён `READY` при содержательном SLA `BLOCKED`; валидатор добавлен в текущий manifest.
- [x] 12.09.2026: независимый SLA-валидатор усилен проверкой пустой/неполной матрицы, авторитетного критерия `110%`, арифметики дельт и дубликатов; quality report блокирует конфликтующие alias-labels, а manifest хэширует также `expected_documents`/`mandatory_sources` и сохраняет явный `labels.approved`. Регрессионный Stage 8 набор — `36 passed`; текущие артефакты по-прежнему `3/6` SLA PASS и labels `0/15` complete.
- [x] 12.09.2026: полный backend suite после усиления acceptance-контура завершён `1188 passed, 7 warnings`; падений нет. Технические проверки зелёные, внешние блокеры labels и SLA не изменились.
- [x] 12.09.2026: дополнительный аудит обнаружил и исправил приём нулевого/отрицательного sample и latency и изменение `expected_documents`/`mandatory_sources` между off/on. Обновлены quality report и manifest; Stage 8 regression-набор — `38 passed`. Labels и содержательный SLA остаются открытыми.
- [x] 12.09.2026: проверка выявила fallback, скрывавший отсутствие labels на одной стороне off/on; сравнение теперь блокирует неполную разметку. Regression-набор — `39 passed`; quality report и manifest пересозданы.
- [x] 12.09.2026: manifest расширен хэшами `probe_sources.py`, `glossary_probe_report.py`, `validate_stage8_labels.py` и `stage8_manifest.py`; текущий read-only snapshot создан с 42 кейсами, 26 документами и 8193 точками Qdrant. Относительные пути CLI также исправлены и проверены запуском из `backend`.
- [x] 11.09.2026: production build фронтенда выполнен в отдельной копии без изменения активного `.next`; webpack build завершён успешно, сгенерированы 11 маршрутов.
- [x] 11.09.2026: PostgreSQL migration smoke в отдельной БД: clean/repeat `alembic upgrade head` PASS, одна head и companion check PASS; обе исходные ветки, drift и PostgreSQL CAS/audit также проверены — migration-критерий закрыт.
- [x] 11.09.2026: обе исходные Alembic-ветки (`f1a2b3c4d5e6`, `f8d9e0a1b2c3`) проверены до merge; PostgreSQL CAS/audit smoke дал `1 success`, `4 conflicts`, `0 errors`, `1` audit-запись.
- [x] 11.09.2026: companion на намеренном PostgreSQL schema drift обнаружил пропавшую колонку и отказался от `--apply` с exit code `1`.
- [x] 11.09.2026: frontend unit suite после UX-изменений — `98 passed`, ESLint затронутых компонентов — PASS.
- [x] 11.09.2026: UX явно сообщает выключенный `GLOSSARY_QUERY_EXPANSION_ENABLED` в `/settings`, чате и админском глоссарии; флаг не включался.
- [x] 11.09.2026: установлено, что `8049 unresolved` в свежем отчёте вызвано отсутствием relevance labels в cases; это не подтверждённые потери источников.
- [x] 11.09.2026: Alembic heads подтверждены — одна head `b2c3d4e5f6a7`.
- [x] 12.09.2026: SLA-валидатор дополнен проверкой заявленного приёмочного протокола (2 warmup, 5 пар, 210 наблюдений на сторону и пустые `acceptance_failures`); регрессионный набор — `40 passed`, реальный отчёт структурно `READY`, содержательно остаётся `3/6` PASS.
- [x] 12.09.2026: полный backend suite после проверки протокола завершён `1192 passed, 6 warnings, 0 failures` (`548.44s`); warnings не связаны с функциональными падениями.
- [x] 12.09.2026: форма нового алиаса переведена с произвольного поля `locale` на общий селектор языков; полный frontend unit suite после изменения — `103 passed, 0 failures`, ESLint изменённого компонента — без ошибок и предупреждений.
- [x] 12.09.2026: неизвестная локаль существующего алиаса больше не маскируется локалью интерфейса: селектор показывает `und`; добавлен регрессионный тест, полный frontend unit suite — `104 passed, 0 failures`.
- [x] 12.09.2026: production build после изменений селектора завершён `next build --webpack` (Next.js 16.3.4, 15 маршрутов, exit code 0).
- [x] 12.09.2026: `GLOSSARY.md` дополнен явной картой сохранения UI: мгновенные фильтры/удаление, кнопки сохранения источника/alias/перевода, отдельное ревью и saved-only preview; описан selector языка и отображение `und`.
- [x] 12.09.2026: в секции «Оригинал», alias и «Переводы» добавлены явные UI-подсказки о границах сохранения; словари и `ui_keys.json` синхронизированы, frontend suite — `105 passed`, production build — PASS.
- [x] 12.09.2026: гидрация retrieval-ключей получила order-preserving дедупликацию перед SQL `IN`; regression-набор гидрации и Stage 8 — `31 passed`, `ruff` — PASS. Изменение не меняет выдачу и не закрывает содержательный BM25 SLA само по себе.
- [x] 12.09.2026: браузерная проверка `/admin/glossary` подтвердила корректный auth-gate; содержательный UI smoke остановлен на Keycloak SSO (`AUTH_PROVIDER=keycloak_oidc`), без автоматизации учётных данных. Это ограничение проверки, не PASS содержимого страницы.
- [x] 12.09.2026: после изменения гидрации пересоздан `stage8-manifest-current-20260912.json`; snapshot БД/Qdrant не изменился (`26/205/8018`, `8193` точек), `labels.approved=false` сохранён. Acceptance validator/manifest/labels — `16 passed`.
- [x] 12.09.2026: безопасная попытка UI smoke через отдельный simulation-backend не изменила рабочий `.env`; отдельный Next frontend заблокирован общим `.next`-lock, временный backend остановлен. Рабочий frontend не затронут, ручной UI smoke не засчитан.
- [x] 12.09.2026: в форме создания термина свободный ввод `canonical_locale` заменён общим `ReferenceLocaleSelect`; frontend glossary regression — `17 passed`, ESLint — PASS, production build — PASS (15 маршрутов).
- [x] 12.09.2026: `GLOSSARY.md` дополнен картой системных полей, версий/конкурентного сохранения, служебных audit-полей, собственных версий переводов и автоматического canonical alias при создании.
- [x] 12.09.2026: `/search` получил bounded hydration по фактическому размеру API-сниппета (`300` символов для concept/chunk), без изменения chat-контракта. Свежая off/on-матрица на `252` кейса на сторону завершилась с `acceptance_failures=[]`; первоначальное read-only сравнение с предыдущим on-протоколом не выявило изменений финальных `identity/kind/title`, но после исправления probe-контракта полное сравнение с предыдущим bounded protocol-1 выявило final-only изменения (`10` off, `38` on) при `0` полных raw source losses. Отдельное сравнение показало для `search/bm25` `-6.74%` average и `-6.50%` p95, но это не полный 2-warmup/5-pair SLA и не закрывает gate. Артефакты: `tests/artifacts/stage8/probe-stage8-search-bounded-off-20260912.json`, `tests/artifacts/stage8/probe-stage8-search-bounded-on-20260912.json`, `tests/artifacts/stage8/probe-stage8-search-bounded-quality-report-20260912.json` (`7951 unresolved` без labels).
- [x] 12.09.2026: исправлен probe-контракт: search-кейсы теперь передают в hydration те же `300/300` символов, что и реальный `/search`; TDD-регрессия и весь `test_probe_sources.py` — `14 passed` (1 известное предупреждение pytest). Новый полный протокол (`2` warmup + `5` пар, `210` наблюдений на сторону) дал `3/6` SLA PASS: `search/bm25 +68.43%/+10.762%`, `chat/bm25 +100.687%/+30.628%`, `chat/hybrid +13.251%/+7.771%` (avg/p95). `acceptance_failures=[]`; независимый validator — `READY`, содержательный статус `BLOCKED`. В сравнении с предыдущим bounded protocol-1: полных raw source losses `0/0`, common source `kind/title` changes `0/0`, но final-only source changes `10` off и `38` on — это требует предметных labels. Артефакты: `tests/artifacts/stage8/probe-stage8-search-bounded-summary-20260912.json`, `tests/artifacts/stage8/probe-stage8-search-bounded-sla-recheck-20260912.json`, `tests/artifacts/stage8/probe-stage8-search-bounded-quality-report-20260912.json` (`7951 unresolved`).
- [x] 12.09.2026: после протокола исправлен отсутствующий импорт `build_doc_lookup` в locale-check probe; повторная проверка — `14 passed`, `ruff check` — `All checks passed`, JSON summary/recheck/quality-report — синтаксически валидны. Поведение поиска и производственные настройки не менялись.
- [x] 12.09.2026: manifest пересоздан после выравнивания probe и зафиксировал неизменный snapshot (`26` документов, `205` чанков, `8018` концептов, `8193` Qdrant points); `labels.approved=false`. Read-only `validate_stage8_labels --require-approved` подтвердил `BLOCKED`: `15` quality-кейсов, `0` complete.
- [x] 12.09.2026: добавлена прямая регрессия runtime-контракта `/search` на `300/300` hydration; совместный targeted-набор `test_glossary_search.py` + `test_probe_sources.py` — `23 passed`, `ruff` — `All checks passed`. Runtime-логика после последнего полного SLA-протокола не менялась.
- [x] 12.09.2026: подготовлен дополнительный `draft_unapproved` packet для final-only изменений после search hydration: `75` case-run (`21` off, `54` on), полных raw source losses — `0`; labels автоматически не назначались. Артефакт: `tests/artifacts/stage8/probe-stage8-search-bounded-final-only-review-20260912.json`.
- [x] 12.09.2026: полный frontend regression suite завершён `106 passed, 0 failures`; production webpack build — PASS (Next.js 16.3.4, 15 маршрутов). UX-регрессии прокрутки, locale selectors, save hints и disabled-flag сообщения прошли.
- [x] 12.09.2026: исправлены две UX-регрессии глоссария: предупреждение `GLOSSARY_QUERY_EXPANSION_ENABLED` получило отдельный ключ и явно сообщает, что глоссарий не участвует в поиске; новый алиас очищает черновик только после успешного ответа. Защищён выбор термина от позднего ответа сохранения. Полный frontend suite — `108 passed, 0 failures`; `next build` — PASS (15 маршрутов).
- [x] 12.09.2026: повторный UI smoke `/admin/glossary` снова дошёл до Keycloak `Sign in to myrealm`; без authenticated сеанса сценарий admin/editor/user не засчитан. Данные и рабочая конфигурация не изменялись.
- [x] 12.09.2026: после ручного входа через Keycloak выполнена безопасная часть authenticated UI smoke: `/admin/glossary` показал предупреждение выключенного `GLOSSARY_QUERY_EXPANSION_ENABLED`; в `IT0003` алиасы `инфотип 0003` и `ИТ 0003` исправлены с `root` на `ru` и сохранены; фильтр документов `Russian (1)` оставил один RU-документ и корректно сбросился; история загрузила список и открыла сохранённый тред с сообщениями/источниками. Первоначальный HTTP 500 истории вызван schema drift живой dev-БД (нет `chat_messages.retrieval_metadata`), исправлен идемпотентным `ADD COLUMN IF NOT EXISTS`; миграция `b2c3d4e5f6a7` уже содержит колонку. Полный release smoke, включение флага и предметные query-кейсы остаются открытыми.
- [x] 14.09.2026: живой кейс `ИТ0003` подтвердил context-filter recall regression: on retrieval содержал русский блок `Общие сведения` с `инфо-типа 0003`, но `drop_unmatched_blocks` удалял его после точного совпадения `IT0003` в другом блоке. Доменное совпадение больше не активирует глобальное отсечение без лексического совпадения исходного запроса; TDD RED→GREEN, связанный backend-набор `104 passed, 1 warning`, `ruff` PASS, повторный off/on probe сохраняет русский блок в on top-5.
- [x] 14.09.2026: для запроса `инфо-тип 0003` устранено вытеснение точного добавленного идентификатора лимитом контекста: `IT0003` присутствовал в плане, но `PY-ES` оставался на raw #38–39. Post-RRF-приоритет ограничен буквенно-цифровыми формами из непустых `MatchGroup.matched_forms`, использует identifier boundaries (`IT0003` не совпадает с `IT00037`) и не меняет off/no-match либо обычные текстовые алиасы. API и измерительный probe используют один механизм; TDD RED→GREEN, итоговый glossary/probe-набор `147 passed, 6 warnings`, `ruff` PASS. Живой on probe поднял `PY-ES: Infotypes, Transactions, and Reports` с raw #38 на #1 финального списка; off остался `disabled`.
- [x] 14.09.2026: добавлено системное query-side правило SAP-инфотипов без обязательной карточки: `IT`/`ИТ`, `Infotype`, `Infotyp`, русские слитные и дефисные падежные формы плюс ровно четыре цифры. Карточка при наличии обогащает отображение и нестандартные aliases; один точный код-alias связывает описательную карточку с инфотипом. Транзакции остаются точными управляемыми aliases без распознавания похожих ключей. Переиндексация не нужна. TDD RED→GREEN и read-only code review; исправлены порядок объединённых spans, ложное продвижение по текстовому alias транзакции и разделение системных/сохранённых форм в UX. Стандартные aliases инфотипов больше не расходуют пользовательский лимит и не создают ложный `limited`. Финальная расширенная проверка: backend `246 passed, 6 warnings`, frontend `138 passed`, Ruff и ESLint без ошибок; живой измерительный backend healthy и применяет правило.
- [ ] Утверждены labels источников, пройден последовательный performance SLA и принято решение о выпуске; migration/frontend checks уже закрыты.
- [ ] Этап 8 выполнен целиком — отмечается только после 8.8.

Следующее действие при исполнении: получить утверждение эксперта по пакету
разметки, затем перенести labels в cases и пересчитать Precision/Recall и
отчёт off/on на одной версии разметки; отдельно устранить или обосновать
BM25 performance FAIL до решения о выпуске.
