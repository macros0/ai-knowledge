# Память Node на объединённой версии — 01.10.2026

## Решение

На merged baseline крупный избыток памяти, специфичный capture или ZIP,
не воспроизведён. Heap уменьшается после естественного GC во всех режимах.
Это поддерживает гипотезу о временных allocations и истории V8 heap/RSS;
причина прежних пиков +39,777/+67,223 МиБ на frozen v16/v11 не установлена.
Утечка всех видов и длительное удержание данных этим опытом не исключаются.
GC flags, forced GC, thresholds, privacy и runtime приложения не изменять.
Следующий этап — отдельная приёмка объединённого release со штатными
entrypoints, без memory observer/profiler и с исходными критериями.
Исходная пятиблочная performance acceptance остаётся **FAILED**.

## Фиксированная база и ограничения

Branch `codex/diagnostics-integration`, revision `2a4a0e0` перед экспериментом.
Backend image `17dbaf92358d`, frontend `ba0f7a01c178`, Next 16.3.8.
Один synthetic документ, BM25, concurrency 8; simulation auth.
Отдельный project `/opt/okf-diag-integration-20261001` в CT102,
frontend `127.0.0.1:18085`; backend numeric profiler и PG
`track_wal_io_timing=on` сохранялись одинаковыми во всех режимах.
Память контейнеров: backend 1 ГиБ, frontend 768 МиБ, PG/Qdrant по 384 МиБ.

Штатная frontend команда `node diagnostics-runner.mjs` сохранена.
Test-only observer подключён через `NODE_OPTIONS --import` и read-only mount
во всех восьми режимах. Inspector, forced GC, дополнительные GC flags,
heap/object dumps, env/argv contents не использовались. Наблюдатель вызывает
memory/V8 APIs и синхронно пишет числа: его влияние на измерения не равно нулю.
Linux sampler выполнялся вне frontend cgroup; RSS/PSS/private/anonymous
снимались отдельно по PID. RSS, PSS и heap вложены друг в друга и не суммируются.

Это два повтора четырёх режимов, по одному короткому idle окну на fresh process.
Нет долгого soak, production workload или полного контроля активности хоста.
ZIP содержал разные штатные объёмы событий: это сравнение режимов целиком,
а не алгоритма ZIP на идентичном input. Natural-GC callback измеряет
наблюдаемый heap после события, а не точный размер живого графа объектов.

## Единственный ограниченный эксперимент

Порядок: off/noZIP → standard/noZIP → off/ZIP → standard/ZIP,
затем обратный порядок. Каждый frontend создан заново из одного image;
backend/storage между режимами сохранялись.
Возраст Next worker: нагрузка начинается в 10 s, warmup длится до 40 s;
измерение >=60 s и >=1000 запросов; затем 60 s без пользовательского traffic.
Штатные Docker health checks могли продолжаться. Capture standard активен
через idle окно. ZIP начинается около 40 s, ready poll через 3 s.
Download/CRC/SHA/delete выполнялись после idle и вне memory window.
Capture остановлен после каждого standard режима. Deadline 30 min соблюдён.

**8/8 режимов, 39 280 измеренных запросов, exit 0, complete=true.**
Все response digests совпали внутри и между режимами; один launcher PID
и один Next PID на режим. Capture counters совпали с полным числом search
calls, включая warmup. Четыре ZIP готовы, каждый прошёл CRC и manifest SHA,
проверен download, затем DELETE. Ошибок observer и OOM не было.

Пики ниже относятся к нагрузке возрастом 10 s — конец измерения, включая
warmup; idle — медиана последних 5 s минуты простоя. МиБ = 2^20 bytes.

| Case | Capture / ZIP | Requests | Peak RSS дерева, МиБ | Next heap peak, МиБ | Idle heap, МиБ | После idle major GC, МиБ |
|---|---|---:|---:|---:|---:|---:|
| 1 | off / нет | 5040 | 158.211 | 46.277 | 31.136 | 30.870 |
| 2 | standard / нет | 4792 | 158.062 | 46.588 | 31.320 | 30.942 |
| 3 | off / ZIP | 5072 | 157.816 | 46.589 | 31.005 | 30.717 |
| 4 | standard / ZIP | 4840 | 157.559 | 47.231 | 31.608 | 30.869 |
| 5 | standard / ZIP | 4760 | 158.617 | 46.620 | 31.551 | 30.957 |
| 6 | off / ZIP | 4920 | 157.500 | 46.591 | 31.058 | 30.632 |
| 7 | standard / нет | 4872 | 157.574 | 46.512 | 31.490 | 30.893 |
| 8 | off / нет | 4984 | 159.129 | 46.580 | 31.066 | 30.642 |

