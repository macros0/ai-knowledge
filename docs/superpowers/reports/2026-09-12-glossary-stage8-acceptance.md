# Stage 8: pre-release acceptance audit

Дата: 12.09.2026  
Статус: **BLOCKED перед выпуском**. Это итоговый технический аудит текущего
рабочего состояния, а не разрешение на включение функции.

Базовая git-ревизия: `abcd880352229d210800cc1c130119ce79e95ea8`. Проверка
относится к текущему working tree; изменения не закоммичены.

## Решение

Release-решение: `GLOSSARY_QUERY_EXPANSION_ENABLED` остаётся `false`.
В текущей локальной копии `.env` флаг временно выставлен в `true` только для
визуальной проверки; отдельный контур `okf_stage8_test` также запускается с
`true`. Это не является выпуском функции и не меняет release-рекомендацию.
Включение запрещено до одновременного выполнения двух условий:

1. предметный эксперт утверждает `relevance` labels источников и seed;
2. после утверждения labels повторный парный протокол проходит performance SLA
   для всех `search/chat × dense/bm25/hybrid`.

## Матрица критериев

| Раздел | Статус | Доказательство / причина |
|---|---|---|
| 8.1 Достоверность probe | PASS | Исправленный код probe/quality проверен профильными тестами (`27 passed`); свежая однопроцессная матрица `252` вариантов на каждую сторону завершилась с `acceptance_failures=[]`, а quality-аннотации сохраняются только для размеченных кейсов. |
| 8.2 Отчёт изменений | BLOCKED на выпуск | Отчёт различает raw/final/source identity; добавлен воспроизводимый расчёт Precision@5/Recall@10. В актуальном bounded quality report `7951 unresolved` событий (в других срезах число меняется); это нельзя закрыть без предметного решения. |
| 8.3 Разметка и матрица | BLOCKED на предметную приёмку | 42 кейса, 252 mode/API-варианта и hermetic fixtures готовы; `labels.approved=false`, обязательные источники для части positive-кейсов не утверждены. |
| 8.4 Парные прогоны | BLOCKED на закрытие качества | Off/on и локализация технических причин выполнены; остаются unresolved relevance cases и BM25 SLA. |
| 8.5 Поисковое качество | BLOCKED | Актуальный bounded protocol-1 quality report дал `acceptance_failures=[]`, но `90` positive/compound сравнений остаются `BLOCKED` из-за отсутствия утверждённых labels; `162` остальных варианта — `NOT_APPLICABLE`. |
| 8.6 Задержки и нагрузка | BLOCKED | Последний валидный bounded search-протокол (210 наблюдений на сторону для каждого API/mode) дал FAIL для `search/bm25` по average/p95 (`+68.43%/+10.762%`), `chat/bm25` по average/p95 (`+100.687%/+30.628%`) и `chat/hybrid` по average (`+13.251%/+7.771%`). SLA recheck: `3/6` пар PASS. Ограничение текста безопасно, но gate не закрывает. |
| 8.7 Регрессия, миграции, rollback | PASS* | Актуальный полный backend suite завершён `1192 passed, 6 warnings, 0 failures`; frontend `108 passed, 0 failures`, production build и PostgreSQL migration/CAS/audit/drift smoke PASS; flag-off rollback проверен. В живой dev-БД при authenticated smoke обнаружен schema drift: отсутствовала `chat_messages.retrieval_metadata`; применён идемпотентный `ADD COLUMN IF NOT EXISTS`, после чего открытие истории прошло. Миграция `b2c3d4e5f6a7` уже содержит эту колонку. `*` Предупреждения — dependency deprecations и завершающий gRPC channel warning, падений нет. |
| 8.8 Выпуск и smoke | BLOCKED | Полный release smoke не выполнялся: labels и SLA остаются незакрытыми, флаг не включался. После пользовательского входа выполнена безопасная часть UI smoke: глоссарий, предупреждение выключенного флага, alias locale/save, фильтр языка и история. |

## Evidence

## Последняя техническая проверка

Перед SQL-гидрацией ключи концептов и чанков теперь дедуплицируются с сохранением
первого порядка появления. Это безопасная оптимизация повторяющихся retrieval-hit
и не меняет ranking/merge-контракт. Профильный набор завершён `31 passed`,
`ruff check` — PASS. BM25 SLA по актуальному acceptance-артефакту остаётся
`3/6` PASS; labels по-прежнему не утверждены, поэтому release gate не закрыт.

