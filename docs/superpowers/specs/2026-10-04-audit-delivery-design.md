# Аудит приложения и доставка структурированных событий

Дата: 04.10.2026. Статус: для следующего audit этапа пользователь выбрал
**durable outbox с повтором**. Этот выбор фиксирует DB + transactional outbox и
at-least-once локальную запись stdout; best-effort не является выбранным режимом.
[Подробный план](../plans/2026-10-04-audit-outbox.md) отделяет этот пакет от
необязательного trusted-IP relay. Ядро audit transport/outbox реализовано и прошло локальную приёмку в рабочей ветке; доверенный IP и внешние adapters ещё не реализованы. Это решение проекта, а не утверждение о правилах внешней
платформы. Действующий audit_log и Security API сохраняются.

## Цель и границы

Общая versioned schema, stable event ID, auth/domain события и JSON после commit
выбраны продуктовым пакетом по [оценке и критериям обобщения](../../KUBERNETES_PLATFORM_QUESTIONS.md),
пункты 9–10. Сохраняется DB audit; mapping внешнего collector живёт в adapter.
Согласование обязательных событий и failure policy определяет полный объём,
но не превращает формат одной платформы в универсальную schema приложения.
Outbox/HMAC ниже — отдельные решения по пунктам 11–12. Пользователь выбрал
durable outbox; best-effort оставлен только в сравнении вариантов. Downstream ACK
не обещается. Пример logging не устанавливает обязательность outbox.

Зафиксировать событийный профиль, committed DB event и соответствующий JSON
в stdout с общим event ID. Rollback не публикует успешное событие. В durable
варианте отправка сохранённого события повторяется из БД; best-effort допускает
непубликацию после crash между commit и emit без потери самого DB audit. Реальные
index/collector/retention/RBAC настраиваются и принимаются в окруженческой копии.
Доставка до внешнего индекса не объявляется подтверждённой записью в stdout.

Сохраняются локальные launchers, Compose bundled/external и компактный Kubernetes
с одним backend процессом. Реплики dispatcher/API, внешний broker и управление
индексами поисковой платформы приложением не входят в первую реализацию.

## Источники и действующая реализация

| Источник | Что подтверждено | Что не подтверждено |
|---|---|---|
| `backend/app/services/audit.py` | Domain/auth catalog; flush без commit; atomic outbox и immutable payload | Внешний collector ACK и применимый внешний event profile |
| `backend/app/db/models.py:AuditLog` | Legacy Int ID/API сохранены; nullable actor/method/reason/UUID и отдельная outbox state | Strict type/FK/CHECK/index drift проверен локально; production rollout ещё не выполнен |
| `backend/app/db/session.py:session_scope` | Commit после yield, rollback при исключении | Flush не является commit и не разрешает публикацию |
| `backend/app/api/users.py` | Durable объединяет block/unblock + audit; disabled сохраняет прежний flow | Физический DB-full и смешанный concurrent block/unblock проверены локально; DNS/multihost и production load открыты |
| API `_client_ip` helpers / Next proxy | Используют request.client.host; Next удаляет forwarded headers | Доказанный клиентский IP через весь ingress/proxy маршрут |
| `backend/app/services/export_queue.py` | Несколько действующих record_in_session внутри транзакций | Допустимость публикации из append до завершения этих транзакций |
| Предоставленный пример JSON logging, рассмотрен 04.10.2026 | Пример приложения пишет JSON в stdout и не вызывает API внешнего индекса | Сам пример называет downstream путь предположением; конкретный агент, индекс и доставка неизвестны |
| Уточнения документации платформы, пункты 9–12 списка вопросов | Namespace `logging.enabled: true`, Logging Operator → OpenSearch; диагностический Gateway NodePort с PROXY protocol | Обязательность JSON/двойного транспорта/outbox, schema, потери/ACK, фактический collector namespace и доверенность отправителей PROXY protocol не установлены |

Названия, namespace, logger package и корпоративные build plugins из примера
не переносятся. Не копируются Java/Logback зависимости. Его риск раскрытия
секретов учитывается правилом allowlist, а DEBUG не считается способом сделать
секрет допустимым для журналирования. Пример не доказывает требования DB+stdout,
поле event ID, сроки хранения или гарантии доставки — их предлагает эта спецификация.

## Рассмотренные варианты

1. JSON logger сразу после flush/append. Проще, но rollback создаёт ложный факт,
   а crash между DB commit и stdout теряет доставку. Отклоняется.
2. Callback после commit с очередью в памяти. Не публикует rollback, но всё ещё
   может не доставить stdout событие при crash. Допустим для явно выбранного
   best-effort contract с сохранением DB audit; гарантия повтора не заявляется.
3. DB audit + transactional delivery outbox, stdout dispatcher. Рекомендуется
   для выбранного durable contract:
   использует существующую БД, сохраняет pending при сбое и отделяет audit fact
   от изменяемого состояния доставки. Цена — миграция и один bounded loop.

## Схема события и совместимость

Текущий int `audit_log.id` и API filters сохраняются. Добавляются nullable поля
для расширяемой миграции: уникальный `event_id` UUID, `schema_version`,
`actor_type`, `method`, `reason`, `outcome`, `request_id`, `ip_source`.
Новые события всегда имеют заполненные поля своего профиля; существующие строки
не объявляются событиями новой схемы и автоматически в stdout не выгружаются.
Переходный reader поддерживает старые записи; historical replay — отдельная
явная процедура. Ни UUID backfill с выдуманной датой, ни FK на пользователя не нужны.

