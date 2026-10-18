# Приёмка совместного редактирования справочников

Актуальный результат: объединено с `ac6b40f` и перенесено в локальный `main`; тестовая БД мигрирована до065 и аренды включены. Пользователь разрешил локальный коммит; push не выполняется. Итоговый протокол и оставшийся ручной этап — в конце отчёта. Предыдущие разделы сохранены как история контрольных точек.

## Границы результата

Реализованы общие ревизии, аренды основных редакторов, защита внутренних писателей/автоперевода, bulk review/cleanup, preview/confirm стоп-слов и UI-словарей, каталоги атрибутов/языки, аудит, диагностические счётчики, инвалидация поколений при смене режима и сравнение черновиков.

Пользователь 18 октября отложил задачу 5А до отдельного коммита T25/T26. Во время приёмки появился `6202af4`; условие выполнено, коммит интегрирован трёхсторонним переносом без нового коммита, staged/unstaged основной копии не копировались. Общие policy/timer/controller/notice и token-only адаптер состава подключены. Новая 065 следует за зафиксированной 064; Alembic head один. Повторные браузерные сценарии общего idle/Excel/two-tabs завершены; рабочая БД не менялась.

## Проверки

| Проверка | Результат и предел доказательства |
|---|---|
| `node scripts/check-project.mjs` | Пройден после интеграции и исправлений (`check-project-unified-r2.log`), npm audit явно разрешён. Аудит: 0 уязвимостей; ESLint: 0 ошибок, предупреждения остаются; Ruff: passed. На checkpoint 621 frontend passed, 1 skipped |
| Финальные frontend-тесты | 622 passed, 1 skipped, включая дополнительную регрессию queued input во время source save. Манифест заморожен: 1568 ключей; ESLint нового теста passed |
| Frontend webpack build | Финальная оптимизированная сборка после всех UI-изменений прошла, `next build --webpack` |
| Default Turbopack build | Не прошёл: изолированный checkout использует внешний `node_modules` junction. Это ограничение среды не объявлено успешной проверкой default build; build defaults не менялись |
| Полный релевантный backend, первый прогон | 791 passed, 19 skipped, 7 failed. Пять ошибок — старые ожидания counters/отсутствующие preconditions в origin-тестах; две — незаполненные audit action labels/список. Исправлены явно, без отключения CAS или ослабления тестов |
| Полный backend до интеграции T25/T26 | 807 passed, 22 skipped, 8 warnings; финальный замороженный прогон `backend-frozen-r3.log` |
| Расширенный backend после интеграции | 874 passed, 22 skipped, 14 warnings за 830,77 с; 85 файлов в manifest прогона. 22 skips — отдельные opt-in PostgreSQL gates; новые scope/write PG-файлы проверяются отдельно |
| Исключённый промежуточный backend-прогон | 802 passed, 22 skipped, 5 failed. Во время процесса был регенерирован манифест с тремя новыми backfill-ключами, а импортированный seed сохранил старый список. Пять ошибок unknown key сохранены в логе; итоговый повтор использует замороженные исходники и манифест |
| Регрессии review | Отдельно пройдены 51 development/catalog/операционных, 39 glossary/audit/операционных, 21 origin/translation проверки. Числа пересекаются и не суммируются |
| PostgreSQL CT102, финальный backend | После интеграции: 25 passed за 61,72 с (reference migration/concurrency + 8 scope contracts). Одноразовый контейнер, отдельная `test_reference_*` БД, read-only исходники, нет .env/корпуса/реальной модели. Включены два OS-процесса за document lease, late release, пять applying состояний и accepted-request replay после expiry/release |
| Дополнительные PostgreSQL writers | 5 existing glossary PG tests passed (включая другой OS-процесс); 6 common writer contracts passed: merge/occupied backfill/lease during LLM/human during LLM, exact revoke + acquire-many rollback, revision/epoch after release. Отдельные файлы: `test_glossary_postgres_concurrency.py`, `test_reference_writers_postgres.py` |
| Нагрузка | 50 одновременно действующих аренд разных ресурсов; 10 независимых процессов за одну запись — 1 владелец/9 отказов; 100 get/save/release без утечки аренд и потерянных изменений |
| Сравнение чтения и записи | Baseline зафиксирован: `6202af4`. ASGI `/api/tags`, 50 тегов, по 200 чтений после прогрева, base/candidate/candidate/base: p95 baseline 3,579/3,242 мс, candidate 3,255/3,218 мс, отношение 0,949; прежний порог ≤1,10 пройден. PATCH перевода тега: по 100 записей после прогрева, p50/p95 baseline 6,806/7,255 и 6,738/7,433 мс; candidate 5,976/6,556 и 6,108/6,675 мс. Baseline — прежний writer, candidate — mandatory CAS с отключённым lease; просмотренная ревизия читается вне измерения записи. Это один путь без HTTP/TLS, не общий SLO и не доказательство универсального ускорения. Оба measurement tests passed |
| SSO API | Реальные demo.editor/demo.admin/demo.user, три независимых cookie jar. Пройдены права, конкуренция, save, revoke точного поколения, отказ старого save, безопасность late release, 428 старого клиента, admin-only summary. После интеграции отдельно пройден scope: reader читает, другой editor/admin не acquires/save, saved draft и idle deadline не изменены претендентами |
| Браузер | Глоссарий, разработки, перевод тега: две вкладки, занятость, owner save/release, повторное получение и сравнение свежих данных. В development dialog Tab остаётся внутри. При 360 px `scrollWidth=innerWidth=360` |
| Реальный idle browser | Пройден. Начало 01:32:26 UTC; в 01:42 UTC наблюдались предупреждение и countdown, затем lost, disabled inputs/Save, неизменный текст «Черновик должен сохраниться после десяти минут бездействия», доступные reacquire/copy. Скриншот сохранён. Первую попытку исключили из-за конфликта общей localhost-cookie с рабочим UI. Тестовый профиль использует отдельное имя cookie и дополнительную строгую CSRF-проверку, без изменения product source |
| Миграция на тестовой PostgreSQL | Пустая и наполненная схема: content/history snapshot до/после одинаков, новые revision=1/epoch=0; duplicate report до DDL; безопасный downgrade и отказ downgrade при использованном состоянии. Малый набор — 8 начальных ресурсов, не объём целевой БД |
| Смена режима | CLI preview/apply проверены на отдельной SQLite SSO-приёмки при остановленном тестовом backend: 15 состояний, epochs инвалидированы, content revisions сохранены. Рабочая БД не использовалась |