После изменения пересоздан текущий reproducibility manifest. Контрольный snapshot
остался прежним: `26` документов, `205` чанков, `8018` концептов и `8193` точек
Qdrant; `labels.approved=false` намеренно сохранён. Набор acceptance
validator/manifest/labels завершён `16 passed`.

До предоставления сеанса маршрут `/admin/glossary` был проверен в браузере до
auth-gate: локальное окружение требует Keycloak SSO
(`AUTH_PROVIDER=keycloak_oidc`), а ввод учётных данных автоматизировать нельзя.
После ручного входа пользователя содержательный smoke выполнен частично; его
результаты приведены ниже.

Для безопасной локальной проверки был поднят отдельный simulation-backend на
временном порту без изменения рабочего `.env`; отдельный Next frontend не
стартовал из-за общего `.next`-lock уже работающего dev-сервера. Временный
backend остановлен, рабочие процессы не затрагивались.

Дополнительно форма создания термина переведена с произвольного ввода
`canonical_locale` на тот же `ReferenceLocaleSelect`, что используется для
алиасов. Frontend glossary regression завершён `17 passed`, ESLint и
production build (15 маршрутов) — PASS.

- [Acceptance plan](../plans/2026-09-11-glossary-stage8-acceptance.md)
- [Main implementation plan](../plans/2026-09-11-glossary-query-expansion.md)
- [Retrieval diagnostic](2026-09-12-glossary-stage8-retrieval-diagnostic.md)
- [Subject-matter labeling packet](2026-09-12-glossary-stage8-labeling-packet.md)
- [Budget trade-off artifact](../../../tests/artifacts/stage8/probe-stage8-budget-tradeoff-20260912.json)
- [Reproducibility manifest](../../../tests/artifacts/stage8/stage8-manifest-20260912.json)
- [Current reproducibility manifest](../../../tests/artifacts/stage8/stage8-manifest-current-20260912.json)
- [Rollback and roles smoke](2026-09-12-glossary-stage8-rollback-roles.md)
- [Migration smoke](2026-09-11-glossary-stage8-migration-smoke.md)
- [Hermetic retrieval fixture](../../../backend/tests/fixtures/stage8_retrieval_documents.json)
- [Matrix quality report](../../../tests/artifacts/stage8/probe-stage8-matrix-quality-report-20260912.json)
- [Fresh single-matrix quality report](../../../tests/artifacts/stage8/probe-stage8-cache2-quality-report-20260912.json)
- [gRPC pair quality re-evaluation](../../../tests/artifacts/stage8/probe-stage8-grpc-quality-report-corrected-20260912.json)
- [same-process harnessfix3 summary](../../../tests/artifacts/stage8/probe-stage8-harnessfix3-summary-20260912.json)
- [same-process harnessfix4 summary](../../../tests/artifacts/stage8/probe-stage8-harnessfix4-summary-20260912.json)
- [harnessfix4 BM25 category breakdown](../../../tests/artifacts/stage8/probe-stage8-harnessfix4-bm25-breakdown-20260912.json)
- [harnessfix4 labeling candidates](../../../tests/artifacts/stage8/probe-stage8-labeling-candidates-harnessfix4-20260912.json)
- [same-process harnessfix5 summary](../../../tests/artifacts/stage8/probe-stage8-harnessfix5-summary-20260912.json)
- [harnessfix5 BM25 category breakdown](../../../tests/artifacts/stage8/probe-stage8-harnessfix5-bm25-breakdown-20260912.json)
- [combined visibility/hydration protocol summary](../../../tests/artifacts/stage8/probe-stage8-combined-hydration-summary-20260912.json)
- [bounded hydration protocol summary](../../../tests/artifacts/stage8/probe-stage8-bounded-hydration-summary-20260912.json)
- [bounded hydration BM25 breakdown](../../../tests/artifacts/stage8/probe-stage8-bounded-hydration-bm25-breakdown-20260912.json)
- [bounded hydration quality report](../../../tests/artifacts/stage8/probe-stage8-bounded-hydration-quality-report-20260912.json)
- [bounded hydration SLA recheck](../../../tests/artifacts/stage8/probe-stage8-bounded-hydration-sla-recheck-20260912.json)
- [Strict SLA artifact validator](../../../backend/test_scripts/stage8_sla_validator.py)
- [Label validator](../../../backend/test_scripts/validate_stage8_labels.py)
- [SLA decision note](2026-09-12-glossary-stage8-sla-decision.md)
- [fresh harnessfix2 quality report](../../../tests/artifacts/stage8/probe-stage8-harnessfix2-quality-report-20260912.json)
- [Recommendations](2026-09-12-glossary-stage8-recommendations.md)

