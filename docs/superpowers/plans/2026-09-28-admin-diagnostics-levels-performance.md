# Уровни детализации и производительность административной диагностики — план реализации

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


## Текущий шаг 01.10, 12:34 МСК

WAL A/B/A:3 rows/10 562 requests,p95 18.853→18.812→18.571 ms,fsync p95 2.350 ms each. Spike not reproduced; no per-request timeline from this control (optional serialization compatibility issue separately fixed/verified113 requests). RAG unchanged. One full affected repeat RUNNING per section8; no third automatic retry. Next native queue prepared only;100-doc/tail remain required/open.

## Текущий шаг 01.10, 12:17 МСК

Full corrected stopped ZIP:18/18,79 357 requests,RSS pass (backend24.777/frontend13.770 MiB max),7/8 gates true. One standard/c1/r3 p95+47.940% failed; old failures retained. IO PSI5.145% in failed minute vs2.8–2.9% nearby, cause unresolved. Bounded WAL-sync A/B/A localization RUNNING, not acceptance; no app/RAG/PG config changes.100-doc/tail required/open, no further full repeat before evidence review.

## Текущий шаг 01.10, 11:35 МСК

Corrected RSS bounded control:3 rows/15 760 requests,8 gates true. Native independent observer:172 real shared-VM samples, passed. Full affected stopped ZIP repeat RUNNING (18 rows), frozen v16/v11, original numeric gates. Earlier failed results preserved;100-doc/tail remain required/open.

## Текущее исполнение 01.10: RSS measurement correction

Full native v16 stopped block:18/18,80 236 requests,7/8 gates passed; RSS failed. Backend shared VM double-count confirmed by KCMP_VM; frontend +37,824 MiB unresolved. Ruling: unique-address-space RSS plus preserved raw sum, unchanged128/32 MiB numeric gates; unknown comparison retains conservative sum.26 targeted tests/Ruff passed. Corrected bounded c8 control RUNNING; full affected repeat after its evidence check.100-doc/tail required/open. Details and immutable failure evidence in report.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Исполнение авторизовано пользователем после составления плана; работа ведётся в отдельной ветке `admin-diagnostics`. Commit, push, merge и production требуют отдельного поручения.

**Goal:** Снизить влияние диагностики на рабочие запросы за счёт управляемого состава событий, пакетной записи и подготовки пакетов без длительной блокировки основного процесса, сохранив пригодность диагностики для расследований.

**Architecture:** Постоянный базовый журнал сохраняется; временные сессии получают уровни `standard` и `detailed`, независимые от области сбора. Решение о записи принимается до дорогостоящего формирования события; успешные вызовы агрегируются, ошибки имеют резерв очереди. Хранилище ведёт точный учёт своих файлов, пишет пакетами и предоставляет ограниченные по длине входные сегменты; подготовка экспортных данных и ZIP выполняются дочерним процессом при квотах, которыми управляет родитель.

**Tech Stack:** Python/FastAPI, SQLAlchemy/Alembic, PostgreSQL/SQLite, stdlib queue/threading/subprocess/zipfile; Next.js/React, Node fs; pytest, Node test runner; существующие Windows dev и Linux Compose bundled/external.

**Spec:** [Исходная спецификация](../specs/2026-09-27-admin-diagnostics-design.md) и дополнение к ней в разделах 2–5 этого плана. Дополнение — предлагаемое проектное решение, которое исполнитель переносит в спецификацию перед изменением поведения. Старые ограничения сохраняются, кроме явно описанного состава успешных событий и версионирования форматов.

**Статус на 01.10.2026:** реализация выполнена, полная приёмка не закрыта.
Ограниченный v16 A/B/A завершён:6 rows/26763 requests, baseline spread
c1/c8 1,820%/0,379%, ZIP p95 growth1,195%/4,090%, losses0. Node RSS
сигнал +33,613 МиБ и ещё больший следующий baseline требуют full check.
Начат полный затронутый18-row stopped ZIP block v16/v11; original gates
сохранены, следующие100-doc/tail только при pass. Детали в отчёте.
Новый PVE stopped ZIP block завершён failed: 18/18 rows /78 029 requests,
p95 standard/c1/r1,r2 false и один RSS false; остальные6 gates true.
Baseline c1 drift48,040%, RAM pressure/OOM/new swap-out0. Целевой дефект
launcher prepare подтверждён RED→GREEN: Linux33/Windows8/CT24 passed;
v16 установлен только в test backend. RSS smoke завершён exit0:
3 570 requests, три ZIP ready/counts exact/losses0; backend growth27,191
МиБ, frontend4,223 МиБ. Короткий c1 smoke не закрывает полный SLA. Продуктовая
заморозка снята только для этого исправления diagnostics. RAG code unchanged;
read-only SQL pilot показал цену shared row locks. Review01.10 уточнил:
причинность HTTP failures не доказана; RAG-изменение не является необходимым
следующим шагом. Рекомендуется один bounded v16 A/B/A с baseline до/после
для обеих concurrency; при стабильной среде — затронутый полный block.
Метод RR snapshot/race tests остаётся отдельным предложением расширения scope.
100-doc/tail не запускаются до разрешения failures. Подробности в отчёте.
Continuation 04:12 МСК: PVE повторяемость baseline прошла, 3 c8 rows /
15 952 requests, p95 spread2,300%, OOM/new swap-out0, images/limits unchanged.
На новом RAM бюджете завершён отдельный полный stopped ZIP 1-doc block,
18 cases/3 repeats/c1,c8, прежние gates, результат failed указан выше. Следующие 100-doc/tail только
после pass; прежние Windows failures не отменяются. Детали в отчёте.
Ограниченная локализация Windows выполнена: 6 строк / 23 088 requests,
без воспроизведения 503; причина unresolved, прежние failures сохранены.
По поручению пользователя исправлен бюджет PVE: CT101 8 ГиБ / swap512 МиБ,
CT102 5 ГиБ / swap0 с мягкой memory.low4 ГиБ; CT100 и VM103 выключены,
их автозапуск выключен. VM103 выключил пользователь, не запускать.
GPU48 ГиБ сохранены. Runtime limits/health/policy syntax проверены без
перезапуска CT101/102; это не закрывает performance acceptance. Подробности,
приватный rollback и безопасные numeric artifacts перечислены в отчёте.
Короткий контроль итогового бюджета: c8, 30 s warmup / 60 s measure,
4 680 requests, p95 107,253 ms, response equality true, exit0; OOM/new
swap-out0, MemAvailable min4,60 GiB. Это RAM/availability smoke,
не drift/full SLA acceptance; IO stalls не исчезли. Следующий шаг перед
оставшимися матрицами — ограниченный контроль повторяемости новой среды.
Windows stopped ZIP 1-doc первый block failed: единственный p95 отказ
standard/c8/r3 +44,588% при 20%; остальные семь gates passed, losses 0,
totals сошлись. Queue остановилась, 100-doc и tail не запускались.
Failed evidence сохранено. Единственный полный повтор по разделу 8 завершён
exit 1: 11/18 строк, HTTP 503 на standard/c8/r2. Safe events локализуют отказ
в вызове Qdrant/BM25 (VectorStoreError); исходный cause не сохранён.
Перезапусков/OOM нет, сейчас runtime available и capture off. Третья попытка
не запущена. Следующий рекомендуемый шаг — ограниченная локализация 503,
а не ещё одна общая матрица. Подробности и критерий остановки — в отчёте.
В failed минуте host CPU samples выше соседних, VM throttling/memory PSI 0;
18/20 slowest вне child phases. Причина не доказана, продукт и thresholds
не меняются. Детали и ограничения — в отчёте.
Live active ZIP 1-doc Windows v15/v11: 24/24 строки, 88 973 requests,
восемь gates passed; max p95 growth 10,251% при 20%, live RSS growth
backend+child 24,563 МиБ / frontend 4,168 МиБ, losses 0, totals сошлись.
Фазовые p95 выше минутных и опубликованы в отчёте; отдельный фазовый SLA
не вводился. Статус stopped ZIP и дальнейшей очереди уточнён выше.
Live normal 1-doc Windows v15/v11: 18/18 строк, 71 520 requests,
восемь gates passed; max p95 growth 5,438% при 10%, live peak RSS
growth backend+child 0,066 МиБ / frontend 2,766 МиБ, losses 0,
capture totals сошлись. Остальные этапы required/open перечислены выше.
Original fixed-input Windows control завершён: 12/12 строк, 45 318 requests,
exit 0, completeness/duration/integrity/equality passed; это reference,
без SLA current продукта. В 21:48:05 UTC начата первая из шести live matrices
на frozen v15/v11. Их gates проверяются перед продолжением очереди.
Current full fixed-input Windows/Docker v15/v11 завершён: 24/24 строки,
3 повтора c1/c8, шесть gates passed, 91 119 measured requests, max p95
growth +11,214% при прежних 20%, private RSS growth max 10,72 МиБ.
Exact issued counts сошлись для всех 12 capture-сессий, losses 0.
37 мин 05 с, exit 0; frozen image IDs до/после те же. CT failed результаты
сохранены. Original full fixed-input завершён; live matrices/storm/
baseline-off/final lifecycle остаются required/open. Продуктовый код заморожен.
Пользователь выбрал попробовать текущую Windows. A/B/A на локальной Docker
Desktop Linux VM с теми же v15/v11 завершён exit 0: 12 серий, 40 008 measured
requests; capture p95 +3,293/+1,078%, ZIP +1,234/+1,924% к более быстрому
bracketing control, drift controls <=2,350%. Exact issued counts сошлись
для всех восьми сессий, losses 0. Windows/Docker resources/transport отличаются
от CT102; подробности в отчёте. Это подтверждает пригодность выбранного
стенда для продолжения контролей, но не закрывает полную исходную матрицу.
Current fixed-input full block выполнен на Windows Docker
с прежними cases/thresholds и дополнительным сохранением timings.
Diagnostic A/B/A 23:10 МСК завершён на frozen v15/v11: 12 серий, 41 138
измеренных requests, 18 мин 42 с. Рост p95 capture к более быстрому
bracketing control +4,245/+5,908%, ZIP +0,461/+1,198%; сами controls
дрейфуют на 38–58%. В 144 пятисекундных окнах window p95 сильно связан
с PVE IO full stall fraction (Pearson 0,963); источник IO latency и причины
прошлых failures не доказаны. Ollama CT100 была stopped до/после. На PVE
только один физический NVMe; выбор другого host/storage либо ограниченной
IO локализации CT102 задан пользователю. Новая общая матрица не запущена,
пороги не изменены, required/open сохраняются. Подробности и artifacts —
в верхнем разделе отчёта.
После завершения 24/24 current private controls и 12 original controls
fixed-input current block дважды не прошёл p95: первая серия standard/c1/r3
+67,295%, единственный полный повтор detailed/c1/r2 +41,618%. Остальные
fixed-input gates проходят. Failed artifacts и оба attempt ledgers сохранены;
очередь остановлена, original fixed-input и live HTTP block не запускались.
Короткий diagnostic HTTP control без ZIP показал высокий baseline p95
29,860 мс и detailed 28,446 мс; причины вариативности ещё не установлены.
Перепроверка 30.09, 22:16 МСК: требование предварительно менять стенд
преждевременно — влияние PVE/Ollama не доказано. Следующий рекомендуемый
шаг — ограниченный (~20 минут) diagnostic A/B/A на текущем CT 102 с
сохранением per-request timings и синхронных CPU/IO данных, отдельно для
capture и ZIP. Это не SLA-приёмка и не новый полный прогон. Пороги
не изменены; performance criterion остаётся open, готовность всей задачи
не объявляется. Подробности и ограничения методики записаны в отчёте.
Финальный review выявил три Important и одно Minor замечание; исправлены
конечный Node batch/coalesced drain, отдельный slow budget/counter,
освобождение document trace с parent request ID и policy interval backend
агрегатов при непрерывной очереди. Каждое воспроизведено RED/GREEN.
После продолжения 30.09 локализован пропуск frontend admission: совпадение
двух пятисекундных расписаний откладывало первое renewal до границы lease 10 с.
Runtime v15 проверяет tick раз в секунду; запись projection остаётся раз в 5 с.
На v14/v11 реальный observer повтор показал 7464/7380, на v15/v11 — 7552/7552.
Новая frozen Linux пара — v15/v11. Три обязательные standard/c8/active ZIP
пары прошли все восемь gates: p95 +4,06/+6,86/+4,32%, потерь и расхождений нет.
Linux accelerated TTL/quota/retry — 3 passed; связанные Linux tests — 41 passed.
Чистая полная Windows regression завершилась exit 0; SHA-256 всех 433 Python
файлов совпали до/после. Remaining controls идут; полная приёмка открыта.
24/24 current private controls v13/v10 passed сохранены как результаты
предыдущего кода. Потери immutable legacy reference наблюдаются и публикуются;
нулевые losses и исходные p95/RSS gates current сборки остаются обязательными.
После ограниченной финализации пользователь явно выбрал продолжить **полную
приёмку исходного плана**; частичное принятие не согласовано. Образы последней
измеренной серии: backend v15 / frontend v11. v12 исправил cleanup/accounting после отказа
удаления, v13 — allowlist route classification после копирования ASGI scope.
Нагрузочные числа предыдущих версий остаются результатами тех версий.
Проверки, критерии и артефакты сведены
в [отчёт исполнения](../reports/2026-09-28-admin-diagnostics-levels-performance.md).

