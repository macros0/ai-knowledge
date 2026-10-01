# Уровни административной диагностики: журнал исполнения и приёмки

## Итог оставшейся приёмки — 01.10.2026

**Все пять оставшихся блоков сняты. Общая приёмка производительности — FAILED.**
Использованы неизменные образы backend v16 / frontend v11, исходная команда
`node diagnostics-runner.mjs` и включённая baseline-диагностика. Завершены
102 серии и 323 369 измеренных запросов за 2 ч 35 мин 48 с
(14:43–17:19 МСК). После решения пользователя новых оптимизаций и повторов
не выполняли. Предыдущие failed-результаты сохранены. Exit 0 означает успешное
завершение сбора; общий журнал прямо хранит `acceptance=failed`.

| Корпус / сценарий | Серии / запросы | Максимум p95: прибавка / мс | Прирост RSS backend / Node, МиБ | Результат |
|---|---:|---:|---:|---|
| 1 документ, обычная нагрузка | 18 / 74 587 | +58,490% / +10,941 | 0,203 / 39,777 | failed: p95 и RSS |
| 1 документ, active ZIP | 24 / 102 302 | +58,355% / +10,722 | 25,145 / 67,223 | failed: p95 и RSS |
| 100 документов, обычная нагрузка | 18 / 44 862 | +7,152% / +8,481 | 0,934 / 10,672 | passed |
| 100 документов, active ZIP | 24 / 57 917 | +10,454% / +12,446 | 1,488 / 1,789 | passed |
| 100 документов, ZIP после stop | 18 / 43 701 | +5,076% / +6,306 | 0,332 / 2,168 | passed |

Пороги сохранены: p95 +10% для обычной нагрузки, +20% для ZIP;
RSS backend +128 МиБ, Node +32 МиБ. Во всех пяти блоках пройдены проверки
длительности, числа запросов, совпадения ответов, точности счётчиков,
отсутствия непреднамеренных потерь и применимых пересечений фаз ZIP.
Повторный расчёт всех пяти analyses полностью совпал с сохранённым;
проверены точные наборы серий и digest обоих образов.

Исходные JSON, analyses, status и review каждого блока, общий журнал,
числовой PVE observer и конечное состояние сохранены с проверкой SHA в
`tests/artifacts/diagnostics/`. Ранее отдельно пройдены native TTL/квоты
(3 теста), 60-секундный recorder storm и HTTP lifecycle. Статус ZIP ready
в матрице не означает CRC каждого архива: CRC/SHA/manifest, права и удаление
проверены отдельным lifecycle smoke. Storm — нагрузка частного recorder
с параллельной проверкой живого API, а не 10 000 HTTP-ошибок в секунду.

### Что доказано и что остаётся неизвестным

- Отказы на 1 документе сохраняются. Максимальный прирост Node RSS
  +67,223 МиБ наблюдался при **capture off + ZIP**. Причина пиков пока
  не установлена; утечка памяти и зависимость от уровня capture не доказаны.
  Ранее выполненный stopped ZIP для 1 документа также остаётся failed.
- Для 100 документов исходные сравнения с baseline своего повтора прошли.
  Разброс baseline p95 между повторами был существенным: active ZIP
  16,702% / 9,203% при concurrency 1 / 8, stopped ZIP 17,099% / 10,571%.
  Поэтому эти результаты ограничивают вывод о стабильности задержек.
  Дополнительное сравнение active ZIP с самым быстрым baseline даёт
  максимум +24,523%. Это диагностический расчёт, который не меняет
  исходные критерии и результат сравнения внутри каждого повтора.
- Фазовые p95 на 100 документах: active prepare до 562,832 мс
  (минимум 5 samples), zip до 180,567 мс (минимум 4);
  stopped prepare до 540,845 мс (минимум 5), zip до 532,277 мс
  (минимум 4). Это малые условные выборки запросов, пересёкших фазу;
  отдельного SLA для них в плане нет.
- Наблюдатель PVE сохранил 4 674 samples за 9 348,43 с без ошибок.
  Первые четыре блока прошли без новых swap-out и событий memory.high/low.
  Swap-out начался **16:56:29 МСК, в последнем stopped100doc блоке**:
  host swap-out 453,566 МиБ, swap-in 265,164 МиБ. MemAvailable опускалась
  до 3,167 ГиБ; CT101 memory.high +287, CT102 memory.low +1643;
  OOM и достижения memory.max не было. CT101 достигал 7,937 ГиБ,
  CT102 — 3,987 ГиБ. Погрешность границ окон около 3 с.
- В двух failed-блоках на 1 документе MemAvailable была не ниже
  5,899 ГиБ, новых swap-out и указанных событий давления не было.
  Позднее давление на память не объясняет эти ранние отказы.
  Причинность p95/RSS и стабильность RAM/IO на весь прогон не установлены.
- Swap current CT101: начало 168,129 / максимум 490,582 / конец 276,750 МиБ;
  CT102: начало и максимум 41,121 / конец 41,090 МиБ. Старые страницы
  CT102 остаются при swap.max=0. Host swap counters не приписываются
  целиком одному контейнеру без отдельной проверки.

### Конечное состояние и рекомендации

Capture выключен, runtime доступен. Recorder queued/dropped/invalid/
expired_queue/storage_errors/drain_timeouts равны 0. Нагрузочные процессы,
ZIP/prepare workers и незавершённые пакеты отсутствуют. Использовано
12 747 903 из 503 316 480 bytes квоты; storage_degraded=false,
unsafe_paths=0, last_failure отсутствует. Reserved 63 865 bytes — остаток
baseline append credit (65536−1671); работающий baseline writer может
сохранять этот резерв, поэтому reserved=0 не заявляется.

Тестовый backend `/health/ready` — HTTP 200. Тестовый и рабочий frontend
18084/8080 — HTTP 200; оба backend имеют Docker healthy, рестартов/OOM нет.
Тестовый frontend не имеет отдельного Docker healthcheck. Сохранены образы,
исходная команда Node.js и включённые baseline flags.

LLM в CT101 работает: RAM 8 ГиБ / swap 512 МиБ. CT102: RAM 5 ГиБ /
swap 0 / memory.low 4 ГиБ / memory.min 0; memory.low родительского lxc —
4 ГиБ. CT100 и VM103 остановлены. Постоянная конфигурация памяти проверена
статически; перезагрузки не было. Тестовый стенд остаётся на отдельном
синтетическом корпусе из 100 документов. Прежние volumes и данные сохранены;
рабочую production-конфигурацию не переустанавливали.

**Рекомендация:** завершить текущую итерацию с подготовленным функциональным
результатом и явным performance-блокером. Дальнейшие настройки GC, БД или
RAM требуют установленной причины. Semi-space=4 отклонён и не принят.
Если расследование понадобится, выделить отдельную ограниченную по времени
задачу общей производительности HTTP/БД/Next.js и фиксировать нагрузку CT101.
Выбирать изменения по причинным данным; исходные пороги сохранять.
Готовность к production не объявляется.

Фактический статус регрессии: последний полный Windows-набор v15 завершён
с exit 0 при неизменных 433 Python-файлах; итоговая summary не сохранена,
поэтому точные counts не приводятся. Для v16 пройдены targeted prepare
(24 теста), RSS-harness (26) и independent native TTL (3).
Полный набор v16 и GitHub CI не запускались. Итоговые Ruff всего backend
`--no-cache` и `git diff --check` прошли; остались только CRLF warnings i18n.
Commit/push/merge/production не выполнялись. Task11: сбор завершён,
приёмка failed. Выпуск и оставшиеся проверки Task12 остаются открытыми.

Ниже сохранён исторический журнал; текущий итог приведён выше.

## Решение пользователя: завершить оставшуюся приёмку без новых оптимизаций, 01.10.2026

Пользователь выбрал завершение оставшихся обязательных matrices на frozen
backend v16/frontend v11, original `node diagnostics-runner.mjs`, baseline on.
Новые tuning, повторы failed blocks и изменения HTTP/DB/RAG не выполняются.
Снять native 1doc normal/active и fresh100doc normal/active/stopped; завершённые
matrices с failed p95/RSS сохраняются failed, но не блокируют сбор следующих.
Incomplete matrix, ответы/потери/квоты/integrity и неизвестные setup errors
останавливают очередь. Пороги 10% normal /20% ZIP, RSS128/32 МиБ неизменны.
Предыдущие failures не отменяются; итоговое performance acceptance остаётся failed.

Runtime preflight: test health/ready HTTP200; production health HTTP200,
production health/ready HTTP404 (старый production API), Docker healthy и
frontend8080 HTTP200. Production readiness не выдаётся за passed и не исправляется.


### Завершён native normal1doc v16/v11

18/18 rows,74 587 measured requests,exit0; исходные numerical gates:
`all_p95_pass=false`, `all_rss_pass=false`; остальные6 gatestrue.
Max p95 overhead58,490% (detailed/c1/r1), absolute10,941 ms;
frontend RSS growth39,777 МиБ (standard/c8/r1), второй failedRSS
34,406 МиБ (detailed/c8/r3). Backend growthmax0,203 МиБ.
C1 baselines18,706/29,240/18,457 ms: заметная изменчивость безcapture,
причинность не доказана. Counts/equality/lossespassed.
Артефакты `tests/artifacts/diagnostics/http-1doc-normal-pve-v16-v11-final-20261001/`:
rawmatrix,analysis,status,review; SHA remote/local совпали.
Block сохранёнfailed; по выбранной пользователем политике начатactive1doc,
повторnormal или tuning не запускается.

### Завершён native active1doc v16/v11; fresh100doc начат

24/24 rows,102 302 measured requests,exit0. p95/RSSfalse, остальные6 gatestrue.
Max p95 overhead58,355% (standard/c1/r3); baseline+ZIP/c1/r3 такжеfailed
24,597%. Frontend RSSgrowth67,223 МиБ — **baseline+ZIP/c8/r2 безcapture**.
Backend growthmax25,145 МиБ. Это не доказательство причины роста памяти.
C1 reference18,329/18,455/18,373 ms; c8 95,380/102,159/95,125 ms.
Фазовые conditional distributions:prepare min87 samples/maxp95136,695 ms;
zip min10/maxp95124,065 ms. У фаз нет отдельного SLA; samples не подменяют
минутный критерий. ZIP overlaps/status и counts/equality/lossespassed.
Rawmatrix/analysis/status/review экспортированы с совпадениемSHA в
`tests/artifacts/diagnostics/http-1doc-active-pve-v16-v11-final-20261001/`.

Fresh synthetic100doc storage создан отдельно, прежние volumes/data сохранены.
Migrate imagev16, backendv16/frontendv11,originalNode,baselineon. Compose
up--waitpassed; seed100passed; DB exactly100 synthetic canonical IDspassed.
Normal100doc запущен12:48:03UTC. Product/production/thresholds не менялись.

### Завершён native normal100doc v16/v11

18/18 rows,44862 measured requests,exit0; all8 numerical gatestrue.
Max p95 overhead7.152%, max absolute+8.481 ms.
Backend/Node RSSgrowth max0.934/10.672 МиБ.
Counts/equality/lossespassed. Raw/analysis/status/review экспортированы
с совпадениемSHA в `tests/artifacts/diagnostics/http-100doc-normal-pve-v16-v11-final-20261001/`.
Этот pass не отменяет failed1doc и предыдущие failures. Active100doc
начат13:15:28UTC; после него только ранее не выполненный stopped100doc.

### Завершён native active100doc v16/v11

24/24 rows,57917 measured requests,exit0; all8 original numerical gatestrue.
Max matched p95 overhead10.454%, absolute+12.446 ms;
backend/Node RSSgrowth max1.488/1.789 МиБ.
Counts/equality/losses/overlaps/distributionspassed. Raw/analysis/status/review
экспортированы с SHA в `tests/artifacts/diagnostics/http-100doc-active-pve-v16-v11-final-20261001/`.
Фазовые conditional p95 maxima:prepare562.832 ms
(min5 samples),zip180.567 ms
(min4 samples); отдельного phase SLA нет.

Baseline p95 spread c1/c8=16.702/9.203%.
Дополнительный diagnostic-only расчёт к fastest baseline даёт max
+24.523%; он не меняет original paired gates
и не заменяет baseline. Изменчивость ограничивает причинные выводы.
Overall performance acceptance остаётсяfailed по1doc/историческим failures.
Последний ранее не выполненный stopped100doc начат13:51:59UTC.

## Исторический срез перед завершением оставшейся приёмки, 01.10.2026

**Реализация сохранена; performance acceptance не закрыта. Новые общие
повторы и tuning не запускаются до решения по дальнейшей области работ.**

- Full repeat прежней configuration:18 rows/79 496 requests, все p95 gates
  passed; frontend standard/c8/r2 RSS+47,047 МиБ при лимите32 — failed.
- Numeric profile подтверждает V8 heap variation также при capture off.
  Semi-space4 bounded A/B/A дал меньший RSS, но candidate block отклонён:
  standard/c1 p95 29,210 vs18,017 ms (+62,120%, лимит20%). Сохранены4 complete
  rows/15 209 requests; owned probe прерван SIGINT, это **не полный block**.
  Product Node default не изменён, frontend command восстановлен.
  Failed-minute IO full PSI≈8,197% vs baseline2,796%, погрешность границ≈3 s;
  RAM available>=6,044 ГиБ, new swap-out/OOM0. Причинность не доказана.
- Independent native v16 TTL/quota:3 passed (4 existing fixture/deprecation
  warnings). Storm:60,000 s/599998 attempts/written,9999,958 attempts/s;
  RSS growth2,359 МиБ, disk8 299 170/16 777 216 bytes, losses0, queued/reserved0.
  Health/search58/58 successful each, max153,779/31,896 ms. Это private
  recorder storm + live availability через frontend, не10k HTTP failures/s.
- Final HTTP lifecycle v16/v11 passed:manifestv2, partialfalse/gaps[], SHA/
  component checksums/CRC/exact7 entries, admin access, reader403, delete410.
  CSRF в simulation неприменим; production CSRF отдельными tests, не этим smoke.
- Baseline on/off1-doc/c1:3567/3602 requests, p95 18,629/18,297 ms;
  observed difference0,333 ms (1,819%), одинаковые responses. Одна пара —
  instrumentation control, не доказательство causal overhead и не SLA baseline.
  RED выявил отсутствующую передачу DIAGNOSTICS_BASELINE_ENABLED во frontend.
  **Исправлен docker-compose.yml: передать существующий флаг, defaulttrue.**
  GREEN: оба Docker flagsfalse, backend capabilityfalse, Node startup written0;
  bundled/external config render при true/false —4 checks passed. Services
  этих rendering checks не запускались. Private env остаётся на CT mode600.
- Restored current state:baseline on/capture off/runtime available;
  backend/frontend imagesv16/v11, original commands. Test и production backend
  healthy, frontend18084/8080 HTTP200; LLM101active, CT100 иVM103 stopped.
  CT1018GiB/swap512MiB, CT1025GiB/swap0/memory.low4GiB/min0 сохранены.
  Оставшийся reserved у running backend соответствует baseline append credit
  (65536−1671=63865 bytes); prepare/ZIP worker отсутствует. Не заявлять
  reserved0 для постоянно работающего baseline writer.

Remaining: current native1doc normal/active и100doc normal/active/stopped,
100doc ещё не seeded; full regression/CI фактический статус открытый.
Native TTL/storm/lifecycle и отдельный baseline-off control теперь covered
в указанных областях. Existing failed evidence и числовые gates сохранены.
Remaining queue guard отклоняет failed/incomplete candidate до любых mutations;
она локально подготовлена, не запущена/не deployed. Ни commit/push/production
не выполнялись. Product memory/DB/RAG новые изменения не вносились.

Ruling: independent correctness gates были отделены от performance prerequisite,
чтобы выполнить обязательную авторизованную работу без бесконечных matrices.
После failed candidate дальнейшее product tuning без причины было бы догадкой;
по systematic-debugging нужен выбор: завершать оставшуюся приёмку frozen
configuration с честным failed status либо расширять scope на общий HTTP/DB/
frontend performance. Исходные SLA thresholds не изменяются при обоих вариантах.


## Semi-space=4: bounded candidate и следующий полный блок, 01.10.2026

Matched-start default и4 измерены отдельно по3 c8 cases (baseline→stopped
standard ZIP→baseline), всего31 232 requests. Default frontend peaks
214036480/242458624/277549056 bytes,4:212602880/215052288/206970880.
Standard growth к baseline-before27,105→2,336 МиБ; относительно меньшего
из обоих baseline у4 +7,707 МиБ. Standard p95 default94,306/4 95,020 ms;
к своему fastest baseline+2,776%/+3,258%. Counts/equality/ZIP ready passed.
B control complete; original command restored, capture off/runtime available/
recorder queued,dropped,storage_errors0; env/mount fields/images совпадают.
Setup failures до B нагрузки сохранены отдельно, default JSON SHA подтверждён.
Одна A/B/A на treatment не доказывает устранение редкого failed RSS.

Ruling: Task11 позволяет настраивать startup values по before/after.
Разрешён один full affected block на candidate runtime semi-space4, без
изменения numerical gates128/32 МиБ и20% p95, frozen v16/v11 image contents.
Это новая runtime configuration с measured обоснованием; третьей общей
попытки на прежней configuration нет. При failed candidate не запускать
другие GC tweaks или broad retry без нового решения; вернуть original command.
Product Dockerfile/default не меняется до результата full candidate.
100-doc/tail required/open; следующий queue guard должен проверять именно
candidate result/runtime fingerprint, не переписывать исторический failed repeat.


## Node runtime trial: setup corrections, 01.10.2026

Default branch measured before B setup: baseline/standard/baseline frontend
peak214036480/242458624/277549056 bytes; p95 94,133/94,306/91,759 ms,
15 696 requests. Baseline-after peak больше ZIP, capture off; это признак
изменчивости RSS, не доказательство причины исходного failed case.
Первый B setup был отклонён до нагрузки: Next process.title перезаписывает
argv; child /proc/cmdline непригоден для проверки execArgv. Reference:
https://nodejs.org/download/release/v20.20.2/docs/api/process.html#processtitle
Второй B setup остановлен до нагрузки из-за порядка Mounts Docker list.
В v3 все mount fields сравниваются после сортировки по destination.
Flag proof — parent argv + same Node executable + documented fork default
execArgv; прямую проверку child execArgv этим методом не заявлять.
https://nodejs.org/download/release/v20.20.2/docs/api/child_process.html#child_processforkmodulepath-args-options

Исходный frontend command восстановлен после обоих setup failures;
после второго это отдельно подтверждено Docker inspect (running, imagev11,
node diagnostics-runner.mjs, прежние mount destinations). Setup stages
не выдаются за failed performance cases. Default reference не переписан и
не повторяется; B продолжен как отдельный v3 stage, SHA reference в status.
Финальный default restoration обязателен, production/другие services не менялись.


## Числовой frontend memory profile завершён, 01.10.2026

`frontend-numeric-memory-v16-v11-20261001`: 3 c8 cases,15 656 requests,
p95 baseline/standard/baseline93,382/92,943/92,097 ms; frontend RSS peaks
222863360/216539136/210939904 bytes. ZIP ready, counts exact; capture off,
runtime available, recorder queued/dropped/storage_errors0; observer errors0,
inspector closed. Сам профиль влияет на Node, это не SLA acceptance.
Safe numeric samples и review сохранены; heap dumps/env/object text отсутствуют.

PID1 — launcher, а diagnostic recorder находится внутри Next.js server:
прежнее название collector в numeric fields означает launcher, не writer.
Launcher RSS46727168 bytes стабилен. В первом baseline Next memoryUsage RSS
106037248–174473216 bytes, heapTotal46759936–115965952, heapUsed34940712–83539912;
external<=3760029 bytes, arrayBuffers<=187584. Old space max71270400 bytes,
new space33554432. Это подтверждает изменчивость V8 heap также без capture,
но не объясняет причинность конкретного исторического +47,047 МиБ отказа.

Один bounded matched-start experiment проверяет только semi-space=4:
пересоздать test frontend на default, затем на4; в каждом варианте
baseline→stopped standard ZIP→baseline, c8/30 warm/60 measure/>=1000 requests.
Нет inspector и ручного GC. Command/actual fork flag проверяются; образы/env/
mounts одинаковы; finally восстанавливает исходный command и runtime health.
Это localization, не третья общая попытка, не принятие нового production default.
Node20.20.2 docs: https://nodejs.org/download/release/v20.20.2/docs/api/cli.html#--max-semi-space-sizesize-in-mib
Флаг меняет память young generation и может влиять на throughput; вывод только
по measured results. Full acceptance/100-doc/tail остаются открытыми.


## Единственный full repeat завершён: frontend RSS fail, 01.10.2026

`http-1doc-stopped-pve-v16-v11-shared-vm-repeat1-20261001`:
12:34:25–13:01:50 МСК, 18 rows / 79 496 measured requests.
Все p95 gates passed; maximum growth относительно своего baseline +2,343%.
Общий acceptance failed: standard/c8/repeat2 frontend RSS +47,047 МиБ
(281055232 vs 231723008 bytes), лимит32 МиБ. Backend growth в этой строке
200704 bytes; p95 92,469 vs93,070 ms. Counts/equality/losses/ZIP phase gates
passed, capture off/runtime available/queued,dropped,storage_errors0;
frozen v16/v11 images unchanged. Результаты экспортированы в safe artifacts.
Предыдущий p95 spike не повторился; прежний failed block не отменяется.

Третья общая попытка не запускается. Следующая native очередь заблокирована
all_rss_pass=false. Выполняется один короткий frontend memory profile:
раздельные RSS/private memory collector и Next.js server + numeric V8 stats
на synthetic c8 baseline→stopped standard ZIP→baseline. Inspector только
в network namespace тестового frontend, без published host port, heap dumps,
env и содержимого объектов. Этот профиль не SLA acceptance; инструмент
может влиять на память/тайминги. Node GC flags пока не меняются.
100-doc/tail required/open; commit/push/production deploy не выполнялись.


Дополнительная проверка full repeat: c8 baseline p95 spread10,926%, но
даже относительно самого быстрого c8 baseline maximum capture growth2,343%.
PVE MemAvailable min5,965 ГиБ; new swap-out0, CT101/102 high/max/OOM deltas0;
IO full PSI2,635%. Физический NVMe test writes: PostgreSQL881688576 bytes /
210806 IO; backend5005312 /767, frontend2289664 /488, Qdrant0. Эти deltas
не доказывают причину frontend RSS fail. Данные сохранены в review.json.


## WAL-sync контроль и единственный full repeat, 01.10.2026

`wal-sync-aba-v16-localization-20261001` завершён12:21:59 МСК:
3 rows,10 562 measured requests, baseline/standard/baseline p95
18,853/18,812/18,571 ms; spread baseline1,520%, standard относительно
быстрого baseline+1,299%. Всплеск28 ms не повторился. C1 fsync p95
во всех трёх cases2,350 ms, p50≈1,89 ms; IO full PSI≈2,62–2,67%.
WAL sync за warmup+measure:5230/5233/5308, WAL bytes476102/666489/470134.
Сохранены15 856 allowlisted numeric sync events, unparsed completed0;
raw trace private и не экспортирован, tracer detached, observer errors0.
Capture off, runtime available, queued/dropped/storage_errors0, images unchanged.

Из-за формы `return {...}` в установленной старой версии probe первое
добавление optional timelines изменило только signature: timelines в этом
контроле отсутствуют. Его HTTP distributions/WAL/syscall метрики остаются
валидными, per-request причинность по нему не заявляется. Выравнивание
case-time использует host samples с погрешностью границы≈1,1 s. Исправлена
только optional serialization; native GREEN на113 запросах:113 timelines,
p95 полностью пересчитывается из них. SHA текущего probe:
d71a3b1376faf7fd7c6172dfbc2f7eb8920550891bc40be1bda69f9e9beaff24.
Исторические результаты не переписаны, отдельный functional artifact сохранён.

Постоянный WAL-sync присутствует также в baseline. Этот контроль не доказывает,
что замена RAG FOR SHARE необходима для устранения +9,149 ms failed case.
Изменения retrieval hydration не выполняются. Tracing меняет тайминги;
A/B/A остаётся localization, не SLA acceptance.