Пики RSS дерева 157,500–159,129 МиБ; разница каждого treatment с baseline
своей половины от −1,629 до −0,149 МиБ. Отрицательная разница не является
доказательством экономии памяти. Peak heapTotal 56,211–57,461 МиБ,
после idle 33,211–34,711 МиБ; наблюдаемый heapUsed после последнего idle
major GC 30,632–30,957 МиБ. В idle было 2–3 естественных major GC во всех
режимах. Next RSS снизился от пиков 108,574–110,871 до 83,977–85,121 МиБ.
Peak external 3,562–3,583 МиБ, detached contexts=0, native contexts max=3.

Есть небольшой повторяемый избыток idle heapUsed standard над off:
без ZIP +0,184/+0,424 МиБ, с ZIP +0,472/+0,485 МиБ. Это не десятки МиБ
из прежних отказов; ограниченные буферы capture также могут давать такую
разницу. Нельзя из этих чисел объявить отсутствие любого retained allocation.

ZIP off: 24 790/25 193 bytes, 220/221 events;
standard: 37 549/37 410 bytes, 338/339 events.
HTTP p95 этого инструментированного опыта 96,503–104,276 ms, существенно
выше 17,758–19,269 ms предыдущей latency localization. Причина разницы
не измерена; её нельзя целиком приписать observer. Эти p95 не используются
как новая приёмка и не сравниваются с frozen release для отмены его отказов.

## Setup failure сохранён

Первая попытка получила ConnectionResetError при login: numeric handshake
появился за 178 ms до Next HTTP Ready. Измеренных режимов было **0**;
finally остановил project. Failed JSON и SHA исходного harness сохранены.
V2 добавил HTTP /health ready gate перед login в пределах worker age<8 s;
условия нагрузки, наблюдателя и GC не менялись. Единственный полный
измерительный прогон — `memory-matched-20261001-v2`.

## Проверки harness и доказательства

Privacy RED/GREEN: arbitrary process metadata и неизвестные heap fields
не сериализуются, нечисловые/невалидные значения отклоняются. Node observer
2 tests passed; runtime smoke в том же image: 9 numeric rows,
5 natural GC events, exit 0. Analysis RED/GREEN: неполные режимы, короткое
окно и неизвестное numeric поле отклоняются; отсутствие natural GC
остаётся unknown. Python 3 tests passed, Ruff новых Python файлов passed.
Полный frontend: **322 passed, 1 skipped**; ESLint **0 errors, 41 warnings**.
Полный backend и production build повторно не запускались: добавлены только
test harness, его проверки и отчёты; ограничения предыдущего full-suite
запуска остаются в integration report.

Все safe JSON сохранены в
`tests/artifacts/diagnostics/node-memory-integration-20261001/`.
Transfer tar SHA `e94e49f1082136828bf2d843858f6abdc2e48b6f376ad314e80459cdcf45b7a2`;
SHA всех шести переданных payload JSON сверены. Строгий анализ завершён,
исходный JSON сохранён. Manifest содержит точные SHA JSON и Git attribute
`-text` сохраняет их bytes между Windows/Linux checkout.

Два harness SHA manifest сохраняют точные bytes обеих измерительных версий;
`normalized-harness-sha256.json` отдельно фиксирует тот же source с LF для
проверки Git blob после штатной нормализации CRLF. Это разные представления
переводов строк, не разные реализации. Env, spool JSONL, ZIP, raw logs,
документы/БД и содержимое ответов не экспортировались.

## Конечное состояние

Собственный Compose project остановлен; backend/PG exit 0,
frontend/Qdrant exit 143 при SIGTERM, OOMKilled=false у всех.
Frontend пересоздан без запуска с исходной командой/image: status=created,
NODE_OPTIONS/OKF_TEST_NUMERIC_MEMORY_DIR и observer mounts отсутствуют.
Baseline и storage проекта сохранены. Production и frozen v16/v11
containers остаются running с прежними image IDs и started_at.

Merge в main и push не выполнялись. Main остаётся `9cb7748`.
Объединённая ветка сохраняется как фиксированная база. Крупный RSS-пик
в новом опыте не воспроизведён, поэтому искать его причину по одному
счётчику RSS или настраивать GC сейчас недостаточно обоснованно.
Перед merge/deployment нужна отдельная performance приёмка merged release;
этот диагностический опыт не переводит её в passed.