После повторного анализа учтены замечания: HTTP-очередь останавливается при
численном отказе и неполных данных, обычный bundle poll приведён к 3 с,
перекрытие подтверждается actual child phases. Управляемый stale-poll probe
воспроизводит 7328 → 7312 без guard и 7328 → 7328 с guard; историческая
причина остаётся неподтверждённой. Ограниченный standard/c8/active ZIP сценарий
и парный baseline прошли три повтора на зафиксированной сборке v13/v10:
рост p95 6,88–7,03%, все восемь gates passed. Linux TTL/quota/retry — 3 passed.
Далее выполняются оставшиеся обязательные controls; численный отказ останавливает очередь;
повторная общая оптимизация памяти не требуется по имеющимся данным.

| Этап | Фактическое состояние |
|---|---|
| 1: инструменты измерения | Реализованы; ограничения RSS/phase overlap описаны в отчёте |
| 2–9: контракт, policy, очереди, writer, leases, child prepare/ZIP, UI | Реализованы, функциональные проверки выполнены |
| 10: отрицательные сценарии | Проверены адресными наборами; финальный cleanup/kill — Linux, native paths/accounting — Windows |
| 11: нагрузочная приёмка | Normal 1-doc v13/v9 проходит; ограниченный standard/c8/active ZIP v13/v10 проходит три повтора по всем gates. Полная приёмка и остальные обязательные сценарии открыты |
| 12: выпуск | Документация и smoke подготовлены; полная регрессия имеет отдельный фактический статус в отчёте, GitHub CI не запускался |

Исходные подробные checklist ниже сохранены как требования, а не как
утверждение об отсутствии реализации. Актуальные `covered / required /
deferred` и границы готовности указаны в таблице отчёта. Отложенное
performance-продолжение не принято: required-критерии выполняются в текущей
задаче. Пороги
p95/RSS и обязательные проверки молча не отменены.

## 1. Отправная точка и границы исполнения

Исследован основной checkout `C:/Users/alexey/ai-workspace/ai-knowledge`, `main` на `71414f0`, и отдельный worktree `C:/Users/alexey/.codex/worktrees/admin-diagnostics/ai-knowledge`, ветка `codex/admin-diagnostics` на той же базовой ревизии с незакоммиченной реализацией. В обоих checkout есть чужие изменения. Этот план сохранён в основном checkout отдельным файлом; существующие планы и отчёт текущей приёмки не переписаны.

На момент анализа только в worktree диагностики доступны:

- `docs/superpowers/reports/2026-09-27-admin-diagnostics-acceptance.md` — отчёт функциональной и нагрузочной приёмки;
- `docs/superpowers/plans/2026-09-28-admin-diagnostics-performance.md` — отложенный план технической оптимизации без изменения семантики;
- `backend/app/services/diagnostics/`, admin API, UI и профильные тесты.

Новый план расширяет отложенную задачу: уровни детализации меняют состав доказательств, поэтому это не только ускорение прежней реализации. Старые результаты приёмки остаются историческими доказательствами конкретной версии.

Подтверждённый в отчёте CT 102 сценарий: синтетический документ, SQL-чанк, Qdrant-точка, fake embedder, HTTP-поиск через frontend; 30 с прогрева и 200 запросов на режим.

| Режим | p95 | Прирост к режиму без подробного сбора |
|---|---:|---:|
| Без подробного сбора | 19,92 мс | — |
| Capture | 23,46 мс | +17,8% / +3,54 мс |
| Capture + ZIP | 33,35 мс | +67,4% / +13,43 мс |

Эти числа не являются production SLA и не сравнивают приложение с полностью удалённой диагностикой. ZIP уже вынесен в отдельный процесс; фильтрация и копирование snapshot остаются в родителе. Простой перенос sampling перед сериализацией уже проверяли без устойчивого выигрыша; не выдавать повтор этого изменения за доказанное решение.