Ruling по разделу8: после одного failed case разрешён один полный повтор
того же affected block. `http-1doc-stopped-pve-v16-v11-shared-vm-repeat1-20261001`
запущен12:34 МСК;18 cases, исходные gates, тот же RSS method/images/config.
Вне приложения дополнительно читается numeric cgroup io.stat тестовых
backend/frontend/PostgreSQL/Qdrant. При повторном отказе третья общая попытка
автоматически не запускается; потребуется отдельное решение по блокеру.
Следующая native очередь подготовлена и прошла AST/Ruff, но не запущена:
normal/active1-doc, затем fresh isolated100-doc volumes/RO mounts и три100-doc
матрицы. Предыдущие volumes сохраняются. Storm/baseline-off/final lifecycle
required/open. Ни Git commit/push, ни production deploy не выполнялись.


## Исправленный full block: RSS pass, один p95 fail, 01.10.2026

`http-1doc-stopped-pve-v16-v11-shared-vm-20261001` завершён
12:02:39 МСК, 27 мин25 с:18/18 rows, **79 357 requests**.
Семь gates true; **all_p95_pass=false**, общий результат failed.
RSS прошёл во всех строках: backend max+24,777 МиБ, frontend+13,770 МиБ;
unknown VM comparisons0. Естественный shared-VM spike в этом block не
наблюдался; исправление дедупликации отдельно доказано native fixture.
Baseline spread c1 4,914%, c8 1,962%; frozen images v16/v11 unchanged.
MemAvailable min5,991 ГиБ, new swap-out0, OOM kill0, IO full PSI2,707%.

Единственный failed case: standard/c1/repeat3, p95 28,234 vs baseline19,085
ms: **+47,940%, +9,149 ms** при исходном лимите20%. p50 почти одинаков:
15,987/15,890 ms. В том же repeat detailed/c1 p95 18,163 ms; предыдущие
standard/c1 p95 18,242 и18,099 ms. Штатные losses0, counts exact,
ответы одинаковы, ZIP ready, обе фазы пересекают реальные requests.
101/3276 requests пересекали prepare/ZIP; наиболее медленные интервалы
распределены также после завершения ZIP. Это не доказательство устойчивой
добавки9 ms от diagnostics и не основание считать failed case прошедшим.

Сопоставление PVE numeric metrics по проверенным совместимым monotonic
часам (погрешность границ до3 s): failed-minute IO full PSI5,145% против
baseline2,911% и соседнего detailed2,816%. RAM CT101 стабильна, в failed
minute MemAvailable>=6,052 ГиБ. Корреляция IO есть; источник/причинность
ещё не установлены. Safe results/analysis/execution/PVE metrics сохранены
в `tests/artifacts/diagnostics/http-1doc-stopped-pve-v16-v11-shared-vm-20261001/`.
Capture off, runtime available, queued/dropped/storage_errors0 после block.

Ruling: новых общих матриц по этому отказу пока нет. Один ограниченный
`wal-sync-aba-v16-localization-20261001` (baseline→stopped standard ZIP→baseline,
c1,30 warm/60 measure/min1000) записывает numeric request timelines и
fsync/fdatasync durations только изолированного PostgreSQL. Tracing меняет
тайминги; этот контроль **не SLA acceptance**. SQL-тексты/env не экспортируются,
сырой syscall trace остаётся private. Конфигурация PostgreSQL, RAG и образы
не менялись; tracer снимается в finally.100-doc/tail required/open.

CT probe добавлен только optional retain_timelines, уже существующий в
ветке; request loop и RSS helpers не менялись. Новый SHA94534ef2fb1467028315f52ed27147b80597635ced57a885e70e970c50d1ca7f.
При чтении прежнего probe Python нормализовал смешанные CRLF/LF:
normalized-text SHA065bc4f1536f72e3cf4612ad15230187169c0e9ba2eac3973ebc2b1aa37a25d7
отличается от ранее записанного raw SHA6fcf6bfc3bdbe55651a68eb0f6bc3abcee3371b6910b84c9db24e0eff189be71.
Исторический fingerprint полного block сохраняется; приложение не пересобрано.


## Исправленный RSS: bounded GREEN, full repeat RUNNING, 01.10.2026

`rss-shared-vm-control-v16-20261001`: 11:24–11:29 МСК, 3/3 rows,
15 760 measured requests, все восемь проверок true, не полный SLA block.
Baseline c8 p95 90,669 ms; detailed93,449 (+3,066%, +2,780 ms),
standard94,151 (+3,841%, +3,483 ms). Backend growth0,395/0,594 МиБ;
frontend growth−1,328/−10,336 МиБ. Неизвестных VM comparisons0;
capture off, runtime available, recorder queued/dropped/storage_errors0.
Frozen v16/v11 images unchanged. Данных для отмены прежнего frontend
failed результата нет; его повторяемость проверяется полным block.

Native synthetic spawn regression с независимым observer:
`rss-shared-vm-native-spawn-observer-v2-20261001` passed,
172 shared-VM samples. Например, raw362733568 bytes / unique181366784
bytes: общий VM учитывается один раз. Отдельные workers и unknown comparisons
проверены focused tests. Это функциональная проверка измерителя, не SLA.

Все промежуточные fixtures сохранены: задержка exec не поймала shared VM;
внутрипроцессный Python observer пропустил его (GIL); независимый observer
первой версии дал591 shared sample, но fixture overall failed из-за обычного
завершения target PID. Во второй версии граница завершения target явно
проверена через bounded wait; измеритель root RSS по-прежнему fail-closed.
Продуктовый prepare.py получил только поправку ошибочного комментария
о копировании физической RSS; runtime image/hash не изменены.

Ruling: bounded gates и native VM-accounting regression достаточны для
одного полного affected repeat. `http-1doc-stopped-pve-v16-v11-shared-vm-20261001`
запущен 11:35 МСК:18 cases/3 repeats/c1,c8,30 warm/60 measure/min1000,
исходные восемь gates,128/32 МиБ, p95+20%. Изображения приложения не меняются;
дополнительные сборки/pytest/trace во время block не запускаются.
100-doc и tail остаются required/open и не стартуют до результата.


## Полный v16 block и исправление учёта RSS, 01.10.2026

`http-1doc-stopped-pve-v16-v11-20261001` завершён 10:49 МСК:
18/18 строк, 80 236 measured requests, 27 мин 25 с. Семь из восьми
исходных gates passed; **RSS failed**, поэтому общая приёмка failed.
Max p95 growth 3,284% при лимите20%; baseline spread c1 4,545%, c8 2,762%.
Counts exact, ответы одинаковы, losses0, ZIP ready с наблюдаемыми фазами.
Backend detailed/c8 r1/r2: raw growth379,094/380,367 МиБ. Frontend
standard/c8 r1: +37,824 МиБ при лимите32 МиБ. MemAvailable min5,933 ГиБ,
OOM/high/max/new swap-out0. Frozen v16/v11 images не менялись.
Safe evidence: `tests/artifacts/diagnostics/http-1doc-stopped-pve-v16-v11-20261001/`.

