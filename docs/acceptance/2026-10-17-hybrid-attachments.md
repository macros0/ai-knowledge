# Приёмка гибридной обработки вложений — 17.10.2026

Текущий статус рабочего main обновлён: [перенос, миграция и включение](2026-10-17-hybrid-attachments-rollout.md). Ниже сохранена исходная приёмка отдельного worktree до внедрения.

Реализовано в отдельном worktree `hybrid-attachments`, база кода `ac25da7e06dde1eec2c62972a343ec74b413ed14`. Коммитов, push, развёртывания, миграции рабочей БД и перегенерации рабочего корпуса не было. Feature flag по умолчанию выключен. Это отчёт локальной реализации; приёмка на рабочей среде остаётся отдельным этапом.

## Результат

Лёгкие вложения обрабатываются автоматически. Для тяжёлых, неизвестных и суммарно тяжёлых деревьев сохраняется выбор и возвращается `202 / awaiting_attachments`, без запуска генерации. Основной документ включён всегда; письмо сохраняет полезное тело при выключенном Excel. Обход включает DOCX/OLE, EML/MSG, PDF, ZIP/7z/RAR и их сочетания. Автоматически определить, устарел ли файл по смыслу, система не пытается: актуальность выбирает пользователь.

Выбор привязан к физическому вхождению и SHA исходника, сохраняется в review с revision CAS и затем в неизменяемом snapshot поколения. Изменение порогов или выключение флага не меняет сохранённый состав. Исключение проверяется до подробного parsing и similarity, перед generation/indexing и при publication. SQL и поиск переключаются на новый состав только после публикации. Действующие исключения и ожидающий выбор видны отдельно в данных документа и UI.

## Среда и зависимости

- Windows: Python 3.12.10, Node 26.3.0; имеющийся 7-Zip 25.01 не изменялся.
- Архивные библиотеки: py7zr 1.1.4, rarfile 4.5. ZIP — стандартная библиотека Python.
- Собранный Linux amd64 runtime: официальный 7-Zip 26.04, `/opt/7zip/7zz`; исходный дистрибутив x64 SHA-256 `fc0327ba27e89bd086cf426dff17d77de582953cdbbc10a6576540a06853ffcd`.
- Для arm64 закреплён SHA-256 `5b0ac3aa91c1e3499f011d6af69d7f6b4de3ef2f011d9e2ff44c58ebd04aa388`; исполнение arm64 и macOS не проверялось.
- Образ `okf-backend:hybrid-attachments-20261017`: image ID `sha256:05781e36229f4d027496b907da3ea178357cd7a98a81dd8e559e356994345190`. Это локальный проверочный образ, не опубликованный релиз.
- В build закреплены версия/хеш helper, лицензии и upstream notices; SBOM содержит 7-Zip, py7zr, rarfile. Runtime не скачивает инструменты.
- Для полного Linux-тестирования создан отдельный одноразовый образ с dev-зависимостью reportlab 5.0.1 и шрифтами; они не добавлены в рабочий backend image.

## Проверки и результаты

| Проверка | Результат |
|---|---|
| Полный doc-parser на Windows | 498 passed, 1 прежний skip |
| Реальный parse → SQL → publication → dense/BM25/hybrid, три формата | 16 passed вместе с maintenance/storage набором |
| Последние admission/CAS/upload/maintenance регрессии | 55 passed |
| Атомарная смена scope, failure/cancel и export | 14 passed |
| Миграция/контракты/schema drift SQLite | 28 passed |
| Полный backend Windows, до последних исправлений | 4246 passed, 8 failed, 45 skipped; исходный failed результат сохранён |
| Повтор всех затронутых полным прогоном наборов после исправлений | 54 passed, 7 PostgreSQL skips |
| Frontend node suite | 506 passed, 1 прежний skip из 507 |
| ESLint | 0 errors, 36 существующих warnings |
| Ruff по проектной конфигурации | passed |
| `node scripts/check-project.mjs`, включая npm audit | passed |
| Next production build | passed с `--webpack` |
| Финальный полный backend Linux | 4268 passed /47 skipped +20 passed последовательно; итог4288 passed,0 failed |
| Финальный полный parser Linux | 499 passed,0 skipped |

В полном Windows-прогоне пять UI-dictionary failures возникли из-за обновления словарей во время запущенного процесса; после генерации обоих manifest набор прошёл. Ещё два failures выявили отсутствующий новый флаг в fixture очереди, один — попадание новых scope-колонок в старый mail-only schema checker. Fixture обновлён, mail helper явно исключает чужую миграцию; тест подтверждает, что helper не добавляет scope-колонки. Исходный провал не переименован в успешный полный прогон.

Новые mail fixtures явно включают `mail_import_enabled`, чтобы результат не зависел от локального `.env`. Попытка параллельного полного Linux-прогона обнаружила случайный UUID в параметризации существующего `test_diagnostics_context.py`; этот файл запускается отдельно последовательно, остальной набор — с четырьмя независимыми процессами.

Ручной UI: собранный frontend на отдельном порту16317 и isolated backend18117 с синтетической SQLite, без LLM/index calls. Проверены RU/EN, сохранение выбора/закрытие/повторное открытие, Tab/Escape и возврат фокуса, 360px (`innerWidth=scrollWidth=360`, dialog312px). Пакетная загрузка тяжёлого и лёгкого DOCX дала ожидание выбора для первого и успешный приём следующего. Reader может читать и получает403 на изменение по API. Дополнительно проверен browser login в simulation: viewer видит отключённые галочки без save/start, editor — активные выбор/save/start. Рабочая SSO-среда не квалифицирована этим отчётом.

## Ресурсные ворота

[Машиночитаемые результаты](2026-10-17-hybrid-attachments-metrics.json). По10 baseline/after проходов после прогрева. Добавленное p95 preflight: без вложений0.799s, 1 лёгкое0.895s, 5 лёгких0.775s; после последнего import fix 20 лёгких0.625s. Порог <=1s не изменялся.

Первый замер4.5–5.2s провалился. Import profile показал загрузку LiteLLM через error formatter в preflight; импорт перенесён в вызываемую функцию. После этого20-файловый случай оставался красным1.022s. Второй профиль показал eager grpc/httpx; перенос этих импортов дал0.625s. Первичные failed отчёты сохранены отдельно от новых результатов. Более ранние зелёные0/1/5 измерения после последнего import-only изменения не повторялись.

Фактический исходник пользователя идентифицирован read-only, без отмены текущей генерации. Размер2150773bytes, найдено3 Excel: тяжёлый выключается; два маленьких остаются включены. У тяжёлого нижняя оценка122строки/9444ячейки/524341text bytes; 2 лёгких имеют9строк и423/387ячеек. Inventory complete, прочитано13726689bytes, worker1.625s. Отдельный end-to-end resource probe:1.812s, peak tree RSS162291712bytes при лимите1073741824, оставшихся worker/helper PID нет. SHA исходника до/после совпал. Имена и содержимое заказчика в отчёт не включены.

Пороги сохранены исходными; это стартовые параметры, подтверждённые синтетическими границами и данным примером, а не статистическая калибровка всего корпуса.

## Матрица A01–A24

Пути тестов указаны относительно корня; сценарии используют synthetic content и stub LLM/embeddings, изолированную SQL/Qdrant. Ни одна строка не означает внедрение в рабочий корпус.

