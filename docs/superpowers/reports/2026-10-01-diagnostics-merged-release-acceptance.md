# Приёмка объединённой версии диагностики — 01–02.10.2026

Статус: **PASSED** — пять HTTP-блоков, исходные числовые критерии,
функциональные проверки и фактическая очистка подтверждены. Это отдельная
приёмка нового кандидата;
исторический FAILED frozen v16/v11 остаётся без изменений.

## Версии и воспроизводимость

- Объединённая ветка: `codex/diagnostics-integration`; main `38f7ed6`
  включён merge-коммитом `c5717f5`. Main checkout, push и production deployment
  в эту работу не входят.
- Runtime source: `57727d9974a73a3451a8f787f9582343b94462ad`.
- Только измерительный harness: `fdc327bcffe004c6aed2fc096d543aa0ed027dda`;
  SHA256 probe: `d65285c8297c513e2a8f3336f4b715ed728e925ff5d3054953f3fc8e0bd3277f`.
- Backend image: `sha256:ac7f713a109f100e013e37ff86b51be3fdc0ee9aac1b4eac9008ba3c4944aceb`.
- Frontend image: `sha256:15aafe8f31facdf95f66c5f6052045fa28f76474f2abb1bb6386c310b4f4b64b`.
- Архив исходников: `aa0833ad19e3e1e239d9a20815295f6115748589dc4025cd39e15afca3a89496`;
  frontend lock: `70ec9567a9257ab02338a3aa4e98842857e6b1079dcf686bc718e3768a21a292`.

CT102, отдельный project `okf-diag-release-57727d9-rssfix`, новые volumes/data/spool,
frontend `127.0.0.1:18085`. Backend использует штатный diagnostic_entrypoint/uvicorn,
frontend — штатный `node diagnostics-runner.mjs`. Profiler, NODE_OPTIONS,
forced GC и runtime heap flags не применялись. Baseline включён.
Лимиты памяти: backend 1024, frontend 768, PostgreSQL/Qdrant по 384 МиБ.
Между сериями и блоками контейнеры не перезапускались; image IDs,
source/env и started_at проверялись перед каждым блоком.

Backend dependency tree не менялось относительно frozen base image;
новый app layer содержит объединённый runtime. Frontend production webpack
build использовал 2 workers и build-only heap 1536 МиБ. Зависимости frontend
переиспользованы по идентичному lock после нормализации CRLF; этот build flag
не попал в runtime. Стенд development/simulation, apparmor=unconfined для LXC.

## Проверки кода

Полный backend на runtime `57727d9`: **2847 passed, 24 skipped, 21 warnings**,
exit 0, 2453,58 s. После harness-only изменения отдельно: **61 targeted passed**;
это не новый полный backend-прогон и не сумма независимых тестов.
Frontend: **339 passed, 1 skipped**. Ruff passed; ESLint 0 errors/41 warnings.
Production frontend build exit 0, включая Linux CT102 сборку.

Whole-branch review обнаружил 3 Important/P2, все исправлены с RED/GREEN:

1. Некорректный baseline мог дать ложную приёмку: теперь до сравнения p95/RSS
   обязательны время, число запросов, положительные метрики, известные нулевые
   потери, равные ответы и непустые fingerprints.
2. Неизмеримая память живого/неизвестного child process больше не считается
   нулём: такая серия отклоняется.
3. Ошибки загрузки/сохранения разметки документа показывают безопасные
   request/local report IDs через настоящий ErrorReference; 4 JSX regression cases.

Первый полный backend на 16bb493: 2846 passed, 24 skipped, 1 failed
(error-code coverage). Он выявил использование raw chat error literals
вместо существующих констант. Исправлено на `errors.CHAT_*`; AST после разрешения
значений констант совпадает, API wire codes/статусы и RAG поведение сохраняются.
После исправления 111 focused tests и полный backend прошли.

## Дефект измерителя и сохранённая первая попытка

Первый запуск на том же runtime оборвался после 9 из 18 ordinary series
на `Child RSS unavailable`. Это неполная матрица без performance verdict,
а не пройденная приёмка и не численный отказ по p95/RSS. Cleanup/lifecycle
завершились; собственные контейнеры остановлены, частичные JSON сохранены.