Точные raw-логи находятся в локальном игнорируемом журнале `.superpowers/sdd/2026-10-17-reference-editing/`. Пароли, исходные токены аренды и значения .env не включаются в этот отчёт. Сценарии реального времени не заменяются fake-clock тестами без явного указания.

## Матрица критериев

«Авто» означает воспроизводимый тест, а не ручную целевую приёмку. Все пути ниже относительно корня проекта.

| AC | Доказательство | Статус |
|---|---|---|
| 01 | `backend/tests/test_reference_edit_lock_api.py`, `test_reference_postgres_concurrency.py`; SSO editor/admin | Авто + SSO |
| 02 | `test_two_tabs_and_late_requests_cannot_touch_new_owner`; browser glossary/development/tag | Авто + UI |
| 03 | PostgreSQL 50 разных открытых редакторов | Авто PG |
| 04 | `backend/tests/test_reference_edit_lock.py`, граница транспортного срока | Авто с часами |
| 05 | `test_heartbeat_and_duplicate_acquire_do_not_keep_forgotten_editor_alive`; реальный browser idle 600 секунд | Авто + UI real-time |
| 06 | `test_new_activity_extends_idle_once_and_token_never_leaks`; `frontend/test/editorIdle.test.mjs` | Авто |
| 07 | `test_reference_edit_lock.py`; медленное fresh snapshot не продлевает deadline в `referenceEditSession.test.mjs` | Авто |
| 08 | Late-release/renew unit + реальный SSO late release | Авто + SSO |
| 09 | API revoke и реальный SSO old save →409 | Авто + SSO |
| 10 | `referenceEditSession.test.mjs`, write rejection/access classifier; черновик остаётся при lost | Авто; UI выборочно |
| 11 | `developmentConflictFlow.test.mjs`, `referenceEditing.test.mjs`; браузерная передача аренды с новым именем | Авто + UI |
| 12 | `test_reference_translation_concurrency.py`, `tagTranslationEditor.test.mjs`; SSO translation | Авто + SSO |
| 13 | Human/review during LLM, manual unreviewed protection в `test_reference_translation_concurrency.py`, `test_glossary_translations.py` | Авто |
| 14 | Source/delete mutations между capture/commit в translation tests | Авто |
| 15 | `test_reference_bulk_concurrency.py`; две локали одной карточки и смешанный batch | Авто |
| 16 | PG cleanup versus restore/assign; immutable preview tests | Авто PG |
| 17 | Acquire-many rollback и borrowed source epoch в `test_reference_edit_lock.py`; `referenceMergeLease.test.mjs` | Авто |
| 18 | Glossary preview/revision/receipt suites + `referenceMergeLease.test.mjs` и `glossaryEditSession.test.mjs`/`glossaryConflicts.test.mjs` | Авто |
| 19 | `test_reference_sets_concurrency.py`, PG stopword add между preview/confirm; `referenceSetEditors.test.mjs` | Авто PG/UI component |
| 20 | Dictionary import/rollback, A→B→A, inactive import, explicit-null snapshot tests; competing PG imports | Авто PG |
| 21 | PG module remove versus development create; scoped/global guard | Авто PG |
| 22 | PG concurrent NULL-org add и partial unique/duplicate report | Авто PG |
| 23 | Raw API protocol tests, SSO old client →428; flag-off сохраняет CAS | Авто + SSO |
| 24 | Role checks на каждом renew/write, `test_reference_edit_lock_api.py`; write-rejection controller | Авто; три роли SSO |
| 25 | Exact epoch revoke unit/API + SSO wrong epoch →409 | Авто + SSO |
| 26 | `glossaryEditSession.test.mjs`, draft reconciliation и dirty-card retention; modal focus/360 px | Авто + UI выборочно |
| 27 | `test_reference_editing_migration.py`, `test_reference_postgres_migration.py`; CLI transition на отдельной SQLite | Авто PG/SQLite; target не выполнялся |
| 28 | Независимые процессы с файловым барьером, `reference_pg_worker.py` | Авто PG |
| 29 | Coherent single-statement tag/development reads, protected glossary/set/catalog reads; `test_reference_operations.py` | Авто + review |
| 30 | Audit/transaction rollback tests, development create failure, glossary merge receipt replay | Авто |
| 31 | `test_edit_lease_policy.py`, `editorLeaseAdapters.test.mjs`: одинаковые deadlines/heartbeat без активности, token-only контекст, skew серверных/клиентских часов | Авто + PG |
| 32 | `processingScopeFlow.test.mjs`: idle закрывает оба окна без apply/cancel; `glossaryEditSession.test.mjs`/real-time glossary сохраняют текст; общий browser протокол ниже | Авто + UI real-time |
| 33 | `test_reference_scope_postgres.py`: пять applying состояний и replay после expiry/release; component applying при heartbeat/save | Авто PG |
| 34 | Scope component: активность Excel, поздние responses, role/reacquire fencing трёх продолжений; чистый background timer; UI две вкладки и Excel | Авто + UI выборочно |
| 35 | Reference epoch/API tests и token-only scope tests; исходные document guards/post-parse проверены расширенным backend-набором | Авто + review |