| ID | Локальное доказательство |
|---|---|
| A01 | `test_processing_scope_api::test_light_upload_keeps_automatic_flow`; inventory root/no-attachment; legacy parser suite |
| A02 | `test_heavy_upload_is_durable_and_does_not_start_pipeline`; policy light sibling; worker не допущен |
| A03 | `test_generation_processing_scope`; `test_processing_scope_downloads` original200, foreignkey404 |
| A04 | `test_processing_scope_end_to_end` ZIP/7z/RAR: SQL chunks/concepts/table cells и итоговые search blocks |
| A05 | `test_processing_policy::test_manually_selected_heavy_file_can_be_parsed`; новый scope с включённым Excel в maintenance |
| A06 | `test_attachment_assessment::test_aggregate_is_deterministic_and_stops_strictly_below_threshold` |
| A07 | `test_attachment_inventory::test_empty_formatted_tail_not_counted`, streaming stop; boundary cells/text |
| A08 | source-key physical occurrence; selected-child ancestor validation; policy depth/ancestor fixtures |
| A09 | scan budget unknown; `test_reassessment_preserves_explicit_selection_and_keeps_new_children_off`; eligible prefill |
| A10 | expired/fresh-restart review lease tests; browser save/close/reopen; нет самозапуска |
| A11 | stale revision409; real confirm replay one durable generation; late-create не стирает draft |
| A12 | scoped similarity token/hash/content tests; existing upload_similarity; final admission serialization |
| A13 | queue failure preserves review; submit failure rollback; startup lease recovery; queue admission durable before submit |
| A14 | `test_new_scope_switches_only_at_publication_and_cancel_preserves_old`; existing publication/cancel races |
| A15 | scope inheritance on resume, flagoff regenerate, pending bulk skip and locked public-start guard |
| A16 | canonical reindex policy validation; unadapted repair/backfills explicit skip before reparse |
| A17 | SQLite upgrade/downgrade/schema tests; ephemeral PostgreSQL17 062→063→062 с synthetic legacy row; flagoff snapshot reuse |
| A18 | RU/EN +360px +keyboard/persistence/batch browser; reader/editor browser +403 API; batch summaries1/100 docs fixed queries; SSO browser отдельно |
| A19 | pending original download isolation; export `processing-scope.json`; hard delete removes review; obsolete reviews cleanup |
| A20 | Фактический исходник: read-only report и resource measurements выше, без нового generation/index writes |
| A21 | `test_docx_msg_archive_excel_obeys_same_selection` и mail archive policy для ZIP/7z/RAR |
| A22 | `test_archive_mail_archive_excel_uses_one_policy_tree`; `test_depth_six_is_allowed_seven_is_visible_and_excluded` |
| A23 | real compressed RAR3/RAR5/solid/crc fixtures; ZIP/7z generated fixtures; password/volume/codec failures explicit |
| A24 | archive_safety path/link/payload quotas; parser supervisor descendant success/timeout/crash cleanup Windows/Linux |

## Границы внедрения

[Runbook](../ARCHIVE_ATTACHMENTS.md) обязателен перед интеграцией. Рабочая БД read-only имеет Alembic marker `f0a1b2c3d4e5`; он не равен фактической схеме create_all. Нельзя запускать upgrade/stamp вслепую. Проверка PostgreSQL17 выполнена на отдельной тестовой БД, не на копии рабочего корпуса. Миграция реальной копии и целевая UI/SSO-приёмка ещё не выполнены.

Canonical repair и старые reparse backfills для scoped документа возвращают явный skip. Для смены parsing используется полноценная перегенерация с сохранённым/новым выбором. Root архив как самостоятельный upload, encrypted/multivolume/SFX, semantic version replacement и автоматическое определение устаревших файлов не добавлены. Нельзя откатывать на несовместимый старый binary и затем перегенерировать scoped документы: это может вернуть исключённый контент.


## Дополнительные исправления перед завершением

Повторный независимый review закрыл: CID на Excel как обход inline-image policy; orphan leases после свежего рестарта; snapshot на resume; атомарную подготовку originals до видимости новой загрузки; late review/create и start/regenerate races; reservation similarity выбранного состава до release admission lock. Reservation хранит только подпись и selection hash, действует только для submitted review, не меняет active signatures и игнорируется после draft/cancel/publication. На этапе перед worker проверяются review ID/revision/request ID/selection/source SHA под document writer lock.

Полный Linux-прогон на Docker overlay был медленным: init_db2.789s против0.0274s в RAM. Повтор использует те же тесты на tmpfs; ограничения продукта и критерии приёмки не менялись. В тестовый образ добавлен git для существующих subprocess Git fixtures; рабочий backend image не расширялся этой тестовой зависимостью.

[Хеши исходных файлов реализации](2026-10-17-hybrid-attachments-source.json) фиксируются отдельно от Git base: незакоммиченный результат нельзя обозначать только SHA базового checkout.

Основные тестовые файлы: [assessment](../../doc-parser/tests/test_attachment_assessment.py), [inventory](../../doc-parser/tests/test_attachment_inventory.py), [policy/nested mail](../../doc-parser/tests/test_processing_policy.py), [archive safety](../../doc-parser/tests/test_archive_safety.py), [API](../../backend/tests/test_processing_scope_api.py), [admission](../../backend/tests/test_processing_scope_admission.py), [E2E search](../../backend/tests/test_processing_scope_end_to_end.py), [publication/maintenance](../../backend/tests/test_processing_scope_maintenance.py), [migration](../../backend/tests/test_processing_scope_migration.py), [summary queries](../../backend/tests/test_processing_scope_summary.py), [supervisor](../../backend/tests/test_parser_supervisor.py).

Manifest SHA-256 итогового набора исходников: `2e48855d055654f025fce57533f832948a8ab2e90ae90effab39698fb916ba59` (105 файлов).


## Итоговый полный прогон

Финальная проверка завершена с exit0: весь backend набор —4288 passed,47 skipped,0 failed. `tests/` кроме одного файла выполнены с `-n4`; `test_diagnostics_context.py` — последовательно из-за случайного UUID в collection IDs. Полный parser499 passed; frontend506 passed/1 старый skip. Последние production webpack build и `node scripts/check-project.mjs` (включая npm audit/Ruff/node tests) завершились exit0.

В последнем Linux backend run Python3.12.15. Временные SQLite располагались на tmpfs, контейнеры работали под UID1000 без сети, без uploads/рабочей БД/Qdrant mounts. Для двух metadata probes передана только `.git` read-only: `rev-parse HEAD` вернул реальный базовый `ac25da7...`; исходники незакоммиченной реализации отдельно закреплены manifest SHA выше. Предыдущий image-only run4266 passed/2 failed был сохранён: оба failures были `git rev-parse` без репозитория, после корректного read-only контекста весь набор прошёл.

Behavioral RED для similarity reservation воспроизведён в одноразовом контейнере отключением только lookup в памяти: второй confirm ошибочно вернул accepted вместо similar. С исходной реализацией этот тест входит в зелёный полный набор. Исходники worktree для RED не менялись.

Причины47 skips в финальном backend наборе:

- 5: GLOSSARY_TEST_POSTGRES_URL is not configured
- 11: Helm required; Kubernetes CI installs it explicitly
- 7: MAIL_MIGRATION_POSTGRES_ADMIN_URL is not configured
- 1: NTFS junctions are Windows-specific
- 2: Private PostgreSQL connection not configured
- 1: Private PostgreSQL test connection not configured
- 5: Requires a separately authorized private PostgreSQL test endpoint
- 1: Windows RSS monitor is tested on Windows
- 1: Windows hard memory gate
- 3: Windows job descendant cleanup
- 1: Windows kill-on-close after supervisor death
- 2: Windows protection setup failures
- 5: isolated FULL_STORAGE_TEST_ROOT is required for real ENOSPC
- 1: macOS RSS monitor
- 1: macOS RSS monitor setup failure

## Дополнение: предпросмотр XLSX перед выбором

Реализован отдельный read-only preview сохранённого оригинала review: переключение
листов, до 30 непустых строк, столбцы A–T, до 500 символов ячейки. Чтение ограничено
по объёму XML и штатным parser supervisor; внешние ссылки и формулы не исполняются.
Миграция БД и новые зависимости не потребовались.

Проверено:

- Сначала получены ожидаемые red-тесты отсутствующего API/parser/UI, затем green.
- 8 тестов предпросмотра: ограничения, shared strings и форматированный хвост,
  неактуальный review, неизвестный лист/источник, отсутствие авторизации,
  неизменность review/очереди и XLSX внутри вложенного письма и ZIP/7z/RAR.
- Регрессия backend: 64 passed, 7 skipped (`test_attachment_preview`,
  `test_attachment_preflight_supervisor`, `test_processing_scope_api`,
  `test_processing_scope_downloads`, `test_parser_supervisor`, `test_audit`).