Ограниченная Linux-проверка воспроизвела 10 exit_mm windows в 33 child processes:
RSS уже недоступен, stat ещё R, затем process становится Z/исчезает.
Точный PID исходного отказа не был сохранён; healthcheck — возможный источник,
но это не доказано. ESRCH/ENOENT теперь означают подтверждённый exit. Только
при недоступном child RSS допускаются до трёх пауз по 1 ms для нового RSS или
подтверждения exit; живой/неизвестный процесс после этого по-прежнему отклоняет
измерение. Это номинальный sleep budget, не realtime гарантия 3 ms.

Проверка исправленного измерителя: 1345 children, 208317 RSS checks,
4029 missing reads, 1331 alive-during-gap, **0 observation failures** за 12 s.
Сохранён исходный archive/source manifest; новая чистая попытка имеет отдельный
root и harness SHA. Runtime images и все численные критерии остались прежними.
Численный performance failure повторным прогоном ради PASS не обходился.

## Критерии и результаты

Каждая серия: >=30 s warmup, >=60 s измерений, >=1000 requests;
3 повтора, concurrency 1 и 8. Обычный p95 overhead <=10%, с ZIP <=20%;
peak RSS delta backend <=128 МиБ, frontend <=32 МиБ. Восемь обязательных
критериев: p95, RSS, duration/requests, losses, ZIP overlap, response equality,
capture counts и phase distributions. ZIP сравнивается с no-ZIP baseline
того же corpus/repeat/concurrency, включая off+ZIP. Пороги, GC и RAG не менялись.

HTTP p95 измерен клиентом через frontend/API на синтетическом корпусе.
Peak RSS — наблюдаемый максимум с периодом около 20 ms, с учётом child processes;
это не гарантия обнаружения каждого краткого пика. Внешний PVE observer имеет
период 2 s. Их собственная стоимость отдельно не измерялась. Короткие prepare/zip
фазы дают небольшие выборки запросов, поэтому phase p95 не следует обобщать на
длинные сборки или иной корпус. API polling с периодом 3 s влияет на время
обнаружения готовности ZIP; build_seconds не равно фактической сумме фаз.

UI здесь проверен JSX/Node тестами и production build; отдельный браузерный
click-through не выполнялся. HTTP lifecycle использует simulation auth:
это не production SSO/cookie-CSRF smoke. Реальные cookie-auth negative tests
входят в полный backend-прогон.

| Блок | Серии | Requests | Макс. p95 overhead | Backend RSS Δ, МиБ | Frontend RSS Δ, МиБ | Итог |
|---|---:|---:|---:|---:|---:|---|
| 1 документ, обычный | 18 | 75 652 | +4.58% | +0.27 | +2.24 | PASSED |
| 1 документ, active ZIP | 24 | 101 198 | +7.23% | +19.32 | +1.14 | PASSED |
| 100 документов, обычный | 18 | 44 202 | +2.03% | +0.20 | +3.18 | PASSED |
| 100 документов, active ZIP | 24 | 58 630 | +2.92% | +23.29 | +1.77 | PASSED |
| 100 документов, stopped ZIP | 18 | 44 336 | +15.28% | +23.56 | +1.08 | PASSED |

**PASSED: 102 series, 324 018 измеренных requests**, все 8 gates
в каждом из 5 блоков. Окно UTC: 2026-10-01T19:19:45.218688+00:00 → 2026-10-01T21:55:08.169327+00:00;
Москва: 01.10 22:19:45 → 02.10 00:55:08, около 2 h 35 min.
Каждая полная матрица повторно проверена локальным validate_matrix;
вычисленные analysis совпали с перенесёнными JSON. Первая 9-row попытка
этим же validator отклонена как incomplete. Нулевые losses известны для
обоих процессов, equality/capture counts/обе фазы ZIP подтверждены.

Внешний PVE observer: 4663 samples, 0 errors; минимальный host
MemAvailable 5839.0 МиБ; swap-in/out Δ
159/0 страниц. Численные pressure/cgroup
observations сохранены отдельно. Это контекст измерений, а не доказательство
причины прежнего FAILED. CT102 memory.events high вырос на 529;
OOM/oom_kill не увеличились ни в CT101, ни в CT102.

## Очистка и конечное состояние

