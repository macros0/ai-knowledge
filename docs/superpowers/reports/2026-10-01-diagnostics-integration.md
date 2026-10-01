# Интеграция административной диагностики с main — 01.10.2026

## Согласованный объём

Сохранить исходную реализацию и evidence, объединить её с main в отдельной
ветке, выполнить функциональную регрессию и один ограниченный эксперимент
по локализации HTTP p95. Исходная performance-приёмка остаётся failed.
Merge в main, push, production и изменение retrieval/GC/порогов не входят.

## Шаги и проверка

1. Snapshot исходного worktree. Выполнено: code491dbc4, evidence4ec9e14;
   127 файлов кода/tests/docs, 274 JSON. Проверки: 46 backend,12 Node,Ruff.
   Старые v16/v11 images и локальные исходные артефакты сохраняются.
2. Новый managed worktree, ветка codex/diagnostics-integration, merge main9cb7748.
   Девять конфликтов разрешаются с сохранением обоих контрактов. Обязательны
   combined streaming sources/progress/error-correlation tests RED/GREEN.
3. Проверки: focused backend, полный backend, полный Node, Ruff, ESLint,
   frontend build; независимый read-only review конфликтов и auto-merges.
   Возможный failure проверяется на исходной базе, статус не подменяется.
4. Отдельный Linux Compose project с pristine PostgreSQL/Qdrant и одним
   synthetic документом. Новый baseline; ordinary search,c1,noZIP,6 интервалов
   off/standard/off/standard/off/standard;30s warmup,>=60s и>=1000 requests.
   Максимум15min measured experiment, без полного повторения матрицы.
   Сопоставить total HTTP, backend, Qdrant, hydration, SQL, connection, commit
   и numeric IO/WAL observations; не сохранять запросы/SQL/ответы/секреты.
   Измерение с profiler является localization, не SLA acceptance.
   Если эффект не воспроизведён, сохранить inconclusive и завершить.
5. HTTP admin start/reproduce/stop/preview/download/CRC/delete, viewer403.
   Stop capture, проверить ready/queues/workers и завершить свои процессы.
   Зафиксировать итог и точные commits, без отправки ветки.

## Решения

- Ruling: работаем с фиксированным main9cb7748; baseline4ec9e14 остаётся
  доступным. Цена ошибки: новый baseline потребуется при смене main.
- Ruling: backend deps/doc-parser не изменены между исходной и объединённой
  веткой; для test image допустим layer поверх frozen backend с merged source.
  Frontend собирается из нового lockfile (Next16.3.8). Это новая версия,
  её цифры не объявляются исправлением причины старого failed результата.
- Ruling: reusable Node deps разрешены только при совпадении lockfile main
  и integration. Production .env/данные в рабочие деревья не копируются.
- Ruling: lifecycle сохраняет честное not_applicable_simulation для CSRF;
  production CSRF остаётся предметом соответствующих backend tests.

## Прогресс

- Conflicts resolved, streaming combined tests RED confirmed.
- Focused90 passed,Node319 passed/1skipped,Ruff passed.
- ESLint0 errors/40warnings,frontend webpack build passed.
- Полный backend прогон завершён; два fixture failures исправлены и проверены отдельно.
- Review исправлен; единичный Linux эксперимент и HTTP smoke завершены, стенд остановлен.

## Результат независимого ревью

Read-only review разрешённых конфликтов и auto-merges нашёл два P2:

- Новые ApiError чата теряли публичный код в operation_failed, а sanitizer
  и frontend contract не знали восемь новых кодов. Добавлен зарегистрированный
  словарь кодов; record_failure сохраняет только allowlisted code, для любого
  другого значения остаётся классификация по типу/status. Негативный canary
  остаётся вне потока. Регрессии воспроизвели 10 failures до исправления.
- Search-scope и cancel attempt не имели route templates и search_chat kind.
  Добавлены точные шаблоны в обоих контрактах; raw attempt ID не сохраняется.
  Проверяется как route template, так и фактический operation_kind.

Оба замечания исправлены. Review подтвердил сохранение main sources/progress,
selected-source контракта и UI error references. Performance, Docker/UI execution
и неизменённые внутренние модули диагностики не входили в статическое review;
их статус определяется отдельными измерениями, а не заключением reviewer.