Перед исполнением заново определить фактическую ревизию и незакоммиченные изменения. Не разрабатывать параллельно с ещё идущей финальной приёмкой в том же worktree. После её завершения использовать доступный изолированный checkout с учтённой реализацией диагностики; новый worktree от старого `main` не содержит незакоммиченный продуктовый код. Не переносить его вслепую и не выполнять commit без поручения.

## Global Constraints

- Постоянный backend-журнал: 7 суток / 50 МиБ; frontend: 7 суток / 20 МиБ.
- Сессия: 15 минут по умолчанию, диапазон 5–60 минут, одна активная на инсталляцию, максимум 100 МиБ; подробные данные — 24 часа после остановки.
- ZIP: максимум 200 МиБ, TTL 24 часа; один building и максимум два queued; один незавершённый пакет администратора, максимум три создания в час.
- Файловый бюджет 500 МиБ = backend 480 + frontend 20; включает временные файлы, control, snapshots, архивы и reservations. Резерв свободного места — 2 ГиБ. Метаданные SQL и журнал ИБ остаются отдельными политиками.
- Событие максимум 8 КиБ, стек максимум 32 кадра; сегмент максимум 5 МиБ; backend очередь максимум 4096 событий, Node максимум 256. Новые буферы также ограничены явно.
- Admin/RBAC/CSRF, обязательный SQL-аудит и fail-closed выдача сохраняются. Остановка/TTL не зависят от доступности БД. Базовая запись не использует БД на каждое событие.
- Очистка безопасных полей до очереди и диска; вход экспорта проверяется повторно. Никаких bodies, промптов, исходного текста, `str(exc)`, SQL, env dump, имён файлов пользователя, URL query, cookies или секретов на любом уровне.
- Проверка путей, symlink/junction/reparse, writer ownership, cutoff, leases, CRC, SHA-256, аудит ready и право на скачивание не отключаются ради скорости.
- Один backend и один Node writer; никаких новых SaaS, агентов на production, Docker socket или прав frontend на backend spool.
- Флаги baseline/capture/bundle/download остаются отдельными. В этом плане нет включения production-флагов, перезапуска рабочего стека или изменения dev/test/prod идентичности.
- Windows host-порты 18000/16300, внутренние Compose 8000/3000 сохраняются. Локальные npm shims не использовать.
- Не увеличивать очереди/квоты/таймауты как замену устранению затрат. Не сокращать retention и не удалять существующие файлы для красивого результата benchmark.

## Review Focus

1. Сессия включена/остановлена посередине запроса, последняя ошибка пришла после HTTP 200: ошибка и код обращения сохраняются, границы покрытия честны — задачи 3, 4, 10.
2. Поток успехов заполнил обычную очередь перед редкой ошибкой; stop/barrier пересекается с приоритетной обработкой — задача 4.
3. Узкий пакет при почти полном несвязанном spool; чтение одновременно с append/rotation/TTL, остановка child посередине записи — задачи 7, 8.
4. Новый backend со старым frontend, восстановленная старая БД, смешанный v1/v2 журнал: нет скрытого подробного сбора или ложной полноты — задачи 2, 6, 9.
5. Медленный/недоступный диск во время heartbeat или status polling: бизнес-запрос не ждёт файловый lock через lock сессии; недоступный статус не выглядит выключенным — задачи 5, 7, 9.

## 2. Пользовательская модель и состав данных

В интерфейсе показывать постоянный «Базовый журнал» отдельно от временного сбора. В форме запуска две независимые настройки: «Область» и «Детализация». Значения временной детализации: «Стандартная» и «Подробная»; новая сессия по умолчанию стандартная. Не создавать третье значение `baseline` в `SessionStart`: это действующий постоянный режим, а не ещё одна временная сессия.

Scope остаётся `system|document|search_chat|interface`. Начальное значение scope сохраняется `system`, чтобы не выбирать за администратора чужой сценарий; рядом дать короткую рекомендацию выбирать узкую область. При активной сессии её уровень неизменяем: остановить и создать новую с новым ID. Рестарт не возобновляет capture.

| Данные | Базовый | Стандартный capture | Подробный capture |
|---|---|---|---|
| WARN/ERROR, неуспешные зависимости, HTTP 4xx/5xx, lifecycle | Существующее поведение | Сохранять с корреляцией | Сохранять с корреляцией |
| Browser errors | Нет скрытого сбора | Только opt-in и подходящий scope | То же |
| Начало/итог фоновой обработки документа, переходы parse/split/generate/index/publish | Только ошибки по прежней политике | Сохранять крупные переходы и итог | Сохранять также детализацию выбранной операции |
| Успешные короткие search/chat-операции | Не писать | Один ограниченный итог + агрегаты | Цепочка событий выбранных операций + агрегаты всех |
| Успешные dependency calls | Не писать | Количество/время/максимум; отдельно медленные | Отдельные события выбранных операций |
| Успешные HTTP/proxy, health/status polling | Не писать | Агрегаты; индивидуально медленные | Выборка связанных запросов; служебный polling остаётся агрегированным |
| Retry, fallback, деградация, частичная индексация | Ошибки/предупреждения по текущей классификации | Сохранять | Сохранять |
| Текст запроса/документа/ответа модели, raw stack locals | Никогда | Никогда | Никогда |

Один итог короткой операции заменяет её `operation_started`, дублирующие `stage_started/stage_finished` для того же внешнего этапа и `operation_finished`; ошибки не превращаются в успешный итог. У фоновых документов сохранять крупные этапы: иначе после kill/OOM нельзя определить последний известный этап. Per-chunk/per-batch успешные события в стандартном режиме агрегировать; счётчики прогресса не считать диагностической потерей.

HTTP и proxy имеют разные границы измерения, особенно для streaming. Не складывать их длительности и не называть время до заголовков временем полного ответа. У stream сохранять отдельную ошибку после 200 и стабильный request ID. Успешный heartbeat или health poll сам по себе не должен порождать рекурсивный диагностический шум.

## 3. Политика записи и новые ограниченные структуры

Стартовые параметры фиксируются в immutable policy snapshot сессии; это эксплуатационные настройки запуска, не административные DB overrides. Не менять их на ходу у активной сессии.

| Параметр policy v1 | Значение |
|---|---:|
| Период выдачи агрегатов | 5 секунд |
| Ключей агрегирования на компонент/активную сессию | максимум 256 |
| Итогов успешных коротких операций в standard | максимум 10/с суммарно на backend, burst 10 после замера CT 102 |
| Новых подробных цепочек в detailed | максимум 1/с суммарно на компонент, burst 1 после замера CT 102 |
| Одновременно отслеживаемых цепочек на компонент | максимум 512 |
| Группа записи writer | до 64 КиБ или 50 мс, что раньше |
| Резерв очереди backend / Node для важных событий | 512 / 32 при штатных размерах очереди |
| Порог медленного HTTP/search | 1000 мс |
| Порог медленного Qdrant / DB | 250 мс |
| Порог медленных embeddings / backend proxy | 1000 мс |
| Порог медленного LLM / chat | 30000 мс |
| Порог PDF dependency | 5000 мс |

Пороги — диагностические ориентиры; нарушение SLA определяется отдельной приёмкой. Медленный успех сохраняется вне обычной выборки, но не получает безусловный резерв ERROR: иначе общий slow storm вытеснит ошибки. Для него отдельный rate limit 20/с на компонент с агрегированием остальных и явным счётчиком. WARN/ERROR/retry/деградации и lifecycle не отбрасываются политикой успехов, но физическая потеря при исчерпании общей очереди всё равно возможна и показывается.

Для очереди уменьшенного тестового размера `reserve=min(default_reserve, max(1, queue_size//8))`. Очередь остаётся одной по общему пределу: обычные записи занимают не более `queue_size-reserve`, важные используют оставшееся место. Это сохраняет порядок accepted sequence и упрощает барьер; отдельное переупорядочивание по приоритетам не вводить. При размере 1 обычные записи не принимаются.

Агрегаты группируются только по ограниченным значениям `(session_id, component, route_template, stage, dependency, outcome)`; без request ID, имён документов и произвольных строк в ключе. Поля: `count`, `duration_sum_us`, `duration_max_us`, фиксированная гистограмма длительностей с границами 10/50/250/1000/5000/30000 мс и последним bucket выше 30000. Это гистограмма, а не точный p95. Для document scope doc_id задаётся сессией; у system нельзя автоматически добавлять doc_id в каждый ключ. Переполнение ключей идёт в один overflow bucket с видимым счётчиком, а не в неограниченный dict.

При detailed решение принимается один раз на корневую операцию, переносится через существующий immutable context во все её потоки и дочерние вызовы. Для HTTP/search ключ — серверный request ID; для фонового документа — operation ID новой попытки. Клиент не управляет admission выбором UUID. Выбранная цепочка сохраняет все разрешённые события до границы сессии/лимита; достижение 512 in-flight запрещает новые подробные цепочки, но не выталкивает уже выбранные. Новая попытка resume имеет новое решение. Выборка Node и backend имеет отдельные бюджеты, поэтому межкомпонентную цепочку нельзя обещать полной, если одна сторона её не выбрала.