## Контрольная перепроверка приёмочного контура

После первоначального аудита проверены сами защитные инструменты приёмки.
Stage 8 regression-набор завершён `39 passed` (один warning связан только с
правами pytest cache). Полный backend suite после этих изменений завершён
`1188 passed, 7 warnings`, падений нет. SLA validator теперь требует полный набор из шести
пар в CLI, использует авторитетный критерий `1.10/1.10`, сверяет вычисленные
дельты и блокирует пустые, неполные, дублированные или подменённые
артефакты. Проверка текущих файлов: `READY` по структуре, `BLOCKED` по
содержательному SLA, `3/6` пар PASS.

Quality report повторно построен текущим кодом из bounded off/on-артефактов:
`acceptance_failures=[]`, качество `90 BLOCKED` и `162 NOT_APPLICABLE`,
`7951` событий остаются `unresolved` до предметной разметки. Manifest обновлён:
42 кейса, `labels.approved=false`, 26 документов, 205 чанков, 8018 концептов
и 8193 Qdrant-точки. Поэтому техническая проверка усилена, но статус выпуска
не изменён.

Последующая проверка выявила ещё два дефекта в acceptance-коде: принимались
нулевой sample size и отрицательные latency values, а изменение
`expected_documents`/`mandatory_sources` между off/on не блокировалось. Они
исправлены и покрыты тестами; Stage 8 regression-набор после исправлений —
`39 passed`. Quality report и manifest пересозданы текущим кодом. Статус
выпуска не меняется: labels отсутствуют, а фактический SLA остаётся `3/6`.

## Следующий разрешённый шаг

Последующая проверка выявила два дефекта в harness: положительный кейс с
нулевым Recall мог получить `PASS`, а `run_case` не переносил в результат
`expected_documents` и `mandatory_sources`. Оба исправлены тестами в текущей
рабочей копии (`27 passed` в тестах probe/quality/labels). Независимый SLA validator
подтвердил согласованность шести пар, исходных значений и строгих `average/p95`
лимитов (`READY`, содержательный SLA `BLOCKED`, `3/6` пар). Старые quality-артефакты
сохранены для аудита; свежие результаты после исправления приведены в
`probe-stage8-harnessfix2-quality-report-20260912.json` и в summary
однопроцессных протоколов harnessfix3/harnessfix4/harnessfix5. В harnessfix5 все пять
пар завершились с `acceptance_failures=[]`, но quality остаётся `BLOCKED`
для `90` positive/compound вариантов из-за отсутствия labels. Они по-прежнему
не являются выпуском:
качество требует утверждённых labels, а performance-gate не пройден.

После оптимизации hydration добавлена условная загрузка текста чанка: для
группы с несколькими основными концептами, непустым canonical-контентом и
заголовком primary-концепта raw-текст чанка не влияет на `merge_and_format` и
не запрашивается; для пустого контента/заголовка сохраняется fallback.
Профильные тесты прошли `4 passed`, расширенный набор Stage 8 после
объединения сессий — `141 passed, 4 warnings`. В контрольных harnessfix4/harnessfix5 это улучшило отдельные
стадии enrichment/context-filter, но не закрыло BM25 SLA.
Сравнение harnessfix4 и harnessfix5 по 2520 case-run не выявило ни одного
изменения source identity, kind, title или acceptance.

После этого visibility lookup и canonical hydration объединены в одну DB-сессию
для `/search`, `/chat` и probe. Новый полный протокол (2 warmup + 5 пар,
210 наблюдений на сторону) сохранил `acceptance_failures=[]`; относительно
harnessfix5 average/p95 составили: `search/bm25 +72.31%/+16.77%`,
`chat/bm25 +103.88%/+32.16%`. Dense/hybrid и `chat/dense` прошли SLA.
Сравнение protocol-1 старого и нового кода дало `0` различий финальных
identity/kind/title для off и on. Артефакт:
`tests/artifacts/stage8/probe-stage8-combined-hydration-summary-20260912.json`.