JSON envelope версии 1:

| Поле | Правило |
|---|---|
| `event_id`, `schema_version`, `event_kind` | UUID, 1, `audit`; один ID для БД/stdout/повтора |
| `@timestamp`, `application`, `component`, `level` | UTC timestamp факта, `ai-knowledge`, `backend`, INFO/WARN; не время retry |
| `action_type`, `outcome` | Канонический action; `success` / `denied` / `failure` |
| `actor` | `type=user/system/anonymous/unknown`, ID/name snapshot при известной проверенной identity |
| `method`, `reason` | Канонический метод, безопасный reason enum; применимы по каталогу |
| `target` | Type/ID при применимости; без содержимого документа или запроса |
| `client_ip`, `ip_source`, `request_id` | Verified client / direct peer / unknown; IP может быть null |
| `old_value`, `new_value`, `meta` | Только разрешённые для конкретного action поля; применимость не заменяется пустым фиктивным значением |

Snapshot имени/ID и IP — данные защищённого audit профиля, доступ к ним
ограничивается в БД и downstream. Внешний collector не должен переносить
неограниченный raw message в audit index. Одна JSON строка, UTF-8, без multiline
traceback; лимит serialized event 16 KiB. Для list changes предусмотрены count
и структурный summary с явным `truncated`; неприменимые поля null/отсутствуют.
UUID/time/actor/action/target/reason не отрезаются ради лимита.

Запрещены cookie, Authorization, code/verifier/state/nonce, passwords, tokens,
DB URLs, signed links, raw IdP responses, headers, prompt/retrieval/document
texts и arbitrary exception strings. Allowlist применяется до DB event и outbox;
для legacy callers неизвестные meta поля не передаются в stdout. Каталог
разрешённых old/new/meta по каждому action обязателен перед включением нового
transport. Сохраняемые обязательные идентификаторы не маскируются так, чтобы
исчезла связь actor/event; доступ и retention определяются отдельно.

Legacy IP от socket сохраняется как transport peer с маркировкой источника,
а не автоматически как адрес браузера. Для нового relay profile отсутствие
проверенного context даёт `client_ip=null`, `ip_source=unknown`; для явно
выбранного direct profile peer может быть client IP. Defaults не включают
relay автоматически по наличию KEYCLOAK_URL или deployment tier.

## Каталог обязательных событий

| Action / группа | Точка фиксации | Метод и причины |
|---|---|---|
| `auth_login_success` | Та же транзакция, что создание auth session, до выдачи cookie | oidc; не угадывать password/MFA внутри IdP |
| `auth_login_denied` | Окончательный отказ callback/start; identity только если проверена | invalid_state/token/subject, no_role, provider_unavailable, malformed_response |
| `auth_access_denied` | Окончательный отказ protected API, один event на request | session_absent/expired, blocked, no_role, insufficient_role; CSRF имеет собственный reason |
| `auth_logout` | Commit локального удаления сессии | local logout success; внешний IdP error отдельно, не отменяет локальное удаление |
| USER_BLOCK/USER_UNBLOCK | В транзакции blocklist change, после проверки фактического результата | Старое/новое состояние и безопасная причина; сохраняется существующий action |
| Существующие domain actions | Подтверждённое событие операции по инвентарю действующих callers | Сохраняются текущие имена, не создаётся дубликат из middleware |
| System cleanup / exports | Действующая transaction boundary/background actor | actor_type=system; request_id/client_ip могут быть null |

При ошибке внешней IdP identity неизвестна: не доверять username/error query
из браузера. Внешний account CRUD/смена пароля/групп происходят в IdP и не
имитируются событиями приложения. Неприменимость фиксируется владельцем audit
профиля в отдельной копии. Ошибка отсутствующего session ID не утверждает
конкретную причину expiry, если сервер больше не может её проверить.

Health/probes, GET auth/me для login gate, OPTIONS и успешное чтение audit UI не создают
auth failure на каждый poll. Protected denial не семплируется молча; ingress
rate limits задаются отдельно. Метрики не заменяют индивидуальный audit event.

## Транзакции и отказ доставки

Этот раздел описывает выбранный durable outbox пакет. Runtime реализован в рабочей ветке;
переход к best-effort не входит в план и потребует отдельного решения.

Audit row + outbox row сохраняются одной транзакцией. `record_in_session`
ничего не отправляет до commit; outer rollback, rollback savepoint и exception
не оставляют pending для отменённых действий. `append` сохраняет прежний API,
но возвращается после собственной DB транзакции, без прямой stdout записи.

Для session creation/block/unblock обязательный audit участвует в той же DB
транзакции. Ошибка записи откатывает действие, браузеру не выдаётся новая cookie.
При отказе запроса audit DB failure не открывает доступ: сохраняется 401/403,
а bounded безопасный аварийный сигнал локальной диагностики отмечает audit unavailable
без утверждения committed event и без блокирующей записи stdout из request path.
При недоступном recorder остаётся только счётчик в памяти; это не durable гарантия
сохранения denial во время отказа DB. При IdP logout failure локальная сессия остаётся
удалённой. Это выбранная политика проекта, не универсальное требование платформы.

Инвентарь существующих domain callers отдельно отмечает same-transaction и
legacy-after-action записи. Нельзя обещать атомарность FS/Qdrant+DB: для них
outcome описывает реально подтверждённый этап. Legacy after-action callers
продолжают работать, но глобальная атомарность domain change+audit не считается
закрытой до миграции конкретного caller и rollback теста. Security mutations
из таблицы выше — обязательный первый объём этой миграции.