- Полный frontend: 509 passed, 1 skipped. Production webpack build, целевые ESLint
  и Ruff завершились успешно. Независимое ревью: замечание о shared-string budget
  воспроизведено тестом, исправлено и повторно проверено.
- В действующем интерфейсе с обычной SSO-сессией открыт отключённый тяжёлый XLSX
  рабочего документа, показаны 10 листов и проверено переключение листа.
  После просмотра review остался draft/revision1, прежняя active generation
  сохранена, candidate отсутствует, документ done с прежними 2909 чанками.
  Генерация и сохранение нового выбора не запускались.

Полный backend-набор в этом дополнении не принят: исходный запуск упёрся в
7 PermissionError при обходе старых `backend/tests/tmp/table-projection-*`;
повтор с `--ignore=tests/tmp` остановлен после
`TestUploadAuditHook::test_upload_records_document_upload`. Причина этого сбоя —
унаследованный локальный `ATTACHMENT_REVIEW_ENABLED=true` в тестовых Settings:
тест не создаёт uploads перед включённым preflight. В регрессионном запуске
явно задан `ATTACHMENT_REVIEW_ENABLED=false` для обычных тестов; preview/scope
тесты включают функцию самостоятельно. `test_audit` в этом запуске прошёл.
Рабочий runtime-флаг остаётся включённым. Полный backend-набор зелёным не заявляется.

Изменения остаются в local main без коммита и пуша.

## Исправления после ревью: T11–T15

По команде пользователя «Продолжай» доработки выполнены в том же незакоммиченном
`main` поверх `ec26700`. Рабочая схема остаётся `063b1c2d3e4f`; дополнительных
миграций нет. Запрет commit/push сохранён.

Исправлены потеря несохранённых галочек при expand и при PUT-конфликте/network error
перед Confirm, подмена имени вложения именем root и автоматическое включение PPTX
без parser. Windows spawn больше не импортирует diagnostics/providers до worker admission.
Предпросмотр объединяет быстрые запросы листов, повторное открытие присоединяется к
незавершённому запросу; 409 предлагает обновление review с сохранением локальных решений.
Добавлено постоянное RU/EN объяснение сырых значений Excel.

Автоматические рекомендации сохранены отдельно в `inventory.recommendations`,
`recommended_excluded_count` не меняется из-за ручных галочек. Для legacy значение
неизвестно, прежние snapshots не пересчитываются. Уже извлечённые excluded originals
копируются в writable candidate с проверкой SHA/путей/бюджета; после публикации и cleanup
review они доступны из generation, а затем повторно используются при новой генерации.
Никогда не извлекавшиеся потомки остаются `container_only`.

Независимый read-only review подтвердил один дополнительный P2: submit catch делал
reload после неуспешного Save. Он воспроизведён для 409 и сетевой ошибки, исправлен;
обе регрессии прошли. Других конкретных блокирующих замечаний reviewer не установил.
Отдельная регрессия закрыла относительный `DATA_DIR` в сохранении originals.

### Проверки и границы результата

| Проверка | Результат |
|---|---|
| Windows bootstrap/diagnostics/parser/preview | 43 passed, 7 platform skips |
| Windows API/download после T11 | 12 passed |
| Windows lifecycle/API: DELETE, поколения, hash и active guard | 21 passed; затем финальная generation/E2E регрессия 9 passed |
| Windows полный parser | 499 passed, 1 skip |
| Полный frontend после финальных reader/retry регрессий | 523 passed, 1 прежний skip |
| Production webpack build | PASS на финальном продуктовом коде |
| ESLint | Полный запуск: 0 errors, 36 прежних warnings; изменённые компоненты и тесты: 0 errors/warnings |
| Ruff и diff whitespace | PASS |
| Полный backend Linux, source snapshot | 4314 passed, 47 skipped, 1 failure из-за free-space reserve тестового tmpfs; первичный неуспешный запуск сохранён |
| Backend Linux, финальный delta и fresh-process проверка | 64 passed, включая relative DATA_DIR, scoped publication/search, embeddings/settings/privacy и повтор debug-теста |
| Расширенный Linux targeted | 109 passed, 10 platform skips; запущен до применения delta, не заменяет fresh-process результат64 |

Полный Windows backend зелёным не заявляется: последовательный запуск прерван,
две группы остановлены из-за длительности FS-записей. Завершившаяся группа:
1431 passed, 18 skipped, 2 failures. Причина двух failures воспроизведена отдельно:
LiteLLM dotenv загрузил `EMBEDDING_OLLAMA_NUM_BATCH=8192` рабочего окружения в unit
Settings. Autouse fixture теперь изолирует `EMBEDDING_*` вместе с auth/LLM, не меняя
рабочую `.env`; весь соответствующий модуль затем прошёл: 16 passed на Windows.
Первоначальные collection errors шардов из-за order-dependent импортов helper сохранены;
путь `backend/test_scripts` проверен отдельной collection88, тесты не исключались ради PASS.

Linux использовал существующий `okf-scope-full-tests:20261017`, image ID
`0bf4bac7f92397a625918b5f2ef6ffd040993494ddd20c15f145507668f9ac55`, Python3.12.15,
UID1000, `--network none`, 4CPU/4GiB memory, tmpfs3GiB. Подключались source-only snapshot
и `.git` read-only; рабочие uploads, PostgreSQL, Qdrant и `.env` не подключались.
Snapshot1774 files закреплён manifest SHA-256
`84144f23bcd07ee816aaaa288bf0c7a2e0a92ac8601c53587e4cfd05ad2b316c`.

Единственный Linux failure — `TestParseJson.test_debug_file_is_created_on_failure`:
в tmpfs осталось около360MiB, `DiagnosticStore` вернул
`StorageQuotaError("Diagnostic free-space reserve reached")`. Повтор подтвердил ту же
причину, затем тест успешно выполнен на отдельном `/tmp` с достаточным свободным местом.
Ни защитный порог, ни поведение diagnostics не менялись. Это не единый зелёный full-run:
причина и успешная повторная проверка указаны отдельно.

После исходного snapshot изменились три файла: `processing_scope_originals.py`
(нормализация root), `tests/conftest.py` (embedding env isolation),
`tests/test_generation_processing_scope.py` (relative-path regression). Они переданы
отдельным delta, SHA-256 сверены внутри контейнера; fresh-process набор64 прошёл уже
на этих файлах. Логи/manifest/delta хранятся в игнорируемом `tests/tmp/attachment-review-*`.
Временный контейнер `okf-attachment-review-20261017` удалён после проверок; существующий
test image сохранён.

### Скорость и реальный UI

Замеры выполнены через работающий `app.diagnostic_entrypoint` и существующую SSO Admin
сессию. Для каждой HTTP-группы взяты20 запросов после прогрева; времена — Next proxy GET
до завершения ответа, не прямой вызов reader. Параллельной генерации документов не было;
фоновый тестовый процесс на машине работал.

| Сценарий | HTTP p50 / p95 / max |
|---|---|
| Тяжёлый XLSX, лист0 | 598 / 659 / 671 ms |
| Тяжёлый XLSX, лист1 | 561 / 686 / 734 ms |
| Малый XLSX, лист0 | 573 / 663 / 702 ms |

Первое UI-открытие после restart1167ms. 42 смены листа: UI p95 789ms/max805ms;
20 открытий малого Excel: p95 1097ms/max1153ms. После финальной сборки и обновления
истёкшей SSO-сессии пользователем предпросмотр снова открылся за1246ms.
HTTP ошибок в исходной измерительной серии не было. Цели HTTP≤1,5s/UI≤2s выполнены
для этого примера; на произвольный XLSX этот результат не распространяется.

RU/EN warning проверен в реальном UI. При360px `scrollWidth === innerWidth === 360`,
таблица прокручивается внутри диалога. Tab остаётся в диалоге, закрытие возвращает фокус
на исходную кнопку preview. Быстрый выбор листов1→2→3 показал последний лист.
Reader без изменения состава и retry429/422 покрыты интерактивными component tests.
Финальный screenshot: `tests/tmp/attachment-review-preview-final.png`;
responsive screenshot: `tests/tmp/attachment-review-preview-360.png`.