## Независимый review

Один свежий рецензент проверил незакоммиченный diff и untracked-файлы. Подтверждены и исправлены шесть P2: переключение языка во время translation save; многосоставные locale codes; перезапись ручного непроверенного перевода; несогласованный localized development snapshot; ревизия union каталога при scoped изменении; неатомарный audit создания разработки. Для каждого есть регрессия. Повторное чтение исправлений не выявило незакрытых P1/P2. BackfillControl и последующая защита StrictMode от двойного acquire также проверены тем же рецензентом.

При review 5А исправлены server clamp (transport timeout должен классифицировать idle expiry), серверные часы, stale Notice после write failure, applying после heartbeat и fencing при замене lease effect. Повторные 38 scope component tests пройдены; рецензент не нашёл оставшихся P1/P2. В коммите зависимости устранены 26 Ruff diagnostics: неиспользуемые импорты/локальные переменные и явная регистрация импортированных pytest fixtures.

## Статусы выпуска

- [x] Реализация справочников в отдельной ветке без commit/push.
- [x] Тестовая миграция PostgreSQL/SQLite и функциональный rollback.
- [x] Разрешённый npm audit/check-project.
- [x] Независимый review и исправление замечаний.
- [x] Окончание общего повторного backend-прогона и real-time browser протокола.
- [x] Повторные автоматические/SSO/двухвкладочные idle/Excel сценарии задачи 5А; ручные профили/screen reader вынесены отдельно по решению пользователя.
- [x] Интеграция зафиксированных 063/064 и единый head 065; без применения к рабочей БД.
- [ ] Миграционная репетиция на копии фактического целевого объёма и резервная копия.
- [ ] Целевая миграция, целевая приёмка и выпуск — отдельно разрешаемые действия.