Outbox хранит immutable sanitized payload, event reference и отдельно status,
attempt_count, next_attempt_at, last_error_code, stdout_written_at. Изменение
delivery state не изменяет audit_log. Один dispatcher компактного backend
читает только committed pending, пишет строку и flush, затем отмечает
stdout_written_at отдельной транзакцией. Не держать DB transaction во время
блокирующей stdout операции. Retry: 1s, 2s, 4s и далее до 60s, без удаления
pending при исчерпании попыток. Bounded batches 100 events, одна попытка в работе,
без неограниченной очереди payload в памяти. Stop signal завершает loop; pending
сохраняется. Блокировка stdout не блокирует запросы: isolated writer thread;
его зависание отражается в delivery health и не запускает дополнительные writers.

Crash после stdout до DB marker повторяет тот же event_id. Это at-least-once
повтор локальной записи, не exactly-once и не подтверждение получения индексом.
Не называть status delivered: stdout flush не является downstream ACK.
Дедупликация event_id и правила parser/index проверяются внешним adapter.
Audit DB остаётся источником для сверки пропусков; generic exporter/replay
по range/ID проектируется перед реальным восстановлением delivery, не запускается
автоматически. Пока stdout_written events не подтверждены внешним приёмником,
они не удаляются по автоматической короткой retention процедуре.

DB-full/недоступная БД/serialization failure: обязательные новые security
mutations/login прекращаются до commit; protected access denial остаётся denial.
Pending backlog ограничивается ёмкостью БД, не promise infinite buffering;
alert и операторские quota/retention обязательны. Outbox outage не превращает
исторический event в новый login success. Existing readiness storage contract
сохраняется; audit delivery health публикуется отдельно от liveness.

## Доверенный клиентский IP

По уточнениям документации диагностический NodePort Gateway обходит внешний
балансировщик и использует PROXY protocol. Это не обязательно прямой обход Next,
но может обходить проверку отправителя LB. До включения профиля подтвердить
сетевой допуск, доверенных отправителей и sanitization на каждом listener;
проверить поддельный PROXY header и сосуществующие legacy маршруты. Протокол
сам по себе не удостоверяет IP, последующий HMAC не исправляет ложный входной IP.

Forwarded headers от браузера остаются удаляемыми. Предлагается отдельный
необязательный proxy context, а не включение доверия всем X-Forwarded-For:

1. Gateway удаляет входящий `X-OKF-Client-IP` и записывает один адрес из своей
   проверенной цепочки ingress/LB. Только Gateway имеет ingress к frontend,
   без прямого альтернативного NodePort/endpoint frontend или backend. Для
   NodePort самого Gateway отдельно проверяется та же граница доверия.
   Фактический CNI enforcement,
   порядок sanitization и доверенные LB — окруженческий gate, не предположение
   Next API: его Request сам по себе не предоставляет проверенный TCP peer.
2. Только в явно включённом gateway profile Next читает этот единственный
   IP literal; duplicate/comma/control characters/невалидный IPv4/IPv6 дают
   unknown. Raw forwarded headers и любые входящие internal context headers
   удаляются. Без проверенной ingress boundary profile не включается.
3. Next передаёт backend HMAC-SHA256 context, подписанный отдельным relay key:
   version, canonical request ID, IP/source, timestamp, HTTP method и фактический
   raw target path без query. Формат — base64url JSON payload и отдельная signature;
   подписываются точные encoded bytes. Backend проверяет signature constant-time,
   schema/length/path/method и временное окно 30s перед обработкой headers;
   stream/upload продолжают использовать уже проверенный request context.
4. Без подписи/с неверной подписью backend игнорирует relay data. Прямой обход
   Next не позволяет назначить чужой IP; сохраняется peer отдельно от client
   либо unknown. Для локального direct backend допустим явный direct-peer
   профиль. Proxy peer не выдаётся за реальный клиентский IP.

Relay key — новая узкая служебная credential, общая только для Next/backend,
не APP_SECRET_KEY, не OIDC secret и не grant пользовательских прав. Это явное
расширение исходного контракта frontend credentials; chart/Secrets/SECURITY.md
изменяются вместе с реализацией. Clock sync, rotation и TLS/network boundary
проверяются. HMAC защищает участок Next→backend, но не исправляет неподтверждённый
источник IP до Next. Uvicorn proxy_headers отключаются для audit transport,
чтобы socket peer не подменялся до проверки собственного context. Backend
даже с корректным context всё равно требует обычные auth/session/CSRF/roles.

Подпись включает version/key ID; backend допускает current и previous key
только в ограниченном rotation window, unknown key ID отклоняется. Общая
canonicalization method/raw path и правила HTTP header length фиксируются
в отдельном интерфейсном модуле до реализации callers. Неподписанный
X-Request-ID не задаёт audit identity: backend создаёт свой UUID. В durable mode
это уже входит в базовый пакет: существующий diagnostics middleware перестаёт
принимать внешний UUID и назначает общий серверный request.state ID для response,
diagnostics и audit. Verified relay позже использует тот же контракт.
Подписанный relay header не возвращается браузеру и не логируется как payload.

Базовый DB/stdout объём и relay IP реализуются последовательными частями:
сначала schema/outbox и security transactions с честным unknown/direct peer,
затем relay/metrics и home path. Task5а закрывается только после обеих частей;
новый key не выдаётся frontend до готовности ingress boundary и relay tests.