После последней серии /health/ready вернул 200, backend/frontend ещё running
с исходными image IDs/started_at. Post-lifecycle: SHA/CRC, admin lifecycle,
reader 403 и deleted download 410 passed; manifest v2, partial=false, gaps=[],
оба loss counters known. Capture выключен, recorder queued/dropped/invalid/
expired_queue/storage_errors/drain_timeouts/shutdown_timeout=0.

Локальная сверка обнаружила дефект cleanup в одноразовой очереди: она читала
только первую страницу списка (limit=20), а API сохраняет deleted tombstones.
Исходный cleanup-before-stop.json сохранён: bundle_jobs=20 означало число
записей первой страницы и не доказывало нулевую очередь. Полный запрос к БД
обнаружил 29 ready и 21 deleted. Через штатный DiagnosticBundleQueue.delete
удалены оставшиеся 29 тестовых ZIP. Теперь live jobs=0, ZIP files=0,
50 deleted tombstones остаются штатной историей. Это отдельное
postmeasurement-cleanup.json; исходные доказательства не переписаны.

Для очистки использованы отдельный временный PostgreSQL с volume только
этого проекта и служебный Python process из того же backend image; исходные
4 контейнера не перезапускались. Временный контейнер и private env удалены.
Ни thresholds, ни результаты, ни runtime code не менялись. Правило для
будущего cleanup: обходить все страницы и считать состояния jobs отдельно
от retained metadata, затем проверить количество архивных файлов.

Собственный project остановлен: backend/PostgreSQL exit 0,
frontend/Qdrant exit 143 при SIGTERM; OOMKilled=false у всех.
Production и frozen v16/v11 сохраняют image IDs/started_at и running —
проверено до измерений, после shutdown и после дополнительной очистки.

## Доказательства и решение

Safe JSON: `tests/artifacts/diagnostics/merged-release-20261001/`.
41 файл исходного экспорта проверен побайтно после переноса; архив SHA256
`39cbf7ad7340b94c3c0d0af391e7b957e209392fe0c0e73d1349f37961aedfe5`.
Отдельное доказательство завершённой очистки SHA256
`d0f38318a755c60c315714189b57674414b7bcede611e7a3c0d95738feb99a9a`.
JSON bytes закреплены правилом .gitattributes -text и сверяются с Git snapshot.
Дополнительно сохранены reviewer/focused checks, AST contract, harness fix,
local strict validation и общий file SHA manifest. Приватный env, документы,
БД, raw logs, spool JSONL и ZIP bodies не перенесены.

Численные блокеры зафиксированного кандидата на main38f7ed6 закрыты.
Во время замеров main продвинулся до cde0724 (31 файл: контекстные действия
документов и вкладка загрузки; commit 02.10 00:42 MSK). На финальной сверке
02.10 01:11 MSK main был чистым; он изменён параллельной работой, а не этим
планом. Этот коммит не входит в runtime57727d9 и не проверен этой приёмкой.

Рекомендация перед слиянием в актуальный main: включить cde0724 в integration,
проверить пересечения DocumentList/SelectionBar, локализации и стилей;
выполнить frontend suite, production
build и UI smoke новых действий документов. Зафиксировать новый source/image
для окончательного release; текущий PASSED относится к указанным здесь SHA.
GC/RAG/threshold tuning по этим результатам не требуется. До production нужен
отдельный smoke реального SSO/cookie-CSRF на release конфигурации.
Причина исторического +58%/большого Node RSS на frozen v16/v11 не установлена;
новый PASSED не является причинным объяснением того отказа.

Main merge, push и deployment не выполнялись. Ветка и worktree сохранены
для выбранного следующего шага; последний наблюдавшийся main — cde0724.
Snapshot состояния и пересечения файлов: repository-at-finish.json.


## Решения процесса

Использован существующий worktree. Windows ledger/brief заменяют bash helpers;
спецификация — согласованный план и исходные критерии. Выполнен один свежий
whole-branch review и один проход исправлений Important; Minor не отложены.
CT performance проверяется фактическими измерениями, RAG quality/full security
аудит вне этой приёмки. Полный функциональный gate обязателен перед 100doc.
Исправление Linux RSS race относится только к harness, сохраняет строгий отказ
для неизвестной памяти; первоначальная неполная попытка сохранена отдельно.
Безопасный архив передан напрямую в CT102, без файла на промежуточном PVE host.
Приватный env, данные документов, БД, ZIP bodies и raw logs в evidence не входят.
