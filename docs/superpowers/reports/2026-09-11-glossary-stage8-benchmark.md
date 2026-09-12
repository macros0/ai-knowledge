# Stage 8: benchmark query-side glossary

Дата: 11.09.2026. Статус: частичная техническая приёмка 8.6.

Артефакт полного прогона: `backend/scripts/glossary-stage8-benchmark-20260911b.json`.
Стенд: отдельная PostgreSQL БД
`okf_glossary_stage8_bench_20260911b`, ровно 1000 активных терминов и 10000
алиасов. Данные рабочего корпуса и рабочей БД не использовались.

Протокол: 2 прогревочных прохода, затем 5 пар с чередованием порядка off/on;
в каждом проходе 2 запроса (один matching, один no-match), всего 10 измерений
на сторону. Время ответа LLM не включалось.

| Сторона | prepare avg/p50/p95, ms | matcher p95, ms | snapshot p95, ms | query sparse p95, ms |
|---|---:|---:|---:|---:|
| off | 0.005 / 0.003 / 0.018 | 0 | 0 | 0.057 |
| on | 1.449 / 1.329 / 2.010 | 1.807 | 0.007 | 0.144 |

Вывод: критерий `p95 prepare вместе со snapshot ≤30 мс` выполнен на целевом
объёме. После оптимизации snapshot переиспользуется до мутации, а matcher
отбрасывает алиасы без точного подстрочного кандидата до regex-проверки.
SQL-счётчик измеренных запросов не растёт с числом алиасов: в steady-state
дополнительных SQL-вызовов нет (`sql_statements=0` в raw samples), так как
используется уже загруженный immutable snapshot.

Ограничение доказательства: этот benchmark покрывает только query-side
glossary preparation (`snapshot`, matcher, `prepare`, `query_sparse`). Dense
embedding, Qdrant, 5 параллельных клиентов, SQL-счётчик холодной загрузки и
сравнение количества embedding/LLM-вызовов требуют отдельного прогона и не
обозначаются здесь как принятые.

## Параллельный smoke

Отдельный read-only прогон на сохранённом стенде: 5 клиентов × 20 запросов на
сторону, всего 100 запросов off и 100 on после прогрева snapshot.

| Сторона | Ошибки | SQL после прогрева | avg/p50/p95, ms | wall, ms |
|---|---:|---:|---:|---:|
| off | 0 | 0 | 0.001 / 0.001 / 0.002 | 0.795 |
| on | 0 | 0 | 1.896 / 1.014 / 2.104 | 112.905 |

Критерий 5 параллельных клиентов для query-side части выполнен: ошибок,
таймаутов и роста SQL-запросов не обнаружено. Это не является доказательством
производственной ёмкости полного retrieval, поскольку dense embedding, Qdrant и
postfilter в этот прогон не входили.

## Повторная проверка окружения 12.09.2026

После подтверждения stale-lock и согласованного исправления ACL каталога
`C:\postgresql17\data` portable PostgreSQL был запущен на `127.0.0.1:5432`.
Изменялся только доступ к каталогу данных; содержимое рабочей БД не
изменялось. В отдельном свежем процессе на benchmark-БД получено:

| Состояние | Распознанные термины | SQL statements |
|---|---:|---:|
| cold off | 0 | 0 |
| cold on | 1000 | 5 |
| warm on | 1000 | 0 |

Это подтверждает отсутствие SQL-запроса на каждый термин/alias: cold `on`
делает фиксированную загрузку snapshot, а после прогрева дополнительные
SQL-вызовы отсутствуют.

## Parallel retrieval smoke 12.09.2026

Запущен read-only прогон существующего retrieval pipeline: 5 клиентов × 20
запросов, режим `chat/hybrid`, отдельно для `GLOSSARY_QUERY_EXPANSION_ENABLED`
off/on. Ответ LLM намеренно не генерировался; область прогона — preparation,
embedding, Qdrant, visibility, hydration, merge и chat-фильтры контекста.
Артефакт: `backend/scripts/probe-stage8-parallel-retrieval-20260912.json`.

| Сторона | Запросы | Ошибки | Acceptance failures | SQL statements | Embedding calls | avg/p50/p95, ms |
|---|---:|---:|---:|---:|---:|---:|
| off | 100 | 0 | 0 | 298 | 100 | 90.644 / 82.051 / 229.772 |
| on | 100 | 0 | 0 | 304 | 100 | 95.364 / 90.368 / 131.189 |

Параллельный smoke прошёл по ошибкам, таймаутам и acceptance-проверкам.
Число embedding-вызовов одинаково (`100/100`); LLM-вызовы не выполнялись по
замыслу теста. SQL-счётчики включают штатные visibility/hydration-запросы и
не являются производственным capacity-SLA; отдельный SLA для параллельной
нагрузки исходной спецификацией не задан.

Дополнительно тот же retrieval-only harness выполнен по полной матрице
`search/chat × dense/bm25/hybrid`: по 42 кейса на сторону для каждой пары.
Во всех 12 ячейках ошибок `0` и `llm_calls=0`. Embedding calls совпали off/on:
`42/42` для dense и hybrid, `0/0` для BM25. Это подтверждает, что glossary
preparation не добавляет LLM-вызовов и не включает dense embedding в BM25;
генерация ответа намеренно не запускалась.

Матрица сохранена в том же артефакте в поле `external_call_matrix`.

## Reproducibility manifest guard 12.09.2026

Manifest guard сохранён до и после короткого retrieval-only smoke:

- `backend/scripts/stage8-manifest-before-20260912.json`;
- `backend/scripts/stage8-manifest-after-20260912.json`;
- основной снимок: `backend/scripts/stage8-manifest-20260912.json`.

После добавления Qdrant payload projection основной снимок пересоздан, чтобы
его hash исходников соответствовал текущему коду. Поэтому before/after guard
выше относится к предыдущему read-only окну, а основной manifest — к текущей
ревизии с projection.

Сравнение всех обязательных отпечатков завершилось `manifest_guard PASS`:
изменений нет (`cases`, labels, glossary snapshot/seed, выбранные исходники,
settings/models, документы/чанки/концепты и Qdrant points). В manifest явно
зафиксировано `labels.approved=false`: это фиксирует версию текущей разметки,
но не выдаёт инженерный список `unresolved` за предметно утверждённый.

Итог 8.6 остаётся частично открытым: cold SQL и параллельная техническая
проверка закрыты, но последовательный authoritative SLA общего retrieval всё
ещё не проходит для `search/bm25` и `chat/bm25`, а предметные relevance labels
не утверждены.