## Наблюдаемость, доступ и retention

Bounded counters: recorded/denied/db_failure/stdout_failure/retry, pending count,
oldest pending age и dispatcher state. Labels только из фиксированных enums;
actor/IP/request/target/event ID не становятся labels. Внутренний metrics
endpoint требует отдельной read-only credential и разрешённого monitor path;
Next proxy не публикует этот endpoint. Ни публичный Ingress, ни anonymous
доступ не добавляются. Механизм подключения monitor проверяется в подробном
плане, не подразумевает установленный Prometheus/ServiceMonitor.

Существующий `audit_retention_days=365` — default проекта, а не согласованный
downstream срок. Retention выполняет отдельная привилегированная процедура,
не audit reader/обычный backend writer. Pending и неподтверждённые downstream
события не удаляются автоматически. Изолированный DB audit account по возможности
имеет SELECT/INSERT; outbox delivery account — только нужные state updates;
maintenance role отдельно. Удаление пользователя/смена имени не меняет snapshots,
FK ради формального SET NULL не вводится. Восстановленный backup не повторяет
исторические stdout events автоматически без отдельного согласованного replay.

## Приёмка и будущая последовательность

Пункты 1 и 3 — общая основа. Пункт 2 проверяется для выбранного durable варианта;
для best-effort проверяются commit-aware JSON, DB persistence и явно принятое
окно потери stdout без обещания retry. Пункты 4–5 относятся к выбранному relay
профилю; для базового профиля проверяются peer/unknown и отсутствие доверия
подставленным headers. Пункт 6 — отдельная окруженческая проверка collector.

1. Инвентарь actions/callers и per-action allowlist; новый JSON schema/UUID,
   legacy reader, migration на непустых PostgreSQL и SQLite fixtures.
2. DB+outbox commit/rollback/savepoint, crash до/после stdout marker, retry,
   broken pipe/блокирующий stdout, restart pending, ID/time consistency.
3. Login/logout/block/unblock/auth denial: identity/method/reason/outcome;
   атомарная security mutation и безопасное поведение DB failure.
4. Node и backend tests на forged headers/context, direct bypass, malformed
   IPv6/duplicates, signature expiry/rotation, Unicode/encoded raw path,
   upload/SSE/cancellation и отсутствие изменения auth/CSRF.
5. Full home path Gateway→Next→backend: настоящие allow/deny CNI probes,
   назначенный synthetic IP, spoofed IP, один DB event и stdout JSON с тем же UUID.
6. В отдельной окруженческой копии: collector parsing/enrichment/index mapping,
   поля на верхнем уровне, поиск по event_id, видимость повтора/дедупликация,
   права/retention, остановка/восстановление collector и сверка пропусков с БД.

Схема доставки: приложение → stdout контейнера → инфраструктурный collector
→ защищённый внешний индекс. Прямой OpenSearch SDK, его endpoint/credentials,
автоматическое создание index и установка logging operator в chart не нужны.
Их отсутствие не доказывает работающий collector. Проверка DB/stdout не заменяет
проверку downstream, а пример другого приложения не заменяет ни одну из них.

## Детализация выбранного durable пакета

Подробный план вводит explicit `AUDIT_STDOUT_MODE=disabled|durable` с default
disabled для совместимости прежних запусков. После restore исторические pending
получают held до первого старта dispatcher; новые события остаются pending.
Held не меняет audit fact/UUID/time/payload и не является потерей события.
Отдельная согласованная historical replay процедура может обработать held позже;
автоматического replay нет. Fixed operator SQL hold работает и со старым binary;
отсутствующая table — проверенный noop, неизвестная schema — отказ до start.

Trusted-IP relay и credential-protected monitor endpoint вынесены в следующий
adapter plan. Этот пакет даёт DB aggregates через защищённый operator CLI;
runtime counters доступны только внутри процесса dispatcher и обнуляются при
restart. CLI не имеет IPC с ним и возвращает runtime_state=unknown. Проверенный
автоматический мониторинг остаётся gate следующего adapter. Публичная метрика
не добавляется; выполнение всей задачи 5а и доставка в collector не заявляются.

## Уточнения после проверки плана 04.10.2026

- Disabled останавливает enqueue и writer, сохраняет pending; durable после
  restart возобновляет pending, но не backfill периода disabled. Rollback/restore
  отдельно удерживают события. Hold нужен и в bundled Compose, и в процедуре
  external/manual restore; приложение не распознаёт копирование DB как restore.
- Legacy domain DB old/new/meta и audit UI сохраняют контракт. Allowlist типов,
  размеров и происхождения значений применяется к immutable event/outbox;
  одно имя разрешённого ключа не разрешает произвольный текст. Auth payload
  проверяется до DB write. Сохранённые UTF-8 bytes не пересериализуются при retry.
- Existing local create_all не обновляет колонки. План включает проверяемый
  additive audit upgrade для непустой локальной DB и отдельный Alembic путь
  для versioned deployments; неизвестный drift не исправляется stamp.
- CSRF direct response и OIDC redirect failures входят в denial coverage.
  Denial DB I/O имеет отдельные connection/pool/statement/lock timeouts и целевой бюджет до 5 s; lock и statement budget, refused/stalled single-endpoint connect проверены; исходный audit-v5 DNS/multihost deadline дал 5 PASS / 3 FAIL, около 6,07–6,08 s. Реализованный audit-v6 проходит выбранную Linux matrix 20 PASS/0 SKIP при неизменном <5 s; полный OS/auth/load/home/CI контракт ещё открыт. I/O выполняется вне
  event loop; emergency diagnostics не ждёт stdout. Cookie/redirect/401/403
  semantics сохраняются, audit UI access denial не исключается.