Сессия может включиться посреди существующей операции: события после включения допускаются, manifest явно сообщает, что начала до включения нет. При ошибке невыбранной операции записывается ошибка с ID, но прежняя история не восстанавливается из воздуха. Кольцевой буфер всех предшествующих событий и автоматическое повышение уровня при ошибке в эту версию не входят.

Счётчики различать: `aggregated_success`, `sampled_out_traces`, `sampled_out_slow`, `policy_omitted`, `aggregate_overflow` — намеренное сокращение; `dropped`, `invalid`, `expired_queue`, `drain_timeouts`, `storage_errors` — потери/сбои. Агрегация и выборка не означают `partial=true` сами по себе; manifest обязан явно описывать соответствующее покрытие. Потерянный flush агрегатов — уже потеря данных. Старый `sampled_success` читается как legacy-счётчик, не переименовывается задним числом.

## 4. Хранение, snapshot и сборка

1. Producer сначала проверяет неизменяемую политику и scope по техническим полям; отброшенный успех не создаёт UUID, asdict, JSON или stack. Принятое событие полностью проверяется до очереди; worker получает только безопасный immutable envelope. Отдельный внешний `append(bytes)` сохраняет строгую проверку для обходящих recorder callers.
2. Writer собирает batch отдельно на каждый stream, сохраняя целые JSONL-строки, границы 5 МиБ и точный учёт записанных байтов. Flush обязателен перед snapshot barrier, ручным stop и shutdown. При deadline/автоматическом stop сохраняется действующая политика прекращения записи, не возникает тайное продление сессии.
3. Index файлов хранилища строится при startup/recovery и обновляется при каждом собственном росте/rename/delete. Обход дерева остаётся в maintenance и явной сверке, а не на каждом событии. Расхождение с диском переводит запись в degraded и запускает сверку; внешние изменения не игнорируются. Статус берёт кэш с временем измерения, не запускает повторные полные обходы.
4. Реальный free-space проверяется перед каждым batch/выделением квоты; нельзя обещать свободное место по устаревшему кэшу. Резервирование должно учитывать конкурентный рост child, Node-бюджет отделён. Контроль файлов и отказ от ссылок сохраняются при открытии/ротации.
5. Сессия хранит immutable runtime view; её lock защищает только смену состояния и счётчики. DB, fs, control projection, fsync и ожидание writer выполняются вне lock, нужного producer. Публикация projection использует revision: устаревшая active-запись после stop не может вернуть подробный сбор. В Node lease остаётся 10 с с backend refresh каждые 5 с.
6. После writer barrier под коротким store lock фиксируются descriptors сегментов: внутренний ID, stream, identity, допустимая длина на границе полной записи, cutoff и TTL. Pin защищает backend-сегменты от физического удаления, но не продлевает право читать уже истёкшие данные. Чтение/JSON/filter/copy не держат store lock.
7. Child читает только зафиксированные префиксы backend-сегментов. Дописывание за пределами prefix допустимо; изменение/усечение самого prefix — ошибка. Frontend остаётся read-only источником: disappearance/rotation отмечаются gap, child никогда не удаляет его сегменты. Snapshot frontend учитывает identity, исходную длину, границы строк и TTL.
8. Child валидирует и фильтрует вход, раскладывает его в три нормализованных component-файла snapshot. В квоту входят только фактически выбранные данные и их разрешённый рост. Не резервировать весь исходный spool: узкий пакет при большой несвязанной истории должен оставаться возможным.
9. Квотами управляет родитель через ограниченный протокол grant/commit между процессами. Child до записи запрашивает кредит порциями до 64 КиБ; не пишет сверх него. Родитель держит outstanding credit в reservations, после подтверждения переводит фактический рост в used. При crash/kill освобождает кредит только после остановки child и сверки/очистки его файлов. Deadline worker — существующие 300 с, один builder. Инвентаризация не должна повторно считать committed и ещё reserved байты; для active child используются записи ledger и outstanding grants.
10. ZIP строится из трёх нормализованных файлов с хэшами/счётчиками, рассчитанными при подготовке. Не сканировать все входные файлы отдельно для каждого component. Сохранять обнаружение изменений исходного prefix, включая повреждённые строки и неизвестные форматы, и нормализованных файлов; CRC и финальный SHA остаются. Состав ZIP — прежние семь имён файлов, DEFLATE level 1.
11. Родитель один публикует ready, SQL audit, download metadata и rename. Убийство child, delete во время building, отказ аудита и shutdown не дают доступного ZIP и не оставляют вечные reservations/pins.

Отдельная экономия: `events/query` не должен создавать экспортный snapshot и копировать весь журнал ради 50 строк. Использовать ограниченное чтение pinned prefixes с фильтрацией и pagination. Просмотр остаётся аудируемым; аудит фиксируется до отдачи строк. В pagination неизменяемый cutoff, лимит 50 по умолчанию/100 максимум, offset по существующему контракту. Из-за TTL/rotation между страницами нельзя обещать вечный снимок; возвращать признак изменения покрытия, а не удерживать leases между HTTP-запросами.

## 5. Версии, данные сессии и совместимость

- Добавить SQL-поля `capture_level`, `policy_version`, `policy_snapshot`. Новые сессии явно пишут `standard|detailed`, `1`, ограниченный allowlisted snapshot. Для старых строк миграция выставляет `detailed`, `0`, `{}` — это прежний сбор с legacy sampling, не полная новая трассировка. ORM и новый API по умолчанию создают standard; SQL defaults остаются legacy-совместимыми для старого кода.
- Новая `SessionStart.capture_level` по умолчанию `standard`; неизвестный уровень — 422. `SessionOut`/status возвращают уровень, policy version, фактические счётчики и ограничения. Scope и минуты сохраняются.
- Добавить v2 schema для новых aggregate/outcome полей и событий. Readers принимают v1 и v2 с отдельными allowlists; writer новой версии пишет v2. Не ослаблять v1 валидатор. Общий corpus fixtures проверяет Python и Node. Запрет неизвестных полей сохраняется.
- Точный контракт новых событий: `operation_summary` = прежние базовые поля + `stage`, `duration_ms`, `counts`, `outcome` из `success|cancelled`; неуспех сохраняет прежний `operation_failed`. `success_aggregate` = базовые поля без request/operation ID + необязательные route/stage/dependency из allowlists + `outcome=success`, `window_start_utc`, `window_end_utc`, `counts`. В counts разрешить `count`, `duration_sum_us`, `duration_max_us`, `le_10ms`, `le_50ms`, `le_250ms`, `le_1000ms`, `le_5000ms`, `le_30000ms`, `gt_30000ms`; buckets непересекающиеся несмотря на верхнюю границу в имени. Все значения целые 0..10^12, сумма buckets равна count. При приближении к численному пределу досрочно выдать aggregate и начать новый, не переполнять/обрезать молча. Duration/outcome endpoint и proxy группируются раздельно по component.
- Manifest v2 добавляет `capture_policies`, `coverage_modes`, `counter_scopes` и `loss_counters_state`. Пакет по временному интервалу может включать несколько старых/новых сессий; не подписывать весь архив одним уровнем. Старые готовые ZIP не переписывать.
- Policy snapshot нужен и offline без БД: хранить безопасную копию policy в bounded control metadata, без пользователей/invitations. Активная сессия после restart не возобновляется. Потеря volatile счётчиков после crash обозначается `unknown_after_restart`, а не доказанной полнотой; metadata checkpoints не чаще раза в 5 с и при штатном stop.
- Control projection v2 содержит только разрешённые policy-поля. Новый frontend при v1/неизвестной версии не начинает новый подробный сбор, оставляет baseline и сообщает несовместимость. Старый frontend при v2 уже отвергает неизвестную schema; это проверить fixture. UI не отправляет новую форму, если backend capabilities не подтверждают уровни.
- При upgrade сначала writers/readers одной версии на тестовом стенде; mixed version обязан дать явное ограничение покрытия. Rollback — выключить capture/bundle/download, оставить cleanup; сохранить совместимые таблицы/файлы. Не откатывать на код, экспортирующий неизвестный v2 как полный или вновь включающий raw dumps.

## 6. Карта файлов

Все пути ниже относительно checkout исполнения, содержащего реализацию admin-diagnostics.

