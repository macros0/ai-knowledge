# OIDC compatibility: реализация и локальная приёмка

Дата: 04.10.2026. Код находится в `codex/kubernetes-rollout` поверх `f551f5f`;
merge, commit, push и публикация не выполнены. Это общее обезличенное решение.
Результат не разрешает production rollout: delivery gates ниже остаются открытыми.

## Что реализовано

- Сохранены confidential/basic/none/auto defaults; добавлены confidential/post,
  public/none/S256 и явный UserInfo endpoint. Public с унаследованным client secret
  отклоняется; неподдерживаемые комбинации не переключаются автоматически.
- Подписанный ID token обязателен. Discovery не может разрешить `alg=none`: этот
  алгоритм исключён перед Authlib; none-only profile отклоняется до login.
  Authlib 1.7.2 проверяет issuer/audience/signature/
  expiry, provider дополнительно проверяет nonce и строковый subject. UserInfo
  endpoint авторитетен, его sub должен совпасть; fallback/union групп отсутствуют.
- Одноразовый state привязан к браузеру и профилю. В PostgreSQL/SQLite хранится
  hash state/binding; verifier/nonce находятся серверно. DELETE RETURNING commit
  завершён до HTTP. Replay/foreign/expired state не вызывает discovery/JWKS/token/
  UserInfo; это измерено HTTP fixture counters.
- Startup сбрасывает pending flows и останавливается при сбое очистки; валидные
  auth sessions неизменного профиля сохраняются. Повторный login ротирует session
  данного запроса; две уже отправленные разные попытки могут завершиться обе.
- Safe errors/log filters скрывают OAuth query и raw dependency records в OIDC
  context. Settings ValidationError не печатает входные значения с credentials.
  Сбой logout сообщает HTTP503 dependency_unavailable и сохраняет cookie.
- CLI с явным защищённым runtime env поддерживает dry-run и транзакционную
  инвалидизацию только OIDC sessions плюс flows. Legacy без id_token удаляются;
  другие providers и документы сохраняются на обеих БД.
- Additive migration `040b1c2d3e4f → a5b6c7d8e9f0`. Schema/config и backup format
  остаются 2; compact/Recreate/replicas=1/workers=1 и Next proxy сохранены.
  Local launchers и Compose bundled/external не заменяются Kubernetes-рецептом.
- Binary rollback использует точный установленный новый backend для migration Job
  через deploy-only `--migration-image`; application получает сохранённый старый
  confidential image. Guard отказывает до lock/stop при отсутствии установки или
  несовпадении image. Backup revision читается из БД без графа старого Alembic.

Настройки и операции: [OIDC_CONFIGURATION.md](../../OIDC_CONFIGURATION.md).
План: [2026-10-04-oidc-compatibility.md](../plans/2026-10-04-oidc-compatibility.md).

## Измеренные проверки

| Проверка | Фактический результат |
|---|---|
| Исходные auth/SSO/groups/authz/deployment tests | 112 PASS |
| Final v2 Linux focused auth/profile/chart/operator suites | 466 PASS, 0 skips, 589,68 s |
| Последний flow store suite на SQLite | 12 PASS, 16,00 s |
| Тот же suite на disposable PostgreSQL 17.11 | 12 PASS, 20,90 s, 0 skips |
| Историческая стабильность до поздних исправлений | 10 × 54 PASS; 28 deselected в каждом |
| Final v4 Linux focused suites, imports из packaged /app | 472 PASS, 0 skips, 729,72 s |
| Final v4 flow store на disposable PostgreSQL 17.11 | 12 PASS, 23,25 s, 0 skips |
| Final v4 стабильность критичных OIDC/JWT cases | 10 × 18 PASS; 86 deselected в каждом |
| Frontend Next API proxy | 4 PASS |
| Linux Compose backup и diagnostics contracts | оба PASS |
| Backend Ruff после удаления одного неиспользуемого import в trust test | PASS |
| Packaged v3 auth/error-path приёмка | 225 PASS, 0 skips, 270,90 s |
| node scripts/check-project.mjs | FAIL: npm audit, 5 high |
| Исходная попытка полного backend suite | historical: остановлена через 1500 s без summary; PASS не засчитан |
| Полный collected backend suite после fixture fixes | 3336 collected = 3309 PASS + 27 SKIP; 8 partitions, все exit 0 |
| Дополнительные PostgreSQL migration/concurrency suites | 17 PASS, 0 SKIP; выполнены 11 ранее пропущенных PG cases |
| Весь frontend suite на Windows | 371 PASS + 1 POSIX SKIP |
| POSIX frontend SIGTERM case в Linux image | 1 PASS; 25 остальных tests deselected через name pattern (Node выводит SKIP) |
| Весь frontend ESLint | 0 errors, 40 warnings |
| Standard frontend Docker build | PASS с cache; OCI manifest совпадает с испытанным frontend v1 |