## Дополнительные проверки и конфигурация стенда

- 54 backend проверки контракта/контекста, 12 API/ZIP и merged route/code
  проверок, 3 privacy/context-reset проверки numeric profiler прошли.
- Финальный Node suite: 320 passed, 1 skipped. Frontend webpack build passed.
- Два падения полного backend прогона: новая main fixture вызывала
  lifespan(None). В объединённой версии lifespan использует app.state.
  Fixture теперь создаёт FastAPI и изолирует диагностический background runtime,
  сохраняя реальную БД и проверки initial seeding/admin edit; 15 focused tests passed.
- Новый Linux project: okf-diag-integration-20261001, localhost:18085;
  отдельные pristine volumes и data/diagnostics mounts. Source runtime tree:
  e323fa0f966cae2d6099eeecd3d94dc80301926b. Более поздние изменения только тестовые.
- Backend image: sha256:17dbaf92358dbbac7b82f731448194bc984183413ec1f748ad7d49a6d73cdce5.
  Frontend image: sha256:ba0f7a01c178201160b43f87cc66f60dd74e808d588d06f445fa87010627b266.
  PostgreSQL/Qdrant image digests совпадают с frozen стендом.
- BuildKit RUN ограничен AppArmor в LXC. Frontend собран в disposable
  node:20-alpine container (apparmor=unconfined), затем COPY-only runner.
  После SIGKILL при 19 build workers build ограничен двумя workers и 1536 MiB,
  webpack build passed. Это test-only build setting, source config не менялся.
- Приватный runtime: development + simulation, baseline enabled.
  Изначальное production environment закономерно отвергло simulation до миграции;
  после исправления environment pristine Alembic migration и ready прошли.
- Числовой profiler запускается только отдельной командой test image;
  штатные app entrypoints не менялись. PG track_wal_io_timing=on и лимиты памяти
  одинаковы во всех интервалах. Данные являются локализацией, не SLA-приёмкой.

Evidence JSON имеет -text Git attribute: bytes/SHA сохраняются на Windows/Linux checkout.

## Итог проверки и единичного эксперимента

Полный backend прогон: **2807 passed, 24 skipped, 2 failed**, 2441.91 s.
Оба failures — описанная выше несовместимая fixture lifespan(None); после
её адаптации оба теста и 13 связанных тестов прошли. Новые integration/profiler
тесты также прошли отдельно. Второй полный 40-минутный прогон не выполнялся:
после первого менялись только fixture и проверки, runtime код не менялся.
Это не объявляется новым полностью зелёным full-suite запуском.
Ruff по всему backend passed; окончательный ESLint 0 errors / 41 warnings; final Node
320 passed / 1 skipped; production webpack frontend build passed.

Один измерительный эксперимент завершён: **21 704 requests**, шесть интервалов,
каждый >=60 s и >=1000 requests, перед каждым 30 s warmup. Полные ответы
побайтно совпали по SHA для каждого synthetic query. Header correlation
проверен; сохранялись только числовые тайминги и response digests.

| Пара | off HTTP p95, ms | standard HTTP p95, ms | Изменение |
|---|---:|---:|---:|
| 1 | 18.486 | 18.335 | -0.82% / -0.151 ms |
| 2 | 17.758 | 18.256 | +2.81% / +0.498 ms |
| 3 | 17.810 | 19.269 | +8.19% / +1.458 ms |

**Причина прежнего +58% сдвига не установлена: эффект не воспроизведён.**
На новом merged runtime нет перехода p95 18→29 ms. Поэтому исходная гипотеза
о PG commit/WAL/IO как причине старого p95 остаётся непроверенной для frozen
v16/v11. Здесь commit p95 2.70–2.74 ms, SQL p95 1.16–1.26 ms; они не показывают
прежнего двурежимного сдвига. Количества WAL sync и суммарный WAL sync time
сопоставимы между режимами. Это не causal proof и не performance acceptance:
изменены main, Next, pristine storage, profiler и test memory/build settings.