Временный callback `localhost:16302` удалён после тестов; helper подтвердил `verified=true`, `callback_present=false`, отсутствие других изменённых полей. Ошибочно выбранный в начале отдельный CT102 Keycloak также восстановлен. Созданные вкладки закрыты, только тестовые frontend/backend остановлены. Одноразовые PostgreSQL-контейнеры удалены автоматически, БД `test_reference_20261018_r1`, `test_reference_20261018_unified`, `test_glossary_20261018_reference` удалены; отсутствие имён и test-контейнеров проверено. Порты18110/16302 освобождены, рабочие18000/16300/18081 сохранились. Тестовые исходники/артефакты в отдельном `/tmp/reference-editing-20261018` оставлены для воспроизводимости; секретов и корпуса в них нет.

Решение пользователя от 18 октября: реальный screen reader и два независимых UI-профиля editor/admin оставить отдельным этапом ручной приёмки. Смена роли/сети покрыта API/controller fixtures, отдельный реальный отказ сети не создавался. Закрытая вкладка и естественное освобождение scope lease по TTL проверены в браузере. Полный согласованный rollout и объём целевой БД остаются будущими этапами; baseline записи измерен. Эти ограничения не скрываются общими числами passed и остаются в плане. Никакое развёртывание из этих результатов не следует.

Лабораторные неуспешные попытки сохранены: первый benchmark6202 не включал зафиксированные prompt-файлы и упал до измерений на read-only mount; исправлен состав архива, пороги/приложение не менялись. При первом запуске нового SSO-стенда старый SQLite не имел колонок063, поэтому создана отдельная свежая SQLite. Отдельно исправлен только тестовый launcher: `__main__` guard предотвращает повтор uvicorn при Windows parser spawn. Эти попытки не используются как доказательства браузерной приёмки.

## Повторный браузерный протокол 5А

18 октября UTC, финальная общая реализация и синтетическая SQLite `sso-unified-data`:

- Закрытая вкладка оставила scope lease до естественного transport deadline 02:03:53; новая вкладка сначала видела owner/read-only, затем штатно получила доступ. Поздние запросы отдельно проверены PG/controller/SSO-тестами.
- Две вкладки demo.admin: owner редактирует, contender видит owner и disabled Edit. XLSX раскрыт и открыт в preview; включение меняет только local selection, Save/Start не нажимались. Preview открыт повторно, далее ввода/scroll не было.
- Scope warning наблюдался около02:13:29 с countdown55s внутри Excel. В02:14:25 оба окна закрыты, фокус вернулся на «Выбрать вложения». Другая вкладка получила аренду и свежий snapshot. Независимое read-only чтение SQLite подтвердило draft revision3/selected0, status awaiting_attachments, operations_started0. Локально было selected1: автосохранения/применения не произошло.
- Глоссарий: с02:03:48 оставлен несохранённый текст; около02:13:05 warning/countdown42s, около02:13:48 lost/disabled Save, черновик остался. Копирование доступно. Сохранённый source не перезаписан.
- Уведомления используют один component/CSS, одинаковые countdown/continue/status, с разным текстом действия при expiry. Тексты/фокус/readonly наблюдались через UI/AX; настоящий screen reader отложен пользователем.

Снимки: `reference-editing-common-scope-warning.png`, `reference-editing-common-scope-closed.png`, `reference-editing-scope-reacquired.png`, `reference-editing-common-glossary-expired.png` в локальных outputs чата. Callback был повторно временно добавлен только для этого протокола; **после последнего теста снова удалён**, verified=true/callback_present=false. Все три созданные вкладки закрыты, последняя аренда освобождена через Close. Windows Stop-Process не завершил тестовый Node; подтверждённый собственный PID остановлен taskkill, отсутствие обоих test listeners проверено.

| Повтор A60–A68 | Доказательства и пределы |
|---|---|
| A60/A61 | SSO roles/ownership API, API save/apply suites; две реальные вкладки одного admin, owner/contender/reacquire |
| A62 | Реальный close→TTL→reacquire; PG stale release и controller network/deferred callbacks |
| A63 | Пять operation states на PG, full-validation guards в расширенном backend, неизменный worker lifecycle |
| A64 | Три независимых SSO cookie jar, два OS-процесса за document lease на PG |
| A65 | Реальное десятиминутное закрытие двух scope окон и сохранение draft |
| A66 | Реальная активность из Excel; reset/continue/activity sequence проверены component/pure/API tests |
| A67 | Delayed background timer/expired activity и поздние callbacks — авто; закрытая вкладка — UI/TTL |
| A68 | Реальный local1/server0 после autoclose; applying/replay после expiry/release — PG |

Результат: локальная реализация и доступные автоматические/браузерные проверки завершены. Это не целевая приёмка: миграционная репетиция фактического объёма, rollout/target smoke и отложенная ручная доступность требуют отдельных этапов. Новых коммитов, push, deploy и миграции рабочей БД не было.


## Ручная приёмка 18.10.2026: потеря клавиатурного ввода

Пользователь обнаружил: textarea оригинального описания доступна, фокус внутри, но клавиатурный набор не меняет значение. Воспроизведено в реальном UI через посимвольный ввод; в поле нового правила набор работает, заполнение textarea целиком работает. Предыдущая проверка одного состояния disabled/readonly не подтверждала исправность клавиатурного ввода.

Причина: общий контроллер синхронно уведомлял React из capture-обработчика активности. Повторный рендер управляемого поля мешал обработать новое значение. Исправление в editorLeaseSession.mjs: состояние сроков обновляется сразу, уведомление об активности отправляется отдельной задачей после события ввода; закрытие редактора отменяет эту задачу. Проверки срока и fencing не откладываются.

Регрессионные проверки editorLeaseInput.test.mjs: все 3 сначала упали на синхронном уведомлении, после исправления прошли. Проверены обычный ввод, сброс предупреждения активностью и отмена уведомления при закрытии. Связанный набор — 20 passed. Полный frontend — 625 passed, 1 skipped. ESLint изменённых файлов — pass. Production webpack build — pass; стенд :16302 пересобран с BACKEND_URL :18110.

В реальном UI новой сборки посимвольный ввод кириллицы и удаление символов работают. Пользователь сохранил собственный текст «— моя проверка», версия записи стала 2. Во второй вкладке эта версия видна только для просмотра с владельцем demo.admin. Пробные символы агента убраны из локальной формы; сохранённый пользовательский текст сохранён. Вторая вкладка закрыта, основная оставлена пользователю. Новый полный десятиминутный проход после этой правки пока не выполнялся; таймеры проверены автоматическими тестами. Screen reader остаётся отдельным непройденным пунктом. Коммит/пуш/миграция рабочей БД не выполнялись.