466-test run был collected до добавления дополнительного DB-preservation case;
последний 12-test store suite проверяет его отдельно на обеих БД. Десять повторов
использовали `test_oidc_flow_store.py` и `test_platform_oidc_contract.py` до поздних
Settings/HTTP503 исправлений. В набор входят атомарный consume/commit/concurrency,
HTTP basic/post/none + S256, invalid signed JWT, UserInfo, replay/denial/rotation,
logging и reset. Из повторов исключены Settings/URI/fingerprint и lifespan startup
cases (28 deselected); дополнения из `test_oidc_operational_edges.py` туда не входили.
Это 540 успешных выполнений указанного исторического набора.

Final v4 suite использует `--import-mode=importlib`, `PYTHONPATH=/app:/repo/backend`:
app импортируется из собранного образа, tests — из readonly рабочей ветки.
Все 22 auth-scope файла и OKF_BUILD_REVISION совпали с source manifest.
Последние десять повторов включают только `actual_authlib_token_auth_and_pkce`,
`signed_invalid_token`, `parallel_callbacks_one_exchange`,
`discovery_advertises_none`, `discovery_includes_none`, `none_only`: три auth/PKCE
профиля, 11 invalid-token cases, один concurrent callback и три discovery-none
cases. Это 180 выполнений 18 различных cases; остальные 86 protocol/edge cases
в этих повторах не запускались, но вошли в полный 472-test run.

На final v4 сохранились предупреждения Starlette/httpx deprecation и Qdrant version
check в unit health fixture; проверки не ослаблялись для их подавления. Первые Windows
прогоны содержали skips для отсутствующих Linux/Helm tools. Linux run после установки
Helm и kubectl подтвердил 0 skips; Windows skips не объявлялись PASS.

## Настоящий Kubernetes/Keycloak стенд

Локальный Docker Desktop/kind, отдельный run `01dc261004ab`: 16 CPU, 15,48 GiB RAM.
Kubernetes 1.37.0, kind 0.33.0, Gateway API 1.6.2, kgateway 2.4.5,
Keycloak 26.8.0. Это fallback на рабочей машине; Proxmox/training-ubuntu в этой
сессии не проверены — защищённый SSH-профиль не найден. Существующие сервисы
домашней инфраструктуры не останавливались.

Три независимых namespace/Secrets/PVC/registration профиля последовательно прошли
HTTPS Gateway → Next → backend → настоящий Keycloak:

| Регистрация | PKCE | Claims | Результат |
|---|---|---|---|
| confidential/basic | none | auto | PASS |
| confidential/post | S256 | auto | PASS |
| public/none без secret | S256 | группы только UserInfo | PASS |

Проверены независимый CA negative control/HTTPS/HTTP redirect, четыре роли/no-role,
Secure/HttpOnly cookies, CSRF, 2 MiB DOCX upload, generation, BM25 sources,
streaming chat, RP logout и application session revocation. Эмбеддинги/LLM —
synthetic stub; live providers не проверялись. На настоящем IdP проверены
registration S256 и UserInfo-only mapper; HTTP/JWT fixture не подменял этот этап.

Первый matrix был на v1; после найденного Settings leak все три профиля повторены
на одинаковом backend v2 и неизменных frontend/stub v1. V3 исправил код ответа
ветки ошибки logout. После воспроизведения unsigned JWT с advertised none собран
v4; отдельный свежий run `01dc261004ac` прошёл те же три HTTPS-профиля.
Сверены UID/labels семи namespaces и владелец dedicated node; этот cluster удалён.
Все шесть прежних backup kits повторно прошли verify_backup и сохранены.

## Непустая миграция, backup/restore и rollback

Эти runtime процедуры выполнены на сохранённых v1/v2 и старом configuration-v2
images до поздних исправлений HTTP503/discovery-none. На final v4 выполнены новая
HTTPS matrix и packaged suites; полный backup/restore matrix на v4 не повторялся.

- PostgreSQL с непустым корпусом и auth sessions прошёл старый head → новый head;
  прежняя валидная session сохранилась, pending callback отказан.
- Public sequence: session + pending flow → coherent backup → logout/consume
  source → fresh namespace/PVC/runtime Secret с новым APP_SECRET_KEY → restore
  сохранённых images/CA → отказ старой auth cookie и callback → новый CLI.
- Отдельная полная процедура сначала восстановила старый configuration-v2 image
  со старым head и saved CA. Новый signing key был задан **до первого старта**.
  Старые cookies отказаны и на старом, и на новом image; затем additive upgrade,
  инвалидизация восстановленных sessions новым CLI и confidential HTTPS smoke.