### A25–A35

| ID | Evidence |
|---|---|
| A25 | `test_attachment_preview.py`: bounds, shared strings, budgets, nested mail и ZIP/7z/RAR, readonly/auth/error cases |
| A26 | `attachmentPreview.test.mjs`: coalescing, reopen, late response, new source/review, 409 refresh, 429/422 retry; live rapid sheet switch |
| A27 | bootstrap subprocess regression; Windows SSO HTTP/UI серии выше; supervisor/resource regressions |
| A28 | real component callbacks: preview toggle→expand, save409, confirm409/network, refresh stable keys |
| A29 | pending unknown→expand→download с неизменными key/parent/именем/расширением |
| A30 | EML→распознанный PPTX: unsupported и excluded, без обещания PPTX parser |
| A31 | raw-values notice RU/EN; 1900/1904 date/percent/code/boolean/formula fixtures; XSS и data-only-after-T |
| A32 | явные test flags/settings и tmp DB; source snapshot full-run + resource-reason повтор + final delta64; причины skips и ограничение Windows full выше |
| A33 | ZIP/7z/RAR publish→review replacement→cleanup→download→new generation; canonical contents/search без canary; hash mismatch/active-write guard |
| A34 | aggregate recommendation→Save: recommendation/count сохраняются; legacy неизвестен, selection hash не переписывается |
| A35 | DELETE draft/validating/submitted для нового и опубликованного документа; выбор/active/consent/revision; snapshot round-trip |

Пользовательский документ не перегенерировался: review revision1/selection hash,
active generation и2909 чанков не изменились. Общие рабочие счётчики прежние:
44 documents/44 generations/3192 chunks/13371 concepts/121 sources/24 tables/1785 rows;
все44 документа done. Новая selected publication остаётся отдельной проверкой после
подтверждения пользователем нужных вложений.

## Дополнение: скачок окна при смене листа

Пользователь сообщил о возврате к первому окну при выборе другого листа. В реальном
SSO UI подтверждено: preview не закрывается и фокус остаётся в select, но условный
рендер убирал таблицу на время загрузки. Карточка сжималась с816 до350px и смещалась
с y58 до y291; за ней становился виден dialog выбора состава.

В `AttachmentPreviewDialog` добавлена постоянная область содержимого с высотой50vh
и `aria-busy`. В ней отображается загрузка, ошибка или текущий лист; старые данные
под новым именем листа не показываются. Изменение ограничено preview и его CSS.

Новая интерактивная регрессия сначала упала на отсутствии постоянного viewport,
затем прошла. Focused preview/scope:16 passed; полный frontend:524 passed/1 прежний
skip; targeted ESLint:0 errors/warnings. После hot reload в работающем приложении
проверены листы1/2/0/9/7: до запроса, во время загрузки и после ответа высота785px,
y73.5 и положение select y299.5 неизменны, фокус остаётся в preview. На360×800:
высота720px/y40 неизменны, `scrollWidth=innerWidth=360`. Временный viewport сброшен.
Артефакты: `tests/tmp/attachment-preview-layout-live.json`,
`tests/tmp/attachment-preview-layout-final.png`,
`tests/tmp/attachment-preview-layout-full-frontend.log`.

Backend/БД и состав обработки не менялись; сохранение выбора и генерация не вызывались.
Production build после этого небольшого JSX/CSS изменения отдельно не запускалась;
проверены component tests, полный frontend suite и фактический dev UI.

## Продолжение: блокировка редактора и забытое окно (T25–T26)

Код находится в незакоммиченном локальном main. Рабочая БД по read-only проверке
остаётся на063; миграция064 не применена, новый backend не запущен.
Последний рабочий снимок:47 документов done; пользовательский документ —
2909 чанков/4483 концепта, active54f31288b0704a479f11b30e33d11952,
candidate отсутствует, review revision1 и selection hash сохранены.
Это более поздний снимок, чем исторические44 документа выше.

Реализованы: один владелец редактирования на документ, отдельный token для каждой
вкладки, transport lease180с/heartbeat60с, серверный idle lease600с. Через540с
бездействия UI предупреждает и показывает отсчёт60с, через600с закрывает оба окна
и освобождает аренду. Клик/ввод/прокрутка внутри состава и Excel считаются
активностью; polling/heartbeat/движение мыши — нет. Закрытие не сохраняет выбор,
не применяет изменения и не отменяет принятый job. Фоновая вкладка проверяет
абсолютный срок; сервер независимо отвергает просроченный token. Повторённый
activity_sequence не продлевает idle lease. Lease HTTP timeout15с.

Доказательства текущего прохода (изолированные fixtures):

- `scope-idle-broader-final.log`:60passed, API/lease/admission/schema/summary/
  legacy mapping/publication/table pipeline.
- `scope-final-ui.log`:542passed,1skip; `scope-final-lint.log`:0errors/warnings
  по изменённым компонентам/helpers/tests.
- `scope-final-build.log`:webpack production build прошёл (до последнего
  добавления строки причины несовместимости vector space; требуется финальный
  refresh после завершения fix-pass).
- `scope-postgres-idle.log`: на отдельной временной БД063→064 сохранены записи,
  проверены idle fields,10 параллельных пар acquire с одним победителем,
  expiration/stale release/queued fence; временная БД удалена. Рабочая БД не менялась.
- `scope-quality-green2.log`:37passed; новые таблицы получают coverage report,
  partial LLM не становится завершённым этапом, повтор не трогает completed sibling.
- `scope-integrity-green2.log`:31passed; final root SHA, original SHA, actor/audit,
  сохранение прежней публикации при изменении файла; canonical repair regressions.
- `scope-space-partial-final.log`:51passed; неизвестное/изменённое embedding space
  блокирует новые embeddings, legacy lineage выдерживает4exclude/restore без
  provider calls; partial опубликованной base не получает complete cache.

Read-only расчёт на оригиналах пользователя: fast_exclude, mapping verified,
142 активных/2767 архивных/2909 сохранённых чанков,1716 активных концептов.
Восстановление legacy locator использует неизменный root SHA, SHA сохранённого
зарегистрированного original и единственное вхождение с этим SHA под доказанным
parent. Неоднозначные повторы, отсутствие original или изменение bytes блокируют
путь; filename/ordinal не используются как доказательство. Сам apply не вызывался.

Общая приёмка T16–T24 ещё не завершена. Открыты: перенос совместимого inactive
cache при явной полной регенерации, обработка доказуемых внутренних relations,
bounded resource/performance A50 (10прогонов), окончательная whole-path review,
рабочая миграция/restart и UI/API применение выбранного состава. Incremental API
остаётся выключенным capability=false. Исторические полные прогоны не являются
доказательством этих новых gates. Commit/push не выполнялись.

Финальный fix-pass: независимый reviewer подтвердил оба исправления и получил
39passed; новых actionable замечаний в них нет. Повторные полный frontend и
webpack build после последней строки перевода прошли (`scope-final-ui2.log`,
`scope-final-build2.log`). ESLint финального набора:0errors,2 существующих
предупреждения export default в locale files, строка4; компоненты/helpers/tests
по отдельному прогону — без предупреждений.

Общий регрессионный проход `scope-final-regression.log`:223passed,1failure.
Единственное падение — strict snapshot экспорта без нового `vector_space`.
Ожидание обновлено с точной проверкой proven fingerprint; исходный failed log
сохранён. Изолированный повтор maintenance файла учитывается отдельно и не
делает первоначальный общий прогон зелёным задним числом.

Повторная read-only проверка рабочего runtime после изменений:063,
47done, новая064 schema отсутствует; пользовательские active/candidate,
2909/4483, review revision1/hash не изменились.

Повтор maintenance:14passed,1warning (`scope-final-maintenance-fix.log`,106с).
Проверяется точный exported proven fingerprint; никаких skips/послаблений
проверок не добавлено. Одного полного зелёного backend прогона после этого
test-only исправления не было. Таймер T26 реализован и проверен в коде;
рабочее включение общей функции ещё не выполнено.