## Повторный полный UI-проход после исправления ввода, 18.10.2026

Человек закрыл редактор кнопкой «Отмена». В проверочной вкладке «Получить доступ снова» вернула редактирование без административного отзыва; оригинальное описание версии 2 с пользовательской добавкой «— моя проверка» сохранилось.

В проверочной вкладке введена, но не сохранена добавка «— черновик таймаута». Начало наблюдения — 03:07:15 UTC. Далее не выполнялись действия ввода, нажатия или прокрутка в редакторе. Проверены реальное предупреждение на девятой минуте и завершение доступа после полного десятиминутного интервала на исправленной production-сборке. После завершения описание осталось в форме с несохранённой добавкой, а поля и сохранение недоступны. Независимое новое открытие получило право редактирования и увидело сохранённую версию 2 без добавки «— черновик таймаута»; это также подтверждает отсутствие автоматического сохранения черновика при таймауте.

Проверочные вкладки освобождены и закрыты. Пользовательская вкладка оставлена на списке терминов. Этот проход закрывает повторную реальную проверку таймаута глоссария после исправления capture-обработчика. Проверка настоящим screen reader и двумя отдельными UI-профилями по-прежнему не заявляется пройденной. Рабочая БД и рабочие сервисы :16300/:18000 не менялись; коммита и пуша нет.


## Уточнение объёма приёмки 18 октября: экранный диктор

По решению пользователя проверка экранным диктором исключена из обязательной приёмки. Ранее зафиксированное «отложено» описывает историю; актуальный статус — «не входит в согласованный объём», а не PASS. Клавиатура, фокус, понятные сообщения, ширина 360 px и проверки блокировок/таймаутов остаются в объёме. Поддержка доступности в коде не удалялась.


## Разработки и теги: повторная UI-приёмка 18.10.2026

Стенд :16302/:18110 использует изолированную SQLite БД и ветку codex/reference-editing на базе 6202af4 с незакоммиченным исправлением capture-уведомления. Основная main на момент проверки — ac6b40f. Два настоящих UI-профиля не заявляются: проверки ниже выполнены парами вкладок одного demo.admin; отдельные роли ранее проверены независимыми SSO API cookie jars.

| Сценарий | Разработка ACCEPT-001 | Тег sso-acceptance-tag |
|---|---|---|
| Посимвольный ввод кириллицы и сохранение | Сохранено название «Приёмка блокировок — ввод проверен» | Сохранён ru-перевод «Приёмка тега» |
| Второе открытие занятой записи | Просмотр, владелец demo.admin, сохранение отключено | Просмотр, владелец demo.admin, сохранение отключено |
| Закрытие владельцем и повторное получение | После «Отмена» вторая вкладка получила редактирование | После «Отмена» вторая вкладка получила редактирование |
| Реальные 600 секунд бездействия | Предупреждение, затем потеря доступа | Предупреждение, затем потеря доступа |
| Несохранённая добавка после timeout | Осталась в форме, сохранить нельзя | Осталась в форме, сохранить нельзя |
| Новое получение после timeout | Доступ получен, сохранённое название без добавки | Доступ получен, сохранённый ru-перевод без добавки |

Полный интервал начат около 03:20:32 UTC; в обеих формах добавлено «— несохранённый черновик», сохранение не выполнялось. Во время ожидания не выполнялись действия ввода, нажатия или прокрутка внутри этих редакторов. Таймеры реальны, не сокращены. Проверочные редакторы в конце освобождены и вкладки закрыты; пользовательская вкладка сохранена. Стенд продолжает работать по просьбе пользователя, поэтому временный разрешённый callback :16302 остаётся до остановки стенда (stop.ps1 удаляет только его).

## Сверка нового коммита вложений ac6b40f