- В обеих восстановленных копиях проверены strict totals/integrity/content:
  1 document, 1 chunk, 1 concept, 2 Qdrant points. Backup kit не переписывался.
- Обычный restart сохраняет валидную session, сбрасывает pending flow. Session
  expiry проверен настоящим HTTPS запросом после контролируемого перевода
  server-side expires_at в прошлое; это не ожидание полного production TTL.
- Binary rollback после CLI вернул old confidential image с новой таблицей/head,
  прошёл HTTPS smoke и вернулся вперёд. DB downgrade/stamp не выполнялись.

Нулевые endpoint request counts измерены в signed HTTP fixture. Runtime Keycloak
приёмка подтверждает реальные HTTP отказы/cookies, но отдельный счётчик token
endpoint на этом стенде не собирался.

## Неудачные попытки и исправления

1. Generic Helm secret-key guard запрещал TOKEN_ENDPOINT_AUTH_METHOD. RED 3 FAIL:
   добавлено только точное исключение с enum basic/post/none; остальные guards
   сохранены, chart suite прошёл 63 теста.
2. Первое восстановление остановилось на загрузке Qdrant snapshot. Exact command
   повторён успешно; причина первого отказа не установлена. Продолжение было
   только после проверенных manifest, совпадения totals и strict integrity;
   maintenance lock сохранялся до завершения. Отдельный old-image restore затем
   прошёл полностью. Автоматическая надёжность восстановления без retry не доказана.
3. Private acceptance helper имел неверное экранирование newline при подготовке
   env-файла CLI. После исправления helper выполнены dry-run/confirm в обеих
   PostgreSQL runtime-копиях; provider-preservation service отдельно проверен на
   SQLite и PostgreSQL; production CLI для этого не менялся.
4. Дополнительный review подтвердил Settings secret в тексте ValidationError;
   исправление hide_input_in_errors проверено RED/GREEN и в реальном image v2.
5. Сбой logout возвращал HTTP503 с auth_required. RED/GREEN исправил код на
   dependency_unavailable; реальный API middleware ответил 503 без redirect_url,
   сохранил cookie и после восстановления DB позволил прежний auth/me.
6. Первый v3 build оборвался на pip IncompleteRead; журнал сохранён, повтор
   прошёл без смены зависимостей/контракта; затем собран итоговый v4.
7. Authlib принимал unsigned JWT, если discovery рекламировал none. RED: три
   новых HTTP fixture tests FAIL. После исключения none из cached metadata
   GREEN: unsigned mixed-list отказан, signed RS256 принят, none-only отказан до
   authorization. Эти cases включены в десять повторов на packaged v4.
8. Первый deploy v4 остановился до migration: PostgreSQL/Qdrant скачивались
   дольше storage readiness timeout 300 s (events: 8m45 и 7m41 включая ожидание).
   После Ready проверены owner UID, прежний lock, отсутствие application/Jobs;
   продолжена та же операция. Timeout в production tool не увеличивался.
9. Первая пара concurrent Windows pytest делила basetemp и столкнулась с WinError32.
   Последующие прогоны использовали разные basetemp. Устаревшие auth mocks и logging
   fixture исправлены; production nonce/state/signature guards не ослаблялись.

## Источники образов и остаточные gates

Authlib 1.7.2, joserfc 1.7.5, SQLAlchemy 2.1.3, httpx 0.28.1,
Pydantic 2.13.5/settings 2.15.0, SQLite 3.46.1 в v1/v2/v4.
В итоговом v4 версии повторно прочитаны непосредственно из image.
Сохранённый old configuration image использовал Authlib 1.8.0.
Source revision ниже — локальная идентичность auth scope, **не Git commit**:

- v1: e362abe950ca522364298520d427e90ebfbaf380;
- v2: f0a236bf9f813f90227efec5ab39edf8227464fc;
- v3: d470f7800fc1ca0f9b72965a97370079f537401f;
- final v4: 706e14d3febb23c69342b481700aac8e24bd6db3.

| Образ | Docker local .Id | Loaded kind OCI manifest |
|---|---|---|
| final backend oidc-v4 | 95f78b494d0936ba5357c61a83de530c4715edffce3f1e8734e61eb18b1f3cf5 | 276d106f68b3c879bf61a128a8361a7ef7dce16f4dee466903ebeb198fa50dde |
| historical backend oidc-v2 | 753041991f46e3eaeaf6d04c667de5cf75a83968432bfd7d70265c563c6d730b | 1dfe00451f4395816d51edb8fa5f31e3d4c30d9135af19082531e42749c10de8 |
| frontend oidc-v1 | cac9d93b01938be2133349edea9273ef564475c181f48c67889adb943638c72f | ac5cc7a0da33c340221c194bd4c9919a28d3405f8edc690527ec2ceb46f73d93 |
| stub oidc-v1 | 3908fcdcab83f65da2e8d107f4e76a721bdcc82dc764f0ac1708a4cd35ed40e2 | e991e78a82301de14f447522e12c5397f92fc30b5f8dd7fd4ba42bcbafdc27b8 |