## Рабочее включение и проверка — 18.10.2026

По отдельной команде пользователя, в локальном dirty main, без commit/push:

- Резервная копия: `data/backups/scope064-20261017T213404Z`, custom PostgreSQL dump16063816bytes, проверены SHA и pg_restore listing; сохранены исходник, зарегистрированные оригиналы, active bundle.
- Alembic063b1c2d3e4f→064b1c2d3e4f применён штатно. Проверены новые таблицы/колонки, все47done до миграции; после DDL точные corpus counts и все generation pointers совпали. Фактическая схема проверена, stamp/create_all reconciliation не использовались.
- Перезапущен только проверенный backend через стандартный hidden helper. Health/ready: БД/Qdrant/PDF ok; UI SSO-сессия действительна. Capability изменения состава включён, отдельный delta endpoint доступен редактору.
- На контрольном документе `211c5906782441cb` в UI выбран прежний draft: тяжёлый Excel выключен, два малых включены. Preview fast_exclude: исключить2767concepts/2767chunks, сохранить привязки. Нажата именно «Применить исключения без перегенерации».
- Published generation `f549a22686604df89e80cc33c2b96d1d`, candidateNone, все47done. Admission00:47:48.683758→publish00:48:17.552295 МСК:28.868537с. Финальный active142chunks/1716concepts/2tables, archive2767chunks, stored2909. Qdrant1858activepoints,0points исключённого source.
- До/после совпали SHA канонического текста, заголовков, slug/chunk_index/source_id, source_spans, generated_at/model_id/prompt_version оставшихся результатов и каждого dense/sparse вектора. Исходник не изменён, зарегистрированный original исключения доступен. Это fast_exclude, LLM/embeddings не вызываются его workset.
- Маршрут исключённого чанка возвращает404 `chunk_source_excluded`, без fallback в staging. Dense40hits/BM2530hits — только текущая generation и ни одного исключённого source. Реальный экспорт:142chunks/1716concepts, только active, без source-cache как контента. Индексы совпадают с сохранёнными. UI показывает «Исключено вложений:1из3» и active/archive/stored; screenshot `tests/tmp/scope-live-published.png`.

Новые проверки: relations exact-edge restore, запрет опасного полного rebuild, metadata при возврате cache, cell-order checksum широких таблиц, отсутствие JSON materialization при preflight hash, линейная проверка link/reparse, writer hash tamper/missing и snapshot под одним lock. Отдельные новые RED/GREEN и независимые fix-pass безP1/P2 записаны в ledger.

Финальный затронутый backend набор:193passed/1host-symlinkskip (`scope-rollout-final.log`,293с). Это выбранные scope/publication/canonical-repair/files tests, не весь backend. Frontend542passed/1skip, webpackPASS; исправленный targeted ESLint0errors/2existing locale export warnings. Ошибочный первый lint путь сохранён как failed invocation, не lint success.

**A50 не принят.** PG-копия реального размера,4диагностических переключения без providers: exclude preview3.47/3.70с, apply42.59/45.09с; reuse preview0.54/0.39с, apply125.74/125.76с, peak1690MiB. После первого переключения параллельно шли другие окончательные проверки: это не квалифицирующий10-run p95. Прежние failed замеры сохранены, пороги5с/30с не изменены. Дорогую10-run матрицу не повторяли без новой гипотезы. Производительность и потоковое ограничение памяти остаются отдельным незакрытым gate; локальная проверка включена по явной команде пользователя, полной production-приёмки не заявляем.

Совместимый inactive cache через явный полный rebuild не переносится автоматически: до reset/admission возвращается контролируемый отказ, чтобы сохранить результаты/IDs. Быстрое исключение, восстановление и delta поддерживаются. Повторное включение проверено на изолированной реальной копии4циклами и малым e2e для proven/legacy, без providers; рабочий пользовательский состав после успешного исключения не переключался обратно.

## Закрытие A50 — 18.10.2026

Предыдущие failed/diagnostic результаты выше сохранены. Новая квалифицирующая серия завершена exit0 без параллельных тестов: отдельные PostgreSQL/коллекция Qdrant и копии файлов контрольного документа; один прогрев каждого режима, затем10 exclude и10 reuse. Пороги не менялись. [Полные измерения, среда и SHA runtime-кода](2026-10-18-scope-performance-metrics.json).

| Метрика p95 | Исключение | Восстановление из cache |
|---|---:|---:|
| Preview | 4.149с | 0.681с |
| Admission→publish | 18.431с | 29.081с |
| Полный вызов, включая validation/cleanup | 22.400с | 29.749с |

Nearest-rank p95 при10 измерениях равен максимальному результату, outliers не исключались. **A50 PASS** для контрольного размера в штатном DB-only режиме. Cold reuse36.567с (включая8.558с первой dedup signature), cold exclude20.560с записаны отдельно;30с для первого нового состава не обещаются. Peak RSS953.195MiB против исторических1690MiB; после прогрева значения в конце операций679.8–835.8MiB, последний803.1MiB. Это измеренный размер, не гарантия постоянной памяти для любого документа.

Причины задержек: ненужные YAML/Markdown и их повторная проверка при `okf_write_bundles=false`;71.6млн повторных MinHash хэшей; row-by-row SQL restore, повторный экспорт/сканирование таблиц; локальный inference inspector SDK обходил готовые числовые векторы. Исправлены DB-only output, bounded signature cache8, пакетный/потоковый restore, единый portable export и один checksum/coverage scan таблиц с независимым покрытием concepts/chunks. Формат row checksum и векторы сохранены. Qdrant остаётся на прежнем локальном URL.

Измеренные SQL pages≤1000 для canonical ORM, derived restore≤256 подтверждён отдельной регрессией; измеренный `_upsert_batches` input≤256. Retrieve/scroll/payload пределы подтверждаются кодом и тестами, не этим upsert-счётчиком. Реальные Mock.call_count: parser0/LLM0/embedder0.

Qualification сравнивает первую и последнюю пары hashes content/selected provenance/numeric vectors. Независимый2turn correctness прогон того же SHA приложения дополнительно сравнил **исходный BASE до первой операции**: сохранённые chunks content/hash/count/title/source/table_refs; concepts content/title/type/slug/chunk/source/generated_at/model/prompt/spans/table_refs/tags; source tree identity и SHA originals; dense/sparse values и source/locale/title/tags/dev_tags payload. Generation IDs в table_refs нормализованы. Исходные retained edges/порядок сохранены; дополнительные edges допускаются только к восстановленным slug. Полные служебные metadata всех source-полей этим fingerprint не сравниваются. Этот correctness прогон шёл параллельно с регрессиями/перезапуском и не входит в latency статистику (cold reuse41.71с).

Свежие проверки: `scope-performance-final-tests.log`236passed/1hostskip; после последних fixes `scope-last-green.log`43passed, `scope-parser-final.log`500passed/1skip, `scope-sdk-final.log`62passed. Независимый read-only review новых оптимизаций: безP1/P2; отдельный protocol review выявил baseline-пробел, закрытый2turn проверкой выше. Финальные SHA Python runtime совпали. `scope-performance-qualification.log` — frozen harness SHA7cfb2adfe81b37a2f40407373c7ab9eee40ba52565705817ccb3699beb8e75de. Первое падение SQL batch instrumentation считало flattened params вместо VALUES tuples; failed log сохранён, в product code ради него изменений не было.

Backend перезапущен штатным hidden helper: wrapper40372/server23220; health/ready ok, frontend200. Рабочая схема остаётся064;47done, activef549a22686604df89e80cc33c2b96d1d/candidateNone,142chunks/1716concepts/1858points и0excluded points. Повторно прошли SHA originals/retained data/vectors, actual chunk handler404, dense40/BM2530 active-only, экспорт142/1716. Рабочий состав не переключался. Все временные scope_accept базы и коллекции удалены (проверено0/0). Дополнительная миграция не требовалась; commit/push не выполнялись этим изменением.