Коммит ac6b40f8d67aaa9201125ef35d2ee14c8224d596 меняет generic preview, подписи, отображение сохранённых изменений состава и предел вложенности. Он ещё не интегрирован в ветку справочников. Условие ожидания T25/T26 закрыто ранее коммитом 6202af4; это дополнительный последующий коммит.

Выполнена только проверка объединения без применения, staging или коммитов. Общих изменённых файлов — 21. Трёхстороннее объединение с нормализацией CRLF/LF не обнаружило текстовых конфликтов в исходниках приложения. Остались 7 файлов с конфликтами: backend/app/i18n/ui_en.json, backend/app/i18n/ui_keys.json (генерируются заново после объединения locale-файлов); backend/tests/test_processing_scope_edit_lock_api.py, backend/tests/test_scope_rollout_guards.py, frontend/test/processingScopeFlow.test.mjs; связанные план и ТЗ обработки вложений. Успех текстового объединения не является проверкой поведения объединённой версии.

Следующий технический этап: перенести ac6b40f в отдельную ветку с сохранением общего editorLeaseSession/EditorLeaseNotice и всех гарантий T25/T26, совместить тестовые harness/fixture-изменения, регенерировать UI-манифесты, затем повторить check-project, frontend build, релевантные backend/PG и scope A60–A68/AC31–AC35 с generic preview. До этого результат данного стенда относится к базе 6202af4 с текущими правками справочников. Рабочая main, её посторонние изменения и рабочая БД не менялись.

## Итог совмещения с ac6b40f и переноса в main — 18 октября 2026

Пользователь отдельно разрешил перенос в main, локальный коммит, миграцию и уточнил, что все БД тестовые. Эти разрешения заменяют прежние ограничения на commit/main/миграцию в исторических разделах отчёта. Push не разрешён и не выполняется. Изменения Kubernetes в основной рабочей копии сохранены отдельно и исключены из коммита.

В изолированной ветке базой стал ac6b40f8d67aaa9201125ef35d2ee14c8224d596. Сохранены общий editorLeaseSession/EditorLeaseNotice, fencing и восстановление черновиков; добавлены upstream generic preview/labels/depth. Разрешены конфликты тестовых harness, двух документов и генерируемых манифестов; UI-манифест содержит 1588 ключей. Затем в main перенесены только 137 файлов этой задачи. Нового коммита в рабочей ветке не создавалось.

| Проверка объединённого дерева | Фактический результат |
|---|---|
| Frontend / check-project | 655 passed, 1 skipped; ESLint и Ruff без ошибок, npm audit 0. Повторный check-project выполнен на main после исправления SQL-таймаута |
| Production build | Webpack production build прошёл и в изолированной ветке, и в main. Основной UI запущен штатным standalone server.js на :16300, backend :18000. Ограничение default Turbopack в worktree с внешним node_modules junction не выдано за успешный тест |
| Релевантный backend-набор | 933 passed, 44 skipped, 906,62 с; 48 focused scope/preview/depth passed. Широкий прогон начат до последнего исправления таймаута; после исправления отдельно пройдены 25 SQLite/API и 38 PostgreSQL проверок; те же25 SQLite/API повторно прошли непосредственно в main |
| Doc-parser | 556 passed, 1 skipped |
| PostgreSQL CT102 | После последнего исправления 38 passed, 102,06 с: миграция, процессы/гонки, scope, writers, glossary, bounded waits, benchmarks. Две выделенные БД очищены по завершении |
| Сравнение с ac6b40f | Порог чтения остался ≤1,10; измеренное отношение p95 0,984. Для записи baseline p95 7,786/7,253 мс, candidate 6,450/6,325 мс. Это ASGI-путь тегов на одном стенде, без заявления об общем ускорении приложения |
| SSO API | На отдельном стенде повторно пройдены три независимых identity/cookie jars, роли, CSRF, save/revoke/late release/428. На фактическом main: три SSO-роли, конфликт владения, viewer 403, старый payload 428, release; содержимое тегов не изменено |
| Scope + новый preview | XLSX-фрагмент и новые пояснения отображаются; вторая вкладка видит demo.admin и запрет редактирования. Реальное бездействие 03:47:19–03:57:21 UTC: предупреждение за минуту, закрытие обоих окон. Локальный выбор 1 не сохранён: server revision=3, included=0, active generation отсутствует. После timeout доступ получен снова. Три SSO-роли не обходят fencing и не продлевают idle чужими запросами |
| Main UI | Для существующего термина проверены получение аренды, read-only второй вкладки, release и успешное повторное получение. Автосохранения не выполнялось |