Обнаружены девять редких запросов >=100 ms в обоих режимах: HTTP 316–328 ms,
hydration 305–316 ms. В этих же запросах SQL 1.20–1.37 ms, connection 0.14–0.21 ms,
commit 2.24–13.44 ms. Значительная часть ожидания находится внутри hydration,
вне измеренных SQL/connection/commit. Объяснение этого ожидания пока неизвестно;
оно не является воспроизведением прежнего p95. Вложенные тайминги нельзя
складывать: SQL/commit/connection частично включены в hydration.

HTTP smoke после измерений passed:

- search-scope available; missing cancel attempt 404; оба нормализованных
  маршрута присутствуют в search_chat detailed capture без raw attempt parameter;
- admin standard start/reproduce/stop/prepare/preview/download/SHA/CRC/delete;
  viewer 403; deleted download 410. Manifest v2, partial=false, gaps=[],
  оба process loss counters known. Production CSRF здесь не проверяется:
  simulation auth; соответствующие cookie-auth negative backend tests прошли.
- перед shutdown: ready, active capture отсутствует, bundle jobs=0,
  recorder queued/dropped/invalid/storage_errors/drain_timeouts=0;
- новый project остановлен. Backend exited 0, Node/Qdrant exited 143 при SIGTERM,
  OOMKilled=false. Старый v16/v11 и production containers сохраняют свои
  image IDs и started_at, остаются running.

Все семь safe JSON evidence files и SHA manifest сохранены в
`tests/artifacts/diagnostics/integration-20261001/`; SHA сверены после переноса.
Приватный env, spool JSONL, ZIP и raw logs в evidence не включены.
Исходная пятиблочная performance acceptance остаётся **FAILED**.
Node RSS в этом localization эксперименте не измерялся.

## Следующее решение

Сохранять объединённую ветку как новую фиксированную базу. Не менять RAG,
GC или thresholds по этим цифрам. Следующий полезный ограниченный эксперимент
— Node RSS/heap на 1 документе при обычном поиске и ZIP, с одинаковым временем
жизни процессов, числовыми memory/GC observations и сохранением baseline каждого
повтора. Это позволит различить retained allocations и историю V8 heap/RSS.
После установления причины и выбранной правки нужна отдельная performance
приёмка merged release; текущий эксперимент её не заменяет. Редкие hydration
пики можно исследовать отдельным измерением pool/rollback/wait/CPU, если они
существенны для принятого latency критерия.

Merge в main и push не выполнялись. Рабочая main база остаётся 9cb7748.

Локальный integration merge commit: `5841b31` (parents `4ec9e14`, `9cb7748`).
Code/evidence исходной ветки: `491dbc4`, `4ec9e14`.

Evidence commit: `f12cbd2`; финальный ESLint выполнен после него и уточнён в этом отчёте.


## Выполнен следующий bounded memory эксперимент — 01.10.2026

Предложенный выше Node RSS/heap опыт завершён: 8 matched-age режимов,
39 280 измеренных requests, без forced GC/GC tuning. Крупный избыток памяти
capture/ZIP не воспроизведён; heap после natural GC около 30,6–31,0 МиБ.
Память наблюдалась только в этом новом опыте; это не пересмотр предыдущей
latency localization или исходного FAILED. Подробности, ограничения,
проверки harness и конечное состояние:
[Память Node на объединённой версии](2026-10-01-diagnostics-node-memory.md).
Следующий этап — отдельная приёмка merged release без test-only profiler/
observer, с исходными thresholds. Main merge/push не выполнялись.


## Приёмка merged release завершена — 02.10.2026

Обновлённый кандидат (main38f7ed6 включён, runtime57727d9, harnessfdc327b)
прошёл отдельную пятиблочную HTTP-приёмку: 102 series, 324 018 measured requests,
все исходные gates PASSED. Максимальный ordinary p95 overhead 4,58%, ZIP 15,28%;
backend/front RSS delta <=23,56/3,18 МиБ. Полный backend2847passed24skipped,
frontend339passed1skipped; фактическая очистка завершена, собственные контейнеры
остановлены, production/frozen сохранены. Исторический FAILED остаётся прежним;
причина того отказа не установлена. Main merge/push/deployment не выполнялись.
Подробности и safe evidence:
[Приёмка объединённого release](2026-10-01-diagnostics-merged-release-acceptance.md).