Уточнение интерфейса по замечанию пользователя: `scope.edit` открывает draft и разблокирует галочки, не применяет состав. Надпись изменена с «Изменить состав обработки» на «Редактировать вложения» (EN `Edit attachments`), активность входа в редактор сохранена.51 frontend i18n/scope tests passed; после этой правки только копии полный build не повторялся.

Следующее уточнение пользователя: после входа действия тоже были всегда активны. Исправлены условия Save (draft diff), review/apply (published diff и не noop), bulk all/none (только непустое изменение); возврат к опубликованному выбору блокирует применение. Bulk сбрасывает устаревший preview/request/warning. Сохранённый изменённый draft применим без повторного изменения галочек; initial processing и явная full regeneration не зависят от diff. Старые lease тесты теперь действительно меняют выбор перед Save; их исходные failures сохранены. Независимый review нашёл неверное использование pending eligibility для published baseline: новый RED воспроизвёл known-active→unknown-pending, baseline перенесён на active inventory; review fix подтверждён без новых замечаний.

Свежий полный frontend `scope-action-state-full2.log`:547passed/1existing skip. В настоящем открытом редакторе без изменения галочек подтверждены disabled «Сохранить выбор» и disabled «Состав не изменился», preview и выход доступны. Скриншот `tests/tmp/scope-actions-unchanged-live.png`. Эти UI fixes не меняют Python runtime SHA квалификации A50 и не переключают публикацию; открытый пользователем draft не сохранялся и не применялся агентом. Production build после этого UI fix отдельно не повторялся; проверены полный frontend suite, ESLint и фактический dev UI.

Последнее уточнение: «Закрыть» вместо «Решить позже» и «Сбросить черновик» вместо «Отменить изменение состава». Reset скрыт и guarded при отсутствии отличий; Close сохраняет только ранее persisted draft без API mutation, Reset вызывает только cancel-review, active неизменен. Соответствующая callback-регрессия и полный frontend `scope-actions-final-frontend2.log`:548passed/1existing skip. `scope-actions-final-lint.log`:0errors/2existing locale export warnings. Full regeneration явно подписана полной; при unchanged убраны ошибочные подсказки о будущей публикации. Независимый review этих добавок: безP1/P2. Пользовательский draftrevision2 существует с прежним selection hash, действующее поколение/counts не изменились.

Финальный dev UI после штатного истечения старой HMR-аренды (без снятия lock вручную): «Закрыть», disabled «Сохранить выбор», «Полная перегенерация документа», disabled «Состав не изменился»; «Сбросить черновик» отсутствует. Скриншот обновлён. Галочки рабочего документа, save/cancel/apply/fullregen агентом не вызывались.

Следующее уточнение пользователя — дублирующий badge/selection entry — исправлено единым компонентом под названием документа. При excluded/pending он показывает только бейдж, без этих состояний — обычную кнопку в том же месте; doc-actions экземпляр удалён. Viewer/upload entry сохранены. Новые7 component scenarios проверяют варианты excluded/pending/оба, обычный/legacy fallback, standalone entry и сохранение открытого dialog при смене кнопки на badge. RED на отсутствии fallback→GREEN; `scope-single-place-frontend.log`:555passed/1existing skip. `scope-single-place-lint2.log`:0errors/1existing unnecessary t dependency warning DocumentList:315; первый lint из корня не нашёл frontend config и сохранён как failed invocation. В рабочем списке у контрольного документа остался только один интерактивный бейдж; у остальных доступны обычные входы. Backend/схема/выбор документа не менялись, commit/push не выполнялись.

### Просмотр, таймаут и поздние ответы — 18.10.2026

Первый вход по единственному бейджу теперь показывает опубликованный состав read-only; сохранённый draft открывается явным «Редактировать вложения». Новый draft не создаётся при просмотре; Reset возвращает в просмотр. Отдельная кнопка обновления убрана. После API timeout (code timeout либо HTTP408/504 без другого кода) или потери lease окно возвращается в просмотр. Перед повторным Edit читается фактическое серверное состояние; submitted/validating не открывается как draft. Автоматического повтора save/apply/confirm нет. Обычные ошибки валидации/сети без timeout сохраняют локальные галочки; внутренняя актуализация Excel-preview сохраняется.

Независимый reviewer воспроизвёл P2: поздний save после потери heartbeat продолжал confirm. Исправлено инвалидацией сессии и проверкой после каждого await; idle autoclose тоже инвалидирует сессию. Три deferred-save регрессии (confirm/preview/expand) сначала упали, затем прошли; повторный независимый review без замечаний. Первоначальный RED тест lease-loss оставлял таймер после assertion; прогон остановлен, добавлен teardown и повторный ограниченный RED сохранил воспроизведение, без изменения критериев.

Итог: `scope-view-timeout-fence-final.log` —567passed/1existing skip; `scope-view-timeout-fence-final-lint.log` —0errors/2existing locale export warnings. Focused33passed. Полная production build после этих UI изменений отдельно не запускалась. `git diff --check` без ошибок whitespace, index пуст. В рабочем dev UI подтверждены published read-only галочки, Close/Edit и отсутствие Save/Reload при первом открытии. Снимок `tests/tmp/scope-view-first-live.png`. Таймаут и потеря lease проверялись на управляемых component fixtures, без искусственного отказа рабочего backend.

Read-only probe рабочего документа: прежний active f549/candidateNone/142chunks1716concepts, все47done; draftrevision2 и selection hash не изменились. В рабочем UI не выполнялись save/reset/apply/regenerate и не менялись галочки. Миграция064 уже применена; новых backend/DB изменений для этого уточнения нет. Коммит и push не выполнялись.

### Проверки перед выборочным коммитом — 18.10.2026

По отдельной команде пользователя подготовлен commit scope из165файлов функции; пять изменённых файлов Kubernetes и один новый Kubernetes report исключены. Локальные originals/backups/logs/tests/tmp не включены. Повторно frontend567passed/1skip, parser500passed/1skip; Python runtime SHA совпадают с квалификацией A50.

Расширенный backend precommit:229passed/8skipped/2failed (`scope-precommit-backend.log`). Один failure выявил зависимость subprocess import-теста от cwd: child теперь явно запускается из backend. Второй выявил пропуск display_order в exclusions старого mail-only schema repair: поля064 не должны становиться конфликтом либо применяться этим helper. Добавлено исключение и проверка отсутствия display_order после mail repair. Рабочая БД не менялась. Повтор двух затронутых файлов:8passed/7PostgreSQL skips (`scope-precommit-fixes.log`); полный расширенный набор после этих узких исправлений отдельно не повторялся. Исходный failed evidence сохранён. Исправлены только замечания whitespace перед staging; staged set и содержимое каждого blob сверены с явным manifest, cached diff check пройден.

### Устранение противоречий legacy-просмотра после6202af4 — 18.10.2026

Пользовательские снимки показали первый просмотр с inventory=null: одновременно утверждался известный опубликованный состав, выводилось0вложений и обещалась обработка, хотя pending был draft. Read-only проверка целевого документа:done, published scope отсутствует, draft содержит3узла, candidateNone,247концептов. Причины: пустой fallback nodes использовался для счётчика неизвестной описи, header проверял только active, а сообщение об обработке — publishedDirty, которое для legacy намеренно консервативно true.

UI теперь различает неизвестный и известный пустой inventory: отдельное объяснение отсутствия опубликованной описи, без нулевых счётчиков; draft открывается только после Edit. Сообщение о завершении обработки выводится только по реальным pending validating/submitted. Шесть новых callback-сценариев: legacy view→edit, known-empty и четыре состояния pending; исходный RED сохранён в scope-legacy-view-red.log, GREEN39focused. Полный scope-legacy-view-full.log:573passed/1existing skip; lint0errors/2existinglocale export warnings. Production build отдельно не повторялась, backend/БД не изменялись. Реальный первый просмотр нужного документа подтверждён AX: корректное legacy-объяснение, сохранённый выбор через Edit, без ложных0/сообщения о пересчёте. Галочки/save/reset/apply не вызывались; screenshot tests/tmp/scope-legacy-view-live.png. Новое исправление не закоммичено и не отправлено.
Финальная подпись явно называет сохранённый выбор черновиком, отдельно от действующей версии. Повторный полный scope-legacy-view-final.log:573passed/1existing skip, final lint0errors/2existinglocale warnings. Независимый read-only review: безP1/P2,39focused passed. Итоговый screenshot scope-legacy-view-live.png снят после повторного открытия по UI; после HMR старая аренда временно корректно ограничивает окно просмотром, без обхода/снятия lock. Первое окно показывает legacy-объяснение и наличие сохранённого draft; нулевой счётчик и обещание начавшегося пересчёта отсутствуют. Новый коммит не создавался.