Финальный read-only review нашёл один P2: SET LOCAL ограничивал всю транзакцию, но преобразование SQL-таймаута в reference_write_busy охватывало только получение resource guard. Поздний UPDATE/flush/commit мог вернуть 500. Исправлено: session_scope после rollback преобразует ошибки только помеченных reference-транзакций. Регрессия сначала дала 6 failed (три SQLSTATE × body/commit), затем прошла; обычный OperationalError остаётся обычной ошибкой. Реальный PG-тест удерживает доменную строку после получения reference guard, проверяет 503, ограниченный срок и отсутствие частичной записи. Повторный полный PG-набор прошёл. P1 и обходов защиты данных reviewer не обнаружил.

### Миграция, включение и проверка отката на фактических тестовых данных

Перед переключением проверены marker 064b1c2d3e4f, соответствие фактической схемы моделям ac6b40f, отсутствие дубликатов глобальных атрибутов и активной обработки (47 документов done). Остановлен только main backend, включая его background writers. Снят PostgreSQL custom-format backup, 11 816 144 байта; содержимое архива проверено pg_restore --list, SHA256 сохранён в локальном журнале.

Upgrade до 065b1c2d3e4f выполнен на разрешённой тестовой okf_knowledge. Созданы 52 состояния, полнота ключей и partial unique index проверены. Сразу после миграции хеши всех 17 таблиц reference-семейства, число документов, переводы и история совпали с исходными. При остановленных writers инвалидированы поколения с сохранением content revisions, в локальном игнорируемом .env включён REFERENCE_EDIT_LOCKS_ENABLED=true, backend перезапущен. После smoke данные/история сохранены, content revisions=1 и активных аренд нет. Исключение из побайтовой сверки после smoke — только технический glossary_state.updated_at: guard обновляет его при acquire/read; id/revision/identities_ready отдельно сверены с исходным dump и не изменились.

Тот же backup восстановлен в отдельную test_reference_20261018_targetcopy (4,656 с); upgrade копии занял 1,453 с. На копии сверены данные/история фактического объёма, затем проверен быстрый откат: lease выключен, поколения инвалидированы, новый CAS backend сохранён, устаревшая запись отклонена. После повторного включения acquire/release прошли. Таблица состояний сохранена; полного возврата старого backend и downgrade использованной схемы не выполнялось. Копия удалена. Данные backup не входят в Git.

### Остаток и границы

- Две настоящие UI-сессии editor/admin с реальным изменением роли и сетевым отказом остаются отдельным этапом ручной приёмки, как согласовано пользователем. API трёх ролей, controller fault tests и две вкладки не объявляются заменой этому этапу.
- Screen reader исключён из обязательного объёма по решению пользователя; клавиатура, фокус, сохранность черновиков и 360 px сохраняются в требованиях.
- Неблокирующие замечания review: общий glossary backfill пока не агрегирует skipped_locked (специализированный endpoint/UI его сохраняет); существовавшее переключение между двумя разработками не спрашивает подтверждения замены несохранённого черновика. Это отдельные небольшие доработки, без найденного обхода серверного CAS/аренды.
- Результат применён к локальному тестовому main. Внешнее production-развёртывание и push не выполнялись.

Временный стенд :16302/:18110 после приёмки остановлен штатным stop.ps1; удалён только согласованный временный callback :16302. Основной UI доступен на http://localhost:16300.