- Единственный writer защищён OS lock в общем DATA_DIR, в том числе от второго
  процесса и premature unlock после shutdown timeout. Несколько DATA_DIR/PVC
  с общей DB не поддерживаются этой защитой и требуют будущего replica пакета.
- Short write, прерванная JSON строка, посторонние access logs, DB retry outage
  и повреждённая outbox row имеют отдельные тесты. Invalid payload сохраняется
  в held с причиной, не мешает следующим событиям. Глобальный commit order и
  CRI/collector reassembly не обещаются. Оператор сверяет audit UUID/payload hash
  после restore отдельно от существующих document/vector totals.

Результаты локальной реализации и оставшиеся проверки: [приёмка](../reports/2026-10-04-audit-outbox-acceptance.md), [матрица плана](../plans/2026-10-04-audit-outbox.md#сверка-реализации-с-критериями-приёмки). Домашние confidential/basic/PKCE none и public/none/S256/UserInfo-only HTTPS backup/restore подтверждены в отчёте; outbox-only tmpfs exhaustion, выбранные denial/security-mutation statement/lock, connect и mixed-race cases дополнительно подтверждены; whole PGDATA/WAL, выбранные cold-start/lifespan/maintenance/recovery probes подтверждены 05.10.2026 на bounded tmpfs. Прочие OIDC комбинации, дополнительные OS/auth/file profiles, production StorageClass/load, новый home proof, trusted-IP и external CI остаются открыты. Интеграция main этим статусом не подтверждается.


Обновление 05.10.2026: отдельная scoped tinyglobby migration закрыла локальный
dependency gate с сохранением ESLint rules и runtime versions. Финальные
Windows/Linux project/frontend checks, production frontend audit-v3 и home
HTTPS/OIDC/response→DB→stdout smoke подтверждены в отчёте. Backend source
270files не менялся; core audit контракт этой доработкой не расширялся.
External CI, main integration и прочие открытые gates остаются самостоятельными.


Audit preflight проверяет схему и права zero-row SQL, не резервирует место для будущего commit. На живом полном PGDATA чтение, идемпотентная schema operation и hold существующей строки могут завершиться успешно, если не требуют расширения relation. Ошибка обязательной записи audit откатывает security mutation; после WAL PANIC недоступная session lookup может закрыть block/unblock на 401 до mutation. Success/cookie или частичный commit при таком отказе не допускаются. Это уточнение подтверждённой границы, не новый runtime disk monitor.


## Уточнение после DNS/multihost RED 05.10.2026

Реализованное ограниченное исправление транспорта denial описано в [Task 3a плана](../plans/2026-10-04-audit-outbox.md#task-3a-общий-deadline-postgresql-denial--предлагаемый-bounded-repair). Общий monotonic deadline начинается до admission/offload и включает очередь, DNS, все connect attempts, dialect initialization, SQL/commit и cleanup; отдельные statement/connect timeouts его не заменяют. Реализованы 4 s на работу и до 0,5 s cleanup внутри неизменного HTTP <5 s. Admission и число helper processes ограничены; перегрузка сохраняет отказ доступа и один failure signal, но не считается доставленным audit событием. DNS helper, ограниченный его бюджетом, не выполняет SQL; исходные host/TLS identity и transaction hooks сохраняются. Runtime audit-v6 имеет новый source/image freeze (272 Python files) и 20 PASS/0 SKIP реальных network cases. Исходный audit-v5 RED сохранён; новый результат не распространяется на непроверенные OS/auth/load profiles и старые home images.


Проверка плана 05.10.2026 уточняет последовательность: capability/configuration proof на packaged driver → admission/DNS lifecycle → connect/SQL integration → новая image/OS/CI/home приёмка. Проверка профиля обязана учитывать эффективные DSN/env/service/passfile параметры и TLS/GSS; несовместимость нельзя маскировать изменением доверия, отключением защиты или fallback на медленный transport. Процесс resolver не наследует application secrets и DB connections. При потере COMMIT ACK исход неизвестен: нет повторного INSERT или ложного rollback/commit подтверждения; фактический committed outbox остаётся доступен dispatcher. Закрытие локального socket не доказывает мгновенное прекращение серверной транзакции — recovery проверяется отдельно. Численные resource limits и endpoint budgets фиксируются до acceptance. Фактические результаты и оставшиеся составные критерии приведены в Task 3a и отчёте; дизайн сам по себе не закрывает проверку профиля.


Реализованный transport ограничивает admission четырьмя попытками без очереди; DNS — 1 s, 8 hosts × 8 IP, ограниченный secret-free helper. Endpoint budget делит остаток между кандидатами, максимум 1 s; тот же deadline ограничивает dialect setup/SQL/commit/cleanup. Неизвестный COMMIT не повторяет INSERT и не увеличивает committed counters; факты и освобождение серверной сессии сверяются после recovery. Проверены Linux psycopg 3.3.6/libpq 18.6/SQLAlchemy 2.1.3 и native Windows psycopg 3.3.6/libpq 18.4/SQLAlchemy 2.0.52. Preflight допускает эти две platform/build пары; service/GSS/SSPI/mTLS/file-blocking profiles требуют отдельного proof. Service profile сейчас отклоняется preflight. Публичные denial API не меняют return contract; внутренний cleanup bool не является commit acknowledgement.


Продолжение 05.10.2026 подтверждает native Windows PG/SCRAM/TLS/COMMIT (35 PASS/0 SKIP), Windows actual resolver interpreter cleanup и OS localhost DNS→TLS→SQL. Linux final-v7 matrix расширена до 24 PASS/0 SKIP: steady bursts/cancellation, реальные Uvicorn SIGTERM при DNS и SQL, zero leftovers и сохранность bootstrap audit facts. Thread/handle baseline учитывает создание общего AnyIO pool и освобождение test instrumentation; последующие bursts не дают роста. Это не production load/CSI gate. Новый home upgrade/final full suite и hosted CI фиксируются отдельно в отчёте; старый image эти проверки не подтверждает.


Продолжение 05.10.2026: final audit-v7 проходит полный backend 3408 PASS/83 SKIP/0 FAIL, coverage88% при gate85%, project/dependency checks PASS. Домашний managed upgrade с coherent backup и running272 hashes подтвердил public HTTPS/OIDC/roles/CSRF/upload/generation/search/chat, response→DB→stdout и сохранение102 immutable audit rows без исторического replay. Это upgrade proof; новый nonempty restore audit-v7, hosted CI, дополнительные auth/file profiles и production load/CSI остаются открытыми.


Следующее продолжение 05.10.2026 подтвердило nonempty home restore того же audit-v7: 136 audit/outbox rows сохранили ID/event UUID/time/payload hash; четыре новые PVC, APP_SECRET_KEY и PG password. Добавленное historical pending удержано до первого writer start, ранее held сохранён, pending=0, исторического stdout replay нет. Старые cookie/flow отвергнуты, новый login имеет response→DB→stdout equality, полный HTTPS smoke пройден. Source остановлен после coherent backup с сохранением PVC; source/target archives проверены вне VM, restored target оставлен Ready. Это один kind-кластер на прежней VM с тем же synthetic public/S256 IdP; отдельная катастрофическая приёмка restore_without_source_vm и production RPO/RTO, дополнительные auth/file/load/CSI profiles и hosted CI остаются открытыми.


### Уточнение file-profile boundary 05.10.2026

Свежая all-scope Linux matrix на неизменном audit-v7:13 PASS/3 FAIL, без skip. Regular passfile/SCRAM/hostname-hostaddr matching/TLS и real Unix socket/SQL/commit пройдены; блокирующие service environment/passfile/TLS CA FIFO не укладываются в общий deadline. Defaults parsing и local file I/O выполняются внутри native calls, поэтому monotonic polling и isolated DNS сами по себе не ограничивают их. Новый required CI шаг сохраняет RED.

Следующий scoped repair описан в3a.5 плана. Unsupported service необходимо выявить до native defaults parsing; file profile должен пройти bounded подготовку неизменяемых материалов либо получить явный отказ до непроверенного native read. Metadata-only check не закрывает blocked regular read/TOCTOU. Resolver остаётся secret-free; возможный file helper не выполняет SQL и использует отдельный private bounded protocol, без credentials/keys в argv/log/evidence. Budget4s+cleanup0,5s/admission4 и прежние TLS identity/SQLAlchemy adapters/commit contracts сохраняются. На момент исходного RED product change/new image ещё не выполнялись; последующие v11/v13 scoped results приведены ниже. Home136-row restore proof относится к прежнему snapshot.

Уточнение плана 05.10.2026: абсолютный request deadline создаётся до admission/offload; file/config, DNS, connect и SQL используют остаток одних 4 s, cleanup ≤0,5 s и HTTP <5 s. Config validation при startup имеет собственный такой же бюджет и завершается до общего engine.connect/readiness/dispatcher. Это не новая гарантия для всех startup SQL/DDL и не изменение общего DB pool. Ошибки service из окружения проверяются до native defaults даже при явных URL credentials.

До реализации требуется решение по explicit/env/implicit passfile и TLS inputs, численным пределам файлов/IPC, владельцу временных материалов и rotation. Поддержка projected Secret с symlinks/read-only mount проверяется отдельно от обычного mode0600 файла. Одна попытка использует согласованные материалы либо отказывает; старый credential/trust cache не становится неявным fallback. Неподдерживаемые профили перечисляются явно и отказывают до опасного native read. Если выбран helper, его protocol ограничен при чтении, он не выполняет SQL, не наследует секретное окружение и не пишет секреты в argv/log/evidence; остатки принадлежат одной попытке и очищаются по owner. На момент исходного design gate этот выбор ещё не был реализован; bounded helper реализован в v11, последующие проверки описаны ниже.

Connect-only и внешний kill тестового процесса недостаточны для закрытия дефекта: required приёмка включает настоящий HTTP denial, startup refusal, burst/cancel/SIGTERM, отсутствие поздних INSERT и восстановление ресурсов приложения. Неожиданное исключение в fixture — FAIL, а не допустимый bounded refusal. Все исходные16 cases сохраняются при расширении matrix. Локальное исправление, новый image, native Windows, домашний projected Secret/HTTPS/restore и hosted release CI имеют отдельные результаты; старый audit-v7 proof их не заменяет.


### Первый service repair 05.10.2026

Unsupported service отказывается до native defaults при наличии URL/dialect параметра или PGSERVICE, включая пустое значение; явные URL credentials не разрешают service lookup. Process environment не меняется. Ранний lifespan guard выполнен до seed/init_db/shared DB операций; disabled и SQLite сохраняют предыдущий путь. Зафиксирован общий error text без реквизитов; пустой параметр, уже удалённый парсером строкового SQLAlchemy URL, не восстанавливается искусственно. Тест использует URL object для проверки явно сохранённого пустого параметра.

Local candidate audit-v8-service-startup: исходные16 profiles14 PASS/2 FAIL; required all matrix35 PASS/2 FAIL/0 SKIP, focused native Windows57 PASS. Strict blocked-file child принимает только ожидаемый timeout terminal result, не произвольное исключение. Service отказ и startup подтверждены в указанном fixture объёме; passfile/TLS CA FIFO продолжают нарушать deadline. A/C material design/implementation, общий file budget, resource lifecycle/home/release gates остаются открыты; новый full suite и новая home поставка не выполнены.


### Выбранный file-material design 05.10.2026 (scoped implementation, release gates OPEN)

`audit_file_probe.py` — отдельный helper без SQL и app/settings imports; `audit_file_materials.py` владеет IPC/process/material lifetime. Operations defaults/prepare/cleanup: defaults получает только allowlisted PG configuration и HOME через private stdin, не наследует их из environment; prepare получает только file inputs и флаги, не URL/password/adapters. SQLAlchemy context остаётся в parent worker. Request/response ≤32 KiB, ограничения проверяются при чтении. Никаких credential/file contents в argv/stderr/log/evidence.

Passfile ≤1 MiB, CA и CRL ≤4 MiB каждый, максимум3 копируемых файла (9 MiB на attempt). Native matching/escaping/first-line/host-port остаётся у libpq, которому передаётся копия исходных bytes. Missing/unreadable/Unix group-world-access passfile сохраняет ignore semantics; параметры с готовым password не читают passfile. HOME (Unix) или PostgreSQL application-data folder (Windows) разрешаются внутри helper. Все implicit TLS/passfile paths заменяются фиксированными snapshot/missing paths, чтобы native connect не повторял чтение source/default home. TLS mode/hostname/CA semantics не ослабляются.

Private temp directory относится к одному attempt, mode0700/files0600 на Unix; на Windows требуется protected user temp ACL. Source metadata/open/read выполняются только helper; nonregular/FIFO/oversize/rotation получают fixed unsupported-profile отказ, stalled regular I/O — timeout с kill/reap. Symlink generation/metadata проверяются после чтения всех материалов; изменившийся набор отказывает, новый attempt заново читает актуальные файлы без cache. Snapshot хранится до connection.close; source paths не удаляются. Cleanup трогает только фиксированные filenames в exact owned directory, incomplete cleanup quarantines admission slot.

Пока не поддерживаются используемый client certificate/mTLS/encrypted key, sslrootcert=system, sslcrldir и sslkeylogfile; ранний отказ, не silent fallback. Неиспользуемые inputs при sslmode=disable/sslcertmode=disable сохраняют исходный смысл. OpenSSL/provider/GSS/SSPI и произвольно зависшая ОС — отдельные профили. Helper получает не более min(1 s, remaining total deadline) на defaults и prepare; это лимиты отдельных фаз внутри общих4s, не новый общий бюджет. Cleanup общий ≤0,5s. Startup проверяет file/config до seed, но не вводит новый общий timeout всем shared-engine SQL/DDL.


Defaults implementation уточнён после измеренного CPU-limit RED: stdlib ctypes загружает единственную bundled libpq из psycopg_binary.libs, проверяет180006Linux/180004Windows и вызывает PQconndefaults/PQconninfoFree без тяжёлого driver import. Lookup/load/ABI call остаются в killable helper. Структура и освобождение памяти — по [официальному libpq ABI](https://www.postgresql.org/docs/18/libpq-connect.html#LIBPQ-PQCONNDEFAULTS). Full native defaults parity проверена на Windows/Linux. Unknown/missing/ambiguous library отказывает, системная libpq не подбирается как fallback.

Windows path-stat и CRT-fstat ctime различались на неизменённом файле: cross-API identity сравнивает file id/device/size/mtime; Unix дополнительноctime. Полеctime Windows не считать переносимым metadata-change proof ([Python stat_result](https://docs.python.org/3/library/os.html#os.stat_result)). Snapshot сравнивается до/после чтения и после всех материалов; это не закрывает projected Secret или hostile filesystem proof.

File cleanup owner учитывает подавление close exceptions в SQLAlchemy NullPool: tracked DBAPI connections проверяются владельцем attempt вне pool callback. Incomplete cleanup остаётся quarantined, повторный close не создаёт helper. SIGKILL/marker/temp-creation cleanup остаются обязательными отдельными gates; native Windows protected DACL проверен в v13 на выбранном account/driver. Общий startup shared-engine SQL/DDL timeout не менялся.


### Уточнение snapshot consistency и native privacy (05.10.2026)

V13 наблюдает resolved generation и metadata всех используемых inputs, включая missing/ignored, до и после подготовки; изменившийся набор получает fixed refusal. Проверен synthetic POSIX projected layout, включая shared inode и old/new attempt; actual Kubernetes CA/CRL/readonly/fsGroup gate открыт. Windows protected DACL задаётся до source I/O, наследуемый доступ только current token user/SYSTEM/Administrators; отказ установки ACL прекращает prepare. Native directory/copy ACL и actual interpreter reap проверены на выбранном runtime. Реализация использует [SetNamedSecurityInfoW](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow).

Owned metadata/read fault injection подтверждает HTTP/burst/cancel/SIGTERM lifecycle, а не реальный NFS/CSI/kernel stall. На snapshot v13 parent temp directory/owner marker creation ещё мог выполнять filesystem I/O вне helper: общий bounded-filesystem claim запрещён до отдельного repair/proof. SIGKILL orphan cleanup и крайние cleanup faults требуют exact owner proof/quarantine. Required all86 и native114PASS/8POSIX SKIP не заменяют full release/home/hosted CI. Подробности и численные результаты — в [отчёте](../reports/2026-10-04-audit-outbox-acceptance.md#file-rotation-и-lifecycle-05102026--scoped-proof-release-open).


### Bounded creation и reachable cleanup ownership (05.10.2026)

V14 выполняет exclusive mkdir/private DACL/полную owner write в существующем helper с min(1s, remaining work4s). Parent выбирает только lexical UUID/path: Unix TMPDIR/TEMP/TMP либо/tmp; Windows TEMP/TMP либоSYSTEMROOT/Temp, без gettempdir/writable probe и silent fallback на иной root. Expired work начинает0helpers. Cleanup требует exact owner; missing directory допустим, markerless/foreign/collision не удаляются. Живой creator убирает только созданные им marker/directory при обычной ошибке; killed creator безполного marker получает quarantine.

Неподтверждённый create/prepare/DNS/cleanup сохраняет объект owner в private process registry и не возвращает admission slot. Повторная очистка quarantine автоматически не запускается. Registry не durable: parent SIGKILL/orphan operational cleanup требует отдельного ownership proof. HTTP/work/helper/cleanup budgets не изменены; source files/TLS trust/adapters/commit semantics сохраняются. Required profiles100/network24/PG31/storage2 и native127PASS/9POSIX SKIP локально прошли; полный release/home/CI и реальные Secret/NFS/CSI gates остаются открыты. [Приёмка](../reports/2026-10-04-audit-outbox-acceptance.md#bounded-temp-creation-и-cleanup-ownership-05102026--scoped-proof-release-open).


### Cleanup edge contract: уточнение 05.10.2026 (v16)

Абсолютный cleanup deadline может равняться0; только None означает отсутствие переданной границы. IPC close failure — cleanup-incomplete, owner/process registry и admission slot сохраняются. Cleanup принимает exact regular32-byte owner; POSIX marker symlink/FIFO отказывает до чтения. После marker unlink и failed/stalled rmdir каталог остаётся markerless/quarantined, повторная попытка не восстанавливает marker автоматически. Protected private temp/ACL не являются универсальной защитой от hostile filesystem races.

Нагрузочная регрессия v15 установила преждевременный timeout рабочей .25s доли cleanup. V16 перераспределяет **тот же общий .5s**: до .4s на helper/.1s на reap; остаток ограничен исходной абсолютной границей. Цена — меньший reap reserve; неуспешный wait/IPC cleanup сохраняет quarantine, а не возвращает slot. Work4s, helper defaults/create/prepare1s, HTTP<5s и admission4/no queue не изменены. Healthy .3s и stall/native/loaded lifecycle tests проверены; failed v15 proofs сохранены в отчёте.

Controlled SIGKILL witness является fixture-only: продукт не сохраняет durable owner evidence после parent death и не имеет sweeper. Перед operational recovery нужны отдельные lifetime/identity/auth contracts и проверки active/foreign/PID reuse/partial marker/races. Actual projected Secret и writable private temp mount должны проверяться в Kubernetes, включая сохранение emptyDir при container restart и удаление Pod; synthetic layout и Docker tmpfs этого не доказывают. [Текущие proof и открытые gates](../reports/2026-10-04-audit-outbox-acceptance.md#cleanup-edges-и-budget-05102026--scoped-proof-release-open). Исторические v14 open cleanup пункты уточнены этим выбранным proof; составные C/C.1/D/E и release OPEN.


### Selected actual Secret/temp proof (05.10.2026)

На unchanged v16 выполнен отдельный домашний kind Pod proof: actual read-only projected Secret без subPath, UID/GID/fsGroup1000, read-only root/private memory emptyDir64Mi. Explicit/default paths, coherent CA/CRL rotation sampling, old snapshot hashes, CRL removal/reappearance и container-restart versus new-Pod temp lifetime PASS;274 running source hashes matched. Synthetic file bytes — не native TLS/CRL/auth proof. Direct0440 passfile сохраняет ignore semantics; test-only0600 staging принимает bytes, не реализует dynamic synchronizer. Product chart temp/projection/passfile configuration и operational orphan recovery, remaining profiles/full release/home app upgrade/CI OPEN. Работающее приложение не обновлено. [Отчёт](../reports/2026-10-04-audit-outbox-acceptance.md#actual-projected-secret-и-emptydir-05102026--selected-file-proof).

#### Product temp layout 05.10.2026 — selected package

Generic chart получил отдельные bounded disk emptyDir /tmp для backend/frontend,
TMPDIR/TEMP/TMP и ephemeral resources; backend contract наследуется writer Jobs,
legacy backup старым template сохранён. Начальные1Gi/256Mi — не measured sizing.
Password остаётся runtime Secret env. Продуктовый CA/CRL profile application/Jobs/
restore, native TLS/auth/revocation, disk-pressure и orphan ownership остаются OPEN.
App home v7 не обновлено, v16 app sources неизменны. [Приёмка и границы](../reports/2026-10-04-audit-outbox-acceptance.md#product-temporary-storage-05102026).