### Предпросмотр8форматов и реальные форматы/заголовки — 18.10.2026

Пользователь расширил XLSX-preview на все поддерживаемые вложения и попросил реальный формат/заголовок, отдельно уточнив Excel. Добавлен отдельный attachment_preview dispatch в существующий supervisor: XLSX прежняя таблица; DOCX bounded main XML/text; PDF первые3страницы text-only; EML/MSG headers/body текущего письма без child payloads; ZIP/7z/RAR первые100validated metadata entries без extraction. Shared slots/time10s/memory/input50MB/no-store/hash/review/auth сохранены. Заголовки: OOXML/PDF Title, mail Subject, оформленный DOCX heading; Excel meaningful sheet name с подписью «Лист», defaultSheet1/Лист1 игнорируется. Original display_name/bytes/source identities не переименовываются. Новые inventory fields optional; metadata reads учитываются scan budget. В старом draft GET ничего не разбирает: уточнённый preview title остаётся только в локальном UI с привязкой review/source/fingerprint.

Подтверждено, что в одном дереве одинаковое служебное имя OLE принадлежит разным фактическим форматам MSG и PDF. После explicit MSG-preview дерево показывает настоящую тему вместе с MSG и исходным именем; PDF-preview показывает текст и ограничение страниц. На другом рабочем документе Excel получил содержательное имя листа с явной пометкой, прежний sheet selector остался. UI не менял галочки и не выполнял save/reset/apply/regenerate. Локальные screenshots: preview-all-pdf-live.png, preview-all-msg-live.png, preview-all-formats-tree-live.png, preview-all-excel-title-live.png. Содержимое файлов/темы/адреса не копируются в этот отчёт.

Тесты: preview-all-delivery-frontend.log584passed/1existing skip; preview-all-parser-final.log511passed/1existing skip; preview-all-api.log25passed (7новых real-worker format scenarios, прежние XLSX/nested archives/readonly/auth/error и bootstrap/import boundary). RED на отсутствии generic dispatch/controls/title сохранён; один fake PDF test дополнен существующим dummy input после штатной проверки размера. Первоначальный запуск API тестов указал неверный относительный python path из backend и не стартовал; исправленный запуск действительно прошёл. Ruff по новым/затронутым scope файлам проходит; в PDF provider остаются3прежних BLE001, число/диагностики сверены с HEAD, новых нет. ESLint0errors/2existinglocale export warnings. После последнего review добавлены escaping и title/fingerprint/review UI regressions; независимый reviewer не нашёлP1/P2. Production build и Docker/macOS generic acceptance не запускались.

Все47 документов done перед restart; перезапущен только локальный backend штатным hidden helper, health/ready database/Qdrant/PDF ok. DB migration не требуется, источник/публикация/выбор не менялись. Предыдущая A50 квалификация сохранена как исторический результат; новая квалификация производительности после preview/title изменений не выполнялась и прежние SHA не выдаются за текущие. Коммит и push для новых изменений не выполнялись.

### Исходные подписи на первом экране и упрощение preview — 18.10

Подтверждён источник имён: внутренние ZIP members DOCX имеют служебные названия, но EMF-значки того же Word object содержат исходную подпись в текстовых записях. Добавлен форматно-независимый bounded reader без OCR/исполнения EMF. Новый inventory хранит optional display_label; старые active/pending описи получают подписи отдельным read-only GET через supervisor, включая сохранённые вложенные DOCX. Metadata enrichment не пишет manifests/review/SQL selection, не меняет source_key/locator/fingerprint и не запускает генерацию. Caption имеет приоритет на первом экране, техническое имя остаётся вторичным; длинные имена переносятся полностью. В самом preview по уточнению пользователя удалены заголовок файла/листа и строка technical filename/format; sheet selector и содержимое сохранены.

Проверки: labels-parser-full.log —521passed/1existing skip; последующие labels-final.log —17passed, включая11caption tests (8форматов, malformed/path/external и9МиБ Word XML). Реальный XML рабочего документа около11МиБ; общий лимит reader32МиБ. labels-api-full.log —25passed с настоящими worker/bootstrap; labels-api-active.log —1passed после добавления active/pending/stale-generation/tampered-fingerprint assertions. labels-preview-final-frontend.log —590passed/1existing skip, включая first-screen/no-preview, caption priority, fingerprint, late published response и отсутствие duplicate preview header. ESLint affected UI/API0errors, Ruff новых/затронутых scope files PASS, git diff --check PASS. Полный production build/новая A50 квалификация не выполнялись.

Рабочий backend перезапущен штатным helper; health/ready database/Qdrant/PDF ok. Read-only service probe рабочего active поколения получил три подписи за1,343с (одиночный замер, не p95). В отдельной фоновой вкладке проверены реальные имена до открытия preview, затем отсутствие duplicate header и сохранённый selector/таблица. Скриншоты в ignored tests/tmp: attachment-labels-first-screen.png, attachment-preview-without-name.png. Не нажимались Edit/Save/Reset/Apply/Regenerate; тестовая вкладка закрыта через штатный Close. До/после совпадают active_generation_id, candidate=null,142активных чанка/1716концептов, review revision2/state draft/selection_hash; миграция остаётся064. Дополнительный commit/push не выполнялись.

### Сохранённое исключение, skipped_depth и задержка подписей — 18.10

На другом рабочем legacy документе пользователь исключил вложенный Word. Подтверждено: revision2/draft хранит исключение, в истории только preview без применения. Прежние проверки blocked/incomplete_provenance не означали потери происхождения generated content: у всех12chunks/182concepts корректные source_id, отсутствуют mixed refs. Отказ вызывал один зарегистрированный original skipped_depth под исключаемым Word, без locator/source_key и собственных chunks/concepts/tables.

Исправление допускает только такие пустые depth markers в проверенной цепочке родителей. Они наследуют исключение родителя, а при его возврате снимается только inherited user_excluded; оригинал и skipped_depth остаются. Payload не разбирается, mapping/cache completion не выдумываются, предел глубины не повышается. Отрицательные guards на собственные результаты/неизвестный status сохраняются. Read-only анализ рабочего документа теперь fast_exclude:93concepts/3chunks исключаются из поиска,89concepts/9chunks остаются, workset пустой. Изменения в рабочий корпус не применялись.

Первый экран показывает отдельный перечень сохранённых неприменённых изменений, не подменяя legacy published inventory. GET возвращает только mode/reasons последней проверки для текущих review/revision/selection_hash/generation/analysis_version. Версия анализа2 исключает показ старого blocked как актуального результата после исправления. UI screenshot: ignored tests/tmp/scope-saved-exclusion-first-screen.png; окно открывалось только для просмотра и закрыто без Save/Apply/Regenerate.

Задержка подписей имела два implementation causes: полное повторное bounded чтение Word XML в новом worker и отмена/дублирование запроса при обновлении неизменного DTO после editor lease. Добавлен process-local metadata-only LRU32хэша/4МиБ после прежних path/hash guards и стабильные зависимости UI по scope/source/fingerprint. Preview content не кэшируется. Read-only сервисные замеры на двух реальных документах: первый1,2418с/0,7695с, последующие0,0085–0,0126с, worker_calls0 на повторных чтениях. Это время сервиса, без browser/proxy/SSO; p95/A50 квалификацией не является.