| Файлы | Изменение |
|---|---|
| `backend/app/services/diagnostics/policy.py` (новый) | Immutable policy, admission цепочек и ранний выбор состава |
| `.../aggregation.py` (новый) | Ограниченные числовые агрегаты и flush |
| `.../schema.py`, `sanitize.py`, `context.py`, `events.py`, `middleware.py`, `recorder.py` | v2, перенос решения, границы операций и очередь |
| `.../sessions.py`, `control.py`, `runtime.py`, `backend/app/config.py` | Snapshot policy, состояние без I/O под producer lock, настройки |
| `backend/app/models/diagnostics.py`, `api/diagnostics.py`, `db/models.py` | DTO/API/SQL-поля, чтение страниц |
| `backend/alembic/versions/<new_revision>_diagnostic_capture_policy.py` | Новая forward-миграция; ID определить по актуальному head, старую миграцию не менять |
| `.../store.py`, `segment_index.py` (новый), `read_view.py` (новый) | Ledger, batch append, pins, bounded prefix reader |
| `.../snapshot.py`, `bundle.py`, `bundle_queue.py`, `bundle_worker.py`, `worker_protocol.py` (новый) | Child prepare/build, quota broker, консистентность и cleanup |
| `backend/app/services/pipeline.py`, `api/search.py`, `api/chat.py`, `services/chat_stream.py`, `services/embedder.py`, `services/vector_store.py` | Подключение политики к фактическим producer; никаких изменений результатов поиска/генерации |
| `frontend/src/lib/diagnosticPolicy.mjs` (новый), `diagnosticServer.mjs`, `diagnosticSchema.mjs`, `diagnosticContract.json` | Node policy/aggregates/batching и совместимый формат |
| `frontend/src/lib/diagnostics.mjs`, `components/DiagnosticsPanel.jsx`, `lib/api.js`, `i18n/locales/{ru,en}.js` | Выбор уровня, capability fallback, coverage UI |
| `backend/app/i18n/{ui_keys,ui_en}.json` | Генерируемые UI-манифесты |
| `backend/test_scripts/probe_diagnostics{,_http}.py`, `probe_diagnostic_storm.py`, `frontend/test_scripts/` | Воспроизводимые измерения и RSS |
| `backend/scripts/collect_diagnostics.py`, `docs/ADMIN_DIAGNOSTICS.md`, `SECURITY.md`, production env examples | Offline совместимость, runbook, эксплуатация/откат |

## 7. Задачи реализации

Каждая задача: сначала содержательный RED по новому контракту, затем реализация, GREEN и просмотр собственного diff. Сбой окружения не является RED. Не выполнять commit автоматически. После каждой группы фиксировать evidence в новом отчёте `docs/superpowers/reports/2026-09-28-admin-diagnostics-levels-performance.md`, не переписывать историческую приёмку.

### Задача 1. Зафиксировать воспроизводимый baseline и точки измерения

**Files:** существующие три backend probe, новый `backend/tests/test_diagnostics_probe_contract.py`, новый отчёт; исходная спецификация и прежний performance-план.

**Interfaces:** probe принимает `--capture-level standard|detailed`, `--concurrency`, `--seed`, `--requests`, `--warmup-seconds`, `--output`; legacy-версия запускается без нового параметра уровня. Выход JSON: параметры окружения/политики, latencies, event counts/bytes, losses, peak RSS parent/child/Node, фазовые timestamps `barrier/pin/prepare/zip/publish`. В каждом sample фиксируются начало/конец и активная фаза builder; ответы поиска не сохраняются.

- [ ] Зафиксировать содержимое незакоммиченной базы: HEAD + хэш diff и хэши новых файлов, версии runtime, FS/mount, CPU/RAM, фоновые процессы. Секреты и тела запросов исключить.
- [ ] Добавить тест probe: неизвестный уровень не подменяется default; overlap считается по временному пересечению, а не только по `ready`; peak RSS не заменяется RSS после завершения.
- [ ] Выполнить `python -m pytest tests/test_diagnostics_probe_contract.py -q`, получить ожидаемый RED; затем добавить контракт измерений и GREEN.
- [ ] Снять старый baseline до изменений на изолированном синтетическом стенде; для каждой фазы сохранить время, входные/выходные bytes и время ожидания lock. Временная instrumentation не пишет per-event логи в измеряемый spool.
- [ ] Перенести разделы 2–5 этого документа в дополнение к спецификации; в прежнем performance-плане указать новый план продолжения, сохранив старые замеры. Выполнить `git diff --check` только после проверки, что не затронута параллельная работа.

**Результат:** сохранённая сравнимая исходная серия и точный контракт будущих замеров; никаких утверждений об ускорении.

### Задача 2. Контракт уровней, версии и миграция

**Files:** schema/sanitize, config, DTO, DB, новая migration, sessions/control, fixtures; `backend/tests/test_diagnostics_policy_contract.py` (новый), существующие migration/schema/API tests.

**Interfaces:** `CaptureLevel = Literal["standard", "detailed"]`; frozen `CapturePolicy(level, version, aggregate_interval_ms, success_limit_per_second, trace_limit_per_second, slow_limit_per_second, max_inflight_traces, slow_thresholds_ms)`; `build_policy(level, settings) -> CapturePolicy`. `SessionService.start(scope, minutes, actor, *, doc_id=None, capture_level="standard") -> SessionOut`.

- [ ] RED: `test_new_session_defaults_to_standard`, `test_unknown_level_rejected`, `test_legacy_rows_preserve_policy_zero`, `test_active_policy_is_immutable`, `test_v1_v2_canaries_rejected`. Проверить defaults API отдельно от SQL defaults.
- [ ] Новые startup settings с указанными значениями и границами: aggregate 1–10 с, success/trace/slow rate 1–100, in-flight 1–512, thresholds 1–300000 мс. Queue/batch limits остаются внутренними константами и DiagnosticLimits; не добавлять меню для каждой цифры.
- [ ] Добавить forward migration, проверить SQLite и PostgreSQL upgrade с существующей строкой сессии. Не полагаться на `create_all` для новой колонки; не переписывать уже использованную migration.
- [ ] Добавить v2 `operation_summary`, `success_aggregate` и точные поля/числовые пределы из раздела 3; v1 принимает только прежнюю схему. Обновить corpus и Node contract одновременно.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_policy_contract.py tests/test_diagnostics_schema.py tests/test_diagnostics_migration.py tests/test_diagnostics_api.py -q`.

Минимальные точные утверждения контракта для первого теста:

```python
assert SessionStart().capture_level == "standard"
assert SessionStart().minutes == 15
assert SessionStart(scope="search_chat", capture_level="detailed").doc_id is None
with pytest.raises(ValidationError):
    SessionStart(capture_level="raw")
```

**Результат:** уровень сохраняется и читается однозначно; записи разных поколений не смешиваются под ложной меткой.

### Задача 3. Ранний отбор, агрегаты и корреляция операций

**Files:** новые policy/aggregation, context/events/middleware, перечисленные producer; новые `test_diagnostics_policy.py`, `test_diagnostics_aggregation.py`, существующие context/dependencies/pipeline tests.

**Interfaces:** `CaptureRuntimeView(session_id, revision, scope, doc_id, deadline_mono, policy)`; `PolicyEngine.decide(view, event_code, context, facts) -> CaptureDecision` с действием `record|aggregate|omit` и счётчиком причины. `facts` — только проверенные статус/длительность/этап/зависимость. `AggregateBuffer.observe(key, duration_us, outcome) -> None`, `flush(session_id, cutoff_mono) -> tuple[SafeAggregate, ...]`. `SafeAggregate` содержит только разрешённые поля из раздела 3, затем проходит v2 sanitizer.

- [ ] RED: 1000 успешных dependency calls дают точные count/sum/max/histogram, а не 1000 записей; четыре внешних lifecycle-события search дают один summary для допущенной операции; ошибка не становится успехом.
- [ ] RED: 257-й ключ не расширяет buffer; опоздавший thread корректно связан с operation; 513-я подробная цепочка не вытесняет 512 живых; лимит 20 новых цепочек не режет середину уже принятой.
- [ ] Проверить, что отброшенное событие не вызывает UUID/asdict/encode/safe_exception, но произвольные input fields не копируются даже во временный aggregate buffer.
- [ ] Добавить scoped runtime view и перенос решения через реальные worker/context boundaries. На смене session revision старое решение не записывает данные в новую сессию. Очистить in-flight при finally/cancel; для незавершённой операции — при stop/deadline, без утечки state.
- [ ] Сохранить крупные этапы pipeline, failed dependencies, retries/fallback и error-after-200. Попадание в normal failure queue не зависит от решения выборки.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_policy.py tests/test_diagnostics_aggregation.py tests/test_diagnostics_context.py tests/test_diagnostics_dependencies.py tests/test_diagnostics_pipeline.py -q`.

**Результат:** стандартный режим действительно создаёт меньше событий; detailed сохраняет выбранные цепочки с документированными границами.

### Задача 4. Резерв очереди, flush и честные счётчики