Ограниченная локализация `rss-v16-localization-20261001`: шесть коротких
случаев, 3336 requests, не SLA acceptance. Два high-RSS наблюдения:
KCMP_VM=0, errno0; parent и child используют одно адресное пространство.
Raw сумма856965120/856981504 bytes, тогда как cgroup memory.current
385150976/385380352 bytes. После exec child RSS7,00/7,52 МиБ.
Cgroup charged-memory peak414347264 bytes, рост30064640 bytes (~28,67 МиБ).
Трассировались только clone/clone3/vfork/fork изолированного backend;
сырой trace остаётся private на PVE. Экспортированы только числовые сведения.
Это подтверждает двойной учёт общего VM, а не выделение второй физической
копии RSS parent. PSS-сумма сама по себе не решает дедупликацию общего VM.
См. [KCMP_VM](https://man7.org/linux/man-pages/man2/kcmp.2.html) и
[glibc2.41 posix_spawn](https://raw.githubusercontent.com/bminor/glibc/release/2.41/master/sysdeps/unix/sysv/linux/spawni.c).

Отдельный frontend baseline-контроль: три одинаковых baseline/c8 без ZIP,
15 848 requests, frontend peak spread1,836 МиБ. Это не объясняет отказ
+37,824 МиБ; frontend RSS gate остаётся открытым.
Safe evidence: `tests/artifacts/diagnostics/frontend-rss-baseline-control-20261001/`.

Ruling: исправляется ошибка измерителя, числовые лимиты backend128/frontend32
МиБ и остальные исходные gates сохраняются. RSS считается по уникальным
адресным пространствам, доказанным KCMP_VM; исходная raw сумма сохраняется
рядом. При exec между сравнениями RSS отдельного worker перечитывается.
Неизвестное сравнение сохраняет консервативную raw сумму. Недоступный RSS
PID1 приводит к отказу измерения, не к нулю. Старые failed blocks остаются
failed; пересчёт старых JSON без VM-наблюдений запрещён.

RED: четыре новых regression cases failed из-за отсутствующего helper;
дополнительный missing-container-RSS case failed до защиты от нуля.
GREEN: 26 focused tests passed (probe contracts/control gates), Ruff passed;
один существующий StarletteDeprecationWarning. Full suite в этой проверке
не запускалась. CT102 syscall capability подтверждена. Установлен только
probe, без rebuild приложения: old SHA17050c818b7721bbb4331e7ce9f8ce3aaa7f4b27d400da1a337a820857df2a51,
new SHA6fcf6bfc3bdbe55651a68eb0f6bc3abcee3371b6910b84c9db24e0eff189be71;
исходный probe сохранён рядом как pre-shared-vm backup.

Запущен ограниченный `rss-shared-vm-control-v16-20261001`: c8,
baseline/standard/detailed, warmup30/measure60/min1000, stopped ZIP,
frozen v16/v11. Перед full repeat требуется его проверка, включая frontend.
100-doc/tail ещё не запускались. RAG и production не менялись.


## Ограниченный контроль v16 и переход к полному block, 01.10.2026

`http-aba-v16-v11-20261001-r2` завершён10:19:59 МСК, 9 мин07 с,
6/6 rows, **26 763 measured requests**, exit0. Baseline→stopped standard
ZIP→baseline, c1/c8, warmup30/measure>=60/min1000; runtime images v16/v11.

| Concurrency | Baseline before / after p95, ms | Baseline spread | ZIP p95, ms | Growth vs faster baseline |
|---|---|---|---|---|
| 1 | 18,328 /18,000 | 1,820% | 18,216 | 1,195% |
| 8 | 90,846 /91,189 | 0,379% | 94,561 | 4,090% |

Оба ранее объявленных допуска baseline<=10% выполнены; оба ZIP ready,
counts exact, обе фазы видимы, ответы одинаковы, losses0. MemAvailable
min5,964 ГиБ, OOM/high/max/new swap-out0; host IO full PSI2,468% времени.
Это допуск среды и целевое измерение, не полный SLA.

Backend RSS growth относительно меньшего пика двух baselines: c1−0,051
МиБ, c8+0,680 МиБ. Frontend c8 ZIP peak242868224 bytes против baseline
before207622144: **+33,613 МиБ**, выше original limit32 МиБ. Последующий
baseline имеет ещё больший peak305405952 bytes. Причинность роста RSS
не установлена; full RSS gate не объявляется passed по этому контролю.

Первый `http-aba-v16-v11-20261001` не начал ни одной строки: HTTP400
/api/auth/simulate для ошибочного actor diag.bench19. В private test config
есть только01–18. Runner исправлен на существующих16/17; это setup failure,
не business5xx. Оба evidence сохранены в одноимённых safe directories.
Runner использовал устаревшие status keys для финальных runtime/session:
его поле capture_off и runtime_available не служат доказательством.
Окончательный status будет проверен корректными keys после full block.

Ruling: стабильный контроль допускает один полный затронутый stopped ZIP
block на новой сборке v16/v11; предыдущий failed v15 block сохраняется.
Запущен `http-1doc-stopped-pve-v16-v11-20261001`:18 cases/3 repeats/c1,c8,
прежние durations и восемь original gates, Node32/parent+child128 МиБ,
p95+20%. Следующие100-doc/tail только после полного pass. Новые сборки,
pytest и auxiliary Docker exec внутри backend во время измерений не идут.
RAG и production не менялись.

## Перепроверка выводов и рекомендуемый следующий шаг, 01.10.2026

Повторно прочитаны исходные JSON full v15 block, v16 smoke и SQL pilot,
критерии раздела 8 плана, prepare launcher/test и retrieval hydration.
Новые нагрузочные прогоны и изменения RAG в рамках этого review не выполнялись.

1. Исправление prepare launcher обосновано реальным RED/GREEN тестом
   posix_spawn и коротким CT smoke. Прирост RSS27,191 МиБ подтверждён
   именно этим smoke; общий критерий RSS на полном v16 block ещё открыт.
2. Full v15 block формально failed по исходным порогам. Он не доказывает,
   что diagnostics создаёт устойчивые +60–64% HTTP latency: baseline c1
   менялся17,784–26,328 мс (spread48,040%), а в repeat3 standard и detailed
   были около18,10 мс. Медленные запросы первого failed standard case
   преимущественно вне worker phases. Это не исключает отложенный IO-эффект,
   но причинность не установлена. Допуск среды ранее проверял только c8.
3. SQL pilot подтверждает цену текущего протокола чтения на одном документе:
   c1 выигрыш примерно2,38–2,46 мс в SQL-срезе. Проценты46–89% нельзя
   переносить на полный HTTP-путь или считать доказанным исправлением
   +11 мс failed HTTP p95. Row lock используется также в baseline. Замена
   на REPEATABLE READ меняет взаимодействие с параллельной публикацией и
   удалением; согласованный снимок и прежняя видимость не тождественны.
   См. [PostgreSQL17 isolation](https://www.postgresql.org/docs/17/transaction-iso.html).

Предыдущая рекомендация сделать разрешение RAG-изменения обязательным
следующим решением была преждевременной. RAG остаётся отдельным кандидатом
на оптимизацию; его изменение не является условием продолжения diagnostics.
Открытый запрос о расширении scope не блокирует предложенный ниже контроль.

Рекомендуется один ограниченный диагностический A/B/A на v16: baseline →
stopped standard ZIP → baseline, отдельно c1 и c8, всего6 rows; прежние
30 s warmup / >=60 s measure / >=1000 requests. Ожидаемо около10–12 минут
без setup. Зафиксировать образы/нагрузку и снимать числовые host metrics
извне backend cgroup. До старта объявить: baseline spread<=10% для каждой
concurrency — только допуск измерения, а не ослабление SLA. При дрейфе>10%
или business5xx остановить расширение матрицы и сохранить конкретный blocker;
ещё один круг общей оптимизации без новой локализации не начинать.

При стабильных controls проверить v16 полным затронутым stopped ZIP block
по прежним gates и затем закончить оставшиеся обязательные 100-doc/tail и
финальную проверку lifecycle. Уже пройденные проверки не повторять без
затронутого поведения. Этот порядок не сокращает Definition of Done и
не объявляет Windows или короткий smoke полной Linux-приёмкой.

## Результат PVE block и две причины для дальнейших действий, 01.10.2026

Полный `http-1doc-stopped-pve-ram-v15-v11-20261001` завершён
04:39:52 МСК, 27 мин 26 с, **18/18 строк / 78 029 measured requests**.
Exit 1: `all_p95_pass=false`, `all_rss_pass=false`, остальные **шесть**
gates true. Все ZIP ready, overlap/обе фазы видимы, capture counts exact,
losses0, ответы одинаковы. Failed p95 standard/c1:
repeat1 18,370→29,420 мс (+60,157% / +11,051 мс), repeat2
17,784→29,227 мс (+64,344% / +11,443 мс). Единственный RSS отказ:
standard/c1/r1 backend parent+child growth378,469 МиБ при 128 МиБ.
Фронтенд во всех строках укладывается в прежние 32 МиБ.

Baseline c1 тоже дрейфует: 18,370 / 17,784 / 26,328 мс,
spread48,040%; baseline c8 spread2,454%. Короткий admission по c8
не гарантировал стабильность c1 в длинном block. Host MemAvailable
min5,91 ГиБ, новых OOM/high/max/swap-out0; images/лимиты неизменны,
VM103 stopped. Следующие 100-doc/tail не запущены. Этот failed block
не усредняется с passed Windows evidence и не объявляется приёмкой.

Safe evidence:
`tests/artifacts/diagnostics/http-1doc-stopped-pve-ram-v15-v11-20261001/`.
Во время поздних строк выполнены ограниченные read-only SQL observations
отдельными Python processes внутри backend cgroup. Они могли попасть в
суммарный RSS observer; это дополнительное ограничение интерпретации.
Первый RSS/p95 отказ произошёл до этих SQL observations. Числовой
IO sampler и auxiliary SQL queries не являются доказательством причинности.

### RSS: подготовка всё ещё запускается через fork/exec

В первой failed RSS строке backend child peak425 967 616 bytes =406,23 МиБ соответствует
RSS основного процесса; наблюдение согласуется с кратким fork/exec.
Проверен код: ZIP launcher уже имеет POSIX spawn options, prepare launcher
оставлял cwd и default close_fds, блокируя этот путь. Новый тест реального
prepare worker подтвердил RED (`posix_spawned=[]`), затем исправление
применяет существующую ZIP policy: POSIX без cwd, close_fds=False,
module root в PYTHONPATH; Windows cwd/creationflags и PYTHONDONTWRITEBYTECODE
сохранены. Это целевое исправление диагностики, разрешённое исходным
планом после нового подтверждённого дефекта, не изменение RAG.

Наблюдаемые GREEN результаты: Linux Docker33 tests passed /50,73 с,
Windows prepare8 passed /12,73 с. Реальные workers, quota protocol,
cleanup и paths проверены. Первая попытка parallel orchestration потеряла
вывод из-за ошибки имени JS variable; для наблюдаемого результата команды
повторены с отдельными basetemp. Сети и performance workload при tests не было.
Минимальный source archive30 КиБ из четырёх проверенных файлов передан
в CT102; собран v16 поверх exact v15. Только prepare.py меняется в runtime.
CT image: `sha256:0a805ae89c3d42360981e170d845d150445912bb277946a52d5b75d9506de388`.
В установленном CT v16 целевой набор дал **24 passed /24,03 с**;
это установленная версия CT tests плюс новый prepare test, а не повтор
всех 33 локальных Linux tests.

Целевой RSS smoke завершён 05:06:48 МСК, 1 мин 47 с, exit0:
baseline +3 standard stopped ZIP, c1, 10 s warmup /15 s measure,
**3 570 measured requests**. Все три ZIP ready, обе фазы пересекались
с запросами, counts exact, equality true, потери/invalid/storage_errors0.
Максимальный прирост RSS относительно этого baseline: backend
**27,191 МиБ**, frontend **4,223 МиБ**; backend child peak52,199 МиБ.
Скачок378,469 МиБ в трёх случаях не воспроизведён. p95 baseline18,329 мс,
standard18,638 /18,420 /18,981 мс. Это короткий smoke на одном документе
и одном concurrency, **не полный SLA** и не замена failed v15 block.
Во время измерения auxiliary Python внутри backend cgroup не запускался;
обычный Docker healthcheck остаётся частью runtime.

Safe evidence:
`tests/artifacts/diagnostics/prepare-spawn-v16-ct102-smoke-20261001/`.
После smoke capture off, recorder queued/dropped/storage_errors0,
runtime available. Контроль 05:12 МСК: test и production backend healthy,
обе frontend HTTP200; CT101 llama-server active, VM103 stopped/onboot0.
Лимиты CT1018 ГиБ и CT1025 ГиБ/swap0/memory.low4 ГиБ сохранены;
rollback metadata JSON валиден. Production images не менялись.

### IO: shared row locks при чтении поиска

В failed standard/c1/r1 minute IO full PSI9,30% против2,78% baseline,
memory full PSI0; 20 slowest не пересекают child phases. Prepare/ZIP
conditional p9519,16/18,49 мс ниже minute p9529,42 мс. В отдельном
61-секундном срезе тестовый PostgreSQL записал38,32 МиБ; это наблюдение
позднее первой failed минуты и не устанавливает её источник однозначно.
Обычные журналы не экспортировались: сохранены только строго распознанные
числа checkpoint. Первый checkpoint завершился до первой failed минуты;
объяснять оба отказа только checkpoint нельзя.

В работающем frozen backend подтверждён
`app/services/retrieval_hydration.py:405`, `with_for_update(read=True)`;
SHA файла совпадает с worktree. WAL stats отдельного 3-секундного окна:
270 Heap/LOCK и269 Transaction/COMMIT. Row locks способны вызывать
запись на диск — [PostgreSQL17 locking](https://www.postgresql.org/docs/17/explicit-locking.html).
Настройки test PG: fsync=on, synchronous_commit=on; они не изменялись.

Ограниченный read-only **SQL protocol pilot**, 04:42–04:45 МСК,
12 строк /3 повтора/c1,c8/5 s warmup/10 s measure, 270 846 requests:

| Concurrency | FOR SHARE p95, ms (3 repeats) | REPEATABLE READ snapshot p95, ms |
|---|---|---|
| 1 | 2,760 /2,705 /2,701 | 0,304 /0,304 /0,321 |
| 8 | 7,595 /7,441 /7,443 | 3,969 /3,992 /3,939 |

Ответы трёх SELECT одинаковы на неизменном синтетическом документе.
Insert-LSN delta включая warmup/background: shared596–603 КиБ (c1),
3,71–4,05 МиБ (c8); snapshot0–264 bytes. Cumulative wal_records лагируют
между cases, поэтому не использовать их как точную атрибуцию —
[PostgreSQL17 statistics](https://www.postgresql.org/docs/17/monitoring-stats.html).
Safe evidence `tests/artifacts/diagnostics/retrieval-lock-pilot-20261001-v1/`.
Это сравнение SQL reads, **не полный HTTP SLA и не publication-race proof**.
Код поиска и постоянные настройки БД не менялись.

### Подготовленное решение для согласования области работ

Предлагается отдельно разрешить целевое изменение
`backend/app/services/retrieval_hydration.py`: одна PostgreSQL read-only
REPEATABLE READ transaction для metadata/pointer/canonical content,
без shared row lock в этой операции. Writer lock и публикация остаются
прежними; SQLite read snapshot сохраняется. Isolation задаётся до первого
SQL — [SQLAlchemy2 transaction isolation](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#setting-isolation-for-individual-transactions).

Перед применением обязательны реальные PostgreSQL race tests: публикация
между metadata/pointer/text SELECT, удаление, stale Qdrant generation,
rollback writer; никогда не смешивать metadata и текст разных generations.
Проверить legacy/source hydration и отсутствие чтения вне той же SQL
transaction. Затем bounded HTTP control, и только при стабильных c1/c8 —
затронутые acceptance blocks. Откат — вернуть текущий read lock, без
миграции/изменения данных. Это расширяет замороженную задачу на RAG
consistency boundary; до решения пользователя изменение не выполняется.

## Продолжение на новом бюджете PVE, 01.10.2026, 04:12 МСК

Пользователь поручил продолжить. Перед общей нагрузкой проверена
повторяемость baseline c8: три повтора по 30 с прогрева / >=60 с измерения,
15 952 measured requests, p95 **90,733 / 91,649 / 92,820 мс**,
spread max/min **2,300%**. Exit 0, ответы одинаковы; лимиты RAM и images
не изменились, VM103 stopped. MemAvailable min 5,92 ГиБ; OOM/new
swap-out0, swap-in1 page. За 271,95 с host memory full PSI delta12 мкс,
IO full PSI5,824 с (~2,14%). Ранее объявленный диагностический допуск
spread<=10% и остальные восемь checks выполнены. Это не SLA продукта
и не доказательство причины исторических failures.

Safe evidence `tests/artifacts/diagnostics/pve-memory-budget-20261001/ram-repeatability-20261001-v2*`.
v1 завершился до workload: установленный CT probe не принимает новый
keyword retain_timelines; сигнатура проверена, неподдерживаемая опция
убрана из orchestration v2. Продукт и установленные probes не менялись.

В 04:12:26 МСК запущен отдельный полный block
`http-1doc-stopped-pve-ram-v15-v11-20261001`: 18 случаев / 3 повтора /
c1,c8 / прежние 30–60 s и >=1000 requests. Это новая среда после
пользовательской настройки RAM, не третья попытка старого Windows block.
Block завершился failed в 04:39:52 МСК; итог приведён выше.
Пороги 20%, RSS, потери, exact counts и обе фазы сохранены. Следующие
100-doc/tail этапы допускаются только при полном pass. Passed Windows
normal/active/fixed-input сохраняются, baseline между hosts не смешивается.
Текущий статус block — running; полная приёмка остаётся open.

## Память Proxmox и ограниченная локализация, 01.10.2026

По поручению пользователя исправлен бюджет памяти PVE `10.10.1.50`.
Физически установлено 64 ГиБ; BIOS выделяет GPU 48 ГиБ, Linux получает
15,23 ГиБ. Пользователь подтвердил назначение машины для локальной LLM:
BIOS/VRAM и настройки модели сохранены. Прежние лимиты CT100/101 по
52 ГиБ превышали всю доступную Linux RAM; это подтверждённый дефект
конфигурации, но причина прежних latency failures остаётся недоказанной.

| Гость | Итоговое состояние | RAM | Допустимый swap | Автозапуск |
|---|---|---:|---:|---|
| CT100 Ollama | выключен | 2 ГиБ | 256 МиБ | выключен |
| CT101 llama-server | работает | 8 ГиБ | 512 МиБ | включён |
| CT102 приложение и тестовый стек | работает | 5 ГиБ | 0 | включён |
| VM103 | выключена пользователем, не запускать | сохранён лимит 2 ГиБ | — | выключен |

Сумма лимитов работающих гостей 13 ГиБ оставляет хосту около 2,23 ГиБ
бюджета. Это расчёт по верхним лимитам, а не фактически свободная RAM.
CT102 получил мягкую защиту `memory.low=4 ГиБ`, включая предка `lxc`;
`memory_recursiveprot` активен, `memory.min=0`. Защита ограничивает
вытеснение при конкуренции, но не является предварительным выделением
или абсолютной гарантией RAM. Она сохранена в LXC config и systemd
ExecStartPost. Проверены runtime cgroups, загруженный drop-in и
`systemd-analyze verify` (exit 0); перезапуск CT102/хоста не выполнялся.

После финальной настройки CT101 занимает около 5,36 ГиБ, CT102 3,60 ГиБ;
MemAvailable хоста 5,55 ГиБ. Memory PSI avg10/60/300 равны 0, OOM/kill
счётчики обоих CT равны 0. LLM `/health` возвращает 200; production и
тестовый frontend возвращают 200, оба backend Docker healthy. CT101/102
и production не перезапускались. `memory.swap.max=0` CT102 запрещает
новое вытеснение в swap, но около 41,7 МиБ прежних страниц пока остаются
в swap; глобальный swap не отключался.

Первый короткий контроль прерван после двух сохранённых строк, когда
пользователь выключил VM103 и её бюджет передан CT101. Exit 137 относится
к намеренно удалённому тестовому probe, не к backend. Эти строки не являются
контролем неизменной среды или приёмкой. Итоговые конфигурации и числовые
артефакты: `tests/artifacts/diagnostics/pve-memory-budget-20261001/`.
Полные приватные резервные конфигурации и подготовленный откат находятся
только на PVE: `/root/okf-memory-budget-20261001T004114Z`. Откат проверен
на синтаксис, не выполнялся; он сохраняет VM103 выключенной с onboot=0.

Контроль на **итоговом неизменном бюджете** завершён exit 0:
30 с прогрева + 60,09 с измерения, c8, 4 680 запросов, p95 107,253 мс,
response equality true. За 91,58 с наблюдения MemAvailable не опускалась
ниже 4,60 ГиБ; max RAM CT101 6,38 ГиБ / CT102 3,75 ГиБ. Новых OOM,
high/max events и swap-out нет; swap-in 5 страниц. Host memory full PSI
прибавился на 695 мкс, CT102 на 4 мкс. IO full PSI прибавился на 1,457 с
(около 1,59% интервала): дисковые задержки полностью не исчезли.
VM103 после контроля stopped, временный probe удалён. Safe result/status/
numeric metrics/review сохранены как `ram-budget-control-20261001-v2*`.
Это проверка доступности и запаса RAM под короткой search-нагрузкой,
а не многоповторная проверка drift или всех SLA; параллельную тяжёлую
генерацию LLM этот workload не задавал. Сравнивать p95 с прошлыми
длинными матрицами как доказанное улучшение нельзя.

Рекомендация: сохранить текущий бюджет и выключенные CT100/VM103.
Следующий performance-шаг ограничить проверкой повторяемости контролей
на новой среде перед оставшимися сценариями; прошедшие Windows блоки
не переисполнять. При возврате Qdrant 503 собирать безопасный cause type.
Новая многочасовая матрица в рамках настройки памяти не запускалась.

### Предшествующий ограниченный контроль Windows

Локализация Qdrant 503 завершена за 9 мин 35 с: шесть c8 строк,
23 088 measured requests, три пары без ZIP / stopped ZIP. Ошибок HTTP
и исключений cause observer нет, ZIP ready, точный учёт сошёлся,
losses 0. Рост p95 в парах +12,387%, +1,964%, +6,159%; baseline drift
13,238%. В каждой ZIP строке 40 запросов пересекли worker phases;
ни один из 20 slowest не пересёк полное наблюдавшееся окно сборки.
Это описательное наблюдение, не доказательство причинности или SLA.

503 не воспроизведён в ограниченном fresh-boot запуске; его причина
остаётся unresolved. Новый запуск не отменяет прежний failed/incomplete
stopped ZIP. Временный observer удалён из команды старта; исходный runtime
восстановлен, capture off. Safe artifacts:
`tests/artifacts/diagnostics/qdrant503-localization-windows-v15-v11-v2/`.
Первый setup не стартовал HTTP workload из-за отсутствующего PYTHONPATH;
его status сохранён отдельно, пустой невалидный result.json удалён.
Продукт и пороги заморожены; полная приёмка, 100-doc и tail gates открыты.

## Проверка результатов и рекомендации, 01.10.2026, 02:56 МСК

**Реализация заморожена; полная приёмка не пройдена.** Повтор stopped ZIP
завершился отказом, новая матрица не запущена. По сохранённым analysis JSON
перепроверены: current fixed-input — все 6 gates true; normal 1-doc и active
ZIP 1-doc — все 8 gates true; первый stopped ZIP — p95 false, остальные 7 true.
Прошедшие блоки сохраняют свой результат, но не закрывают остальные сценарии.

Единственный полный повтор `http-1doc-stopped-windows-v15-v11-r2` шёл
23:30:26–23:48:14 UTC: exit 1, закончены **11/18 строк, 39 509 requests**
в законченных строках. standard/c8/r2 прерван HTTP 503; его незаконченные
измерения не сохранены в rows и не включены в это число. Полного analysis
нет, повтор не принят. Оба observers завершились exit 0 / error bytes 0.

Причина 503 локализована по четырём связанным безопасным событиям:
23:48:11 UTC, request `02c80d7c-42f4-4cd6-a498-9936d9c6b879`,
`search_bm25 → _qdrant_call → VectorStoreError`, код `dependency_unavailable`.
Вызов зависимости длился 26,066 мс, весь запрос 147,768 мс.
Frame `_qdrant_call:99` соответствует общему `except Exception`, а не ветке
HTTP UnexpectedResponse. Поэтому это ошибка вызова Qdrant, но точный тип
внутреннего исключения (транспорт, клиент или иной сбой) не установлен.
Не считать её доказанным таймаутом, отказом Qdrant-сервера либо эффектом ZIP.
Все четыре контейнера: restarts=0, OOMKilled=false; в коротком окне журнала
Qdrant WARN/ERROR не найдены. Отсутствие этих записей не доказывает отсутствие
проблемы сервера. Тело HTTP-ошибки probe не сохранял; безопасные events
сохраняют внешний тип исключения, но не его cause.

Повтор и safe failure events экспортированы в
`tests/artifacts/diagnostics/http-1doc-stopped-windows-v15-v11-r2/`.
Private env и обычные журналы не экспортировались. Текущий runtime доступен,
failure_code=null, capture выключен, queued/dropped/storage_errors=0.
100-doc не seeded; его три матрицы, storm, baseline-off и final lifecycle
остаются required/open. Продуктовый код и пороги не менялись.

Рекомендации по продолжению:

1. Сохранить заморозку продукта и прошедшие блоки. Третью общую попытку без
   нового диагноза не запускать: разрешённый один повтор уже использован.
2. Следующий ограниченный шаг — локализация ошибки Qdrant: в тестовом
   наблюдении сохранять класс причины исключения и безопасный код ответа,
   request ID, интервалы запроса и полной сборки, без текста запроса/секретов.
   Сначала короткий контроль без ZIP и с ZIP, одинаковая c8 нагрузка.
   Лимит исследования 15–20 минут; отсутствие воспроизведения не является
   успешной приёмкой и не оправдывает повтор всей матрицы.
3. Только при подтверждённой причине назначать исправление и повтор
   затронутого блока; при отсутствии причины оставить статус unresolved.
   Не менять SLA, не усреднять failed строки с passed и не добавлять
   скрытые retry, маскирующие 503.
4. После разрешения stopped ZIP выполнить только оставшиеся 100-doc и tail
   gates. Учесть короткие фазовые задержки active ZIP: минутный p95 проходит,
   но conditional prepare p95 достигает 386 мс; отдельного фазового SLA нет.

## Отказ stopped ZIP на Windows и единственный повтор, 01.10.2026

**Полная performance-приёмка остаётся open.** Windows очередь остановилась
на `http-1doc-stopped-windows-v15-v11`: 22:52:07–23:19:37 UTC, 27 мин 29 с,
18/18 строк, 65 034 requests. Только `all_p95_pass=false`; остальные семь
gates true. 11/12 p95 comparisons проходят; единственный отказ
standard/c8/r3: baseline 147,894 → ZIP 213,837 мс, **+44,588% / +65,943 мс**,
при прежних 20%. Losses 0, exact warmup-only capture totals сошлись,
response equality/phase overlap/duration passed. Max RSS growth backend+child
24,719 МиБ / frontend 1,902 МиБ. Images frozen, sampler exit 0 / errors 0.
Исходный failed artifact/analysis/queue ledger сохранены. Корпус 100-doc
ещё не seeded; три его матрицы и tail controls не запускались.

Описательная локализация по уже собранным numeric metrics: в failed минуте
mean Windows CPU samples 40,14%, p95 91,64%, max 100%; в соседних c8 baseline/
detailed mean около 28,3%. VM CPU busy примерно 15,5–15,8%, CPU throttling 0,
memory full PSI 0; VM IO full stall fraction failed 2,099%, соседние
0,998/1,600%. Measurement bounds восстановлены по host samples с точностью
примерно 1 с; CPU mean здесь среднее interval samples, не causal estimate.
18/20 самых медленных запросов вне **наблюдавшихся child** prepare/ZIP phases;
есть поздние bursts через десятки секунд после сборки. Это не исключает
parent/finalization effects и не доказывает причину отказа, но не поддерживает
объяснение всей минуты только коротким child ZIP overlap. Текущий process
snapshot после нагрузки не может установить виновника прежних CPU peaks.
Safe analysis: `tests/artifacts/diagnostics/http-1doc-stopped-windows-v15-v11/failure-localization.json`.

По разделу 8 плана при единичном превышении разрешён один полный повтор.
Начат отдельный `http-1doc-stopped-windows-v15-v11-r2`, прежние 18 cases/
3 repeats/c1,c8/30–60 s/1000 requests/gates, те же images и app boot.
Добавлено сохранение всех уже измеряемых request intervals и WMI numeric
process CPU observer (name/PID/CPU/private working set, без command lines,
env, текстов или сетевых данных). Observer preflight exit 0; compile/Ruff
passed; probe/queue contracts **30 passed** в frozen Docker image.
System Python collection не имел SQLAlchemy; зависимости не устанавливались,
проверка выполнена в штатном image runtime. Продуктовый код и SLA не менялись.
Повтор завершился HTTP 503; результат и рекомендации приведены выше.

## Live active ZIP 1-doc на Windows, 01.10.2026

`http-1doc-active-windows-v15-v11` завершён 22:15:37–22:52:04 UTC,
36 мин 27 с, exit 0; sampler exit 0 / error bytes 0. **24/24 строки,
все восемь gates passed**, 88 973 measured requests. Три повтора c1/c8,
парный baseline без ZIP и baseline/standard/detailed с ZIP,
30 с warmup / >=60 с / >=1000 requests. Max p95 growth **10,251%**
(standard/c1/r2: 21,977 → 24,230 мс, +2,253 мс), при лимите 20%.
Max live peak RSS growth backend+child **24,563 МиБ**, frontend tree
**4,168 МиБ**, ниже 128/32 МиБ. Losses 0, exact capture totals сошлись,
fingerprints поиска равны baseline, реальные prepare/ZIP workers наблюдались
и пересекались с requests во всех 18 ZIP comparisons. Frozen images те же.

Полная минутная p95 проходит, но фазовые условные распределения выше:
максимум prepare p95 385,998 мс (baseline ZIP/c8/r1, 64 overlapping requests,
1,174 с observed prepare), detailed prepare/c8/r1 366,316 мс (24 requests),
standard prepare/c8/r2 330,234 мс (23 requests). Max ZIP phase p95 281,605 мс.
Минимум фазовых samples по всем ZIP rows: prepare 11, ZIP 10.
Эти малые выборки и пересечение интервалов не доказывают причинный эффект
фазы и не вводят отдельный SLA; `phase_distributions_pass` подтверждает
наличие samples, а не соблюдение отдельного фазового лимита. Числа сохранены
для честной оценки кратковременных задержек при сборке пакета.

Safe raw/analysis/numeric metrics:
`tests/artifacts/diagnostics/http-1doc-active-windows-v15-v11/`.
В 22:52:05 UTC начат stopped ZIP 1-doc. Remaining live/storm/baseline-off/
final lifecycle остаются required/open; полная готовность не объявляется.

## Live normal 1-doc на Windows, 01.10.2026

`http-1doc-normal-windows-v15-v11` завершён 21:48:07–22:15:33 UTC,
27 мин 27 с, exit 0; sampler exit 0 / error bytes 0. **18/18 строк,
все восемь gates passed**, 71 520 measured requests. Три повтора c1/c8,
baseline/standard/detailed, 30 с warmup / >=60 с / >=1000 requests.
Максимальный capture p95 growth **5,438%** (detailed/c1/r1:
22,479 → 23,701 мс, +1,222 мс), при лимите 10%.
Максимальный рост live peak RSS backend+child 0,066 МиБ, frontend tree
2,766 МиБ при лимитах 128/32 МиБ. Все capture totals сошлись;
backend/frontend losses 0, fingerprints поиска не изменились.
Frozen image IDs до/после те же v15/v11. Raw/analysis и numeric metrics:
`tests/artifacts/diagnostics/http-1doc-normal-windows-v15-v11/`.

В 22:15:35 UTC очередь начала active ZIP 1-doc. Пять остальных live
матриц, storm/baseline-off/final lifecycle пока required/open.

## Original контроль и начало live-приёмки, 01.10.2026, 00:49 МСК

Original fixed-input control завершён 21:28:31–21:46:43 UTC, 18 мин 11 с,
exit 0, sampler exit 0 / error bytes 0. 12/12 строк (3 повтора × c1/c8 ×
без ZIP / ZIP), 45 318 measured requests; duration, completeness,
integrity и response equality gates passed. Reference analyzer сохраняет
численные p95/RSS и проверяет известность metrics, но не применяет SLA
текущего продукта к immutable original. `performance_acceptance=false`.

Scope control: frozen **current** v15/v11 live HTTP path и private supervisor/
ZIP algorithm из original `backend/app` 2614bfa, mounted read-only.
Git archive SHA-256 `f09d64ef3ee3f2087b013c472c48f3b41eced1ebfc500c4d040dd2cdeffaed57`.
Safe artifact directory: `tests/artifacts/diagnostics/original-fixed-input-windows-v15-v11/`.
Baseline c8 по повторам 132,417 / 131,774 / 149,800 мс; ZIP соответственно
131,980 / 128,590 / 132,443 мс. Последний контроль вырос; не представлять
это как доказанное ускорение original ZIP. Синхронные numeric metrics и
timelines сохранены; результаты не объединены с current block.

В 21:48:05 UTC начат `http-1doc-normal-windows-v15-v11`. Очередь выполняет
ровно шесть required live matrices: 1-doc normal/active ZIP/stopped ZIP,
затем seed 100-doc и те же три сценария. Пороги исходные, каждый блок
3 repeats / c1,c8 / 30–60 s / >=1000 requests, exit/gates проверяются
перед следующим этапом. Windows wrapper использует host PID/cgroup namespace
для live backend+child и frontend tree RSS; Docker socket не монтируется.
Preflight показал ненулевой RSS обоих процессов. Код продукта не менялся.

Для ZIP quotas добавлены отдельные simulation admins в private runtime-live.env;
перезапущен только backend отдельного test project. Frontend, PostgreSQL,
Qdrant и рабочий Windows стек не перезапускались. Images те же v15/v11.
Private env/Compose не входят в evidence. Ledger текущей очереди:
`tests/artifacts/diagnostics/live-windows-v15-v11-state.json`.
При numerical failure очередь останавливается и сохраняет artifact/analysis.
Все live/storm/baseline-off/final lifecycle пока required/open.

## Полный fixed-input контроль на Windows, 01.10.2026, 00:30 МСК

Frozen v15/v11 на выбранном Windows / Docker Desktop Linux VM стенде:
**24/24 строки current fixed-input завершены, все шесть gates passed**.
Runner 20:50:45–21:27:50 UTC, 37 мин 05 с, exit 0; numeric sampler exit 0,
без ошибок. 3 повтора × c1/c8 × baseline без ZIP, baseline ZIP,
standard ZIP, detailed ZIP. Каждая строка 30 с warmup / минимум 60 с и
1000 измеренных requests; всего 91 119 измеренных requests. Пороги прежние.

Максимальный рост p95 среди 18 ZIP сравнений **+11,214%** (standard/c1/r3:
22,183 → 24,670 мс), ниже обязательных 20%. Максимальный рост private
supervisor+child RSS 11 243 520 bytes, около 10,72 МиБ, при лимите 128 МиБ.
Это private RSS и не заменяет gate памяти живых backend/frontend.
Baseline c1 по повторам: 21,638 / 22,071 / 22,183 мс;
c8: 129,617 / 126,664 / 127,656 мс. Контроль существенно стабильнее CT102.

SHA-256 input одинаковый, каждый ZIP содержит 20 000 событий; CRC/hash,
семплы prepare/zip и equality ответов прошли. Дополнительная проверка exact
issued requests вместе с warmup подтвердила равенство четырёх потоков для
**всех 12 capture-сессий**. Backend/frontend loss counters 0, queued=0,
runtime_available=true, active_capture=false. Frozen image IDs до/после
совпали. Safe artifacts: `tests/artifacts/diagnostics/current-fixed-input-windows-v15-v11/`;
полные request timelines и синхронные VM/Windows numeric metrics сохранены.

Результат снимает прежний fixed-input blocker на выбранном Windows стенде;
две failed CT102 серии сохраняются failed и не переписаны. Продуктовый код
не менялся. Начат обязательный immutable original fixed-input control:
исходное `backend/app` извлечено через `git archive` из `2614bfa22fdda249985956c77f79ddeab5cc31bc`
в private TEMP и монтируется read-only только в private ZIP supervisor.
Original control, live 1/100-doc normal/active/stopped ZIP, storm,
baseline-off и final lifecycle пока required/open; полной готовности нет.

## Контроль на текущей Windows, 30.09.2026, 23:45 МСК

По выбору пользователя повторён тот же ограниченный A/B/A на Windows 10
через локальный Docker Desktop **Linux VM**, не native Windows backend/Node.
Backend/frontend immutable image SHA-256 те же v15/v11, private ZIP input
20 000/7 580 000 bytes/прежний SHA-256. Свежий project
`okf-diag-win-aba-20260930`, отдельные PostgreSQL/Qdrant/данные/spool Linux
named volumes. Secrets сгенерированы заново в private TEMP; рабочий локальный
стек не менялся. Frontend host URL: `http://127.0.0.1:18484`.

Среда: Ryzen 7 7840HS, 8 cores/16 logical CPUs, host RAM 34 056 777 728 bytes;
Docker VM 16 CPUs/16 617 472 000 bytes. Private workload соединяется с
`http://frontend:3000` через Docker app network. Это иной transport/resources
и FS, чем CT102 host loopback: абсолютные latency нельзя трактовать как
чистую разницу Windows/Linux. Внутри каждого A/B/A images/resources/route
сохранялись одинаковыми. Продуктовый код и acceptance thresholds не менялись.

Runner 20:24:17–20:43:00 UTC, 18 мин 42 с, exit 0. 12 серий, 40 008
измеренных requests плюс warmup; каждую — 30/60 с и >=1000 requests.

| Сравнение | A1, p95 мс | B, p95 мс | A2, p95 мс | B к более быстрому контролю |
|---|---:|---:|---:|---:|
| Standard capture off/on/off | 21,828 | 22,547 | 22,014 | +3,293% |
| Standard ZIP off/on/off | 22,224 | 22,498 | 22,594 | +1,234% |
| Detailed capture off/on/off | 21,977 | 22,214 | 22,174 | +1,078% |
| Detailed ZIP off/on/off | 22,965 | 22,857 | 22,426 | +1,924% |

Дрейф соседних controls по модулю <=2,350%, вместо 38–58% в CT102 A/B/A.
CPU throttling live backend/frontend 0. VM IO full stall fraction за минуты
1,24–1,59%; 143/144 пятисекундных окна <5%, Pearson window p95/IO full
-0,053. Windows host CPU average примерно 17,8% по numeric GetSystemTimes.
Физический Windows disk IO этим sampler отдельно не измеряется.

ZIP elapsed 1,657/1,710 с, CRC/hash/20 000 events проверены. Небольшие фазовые
выборки имеют p95 до 36,018 мс (detailed prepare, 42 requests); они не вводят
отдельный фазовый SLA. В отличие от CT A/B/A, здесь сохранены exact issued
counts вместе с warmup: все четыре потока в каждой из восьми capture-сессий
совпали с ними. Backend/frontend losses 0, queued=0, runtime_available=true,
active_capture=false. Images до/после совпадают.

Вывод: стабильный относительный эффект capture/ZIP на этом опыте мал;
оснований продолжать менять продуктовый код не получено. Это поддерживает
выбор текущего Windows/Docker Desktop стенда для оставшихся контрольных
измерений. Не доказывает конкретную причину прошлых CT failures и не
гарантирует latency production на PVE. Полная исходная приёмка всё ещё open:
этот A/B/A c1/one repeat не заменяет three-repeat c1/c8 и остальные scenarios.
Следующий шаг — обязательный current fixed-input block на выбранном стенде,
с исходными cases/30-60 s/1000 requests/3 repeats и прежними gates. Полные
timelines сохраняются дополнительным флагом без изменения сравнения.

Safe evidence: `tests/artifacts/diagnostics/aba-windows-v15-v11-20260930/`.
В artifact directory перенесены только raw/analysis/timings/numeric metrics,
hardware/status и exact capture verification; runtime.env/Compose не копировались.

## Ограниченный A/B/A эксперимент, 30.09.2026, 23:10 МСК

**Полная приёмка остаётся открытой. Устойчивая деградация от capture/ZIP
в этом эксперименте не воспроизвелась; выявлена сильная связь задержек с IO.**
Это описательный результат на текущем стенде, не доказательство причины
прошлых failed gates и не замена исходной трёхкратной матрицы.

По поручению пользователя выполнены 12 последовательных c1 серий:
capture off/on/off и ZIP off/on/off для standard/detailed. Для ZIP controls
уровень capture включён во всех трёх случаях; каждая серия получает новую
сессию. Baseline instrumentation всегда включена. Один и тот же caller
demo.admin, три синтетических запроса round-robin, ответы сравниваются с
fingerprints. В каждой серии 30 с warmup, >=60 с measurement, >=1000 requests.
Весь runner: 19:37:01–19:55:42 UTC, 18 мин 42 с, exit 0; 41 138 измеренных
запросов, warmup дополнительно. CPU/IO/pressure записаны каждые 250 мс на
PVE и в CT, все интервалы запросов и child phase markers сохранены.

CT 100 ollama-server уже был stopped до эксперимента и остался stopped
после него. CT 101 llama-server до старта почти idle; его не останавливали.
Работающее состояние Ollama не объясняет колебания текущего эксперимента.
Образы backend v15/frontend v11 до/после совпали с frozen SHA-256;
продуктовый код, runtime настройки приложения и пороги приёмки не менялись.

| Сравнение | A1, p95 мс | B, p95 мс | A2, p95 мс | B к более быстрому контролю |
|---|---:|---:|---:|---:|
| Standard capture off/on/off | 18,288 | 19,064 | 28,948 | +4,245% |
| Standard ZIP off/on/off | 19,069 | 19,157 | 19,313 | +0,461% |
| Detailed capture off/on/off | 29,264 | 19,173 | 18,103 | +5,908% |
| Detailed ZIP off/on/off | 18,961 | 19,188 | 28,778 | +1,198% |

Контроли внутри отдельных троек изменились на +58,296%, -38,138% и
+51,772%. Последний высокий контроль имеет detailed capture без ZIP,
поэтому высокие p95 возможны и при capture off, и при capture on.
Отрицательные относительные значения не доказывают ускорение от диагностики.

Синхронная IO association:

- 144 пятисекундных окна; Pearson между window p95 и PVE IO full stall
  fraction = **0,963454**. Серийные окна зависимы, p-value/causal effect
  не оцениваются. Разделение по 5% IO full использовано только описательно.
- В 20 окнах с IO full >=5% median window p95 = **29,795 мс**; в остальных
  124 — **18,811 мс**. В трёх медленных минутных controls IO full =
  7,43/9,64/6,70%; в остальных — примерно 2,62–2,79%.
- PVE CPU busy около 4–5%, CPU steal 0; cgroup throttled_usec backend и
  frontend не растёт. Это не подтверждает гипотезу о перегрузке CPU Ollama.
- Device 252:6 — `pve-vm--102--disk--0`; IO counters проходят через общий
  `nvme0n1`/local-lvm. Read-only inventory нашёл один физический NVMe
  и один rootdir storage local-lvm. Production и simulation Compose находятся
  в CT 102. Другой Compose project/CT на том же диске не даст физической
  IO-изоляции. Объёмы IO нельзя суммировать между DM layers и physical device.
- Конкретный процесс/механизм IO latency не установлен. Поздний 2-секундный
  PostgreSQL IO sample после окончания workload дал нулевые write deltas;
  он не локализует предшествующие пики. Безопасный отдельный контроль из
  100 baseline поисков занял 2,044 с; SQL statement logging выключен,
  fsync/synchronous_commit включены; WAL delta 8161 bytes/182 records.
  Это также не доказывает виновность PostgreSQL или внешнего процесса.

Оба ZIP собраны из прежних 20 000 событий / 7 580 000 bytes с тем же SHA-256
`62d0f9188ecaee669fae70d46ce8b830de5454a98b32206eda4a48cc9946d534`.
Prepare/ZIP SHA, CRC и event counts совпали; elapsed 1,592/1,687 с.
В каждой prepare phase 46 измеренных requests, ZIP phase — 44; фазовый
p95 standard 20,299/19,531 мс, detailed 19,431/24,125 мс. Эти небольшие
фазовые выборки не являются отдельной SLA-приёмкой. Private ZIP supervisor
находится в отдельном тестовом Docker cgroup; cgroup metrics live backend/
frontend не включают этого private child.

После эксперимента все восемь SQL capture-сессий stopped. Для каждой сессии
backend requests/frontend requests/backend operations/Qdrant totals совпали
и ненулевые: 5299, 5179, 5305, 5327, 5375, 5375, 5376, 4564 соответственно.
Это сверка внутренних totals; точный issued count вместе с warmup отдельно
не сохранён, поэтому равенство всех четырёх не доказывает отсутствие общей
потери на самом входе. Cumulative backend/frontend loss counters равны нулю,
queued=0, runtime_available=true, active_capture=false.

Рекомендации и граница продолжения:

1. Заморозить v15/v11; не продолжать оптимизацию ZIP/queues по старым
   одиночным p95 failures. Старые два fixed-input failures остаются failed.
2. Следующий предмет исследования — IO latency текущего volume/NVMe и его
   связь с реальным HTTP/SQL путём. Корреляция достаточна для выбора этой
   гипотезы, но недостаточна для произвольного отключения служб/изменения БД.
3. Перед новой общей матрицей выбрать другой Linux host с отдельным storage
   либо продолжить ограниченную локализацию IO в CT 102. Этот выбор задан
   пользователю; production stop, storage move и ослабление порогов не
   выполнялись. Новая многочасовая очередь не запущена.
4. Полные original fixed-input/live 1/100-doc/storm/baseline-off/final lifecycle
   остаются required/open. Commit/push/merge/production требуют отдельного
   поручения. Успешный диагностический A/B/A не закрывает всю задачу.

Артефакты: `tests/artifacts/diagnostics/aba-v15-v11-20260930*` — raw JSON,
полные request timelines, PVE/CT numeric metrics JSONL, analysis, frozen image
status, capture verification, IO localization и PG read control. Для
воспроизведения добавлен diagnostic-only режим existing fixed-input probe,
PVE runner и analyzer. Python compile, Ruff для трёх harness files и
git diff --check прошли; enclosing counter-window calculation проверен на
ручном наборе значений. Полная pytest повторно не запускалась: продуктовый
код и образы не менялись.

## Перепроверка выводов и следующего шага, 30.09.2026, 22:16 МСК

Live read-only проверка CT 102: образы v15/v11 совпадают с зафиксированными,
runtime_available=true, active_capture=false. Controller ledger содержит
37 passed stages и failed fixed-input attempt 2. Live ledger отсутствует.

Повторный пересчёт raw artifacts подтвердил ровно одно превышение в каждом
fixed-input проходе: standard/c1/r3 +12,096 мс (+67,295%) и detailed/c1/r2
+7,498 мс (+41,618%). В каждом проходе 24 строки, включая шесть baseline
контролей, то есть 18 сравнений с ZIP. Формулировка «23 из 24 прошли» включает
сами baseline; для оценки эффекта корректнее говорить 17 из 18 сравнений.
Оба блока целиком остаются failed, успешные сравнения не отменяют отказ.

Выявлены ограничения причинного анализа harness:

- baseline всегда идёт первым; только последующие cases перемешиваются.
  До последнего сравниваемого режима проходит несколько минут. Без повторного
  baseline в конце нельзя оценить дрейф времени ответа в пределах этой группы.
- Полный p95 за 60 с смешивает около 1,6 с prepare/ZIP и остальное время.
  Фазовые quantiles есть, но полные интервалы запросов после их расчёта
  отбрасываются (`measured.pop("_intervals")`), поэтому момент и длительность
  ухудшения по сохранённым artifacts уже не восстановить.
- Private fixed-input RSS относится к supervisor/child. Он не доказывает
  прохождение live backend/frontend memory criterion, который остаётся open.
- Поздний короткий профиль имеет другой запрос/пользователя/длительность.
  Он полезен для гипотез, но не устанавливает причину предыдущих failures.

Предыдущее предложение сделать новое окно/стенд предварительным условием
было преждевременным. Нагрузка PVE/Ollama не доказана; низкий CPU pressure
в последующем коротком контроле также не объясняет прошлый интервал.
Вопрос о смене стенда можно отложить до получения синхронных доказательств.

Рекомендуемый следующий шаг — один диагностический эксперимент на текущем
CT 102 с бюджетом около 20 минут, без изменения images и acceptance thresholds:
для c1 сравнить capture off/on/off, затем при фиксированном capture level
ZIP off/on/off; выполнить для standard и detailed. Все baseline сохраняют
baseline instrumentation on. Сохранить timestamps/длительности отдельных
синтетических запросов, безопасные phase markers и синхронные CPU/IO/pressure
метрики; сопоставить одинаковые query distributions и caller context. Это
диагностика причинности, не замена полной трёхкратной SLA-приёмки.

Код менять после локализации задержки. Резервирование ресурсов/переезд
обоснованы при подтверждённой конкуренции; при отсутствии воспроизведения
за этот ограниченный эксперимент оставить performance criterion open и
подготовить reviewable ветку с явным блокером. Новая полная матрица до такого
решения не добавляет достаточных данных о причине. Commit/push/production
по-прежнему требуют отдельного поручения.

## После единственного полного fixed-input повтора, 30.09.2026

**Полная performance-приёмка не пройдена; очередь остановлена.** Вторая серия
на тех же images/входе/порогах также имеет `all_p95_pass=false`: теперь
detailed/c1/r2, 18,015 → 25,513 мс (+7,498 мс, +41,618%). Остальные 23
комбинации проходят порог. Полнота матрицы, duration, private RSS, SHA/CRC/
число событий и совпадение ответов проходят. Первый failed result не удалён;
он остаётся standard/c1/r3 +67,295%. Перемещение выброса между режимами
не доказывает ни внешний шум, ни устойчивую стоимость конкретного уровня.

Образы не изменились, active_capture=false, runtime_available=true;
проверка сохранена в `fixed-input-v15-final-status.json`. Новых iterations
порогов или продуктового кода после первого failure не было. Original
fixed-input и live HTTP/storm/baseline-off/final lifecycle не запускались.

Два ограниченных диагностических контроля (не SLA):

- Прямой synthetic search внутри backend, один фиксированный запрос,
  3 с прогрева / 10 с измерения на режим: total p95 baseline 7,170 мс,
  standard 7,412 мс, detailed 7,248 мс. Это отдельный процесс, без HTTP,
  другой пользовательский контекст и один запрос; он не воспроизводит
  весь input distribution failed HTTP controls и не оправдывает их отказ.
- HTTP c1 через frontend, без ZIP, 3/30 с, >=1000 requests: baseline p95
  29,860 мс, detailed 28,446 мс. Повышенная задержка возможна и при capture off
  и без builder. Detailed expected counts сходятся. В 34 sampled traces
  Qdrant p95 2,328 мс, backend operation p95 25,312 мс, backend request
  32,430 мс, frontend request 26,575 мс. Разные quantiles нельзя вычитать
  для причинного разложения. Нужна дальнейшая локализация backend/HTTP
  вариативности, а не предположение о медленном ZIP/Qdrant.
- CPU pressure some avg10 максимум 0,64/0,82, full=0 в этом последнем
  HTTP контроле; это не синхронные данные двух failed fixed-input repeats
  и не доказательство отсутствия влияния PVE/Ollama в момент их выполнения.

Все числовые результаты и profiles скачаны в `tests/artifacts/diagnostics/`:
`fixed-input-full-v15-v11{,-analysis}.json`, отдельный `-r2{,-analysis}.json`,
оба attempt ledgers, `fixed-input-v15-localize-r1.txt`,
`http-path-localize-c1-v15-v11{,-events}.json` и final status.

Открытое решение перед следующими длительными измерениями: обеспечить
контролируемое окно нагрузки на PVE/CT 102 либо отдельные гарантированные
ресурсы; альтернативно сохранить performance criterion как невыполненный
и согласовать ограниченную готовность ветки. Причина задержки пока неизвестна.
Следующий проход должен собирать сопоставимые по времени HTTP/stage и host
CPU/IO данные на baseline и capture; повторять матрицу до случайного GREEN
или объявлять исходный порог выполненным по двум коротким profiles нельзя.

## Fixed-input отказ и ограниченный повтор, 30.09.2026, 21:15 МСК

Первый current fixed-input block завершён полностью (24 комбинации), но
`all_p95_pass=false`. Остальные gates — complete/duration/private RSS/
bundle integrity/response equality — true. Единственное превышение:
standard/c1/r3, baseline 17,975 мс → 30,071 мс (+67,295%; лимит +20%).
Для того же standard/c1 r1 -3,331%, r2 -3,663%; baseline c1 по трём
повторам 28,971/19,724/17,975 мс. Различие не объявляется доказанным внешним
шумом или ошибкой приложения. Prepare/ZIP wall time стабилен: около 1,5–1,7 с;
все пакеты содержат ровно 20000 событий с одинаковым input SHA-256 и valid CRC.

Очередь автоматически остановилась после 37 passed stages и failed fixed-input
stage; original fixed-input и live HTTP block не запускались. Первый raw result,
analysis и failed ledger скачаны локально. Failed probe exit code 0 не принят
как GREEN: numerical validator отверг итог.

Ruling: выполнить один полный повтор fixed-input block без изменений кода,
images, входа, длительности и порогов, как требует раздел 8 плана для latency
выброса. Failed evidence сохраняется. CT first-attempt ledger сохранён в
`remaining-acceptance-v15-v11-first-attempt-state.json`; повтор пишет отдельные
`fixed-input-full-v15-v11-r2{,-analysis}.json` и новый private root. Текущий
mutable controller ledger отмечает attempt=2, previous_attempt_ledger и
selected_artifact, чтобы успешный повтор не скрывал первую неудачу. Все 37
завершённых stages переиспользуются; они не перезапускаются. Если повтор
проходит исходные gates, очередь продолжает original fixed-input и live block;
при повторном отказе останавливается для разбора устойчивого превышения.
Источник и harness files во время обоих замеров не изменяются.

## Завершение private controls v15/v11, 30.09.2026

Все 24 current TestClient controls прошли duration/p95/private RSS/known losses/
zero losses/bundle gates. Максимальная прибавка capture p95 4,873%, с ZIP 6,688%
(settings/standard/c1/r2). Все пять loss counters во всех current controls равны
нулю. Завершены также 12 immutable original controls; reference observations
сохранены без переноса старых потерь в current acceptance.

Для search_stub/c8 original capture p95 вырос на 10,79–11,19%, с ZIP на
16,79–19,13%. Current standard/c8: capture +3,31–3,60%, с ZIP +3,21–4,26%.
Original search_stub/c8 dropped: r1 18219, r2 17903, r3 18043; остальные
reference controls имеют нулевые loss counters. Current остаётся zero-loss.
Эти потери относятся к перегрузке исходного recorder в синтетическом сценарии,
а не к историческому frontend admission gap.

72 JSON artifacts (36 измерений и 36 analysis) сохранены локально в
`tests/artifacts/diagnostics/testclient-*-v15-v11{,-analysis}.json`.
Начат fixed-input block с 20000 одинаковых событий. Полная live HTTP приёмка
и baseline-off/storm/lifecycle текущей пары ещё остаются обязательными.

## Проверка текущих выводов, 30.09.2026, 19:30 МСК

**Оснований для нового изменения продуктового кода по завершённым замерам нет.**
Полная приёмка ещё не завершена. В CT 102 подтверждены 22 из 24 current
TestClient controls v15/v11: все duration/p95/private RSS/loss/bundle gates
passed. Максимум capture overhead 4,873%, capture+ZIP 6,688%; оба максимума
относятся к settings/standard/c1/r2. Вместе с native TTL/quota завершены
23 из 39 stages remaining controller; выполняется settings/standard/c8/r3.
Счётчик 39 относится только к этому controller и не включает следующий live block.

Оставшийся объём проверен по фактическим циклам controllers: два current
controls, 12 original controls, 36 fixed-input phases и 120 live HTTP phases.
При 30 с прогрева и 60 с измерения это около 297 минут, плюс startup,
ожидание минимального числа запросов, lifecycle/storm и служебные задержки.
Следовательно, оценка оставшегося времени — около пяти часов или больше;
это заранее заданная длительность, а не признак зависания приложения.

Ограничение интерпретации: общий p95 за 60 с не исключает короткого пика
в prepare/ZIP. В targeted r3 ZIP phase имеет только 8 пересекающихся запросов,
phase p95/max 419,228 мс; весь capture+ZIP repeat имеет p95 96,318 мс.
В соответствующем baseline p99 385,475 мс и max 399,683 мс; для capture+ZIP
p99 388,017 мс и max 426,794 мс. Фазовый gate проверяет наличие samples,
а не отдельный численный SLA для phase p95. На такой малой выборке нельзя
объявлять доказанными ни отсутствие пиков, ни причинную связь с ZIP/Ollama.
Источник: `targeted-standard-c8-active-v15-v11.json`.

Рекомендации: завершить один уже запущенный проход на фиксированных images;
следующее изменение кода делать только по конкретному непройденному критерию.
При одиночном latency/RSS выбросе применять предусмотренный планом повтор
затронутого блока с сохранением обоих результатов. В итоговой таблице рядом
с общим p95 публиковать baseline/capture p99/max, фазовые samples и область
доказательства. После выполнения gates закрыть оптимизацию и подготовить
review/CI; локальный pytest exit 0 не подменяет фактический GitHub CI.
Длительный полный прогон оправдан как итоговая приёмка всей переработки;
для следующих локальных исправлений выбирать проверку затронутого механизма,
а полный проход выполнять после фиксации окончательной версии.

## Продолжение после проверки результатов, 30.09.2026, 14:38 UTC

**Полная приёмка остаётся открытой.** Локализован и исправлен воспроизводимый
пропуск frontend capture admission; начат обязательный трёхкратный блок v15/v11.

- На прежней паре v14/v11 короткий observer repeat r2 сохранил 1792/1792 запросов.
  Повтор исходной длительности 30/60 с (r3) воспроизвёл расхождение:
  backend request/operation/Qdrant по 7464, frontend 7380, пропуск 84.
  Safe samples сохранены: `investigate-admission-v14-v11-r3.json`.
- Первое продление control lease произошло через 9,95 с после старта capture,
  при сроке lease 10 с. Scheduler просыпался каждые 5 с; отдельный guard в
  `sessions.tick()` требовал 5 с после старта сессии. Если первый wake приходил
  чуть раньше этого срока, он пропускал renewal до следующего wake. Frontend
  с polling раз в секунду мог перестать принимать capture до получения renewal.
  Admission до очереди объясняет нулевые loss counters. Для первоначального
  пропуска 32 historical samples отсутствуют; прямое доказательство его точной
  временной причины не заявляется.
- Детерминированный тест с разными фазами старта: до исправления 2 failed / 1 passed;
  после исправления все три сценария passed. Runtime проверяет tick раз в секунду,
  а сам tick по-прежнему пишет projection не чаще одного раза за 5 с.
- v15 собран на точной базе v14, заменён только `app/services/diagnostics/runtime.py`.
  Backend image `sha256:2aad41d130c6d877b8bcfd98f3ed7381b4f4acaa2e6a0fe7fec5e0cfdd8bb023`;
  frontend v11 `sha256:a37ae1505d48528941a849e8ca7c07e568e6c453bdfa59e52626d5fba35b4799`.
  Зависимости и frontend не пересобирались.
- Observer repeat v15/r1: все четыре totals по 7552. Завершение probe прервано
  отказом ZIP submit: HTTP 429 `diagnostic_bundle_rate_limited`, у синтетического
  `diag.bench01` уже три bundle за час. Этот repeat не засчитывается как SLA/ZIP
  acceptance. Следующий targeted блок использует свободные `diag.bench04..06`;
  лимит приложения не меняется, исторические jobs сохраняются.
- Новый bounded observer сохраняет allowlisted control/status samples в finally,
  в том числе при HTTP отказе; текст исключения/URL/ответ в JSON не входит.
  Harness сохраняет причину ZIP-исключения в памяти, чтобы observer видел HTTP-код.
- Проверки Windows: session/lifecycle/heartbeat 20 passed; final harness/observer
  и policy measurement contracts 36 passed. Linux соответствующий набор 41 passed.
  Ruff focused и `git diff --check` passed.
- Прямой synthetic API/Qdrant probe: 128/128 passed, exception chain не возникла.
  Прежний HTTP 503 у Qdrant остаётся без установленной первопричины; влияние
  Ollama не подтверждено.

Запущены три пары baseline/standard c8+active ZIP с прежними порогами, 30/60 с,
>=1000 запросов и polling ZIP раз в 3 с. Observer выключен в SLA измерениях.
Application images, harness и fixtures зафиксированы до запуска; новые артефакты
используют суффикс v15/v11. Commit/push/production не выполнялись.

Обязательные paired repeats r1/r2/r3 прошли все восемь gates:
r1 p95 94,492 → 98,329 мс (+4,06%), четыре totals по 7544;
r2 92,136 → 98,453 мс (+6,86%), totals по 7616;
r3 92,326 → 96,318 мс (+4,32%), totals по 7608.
Максимальный backend RSS growth 0,73 МиБ; frontend 16,06 МиБ.
Native Linux accelerated TTL/quota/canonical retry: 3 passed, 4 dependency warnings.
Численные решения: `targeted-standard-c8-active-v15-v11-analysis.json`.
Начат следующий remaining block; partial targeted success не считается полной приёмкой.
Observer v15 показал минимальный запас control lease 4,73 с; интервалы renewal
не превышали 5,30 с. Это отдельная diagnostic evidence, не SLA workload.
Чистая полная Windows regression v15 завершилась с exit 0. Контрольный manifest
433 Python-файлов application/harness/tests после завершения совпал полностью:
changed_files=[]; source_unchanged=true. Доказательство:
`backend-v15-full-regression-verification.json`. Изменений кода/harness во время
pytest не было. Точный итог passed/skipped не переносится из предыдущего прогона:
финальная summary-строка этого прогона не сохранена монитором; подтверждены
завершение 100%, exit 0 и неизменный набор исходников.
Remaining controls идут; первые восемь search_stub stages прошли gates.

## Повторная проверка результатов, 30.09.2026, 13:55 UTC

**Полная приёмка не пройдена.** Финальный controller v14/v11 остановился
после первой пары; оставшиеся performance stages не запускались.

| Проверка текущей пары v14/v11 | Подтверждённый результат |
|---|---|
| Linux regression после review | 56 passed |
| Generation/stream/retry/cancel, три режима | 21/21 passed, recorder losses zero |
| Frontend tests / production build | 279 passed, 1 skipped / passed |
| Standard c8 + active ZIP, первая пара | baseline p95 90,569 мс; capture+ZIP 96,873 мс, +6,96% |
| Peak RSS, прирост той же пары | Backend+child 25,6 МиБ при лимите 128; frontend 29,8 МиБ при лимите 32 |
| Полнота той же пары | Backend request/operation/Qdrant: 7688; frontend: 7656; не хватает 32 (0,416%) |
| Численные loss counters той же пары | Нулевые; они не обнаружили расхождение end-to-end totals |
| Диагностический повтор с наблюдением lease | Прерван HTTP 503; итоговый observer JSON не сохранён из-за исключения до записи |
| Полная локальная backend regression | 2678 passed, 24 skipped, 1 failed; 2306,71 с. Полного чистого GREEN нет |
| Повтор проблемного harness-набора на неизменных текущих файлах | 15/15 passed, 13,32 с |

В 13:50:41 UTC безопасная цепочка событий повторного probe связывает HTTP 503
с `dependency=qdrant`, `exception_type=VectorStoreError`,
`error_code=dependency_unavailable`, request ID
`3849ee4b-9b27-4a5b-bd7e-9670a2b60485`. Это подтверждает ошибку обращения к
Qdrant, но не её первопричину и не падение Qdrant. При последующей проверке
длительность сбойного dependency call составила 5,69 мс; долгий таймаут не подтверждён.
При последующей проверке доступности
поиск вернул HTTP 200, backend `/health/ready` напрямую — 200/ready;
OOM и рестартов у контейнеров нет. Общий health degraded из-за выключенных
LLM/Ollama тестового стенда; PostgreSQL/Qdrant/pdf_parser — ok.
Frontend не публикует `/health/ready`, его 404 не является отказом backend readiness.

Единственный отказ полного pytest —
`test_live_queue_accepts_complete_controls_with_explicit_reference_scope`.
Во время этого прогона исполнитель изменил harness/fixtures: собранный ранее
тест ещё формировал старый набор gates, а поздно импортированный validator
уже требовал `all_loss_counters_known_pass`. Свежий запуск всего файла
`test_diagnostics_acceptance_queue.py` на неизменных файлах: 15 passed.
Это ошибка организации проверки; полный смешанный прогон не объявляется GREEN.
Фиксировать до запуска требуется продуктовый код, harness и тестовые fixtures.

Рекомендации к следующему ограниченному проходу:

1. Не расширять оптимизацию до локализации пропуска 32 событий. Сохранить
   текущую пару и failed артефакт; расхождение отдельно от latency.
2. Observer обязан сохранять safe control/counter samples и сведения о
   первом HTTP отказе в `finally`, даже при незавершённом измерении. Текущий
   повтор не доказывает гипотезу о lease, поскольку samples утрачены.
3. Одним адресным сценарием установить место расхождения: вход frontend,
   решение capture admission, aggregate/queue, persisted totals. Не выдавать
   искусственно воспроизведённые 16/32 пропуска за историческую первопричину.
4. Отдельно классифицировать Qdrant-сбой. Связь с Ollama/нагрузкой PVE остаётся
   гипотезой; нужны совпадающие по времени признаки, а не только общий CPU peak.
5. После устранения конкретного отказа повторить затронутый трёхкратный блок.
   Полную матрицу запускать только после его прохождения на неизменных images.
   Полную regression запускать после фиксации также harness и tests; не
   повторять 38-минутный набор посреди ещё продолжающихся правок.

Нагрузка остановлена по gate, активной capture-сессии после probe нет.
Публикация/production и смягчение критериев не выполнялись.

## Учёт финального review, 30.09.2026

Выполнен один ограниченный проход по трём Important и одному Minor замечанию:

- Node writer отделяет конечный batch до первого I/O await. Новые события
  остаются в ограниченной очереди; одновременно запланирован только один drain.
  `flush/stop` дожидаются последующих конечных batches.
- Медленные успешные HTTP-вызовы используют отдельный секундный лимит;
  превышение агрегируется и отражается в `sampled_out_slow`. Ошибки обходят
  этот лимит. Счётчик попадает в безопасные metadata пакета, без признака loss.
- Документная operation освобождает trace slot при finish/failure даже с
  родительским request ID. Search/interface сохраняют request trace до HTTP finish.
- Backend агрегаты используют интервал policy snapshot; deadline проверяется
  между элементами непрерывной очереди, а не только при её опустошении.

Каждое замечание воспроизведено RED до исправления. Адресный backend набор:
53 passed. Frontend: 279 passed, 1 skipped; production build passed. Полная
backend-регрессия ещё выполняется, её статус не подменяется адресным набором.
На frozen Linux v14/v11: 56 passed (4 dependency warnings); отдельно
generation/retry, stream completion/failure/cancel и late error после HTTP 200
прошли в трёх режимах — 21/21 contracts, с реальным recorder и нулевыми losses.
Первая попытка outcome запуска завершилась до выполнения тестов из-за неверного
названия pytest option; failed launch ledger сохранён, исправлен только аргумент.

Финальный последовательный controller запущен 30.09.2026 в 13:37 UTC:
`run_diagnostics_review_final_ct.py`. Каждый блок пишет собственный ledger;
image IDs проверяются до/после stages, завершённые артефакты не перезаписываются.
Запуск контроллера не означает прохождения performance критериев.

Зафиксирована новая Linux пара v14/v11:

- backend `sha256:7dfea1d712ec74ef0fb5bd1430fc78805f4615adb06f11ca40c049165b6487f0`;
- frontend `sha256:a37ae1505d48528941a849e8ca7c07e568e6c453bdfa59e52626d5fba35b4799`.

Backend построен непосредственно поверх неизменённого CT image v13: изменены
только `recorder.py` и `snapshot.py`, зависимости сохранены. Локальный пробный
rebuild с повторным разрешением зависимостей в CT не передавался и в приёмке
не используется. Frontend архив SHA-256:
`9c4fcdebe4f871d4f6dc191ee173a2771ccad13647b533116343ffe611769056`;
проверено отсутствие runtime env и БД. Обновлён только disposable project.

Все 24 current private controls v13/v10 завершились с прохождением gates.
Максимальная прибавка capture p95 по этому набору — 5,855%, build — 6,524%.
Это исторические результаты указанной пары; после review не переименованы.
Очередь затем самостоятельно остановилась на original/c8/r1: `dropped=17889`.
Failed ledger сохранён. Legacy контроль измеряет исходную immutable реализацию,
поэтому её реальные losses теперь сохраняются как reference observation;
известность всех counters, duration и целостность остаются обязательными.
Для current сборки нулевые losses и исходные p95/RSS thresholds обязательны.
Адресный тест доказывает, что reference исключение невозможно без immutable mount
и original identity, и что тот же loss остаётся отказом current gate.

Следующий проход — зафиксированная v14/v11, последовательные mandatory проверки
без новых изменений policy/writer во время измерений. Полная приёмка открыта.

## Исполнение уточнённых рекомендаций, 30.09.2026

Текущая карта приёмки (таблицы в последующих датированных разделах исторические):

| Проверка | Текущий статус |
|---|---|
| Standard/c8/active ZIP и baseline, frozen v13/v10 | 3/3 пары, все восемь gates passed |
| Native Linux retry, near-quota narrow/wide prepare, ускоренный API TTL/download lease | 3 tests passed; область backend API/filesystem, не browser TTL |
| Строгие search_stub/settings private controls v13/v10 | 24/24 passed; сохранены как исторические после review fixes |
| Полные live 1/100-doc normal/active/stopped ZIP финальной пары | Открыто; предыдущие версии не переименованы в результаты v13/v10 |
| Fixed-input current/original, legacy controls, baseline-off | Открыто |
| GitHub CI и публикация ветки | Не выполнялись; нужны отдельное поручение на commit/push и фактический CI run |

Ruling: установление причины исторических 16 пропусков не является условием
приёмки текущей версии. Приёмка требует полного равенства ожидаемых totals,
неизменных ответов и исходных p95/RSS критериев на текущей сборке. Историческая
неопределённость сохраняется; совпадение числа 16 в управляемом probe задано
сценарием и не доказывает историческую причину.

Исправления приёмки проверены 24 адресными tests, Ruff passed. Дополнительная
проверка отсутствующего `--capture-level` прошла: такой current control
не становится legacy reference. Исключение SLA разрешено только при явно
смонтированном read-only immutable original app tree и его build identity.

- Отсутствующие, boolean, строковые и отрицательные loss counters больше
  не подменяются нулями. Backend и frontend проверяются одинаково.
- TestClient, fixed-input и error-storm результаты проверяются после exit 0.
  Отказ сохраняет failed_gates и отдельный analysis JSON. У TestClient
  проверяются 10%/20% p95, private RSS, duration/requests, losses и overlap;
  у fixed-input — полный набор cases, duration, p95, private RSS,
  SHA/CRC/event counts, response equality и обе фазы. У storm — известные
  counters, quota/drain и live availability. Smoke и immutable-original
  controls явно не являются прохождением SLA текущей версии.
- Private supervisor/child RSS не заменяет live backend/frontend RSS.
  Это отдельная область доказательства. Потери при error storm допустимы
  по исходному плану, при штатной нагрузке — нет.

Собран и доставлен в CT 102 frontend v10 с guard. Runtime env и БД в образе
отсутствуют. SHA-256 переданного архива:
`d97c6869874a9a280ecadd127ba4ca94f4ac5f9d0d9b137b256b798413c6b710`.
Изменён только frontend disposable project, backend v13 сохранён.
Frozen images:

- backend `sha256:b51d0b122dc243d67417f4bd520d67f8629a08981ff63124e9dd296a6fe52d8d`;
- frontend `sha256:d2541f58d4b9af9c945d2f87214ebb7e4a8c71024084f549668c974c18d8c585`.

Завершён `run_diagnostics_targeted_ct.py`: три пары standard/c8/active ZIP
и baseline, 30 с warmup + минимум 60 с / 1000 измеренных запросов. Во второй
паре порядок обратный. Bundle poll 3 с; images проверяются перед и после
каждого measurement. Это ограниченный subset, не вся HTTP-матрица.
Все три пары прошли восемь gates:

| Повтор | Baseline p95, мс | Standard+ZIP p95, мс | Рост | Ожидаемый/фактический total каждой из 4 цепочек |
|---|---:|---:|---:|---:|
| 1 | 94,26 | 100,89 | 7,03% | 7368/7368 |
| 2 | 93,29 | 99,81 | 6,99% | 7392/7392 |
| 3 | 94,43 | 100,93 | 6,88% | 7384/7384 |

Backend RSS growth максимум 0,24 МиБ. Node peak differences отрицательные
во всех трёх парах; это разность максимумов разных окон, не доказательство
снижения потребления памяти. Prepare пересекли 184/192/176 requests,
ZIP — 32/24/24; каждому bundle потребовалось 2 API polls с интервалом 3 с.
Host CPU pressure avg10 на старте capture 14,84/12,45/14,33; сервер не был
полностью простаивающим. Активность именно Ollama не устанавливается.
Одновременно поменялись frontend guard и observer cadence, поэтому результат
не приписывается одной из этих причин. Ответы поиска идентичны baseline по SHA.
Артефакты `targeted-standard-c8-active-v13-v10{,-analysis,-state}.json`.

Следующий sequential ledger — `remaining-acceptance-v13-v10-state.json`:
native CT TTL/quota, затем строгие private search_stub/settings controls.
Переход к следующим блокам запрещён при failed numeric gate. Полные уже
пройденные backend/Node наборы не повторяются после чисто harness-правок.

Первый remaining stage завершён: `native-ttl-quota-final-v13-v10` — **3 passed**.
Проверены retry только failed chunk, narrow prepare при почти полной квоте
с отказом wide export, ускоренный TTL capture/bundle и существующая download
lease. Это backend API/native Linux filesystem проверки с изолированными
часами; часы PVE/CT не менялись, реальное ожидание 24 часов и browser TTL
этими тестами не подтверждаются.
`testclient-search_stub-standard-c1-r1-v13-v10` также прошёл строгие gates:
capture p95 +2,53%, capture+ZIP +2,85%. Остальные controls ещё выполняются;
пройденный частный сценарий не закрывает общую performance-приёмку.

Подготовлен `run_diagnostics_live_ct.py`: переход разрешён только после всех
39 private stages с явными численными gates и корректной областью reference.
Проверка перехода — **4 адресных tests passed**, Ruff passed. В следующих
Compose-переключениях последним подключается frozen frontend v10 overlay;
baseline off не меняет images и не подменяет основной baseline. Этот driver
ещё не запущен: текущие private controls должны завершиться первыми.

Baseline-control дополнительно сверяет actual backend flag/runtime через API
и только выбранный boolean frontend env через отдельный Node process; полное
env содержимое не выводится. Flags фиксируются до/после measurements, mismatch
останавливает очередь. Перед обоими states пересоздаются только backend и
frontend (`--no-deps`), чтобы не сравнивать старый Node heap с новым;
PostgreSQL/Qdrant сохраняются. On восстанавливается в finally. Итоговая
адресная регрессия queue/control gates: **19 passed**, Ruff и diff check passed.

Закрыт дополнительный false-pass в storm gate (2 RED → GREEN, всего **5
control-gate tests passed**): одни 60 с и положительный attempted не доказывали
номинальную нагрузку. Новый gate требует известную согласованную achieved rate
и requested rate, а также starting/peak private RSS и рост <=128 МиБ.
Ruling: для paced номинальных 10 000 попыток/с допуск границы измерения — 0,1%;
фактическая скорость всегда публикуется. Это учитывает дискретный последний
arrival и конечную длительность, но не принимает недогруженный генератор.
128 МиБ — дополнительный строгий private storm budget; он не заменяет live
backend/Node RSS. Старый storm artifact без starting RSS не выдаётся за этот
новый gate. Измерительные harness-правки не требуют пересборки app images.

### Завершённый текущий search_stub control

Все 12 rows прошли каждый strict gate; максимум capture p95 +5,12% / +1,52 мс,
capture+ZIP +4,24% / +1,25 мс, private tree RSS growth 5,69 МиБ. Нулевая потеря
и успешное пересечение business requests с building проверены в каждой row.
10 мс — задержка управляемого search stub, а не обещанная полная latency
TestClient/framework. Это не измерение live Node RSS.

| Повтор | Уровень | c | Baseline p95, мс | Capture p95, мс | Capture+ZIP p95, мс |
|---|---|---:|---:|---:|---:|
| 1 | standard | 1 | 14,09 | 14,45 | 14,49 |
| 1 | detailed | 1 | 14,15 | 14,41 | 14,53 |
| 1 | standard | 8 | 29,38 | 30,37 | 30,62 |
| 1 | detailed | 8 | 29,91 | 30,96 | 31,14 |
| 2 | standard | 1 | 14,12 | 14,42 | 14,53 |
| 2 | detailed | 1 | 14,00 | 14,35 | 14,46 |
| 2 | standard | 8 | 29,59 | 31,10 | 30,53 |
| 2 | detailed | 8 | 29,38 | 30,40 | 30,42 |
| 3 | standard | 1 | 14,33 | 14,54 | 14,61 |
| 3 | detailed | 1 | 14,19 | 14,40 | 14,41 |
| 3 | standard | 8 | 29,85 | 30,75 | 30,85 |
| 3 | detailed | 8 | 29,98 | 30,76 | 31,01 |

Raw/analysis JSON сохранены в CT `fixed-probe/testclient-search_stub-*-v13-v10*`;
status и gates находятся в `remaining-acceptance-v13-v10-state.json`.

## Учёт замечаний после повторного анализа, 30.09.2026

Это текущий статус; следующие разделы сохраняют историю предыдущих решений
и версий. Полная performance-приёмка остаётся открытой. Повторная общая
оптимизация памяти и новые полные матрицы в этом цикле не выполнялись.

- **Исправлен переход HTTP-очереди.** После каждого блока проверяются все
  восемь численных/контрактных gates, полнота комбинаций 3 × c1/c8 × режимы,
  corpus, stopped-флаг и известные image IDs. Exit 0 без этих доказательств
  больше не означает passed. Отказ сохраняет failed и failed_gates в ledger;
  анализ сохраняется отдельным JSON. Проверяется и исходный уже завершённый
  active блок до первого следующего этапа. Tail отвергает старый ledger без
  numerical gates. Ошибка запуска/прерывание Python также фиксируется как
  failed. SIGKILL по-прежнему может оставить running; существующий ledger
  запрещает автоматическое возобновление и требует проверки фактических процессов.
- **Исправлена частота наблюдения ZIP.** По умолчанию список пакетов
  запрашивается раз в 3 с, как в `DiagnosticsPanel`. Частый опрос допускается
  только явно через `--bundle-poll-interval 0.05` как отдельная диагностическая
  нагрузка. Интервал и число poll запросов сохраняются. Перекрытие определяется
  по actual prepare/ZIP child intervals; короткий ZIP не обязан попасть между
  двумя API polls. Обе фазы по-прежнему требуют пересекающихся запросов.
  Получение ready при таком опросе содержит задержку наблюдения до 3 с;
  `build_seconds` не следует считать точной длительностью работы child.
  Старые результаты с 50-мс опросом не переименованы в результаты новой методики.
- **Расхождение 7328 → 7312 подтверждено, историческая причина не доказана.**
  Read-only анализ сохранённых CT 102 JSONL одного сеанса показал совпадающие
  начало и конец backend/frontend; недостающие события находятся внутри сеанса.
  Агрегаты не позволяют восстановить их точное время или состояние control.
  Нулевые recorder_delta относятся к measurement, не доказывают отсутствие
  ошибки в warmup. Активность Ollama этим анализом не установлена.
- **Механизм гонки воспроизведён отдельно.** Управляемый probe принимает
  7312 событий, затем завершает старый inactive poll после нового active poll
  и отправляет два пакета по 8 событий. В варианте без guard записано 7312,
  с текущим guard — 7328; dropped/invalid/expired_queue в обоих случаях 0.
  Это показывает пропуск при admission, а не потерю уже принятых событий.
  Reference — намеренно изменённый исходник, не утверждение о фактическом
  расписании исторического v9 прогона. Артефакт `control-race-mechanism.json`.

Проверки этого цикла: **17 backend tests passed**, **3 Node race tests passed**,
управляемый differential probe passed. Новые тесты queue/observer сначала
воспроизвели ошибку (RED), затем прошли (GREEN). Полные уже завершённые наборы
не повторялись: backend v13 — **2643 passed, 27 skipped**, frontend v10 —
**275 passed, 1 skipped**. Эти цифры сверены по сохранённым финальным логам;
новые harness-правки проверены отдельно.

Следующий ограниченный этап: зафиксировать сборку frontend с guard и проверить
один проблемный сценарий standard/c8/active ZIP вместе с baseline, по новой
частоте опроса. Любой отказ останавливает очередь; расширение полного набора
возможно после разбора отказа. Исходные пороги и остальные обязательные
пункты приёмки сохраняются. Текущий исторический v13/v9 active результат
не принимается из-за `all_capture_counts_pass=false`.

## Продолжение полной приёмки по выбору пользователя, 30.09.2026

Пользователь выбрал **«Продолжить полную приёмку исходного плана»** вместо
завершения функционального этапа. Performance/TTL пункты ниже обязательны;
частичная приёмка не согласована. Стенд: backend v13 / frontend v9,
backend image ID `sha256:b51d0b122dc243d67417f4bd520d67f8629a08981ff63124e9dd296a6fe52d8d`.

Перед длинными сериями расширен probe, пороги не изменены:

- Полный JSON ответа поиска сравнивается с baseline по SHA-256; body не
  сохраняется. Только fingerprints заданных synthetic запросов.
- Actual prepare/ZIP child определяется по allowlisted module tokens в
  backend cgroup каждые ~20 мс. Границы приблизительны; отдельно считаются
  p50/p95/p99/max и число пересекающих фазу запросов. Учитывается полная
  latency пересекающего запроса, не её обрезок.
- Probe ждёт подтверждения frontend start/stop в `sessions.json`, затем
  после stop сверяет HTTP/operation/Qdrant totals с количеством сделанных
  warmup+measurement вызовов. Для stopped — только warmup. Fixture включает
  одну BM25 Qdrant операцию на поиск; counters не равны числу JSONL-строк.
- Сохраняются numeric host `/proc/stat`/CPU pressure и 20 самых медленных
  request intervals для оценки внешнего шума. Это не устанавливает активность
  именно Ollama. Прежний RSS максимум с warmup остаётся нормативным,
  `measurement_peak_rss_bytes` — дополнительная метрика.
- Analyze добавляет response/counts/phase flags. Старые JSON без этих
  доказательств не объявляются полностью проверенными по новым флагам.

v12 smoke выявил host-root ошибку подсчёта: backend данные находились в
`backend/events`, не в `events`. Пересчёт существующих файлов без нового
прогона устранил эту ошибку и показал runtime дефект: HTTP backend route
записывался `/unknown`, если inner ASGI router работал с копией scope.
Операции, Qdrant и frontend counts уже сходились. v13 исправляет только
allowlist route classification: зарегистрированный template, если доступен;
иначе static/parameter template из `ROUTE_TEMPLATES`; иначе `/unknown`.
Значения URL-параметров не возвращаются. Copied-scope тесты дали RED;
context/privacy/probe после исправления — 34 passed, Ruff passed.
Первый GREEN запуск содержал неверную сериализацию canary assertion в
тестах; assertion исправлен, 34 относятся к чистому повтору. Review этого
runtime/harness изменения важных замечаний не нашёл.

Живой короткий v13 контроль: 896 вызовов, backend HTTP=896, frontend HTTP=896,
operations=896, Qdrant=896; counts/response flags true. ZIP ready, actual
prepare пересекли 136 запросов, ZIP — 24. Это 2/10-секундный контроль
инструмента, не acceptance latency. Артефакт `instrument-smoke-v13-v9.json`.
`instrument-smoke-v12-v9.json` сохранён с первоначально ошибочными counts
как отвергнутый smoke, не как доказательство потерь.

Один ранее запущенный общий backend набор завершён:
`2631 passed, 27 skipped, 140 warnings`, 3515,16 с, exit 0. Он начат до
cleanup/route правок и не объявляется полным v13 набором. Запущен свежий
полный v13 pytest на локальном Docker Desktop; CT 102 измеряет final 1-doc
normal матрицу отдельно, без pytest/build на CT. Runtime frozen v13/v9.

### Final normal 1-doc v13/v9

Завершены все 18 строк: 3 повторения × concurrency 1/8 × baseline/standard/detailed,
30 с warmup + минимум 60 с / 1000 измеренных запросов. Все исходные критерии и
новые response/counts flags пройдены. Максимальный p95 overhead: standard
5,65%, detailed 5,64%; backend peak RSS growth 0,12 МиБ, Node 12,12 МиБ.
Без непреднамеренных потерь; четыре ожидаемых totals сходятся во всех capture
строках. Это результат без ZIP, не закрытие всей performance-приёмки.
Артефакты: `http-matrix-1doc-v13-v9.json` и
`http-matrix-1doc-v13-v9-analysis.json`. Начат отдельный полный active ZIP блок.

Короткий same-input smoke: 1000 одинаковых событий, baseline reference и
baseline/standard/detailed с private prepare/ZIP, c=8, 2/10 с. Все четыре
строки завершены, SHA-256 исходного/prepared/ZIP потока и CRC сходятся.
Он проверяет инструмент, а не исходные performance пороги; private supervisor
RSS не заменяет RSS живого backend/Node. Полная серия ещё требуется.

### Дополнительные конечные контракты

Native Windows: 2 passed — общий backend spool >90% квоты, узкая подготовка
с 1 событием проходит, широкая отказывает без оставшихся pins/credits;
ускоренный capture/bundle TTL с API query/preview/download и существующим
download lease. Часы хоста не меняются: тест вызывает tick/sweep с будущим
временем и подменяет часы только isolated read-view. Физическое удаление
отложено до release; ZIP завершён и CRC проверен. Final CT повтор обязателен.

Исходы generation publication, двух чанков с transient timeout/retry,
отмены во время index, stream delta/result/error и error reference после
HTTP 200: **7 passed в каждом из baseline/standard/detailed**, всего 21.
Policy настоящая, внешние LLM/Qdrant заменены управляемыми test fixtures.
Дополнительная real-Qdrant нагрузочная матрица измеряется отдельно.
Первая попытка старого retry-fixture имела doc_id `partial`, давала invalid
метаданные при сохранении исхода; заменена на canonical doc_id и повторена,
после чего invalid/dropped/storage/drain/expired_queue равны 0.

Полный frontend script `node --test`: 272 passed, 1 skipped, 0 failed.
Начальный glob `.mjs` давал лишь 267 passed; финальный результат включает `.js`.
Новый probe умеет измерять immutable original app tree без product-переключателя
legacy: runtime-view selector добавляется только при наличии в исходной версии.
После изменения harness — 9 probe contract passed, Ruff passed.

CT очередь `run_diagnostics_final_ct.py` ждёт окончание текущей active матрицы,
затем выполняет stopped 1-doc, normal/active/stopped 100-doc, fixed input,
native TTL/quota и settings/search_stub sequential. Она не меняет рабочий
`okf-knowledge` проект и не выполняет build/pytest одновременно с perf.
Завершение команды не равнозначно прохождению числовых критериев; JSON после
каждого блока анализируется отдельно. Ledger: `final-acceptance-v13-state.json`.

## Ограниченная финализация по поручению пользователя, 30.09.2026

**Итог:** реализация подготовлена к review; полная performance-приёмка
остаётся открытой. Повторять полные матрицы до случайного зелёного результата
прекратили. Пороги не менялись; commit/push/merge/production не выполнялись.

### Конечные проверки и изменения

- Короткая диагностика памяти v11/v9: 370 numeric samples, инспектор
  временно включён только в тестовом Next. RSS Next: старт 93,78 МиБ,
  peak 162,61, перед контрольным GC 137,22, после 89,62; heapUsed:
  34,67 / 76,70 / 51,54 / 30,85 МиБ. Runner оставался 44,51 МиБ,
  external 3,49–3,65 МиБ. Cgroup memory включает дополнительный observer
  и не подменяет RSS-gate. В этом коротком опыте память после GC вернулась
  ниже начальной: подтверждено временное удержание heap, устойчивую утечку
  этот опыт не показал. Причина всех исторических пиков и влияние Ollama
  этим не доказаны. Workload сокращён до 15 с warmup / 20 с measurement,
  поэтому опыт не считается приёмочной серией. Входы ZIP различались;
  сравнения алгоритма на фиксированном входе здесь нет. Inspector отключён
  рестартом только тестового frontend, production GC не добавлялся.
  Артефакты: `diag-memory-v11-v9.json`, `rss-diagnostic-v11-v9.json`,
  `rss-diagnostic-v11-v9-summary.json` в `tests/artifacts/diagnostics/`.
- Финальный статический review нашёл один P2: если `delete_tree` после
  остановки prepare-child получает EACCES, read-view pins не освобождались,
  а записанные до commit-frame байты теряли quota accounting. Дефект
  воспроизведён RED-тестом (used_bytes не вырос на оставшиеся 1024 байта).
  Исправлены только `snapshot.py` и `worker_protocol.py`: view освобождается
  в `finally`, surviving output учитывается до возврата credits. Если даже
  stat/accounting недоступны, резерв сохраняется и storage помечается
  degraded; новый экземпляр store сканирует остатки до дальнейшей записи.
  Отказ удаления может оставить учтённые файлы до последующей очистки,
  но больше не оставляет pins и не освобождает необеспеченный резерв.
  Повторный review исправления важных замечаний не нашёл.
- Backend v12 собран поверх v11 с этими двумя файлами; CT image ID
  `sha256:269ca91ac78e46b21472f57191723a1ad9182476a3b8cad6b154228b9f609066`.
  Frontend v9 сохранён. Локальный Docker BuildKit имеет другой image ID;
  CT использовал legacy builder. Это отдельные сборки, их digest равенство
  не утверждается. Пакет передачи содержит только два исходных файла и
  Dockerfile, без runtime env/данных/секретов. Менялся только тестовый project.
- Linux: 30 passed в полном затронутом quota/prepare/queue наборе.
  Kill и shutdown реального prepare проверены после credited write до
  commit, включая отказ удаления. Ещё 2 passed для реального ZIP-child:
  штатный `bundle_worker.main()` остановлен на тестовом checkpoint после
  настоящего `ZipFile.writestr`/flush сжатого содержимого. Проверены failed
  без sha256, отсутствие опубликованного ZIP, временных файлов, pins и
  новых reservations. Checkpoint существует только в тесте.
- Windows native: 33 passed, 35,46 с — prepare/protocol/store/batching,
  включая кириллицу/пробелы, junction rejection, partial writes и отказ
  измерения surviving output. Артефакт `windows-final-v12.log`.
  Ruff всего backend `--no-cache`: `All checks passed!`; `git diff --check`
  прошёл. Frontend после v9 не менялся: ранее полный Node 272 passed,
  1 skipped, ESLint 0 errors / 40 существующих warnings, Next build passed.
- CT 102 lifecycle v12/v9: start → reproduce → stop → preview → download
  → CRC → delete, reader 403 и повторный GET 410 прошли. Manifest v2,
  partial=false, gaps=[], counters known, 2 events, ZIP 2504 bytes.
  Артефакт `lifecycle-final-v12-v9.json`. Первый вызов слишком рано после
  restart получил 502; повтор после старта прошёл. Frontend не проксирует
  `/health/ready`, поэтому тот readiness URL не подтвердил готовность;
  прямой backend `/health/ready` затем проверен отдельно: 200. CSRF в
  simulation не проверяется этим smoke, его покрывает production auth suite.
- Error storm v12: 60,000 с, 599997 попыток с уникальными request IDs,
  фактически 9999,95/с; written=599997, dropped=0, invalid=0, repeats=0,
  queued=0, reserved_bytes=0, degraded=false. Peak RSS отдельного probe
  процесса 37,12 МиБ, spool 7,91 МиБ при quota 16 МиБ. После ротации
  осталось 23181 событие в 8 JSONL: written — накопленный счётчик,
  сохранение всех 600000 событий не обещается. Health 58/58, максимум
  165,37 мс; live indexed search 58/58, максимум 26,34 мс. Probe search
  требует непустые hits, не равенство всей выдачи. Нагрузка действует на
  отдельный recorder с частным spool в CT; live API проверяется параллельно.
  Это library storm + live availability, не 10000 HTTP ошибок/с через API.
  Первый запуск остановлен Docker/AppArmor до начала нагрузки; тестовый
  контейнер запущен с nested-LXC `apparmor=unconfined`, PVE не менялся.
  Локальная регрессия шла на Windows Docker Desktop, на CT pytest/build
  не работали. Артефакт `error-storm-v12.json`.
- Полная backend-регрессия: итог будет внесён после завершения единственного
  уже запущенного полного набора. Он начат до двух финальных cleanup-правок;
  эти правки отдельно проверены указанными GREEN-наборами. Полный набор
  нельзя называть свежим полным прогоном исключительно v12. GitHub CI не
  запускался: отправка ветки не поручена.

### Итоговая карта обязательных критериев

`covered` означает указанное доказательство; `required` — открытый критерий,
не отменённый этим отчётом; `deferred` — работа за рамками согласованной
ограниченной финализации, без утверждения о полной приёмке.

| Критерий | Статус | Доказательство / ограничение |
|---|---|---|
| Уровни, aggregates, bounded queues, correlation, safe metadata, manifest v1/v2 | covered | Профильные backend/Node tests, UI/runbook; детали в хронологии ниже |
| Privacy/RBAC/audit, revoked access, delete/download, DB-down, restore без spool | covered | Отрицательные наборы, offline/restore smoke; финальный simulation lifecycle не доказывает CSRF |
| Prepare/ZIP kill, shutdown, credits/pins и отказ cleanup | covered | Реальные child tests после записи, RED→GREEN исправление P2; surviving files остаются учтёнными |
| Windows paths/junction/partial writes | covered | 33 passed финального native набора |
| TTL expiry и действующий download lease | covered частично / required | Clock-driven unit/API tests есть; ускоренный end-to-end TTL на финальной CT-паре не проведён |
| Normal 1-doc p95/RSS, 3 повтора c1/c8 | covered для v11/v9 | Standard +5,55%, detailed +5,81%; Node +13,66/+17,39 МиБ; потерь нет. v12 cleanup error path проверен функционально |
| Concurrent ZIP p95/RSS, 3 повтора | required | p95 проходит; общий RSS флаг первого и повторного v11/v9 блоков false. Успешные standard/detailed строки повторного блока не отменяют красные baseline+ZIP controls |
| Stopped ZIP финальной пары | required | v11/v9 остановлен на 14/18 строках; полный v11/v8 — более раннее доказательство |
| Normal/ZIP 100-doc финальных образов | required | Есть полные старые серии, включая normal v9/v7; свежая v12/v9 серия не проводилась |
| 10-мс stub, генерация/stream/retry/cancel | covered частично / required | Функциональные контракты проверены; строгая финальная нагрузочная приёмка не подтверждена |
| ZIP на одинаковом входе и отдельные phase latency distributions | required | Текущие HTTP-матрицы измеряют real-level объёмы/общую минуту; одинаковый input и prepare/ZIP distributions не закрыты |
| Baseline-off control / все ожидаемые aggregate totals в live workload | covered частично / required | Детерминированные unit fixtures есть; полного live равенства response/count totals probe не проверяет |
| Error storm bounded memory/disk + availability | covered в указанной области | 60 с / ~10000 событий/с, отдельный recorder; live health/search успешны |
| Bundled/external upgrade/rollback и UI RU/EN/390px/2 tabs/post-200 | covered ранее | Выполнены в хронологии; последний v12 проверен bundled lifecycle, полный external drill заново не запускался |
| Backend полная регрессия / Node / lint / build | частично до итогового pytest | Финальный cleanup — focused GREEN; Node/lint/build выше; статус полного набора отдельно |
| Фактический GitHub CI / commit / push / merge / production | deferred | Нет отдельного поручения; результаты локальных команд не выданы за CI |

**Открытое решение:** принять текущую реализацию как функциональный результат
с performance-gates в отдельном продолжении либо продолжить строгую
приёмку исходного плана. Рекомендация — первый вариант: runtime-изменения
завершены, следующий шаг должен иметь фиксированный бюджет и одну конкретную
гипотезу/методику RSS. До отдельного решения статус остаётся частичным;
порог +32 МиБ не заменён GC/cgroup-метрикой и Ollama не объявлена причиной.

## Переоценка задачи по запросу пользователя, 30.09.2026

**Вывод:** основная реализация выполнена; полная приёмка не закрыта.
Рекомендуется прекратить повторение полных матриц без новой проверяемой
гипотезы, заморозить backend v11/frontend v9 и завершить конечный набор
проверок корректности. Изменение критериев приёмки этим выводом не выполняется.

На момент аудита до сохранения последнего partial найдены 32 различных
HTTP matrix JSON (32 уникальных SHA-256), 549 строк и 13,48 часа суммы
`warmup_seconds + elapsed_seconds`. Это накопленное время измерительных
интервалов, не полная длительность задачи и не оценка стоимости токенов.
Последний stopped-прогон прерван: 14 из 18 строк сохранены отдельно в
`tests/artifacts/diagnostics/http-matrix-1doc-stopped-v11-v9-partial.json`.
Опрос процессов CT 102 не обнаружил работающего matrix probe. Новые
нагрузочные прогоны в рамках этого анализа не запускались.

Реальный результат: standard/detailed, агрегация и выборка цепочек,
ограниченные очереди с резервом ошибок, пакетная запись, индекс сегментов,
чтение по leases, child prepare/ZIP с credits, manifest v2, совместимость,
RU/EN UI и runbook реализованы. Это существенная переработка подсистемы,
а не небольшая настройка лимита. Были найдены и исправлены реальные
дефекты: тяжёлый импорт ZIP-child, fork RSS, потеря принятого frontend
агрегата после продления lease. Доказательства и ограничения — в журнале ниже.

Последняя полная normal матрица v11/v9 проходит: p95 standard +5,55%,
detailed +5,81%; Node RSS +13,66/+17,39 МиБ, потерь нет.
Первый ZIP-блок имеет standard Node RSS +45,29 МиБ; в полном повторе
все standard/detailed строки проходят, но два baseline+ZIP контроля дают
+35,74/+47,82 МиБ. Общий RSS-флаг обоих ZIP-блоков остаётся false.
Это не доказанная утечка и не полностью пройденная RSS-приёмка.
Размеры архивов различаются, но это само по себе не доказывает причину
Node RSS. Снимки CPU CT 101 показывали почти простой; влияние Ollama
на другие интервалы не установлено.

Причины затягивания:

- Поздно исправлялся измерительный инструмент: сначала пропускался дочерний
  Next process, затем backend ZIP-child (подробности ниже). Надёжность
  проб следовало проверить короткими контрольными опытами до длинной матрицы.
- После изменения образов расширялся повтор проверок без явной карты того,
  какие результаты действительно устарели. Поиск редкого RSS-пика превратился
  в последовательность полных прогонов вместо профилирования причины.
- Методика RSS вычитает максимумы разных окон
  (`backend/test_scripts/analyze_diagnostics_matrix.py:27`); sampler стартует
  до прогрева (`backend/test_scripts/probe_diagnostics_http_matrix.py:186`).
  Эти числа годятся для консервативного gate, но не устанавливают причину
  выделения памяти. GC/allocator и соседняя нагрузка остаются гипотезами.
- p95 считается по всей минуте, overlap — только количеством запросов
  (`probe_diagnostics_http_matrix.py:248`, `:256`). В повторном ZIP-блоке
  время submit→ready составляло 2,42–8,60 с; отдельных latency distributions
  prepare/ZIP в итоговом JSON нет. Проверка поиска требует непустые hits
  (`:159`), а не полного равенства результатов. Нулевые loss counters также
  не заменяют проверку ожидаемых aggregate totals.
- Отчёт разросся в хронологию, а checkboxes плана не отражают готовность.
  Частые сообщения по каждой строке скрывали главное: что блокирует завершение.

Рекомендуемый порядок завершения, без автоматического запуска:

1. Заморозить текущие образы и составить короткую таблицу обязательных gates
   с имеющимися доказательствами. Старые UI/migration/RBAC результаты не
   повторять автоматически после изменения внутреннего запуска ZIP-worker.
2. Один ограниченный по времени RSS diagnostic: time series Node RSS/heap,
   backend RSS и cgroup memory, фазы сборки и нагрузка хоста. Контрольный ZIP
   с фиксированным входом плюс одинаковый workload. Пороги +32/+128 МиБ
   сохраняются; дополнительная метрика объясняет RSS, не подменяет его.
3. Завершить реальный kill активного child и освобождение pins/credits,
   error storm с проверкой доступности, финальный lifecycle и полную регрессию
   текущего кода. Ранние 2629 backend tests предшествуют v11; 272 Node tests
   относятся к v9. Фактический GitHub CI пока не запускался.
4. Только по результату диагностики выбрать одну итоговую приёмочную серию
   для затронутого пути. Уже открытые пункты исходного плана (в том числе
   stress/100-doc/TTL) явно оставить required, covered либо deferred с
   обоснованием; сокращение обязательной приёмки требует согласования.
5. Если RSS не подтверждён, зафиксировать невыполненный критерий и принять
   явное решение о продолжении или ограниченной приёмке. Не перезапускать
   серии до случайного зелёного результата. Commit/push/production этим
   аудитом не разрешаются.

Это инженерная переоценка текущей задачи, а не расследование дефекта
superpowers или экспорт истории чата. Полный forensic workflow не запускался.

Состояние: выполнение на ветке `admin-diagnostics`, HEAD до изменений `2614bfa22fdda249985956c77f79ddeab5cc31bc`. Исходный worktree был чистым; план перенесён из `main` как отдельный новый файл. Commit, push и deployment не выполнялись.

## Задача 1: исходное состояние

Среда: Windows `Windows-10-10.0.19045-SP0`, Python 3.12.10 из `backend/.venv` основного checkout, Node v26.3.0, 16 логических CPU. Файловая система, объём RAM и фоновые процессы не зафиксированы: доступ к Windows CIM и `Get-Volume` в этой песочнице запрещён. Синтетические данные, SQLite и TestClient; внешних PostgreSQL, Qdrant, LLM нет. Длительность прогрева 30 с и 200 запросов на каждый режим. Это локальная отправная точка, не Linux acceptance из раздела 8 плана.

Перед изменением кода 55 профильных тестов (`schema`, `sessions`, `recorder`, `store`) прошли. Первая попытка системным Python прервалась при collection из-за отсутствующего `sqlalchemy`; повтор выполнен проектным интерпретатором. Новые тесты контракта probe: 3 RED по отсутствующим уровню, пересечению фаз и peak RSS; после минимальной реализации 3 GREEN. Полная регрессия ещё не запускалась.

| Сценарий | baseline p95, мс | legacy capture p95, мс | capture + ZIP p95, мс | capture | capture + ZIP |
|---|---:|---:|---:|---:|---:|
| /api/settings | 2.679 | 2.994 | 5.987 | +11.7% | +123.5% |
| /api/search, 10-мс stub | 14.674 | 15.986 | 18.166 | +8.9% | +23.8% |

ZIP построен в обеих сериях. Результаты: `tests/artifacts/diagnostics/diagnostics-levels-baseline.json` и `tests/artifacts/diagnostics/diagnostics-levels-search-baseline.json`. Пики RSS parent/child/Node и фазовое перекрытие не были частью старого probe; их нельзя восстановить из замера после завершения. Они являются обязательными полями обновляемого probe и будущей приёмки.

Решение: старую серию хранить под меткой `legacy capture`, новый `standard` и `detailed` сравнивать только после реализации уровней и повторного замера на одинаковой версии стенда. Цена ошибочного выбора: ложный вывод об ускорении, если смешать версии или p95 разных систем.

## Промежуточный результат задач 2–4 (2026-09-29)

Контракт: `SessionStart` по умолчанию `standard`; новый SQL policy snapshot immutable на запуске, старая строка после forward-миграции остаётся `detailed`/version 0. Python и Node принимают строгие v1/v2-события по общему corpus, не включая текстовые поля. RED: 4/4 отсутствующих контрактов; GREEN: 41 passed, 1 skipped backend (`test_diagnostics_policy_contract`, schema, migration, API), 20 passed, 1 skipped Node schema/server. PostgreSQL-миграция в приватной схеме ещё не запускалась.

Ранний backend policy engine и bounded aggregate buffer подключены к recorder. 1000 успешных Qdrant-вызовов дали один aggregate c точными count/sum/max/buckets, без вызова event encoder на producer до flush. Операции `detailed` выбираются по серверному request/operation ID; старое решение не сбрасывает новую сессию. Гонки stale-session и смешения overflow между сессиями воспроизведены RED и исправлены. RED→GREEN: 9 policy и 3 aggregation tests; интеграция context/dependencies/pipeline/recorder/sessions: 48 passed. Это ещё не доказывает все producer boundaries из задачи 3.

Backend FIFO: обычные события могут занять 3584 из 4096 слотов; 512 зарезервированы важным. При queue size 8 доступно 7 обычных и 1 важный слот, при размере 1 обычные не принимаются. RED→GREEN: 3 admission tests; после восстановления Queue.join-прокси профильная регрессия recorder/lifecycle/integration: 23 passed. При этом `BarrierResult`, checkpoint counters и producer-lock/FS разделение ещё не завершены. Ни один промежуточный статус не является выполнением общего p95/RSS критерия.

## Промежуточный результат задач 5–6 (2026-09-29)

Backend: `SegmentIndex` заменил повторный обход spool на учёт owned-файлов; запись событий объединяется до 64 КиБ/50 мс с границей целой NDJSON-строки. Контрольный тест partial write/ENOSPC проверяет откат к прежней длине. Внешнее изменение обнаруживается maintenance reconciliation. Горячий selector читает опубликованный immutable view без lock, удерживаемого SQL/FS; устаревшая active projection не может переписать более новую stop revision. Тесты bundle/queue/retention: 34 passed; lock/session/lifecycle: 21 passed. Долгая SQL/FS работа в самих control-операциях пока остаётся, хотя producer selector её не ждёт.

Frontend: control projection v2 передаёт только безопасный снимок policy; Node отвергает v1/неизвестную policy. `standard` сводит успешные proxy-вызовы в bounded histogram aggregate, а ошибки пишет отдельно; выбор подробного успешного вызова не основан на входящем request ID. Очередь сохраняет резерв важных событий, writer использует cached file ledger и пакетную запись NDJSON. Тест с 50 строками подтвердил отсутствие повторного обхода spool в admission. Node suite: 25 passed, 1 skipped (SIGTERM runner, Linux-only). Backend projection suite: 21 passed.

Это не завершает задачи 5–6: durable counter checkpoints, строгий stop barrier, полная проверка concurrency/partial batch и Linux-runner ещё ожидают исполнения. Изменения не развернуты.

## Реализация задач 7–9 и локальная проверка (2026-09-29)

Чтение admin API теперь фиксирует короткий `ReadViewLease` над дескрипторами
сегментов: identity, длина префикса и срок жизни. Оно не создаёт временный
`snapshots/` и возвращает максимум 100 событий на страницу. Backend-сегменты
удерживаются pins до `release`, а frontend-сегменты читаются с отдельной
проверкой пути и идентичности. Следующая страница передаёт cutoff и токен
состава view; при изменении состава возвращается `coverage_changed`. Тесты
read-view/store/API/retention проверили ограниченный page, append за префиксом,
rotation и освобождение lease. Потеря байтов при подмене содержимого без смены
identity/mtime для admin page не доказана, поэтому это остаётся риском.
Позже добавлена проверка mtime для сегмента прежней длины: тест подтверждает
обнаружение такой подмены и продолжение чтения при обычном append. Подмена
ранее закреплённого префикса одновременно с append всё ещё требует проверки
SHA в prepare worker; admin page не обещает криптографическую неизменность.

Подготовка пакета вынесена в отдельный PID через `prepare_worker.py`.
Версионированный IPC ограничен 64 КиБ на frame и 4 МиБ на prepare message,
проверяет UUID job и возрастающую sequence. Child в одном проходе проверяет
safe events, фильтрует и формирует три нормализованных component-потока;
parent выдаёт credits только для собственных output paths. Child повторно
проверяет SHA-256 прочитанного префикса и identity/mtime при открытии.
Сборка ZIP использует готовые component-файлы, проверяет CRC/checksum.
После ошибки/kill parent освобождает grant, pins и временный каталог;
отдельно исправлена очистка child handle после ошибки до ZIP. Адресный
повтор prepare/bundle queue: 17 passed. Реальные Linux kill/race и symlink
проверки ещё не выполнены.

Manifest v2 содержит `capture_policies`, `coverage_modes` и
`loss_counters_state`. Offline без SQL использует безопасную активную
проекцию, а при её отсутствии сообщает `unknown`. UI показывает уровень,
область, ограничение времени и различает агрегацию/выборку от потерь;
старый backend не получает неизвестное поле. Старые v1 ZIP не
перезаписываются. Frontend: 69 passed, 1 skipped в diagnostics+i18n;
Next production build успешен. Windows backend `pytest -k diagnostics` сначала
дал 5 отказов старых утверждений о manifest v1 и одиночной записи. После
обновления тестов под v2/batch writer адресный повтор: 35 passed. Полный
backend suite выполняется отдельно, статус будет указан ниже.

## Проверки после задач 7–9 (2026-09-29)

Полная backend-регрессия до последних адресных исправлений прошла на Windows:
`2615 passed, 24 skipped, 21 warnings`, exit 0, 2296,90 с. Полный Node
`node --test`: `265 passed, 1 skipped`; ESLint: exit 0, 40 существующих
предупреждений, ошибок нет; production build Next: exit 0. После этих команд
были исправлены checkpoint счётчиков, определение `unknown`, чтение при append
за закреплённым префиксом и ошибка учёта frontend loss. Их покрыли адресные
прогоны (48, затем 20 и 5 passed), но общую регрессию нужно повторить.

В отдельном Compose project на CT 102 миграция PostgreSQL дошла до ревизии
`040b1c2d3e4f`. Чистый synthetic bundled smoke на тестовых образах backend
`20260929-v4` и frontend `20260929-v4` выполнил admin start → 20 запросов
`/api/settings` → stop → bundle → preview → download → SHA-256/CRC всех семи
файлов → delete и 410 при повторном download. Reader получил 403 на status и
download. Manifest v2: `partial=false`, `gaps=[]`, оба process counter states
`known`, покрытие сессии `aggregated`. Артефакт стенда:
`/opt/okf-diag-levels-20260929/lifecycle-v4-clean-result.json`. В режиме
simulation CSRF middleware намеренно пропускает проверку, поэтому этот smoke
не доказывает CSRF: её проверяет backend auth suite в production-конфигурации.

Первый HTTP probe был отброшен из приёмки: он учитывал RSS Node runner без
дочернего Next process. Исправленный probe суммирует RSS дерева PID 1 и
измеряет CPU через cgroup; короткий smoke подтвердил ненулевые значения.
Матрица с 30 с прогрева, минимум 1000 запросов и 60 с измерений на режим
запущена отдельно; её результаты будут добавлены после завершения всех трёх
повторов. Старые и короткие серии не считаются приёмкой.

## Настройка подробной выборки по повторному HTTP замеру

Изолированный CT 102, ext4, 4 CPU, 6 GiB RAM; synthetic корпус из одного
документа; одинаковые образы backend/frontend `20260929-v4`; прямой HTTP путь
через Next → backend → PostgreSQL/Qdrant. На каждый режим: 30 с прогрева,
не менее 1000 запросов и не менее 60 с измерения. Порядок режимов менялся.
При исходном лимите 20 успешных цепочек/с повтор concurrency 1 дал для
`detailed` +8,47%, +7,67% и +10,98% p95 к baseline соответствующего повтора;
третья серия не прошла предел +10%. Артефакты:
`http-matrix-1doc-c1-repeat-v4.json` и `-analysis.json` в
`tests/artifacts/diagnostics/`.

Только на тестовом runtime env лимит снижен до 10 цепочек/с, без изменения
образов и порога приёмки. Повтор полного блока concurrency 1 дал:

| Повтор | baseline p95, мс | standard, % | detailed, % |
|---:|---:|---:|---:|
| 1 | 18,642 | +0,44 | +3,66 |
| 2 | 17,884 | +3,01 | +7,14 |
| 3 | 18,064 | +4,21 | +5,36 |

Все шесть capture-серий прошли p95 ≤ baseline × 1,10, backend RSS ≤128 МиБ,
Node RSS ≤32 МиБ, 30/60 с и ≥1000 запросов; приращения loss counters backend
и Node равны нулю. Максимальный прирост backend RSS 45 056 байт, Node
1 720 320 байт. Машинный результат:
`tests/artifacts/diagnostics/http-matrix-1doc-c1-trace10-analysis.json`.
RED тест показал, что прежняя настройка выбирала одиннадцатую успешную цепочку;
после смены default с 20 на 10 адресный набор policy/read-view/prepare:
20 passed. Ошибка вне выборки остаётся отдельным обязательным событием.
Окончательная приёмка требует ещё concurrency 8, 100 документов, ZIP и другие
сценарии раздела 8 плана. Эти серии не подменяются контрольным блоком.

Следующий полный HTTP-блок на 100 синтетических документах и том же образе
`v4` показал недостаточность лимита 10/с именно для `detailed`, concurrency 1:
превышение p95 +17,50%, +12,84%, +12,24% в трёх повторах. Все три standard
и все серии concurrency 8 прошли. Длительность, минимум запросов, потери и
RSS прошли для каждой серии. Артефакты:
`http-matrix-100doc-trace10-v4.json` и `-analysis.json`.

Контрольный повтор на том же корпусе и образе с **только** тестовым env
`DIAGNOSTICS_TRACE_LIMIT_PER_SECOND=5` дал для concurrency 1:

| Повтор | baseline p95, мс | standard, % | detailed, % |
|---:|---:|---:|---:|
| 1 | 63,383 | +6,29 | +4,18 |
| 2 | 63,488 | +1,99 | +6,10 |
| 3 | 63,128 | +1,87 | +6,45 |

Все шесть серий прошли p95 ≤ baseline × 1,10, RSS, потери и длительность.
Артефакты: `http-matrix-100doc-c1-trace5-v4.json` и `-analysis.json`. После
подтверждённого RED-теста лимит 5/с закреплён как default, GREEN policy/probe
13 passed. Значение 10/с остаётся документированным промежуточным опытом,
а окончательную Linux-приёмку нужно снять на финальном образе с default 5/с.

### Повтор на backend v6: p95 detailed остаётся выше порога

После переноса образа backend `20260929-v6` в CT 102 проверены одинаковые
SHA-256 архива на Windows/PVE/CT и совпадение 103 установленных Python-пакетов
с предыдущим образом. Из 164 Python-файлов приложения отличаются только
`config.py` (default 5/с) и `diagnostics/read_view.py` (проверка mtime при
чтении закреплённого префикса). Frontend остался `20260929-v4`. На 100
синтетических документах проведена полная HTTP-матрица concurrency 1/8,
три повтора, 30 с прогрева, ≥60 с и ≥1000 запросов на режим. Все standard и
серии concurrency 8 прошли, но detailed при concurrency 1 дал +18,04%,
+5,47%, +21,31% p95 относительно парного baseline. Дополнительный полный
блок concurrency 1 дал +12,92%, +19,45%, +8,42%; в обоих блоках два из трёх
повторов превысили порог +10%. RSS, длительность, объём выборки и приращения
backend/frontend loss counters прошли. Артефакты:
`tests/artifacts/diagnostics/http-matrix-100doc-default5-v6.json`,
`http-matrix-100doc-default5-v6-analysis.json`,
`http-matrix-100doc-c1-default5-repeat-v6.json` и соответствующий analysis.

Числовой срез трёх последних detailed-сессий показывает backend
`request_finished` p95 72,49/74,27/71,74 мс и frontend 73,20/75,57/68,50 мс.
Qdrant `dependency_call_finished` p95 остаётся 44,05/44,04/43,99 мс. Значит,
задержка уже видна в backend, а стабильный Qdrant не объясняет её колебания.
Локальный синтетический TestClient замер `DiagnosticRecorder.emit` дал обычно
0,02–0,08 мс на событие (p95 <0,16 мс), поэтому утверждать, что один вызов
`emit` добавляет 8–12 мс, нельзя. Причина на пути после Qdrant ещё не
изолирована; финальный p95-критерий не пройден.

Linux-тесты миграции и policy contract на этом же контейнере прошли: 7/7,
в том числе временная PostgreSQL schema. Первый прогон не нашёл только
JSON-фикстуру, которую поместили из уже существующего тестового каталога CT;
повтор прошёл полностью. Повтор admin lifecycle на backend v6 прошёл
start → stop → bundle → preview → download с SHA-256/CRC → delete и reader
403. Он честно дал `partial=true`, `gaps=[frontend_loss]`, потому что у
давно работающего frontend process до этого теста уже было
`expired_queue=1`. Поэтому этот прогон не считается чистой приёмкой отсутствия
потерь; предыдущий чистый v4 smoke и повтор v6 имеют разные условия.

### Разделение цены trace и standard summary

На том же backend v6 и корпусе 100 документов тестовый env изменил только
`DIAGNOSTICS_TRACE_LIMIT_PER_SECOND` с 5 на 1; frontend v4, образы и пороги
приёмки прежние. Полный блок concurrency 1 (30 с прогрева, ≥60 с и ≥1000
запросов на режим, три повтора) дал:

| Повтор | baseline p95, мс | standard, % | detailed, % |
|---:|---:|---:|---:|
| 1 | 63,994 | +16,07 | +2,57 |
| 2 | 63,877 | +17,56 | +1,88 |
| 3 | 63,684 | +12,74 | +3,05 |

Detailed прошёл p95 во всех трёх повторах, standard — ни в одном. Прочие
пороги (RSS, длительность/объём и новые loss counters) прошли. В standard
спул вырос примерно на 505–507 КиБ на серию, в detailed при trace=1 —
229–231 КиБ. Backend CPU standard около 15,4–15,5 с против baseline
13,3–13,8 с; detailed 14,2–14,4 с. p50 standard вырос примерно на 1 мс,
тогда как p95 — на 8–11 мс. Это указывает на цену регулярных summary или
записей, но не доказывает конкретный источник задержки внутри backend.
Артефакты: `http-matrix-100doc-c1-trace1-v6.json` и `-analysis.json`.
Следующий controlled block меняет только standard success budget с 20 до
10/с поверх trace=1.

На тех же образах и корпусе тестовый env с `trace=1`, `success=10` дал три
полных concurrency 1 повтора:

| Повтор | baseline p95, мс | standard p95, мс | detailed p95, мс |
|---:|---:|---:|---:|
| 1 | 63,063 | 64,734 (+2,65%) | 64,347 (+2,04%) |
| 2 | 63,480 | 64,739 (+1,98%) | 64,561 (+1,70%) |
| 3 | 63,754 | 64,228 (+0,74%) | 64,156 (+0,63%) |

Машинный анализ подтвердил все p95, RSS, длительность/объём и отсутствие
новых backend/frontend loss counters. Артефакты:
`http-matrix-100doc-c1-trace1-success10-v6.json` и `-analysis.json`.
Проверка test contract сначала дала RED на старом default 20; после этого
оба измеренных значения закреплены в `config.py`, спецификации, плане,
runbook и двух production env examples. Для окончательной приёмки нужны
повторы на образе, собранном уже с этими defaults, и остальные сценарии
матрицы; экспериментальный env этого не заменяет.

Полный backend suite непосредственно до смены этих двух defaults прошёл:
`2625 passed, 24 skipped, 21 warnings`, exit 0, 2324,73 с. После смены
контрактный RED выявил старое значение 20. Первый GREEN выявил две старые
тестовые предпосылки о бюджете 5/с; тест лимита in-flight теперь задаёт
независимый trace budget 2/с, тест default проверяет 1/с. Адресный повтор:
18 passed. Frontend после изменений в UI: 265 passed, 1 skipped; Next build
успешен; ESLint exit 0 с 41 предупреждением.

### Приёмочная матрица backend v7 с окончательными defaults

Образ `okf-diag-levels-backend:20260929-v7` собран из этой ветки, проверен
на отсутствие runtime `.env` внутри и совпадение полного списка установленных
пакетов с v6. SHA-256 переданного архива одинаков на Windows, PVE и CT 102:
`64ef5a6832a94d63ec942b89cff9c1d06b1357d835c656d69304a044f5592424`.
Фактический Docker image ID backend:
`sha256:14d913cdfa899e18e00011d8647aacb44e054bb59ecfc2f5c640373c1b310433`.
Frontend остаётся v4 (`sha256:de8ebc01fd8d4d2d22783d882cfb7e42970e4909b9a0bbc1e50486e44f8c285d`). Исходный synthetic runtime env не задаёт override
успешной выборки; запущенное приложение подтвердило `success=10`, `trace=1`.

Полная матрица 100 документов, concurrency 1/8, три повтора, прогрев ≥30 с,
измерение ≥60 с и ≥1000 запросов на каждый режим: все серии прошли
длительность/объём, пиковый прирост RSS и нулевые новые backend/frontend loss
counters. Для c1 все шесть capture-серий прошли p95 (+0,71…+7,98%). Для c8
пять из шести прошли; первый standard дал 135,62 мс против baseline
121,17 мс (+11,92%), выше порога +10%. Остальные c8: +0,27…+6,78%.
Машинный `all_p95_pass=false`; результат не объявляется принятым. Артефакты:
`http-matrix-100doc-defaults-v7.json` и `-analysis.json`. Чтобы отделить
случайный шум, запущен повтор полного c8 блока на тех же образах и корпусе.

Повторный c8 блок прошёл все машинные критерии: p95, RSS,
длительность/объём и отсутствие новых потерь. Три парных baseline дали
121,756/121,295/121,721 мс; standard — 122,094/123,143/121,726 мс,
detailed — 122,338/123,654/122,787 мс. Артефакты:
`http-matrix-100doc-c8-repeat-v7.json` и `-analysis.json`. Первое
превышение сохраняется в истории, но не воспроизвелось при повторе полного
блока на той же версии и данных.

Числовая сводка spool по сессиям первой матрицы
`spool-summary-100doc-v7.json` показывает состав записей. За три c1-сессии
standard backend записал 2780 `operation_summary` и 193
`success_aggregate` (1 543 524 байта всего); detailed backend записал 278
выбранных цепочек по шесть событий и 198 агрегатов (915 195 байт). Для c8:
2730 summary и 191 aggregate (1 567 803 байта) против 273 цепочек и 192
aggregate (933 566 байт). Frontend standard записал по 64 aggregate в c1 и
c8; detailed — 279/273 выбранных `request_finished` и 63/61 aggregate.
Выборка 1/с может занимать меньше места, чем standard с 10 summary/с;
числовые итоги оставшихся успешных вызовов сохраняются в агрегатах.

Для корпуса из одного документа выделены отдельные тома PostgreSQL/Qdrant и
отдельные каталоги data/diagnostics в том же CT 102. При первом запуске API
диагностики вернул 503: Docker создал новые bind mount каталоги под root, а
приложения работают под UID 1000. Это тестовая ошибка подготовки стенда:
production runbook уже требует `prepare_diagnostics_dirs.py` до `compose up`.
После выставления владельца только у новых каталогов backend/frontend
перезапущены; тестовые тома 100-документного корпуса сохранены.

Полная матрица 1 документа на backend v7/frontend v4 закончилась с exit 0:
concurrency 1 и 8, три повтора, для каждого режима прогрев 30 с,
измерение минимум 60 с и 1000 запросов. Image IDs до и после матрицы
совпали. Все 12 capture-серий прошли p95 ≤ baseline × 1,10, прирост RSS
backend ≤128 МиБ и Node ≤32 МиБ; новых backend/frontend loss counters нет.
Относительная прибавка p95 к парному baseline: standard c1
+2,81/+1,34/+1,88%, detailed c1 +1,92/+1,20/+1,26%; standard c8
+3,33/+3,77/+4,87%, detailed c8 +2,07/+3,50/+4,64%. Машинные флаги
`all_p95_pass`, `all_rss_pass`, `all_duration_requests_pass` и
`all_losses_pass` равны true. Артефакты:
`http-matrix-1doc-defaults-v7.json` и `-analysis.json`.

Первый lifecycle на этом каталоге прошёл SHA-256/CRC, RBAC и delete denial,
но manifest был `partial=true`, `gaps=[frontend_loss]`: frontend status
содержал 5 `invalid`, накопленных при первой попытке запуска до исправления
прав. Повторный lifecycle счётчик не увеличил. После контролируемого restart
только frontend новый `boot_id` имел `invalid=0`; следующий полный
start→stop→preview→download→CRC→delete smoke дал `partial=false`, `gaps=[]`,
оба process counters `known`, reader denial и повторный download 410.
Артефакт: `lifecycle-1doc-v7-clean.json`. Simulation auth не проверяет CSRF;
её отдельно покрывают production-mode backend tests.

Offline drill на восстановленном 100-документном корпусе: backend, frontend и
PostgreSQL были остановлены (`postgres` status `exited`); штатный
`scripts/production/collect-diagnostics.sh` с v7 compose override создал ZIP
из safe spool. У копии shell-скрипта на CT были CRLF после переноса из Windows;
для исполнения нормализованы переводы строк только в тестовой копии.
`python3 -m zipfile -t` завершился успешно. Manifest: `collection_mode=offline`,
`partial=true`, `audit_reconciliation=pending_sql`, gaps
`frontend_unavailable`/`metadata_unavailable`, process loss counters `unknown`.
Это ожидаемые ограничения offline без БД и frontend, а не ложная полнота.
Артефакт остаётся только на CT:
`/opt/okf-diag-levels-20260929/offline-output/offline-db-down-v7.zip`.
После drill `docker compose up -d --wait` вернул healthy для backend, frontend,
PostgreSQL и Qdrant.

На CT 102 отдельно прошли Linux fixture tests штатных production wrappers:
`test_collect_diagnostics.sh` (offline отказ при работающем backend, dry-run,
ограниченный container state и incomplete gap) и `test_backup_scripts.sh`
(dry-run bundled backup/restore, отказ при существующей целевой папке и
обязательное подтверждение replace). Тестовые копии shell-файлов были
нормализованы с CRLF и получили исполняемый бит после переноса из Windows;
репозиторные исходники не менялись.

Browser smoke через localhost-only SSH/PVE relay на frontend v4 подтвердил
русскую панель диагностики при ширине окна около 631 px: состояние, квота,
уровень/область/время, кнопки и события без горизонтального обрезания
контролов. В первой вкладке включён `detailed` на 5 минут; UI показал
выборку и агрегаты, во второй — ту же активную сессию. Обнаружено расхождение:
во второй вкладке сводка верно показывала `Подробная`, но disabled select и
пояснение оставались на локальном default `Стандартная`. Сессию остановили
через UI. `DiagnosticsPanel.jsx` теперь выводит disabled поля из active session
(level, scope, duration, document ID), сохраняя несохранённый выбор формы
для следующего запуска. После правки полный Node suite: 265 passed, 1 skipped;
ESLint exit 0, 40 warnings; Next production build exit 0. Новый frontend v5
собран и проверен на отсутствие `.env`/data/spool; перенос в CT и повторный
browser smoke ожидают отдельного разрешения для этого образа.
Frontend v5 Docker image ID:
`sha256:8f296496c093b3595be280fa990728f135a9df0e8f75419f5b06cdf5c25a2c7e`;
SHA-256 локального transfer tar:
`3225e5a0aec00ca92a162fe06005b730b7f8b250a9692fc9197e2f88923a9e4a`.
Локальный Next dev из текущего worktree был подключён через localhost-туннель
к CT 102 для проверки исправления до переноса образа. Прямой CT frontend
создал `detailed` сессию на 5 минут; новая локальная вкладка получила статус
сервера и одновременно показала `Подробная` в сводке, disabled select и
пояснении, `5` в disabled minutes. Сессию остановили через прямой CT UI,
локальный dev server выключен. Попытка создать сессию через двойной
Next proxy (локальный dev → CT frontend → backend) дала HTTP 502; это
ограничение временного тестового маршрута, для самой проверки синхронизации
использован прямой CT frontend. Production image v5 ещё не запускался в Linux.

Проверен реальный post-200 stream failure через тот же localhost UI:
синтетический вопрос к 100-документному корпусу вызвал
`POST /api/chat/stream`, backend access log показал HTTP 200. При
недоступном тестовом LLM в потоке пришла ошибка; чат показал локализованное
«Необходимый сервис временно недоступен» и UUID кода обращения, без сырого
сообщения провайдера. Это подтверждает пользовательский путь обработки
stream error после 200 на frontend v4/backend v7. Дальнейшая проверка нового
frontend v5 остаётся отдельной после разрешения переноса.

Английская локаль на той же живой панели проверена во второй вкладке:
`Error diagnostics`, `Temporary capture`, `Level`, `Scope`, `Minutes`,
`Developer support bundles`, stop reason и текст агрегатов переведены.
Переключатель языка меняет панель без перезагрузки. Заголовок активного
контура «Основной контур» остался на русском в английской шапке; это
существующее имя контура из тестовых данных, вне диагностической панели.
При отдельном viewport 390×844 px шапка переносится, а блоки status,
temporary capture, events и bundles остаются доступны вертикальной прокруткой:
селекторы и кнопки занимают ширину карточки без горизонтального обрезания.
После проверки временный viewport reset.

Code review после этих проверок выявил три дефекта и дал адресные RED-тесты:
штатный Node shutdown отзывал control до записи уже принятых capture-событий;
backend sweep прерывался на просроченном capture с активным read lease;
backend recorder удерживал ссылки на все завершённые aggregate-сессии и
неограниченную историю закрытых ID. Node теперь сохраняет control до flush;
sweep удаляет каталог и expiry marker только после освобождения последнего
сегмента, не прерывая очистку остальных потоков; aggregate views освобождаются
после flush, история закрытых ID ограничена 1024. Адресные проверки: Node
shutdown GREEN; read-view/recorder 21 passed; bounded-state 2 passed. После
правок полный Node suite: 266 passed, 1 skipped; diagnostics backend suite:
243 passed, 1 skipped, 2406 deselected. Ruff `--no-cache` и ESLint завершились
без ошибок (40 предупреждений ESLint); `git diff --check` без ошибок.

Backend v8 собран после исправлений review, проверен на отсутствие
runtime `.env`, data и diagnostics и по разрешению пользователя перенесён в
CT 102. SHA-256 transfer tar:
`e47c3b5284dfd428a437ae9075c4274649fe604aa14f3ec90593e0e59340e1e4`;
image ID `sha256:e7ace3d1e7e29ba8461bba404c7ac6a67370d594c7ef5a5bd24fb7137f9ad480`.
Хеш совпал локально, на PVE и в CT; bundled stack пересоздал backend и
migrator, все сервисы healthy. Frontend v6 также собран с production Next
build и проверен на отсутствие `.env`/data/diagnostics; image ID
`sha256:bf97d1b41e80ce0a6a2d8fa543d5e6cce97defb0d1fb4ba33850d980d38e7ba7`.
SHA-256 подготовленного локально frontend v6 transfer tar:
`041310ab20d5105a7752dd1043ebaefff107d7b82be8030583f7f9a20c1b56d1`.
Его перенос ожидает отдельного ответа: прежний запрос на v5 отозван как
устаревший. Первый Docker build frontend v6 завершился `npm EIDLETIMEOUT`,
после проверки доступности registry повтор с host network прошёл.

PostgreSQL forward migration проверена в отдельной БД
`okf_diag_migration` на CT: на старой revision `030b1c2d3e4f` добавлена
синтетическая legacy session, затем `alembic upgrade head` довёл схему до
`040b1c2d3e4f`. Старый ряд сохранил `capture_level=detailed`,
`policy_version=0`, `policy_snapshot={}`. Первые две попытки команды миграции
не выполнились из-за CRLF stdin Windows; SQL до успешного прогона не менялся.

Отдельный Compose project `okf-diag-levels-external` проверил режим
`STORAGE_MODE=external` с собственной синтетической БД
`okf_diag_external` и Qdrant collection, подключёнными к уже работающим
тестовым storage-контейнерам через external network. `compose config --services`
содержал только migrate/backend/frontend; migrator завершился, backend и
frontend healthy, loopback frontend `/health` вернул 200, Alembic revision
во внешней БД `040b1c2d3e4f`. После smoke этот второй Compose project
остановлен без удаления синтетических данных; основной bundled stack остался
работать.

На backend v8 повторён полный admin lifecycle через bundled frontend v4:
start→stop→preview→download→CRC→delete, reader denial и повторный GET 410.
Результат `lifecycle-100doc-v8.json`: `partial=false`, `gaps=[]`, process
counters известны; ZIP 2462 байта, одно aggregate-событие для 20 успешных
запросов. Это Linux smoke backend v8 до дополнительной правки missing ZIP; frontend v6 ещё ждёт
отдельного разрешения на перенос.

Crash/restart drill: тестовый backend дважды остановлен `SIGKILL` (exit 137),
после чего Compose восстановил все healthy без ручной очистки writer lock.
Во второй попытке файл `backend/control/counters.json` прочитан пока процесс
был убит: он сохранил предыдущий `boot_id`, UTC checkpoint и численные
счётчики (`written=5`, потери 0). После старта появился новый `boot_id`,
frontend `/health` снова 200. Этот опыт доказывает сохранение последнего
checkpoint, но не нулевую потерю событий после него; manifest обязан
отмечать историю прежнего процесса как unknown.

Restore metadata без spool проверен в отдельной БД external-проекта: в SQL
помещён синтетический active session, затем проект поднят с новым пустым
каталогом, подготовленным штатным `prepare_diagnostics_dirs.py`. Recovery
перевёл строку в `stopped`, освободил `active_slot`, выставил
`server_restarted` и создал одну запись `diagnostic_session_stopped` в audit.
Проект после проверки снова остановлен. Недоступность ранее готового ZIP без
файла отдельно в этом CT опыте не проверялась. Дополнительный RED-тест на
локальном HTTP-пути обнаружил, что ready SQL-строка без ZIP возвращала 503:
`FileNotFoundError` при `lstat()` попадал в общий audit-unavailable handler.
`acquire_download` теперь переводит именно отсутствующий файл в
`diagnostic_bundle_gone`/HTTP 410; прочие I/O и audit ошибки остаются 503.
Адресный тест стал GREEN, связанные API/bundle тесты прошли.

После этого backend v9 собран с неизменённым dependency-layer v8, проверен
на отсутствие runtime `.env`/data/spool и перенесён в CT по ранее данному
разрешению. SHA-256 transfer tar:
`f4ad3d0e9ccb79ff8e0fcb607dbfecb0cc1e9eb7e5c17cecadfd50d3342f7fe7`
(одинаков локально, на PVE и в CT); image ID
`sha256:65a34cd5df6b1122cab56b44c00819e67d739b0a2edd1012b772d91485e7bba3`.
Bundled stack пересоздан с v9 и стал healthy. Связанный API/bundle suite:
33 passed, Ruff без ошибок. Через CT frontend v4 в UI создан синтетический
ready bundle, его ZIP 13 853 байта временно переименован без удаления; новый
авторизованный GET через frontend вернул HTTP 410. ZIP восстановлен, bundle
удалён через UI, подтверждены статус `Deleted` и отсутствие файла.
External-topology smoke повторён с тем же backend v9 image ID: migrator
завершился, backend/frontend healthy, loopback `/health` вернул 200; второй
Compose project после проверки остановлен.

Дополнительный v9 lifecycle после серии restart drills завершил API/CRC/delete,
но manifest был `partial=true`, `gaps=[frontend_loss]`: старый frontend v4
сохранял накопленное с 15:30 состояние `invalid=41`, `dropped=0`,
`expired_queue=0`. Контролируемый restart только frontend дал новый boot с
`invalid=0` и `/health` 200. Немедленный повтор smoke упёрся в штатный лимит
трёх bundle-операций за час для `demo.admin` (HTTP 429, `Retry-After: 60`);
он не записан как прошедший clean lifecycle. Предыдущий clean lifecycle v8 и
адресный v9 missing-file/normal-bundle путь приведены отдельно.

Причина `invalid=41` установлена в `diagnosticServer.refreshControl`: после
истечения 10-секундной аренды корректный control-файл повторно разбирался
каждую секунду и учитывался как несовместимый. Локальный RED-тест воспроизвёл
счётчик; исправление отличает валидную, но истёкшую проекцию и отсутствие файла
от повреждённой схемы. `_active` теперь проверяет и настенные часы аренды, и
монотонный предел. Два адресных теста прошли; общий frontend suite прошёл
269 тестов (268 passed, 1 skipped), линтер изменённых файлов без ошибок.
Linux image frontend v4 не содержит этого исправления, поэтому его v9
`frontend_loss` нельзя интерпретировать как фактическую потерю событий.

После явного разрешения пользователя три обновлённых synthetic probe-скрипта
перенесены через PVE в CT 102; SHA-256 всех трёх файлов совпал с локальным.
Полная backend-регрессия после последних исправлений: `2629 passed, 24 skipped,
21 warnings`, exit 0, 2326,85 с. Ruff `--no-cache`: `All checks passed!`.
Frontend после исправления: `268 passed, 1 skipped`; `eslint src`: 0 errors,
40 ранее существовавших warnings; локальный Next production build успешен.
Из актуального кода собран frontend v7, внутри image отсутствуют runtime
`.env`, БД и diagnostic spool. С согласия пользователя tar SHA-256
`a26a5a9f68eb6696168575a2d0580b4537fd1315eebebfd93cbcbbe40fec7e86`
перенесён в CT 102 (локальный/PVE/CT хеши совпали), image ID
`sha256:e32ec44abea8e97f885f0eb0689b70cb7fef38b31f14798789557e5696ff086a`.
Bundled Compose переключил только frontend на v7, `GET /health` вернул 200.
Живая английская панель через localhost показала `Detailed` во всех disabled
полях активной 5-минутной сессии. Для проверки истёкшей аренды backend
приостановлен на 13 секунд; frontend `status.json` сохранил тот же boot,
`invalid=0`, `dropped=0`, `expired_queue=0`. Backend возобновлён, сессия
остановлена через UI; состояние вернулось к `Capture stopped`.

На этой же паре образов завершена финальная normal HTTP-матрица для 100
синтетических документов: 3 повтора, concurrency 1/8, каждый режим после 30 с
прогрева содержал минимум 1000 запросов и 60 с измерения. Полные числовые
артефакты: `tests/artifacts/diagnostics/http-matrix-100doc-v9-v7.json` и
`http-matrix-100doc-v9-v7-analysis.json`. Все 12 решений анализатора прошли
неизменённые пороги p95 (+10%) и RSS (+128 МиБ backend, +32 МиБ frontend),
длительность/число запросов и нулевые потери обоих процессов. Максимальная
относительная прибавка p95: `standard` +7,67%, `detailed` +2,93%; пик прироста
RSS соответственно backend +0,02/+0,46 МиБ, frontend +10,1/+2,2 МиБ.
Это normal path без одновременной сборки ZIP; её серии идут отдельно.

100-doc ZIP-overlap матрица на тех же image ID также завершена: 18 пакетов
(`baseline+ZIP`, `standard+ZIP`, `detailed+ZIP` × 3 повтора × concurrency 1/8)
и отдельный baseline без ZIP для каждой пары. Все решения прошли p95-порог
+20%, RSS, длительность, отсутствие потерь и фактический overlap: минимум
81 HTTP-запрос пересёк наблюдаемое состояние `building` в каждом пакете;
максимальная длительность сборки 14,24 с, размер пакета до 108 415 байт.
Максимальная прибавка p95: `baseline+ZIP` +10,89%, `standard+ZIP` +13,82%,
`detailed+ZIP` +7,45%. Пиковый прирост RSS backend не выше 1,51 МиБ, Node
не выше 18,64 МиБ. Артефакты:
`tests/artifacts/diagnostics/http-matrix-100doc-bundle-v9-v7.json` и
`http-matrix-100doc-bundle-v9-v7-analysis.json`.

100-doc ZIP-after-stop матрица также завершена на тех же образах: 12 пакетов
(`standard`/`detailed` × 3 повтора × concurrency 1/8), у всех
`stopped_before_bundle=true`, статус `ready` и минимум 80 запросов во время
наблюдаемого `building`. Все 12 решений анализатора прошли p95 +20%, RSS,
длительность, потери и overlap. Максимальная прибавка p95 `standard` +6,06%,
`detailed` +15,36%; пик прироста RSS backend +0,14 МиБ, Node +8,43 МиБ,
сборка до 6,07 с. Артефакты:
`tests/artifacts/diagnostics/http-matrix-100doc-stopped-v9-v7.json` и
`http-matrix-100doc-stopped-v9-v7-analysis.json`.

После переключения изолированного Compose project на отдельные one-doc
PostgreSQL/Qdrant volumes, data и spool проверено ровно 1 синтетический
документ в SQL и `/health` 200. На backend v9/frontend v7 завершена normal
1-doc HTTP-матрица: 3 повтора, concurrency 1/8, 12 сравнений. Все решения
прошли p95 +10%, RSS, длительность и отсутствие потерь. Максимальная прибавка
p95 `standard` +4,91%, `detailed` +4,52%; пик прироста RSS backend
+0,18 МиБ, Node +11,67 МиБ. Артефакты:
`tests/artifacts/diagnostics/http-matrix-1doc-v9-v7.json` и
`http-matrix-1doc-v9-v7-analysis.json`.

Первая 1-doc ZIP-overlap матрица на backend v9/frontend v7 завершила все
18 пакетов: p95, длительность, отсутствие потерь и реальный overlap прошли,
но RSS-критерий не прошёл. Максимальный прирост Node: `baseline+ZIP`
+68,12 МиБ, `standard+ZIP` +39,94 МиБ, `detailed+ZIP` +32,97 МиБ
при пределе +32 МиБ; backend не выше +0,53 МиБ. Исходные строки и решения:
`tests/artifacts/diagnostics/http-matrix-1doc-bundle-v9-v7.json` и
`http-matrix-1doc-bundle-v9-v7-analysis.json`. По правилу раздела 8 плана
запущен повтор всего блока с теми же образами и порогами; до него сценарий
не считается принятым.

Повтор 1-doc ZIP-overlap завершён всеми 18 пакетами без смены образов,
конфигурации и порогов. Все `standard+ZIP` и `detailed+ZIP` строки прошли
p95 +20% и RSS +32 МиБ Node/+128 МиБ backend: максимумы p95 +15,19% и
+16,83%, Node RSS +1,16 и +25,00 МиБ. Общий флаг анализатора `all_p95_pass`
остался `false`: `baseline+ZIP` без capture при concurrency 1 вышел на
+20,21% и +28,83% во втором и третьем повторах. ZIP-контроль более широк:
в первом блоке baseline ZIP содержал до 117 054 байт против примерно
41 000 байт standard и 22 000 байт detailed. Это не снимает нарушения
общего контрольного порога и не доказывает равный входной объём. Остальные
общие флаги повторного блока (`rss`, duration/requests, losses, overlap) —
`true`. Артефакты: `http-matrix-1doc-bundle-repeat-v9-v7.json` и
`http-matrix-1doc-bundle-repeat-v9-v7-analysis.json` в
`tests/artifacts/diagnostics/`.

Первая 1-doc ZIP-after-stop матрица завершила 12 пакетов на тех же образах.
Все строки имели `stopped_before_bundle=true`, статус `ready` и наблюдаемый
overlap; p95, длительность и счётчики потерь прошли. Максимальный p95 прирост
`standard` +13,26%, `detailed` +12,70%. Один `standard` при concurrency 8
превысил Node RSS-порог: +65,41 МиБ при лимите +32 МиБ. По разделу 8 плана
запущен полный повтор без смены конфигурации. Исходный результат сохранён в
`http-matrix-1doc-stopped-v9-v7.json` и
`http-matrix-1doc-stopped-v9-v7-analysis.json`.

Повтор 1-doc ZIP-after-stop завершил все 12 пакетов с
`stopped_before_bundle=true`; минимум 291 запрос пересёк наблюдаемую фазу
`building` в каждом пакете. Все решения анализатора прошли p95 +20%,
RSS, длительность и отсутствие потерь. Максимальный прирост p95:
`standard` +16,05%, `detailed` +13,87%; backend RSS +0,07/+0,05 МиБ,
Node RSS +9,02/+2,14 МиБ соответственно. Артефакты:
`http-matrix-1doc-stopped-repeat-v9-v7.json` и
`http-matrix-1doc-stopped-repeat-v9-v7-analysis.json` в
`tests/artifacts/diagnostics/`.

Финальный bundled lifecycle на backend v9/frontend v7 и одном синтетическом
документе прошёл через Next: admin start → 20 `/api/settings` → stop →
bundle → preview → download → SHA-256 и CRC всех семи ZIP-компонентов →
delete → новый GET 410. Reader получил 403 на status/download. Manifest v2:
`partial=false`, `gaps=[]`, оба process counter states `known`, покрытие
сессии `aggregated`; результат
`tests/artifacts/diagnostics/lifecycle-v9-v7-1doc-result.json`.
Simulation auth отключает CSRF middleware, поэтому CSRF подтверждён
отдельными backend auth tests, а не этим smoke.

После полного 10-мс TestClient блока обнаружено ограничение ранних HTTP
RSS-решений: в их JSON `backend_child=0` во всех измеренных строках, хотя
TestClient v9 стабильно измерил ZIP-child около 262 МиБ. Следовательно,
указанные выше решения анализатора о backend RSS не подтверждают критерий
parent+child; сами p95/Node/loss/overlap артефакты сохраняют значение.
Причина RSS локализована: `bundle_worker` импортировал тяжёлый `snapshot`
ради двух чистых валидаторов, тем самым загружая SQLAlchemy/DB stack.
Валидаторы вынесены в `safe_metadata.py`, RED→GREEN import-isolation test,
45 профильных тестов и Ruff прошли. Локальный Docker импортный RSS v9→v10:
267 264→25 672 КиБ, на CT 102 v10 — 25 516 КиБ. Синтетический
TestClient+ZIP smoke v10 дал child peak 24,68 МиБ, консервативный прирост
parent+child к baseline 26,59 МиБ, ZIP завершился за 0,47 с;
`tests/artifacts/diagnostics/stub-search/smoke-v10.json`. Полные Linux
матрицы на backend v10 ещё нужны для итогового p95/RSS утверждения.
Пользователь сообщил, что на том же Proxmox иногда работает Ollama;
без синхронных метрик хоста влияние на отдельные p95/Node RSS всплески
не установлено.

После исправления RSS-пробы полная 1-doc normal HTTP-матрица на backend
v10/frontend v7 сохранила `all_p95_pass=true`, `all_duration_requests_pass=true`
и `all_losses_pass=true`, но Node RSS превысил +32 МиБ в двух из 12
capture-строк: repeat 2/c8/standard +68,18 МиБ и repeat 3/c8/detailed
+55,01 МиБ. Исходный JSON и решение — `http-matrix-1doc-v10-v7.json` и
`http-matrix-1doc-v10-v7-analysis.json`. Первый запуск полного повтора был
прерван: параллельно по ошибке начат локальный pytest, что нарушало условие
плана об изоляции perf. Процессы pytest и этого неполного повтора остановлены,
его результаты не использованы. Чистый повтор без параллельных тестов и сборок
завершил все 18 пакетов; `all_p95_pass`, `all_rss_pass`, duration/requests и
losses — `true`. Максимальный прирост p95: standard +3,93%, detailed +3,71%;
Node RSS: +22,39/+10,47 МиБ; backend cgroup RSS: +0,05/+0,28 МиБ. Образы
backend `sha256:4160a419...`, frontend `sha256:e32ec44...`.
Артефакты — `tests/artifacts/diagnostics/http-matrix-1doc-clean-v10-v7.json`
и `http-matrix-1doc-clean-v10-v7-analysis.json`. CT 101 с моделью был
запущен, но два моментных опроса во время матрицы показали CPU=0 и CPU
pressure=0; это не устанавливает причину прежних кратких Node RSS-пиков.

На той же паре образов полная 1-doc ZIP-overlap матрица (три повтора,
concurrency 1/8, 18 ZIP-строк плюс шесть reference) прошла все флаги
анализатора: p95, RSS, duration/requests, losses и наблюдаемый `building`.
Максимальный прирост p95 относительно своего reference: baseline+ZIP +7,16%,
standard+ZIP +5,64%, detailed+ZIP +6,91%; Node RSS +8,79/+4,98/+19,88 МиБ,
backend cgroup RSS +25,26/+22,79/+25,25 МиБ соответственно. Каждый ZIP
готов; минимум 79 запросов пересекли `building` в строке. Размеры архивов
неодинаковы: baseline 63 962–118 549 байт, standard 40 373–42 144,
detailed 22 533–23 999. Эти данные доказывают пользовательский эффект при
реальном объёме каждого режима, а не скорость сборки одинакового входа.
Артефакты: `tests/artifacts/diagnostics/http-matrix-1doc-bundle-v10-v7.json`
и `http-matrix-1doc-bundle-v10-v7-analysis.json`.

Первая 1-doc ZIP-after-stop матрица v10/v7 завершила 12 ZIP-строк:
`stopped_before_bundle=true`, все ZIP ready, overlap и p95 прошли. Однако
repeat 2/c1/detailed дал frontend `expired_queue=1`, а
repeat 3/c1/detailed показал backend cgroup sum-RSS +426,88 МиБ. Полный
повтор на тех же образах и порогах не повторил loss, но дал backend sum-RSS
+424,23 МиБ в repeat 1/c8/standard. Поэтому RSS-критерий **устойчиво не
выполнен** по текущей методике; единичная потеря остаётся расследуемой, а не
скрыта зелёным повтором. Оба блока сохранены:
`tests/artifacts/diagnostics/http-matrix-1doc-stopped-v10-v7.json`,
`http-matrix-1doc-stopped-v10-v7-analysis.json`,
`http-matrix-1doc-stopped-repeat-v10-v7.json` и
`http-matrix-1doc-stopped-repeat-v10-v7-analysis.json`. Основной backend
Python-процесс держал примерно 462 МиБ RSS; краткий child peak 476 МиБ
похож на fork до exec, но физическое потребление cgroup и причина требуют
отдельной проверки. Порог или образ не менялись между сериями.

Проверка после этих серий: Linux cgroup `memory.peak` backend-контейнера
составил 496 168 960 байт, `memory.current` 466 812 928 байт; при этом
сумма двух process VmRSS кратковременно достигала примерно 953 МиБ.
CPython в образе идёт через `fork_exec` при прежних `cwd` и
`close_fds=True`; краткий child VmRSS близок к parent RSS, что согласуется
с двойным учётом общих страниц до exec. Код Linux worker изменён на
`posix_spawn` через `Popen` без `cwd` и с `close_fds=False` (Python-owned
дескрипторы по умолчанию CLOEXEC); путь модуля передаётся в `PYTHONPATH`.
Windows-ветка сохраняет прежний `cwd`/creationflags. Адресный тест проверяет
фактический вызов `Popen._posix_spawn`, успешный ZIP и shutdown. Отдельно
воспроизведён RED-сценарий принятого frontend-события, оставшегося в очереди
при ручном stop; запись до исходного monotonic lease и TTL исправлена, а
существующий тест hard-expiry остаётся зелёным. Node 270 passed/1 skipped,
ESLint 0 errors/40 прежних warnings, Next build, 41 backend ZIP/queue/prepare
tests и Ruff прошли. Образы backend v11/frontend v8 загружены в тестовый
CT 102, health=200. Короткий изолированный v11 TestClient+ZIP smoke:
child peak 24,68 МиБ, консервативный parent+child прирост 27,45 МиБ,
166 запросов пересекли build, ZIP 0,51 с; артефакт
`tests/artifacts/diagnostics/stub-search/smoke-v11.json`. Полные матрицы на
этой новой паре ещё требуются.

После smoke полная 1-doc normal v11/v8 матрица прошла все критерии:
p95 максимум standard +4,13%, detailed +4,67%; Node RSS +4,17/+17,05 МиБ,
backend cgroup sum-RSS +0,08/+0,07 МиБ; duration/requests и losses зелёные.
Артефакты: `tests/artifacts/diagnostics/http-matrix-1doc-v11-v8.json` и
`http-matrix-1doc-v11-v8-analysis.json`. Критическая ZIP-after-stop матрица
на тех же image IDs завершила 12 ZIP-строк: все `stopped_before_bundle=true`,
все ready, минимум 102 запроса пересекли `building`, все флаги анализатора
зелёные. Максимальный прирост p95 standard +4,35%, detailed +4,24%; Node
RSS +11,17/+14,98 МиБ; backend parent+child sum-RSS +24,00/+23,68 МиБ.
Максимальный абсолютный child RSS (включая другие короткие процессы cgroup)
51,26 МиБ. Потерь frontend и backend нет. Исходный JSON и анализ:
`tests/artifacts/diagnostics/http-matrix-1doc-stopped-v11-v8.json` и
`http-matrix-1doc-stopped-v11-v8-analysis.json`. Это подтверждает устранение
устойчивого RSS-превышения v10 на полном Linux-блоке без смены порога.

Полная 1-doc ZIP-overlap матрица v11/v8 затем прошла p95, RSS,
duration/requests и наблюдаемый overlap, но **не прошла losses**: пять из
18 ZIP-строк имели ровно один frontend `expired_queue`; остальные счётчики
потерь нулевые. Максимальный прирост p95 baseline+ZIP +12,19%, standard+ZIP
+7,39%, detailed+ZIP +8,80%; backend sum-RSS +24,15/+23,63/+23,88 МиБ,
Node +6,52/+0,68/+11,60 МиБ соответственно. Артефакты:
`tests/artifacts/diagnostics/http-matrix-1doc-bundle-v11-v8.json` и
`http-matrix-1doc-bundle-v11-v8-analysis.json`. По строкам loss совпадал с
одним недостающим frontend `success_aggregate`; повторяющиеся агрегаты
пишутся асинхронно, а backend продлевает ту же 10-секундную lease раз в
5 секунд. RED-тест воспроизвёл сброс уже принятого события после продления
той же сессии: прежняя проверка учитывала только исходную lease на момент
приёма. Frontend допускает запись принятого события при действующей
продлённой lease той же сессии, сохраняя запрет после настоящего истечения,
отзыва или TTL. Адресные тесты queued event и `success_aggregate`, полный
Node набор (272 passed, 1 skipped), ESLint (0 errors, 40 прежних warnings)
и Next build прошли. Frontend v9 (`sha256:97b1530a...`) загружен в CT 102;
backend остаётся v11, `/health` через frontend вернул 200. Повтор полной
матрицы на v11/v9 начат; v11/v8 loss не объявляется принятой.

Полная 1-doc normal матрица на backend v11/frontend v9 завершена: три
повторения при concurrency 1 и 8, все p95/RSS/duration/requests/losses
прошли. Максимальная относительная/абсолютная прибавка p95: standard
+5,55%/+5,06 мс, detailed +5,81%/+4,95 мс. Максимальный прирост RSS
backend: +0,03/+0,08 МиБ; Node: +13,66/+17,39 МиБ. Все observed frontend
и backend loss counters равны нулю. Артефакты:
`tests/artifacts/diagnostics/http-matrix-1doc-v11-v9.json` и
`http-matrix-1doc-v11-v9-analysis.json`. ZIP-overlap матрица на этой же паре
завершена, её результат описан ниже.

Во время v11/v9 ZIP-матрицы CT 101 (локальная модель на том же PVE) был
запущен, но cgroup `cpu.stat usage_usec` за примерно четыре минуты вырос
только на 8 816 мкс (11 711 943 793 → 11 711 952 609). В этих двух
снимках CT 101 практически не выполнял вычислений; это не доказывает его
неактивность в иных сериях. У CT 102 за тот же интервал счётчик вырос на
188 282 009 мкс. Поэтому возможные обращения к Ollama считаются внешним
фактором, но не объясняют текущий замер без синхронного следа.

Первая полная 1-doc ZIP-overlap матрица на v11/v9: три повторения c1/c8,
18 ZIP-пакетов `ready`, в каждом наблюдалось пересечение с запросами
(минимум 107). Все p95, duration/requests и losses прошли; frontend
`expired_queue=0` во всех 18 строках, то есть потеря v11/v8 не повторилась.
Максимальные p95 overhead baseline/standard/detailed: +12,79/+7,71/+8,74%;
backend parent+child RSS +23,72/+23,84/+23,62 МиБ. Один Node RSS результат
превысил неизменённый лимит +32 МиБ: repeat 1/c8/standard +45,29 МиБ;
остальные 17 строк RSS прошли. Общий `all_rss_pass=false` сохранён:
`tests/artifacts/diagnostics/http-matrix-1doc-bundle-v11-v9.json` и
`http-matrix-1doc-bundle-v11-v9-analysis.json`. По правилу плана запущен
повтор полного блока без смены образов, конфигурации или порогов.

Полный повтор v11/v9 сохранил те же image IDs и завершил 18 ZIP-пакетов.
Для всех 12 пользовательских capture-строк (`standard+ZIP` и
`detailed+ZIP`) p95, RSS, duration/requests, losses и overlap прошли;
максимальный прирост p95 +10,65/+8,04%, Node RSS +23,35/+22,38 МиБ,
backend parent+child +23,77/+0,34 МиБ. Прежняя строка r1/c8/standard
имела Node 184,71→208,06 МиБ (+23,35 МиБ). Потери нулевые у всех 18 ZIP.
Однако общий `all_rss_pass=false` остаётся: два контрольных `baseline+ZIP`
при c8 дали +35,74 и +47,82 МиБ Node против лимита +32 МиБ, когда их
архивы имели 66 869 и 118 275 байт (standard 41–42 КиБ, detailed около
23 КиБ). Это отдельное ограничение baseline-контроля, не зелёный общий
результат; пороги и методика не менялись. Исходный повтор и анализ:
`tests/artifacts/diagnostics/http-matrix-1doc-bundle-repeat-v11-v9.json`,
`http-matrix-1doc-bundle-repeat-v11-v9-analysis.json`.

Code rollback drill в bundled CT: Compose override переключён с backend v8
на сохранённый v7 (`sha256:14d913...`); migrator завершился на текущей схеме,
backend и frontend стали healthy, frontend `/health` вернул 200. Затем
override возвращён на v8 (`sha256:e7ace3...`) и весь стек снова healthy.
SQL downgrade не выполнялся; это проверка обратимого выбора образа при
совместимой forward-схеме, а не восстановление production backup.

Существующий `.github/workflows/ci.yml` запускает общий backend `pytest`
и frontend `npm test`, поэтому новые тесты в стандартных каталогах попадают
в эти jobs; Linux job уже вызывает `test_backup_scripts.sh` и
`test_collect_diagnostics.sh`. Фактический CI run без отправки ветки не
утверждается.

## Открытые проверки

- Актуальный список `covered / required / deferred` находится в итоговой
  таблице в начале отчёта. ZIP RSS, final stopped/100-doc/stub серии,
  фиксированный ZIP input, phase distributions, live aggregate/response
  equality и final CT accelerated TTL остаются required.
- Финальный lifecycle v12/v9, минутный error storm и реальные prepare/ZIP
  kill/shutdown выполнены в ограниченной финализации. Старый пункт о SIGKILL
  без active build этим новым доказательством заменён, а не выдан за него.

До фактического выполнения этих пунктов p95/RSS и полный Definition of Done
не объявляются пройденными.