Проверки: scope-status-final-all.log73backend passed (API/planner/candidate/jobs/SQL+Qdrant-memory cycles); labels-cache-green.log4passed после новых cache asserts и запрета parser calls на depth-original при многократном exclude/restore; scope-labels-performance-frontend.log593passed/1existing skip. RED зафиксированы на отсутствии last_check, неотображённом черновике, ложном incomplete_provenance, inherited metadata после restore, двух одинаковых title requests и повторном worker/cache absence. Первый depth fixture ошибочно разместил original вне поколения — исправлен fixture, защитный guard не ослаблялся. Ruff affected services/API PASS, ESLint0errors/2existing locale warnings, diff check PASS.

Backend обновлён штатным helper, health/ready database/Qdrant/PDF ok. До обновления все47documents done; review/selection/active pointer проверяемого документа не менялись, candidate отсутствует. Миграция064 не менялась. На момент этой проверки отдельного env-параметра attachment depth ещё не было; последующая унификация описана ниже. Без дополнительного commit/push. Production build, Docker/macOS и новая A50 квалификация не выполнялись.

### Единый предел вложенности — 18.10

Исходное решение найдено в §6.1 commit6202af4: legacy допускает1–2, snapshot1–6 при одинаковом root0. По явному ответу пользователя установлен общий default6. Добавлен runtime `ATTACHMENT_MAX_DEPTH` (0–32), capture в inventory/snapshot и единая граница; delta больше не содержит независимого6. MIME32 остаётся отдельным техническим ограничением.

RED: unified-depth-red.log —6 failures на отсутствии настройки/прежнем отсечении третьего уровня; depth-archive-red.log —3 failures на попытке extraction за границей. GREEN: depth-parser-full.log —553passed/1existing skip; реальные DOCX/XLSX/PDF/EML/MSG границы2/6, mixed mail, граница inventory до чтения и общий archive contract ZIP/7z/RAR. Обычный parser сохраняет bounded Office/PDF original на границе, как прежде; inventory не читает дочерний payload.

Backend depth-backend-regression.log —148passed/7skipped и один устаревший assertion suffix parser_version, исправленный на полный текущий ParseContext identity. Финальный depth-backend-final.log —11passed: исправленный supervisor test, env propagation в реальный изолированный worker, invalid config, frozen save/refresh, delta boundaries2/3,6/6,8/7 и import boundary. В первом новом worker fixture был задан512MiB; он превысил этот тестовый бюджет. Fixture использует штатный settings.parser_max_memory_mb=1024; рабочие лимиты и квалификационные gates не повышались. При подготовке дополнительных fixtures исправлены имя переменной и mock pipe.close; product guards не ослаблялись.

Frontend depth-frontend-full.log —594passed/1existing skip; исправлен drift манифестов нового ключа depthLimit. Ruff изменённых runtime/test файлов PASS, ESLint затронутого dialog/helper PASS, diff check PASS. Полная backend-suite, production build, Docker/macOS и ресурсная A50 квалификация в этой итерации не запускались.

Локальная `.env`: ATTACHMENT_MAX_DEPTH=6; backend перезапущен helper (PID46724), /health/ready database/Qdrant/PDF ok. До/после все47documents done, проверенные active generation, review revision2/selection_hash,142chunks/1716concepts неизменны, candidate отсутствует. Автоматической регенерации и миграции нет; схема остаётся064. Без commit/push.

### Проверка окна выбора и видимости отсечённых узлов — 18.10

Проверены UI → API → inventory и повторный expand: абсолютная глубина и captured max_depth учитываются, API отклоняет сохранение/раскрытие за границей до запуска parser. Пользователь уточнил: достаточно оставить видимым сам отсечённый контейнер с причиной, без чтения его внутренних файлов. В строке показаны глобальная настройка глубины и её значение для состава; checkbox недоступен, кнопки повторной оценки нет. Общее вычисление доступного выбора также учитывает глубину, чтобы «Выбрать все» не включало узлы без корректного маркера skipped_depth.

Найдены и исправлены две дополнительные ошибки. Реальный EML с1005вложениями терял технический MIME marker, inventory.complete оставался true; аналогично терялись MIME depth/malformed markers. Теперь такие узлы видны как unknown/неполная оценка, без payload и автоматического запуска генерации. MIME depth имеет собственный статус mime_depth_exceeded и не объясняется настройкой файловой глубины. Вложенный EML при исчерпании общего inventory max_nodes теперь сохраняет skipped_count вместо общего assessment_incomplete.

RED: scope-mime-limit-red.log —3 failures с ложным complete=true; scope-bulk-depth-red.log —ошибка активности «Выбрать все» при max_depth0 и узле без marker. GREEN: scope-inventory-audit-final.log —556passed/1existing skip; scope-choice-limit-frontend.log —595passed/1existing skip; scope-choice-api-final.log —22passed, включая root/nested EML с1005вложениями; scope-depth-audit-setting.log —9passed. Отдельные depth API2passed и parser/UI16/56passed перед исправлением. Ruff и ESLint затронутых файлов PASS, diff check PASS. Полный backend/production build/новая ресурсная квалификация не запускались. Корпус и выбор пользователя не перегенерировались; commit/push не выполнялись.

### Поддерживаемые вложения в счётчиках — 18.10

На рабочем документе summary.total32 включал4поддерживаемых и28неподдерживаемых объектов. excluded2 уже не включал неподдерживаемые. Теперь total/included/excluded/recommended_excluded_count строятся по supported!=false, unsupported остаётся отдельной диагностикой; counts_version2 отличает новую семантику. Старые cached summaries нормализуются по компактным counters без чтения inventory или записи в БД. Список документов сохраняет3SQL-запроса для1/100документов и не выбирает processing_scope_json/inventory_json. UI корректно читает обе версии, не вычитает unsupported повторно и считает только поддерживаемые объекты в статистике окна; сами неподдерживаемые строки не скрываются.

RED: supported-count-red.log —3failures; supported-count-ui-red.log —3failures. GREEN: supported-count-green.log —46passed, включая scope summary/review/API/end-to-end; supported-count-frontend.log —597passed/1existing skip. Ruff/ESLint затронутых файлов и diff check PASS. Read-only DocumentRegistry рабочего документа возвращает total4/included2/excluded2/unsupported28, availability available; generation6d5cb1e8090340d8ad3ab5435c265f08 и selection_hash380fc…d40ed сохраняются, candidate отсутствует. Нет изменения snapshot, генерации, БД-схемы или выбора. Без commit/push; production build не запускался.

### Проверка перед коммитом — 18.10

Обязательный node scripts/check-project.mjs: первый запуск не получил доступ к npm audit; повтор с сетевым доступом подтвердил0vulnerabilities. Gate затем выявил19ошибок Ruff в связанном scope-коде/тестах: удалены неиспользуемые импорты и присваивания, imports pytest-fixtures заменены на module alias + fixture assignment без изменения вызываемых операций. Финальный attachments-commit-project-check-final.log: npm audit0, ESLint0errors/36warnings, frontend597passed/1skip, backend Ruff PASS. Дополнительный attachments-commit-fixtures.log —31passed после изменения fixture imports; attachments-commit-parser.log —556passed/1skip.

Отдельный Ruff-прогон изменённых parser-файлов имеет3старых BLE001 в pypdf_pdfium_provider.py (extract_text, extract_images, image extension reader). Проверка git show HEAD той же команды подтвердила все3до этой правки; новое document_title использует перечисленные исключения. Эти существующие обработчики исключений не менялись и общий parser lint не объявляется зелёным. Полные backend CI/build и ресурсная квалификация не подменяются локальным gate.

Расширенный attachments-commit-backend.log завершён:245passed/7skipped за373,61с. Это все processing_scope suites и связанные preview/depth/generation/parser supervisor/import/checkpoint проверки; дополнительный31-test прогон выше подтверждает обновлённые fixture imports и rollout guards. Корпус рабочего приложения эти проверки не изменяют.

В индекс отобраны76файлов этой задачи. Шесть несвязанных dirty/untracked файлов Kubernetes исключены из индекса и проверяются по SHA-256 до/после коммита. Локальная .env, рабочие документы, тестовые logs/screenshots и data не входят в коммит. Пуш отдельно не разрешался.