**Files:** recorder/runtime, aggregation; `backend/tests/test_diagnostics_recorder.py`, новый `test_diagnostics_admission.py`, lifecycle/integration tests.

**Interfaces:** `AdmissionQueue.offer(envelope, *, important: bool) -> bool`; `barrier(timeout_seconds) -> BarrierResult(completed, failed_count)`; `RecorderCounters.snapshot(scope) -> dict`. Для accepted envelopes последовательный номер; barrier подтверждает только записи до его watermark, включая aggregate flush и repeat summary.

- [ ] RED: заполнить 3584 обычных slots, принять 512 важных, 4097-я запись получает видимый drop; аналогично уменьшенная очередь и очередь размера 1. Producer никогда не ждёт свободного места.
- [ ] RED: ручной stop при задержанном writer дописывает принятые события; timeout помечает loss; flush агрегатов включён в barrier; автоматическое expiry не принимает новые detail события.
- [ ] Реализовать резерв в одной bounded FIFO, не две независимые очереди размером 4096. Важность не определять по произвольному сообщению logger.
- [ ] Ввести отдельные intentional/loss counters с областями `session|backend_process|frontend_process`; counters не делают синхронную запись на каждый запрос. В checkpoints сохранять последнее известное состояние и время.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_admission.py tests/test_diagnostics_recorder.py tests/test_diagnostics_lifecycle.py tests/test_diagnostics_integration.py -q`.

**Результат:** успехи не занимают резерв ошибок; stop/snapshot не теряют незавершённый batch молча.

### Задача 5. Пакетный backend writer и точный ledger

**Files:** store, новый segment_index, recorder/sessions/control/runtime; новые `test_diagnostics_batching.py`, `test_diagnostics_lock_boundaries.py`, существующие store/retention tests.

**Interfaces:** `ValidatedEvent(encoded: bytes, event_code: str, timestamp_utc, session_id)` — только результат внутреннего валидатора, без внешнего API-конструктора. `append_batch(events: tuple[ValidatedEvent, ...], *, stream: str) -> BatchWriteResult(written_events, written_bytes, failure_reason)`. `SegmentIndex` учитывает каждый owned file и outstanding reservations; `reconcile() -> ReconcileResult`. Public `append(bytes)` валидирует и делегирует batch из одного события.

- [ ] RED: batch на границе сегмента не разрывает строку; short write и ENOSPC оставляют точные bytes/counts и loss; повторная JSON-сериализация безопасного внутреннего envelope не вызывается.
- [ ] RED: рост/rename/delete/control/tmp/child credits входят в ledger; переполнение session/backend/free-space отвергается до соответствующего роста. Внешний изменённый файл обнаруживается при сверке и не приводит к ложному `used=0`.
- [ ] Реализовать flush 64 КиБ/50 мс; держать не более одного открытого текущего файла на активный stream, закрывать при rotation/retire/shutdown. Обход сегментов заменить index lookup; safe path/identity проверять при открытии и смене файла.
- [ ] Вынести fs/SQL/projection из session lock. RED-тест ставит filesystem на latch и доказывает завершение producer admission без снятия latch; отдельный тест гонки active projection со stop проверяет revision fencing.
- [ ] `status` возвращает cached used/free и `measured_at`; unavailable остаётся unavailable. Maintenance сверяет физические данные с ledger без блокировки producer на весь scan.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_batching.py tests/test_diagnostics_lock_boundaries.py tests/test_diagnostics_store.py tests/test_diagnostics_retention.py tests/test_diagnostics_sessions.py -q`.

**Результат:** файловые операции выполняются на batch, нет disk I/O под lock горячего выбора сессии.

### Задача 6. Эквивалентная политика и batch writer Node

**Files:** diagnosticPolicy.mjs, diagnosticServer.mjs/schema/contract, proxy route/backendFetch при необходимости переноса решения; новые `frontend/test/diagnosticPolicy.test.mjs`, `diagnosticBatching.test.mjs`, существующие diagnosticServer/requestBoundary tests.

**Interfaces:** `selectCapture(control, eventFacts, traceState) -> decision`, `observeAggregate(key, durationUs, outcome)`, `flushAggregates(sessionId)`; JSON corpus policy одинаковый с backend. Node сам создаёт request identity; входной header не является выбором detailed цепочки.

- [ ] RED: стандартный успешный proxy даёт aggregate; ошибочный ответ/exception не отсекается; 224 обычных slots сохраняют 32 важных; malformed control не включает detail.
- [ ] Убрать full inventory из `_admit` каждого события: startup index + обновления owned writer + maintenance reconciliation. Batch 64 КиБ/50 мс, реальные проверки budget/free-space перед batch.
- [ ] Поддержать projection v2 и отказ detailed на несовместимой версии; 10-секундный lease/monotonic deadline/opt-in не ослаблять. Сессионные metadata не переписывать при каждом heartbeat, если эффективные данные не изменились; lease обновление остаётся своевременным.
- [ ] Сохранить handler chain, SIGTERM/PID1 marker cleanup, несколько Set-Cookie, NDJSON, cancellation и отсутствие response buffering. Проверить error после HTTP 200 и clear очереди browser при opt-out.
- [ ] GREEN из frontend: `node --test test/diagnosticPolicy.test.mjs test/diagnosticBatching.test.mjs test/diagnosticServer.test.mjs test/diagnosticRequestBoundary.test.mjs`.

**Результат:** переход на standard уменьшает нагрузку обоих сервисов, а не только backend.

### Задача 7. Короткие leases для чтения и просмотр без копирования spool

**Files:** store/segment_index, новый read_view, snapshot, API events/query; новые `test_diagnostics_read_view.py`, существующие store/API/retention tests.

**Interfaces:** `SegmentDescriptor(segment_id, stream, identity, length, expires_at)`; `pin_read_view(filter, cutoff_at) -> ReadViewLease` с tuple descriptors и idempotent `release()`. `read_event_page(view, query) -> EventPage(events, scanned_count, partial, gaps, next_offset)`; память ограничена 100 событиями и блоком чтения 64 КиБ. TTL проверяется при открытии каждого descriptor.