В следующей версии canonical text в SQL-запросе ограничен границами фактического
ответа (`4000` символов концепта и `6000` чанка). Это не изменило финальные
identity/kind/title (`0` различий против combined protocol-1), но новый полный
протокол дал `search/bm25 +76.17%/+10.39%` и `chat/bm25 +108.55%/+26.16%`
(average/p95). Поэтому ограничение оставлено как защита от лишней передачи
данных, а не как решение SLA. При критерии плана p95 ≤110% дополнительно
`chat/hybrid` не проходит p95 (`+11.78%`). SLA recheck сохранён в
`tests/artifacts/stage8/probe-stage8-bounded-hydration-sla-recheck-20260912.json`,
исходный протокол — в `tests/artifacts/stage8/probe-stage8-bounded-hydration-summary-20260912.json`.

В первом реальном прогоне после оптимизации появились многочисленные
предупреждения `Чанк (...) не найден в document_chunks — рассинхрон БД и Qdrant`.
Read-only inventory подтвердил `0` отсутствующих ключей чанков и `0`
отсутствующих ключей концептов: причиной был false positive в новой ветке
намеренно пропущенной hydration. Логирование исправлено, а regression-тест
проверяет, что intentional skip не выглядит как drift; реальный drift по этому
прогону не подтверждён.

Эксперт заполняет labels по формату из labeling packet: `case`, полная
идентичность `doc_id/slug/chunk_index`, `relevant|irrelevant`, `mandatory` и
причина. После этого labels переносятся в cases, фиксируется новый manifest,
сначала запускается read-only валидатор
`backend/test_scripts/validate_stage8_labels.py`, затем пересчитываются
Precision/Recall встроенным отчётом и выполняется новый
2-warmup/5-pair protocol. Неразмеченный блок остаётся `BLOCKED`, а не считается
нерелевантным автоматически.
Только при PASS всех критериев разрешены restart backend, release smoke и
финальная отметка «этап 8 выполнен» в обоих планах.

## Актуализация после проверки 12.09.2026

После усиления SLA-валидатора выполнен свежий полный backend suite: `1192
passed, 6 warnings, 0 failures` за `548.44s`. Валидатор теперь отдельно
контролирует заявленный протокол (`2` warmup, `5` пар, `210` наблюдений на
сторону и пустые `acceptance_failures`). Это подтверждает отсутствие
технической регрессии, но не закрывает два содержательных gate: labels ещё не
утверждены, а authoritative SLA остаётся `3/6` PASS.

## Актуализация после проверки bounded search hydration 12.09.2026

Для `/search` SQL-гидратация ограничена фактическим размером выдаваемого
сниппета — `300` символов для concept и chunk. Chat-контракт не изменён.
Свежая off/on-матрица на `252` кейса на сторону завершилась с
`acceptance_failures=[]`. Первоначальное read-only сравнение нового on-прогона
с предыдущим on-протоколом не выявило изменений финальных
`identity/kind/title`; последующее сравнение с корректно выровненным
search-протоколом отдельно выявило final-only изменения (`10` off, `38` on),
но не полные raw source losses. В отдельном сравнении `search/bm25` получил
`-6.74%` average и `-6.50%` p95.

Это контрольный результат безопасности и локального улучшения, а не закрытие
SLA: он не заменяет обязательные `2` warmup + `5` пар + `210` наблюдений на
сторону. Labels всё ещё не утверждены, quality report содержит `7951
unresolved`, а authoritative SLA остаётся `3/6` PASS. Артефакты:

- [fresh bounded off probe](../../../tests/artifacts/stage8/probe-stage8-search-bounded-off-20260912.json)
- [fresh bounded on probe](../../../tests/artifacts/stage8/probe-stage8-search-bounded-on-20260912.json)
- [fresh bounded quality report](../../../tests/artifacts/stage8/probe-stage8-search-bounded-quality-report-20260912.json)

## Уточнение протокола search hydration

При предыдущей свежей off/on-матрице probe ещё не передавал search-кейсам
ограничение `300/300`, хотя реальный `/search` уже его использовал. Это было
исправлено и покрыто тестом; весь `test_probe_sources.py` прошёл как `14 passed`
с одним известным предупреждением pytest.

После исправления выполнен полный протокол: `2` warmup, `5` чередующихся пар,
по `210` наблюдений на сторону для каждой API/mode-пары. `acceptance_failures=[]`.
SLA validator вернул структурный `READY`, но содержательный `BLOCKED`, `3/6`
пар PASS:

- `search/bm25`: `+68.43%` average, `+10.762%` p95 — FAIL;
- `chat/bm25`: `+100.687%` average, `+30.628%` p95 — FAIL;
- `chat/hybrid`: `+13.251%` average, `+7.771%` p95 — FAIL по average;
- остальные три пары проходят.