Все значения таблицы — SHA256, local index/identity и loaded manifest различаются.
Это не опубликованные registry/release digests. Private source manifests, exact
command logs, CA keys/credentials/cookies и dumps остаются вне публичного отчёта.

После proof проверены UID/labels 11 собственных namespaces и владелец dedicated
kind node; cluster удалён. Шесть независимо проверенных backup-комплектов и images
сохранены в private Docker volume `okf-oidc-01dc261004ab`. Чужие volumes не удалялись.

Не закрыты: npm audit (5 high, check-project прекращается до следующих стадий),
монолитный backend/coverage CI и оставшиеся platform/storage fault cases,
внешние GitLab/CI/security gates, OCI/release publication,
training-ubuntu capacity/connectivity, целевая IdP registration, live models,
certificate renewal, окруженческие Gateway/IdP/custom collector log checks.
Аудит/outbox/trusted proxy, external storage, независимые frontend/backend replicas
и HA остаются отдельными архитектурными этапами. Review: inline self-review,
дополнительные tests и actual lab; независимый agent review не запускался.

## Продолжение: полный локальный набор и следующий audit этап

Полный collected backend набор разбит по модулям на восемь независимых
процессов по 417 cases. Все 3336 cases назначены ровно один раз, без exclusions;
app импортирован из packaged v4, tests — readonly из рабочей ветки. У каждого
процесса отдельный basetemp и tmpfs 4 GiB. Итоги: 3309 PASS / 27 SKIP / 0 FAIL;
все восемь exit codes 0. Это локальный partitioned suite, не монолитный CI с
coverage threshold. Fixture fixes не изменили production source/image.

Из 27 пропусков 11 PostgreSQL cases затем выполнены на отдельном disposable
PostgreSQL 17.11: диагностика/migration, mail migrations и glossary concurrency.
Эти модули дали 17 PASS / 0 SKIP (часть SQLite cases повторилась). Остались
10 Windows/macOS resource-monitor cases, 5 real-ENOSPC cases без выделенного
FULL_STORAGE_TEST_ROOT и один Windows NTFS junction case. Контрольный skip
набор дал 38 PASS / 27 SKIP и подтвердил причины; пропуски не названы PASS.

Неудачные попытки сохранены:

- Первые восемь diagnostic runs на Docker overlay были остановлены после
  измерения I/O: одинаковые 20 queue tests — 30,32 s на обычном FS и 11,20 s
  на tmpfs, при малом CPU usage. Это подтверждает влияние temporary filesystem,
  но не объявляет универсальную причину любой задержки.
- Первое полное tmpfs испытание (1 GiB) закончено: 3269 PASS / 40 FAIL / 27 SKIP.
  30 failures вызваны старыми HTTPS/mock fixtures; 10 — reserve min_free_bytes
  2 GiB на tmpfs 1 GiB. Исправлены только fixtures в test_settings,
  test_security_headers, test_kubernetes_chat_modes и test_glossary_initial_startup.
  Guard HTTPS/min-free не ослаблен. Последующий targeted набор: 105 PASS;
  затем выполнен весь collected suite заново на tmpfs 4 GiB.
- Default Windows Next build отказан: worktree node_modules — junction за пределы
  Turbopack root. Production config не изменялась ради workspace. Standard Docker
  build с обычным dependency tree прошёл с cache, manifest
  ac5cc7a0da33c340221c194bd4c9919a28d3405f8edc690527ec2ceb46f73d93
  совпал с ранее испытанным frontend. Повторный compile в этом build не заявляется.

npm audit повторён: пять high в braces→micromatch→fast-glob→Next ESLint цепочке.
На 04.10.2026 patched version отсутствует в
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm);
metadata latest @next/eslint-plugin-next 16.3.8 по-прежнему требует fast-glob 3.3.1.
Предложенный npm force откат к eslint-config-next 14.2.35 не применён.
Gate не ослаблен и не исключает dev dependencies. ESLint/frontend tests/Ruff
проверены отдельно, это не превращает check-project в PASS.

Пользователь выбрал **durable outbox с повтором** для следующего этапа.
[Подробный audit план](../plans/2026-10-04-audit-outbox.md) фиксирует transactions,
immutable payload, retry, crash/duplicates, unknown client IP и restore hold.
Новый audit runtime ещё не реализован. Trusted-IP/monitor/collector/replay
остаются отдельными adapters; roadmap/questions синхронизированы с этим статусом.
Все disposable test containers, включая PG, удалены; private evidence volume,
старые images и шесть резервных комплектов сохранены. Commit/merge/push не сделаны.