- [ ] RED: latch на чтении 50-МиБ segment не блокирует admission/append; append после зафиксированной длины не входит в view; rotation/TTL не удаляют pinned backend segment до release.
- [ ] RED: запрос 50 событий не создаёт directory `snapshots/`; отмена запроса и audit failure освобождают lease; без аудита строки не отдаются.
- [ ] Pins — ссылки на имеющиеся байты, не новая копия, но удержанные файлы продолжают учитываться в квоте. Если нечего безопасно ротировать, запись отказывает с loss; не превышать бюджет.
- [ ] Session selector отсекает явно несвязанные session streams до чтения; временной индекс сегментов только ускоряет, не заменяет per-event filter. Baseline может содержать связанные сессии: не исключать его вслепую.
- [ ] Сохранять фиксированный cutoff пагинации, прежний порядок результатов; при исчезновении/истечении между страницами сообщать изменение покрытия. Никаких долгоживущих cursor leases в браузере.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_read_view.py tests/test_diagnostics_store.py tests/test_diagnostics_retention.py tests/test_diagnostics_api.py -q`.

**Результат:** просмотр и snapshot больше не удерживают lock во время разбора всех строк.

### Задача 8. Изоляция подготовки данных и квотируемая сборка

**Files:** worker_protocol, snapshot, bundle/queue/worker, store ledger; новые `test_diagnostics_worker_protocol.py`, `test_diagnostics_prepare_worker.py`, существующие bundle/queue/CLI tests.

**Interfaces:** versioned framed IPC, максимум frame 64 КиБ: `prepare`, `request_credit`, `grant`, `commit_growth`, `phase`, `result`, `cancel`. Все сообщения содержат job UUID и monotonic sequence. Большие prepare/result передаются нумерованными частями, суммарно максимум 4 МиБ на сообщение; неполная последовательность не исполняется. Child получает только descriptors, технические фильтры, safe policy/runtime snapshots и свой output directory. `QuotaBroker.grant(job_id, bytes) -> grant_id`; `commit(grant_id, actual_bytes)` требует `0 <= actual_bytes <= granted`. Родитель проверяет принадлежащие job пути, не доверяет произвольным child paths.

- [ ] RED: prepare реально выполняется в другом PID; stalled child не блокирует requests; неизвестное сообщение/oversized frame/двойной commit/неверный sequence завершают job без ready.
- [ ] Перенести validation/filter/demultiplex в child; metadata БД и control audit остаются у родителя. Без полного копирования исходной истории; selected rows пишутся только после grant. Начало/конец фаз доступны probe без документного контента.
- [ ] RED: почти полный unrelated spool + маленький фильтр успешно строятся; большой выбранный результат fails с квотой; child не может писать на заёмные невыделенные байты. Полный reserve всех входных сегментов запрещён.
- [ ] RED: append после prefix допустим; mutation существующего prefix, включая битую строку, обнаруживается; unknown version/invalid line создают корректные counts/gaps. После pin не следовать подменённому symlink/junction.
- [ ] Формировать три нормализованных component-файла одним проходом в child; читать каждый для ZIP без повторного разбора чужих components. Подсчёт/хэши во время подготовки плюс проверка неизменности, CRC, checksum ZIP и прежние семь файлов.
- [ ] RED: kill во время grant/write/rename, 300-секундный timeout, delete-building, отказ ready-аудита, shutdown queued/building освобождают pins/credits; рестарт не пересобирает автоматически. Старое скачивание с lease завершается после удаления, новый GET запрещён.
- [ ] GREEN: `python -m pytest tests/test_diagnostics_worker_protocol.py tests/test_diagnostics_prepare_worker.py tests/test_diagnostics_bundle.py tests/test_diagnostics_bundle_queue.py tests/test_collect_diagnostics.py -q`; повтор основных race tests на Windows и Linux.

**Результат:** из основного процесса вынесена подготовка больших объёмов, а не только ZIP compression; квоты остаются общими и проверяемыми.

### Задача 9. Manifest, offline, status и интерфейс уровней

**Files:** DTO/API/sessions/control, bundle/snapshot, collect CLI, DiagnosticsPanel/diagnostics/api/i18n и manifests; новые `test_diagnostics_coverage.py`, frontend `diagnosticCoverage.test.mjs`, существующие UI/CLI tests.

**Interfaces:** `capabilities.capture_levels=["standard","detailed"]`, `policy_version=1`; `SessionOut.capture_level/policy_version`; manifest v2 `capture_policies` (список session IDs + safe policies), `coverage_modes` и `loss_counters_state`. Значения покрытия `errors|aggregated|sampled|detailed|legacy|unknown`, отдельно от `partial/gaps`.

- [ ] RED: interval ZIP с v1/v2 и несколькими сессиями показывает все policies; offline без БД читает safe policy projection, missing projection даёт unknown. Crash до checkpoint не выдаётся за отсутствие потерь.
- [ ] UI: «Базовый журнал»; выбор «Стандартная»/«Подробная»; короткое описание сохраняемых данных; активные scope/уровень/countdown; намеренная агрегация отдельно от потерь; legacy и unavailable имеют понятные RU/EN подписи.
- [ ] Не делать дополнительное подтверждение каждого старта. Browser participation остаётся отдельным opt-in, независимо от выбранного уровня. Уровень не является разрешением собирать браузеры всех пользователей.
- [ ] Preview показывает область и реальное покрытие. `partial=false` подписывать как отсутствие обнаруженных потерь в заявленном режиме, а не как «сохранены все события». Старый ZIP показывается по старому manifest без переписывания.
- [ ] Проверить capability fallback со старым backend: не отправлять неизвестное поле, не подписывать его legacy capture как standard. Перед изменениями Next читать installed docs из frontend/AGENTS.md.
- [ ] GREEN: backend coverage/API/CLI tests; frontend `node --test test/diagnosticCoverage.test.mjs test/diagnostics.test.mjs test/diagnosticClient.test.mjs`; выполнить `node scripts/export-ui-keys.mjs`, затем `node --test test/i18n.test.mjs`.

**Результат:** администратор понимает, какие данные получит, а разработчик видит ограничения пакета без доступа к системе.

### Задача 10. Комплексные отрицательные проверки

**Files:** diagnostics privacy/integration/lifecycle/context tests, Node recorder/client tests, общий synthetic fixture corpus; SECURITY.md.

- [ ] Добавить canaries через успехи, агрегаты, failed LLM parse, SQL exception, браузер/proxy и chain exceptions. Проверить queue envelopes, spool, control, SQL diagnostic metadata, API, ZIP и worker IPC. Обычный debug stdout не превращать во вход пакета.
- [ ] Проверить start посреди операции, stop при pending batch, expiry во время build, session switch, scope document без чужих doc/job IDs, end-to-end correlation в ThreadPoolExecutor и streaming.
- [ ] Проверить DB down + disk full, malformed control, mixed writers/readers, восстановленную старую БД без файлов, rotation/sweep с pins, audit gap, отозванную роль и CSRF на каждом действии/новом download.
- [ ] Проверить одинаковые ошибки разных request IDs: существующая дедупликация не должна скрывать отдельный инцидент. Перегруппировку всех ошибок по одной глобальной сигнатуре не добавлять в этой версии.
- [ ] GREEN: `python -m pytest tests/ -k diagnostics -q` с отдельным basetemp; Node полный diagnostics subset. Обновить SECURITY.md реальными ссылками на evidence, не утверждениями о ещё не пройденных проверках.

**Результат:** уменьшение объёма не ослабило контроль доступа, очистку данных и расследование ошибок.

### Задача 11. Нагрузочная приёмка и настройка стартовых значений

**Files:** probes задачи 1, новый отчёт и synthetic artifacts в `tests/artifacts/diagnostics/`; временное хранение — отдельный `tests/tmp/diagnostics-performance/`.

- [ ] Снять матрицу из раздела 8. После каждого изменения policy/writer/snapshot выполнять один сравнимый блок замеров, чтобы различать источник эффекта. Не смешивать старые и новые build IDs.
- [ ] Для каждого режима зафиксировать p50/p95/p99/max, throughput, peak RSS parent/child/Node, CPU, events/s и bytes/operation по event_code, queue high-water, intentional counters, losses, lock wait, стадии builder.
- [ ] Сравнить event counts на одном детерминированном fixture: standard не пишет успешные per-call dependency и дубли коротких stage; aggregate totals равны реально выполненным calls. Отдельно проверить неблагоприятный набор уникальных request IDs, где дедуп почти не работает.
- [ ] Если лимиты времени не проходят, выбрать изменение по профилю и повторить соответствующие tests/серии. Менять цифры policy разрешено только с записью before/after и обновлением policy snapshot/tests. Пороги приёмки не ослаблять молча.
- [ ] Проверить оба FS-сценария: Linux native volume — основной acceptance, Windows native dev — совместимость; Docker Desktop bind-mount — отдельный стресс-сценарий, не смешивать с Linux SLA.

**Результат:** измеренное выполнение либо честно оставшийся блокер для каждого режима.

### Задача 12. Регрессия, документация и подготовка выпуска

**Files:** runbook/SECURITY/spec/старый performance-план/новый отчёт, CI, env examples только при добавлении startup settings.

- [ ] Обновить runbook: выбор уровня и scope, значения по умолчанию, значение агрегатов/выборки, неизвестных счётчиков после crash, ограничения detailed, offline и несовместимость версий.
- [ ] Выполнить команды раздела 9 после focused GREEN. Любой общий known failure проверить на неизменённой базе отдельно и описать; красный набор не называть зелёным.
- [ ] Изолированный Linux smoke bundled/external: upgrade старой схемы, restart, admin start→reproduce→stop→preview→download→CRC→delete, reader denial, offline без БД, ускоренный TTL и restore metadata без spool.
- [ ] Windows: пути с кириллицей/пробелом, partial writes, pins/leases, junction rejection, штатная остановка worker. UI RU/EN, узкий экран, две вкладки, новая ошибка после 200, новый GET после удаления запрещён.
- [ ] Откат на тестовом стенде: stop capture, запрет новых bundles/download при необходимости, остановка child и освобождение leases; baseline/cleanup сохраняются. Схему и файлы не удалять. Проверить старый reader явно, не рассчитывать на случайную совместимость v2.
- [ ] Сохранить итоговую таблицу критериев и артефактов. Commit/push/merge/production выполнять только по отдельному поручению; stage только относящиеся к задаче файлы/хунки.

**Результат:** reviewable изменение с доказанной функциональностью, численными результатами и проверенным откатом.

## 8. Матрица измерений и критерии приёмки

Изолированный Linux стенд, синтетический корпус, одинаковые образы/CPU/RAM/FS. Не запускать pytest, сборку frontend и perf одновременно. Для каждого режима прогрев не менее 30 с, затем не менее 1000 запросов и не менее 60 с измерений; минимум три повторения с перестановкой порядка режимов. Concurrency 1 и 8; долгие LLM-сценарии измерять отдельно с управляемой заглушкой и успешным контролем конечного результата.

| Сценарий | Режимы | Что доказывает |
|---|---|---|
| `/api/settings` и поиск с 10-мс stub | baseline, legacy capture, standard, detailed | Фиксированные расходы; legacy — только исходная версия, не production-переключатель |
| Поиск через frontend + PostgreSQL/Qdrant | baseline, standard, detailed | Полный HTTP-путь; 1 и 100 синтетических документов, разные route/query distributions |
| Одновременный ZIP | baseline+ZIP, standard+ZIP, detailed+ZIP | Влияние каждой фазы; выделить samples реально пересекающие prepare и zip |
| ZIP после stop | stopped standard/detailed + текущая бизнес-нагрузка | Отдельная цена пакета без новых detail events |
| Spool близко к квоте | narrow doc/request/session и широкий interval | Нет повторных полных сканов на append и ложного отказа узкого пакета |
| Генерация/stream | stub LLM, retries, chunk batching, cancel | Уменьшение записей не меняет исход операции и error reference |
| Error storm | 10 000 попыток/с в течение 60 с | bounded memory/disk, честные losses, API остаётся отвечающим |

Baseline — новый код с baseline on, capture off; дополнительно один диагностический контроль с baseline off для определения постоянной цены instrumentation. Он не подменяет основной baseline. Для ZIP фиксировать и одинаковый входной объём (сравнение алгоритма), и реальный объём, создаваемый каждым уровнем (пользовательский эффект). Нельзя выдать уменьшившийся архив за ускорение обработки одинакового количества данных.

Критерии для normal workload, отдельно standard и detailed:

- p95 capture <= baseline × 1,10; p95 capture+build <= baseline × 1,20. В отчёте также абсолютная прибавка в мс. Все три серии публикуются; один удачный результат не является приёмкой.
- Если одна серия превышает порог — повторить полный блок для исключения внешнего шума; устойчивое превышение остаётся невыполненным критерием. Отдельного более мягкого SLA для detailed этот план не вводит.
- Прирост peak RSS backend parent вместе с child <=128 МиБ; Node <=32 МиБ. Старые измерения RSS после завершения не доказывают этот критерий.
- Нет новых business 5xx, изменения ответов поиска/генерации, непреднамеренных потерь при штатной нагрузке; все expected aggregate counts сходятся. Намеренная выборка отражена в manifest.
- В error storm потери допустимы и численно видны; очереди/диск/RSS ограничены, health/search отвечают в заданных probe timeout. Не обещать сохранение всех 600 000 событий.
- Нет превышений квоты, оставшихся pins/credits после stop/kill, повреждённых или опубликованных до audit ZIP. Действуют privacy/RBAC/TTL/streaming проверки.
- Если typical workload проходит, а 10-мс стресс-сценарий нет, область применимости явно согласовывается отдельно; не переименовывать тест и не менять baseline ради зелёного результата.

## 9. Команды проверки

Команды запускать из соответствующей директории checkout исполнения. Использовать интерпретатор проектного окружения; если в worktree нет `.venv`, взять проверенный interpreter базового проекта и запускать с cwd backend worktree. Все данные/env тестов синтетические. Для параллельных прогонов разные basetemp; предпочтительно не запускать их параллельно с приёмкой другой задачи.

Backend, адресный цикл (из `backend/`):

```powershell
python -m pytest tests/test_diagnostics_policy.py -q --basetemp=../tests/tmp/pytest-runs/diagnostics-policy
python -m ruff check app/services/diagnostics app/api/diagnostics.py app/models/diagnostics.py
```

Итоговая регрессия (из `backend/`):

```powershell
python -m ruff check .
python -m pytest tests/ -q --basetemp=../tests/tmp/pytest-runs/diagnostics-performance-final
```

Frontend (из `frontend/`):

```powershell
node scripts/export-ui-keys.mjs
node --test
node node_modules/eslint/bin/eslint.js src
node node_modules/next/dist/bin/next build
```

Корень checkout:

```powershell
git diff --check
```

Linux дополнительно: fixture tests production collection/backup wrappers и Compose smoke обеих топологий. Полный doc-parser прогон обязателен, если затронута граница parser; при отсутствии изменений parser не добавлять его повторные тяжёлые прогоны без новой причины. В CI content checks — `git grep`, не предполагать установленный rg. Все новые tests должны попасть в действующие CI jobs; фактический CI run подтверждается только после разрешённой отправки ветки.

## 10. Порядок, оценка и границы готовности

Последовательность: **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12**. Backend/Node policy можно разрабатывать независимо только после общего corpus задачи 2, но данный план не требует параллельных агентов. Рекомендуемое исполнение — одним исполнителем по этапам: policy, recorder и snapshot разделяют много контрактов.

| Группа | Задачи | Ориентир |
|---|---|---|
| Измерения и контракт | 1–2 | 1–2 рабочих дня |
| Уровни, агрегаты, очереди | 3–4 | 2–3 дня |
| Backend/Node writer | 5–6 | 2–3 дня |
| Чтение, prepare worker, quota protocol | 7–8 | 3–5 дней |
| UI/compatibility и отрицательные сценарии | 9–10 | 2–3 дня |
| Нагрузка, регрессия и выпускная документация | 11–12 | 2–4 дня |

Итого ориентировочно 12–20 рабочих дней для знакомого с проектом разработчика при доступном тестовом Linux-стенде. Наибольшая неопределённость — межпроцессный учёт квот и Windows file leases. Это не обещание срока и не основание сокращать проверки.

Промежуточные результаты полезны отдельно: после задачи 6 — уровни и более дешёвая запись; после задачи 9 — полный пользовательский путь и новый builder. Ни один промежуточный результат не объявляется выполнением общего p95-критерия без задачи 11. Если запись уже укладывается в цель, но builder ещё нет, в отчёте это два отдельных статуса.

Вне объёма: переписывание logging приложения целиком, внешняя observability-платформа, запись содержимого, глобальный error tail buffer, автоматический replay операций, переиндексация Qdrant, настройка моделей LLM, изменение аудита ИБ, произвольные runtime-настройки из админки и постоянный detailed всех сервисов.

## Definition of Done

- [x] Состав всех уровней реализован и совпадает с manifest/UI/runbook.
- [x] Сокращение успешных событий доказано числом записей; ошибки/деградации и корреляция сохранены.
- [x] Producer не ждёт I/O/SQL через session lock; writer и чтение не держат общий lock на скан журнала.
- [x] Child prepare/build имеет единый с родителем учёт квот; реальные Linux stop/kill проверены, отказ измерения сохраняет резерв до безопасного восстановления.
- [x] v1/v2, старые строки/ZIP, mixed versions и offline имеют честное покрытие.
- [ ] Privacy, доступ, audit, TTL, concurrent download/delete и platform tests зелёные. Адресные наборы пройдены; final CT accelerated TTL остаётся required.
- [x] Standard и detailed оценены отдельно по неизменённым целям p95/RSS; ограничения названы явно. Полного прохождения RSS этим checkbox не утверждается.
- [ ] Полная регрессия/CI имеют фактический статус; known failures не скрыты.
- [x] Спецификация, SECURITY.md, runbook, отчёт и rollback drill обновлены.
- [x] Несвязанные изменения сохранены; без поручения не выполнены commit/push/production deploy.

## Самопроверка плана

При составлении плана проверены исходные ограничения, отличие scope от level, редкие ошибки при заполненной очереди, миграция legacy rows, состав v2 и offline policy, перенос решения через потоки, барьер принятых событий, batch/free-space/ledger, узкий snapshot без полного reserve spool, prefix append/mutation и права frontend. Все пять Review Focus привязаны к задачам с отрицательными тестами. Параметры новой политики были помечены как предлагаемые; прежние числа p95 не выдавались за свежий замер. Актуальный статус исполнения указан в начале документа и в отчёте; production-включение не выполнялось.


## Приёмка merged release завершена — 02.10.2026

Обновлённый кандидат (main38f7ed6 включён, runtime57727d9, harnessfdc327b)
прошёл отдельную пятиблочную HTTP-приёмку: 102 series, 324 018 measured requests,
все исходные gates PASSED. Максимальный ordinary p95 overhead 4,58%, ZIP 15,28%;
backend/front RSS delta <=23,56/3,18 МиБ. Полный backend2847passed24skipped,
frontend339passed1skipped; фактическая очистка завершена, собственные контейнеры
остановлены, production/frozen сохранены. Исторический FAILED остаётся прежним;
причина того отказа не установлена. Main merge/push/deployment не выполнялись.
Подробности и safe evidence:
[Приёмка объединённого release](../reports/2026-10-01-diagnostics-merged-release-acceptance.md).


### 02.10.2026 — слияние в main

По разрешению пользователя объединены актуальная maincde0724 и
integration64677c3, включая всю admin-diagnostics. Backend: 2851 passed,
24 skipped; frontend: 354 passed, 1 skipped; doc-parser: 400 passed, 1 skipped.
Ruff, ESLint (0 errors) и production Next build прошли.
[Отчёт слияния](../reports/2026-10-02-diagnostics-main-merge.md).
Предыдущие результаты нагрузки сохранены с исходными SHA и критериями;
новые замеры, push и deployment не выполнялись.