Сравнение protocol-1 с предыдущим bounded hydration protocol-1 показало
`0/0` полных raw source losses и `0/0` изменений `kind/title` на общих
источниках. При этом есть final-only изменения: `10` кейсов off и `38` on;
источники остаются в raw-пуле, поэтому это не доказанная потеря, но качество
нельзя закрыть без предметных labels.

Новые артефакты:

- [bounded search protocol summary](../../../tests/artifacts/stage8/probe-stage8-search-bounded-summary-20260912.json)
- [bounded search SLA recheck](../../../tests/artifacts/stage8/probe-stage8-search-bounded-sla-recheck-20260912.json)

После протокола также исправлен отсутствующий импорт `build_doc_lookup` в
locale-check probe. Повторная проверка завершилась `14 passed`, `ruff check` —
`All checks passed`, JSON summary/recheck/quality-report — валидны.

Manifest после выравнивания probe зафиксировал неизменный snapshot: `26`
документов, `205` чанков, `8018` концептов и `8193` Qdrant points;
`labels.approved=false`. Read-only `validate_stage8_labels --require-approved`
подтвердил `BLOCKED`: `15` quality-кейсов, `0` complete.

Для экспертной проверки состава final-блоков подготовлен дополнительный
[final-only review packet](../../../tests/artifacts/stage8/probe-stage8-search-bounded-final-only-review-20260912.json):
75 case-run (21 off, 54 on). Полных raw source losses — `0`; старые final-
источники остаются среди raw-кандидатов. Пакет не содержит автоматически
назначенных labels и имеет статус `draft_unapproved`.

Добавлена прямая регрессия runtime-контракта `/search` на `300/300` hydration.
Совместный targeted-набор `test_glossary_search.py` + `test_probe_sources.py`
прошёл `23 passed`, `ruff` — `All checks passed`; runtime после последнего
полного SLA-протокола не менялся.

Полный frontend regression suite после актуальных UX-изменений завершён как
`108 passed, 0 failures`; production build — PASS (Next.js 16.3.4,
15 маршрутов). Дополнительно исправлены две локальные UX-регрессии: сообщение
о выключенном глоссарии отделено от статуса записи, а черновик нового алиаса
очищается только после успешного сохранения; поздний ответ сохранения не меняет
выбранный термин. Проверены прокрутка админского глоссария, locale selectors,
явное сохранение и сообщение о выключенном флаге.

Дополнительная попытка прочитать фактические runtime-настройки через
`GET /api/settings` без сессии завершилась `401 Unauthorized`. Поэтому
`false` подтверждён в конфигурации/исходниках и в статических UI-проверках; полный
runtime-readback и содержательный query smoke с включённым флагом остаются
открытыми и не засчитываются как PASS.

После ручного входа пользователя через Keycloak повторная проверка достигла
содержимого `/admin/glossary`: warning явно сообщает, что
`GLOSSARY_QUERY_EXPANSION_ENABLED` выключен и сохранённые записи не участвуют
в поиске. В редакторе `IT0003` два русских алиаса `инфотип 0003` и `ИТ 0003`
были исправлены с ошибочного значения `root` на `ru`; оба сохранения показали
`Changes saved`. Это исправление данных выполнено только для этих двух
алиасов, остальные записи не менялись.

Проверен фильтр языка документов: `Russian (1)` оставляет один RU-документ,
возврат к `All languages` восстанавливает четыре документа. История чата
загрузила список тредов и открыла сохранённый диалог с сообщениями и
источниками. Первоначальное открытие истории дало HTTP 500 из-за отсутствующей
колонки `chat_messages.retrieval_metadata` в живой dev-БД; после
`ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS retrieval_metadata JSON`
повторная проверка прошла. Это состояние БД сохранено для следующих запусков;
штатный release smoke, запросы при включённом флаге и проверка no-match/
forbidden/compare по-прежнему не выполнялись. Итоговый статус этапа остаётся
`BLOCKED` из-за labels и SLA.

После последних изменений выполнен полный backend regression в отдельном
workspace temp-каталоге: `1196 passed, 6 warnings, 0 failures` за `552.69s`.
Warnings не являются падениями: это deprecation-предупреждения зависимостей и
финальное предупреждение закрытия gRPC-канала.

После временного включения `GLOSSARY_QUERY_EXPANSION_ENABLED=true` пользователь
подтвердил практическую пользу глоссария при визуальной проверке: расширение
запросов действительно влияет на результат поиска. Это зафиксировано как
положительный qualitative UX-smoke результат, но не заменяет предметные labels
и проверку SLA для закрытия этапа 8.
