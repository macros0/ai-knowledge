# PostgreSQL TLS для bundled Kubernetes — проект дизайна

Дата: 05.10.2026. **Статус: T1–T2 проверены; T3 static/native и durable checkpoint foundation проверены; full storage recovery и backup/restore/full matrix T3–T6 OPEN.**
Это необязательное расширение после [временного хранилища](../reports/2026-10-04-audit-outbox-acceptance.md#product-temporary-storage-05102026).
Текущий compact chart, домашнее приложение и frozen audit-v16 не изменяются
этим документом. TLS не объявляется обязательным требованием платформы.

Первоначальный bundled запуск с `enabled=false` не зависит от реализации TLS,
если для выбранной установки шифрование PG-трафика не требуется. Его собственные
release/audit/backup/resource gates сохраняются. TLS-профиль нельзя включать до
полной приёмки этого пакета. [План реализации и матрица выхода](../plans/2026-10-05-kubernetes-postgres-tls.md).

## Зачем нужен отдельный контракт

Исходная точка дизайна до реализации T1/T2 в `codex/kubernetes-rollout`
(исторические ограничения, не описание текущего chart):

- `deploy/helm/ai-knowledge/templates/storage.yaml` не включает PostgreSQL TLS,
  не монтирует server certificate/key и не задаёт собственный HBA.
- `scripts/kubernetes/lifecycle.py:validate_runtime_secret` отклоняет любой query
  в `DATABASE_URL`; одной передачи `sslrootcert` в URL недостаточно.
- `operation_pod_spec` переносит HTTP trust и backend temp, но не PG trust.
- `backup_stopped`/`restore` исполняют `pg_dump`/`pg_restore` внутри PG Pod без `-h`,
  через локальный Unix socket. SQLAlchemy и audit подключаются по TCP отдельно.
- Backup format2 сохраняет HTTP trust identity, но не профиль PostgreSQL TLS.
- Общий operator runtime сейчас содержит только PyYAML. Добавление разбора
  сертификатов требует явной зависимости и нового проверенного toolbox.

Серверу необходимы `ssl=on`, certificate/key и правила доступа; клиентский CA
сам по себе не включает шифрование. [PostgreSQL 17 server TLS](https://www.postgresql.org/docs/17/ssl-tcp.html).

## Выбранное предложение и альтернативы

Предлагается **необязательный bundled TLS**, по умолчанию выключенный. При
включении сервер разрешает TCP только через TLS с SCRAM, а backend и writer Jobs
используют `verify-full`, отдельный CA и необязательный CRL. Пароль остаётся в
существующем runtime Secret. Ни client mTLS, ни автоматический выпуск сертификатов
не входят в первый пакет.

Альтернативы: только монтировать клиентский CA/CRL (не создаёт работающего TLS
профиля текущего сервера); сначала реализовать external PostgreSQL Kubernetes
(расширяет границы хранения, сетей и backup); включить TLS по умолчанию (ломает
существующий первоначальный запуск без PKI). Для следующего пакета предлагается
полный opt-in bundled профиль, сохраняя текущий способ запуска.

## Внешний интерфейс

Дерево values реализовано в chart0.2.0; **TLS release ждёт T3–T6**:

```yaml
storage:
  postgresTls:
    enabled: false
    serverSecret: ""
    clientSecret: ""
    crlEnabled: false
```

При `enabled=false` оба имени пустые, `crlEnabled=false`; существующая установка
не получает новый HBA или TLS volume. При `enabled=true` оба имени обязательны,
валидны как Kubernetes Secret names, различаются между собой и runtime Secret.
HTTP `trust.existingConfigMap` остаётся независимым. Arbitrary paths, passthrough
postgres arguments и пользовательский HBA в первом профиле не принимаются.

Chart не создаёт Secrets и не выпускает сертификаты. Оператор заранее создаёт
**immutable Secrets с новыми именами на каждую версию**:

| Secret | Разрешённые ключи | Получатель |
|---|---|---|
| serverSecret | `tls.crt`, `tls.key` | только PostgreSQL Pod |
| clientSecret | `ca.crt`; `ca.crl` только при crlEnabled | backend и operation Jobs |
| существующий runtime Secret | DATABASE_URL/POSTGRES_PASSWORD и app config | как сейчас |

Secret metadata/UID, exact keys, base64 и размеры проверяются до lock/stop. Новые
TLS Secrets нельзя удалить/создать под тем же именем во время операции: UID и
hash повторно проверяются перед использованием. Это проверка согласованности
операции, а не доказательство защиты от субъекта с правом менять Secrets.
[Immutable Secrets](https://kubernetes.io/docs/concepts/configuration/secret/#immutable-secrets).

## Сервер и клиент

PostgreSQL сохраняет UID/GID/fsGroup999, один StatefulSet и прежний PVC. Secret
монтируется read-only каталогом вне PGDATA, без subPath, с mode0440; ожидается
root owner/group999 после kubelet fsGroup. Root-owned key с group-read допускается
сервером, однако именно0440/root999 требуется доказать на используемом image/CSI;
не включать root/chmod-to-world fallback при несовпадении.
[Server key permissions](https://www.postgresql.org/docs/17/ssl-tcp.html#SSL-SETUP).

Пути предложения: `/etc/okf/postgres-server/tls.crt`, `tls.key`; параметры
`ssl=on`, `ssl_cert_file`, `ssl_key_file`, `ssl_min_protocol_version=TLSv1.2`.
Certificate chain начинается с leaf; SAN включает DNS `postgres`, которым
пользуется bundled URL. Непроверенный CN-only/IP/wildcard профиль не принимается.
Client CA содержит доверенные CA; приватные ключи в client Secret запрещены.

HBA задаётся immutable chart ConfigMap с content-hash в имени, путь передаётся
`hba_file`. Старый ConfigMap сохраняется для установленного Pod и восстановления;
его удаление — только после проверки отсутствия ссылок и окончания retention.
Правила порядком: `local all okf trust` для существующих entrypoint/health/dump/restore,
затем `local all all reject`;
`hostnossl all all 0.0.0.0/0 reject` и IPv6 аналог; `hostssl okf_knowledge okf`
с SCRAM для IPv4/IPv6; остальные TCP подключения reject. Готовность по прежнему
локальному pg_isready не подтверждает TCP/TLS — добавляется отдельная проверка.
Local admin socket не публикуется в другие Pods. Это не пакет least-privilege:
текущий bundled bootstrap user `okf` сохраняет существующие права.
`trust` ограничен локальным socket внутри PG Pod; TCP trust запрещён. Контракт
не считается доказанным без first boot на пустом PVC и повторного старта на
непустом PVC: initdb/temporary server также получают HBA и должны завершиться.
[HBA ordering и connection types](https://www.postgresql.org/docs/17/auth-pg-hba-conf.html).

Backend монтирует client Secret read-only в `/etc/okf/postgres-client`, mode0440,
без subPath. Chart задаёт `PGSSLMODE=verify-full`, `PGSSLROOTCERT=.../ca.crt` и
при crlEnabled `PGSSLCRL=.../ca.crl`, а также `PGGSSENCMODE=disable` и
`PGREQUIREAUTH=scram-sha-256` для выбранного backend libpq18. GSS encryption может
иметь приоритет над SSL, поэтому одного sslmode недостаточно.
[libpq negotiation/auth parameters](https://www.postgresql.org/docs/18/libpq-connect.html#LIBPQ-PARAMKEYWORDS).
`DATABASE_URL` остаётся без query. Те же
env/mount/volume наследуются всеми writer/toolbox Jobs из своего template;
frontend не получает PG Secrets. `verify-full` проверяет цепочку и hostname,
CRL проверяет отзыв серверного сертификата. [libpq TLS](https://www.postgresql.org/docs/18/libpq-ssl.html).

В TLS профиле конфликтующие `PGSSL*`, `PGGSS*`, `PGREQUIREAUTH`, `PGSERVICE*`, `PGOPTIONS`, `PGHOST*`,
`PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGPASSFILE` в runtime env/config
отклоняются, а не побеждаются порядком envFrom. Query в URL по-прежнему запрещён.
Для `crlEnabled=false` отсутствие implicit HOME CRL/client-cert inputs должно
быть доказано на shipping image; пустая env-строка не считается доказательством
отключения native default. mTLS inputs запрещены, supported `sslcertmode=disable`
фиксируется для backend libpq18 и проверяется вместе с audit helper defaults.
Импортированный image не должен содержать credentials/default PG files в HOME;
случайное присутствие таких файлов или несовместимая native libpq — отказ
проверки image, а не тихое игнорирование. Connection pools проверяются на новых
соединениях: уже открытая сессия не доказывает применение нового CA/CRL.
Старый plaintext профиль не получает новые ограничения PG env молча.

## Preflight и зависимости

Отдельный модуль `scripts/kubernetes/postgres_tls.py` отвечает за описание PG
profile, валидацию Secret materials, наследование в Job и backup identity.
Lifecycle оркестрирует проверки и stop/backup/start, но не содержит PEM parser.

Operator kit явно объявляет и фиксирует версию `cryptography`, проверенную на
Python3.11+ Windows/Linux и в toolbox; версия выбирается и фиксируется до первой
продуктовой реализации. Metadata/archive allowlist, bootstrap runtime check,
release/config-schema compatibility и dependency gate обновляются согласованно.
Нельзя положиться на транзитивную библиотеку backend или скрытый shell openssl.
Новый chart release использует config schema3 даже при TLS disabled. Kit3
исполняет только schema3 archives с exact allowlist; schema2 release остаётся
запускаемым своим сохранённым kit2. Не расширять старый allowlist до произвольных
новых файлов. Старый kit обязан отказать schema3 до cluster mutations; kit3
не должен выдавать себя за kit2. Чтение backup2 — отдельная совместимость данных.

Предварительные верхние границы: server certificate256KiB/key64KiB,
CA256KiB/CRL256KiB, максимум16 certificate PEM blocks; exact keys и total size
укладываются в лимит Secret API. Проверять лимиты до decoding/parsing, включая
encoded length. Материалы не пишутся в argv, stderr, evidence или диагностику.
Пароль и private key не входят в returned identity или backup. Ответы ошибок
содержат только категорию причины и не рендерят входные данные.

Preflight проверяет immutable/UID, PEM types, key/leaf public-key match, SAN,
validity и CA constraints, CRL parsing/validity. Это не подмена native chain/CRL
verification: настоящий frozen driver/OpenSSL и PG17 проверяются отдельно.
Статический preflight не требует работающей БД: он нужен и для fresh install,
и для `provision(require_secret=false)`. Перед первым запуском PG TLS Secrets
уже должны существовать; VSO здесь синхронизирует только runtime Secret. После
его получения повторно проверяется credential/config contract. Post-storage
Job проверяет настоящее TCP verify-full/SCRAM до migration/application;
ошибка сохраняет lock и остановленное application. В действующей БД до stop
SCRAM verifier проверяется как boolean, без вывода rolpassword;
несовместимые старые verifier требуют отдельной ротации пароля, не скрытого reset.

## Обновление и ротация

Оператор готовит новые immutable version-named Secrets и защищённые values.
Перед stop проверяются desired и installed PG profiles, наличие старых материалов
для backup, конфликты env и ownership. Затем текущий lifecycle: lock → подтверждённый
выход application → backup старым template/image/trust → новый storage config →
PostgreSQL readiness плюс TCP verify-full/SCRAM probe → migration → application.

Нет hot reload, in-place Secret mutation, automatic downgrade или force-delete.
При ошибке после stop приложение остаётся остановленным, lock сохраняется.
PG restart подтверждается новым Pod UID/готовой ревизией и новыми соединениями;
`pg_reload_conf()` не является доказательством переключения — при неверных TLS
файлах PostgreSQL может сохранить старую конфигурацию.
[Server reload behavior](https://www.postgresql.org/docs/17/ssl-tcp.html#SSL-SERVER-FILES).
Два отдельных Secrets не являются атомарным поколением: preflight/native proof
проверяют совместимость выбранной пары; повторные UID/hash checks обнаруживают
замену внутри операции, но не создают транзакцию Kubernetes API.

Ротация leaf при прежнем CA и ротация CA — разные проверки. Для CA требуется
контролируемое перекрытие trust bundles и отдельная последовательность upgrade;
старые Secret versions сохраняются до окончания backup/rollback retention.
Сам chart не меняет пароль, CA или certificate issuance.

Перед мутацией сохраняется защищённый operation checkpoint: installed/desired
chart+image digests, Pod/StatefulSet identity, HBA hash, Secret refs/UID и hashes
публичного trust, фаза migration. Private keys/passwords не копируются в checkpoint.
Recovery — отдельное действие оператора над этим operation ID. До начала migration
разрешается возврат прежней проверенной TLS конфигурации с подтверждением выхода
нового PG Pod; после начала migration действует политика совместимости SQL и
restore в новые PVC, а не откат Helm. Автоматический переход TLS→plaintext запрещён.
Для неудачной первой активации TLS возврат к прежнему plaintext — отдельное явно
санкционированное recovery с фиксацией понижения защиты; обычный deploy этого не делает.
Старые ключи, сертификаты и CRL должны быть ещё допустимы: revoked/expired профиль
нельзя оживить ради rollback. Если допустимого старого профиля нет, выполняется
repair/reissue или restore с контролируемой сменой trust.

## Backup/restore и совместимость

Новые backup записывают format3 и обязательное `postgres_tls` в release identity
(null для disabled). TLS identity хранит profile mode, hashes точных CA/CRL bytes,
CRL policy; private key и password отсутствуют. Публичные `postgres-ca.pem` и
`postgres-crl.pem` входят в manifest только когда требуются, exact bytes не
нормализуются. HTTP trust сохраняется отдельным полем, как сейчас.

В format2 отсутствие PG identity означает **unknown**, а не доказанный disabled:
старые runtime env могли задавать TLS вне chart. Новый kit по умолчанию отказывает
такому restore до lock/import. Предлагаемый явный `--legacy-postgres-profile=disabled`
разрешает adoption только в disabled target после сверки исходного release/runtime
оператором и при отсутствии PG trust artifacts. Для неизвестного/старого TLS
профиля нужен отдельный migration kit; автоматически считать его plaintext нельзя.
Для format3
поле обязательно; malformed/extra/missing/hash mismatch отклоняются. Старый kit
format3 не читает. Это отдельный backup compatibility gate, не auto-migration.

Обычный restore требует тот же PG TLS mode, CA/CRL hashes и CRL policy; Secret
names/server leaf могут отличаться для target namespace при валидной цепочке/SAN.
Проверка целевого trust использует текущее время, а не дату создания backup.
Истёкший CA/CRL не делает данные невосстановимыми: предлагаемый отдельный
`--postgres-tls-transition <protected.json>` связывает SHA256 backup manifest,
исходный и целевой profile hashes, target context/namespace, срок действия решения
и reason `restore_reissue`/`ca_rotation`/`crl_refresh`. Это явное решение оператора
о новых доверенных материалах; один файл с hashes не доказывает их доверенность.
Перед import проверяются совпадение всех привязок, новый valid native trust и
Secret UID; никакого `skip-verify`, TLS downgrade или required-CRL→none.
Решение также привязывается к фактическому target namespace UID, чтобы пересоздание
namespace или смена context на другой кластер требовали нового решения.
Документ перехода не содержит keys/passwords, его hash входит в результат restore.
Старый архив проверяется на byte integrity без требования актуальности старой PKI.
Контролируемый переход обязателен для TLS disaster-recovery приёмки; пока он не
реализован, TLS release gate остаётся открыт. Изменение других policy выполняется
отдельным upgrade после восстановления.
PG private key восстанавливается из защищённого provisioning, не из backup.

Формат решения version1 принимает ровно поля `format`, `backup_manifest_sha256`,
`source_profile_sha256`, `target_profile_sha256`, `target_context`, `target_namespace`,
`target_namespace_uid`, `expires_at`, `reason`. Hash profile вычисляется как SHA256
UTF-8 JSON (`sort_keys=True`, `separators=(',', ':')`, `allow_nan=False`) с ровно
`mode`, `ca_sha256`, `crl_sha256`, `crl_required`. Для disabled mode=`disabled`,
hashes=null и crl_required=false; для TLS mode=`verify-full`, CA hash обязателен,
CRL hash обязателен при crl_required=true, иначе null. Hashes —64 lowercase hex chars.
Legacy unknown не получает фиктивного hash и не проходит этот transition.
Файл решения ≤16KiB, read N+1 до JSON parsing. `expires_at` — UTC RFC3339 с Z,
проверяемый aware datetime. Неизвестные поля,
дубли JSON keys, NaN, bool вместо format integer и некорректные hashes отвергаются.
Validity/expiry перепроверяются перед import, migration и start; системное время
стенда и controller сверяется до PKI приёмки.

Для format3 byte hashes обязательны также у public CA/CRL; PEM parsing не
нормализует CRLF. CA KeyUsage/BasicConstraints, leaf EKU serverAuth (при наличии),
key match, CRL issuer/signature/thisUpdate/nextUpdate проверяются отдельно;
native verification проверяет всю цепочку, включая intermediate/revocation.
Без CRL проверка отзыва не обещается. Владельцы PKI задают сроки/оповещения и
доступность issuer вне приложения; это prerequisite эксплуатации, не контроллер
автовыпуска сертификатов. RPO/RTO измеряются с восстановлением доступных ключей
и выпуском replacement leaf, а не только временем импорта dump.

`pg_dump`/`pg_restore` остаются локальными socket-операциями внутри PG Pod.
SQL metadata/integrity/outbox/migration в backend Job используют TCP verify-full;
отдельно проверяется весь restore с фактическим encrypted TCP. Нельзя объявлять
pg_dump proof доказательством TLS клиентского соединения.

## Порядок реализации и критерии выхода после согласования

1. Operator profile/PEM validation и redaction, exact limits N/N+1, schema3 kit
   compatibility. Сначала owning RED, затем реализация и GREEN без skips.
2. Chart server/client/HBA/env плюс inheritance всех Jobs; disabled render и
   legacy old-template backup остаются прежними. Read-only Secret/mode/fsGroup,
   conflicting config, missing CRL и unsupported auth inputs имеют owning tests.
3. Backup format3/legacy2 и restore identity: TLS downgrade/change отклоняются
   до lock/import, private materials отсутствуют в archive и logs.
4. На одноразовой установке с синтетической PKI: valid chain+SAN+SCRAM, неверный
   CA/SAN/password, expired certificate/CRL, revoked leaf, key mismatch, plaintext
   TCP refusal, old Secret исчез/заменён, leaf/CA rotation, failed-upgrade recovery.
   Подтвердить `pg_stat_ssl`, SQL commit, denied audit response→DB→stdout и <5s
   выбранный audit failure budget. Полный shared-engine startup не получает
   утверждения о таком же latency budget без отдельного измерения.
5. Реальные migration, backup, непустой restore в новый namespace, matching
   identity/totals, application CRUD/BM25/stream/restart. Минимальный home proof
   выполняется последовательно, с проверкой RAM/disk и exact owned cleanup.
6. Приёмка вместе с temp sizing/disk-pressure, актуальным dependency audit,
   project checks/coverage и release packaging. Только потом обновление home
   приложения; целевая платформа/CSI и реплики остаются отдельными gates.

Frozen v16 file-only Secret proof не закрывает этот контракт. Native TLS tests
на обычных файлах не закрывают actual projected Secret + shipping chart.
Боевые реквизиты, действующая инфраструктура и пользовательские PVC не участвуют
в fixture. Local launchers и оба Compose режима должны пройти regression.

### Retention и cleanup версий HBA

`helm.sh/resource-policy: keep` сохраняет HBA после обновления/удаления release.
Это не бессрочная retention и не разрешение массового удаления по label.
Оператор завершает backup/rollback retention и активные maintenance операции,
затем проверяет, что старая версия не нужна retained checkpoints/recovery kits
и не упоминается в StatefulSet templates, Pods (включая Pending/Terminating) или
Jobs. При неполном inventory cleanup запрещён. Повторно проверяются namespace
UID, release ownership, ConfigMap UID и exact data hash. Удаляется только
конкретная версия с API preconditions UID/resourceVersion; при конфликте
проверка начинается заново, без force или удаления по одному имени.
[DeleteOptions preconditions](https://kubernetes.io/docs/reference/kubernetes-api/definitions/delete-options-v1-meta/).
Server/client Secret versions очищаются отдельно по тем же reference/retention
критериям и их явному владельцу. Cleanup здесь описан как operator runbook;
автоматической процедуры удаления в текущем kit нет.


### Реализованный промежуточный контракт T3, 05.10.2026

Static/UID/material binding и native pre-migration gate реализованы. Проверка
desired profiles принимает стандартный Kubernetes API defaultMode0644 только
у HBA ConfigMap volume; Secret projections остаются0440. Public HBA hash и
immutable UID проверяются независимо. TCP Job использует только client CA/CRL,
проверяет libpq18/verify-full/SCRAM/pg_stat_ssl и actual server leaf. Native Job60s,
controller wait90s, request30s/client≤60s; поздний succeeded не продлевает wait.
Unit ordering failures сохраняют lock и запрещают следующий writer.

Operation checkpoint/recovery ещё не реализованы. До backup3 support installed
TLS upgrade/backup отказаны до lock/stop, legacy format2 в TLS target — до import.
Это защитное промежуточное ограничение, а не заявленная поддержка TLS lifecycle.
PG-only actual proof и operator398 PASS не закрывают полный deploy/restore,
interruption, hosted/platform или audit failure-budget gates.

### Durable checkpoint foundation, 06.10.2026

Checkpoint schema2 хранится в namespaced `okf-pg-operation-<operation-id>`
ConfigMap. Доступ на запись — только у maintenance operator по RBAC; это не
подпись данных и не защита от администратора с правом произвольной записи CM.
Формат ограничен64KiB, имеет exact fields/unique JSON keys и запрещает NaN,
bool version, raw env/templates/private material. Secret refs/UID и public hashes
не заменяют сам Secret; ключи не копируются. API updates — replace с UID и
resourceVersion; namespace/lock identity проверяется повторно после inventory.

Журнал создаётся после acquire и до stop/изменения storage. Lock указывает на него
уже при создании: неполная запись/потеря ответа не дают права разблокировать TLS
операцию. Prepared → stopped → backup_complete при старой app → storage_changing
→ storage_ready → native_verified → migration_started → migration_complete
→ start_started → completed. Provision имеет отдельную ветку без migration.
Фаза migration_started сохраняется **до** вызова writer: при interruption она
значит may_have_started, а не доказанный rollback-safe no-op.

Фиксируются namespace/lock/CM/StatefulSet/PVC/Pod IDs, hashes template и текущего
chart tree, отдельный hash ordered values inputs. Перед фазами и непосредственно
перед storage/start перепроверяются current TLS materials. PVC/controller
replacement или template mutation вне storage boundary отвергаются. При смене
template старый Pod должен выйти, единственный новый PG Pod — Ready. Changed
chart/values и stale local checkpoint после lost reply не продолжают цепочку.
Selected TLS bounds: ≤128 chart files/8MiB, ≤16 values files/8MiB; это ограничения
checkpoint inputs, disabled Helm path не получает новый values-count gate.

Installed chart tree/artifact digest неизвестен без saved old release/kit:
installed Helm manifest hash записывается отдельно, не выдаётся за artifact SHA.
Неприкреплённый image tag сохраняется с digest=null. Эти unknown inputs не дают
права на возврат old writer. Первый unreleased schema1 proof уже очищен; schema1
отвергается, missing values identity не домысливается. Это независимая версия от
operator kit3 и будущего backup3.

Readonly `operation-status` требует context/namespace/release/operation ID, не
требует values и не делает SQL/mutations. Неполный TLS journal не обслуживается
обычным recover-lock; исчезнувший указатель при сохранившемся journal и legacy
TLS state без journal также отвергаются. Conditional UID/RV deletion lock доступен
только после terminal completed/provision_complete. Journal остаётся в namespace
после unlock; retention/cleanup требует отдельной проверки ссылок/checkpoints и
операционных нужд, удаления только конкретного UID/RV, без bulk label cleanup.

Доказан journal API/CAS/CLI/read after controller replacement и PG Pod restart
на одноразовом PG-only стенде. Dedicated old-storage recovery, реальные pipeline
interruptions/migrations/backup3/restore и hosted/platform proof ещё OPEN.


### Offline recovery package integrity, 06.10.2026

`verify_recovery_package` и отдельный CLI в `release.py` проверяют локальный
сохранённый schema3 пакет до любых recovery mutations. Expected release SHA256
и полная revision должны поступить независимо из protected promotion. Проверяемые
файлы не являются источником доверенного expected SHA; администратор обязан
обеспечить эту границу. Внешний digest связывает release с exact chart artifact,
operator archive, toolbox digest и pinned backend/frontend/stub refs.

Inputs читаются по одному bounded snapshot: release≤64KiB, chart/archive≤8MiB;
symlink files, duplicate/unknown fields, bool formats, fractional schema, NaN,
mutable image digest и credential-bearing archive URL отклоняются. Chart имеет
bounded project allowlist без traversal/links/duplicate/private files. Current
trusted bootstrap проверяет exact kit members и внутренние hashes как bytes;
старый bootstrap/lifecycle не исполняется. Нет extraction, writes, SQL, Kubernetes
API или автоматического unlock. Вывод содержит только public digests/revision/refs.

Это artifact integrity, **не** подпись пакета, native certificate proof,
installed-generation match, API-default normalization или permission вернуть old
writer. Возвращать storage/снимать lock по этому verdict нельзя. Schema2 recovery
здесь не поддержан и отказывает; existing legacy consumer fixtures остаются
отдельным контрактом. Dedicated recovery по operation ID и policy после возможного
начала migration остаются OPEN; checkpoint schema2 и operator kit3 не меняются.


### Saved generation binding, 06.10.2026

Readonly `postgres-recovery-check` требует operation ID, явные context/namespace/
release, saved schema3 release/kit/chart, saved Helm manifest, ordered old values,
protected expected release SHA/revision и saved toolbox digest. Загружается bound
journal с существующим lock; fresh/provision/terminal или possible migration
отказывают до Helm render. Installed application/PG UID, manifest hash и pinned
images обязательны. Artifact verification выполняется current trusted кодом.

Old chart локально рендерится Helm template, timeout60s, без upgrade/rollback/
application.enabled override. Saved manifest byte hash должен совпасть с journal;
все ресурсы и поля render должны совпасть со saved manifest. Игнорируются только
YAML comments и порядок документов; порядок массивов, типы и значения полей сохраняются.
Manifest≤2MiB/128 resources, duplicate keys/resources, aliases, wrong namespace,
Secret/Namespace/List documents отказывают. Ошибки не возвращают raw templates.

Known controller-template API defaults1.37 заполняются явно: DNS/restart/scheduler,
grace30, serviceAccount alias, termination message fields, digest image pull policy,
resources empty object, port TCP, probe defaults/HTTP scheme, omitted zero initial
probe delay и Secret/ConfigMap mode0644. Уже заданные значения сохраняются; другие
поля не удаляются. Hash **всего** нормализованного template сравнивается с installed
API template hash. Unknown API/admission changes требуют явной доработки и proof;
это не общая нормализация любых Kubernetes/CRD/workload. Actual API dry-run подтвердил
точное совпадение Deployment/application и StatefulSet/postgres текущего chart.

Сверяются backend/frontend/PG image refs с journal и release, init images — с
pinned release; model-stub image при наличии — с release. Old TLS projection refs,
CRL policy и HBA hash должны совпасть с journal. Это не проверка actual current
Secret UID/expiry/revocation или native connection: они остаются отдельными gates.
TLS profile absent не является legacy backup/plaintext inference.

После render повторяются bounded file/ordered-values hashes и namespace/lock/
journal UID/body binding. Возврат только public identity; обязательный
`storage_recovery_authorized: false` не позволяет использовать check как mutation
или unlock. Generation proof использует saved metadata; current PG/PVC/Pod/writer
identity ещё нужно проверять dedicated recovery. Исходный journal schema2/kit3
не меняется. Schema2 artifact recovery и SQL policy после migration остаются OPEN.


### Current old TLS static prerequisite, 06.10.2026

`--check-old-tls-materials` допустим только у readonly `postgres-recovery-check`
и исполняется после verified package/render/installed-generation binding.
Текущие Secrets читаются по старым refs; exact public identity, включая Secret
UIDs, сверяется с journal, время проверки актуальное UTC. Просроченный материал,
неверная подпись CRL или известный отозванный сертификат server chain отказывают.
CRL serial сравнивается только для сертификата с соответствующим issuer.
Delta/indirect/scoped CRL не интерпретируются как complete CRL: здесь отказ,
поддержка требует отдельного дизайна/native proof. CRL-disabled допускается
только при явно сохранённом таком профиле.

Current HBA обязан иметь exact content/hash и namespace/release ownership.
Его UID закрепляется между двумя чтениями текущей inspection; journal2 содержит
исторический HBA hash, но не UID, поэтому историческая непрерывность UID не
заявляется. Оба TLS Secrets и HBA проверяются повторно. Затем повторяются
protected files/ordered-values и namespace/lock/journal identity. Это bounded
readonly inspection, не атомарный API snapshot и не разрешение следующей mutation.

Public verdict: old_tls_materials_validated=true, old_tls_native_verified=false,
storage_recovery_authorized=false. Native old-leaf/verify-full/SCRAM, current
writer/storage binding и durable recovery phases остаются обязательными.
Отсутствие старого managed TLS не запускает plaintext fallback. Схемы checkpoint2,
kit3 и existing backup guards не меняются. Локальная проверка текущего изменения
Windows69/Linux554 PASS без skips; native recovery текущего изменения ещё не
подтверждено. Linux unit proof не заменяет current writer/storage/native gates.


### Current-state readonly prerequisite, 06.10.2026

Флаг --check-current-recovery-state только у postgres-recovery-check; подразумевает
old TLS static gate и полный saved package/generation gate. Два полных namespace
GET без selectors включают standard workloads и PVC. Ответ8MiB/256 objects,
duplicate/NaN/continuation/deep JSON отказ; stdout reader N+1 с timeout60s,
request30s и bounded cleanup1+1s, stderr подавлен, временного inventory файла нет.
Сравнение всех текущих specs/status/UID между проходами выполняется в памяти и
не публикует inline credentials, команды или их hashes. Между проходами повторён
old TLS/HBA, после — files/values/namespace/lock/journal. HBA current UID закреплён
на всё время inspection; historic HBA/Qdrant UID в journal2 не добавляется.

Поддерживается только TLS-only PG template recovery: текущий PG UID совпадает с
installed/observed; old template hash соответствует saved generation, canonical
TLS refs выбираются из old/desired journal. Из полного нормализованного сравнения
исключаются только уже строго проверенные TLS server args/mounts/volumes.
Остальные поля, PG image/PGDATA/security/env/PVC не игнорируются. Изменения этих
полей требуют отдельной storage migration/repair. New Pod UID/template без observed
binding разрешён только в storage_changing; неготовый PG возможен именно как
предмет recovery, а не причина отменить old trust validation.

External PVC имена exact saved/journal, UID неизменны, каждый Bound/volumeName,
без deletion. PG/Qdrant0/1; unsupported ordinals/volumeClaimTemplates отказывают.
Qdrant template соответствует protected saved/API-normalized template. Storage
Pod name/StatefulSet controller UID/containers/volumes/security точные; ephemeral
containers запрещены. Application/model-stub отсутствуют или fully stopped с
observedGeneration и status0. Остаточные RS0 с known owner; Job terminal/no active,
Job Pod terminal и regular/init container exit подтверждён. Unknown Pod/controller,
DaemonSet/CronJob отказывают. Для stub home profile также нужен stopped model-stub.

Public verdict current_application_writers_absent/current_storage_identity_verified
true, old_tls_native_verified false, storage_recovery_authorized false. Это проверка
supported standard topology по API; CRD/external operator/будущие scale/create не
fenced, физический выход Pod не доказан. Recovery claim/CAS, подтверждённая остановка
исходного pipeline и отсутствие in-flight Helm, graceful PG exit и native old TLS
после возврата остаются отдельными обязательными gates. Возможная migration никогда
не сбрасывается. Никакие SQL, scale/apply/delete/unlock этот check не выполняет.

Проверено Linux634/Windows149 без skips; real1.37 defaults и bounded namespace List
команда проверены через server-side dry-run/GET с persistent objects0. Historical
checkpoint UID/PVC/status синтетические: полноценных interruption/native recovery
proof этим не заявляется. Checkpoint2/kit3/backup guards не меняются, full T3/T4–T6 OPEN.

## Current runtime recovery inspection (06.10.2026)

Отдельный `--check-current-runtime` включает old generation/TLS/current-state,
затем повтор state/runtime и protected operation binding. Backend exact envFrom
ConfigMap→Secret, PG password из того же Secret/key, no prefix/optional/extra
sources. ConfigMap data соответствует protected old manifest; runtime Secret
Opaque/nondeleting/name/namespace/UID, strict base64/UTF8, URL/password/session
format валидны. Config/Secret key overlaps, controlled PG/TLS env, inline
credentials, inline HTTP verification disable и unsupported envFrom отказывают.

Каждый API GET bounded2MiB/N+1/60s/request30s; JSON duplicate/NaN запрещены.
Два чтения и повторные passes фиксируют UID/private body/type/immutable только
в памяти, без public hash. RV/managedFields bookkeeping не является private body.
Selected HTTP bundle проверяется по независимому protected expected public hash;
immutable ConfigMap/единственный PEM key/current CA validity/UID freeze. Bundle
отсутствует → explicit selected_http_trust_validated=false; hash без selected
bundle или без runtime check отказывает. Hash из текущего GET не является anchor.

Runtime static verdict не доказывает историческое содержимое Secret, actual SQL
password/role, HTTPS path или доступность OIDC/LLM. Checkpoint2 не содержит runtime
private baseline. Перед old writer обязательно protected Secret/VSO version
provenance для session key или отдельно согласованная ротация; длина ключа не
доказывает историческую идентичность. Native old SCRAM остаётся обязательным.
Public output только flags, old_runtime_native_verified/storage_recovery_authorized
false. Linux693/Windows208 без skips проверяют exact344 source; hosted CI/native
runtime и полная recovery матрица OPEN. Никакие SQL/scale/apply/unlock не выполняются.

## Recovery ownership foundation (06.10.2026)

Отдельная explicit claim операция принимает protected stop record version1:
exact binding/context/nsUID/release/operation/lockUID, checkpoint UID/SHA и
original_pipeline_stopped=true. Record regular/4KiB/strict JSON, никаких raw
commands/URLs/учётных данных. Оператор подтверждает фактическую остановку CI/
процесса; автоматический cancel adapter этим не предоставляется. Full static
package/generation/old TLS/current-state/runtime и pending Helm checks обязательны.

Lock CAS UID/RV сохраняет bounded public schema1 postgres_recovery (phase claimed,
recovery ID, checkpoint/binding hashes, public stop/release/values SHA, Helm
revision/status). Journal2 и migration state не меняются. Status/inputs/record/
namespace/journal/lock повторяются до/после CAS; lost reply retains lock без
retry/delete/adoption. Phase claimed не позволяет scale/apply/SQL/unlock.

Original TLS operator в текущем kit проверяет maintenance lock перед dispatch
каждого kubectl/Helm effect; claim/ownership loss отказывает. Auth reconcile
mutating, auth can-i/whoami readonly. Даже claim owner в этом этапе не получает
storage effects. Ordinary unlock/recover-lock отказывают при любом marker;
operation-status читает bound recovery ID/phase после перезапуска без adoption.

Cooperative fence не отменяет in-flight Helm/subprocess и не защищает от older
kit/external actor/CRD-controller. Marker не удаляется, TTL отсутствует. Actual
остановка original operator/CI и отсутствие pending/in-flight операций остаются
предусловием. Resume по recovery ID/новые durable phases ещё не реализованы.
Graceful captured PG exit, same storage/new Pod UID и native old TLS/SCRAM должны
предшествовать любому terminal/unlock/writer; migration state не сбрасывается.

Linux755/Windows270 без skips и actual1.37 native ConfigMap CAS/fresh reader/
stale-RV refusal/owned cleanup проверяют ownership foundation. API fixture
использует synthetic TLS/runtime/Helm release gates; native old storage recovery
и target acceptance этим не подтверждены. Full T3/T4–T6 остаются OPEN.

## Recovery-ID resume / durable stop intent (06.10.2026)

Resume принимает original operation и existing recovery ID, protected old inputs
и прежний stop record. Current namespace/lock/checkpoint UID/SHA и record binding
точные; release/ordered-values/stop SHA, Helm revision/status неизменны. Full
old generation/TLS/writer/storage/runtime checks и owner/input/status repeat
не являются разрешением mutation. Resume readonly, storage_recovery_authorized=false.

Prepare-stop выполняет два resume passes, фиксирует public recovery marker schema2
phase stop_prepared через lock UID/RV CAS. Marker1 claimed readable, checkpoint2
не изменяется. Новые поля marker2: observed (PG UID/template SHA, Pod UID/name/owner/
ready, PVC UID/name) и http_ca_sha256|null. Captured binding immutable при resume;
Ready может меняться, не являясь identity/native proof. Repeat prepare отказ,
lost response требует инспекции, no blind retry/delete/adoption/TTL.

CAS permit содержит только exact request и public marker YAML bytes, очищается
finally; изменённые bytes/другая команда и scale/exec/apply/unlock отказывают.
Stop intent сам PostgreSQL/PVC/Pod не меняет. Linux797/Windows312/0skip и real1.37
marker CAS/new-reader/duplicate refusal/owned cleanup подтверждают metadata-only
foundation; static/Helm/storage/runtime gates API fixture являются doubles.

Следующий step должен записать stop_requested **до** conditional PG stop, затем
доказать физический graceful выход captured regular/init containers. NotFound
без termination proof при partition/unreachable/force delete недостаточен.
Предусмотреть наблюдение terminal state до исчезновения объекта и node/lease/
CSI fencing prerequisites; при неопределённости не создавать новый storage writer.
Durable stop-complete/restore/native old TLS/SCRAM и terminal/unlock остаются OPEN.
Session-key provenance/rotation policy и full T3/T4–T6 не закрыты.


## Captured stop protocol (06.10.2026)

Recovery marker3 (отдельно от checkpoint2/kit3) добавляет public stop record:
Node name/UID, Lease UID, owned recovery-ID finalizer, grace≤150s, regular/init
names и proof=null. Requested CAS до effects; completed CAS с bounded terminal
name/exit_code/signal proof до finalizer removal. Старые markers1/2 readable.
Original migration journal не переписывается: readonly stop overlay требует
same PG template/UID/PVC, replicas0 и captured Pod либо durable completed proof
его выхода при отсутствии. Namespace-wide no-writer/runtime/trust gates остаются.

Transport: fresh bounded GET → local exact tests → `kubectl replace -f -` с
исходным UID/RV. Полный объект сохраняет unknown fields; тело/его private hash
не логируются и не передаются argv. Finalizer защищает captured Pod status от
удаления до proof; force delete не используется. Existing marker3 никогда не
повторяет stop dispatch. Поля owner/context/inputs/Helm и scope permit exact;
permit очищается finally. Прерывание между intent и dispatch требует отдельного
repair и не разрешает новый writer. Status NotFound без completed proof отказ.

Healthy same Node +fresh owned Lease обязательны, но не заменяют CSI/partition
fencing целевой среды. Regular containers ready=false/terminated; init ready=true
возможен после успешного завершения, поэтому для него проверяется terminated.
Unknown statuses, sidecar init, SIGKILL/137, missing captured Pod или changed UID
блокируют completion. Completed cleanup снимает только собственный finalizer
по UID/RV; затем PG0/absence и full gates повторяются. No SQL/app/unlock effects.

Actual1.37/PG17.11 emptyDir native stop подтверждает regular/init exit0, Node/Lease,
intent/proof ordering, new observer и cleanup; package/TLS/runtime/Helm gates lab
синтетические. Exact347, Linux850/Windows365/0skip. Native old-template/PVC TLS
recovery, pre-dispatch repair, CSI/node failures, session-key provenance и terminal
protocol остаются OPEN. stop_verified=true не разрешает storage recovery;
installedTLS backup3 guard, full T3/T4–T6 сохраняются.


## Stopped old-template restore protocol (06.10.2026)

Marker4 (отдельно от checkpoint/kit/backup) расширяет completed stop record полем
restore={old_template_sha256}; phases template_restore_requested→template_restored.
Original stop proof/observed binding неизменны. Old template берётся из bounded
protected saved Helm manifest, API defaults нормализуются и exact installed SHA
обязателен. No old code execution/raw-template journal/private-body logging.

Requested CAS предшествует единственной template mutation. Full gates/ownership/
files/PG0/captured UID/Pod absence/Node Lease повторяются. Scoped ephemeral permit
связывает old hash и StatefulSet UID; conditional replace stdin сохраняет все
non-template fields. Повтор requested marker только проверяет применённый target;
неприменённый dispatch остаётся pending с retained lock. Completed CAS после exact
old template/PG0 verification, затем full gates/absence повторяются. No SQL/start/
unlock; original migration checkpoint не переписывается.

Readonly stop overlay для marker4: PG0, same UID/PVC, no Pod; requested допускает
captured или installed old hash, completed — только old hash. Changed target hash,
unknown template/possible migration и любой current writer отказывают. Это не
авторизация PG start. Start intent/new Pod/native old TLS/SCRAM/SQL/CSI fencing
и terminal/app/session-key protocol остаются отдельными OPEN obligations.

Actual1.37/PG17.11 disposable PVC доказывает real stop+UID/RV stopped-template
replace, same StatefulSet/PVC/PV, no new Pod и owned cleanup. Native saved/TLS/
runtime/Helm gates — fixture doubles; old template annotation синтетическая,
actual TLS projection rollback не проверен. Exact348/Linux878/Windows393/0skip;
full T3/T4–T6 и installedTLS backup3 guard сохранены.


## Bounded old-PG start protocol (06.10.2026)

Marker5 расширяет marker4 полем start={fencing_sha256,requested_at,new_pod}; phases
start_requested→pod_observed→storage_ready. Requested CAS до единственного
replicas0→1 UID/RV replace с exact old template/StatefulSet; scope permit очищается
finally. Existing marker5 только наблюдает, pending PG0/lost replies не повторяет
команду. Original checkpoint/migration и durable terminal stop proof неизменны.

Независимо защищённый storage-fencing-record обязателен: strict public JSON≤16KiB,
exact operation/checkpoint/recovery/template/stop-proof/PVC/PV identities, timestamps с timezone, TTL
≤900s, external_actors_fenced/exclusive_storage_access_confirmed=true. Actual Bound
PVC→PV claimRef/UID и non-deleting identity повторяются. Решение актуально перед
scale; observer сверяет неизменный SHA и validity на recorded requested_at. Это
операторская/platform attestation, не автоматический CSI/partition fence. Перед её
выдачей нужен фактический accepted fencing; непроверенный доступ/unknown actors
не допускают affirmative boolean. PV get — отдельный cluster-scoped RBAC prerequisite.

Первый наблюдённый new Pod UID/owner сохраняется до Ready, исчезновение/подмена
отказывают. У unscheduled Pod node fields null; при назначении Node healthy owned
Lease/UID связывается немедленно, также до Ready. После binding node/lease identity
неизменны. Pod containers/init/volumes/security exact old template; Ready требует
Running/all regular running+ready и init exit0. Deadline300s, GET≤30s/2MiB; full
protected gates имеют отдельные bounds, не whole-command SLA. RWO/Ready не fence.

storage_ready не доказывает native TLS/SCRAM/SQL/application integrity и не даёт
writer/unlock. Actual1.37/PG17.11 proof подтверждает stop/restore/new Pod на same PVC/
PV и synthetic SQL42 continuity; package/TLS/runtime/Helm gates doubles, template
annotation и lab fencing attestation. Exact349/Linux918/Windows433/0skip; actual
old TLS, platform failure matrix, pre-dispatch repair, session-key provenance и
terminal/unlock/full T3/T4–T6 остаются OPEN, installedTLS backup3 guard сохранён.


## Native recovery probe foundation (06.10.2026)

Fixed command выполняет отдельное свежее native подключение с принудительными
libpq verify-full/sslrootcert/sslcrl/require_auth=scram-sha-256, gssencmode=disable,
sslcertmode=disable и TCP postgres:5432. SQL options statement_timeout=3000 и
default_transaction_read_only=on; observed pg_stat_ssl, used_password, SCRAM
verifier, transaction_read_only=on, SELECT1. Отдельный certificate handshake
сверяет actual server leaf SHA с public installed leaf. Это проверка подключения,
не application schema/totals/FS/Qdrant consistency. Exceptions выходят только
как {ok:false}; credentials и native provider errors не сериализуются.

Public verdict bytes≤1024, strict exact flags tcp/tls/scram/sql_readonly/
sql_roundtrip=true и server_leaf_sha256; duplicates/NaN/nonbool/private keys
отказывают. Helpers не меняют marker5, не дают create Job/exec/writer/unlock.
Production native integration требует нового durable intent, owned Job UID/
exact command+template+image/current client trust/new PG Pod binding до допуска
observer; full inventory no-writer barrier должен знать только этот конкретный
readonly Job. Lost reply/partial creation/terminal failure не дают redispatch;
completed logs не заменяют повтор trust/runtime/PG identity. Protocol OPEN.

Actual lab меняет current server-new→old server-old projection при PG0, затем
запускает new Pod на same PVC/PV и native Job с CA/CRL root:1000:0440/read-only,
server key root:999:0440. Verify-full/SCRAM/read-only SQL PASS; wrong CA/password/
expected leaf refused; synthetic SQL42 preserved. Frozen backend source274 и
image identities проверены. Recovery package/generation/runtime/Helm gates —
doubles, physical platform fencing — operator lab attestation. Native Job
completion ещё не записывается в recovery journal. Exact350/Linux944/Windows459/
0skip; schema compatibility/integrity/session provenance/terminal/full T3/T4–T6 OPEN.


## Durable native preparation protocol (06.10.2026)

Marker6 имеет только phase native_prepared и native={job_name,
job_template_sha256,server_leaf_sha256}, наследует full marker5/stop/restore/start
bindings. Checkpoint2/kit3/backup3 не переименовываются. Public normalized Job
proposal SHA построен из bounded protected old saved manifest: pinned installed
backend/image reference, whole old client/server profile/HBA, fixed read-only
native command; application data PVC and mount readonly; no frontend/server key/
service-account token. Job deadline60/backoff0/restartNever. Proposal body/values
и credentials не сохраняются в marker и не логируются.

Prepare требует marker5 storage_ready или existing6 native_prepared. Exact new
PG Pod/Node/Lease/old template/PVC/readiness, protected ownership/files/trust/runtime
повторяются; reserved Job absent. One UID/RV CAS after two full passes, post-CAS
full repeat; lost reply имеет observer-only completion без повторного CAS/create.
Fresh marker6 resume пересчитывает proposal SHA/leaf и повторяет storage gates.
Readonly local overlay6→5 storage_ready не меняет original journal; unknown
active Jobs/Pods остаются запрещены. Original dispatch fence не ослаблен.

native_job_prepared=true/native_job_created=false/old_tls_native_verified=false/
storage_recovery_authorized=false. Это не native completed verdict. Exact351/
Windows490/Linux975/0skip; actual1.37/PG17.11 restored TLS +metadata marker6 CAS
на disposable PVC проверены, full package/runtime/Helm recovery gates doubles.
Production create-intent/Job UID/active readonly overlay/verdict/cleanup protocol,
application SQL/session-key/terminal/unlock/platform acceptance/full T3–T6 OPEN.

## Durable native dispatch protocol (06.10.2026)

Recovery marker7 наследует marker6/stop/restore/start bindings; native содержит
job_name/job_template_sha256/server_leaf_sha256/job_uid/requested_at.
native_create_requested требует job_uid=null; native_job_observed требует exact
UID. requested_at — bounded aware timestamp. Strict public JSON≤4KiB, private
Job body/credentials не сохраняются. Старые markers1–6 readable.

Dispatch после two full protected passes фиксирует create intent, повторяет
owner/files/old profile/new PG UID/node/Ready/PVC/runtime/trust/fencing и только
потом разрешает один exact create argv+JSON stdin. Permit исчезает finally.
Existing7 не повторяет create ни при missing Job, ни при timeout/lost response.
В ответе create допускается только exact public object name; UID/spec получают
отдельным bounded read. First observed Job UID проходит strict owner/proposal
validation и повторные actual reads/full gates перед public UID CAS.

Transient readonly inventory overlay при capture использует actually read UID,
проверенный against exact reserved intent/lock owner/checkpoint/proposal, и не
сохраняет фиктивный completed verdict. После UID CAS только exact captured UID.
Native Job и его единственный Pod сверяются целиком с fixed old-client readonly
template: API-generated controller labels/selector и known API/scheduler defaults
принимаются строго. Unknown admission/spec changes не игнорируются. Job
parallelism/completions1, policy Failed, backoff0/deadline60/restartNever/no token;
readOnly application PVC/mount, no frontend/server private key. Unknown active
Jobs/Pods/controllers проходят прежний gate и отказывают.

Job owner — existing lock ConfigMap UID; annotation — original checkpoint SHA.
Local filtered inventory не изменяет persisted checkpoint/migration. Full
inventory instability during ordinary Job/Pod status transitions может отказать
без повторного dispatch. Next observer сохраняет lock и exact UID binding.
Markers5/6/7 также сверяют original fencing body SHA: другой valid attestation
или PV identity не заменяет journaled decision.

native_job_created=true/old_tls_native_verified=false/storage_recovery_authorized=false.
Ни Job Complete, ни raw logs сами по себе не закрывают native terminal/completion,
application SQL compatibility/integrity/session-key, writer или unlock.
New42/Windows532/Linux1017/0skip, actual1.37/PG17.11 lost reply+fresh UID observer
без второго Job и current Job/Pod template/native readonly proof проверены.
Full package/runtime/Helm gates native fixture doubles, data emptyDir/PG PVC,
physical fence lab-only; next terminal/verdict/cleanup and full T3–T6 OPEN.

## Durable native terminal verdict protocol (06.10.2026)

Marker8 наследует marker7 и native расширяет полями pod/verdict. pod имеет exact
name/uid/node_name/node_uid/lease_uid. native_pod_observed допускает ещё unknown
node fields только вместе с node_name=null и verdict=null; native_verified
требует scheduled Node/Lease IDs и exact public verdict={tcp,tls,scram,
sql_readonly,sql_roundtrip,server_leaf_sha256}; flags строго bool true, leaf равен
installed recovery leaf. Strict bounded public JSON≤4KiB, raw logs/SQL/private
material не сохраняются. Старые markers1–7 readable, checkpoint2 неизменен.

First observed Pod UID записывается CAS до terminal wait; late first Pod можно
ждать90s с repeated protected prerequisites без повторного Job create. Сразу
после scheduling записывается Node/Lease identity; замена/исчезновение captured
Pod/Node/Lease не принимается. Локальный reader overlay требует exact Job UID,
captured Pod UID/name/owner/template и полный original namespace uniqueness.
Known scheduler defaults принимаются строго, unknown executable changes отказывают.

Native verification требует Job Complete+succeeded1/no active/failed и actual
Pod Succeeded, one operation terminal exit0/signal0/reason Completed без init или
ephemeral. NodeReady/fresh Lease и original new PG UID/old profile/Ready/PVC,
old trust/runtime/full ownership/file gates повторяются перед и после bounded
logs. Public logs≤1024/strict flags проверяются отдельно, поздний положительный
ответ не расширяет90s controller deadline. native_verified CAS только после
проверенного terminal/native verdict; lost response сохраняет record для fresh
observer exact identity/terminal/gates без повторения logs/SQL/create.

old_tls_native_verified=true/storage_recovery_authorized=false. Job пока остаётся
для subsequent scoped cleanup, app writer/unlock не разрешаются. Actual
marker8/native/lost CAS/fresh observer proof на1.37/PG17.11 и34 new cases проверены;
full package/runtime/Helm gates doubles, app data emptyDir/PG disposable PVC,
platform fence lab-only. Application schema/integrity/session, cleanup, terminal
app/unlock/full T3–T6 остаются OPEN.


## Durable owned native cleanup protocol (06.10.2026)

Recovery marker9 inherits marker8/stop/restore/start/native bindings; native adds
cleanup={job_resource_version,pod_resource_version,requested_at}. Strict public
JSON<=4KiB, both RV nonempty bounded identifiers, aware timestamp; scheduled
captured Pod/Node/Lease and exact true native verdict mandatory. Phases
native_cleanup_requested/native_cleaned. Markers1–8 remain readable;
checkpoint2/kit3/backup3 unchanged, no credentials or private derivatives stored.

Verified8 cleanup repeats full ownership/files/protected generation/old trust/
runtime/PG Ready/PVC/fencing/native Job-Pod successful terminal gates. Capture
actual Job+Pod metadata/RV, two passes, persist cleanup intent with UID/RV CAS,
repeat, then permit ONE exact raw Job DELETE argv and public DeleteOptions stdin
with UID+resourceVersion preconditions/Foreground. Revoke permit finally; discard
DELETE response output. No label-selective/bulk delete, no Pod delete, no force.

Existing9 is observer only, including interruption before DELETE dispatch. A
pending intent that was never sent requires separate explicit repair, not retry.
Read-only GC comparison accepts valid aware deletionTimestamp and exactly the
normal foregroundDeletion Job finalizer, without changing original objects or
inferring exit from absence. Remaining captured Pod requires exact UID/name/node,
owner/template and successful terminal container proof. Unknown finalizers,
changed objects, reappearing cleaned identities fail and retain lock.

Only BOTH exact Job and captured Pod absence plus repeated full no-writer/
protected generation/trust/runtime/PG/fencing/owner/file gates allows cleaned
CAS and post-repeat. Public native_job_cleaned=true/old_tls_native_verified=true,
storage_recovery_authorized=false. Original checkpoint/migration and lock remain;
no app/session/writer/unlock permit follows from cleaned.

New31/Windows597/Linux1082/0skip; actual1.37/PG17.11 conditional foreground DELETE,
lost response and fresh observer, checkpoint/lock/PVC/SQL continuity checked.
Full generation/runtime/Helm native gates remain doubles; application data
emptyDir, physical fencing lab attestation. Application SQL/schema/session-key,
pre-dispatch repair, terminal/unlock and full T3–T6/backup3 guard remain OPEN.


User decision 06.10.2026: explicit APP_SECRET_KEY rotation is allowed during
recovery when historical key provenance is unavailable; all users must log in
again. A separately bound durable decision/verification is required before app
writer. No silent rotation or claim of historical key equivalence. Public durable
decision is implemented below; post-rotation login/in-flight OIDC acceptance remains OPEN.


## Protected explicit session rotation prerequisite (06.10.2026)

Readonly check accepts only validated marker9/native_cleaned and separately
protected rotation issuer record1 plus independent expected public record SHA.
Strict JSON<=16KiB/unique keys/no NaN/unknown fields. Exact binding/checkpoint UID
and SHA/recovery ID/rotation12hex/runtime Secret name-UID-RV. Bounded aware times
TTL>0<=900s, issuance skew<=5s, expiry current. key_rotation_completed,
all_sessions_reauthentication_required, previous_key_reuse_prevented strict true.
No key/password/provider path/private Secret body/hash is in the record/output.

Issuer actually generates/delivers fresh cryptographic key and prevents rollback
to prior active key; record is trusted external attestation, not inference from
current Secret GET. Product readonly controller derives Secret reference from
protected old backend manifest, repeats full recovery/generation/trust/runtime/
fencing/PG/namespace prerequisites, compares bounded current Secret UID/RV and
private body only in memory, rejects weak key/credential mismatch/inline TLS.
Public file/anchor/current Secret repeats; no permissions to rotate key/provider,
change metadata/SQL, start writer or unlock. Checkpoint/marker9 unchanged.

Public session_rotation_attestation_validated=true/reauthentication_required=true,
session_rotation_native_verified=false/storage_recovery_authorized=false.
Synthetic Starlette cookie/new-sign-in and actual OIDC profile HMAC mechanism
are checked; reusing previous key would revive its cookie. That is mechanism
acceptance, not actual production login/provider rollback or session DB proof.

New93/Windows690/Linux1175/0skip/source358/frozen274; home synthetic issuer replaced
only disposable immutable runtime Secret/key with DB credentials unchanged,
product readonly validation and wrong anchor refusal/unchanged lock/checkpoint/
Job count checked. Whole package/runtime/Helm gates doubles, application data
emptyDir/PG PVC, fencing/issuer synthetic lab-only. Durable decision/observer is specified below;
full native login/SQL/integrity/terminal/unlock/full T3–T6/backup3 guard OPEN.

## Durable explicit session rotation decision (06.10.2026)

Команда `postgres-recovery-record-session-rotation` использует те же protected
record/anchor/fencing inputs, что readonly check. Она сохраняет отдельное поле
lock ConfigMap `postgres_session_rotation`, strict public schema1<=4KiB. Native
marker9/checkpoint2/kit3 не изменяются. Поля: policy=rotate_reauthenticate,
operation/checkpoint/recovery bindings, SHA canonical public native9,
public issuer record SHA, rotation ID, runtime Secret name/UID/resourceVersion,
aware decision_at и reauthentication_required=true. Key/private body/private
derivative не записываются.

Два full passes связывают actual current Secret/version, protected issuer file,
native9, saved generation/trust/runtime/fencing/PG/no-writer prerequisites.
Ровно один exact UID/RV ConfigMap replace разрешается узким argv+stdin permit,
который отзывается finally; request<=30s внутри controller deadline90s.
Ответ bounded<=2MiB обязан подтвердить object identity, новый RV и exact data.
Lost/invalid response оставляет lock; fresh observer читает сохранённое решение
без повторной записи, key rotation, Job, SQL, writer или unlock. Post-CAS full
repeat обязателен. Уже имеющееся поле никогда не перезаписывается этой командой.

Issuer expiry проверяется после последнего full pass, а не только до него.
Уже записанное решение использует issuer validity на decision_at; current Secret
UID/RV и full prerequisites остаются актуальными. Истечение issuer record не
создаёт новое решение и не позволяет подменить Secret. Сам readonly check
требует current issuer validity. Historical observer test renews the same bound
NodeLease, freshness guard unchanged.

Home proof: actual public decision CAS, потерянный ответ после записи, fresh
observer без второго CAS, native marker9/checkpoint/Secret/Job count неизменны.
93 new cases, Windows690/Linux1175/0skip, source358/frozen274. Native generation/
runtime/Helm gates fixture doubles; issuer/fencing synthetic lab-only, application
data emptyDir/PG actual PVC. `session_rotation_native_verified=false` и
`storage_recovery_authorized=false`; actual provider guarantee/native login/OIDC/
application schema/integrity/terminal/unlock/T3–T6 remain OPEN.

Bounded CAS transport allows stdin bytes<=2MiB only with exact active argv+data permit; readonly commands retain DEVNULL stdin. Concurrent bounded stdout/stderr discard/timeout/overflow handling prevents input-output deadlock. No general write permission is added. Real subprocess success/overflow/timeout/nonzero/missing permit/changed argv/data/oversize/type tests cover the transport boundary.

## Readonly application SQL revision foundation (06.10.2026)

The installed old backend image supplies its trusted Alembic graph. ScriptDirectory
can read the graph without running env.py; the project checks exact single-head
equality separately from schema integrity. [Alembic script API](https://alembic.sqlalchemy.org/en/latest/api/script.html).

Fixed Python command reads /app/alembic with get_heads(), then public.alembic_version
via psycopg/libpq18 with TCP postgres:5432, verify-full/CA/CRL/SCRAM and readOnly.
Bounded query limit9/connect3/statement3000ms, one image head and one equal DB
head mandatory. No app/config/engine init, env.py, upgrade/downgrade/stamp, schema
or data writes. Public<=2048 strict verdict includes native flags/public leaf and
image_heads/database_heads/schema_revision_equal=true/schema_structure_verified=false.
Missing/empty/multiple/unknown/nonmatching heads fail closed. A matching version
number does not prove tables/columns or data integrity.

50 focused cases per Windows/Linux include executing the inline script with
controlled native boundaries; synthetic provider errors never leave {ok:false}.
Actual old pinned image graph and six owned Job cases on1.37/PG17.11 confirm
native SQL success/refusals, terminal exit/image identity, synthetic SQL42 and
original journals. Version table seeded only in disposable lab; app schema was
not migrated or accepted. Source360/frozen274, no new combined full-suite claim.
Module alone grants no Job/start/unlock permissions and is not yet integrated
into kit/CLI; durable reader protocol, structural compatibility, whole integrity
and post-rotation native login/terminal/full T3–T6 remain OPEN.

## Separate SQL reader journal and preparation (06.10.2026)

A separate public lock field postgres_application_sql preserves native9 and
session decision1 canonical identity. Record1<=8KiB binds both public hashes,
operation/checkpoint/recovery, current runtime Secret name/UID/RV and independently
recomputed fixed old-image Job proposal/name/public leaf. Strict phases:
prepared/create_requested/job_observed/pod_observed/verified/cleanup_requested/cleaned.
The decoder checks phase-specific fields only; producer implemented here is prepared.

Intent timestamp is not earlier than session decision; cleanup is not earlier
than intent, with existing5s skew. First Pod identity supports unscheduled state
only while pod_observed; terminal states require Node/Lease IDs and exact public
revision equality verdict with schema_structure_verified=false. Cleanup RV/time
fields required in cleanup states. Unknown/duplicate/NaN/private fields refuse.

Prepare requires existing independently protected rotation decision/current
runtime identity and all original native9/file/generation/trust/fencing/newPG/
no-writer prerequisites. Two full passes plus absent unique reserved Job precede
one exact lock UID/RV CAS. Bounded stdin/stdout2MiB, request30/deadline90, strict
response identity/new RV/exact data and full post-repeat. Existing prepared record
is observer-only; wrong/later records refuse without overwrite. Lost CAS response
is observed by fresh Manager, no second write or Job.

Private proposal derives old pinned backend/client trust/readOnly data PVC and
mount/no token from protected old manifest; fixed SQL command only, no server key/
frontend. Job name is separate from old native connectivity reader, annotations
bind original checkpoint/native/session hashes, deadline60/backoff0. Proposal body/
private runtime snapshot never goes into public record/output.

SQL code now resides in existing allowlisted postgres_tls.py; exact schema3 kit
archive contract is preserved. New71 parser/controller cases, aggregate811/1296,
0skip/source361/frozen274. Native actual prepared CAS/lost reply/fresh observer/
absence/unchanged journals and readonly SQL six cases checked; whole static/runtime/
Helm gates fixture doubles, issuer/fencing synthetic lab-only, application data
emptyDir/PG actual PVC. No create/SQL/app/unlock capability from prepared. Remaining
producer stages, structural schema/data/native login/full T3–T6 remain OPEN.


## SQL reader create and identity protocol (06.10.2026)

Separate record1 adds produced create_requested/job_observed phases; original
native9 and session decision1 stay immutable. Two full prerequisites passes before
one narrow lock UID/RV CAS; intent precedes one exact create argv+stdin. Permit
revoked finally, create stdout bounded256 bytes/public exact Job name only,
request30 and controller90 unchanged. Existing intent never repeats create, even
if the Job is absent. Interruption before create dispatch remains explicit repair.

First actual Job UID is validated against independently recomputed protected old
manifest/pinned image/fixed SQL command and original public native/session anchors.
A transient local UID candidate allows only that actually observed reader before
UID CAS; no adoption by labels, proposal from live Job, or writer verdict. Full
namespace uniqueness is checked before removing exact Job/at most one owned Pod
from a read-only local view. Complete inventory remains in private comparison.

SQL raw metadata and protected proposal SHA are checked before a copy-only adapter
uses the existing strict native reader API-default/scheduler validation. Adapter
cannot erase unknown annotations, wrong role/anchor/command, admission executable
changes, token, writable data or foreign owner. It never changes original API
objects or persisted native/session journals. Unknown unrelated resources are
preserved for the original no-writer gate. The optional SQL scope is constructed
inside protected generation inspection from current lock and old saved manifest;
it is not a user-supplied allowlist.

Existing job_observed is observer only; missing/replaced Job fails and retains
lock. Public application_sql_job_created=true/application_sql_revision_verified=false,
schema_structure_verified=false/storage_recovery_authorized=false. Native lab
observes terminal readonly revision equality independently, but durable Pod capture,
verdict/cleanup and actual schema/data/native login/terminal authorization remain OPEN.


The preceding source363 Linux run passed all1352 cases in1037.40s, but failed the
unchanged900s observer limit. That failure and the original container identity,
full logs and normal exited-only removal remain retained; it is not acceptance.
A single-test Linux cProfile identified138 repeated TLS module loads (1.698s
of6.174s). Loading the protected operator module once per lifecycle instance,
with serialized first initialization and no publication after a failed execution,
reduced that component to0.013s (total4.398s). Cluster reads, runtime/generation
gates and protected evidence verification are not cached. Four regression cases
cover reuse, independent namespaces, concurrent first calls and failed-load retry.
The fresh source364 acceptance still requires the same900s observer limit.


## SQL reader terminal protocol (06.10.2026)

Record1 produces job_observed->pod_observed->verified. First Pod capture precedes
terminal acceptance; later scheduling may change only node fields of that same
UID/name. NodeReady and fresh bound Lease required. Failed Job/Pod, nonzero exit,
signal, unexpected init/owner/template/extra Pod, replacement or lost captured
Pod refuse and retain lock. Terminal does not imply structural/data correctness.

All protected prerequisites/old proposal/runtime Secret/public journals and exact
Job/Pod identity repeat before and after bounded2048-byte logs. Only strict public
readonly equal-revision/leaf verdict is persisted via lock UID/RV CAS. Existing
verified rechecks current prerequisites and successful terminal identity but does
not read logs or repeat create/CAS. On lost Pod/verified CAS response a new observer
uses the actual durable field. No input/output permits survive failure. Deadline90
and request30 remain unchanged. Native9 and session decision1 are immutable.

Context comparison now preserves the complete lock except its SQL transition
field and API resourceVersion/managedFields; Job and Pod RV strings bounded128.
SQL cleanup is a subsequent separate intent and both-absence protocol; verified
still requires its exact reader to exist. Structural schema/data/FS/Qdrant/outbox,
native post-rotation login/provider rollback and terminal/app writer/unlock remain
separate pending gates; storage_recovery_authorized=false throughout.


## Протокол удаления SQL reader (06.10.2026)

Команда postgres-recovery-cleanup-application-sql переводит отдельный SQL record1
verified→cleanup_requested→cleaned. Cleanup intent фиксирует Job/Pod resourceVersion
и время через lock UID/RV CAS. Повторные protected checks сравнивают старый пакет,
proposal, runtime Secret, все non-SQL lockdata и стабильные metadata. Native9 и
session decision1 неизменны. Deadline90/request30 сохраняются.

Только первый вызов после durable intent может выполнить один raw Foreground
DELETE с фактическими Job UID/RV. Сохранённый intent всегда observer-only, включая
прерывание до DELETE. Подмена Job/Pod после intent запрещает DELETE; разрешение
отзывается при любом исходе. Процедура явного исправления intent остаётся OPEN.

При GC исходный namespace inventory проверяется на дубли UID/name до фильтрации.
Оставшиеся Job/Pod проверяются по защищённому шаблону и captured identities. Для
удаляющегося Job допускается только штатный foregroundDeletion finalizer; Pod
сохраняет обычную проверку job-tracking, владельца, шаблона, nodeName и успешного
terminal. Если Job уже исчез, успешный статус Job не фабрикуется. Чужие ресурсы
сохраняются для общей проверки отсутствия writer. Изменять finalizers запрещено.

Оба объекта должны отсутствовать до cleaned CAS и после него. Новый cleaned
observer повторяет gates/absence без create/delete/logs/CAS. Повторное появление
объектов запрещено. Lock сохраняется: этот переход не разрешает запуск приложения,
запись в БД или unlock. Структура SQL, реальные данные, login и terminal — отдельные gates.


## Read-only public-schema-1 foundation (06.10.2026)

Эталон — результат миграций конкретного закреплённого backend в пустой отдельной
PostgreSQL конкретного закреплённого образа. ORM metadata не равнозначны этому
результату: в проверенном образе18 server-default и2 unique-index/constraint
представления отличаются. Проверяемая восстановленная БД не создаёт свой эталон.
Независимый ожидаемый SHA защищает exact reference bytes; сам hash не доказывает
происхождение, поэтому producer/promotion остаются отдельным обязательным этапом.

Справочник reference1<=2048 bytes связывает backend/PG digests, точную строку версии
PG17, единственную head, canonical hash и11 counts. Duplicate/unknown/NaN/float,
bool-as-int, неверные изображения/head/hash, превышение лимита отказывают. Collector
начинает REPEATABLE READ READ ONLY, проверяет effective public search_path и3s/1s
timeouts; получает только системные каталоги. N+1 limits: relations128, columns2048,
constraints/indexes/triggers/rules512, sequences128, routines64, policies128,
types512, extensions64; UTF8 field8192, flat array128, aggregate1MiB, helper30s.

Canonical rows не содержат OID, оценок строк или текущего значения sequence;
внутренние trigger имена нормализованы через constraint/function identity.
Relations, columns/default/nullability/type, constraint definitions, index state,
sequence config/ownership, internal/user triggers, routines, RLS policies, types,
extensions и фактические pg_rewrite сравниваются. Lazy relhasrules исключён:
после DROP RULE он может оставаться true при пустом pg_rewrite. Это не позволяет
пропустить rule: отдельная обязательная ветка сравнивает definition и enabled.
[PostgreSQL17 pg_class](https://www.postgresql.org/docs/17/catalog-pg-class.html).

Фиксированная программа дополнительно проверяет TCP/TLS/SCRAM, head и leaf SHA;
вывод — только публичный verdict/hash/counts/version, ошибка — {"ok":false}/exit1
без stderr. Outer и inline функции/константы проверяются AST parity. Verdict
schema_structure_verified=true ограничен public-schema-1 и независимо защищённым
эталоном; storage_recovery_authorized=false. Roles/grants/global configuration,
данные/sequence current value, FS/Qdrant/outbox и auth этим не проверяются.

Source367 Windows1006/Linux1491 без skips и реальные14 catalog/6 TLS cases PASS.
Dedicated structural Job/controller/durable record и trusted producer/promotion
ещё не реализованы; старый lifecycle остаётся revision-only. T3–T6/backup3 guard
сохранены, запуск приложения и unlock запрещены до остальных gates.


## Durable structural reader и ожидание процессов (07.10.2026)

В source377 реализован отдельный postgres_application_schema record1 после
CLEANED revision SQL1, native9 и explicit session decision1. Подготовка связывает
canonical SHA старых журналов, checkpoint/recovery, current runtime Secret UID/RV
и independently protected reference1 SHA/pinned old backend/PG/head. Четыре
явные команды выполняют prepare/dispatch/complete/cleanup. Единственный create
следует только после durable intent; Job UID/Pod UID/Node/Lease сохраняются.
Exact command/image/template/owner/annotations и строгий readonly verdict
проверяются свежими чтениями. UID/RV Foreground DELETE имеет отдельный intent;
Job и Pod должны отсутствовать до CLEANED. Lost replies ведут к observer без
повторения действий. Старый SQL record остаётся revision-only и неизменным.

Производитель эталона — fixed program с SCRAM/fixed host/database/user и проверкой
пустого public и Alembic graph до миграций. Только две независимые scratch-БД
доказали воспроизводимость. Общий runner/protected promotion ещё OPEN:
сам SHA результата не доказывает происхождение или изоляцию внешнего pipeline.

Одноэлементный cache хранит только exact manifest bytes+namespace и bounded
immutable JSON; каждое чтение возвращает свежие nested dict/list. Вход и encoded
output ограничены 2MiB; duplicate/alias/forbidden/unsafe YAML не кэшируется.
JSON roundtrip не должен менять исходные типы. Никакого cache актуального cluster
state, файлов/SHA/Secret/time/trust/verdict; исходные проверки остаются свежими.

Для bounded inventory command дочерний Popen.wait() выполняется в daemon waiter,
Event ограничивает ожидание main thread прежним timeout и grace1s. stdout читает
N+1 байт (максимум8MiB), CAS input максимум2MiB; stderr отбрасывается. Reader и
writer имеют прежние join1s. Timeout, pipe/wait error, overflow и неизвестный
returncode отказывают. Список команд и обязательные актуальные чтения не сокращены.
[Popen.wait Python3.12](https://docs.python.org/3.12/library/subprocess.html#subprocess.Popen.wait)
описывает POSIX polling при timeout, который устраняет новое ожидание.
Явная очистка при main-thread interruption и всей process group остаётся
ограничением исходного helper; этот этап не заявляет такие гарантии.

Source377 Windows1258/Linux1743 без skips и actual native within original600 PASS.
Generation/runtime/Helm gates остаются doubles; issuer/fencing лабораторные,
appdata emptyDir. Storage authorization=false, full T3–T6/backup3 guard OPEN:
roles/grants/settings, данные/FS/Qdrant/outbox, actual login/OIDC после разрешённой
явной ротации ключа/antirollback, terminal/writer/unlock и protected promotion.


Проверка package layout, 07.10.2026. После переноса source377 найден collection ERROR: три теста импортировали соседний helper без относительного пути. Исправлены только эти три импорта; существующий tracked пустой tests/__init__.py включён в manifest source378. Runtime-файлы побайтно совпадают с принятой версией377. Windows: collection1258 PASS, smoke58 PASS/3.00s, изменённые модули37 PASS/1.39s; Linux: collection1743 PASS и focused89 PASS/5.33s. Независимое ревью:37 PASS и collection1258 PASS, блокирующих замечаний нет. Завершённый собственный Linux-контейнер удалён штатно, независимое отсутствие ресурсов проверено.

Первоначальный collection ERROR сохранён; ошибочное сообщение wrapper о58 PASS до исправления явно отозвано (фактически0). Полный runtime/native matrix для test-only изменения не повторялся: используется ранее принятый source377. Hosted CI, generic owned builder/protected promotion и полная приёмка T3 остаются OPEN. Разрешение на запуск приложения и снятие запрета записи не выдано этой проверкой.


## T3 owned offline schema builder (07.10.2026)

Общий сборщик принят в source392r5: pinned Linux Docker engine, две новые
собственные tmpfs БД, fixed producer/repeat/read-only catalog, фактическая
root+platform identity, atomic write-once artifacts, bounded CLI и внешний
SHA verifier. Прежний runtime prefix source378 не изменён; allowlist/release
schema3 сохранены. [Операторская инструкция](../../KUBERNETES_SCHEMA_REFERENCE.md)
содержит доверенную границу pipeline и ручное исключение удаления только
never-started own scratch (created/Pid0/нулевые времена/no endpoint/tmpfs).
Это не автоматический delete/replay и не разрешение для ever-started ресурса.

Новая часть406 cases, focused509 PASS/0skip: Windows4.57s/Linux10.64s;
collection Windows1664/50модулей и Linux2149/62модулей. Actual native two-run
23.16s, byte-identical reference, repeat refusal/unchanged catalog/normal cleanup
и independent absence PASS. Reference SHA256
a8dad7c2e7175d877b85f3ee4a0456f3f83b3e498f23a27877cba3b1a59d80fa, build SHA256
e667dbae45c194284815658f7572182f5abc8104bd3c5f036aa385743ad2d67a — функциональные
локальные артефакты unpromoted, а не independently protected expected SHA.

Kit archive воспроизводим, TLSmodule251700bytes, прежний набор12 source members.
Windows/Linux compatibility64 PASS+1skip из-за отсутствия Helm/kubectl в unit
окружении. Отдельно настоящий pinned toolbox bootstrap выполнил consume,
извлечённый release overlay/lifecycle и новый verify-build без source checkout
в рабочем каталоге; неверный SHA отвергнут. Synthetic revision не опубликован.
Первичный kit observer ожидал старые counts2149/509 и завершился assertion:
исходный FAIL сохранён, фактические64 PASS+1skip отражены отдельно; собственный
завершённый контейнер проверен и удалён штатно, отсутствие подтверждено.

Ревью r2 выявило два P2: default network Options и гонка terminal reader/network
inspect. Исправлены с RED→GREEN; реальные следующие r3/r4 выявили root Image
binding и deprecated stop flag stdout. Исправления отдельно RED→GREEN и новый
r5 native PASS; прежние FAIL не переоценены, post-stop network lag не подтверждён.
Actual tests Linux/amd64/API1.54; native arm64/ранние engines не приняты.

Protected promotion/hostedCI/fullT3–T6 OPEN. Roles/grants/settings, реальные
данные/FS/Qdrant/outbox, all-provider sessions/native login/OIDC/antirollback,
terminal/appwriter/unlock и operator pre-dispatch repair остаются следующими
этапами. APP_SECRET_KEY разрешено явно ротировать, все пользователи входят
заново; одного решения о ротации недостаточно для старта приложения.
Полный прежний runtime matrix source377 не повторялся: прежний prefix побайтно
сохранён. Working app/local/Compose, commit/push/release не изменялись.


## T3 all-provider session maintenance foundation (07.10.2026)

- [x] Отдельная backend функция/CLI: preview, strict bool, удаление всех AuthSession
  и OidcLoginFlow одной транзакцией, zero remaining/return after commit, rollback
  при частичном/commit сбое. Старый OIDC-only helper и auth service не изменены.
- [x] Один защищённый runtime-file snapshot, Settings/JSON validation без ambient
  config; PG* и worker hints изолированы через подключение/транзакцию. Cached
  DB engine/factory отвергнут, отсутствующая SQLite не создаётся.
- [x] Windows/Linux43 PASS/0skip, actual15 PG behavior cases+2 SQLite missing-file
  refusals, real PG17.11 CLI с чужими ambient PGHOSTADDR/PGPORT/password/workers,
  readonly preview/lock refusal1.008s, native19.86s/normal cleanup/independent absence.
- [x] Независимый review:29 PASS на исходном396r1; P1 libpq reroute/P2 workers/
  P3 SQLite preview create воспроизведены13RED30PASS и исправлены43GREEN.
  Первая версия/FAIL сохранены; scoped Ruff PASS.
- [x] [Операторская инструкция](../../KUBERNETES_SESSION_RECOVERY.md) объясняет
  разрешённую ротацию, прикладные сессии и ограничения поставки.
- [ ] Durable Kubernetes intent/Job/UID/template/CAS observer/receipt/cleanup
  и binding к rotation decision; native provider login/новая сессия, независимые
  key provenance/Secret и DB antirollback, terminal/writer/unlock/fullT3–T6.

Подписанная cookie при новом ключе отвергнута; возврат старого ключа до удаления
серверной записи восстанавливает доступ, после удаления — нет (четыре providers).
Это не доказательство native login/DB rollback protection: старый backup может
вернуть AuthSession. Нужны отдельные protected key/restore gates перед app start.
Новые backend helper/CLI проверены с read-only source mount и pinned runtime
dependencies, а не новым опубликованным образом. Old image не получает скрипт
автоматически; operator archive schema3 не расширен. Local/Compose/working app
и домашние кластеры не обновлялись; commit/push/release/hostedCI не выполнялись.


### Подготовленное намерение удалить сессии (07.10.2026)

Проверка `decode_session_invalidation_intent` принимает только фазу `prepared`
после `cleaned` проверки структуры БД. Она связывает checkpoint/recovery,
записи native recovery, решение о ротации, runtime Secret UID/RV, SQL/структуру
и независимо заданный SHA эталона. Job UID, результат и cleanup должны отсутствовать;
`all_provider_sessions_invalidated` и `storage_recovery_authorized` равны `false`.
Это только проверка записи: Job, SQL и запуск приложения ею не разрешены.

Windows203 PASS/18.14s и Linux203 PASS/26.79s, без skips; независимое review
203 PASS/17.17s и57 повреждённых записей отклонены. Прежний CLI/main AST сохранён,
schema3 archive воспроизводим. Исходные RED и ошибки test harness сохранены.
Нормализованный maintenance Job, durable CAS/create/observe/commit receipt/cleanup,
реальный повторный вход, Secret/DB antirollback и terminal/writer/unlock остаются OPEN.


### Fixed maintenance SQL program и привязка Job (07.10.2026)

Принят фиксированный self-contained writer/strict commit receipt. Он проверяет
сертификат именно SQL-соединения до записи через PGconn/PQsslStruct/OpenSSL,
поддерживает подтверждённый Linux binary libpq18 runtime и отказывает на
неподдерживаемом ABI. Image graph загружается из установленного `/app`.
Неизвестные PG defaults/URL overrides отвергаются; TLS verify-full/SCRAM и
bounded read-only catalog/head проверяются до отдельной транзакции READ WRITE.
Две auth-таблицы блокируются, удаляются целиком, zero remaining проверяется
до commit; публичный результат выдаётся после commit/закрытия соединения.

`session_binding_sha256` — SHA validated prepared projection в domain
`session-maintenance-binding-1`, исключающий только `job_template_sha256`.
Это позволяет включить binding в command без цикла SHA. Сам template SHA
по-прежнему обязателен в полном intent и проверяется независимо: projection
не разрешает его подмену. Helper проверяет всех родителей прежде вычисления.

Source399: Windows312 PASS/28.36s, Linux312 PASS/39.88s, без skips.
Actual PG17 TLS native53.77s: peer fingerprint/bad leaf/bad CA,
PGHOSTADDR refusal, lock timeout1.82s, catalog drift отказ,
настоящий partial DELETE rollback, все6 provider variants/expired и flows удалены,
контрольный chat сохранён. Fresh read-only same-peer/absence/schema PASS,
own normal cleanup и независимый absence PASS. Fault-trigger reference —
только synthetic test fixture, без protected promotion. Scoped Ruff PASS.

Независимый review исходного398r1:61 PASS, findings none. Native обнаружил
working-directory импорт; regression13 RED→61 GREEN. SHA-cycle устранён
отдельно7 RED→66 GREEN. Исходные native/harness FAIL сохранены; неправильная
гипотеза Config-constructor CWD отозвана. Full runtime/hosted CI не повторялся.
Production dispatch/CAS, normalized Job, receipt persistence/cleanup, caller
fencing/schema writer exclusion, key/DB antirollback, real login и app start
остаются OPEN. Commit ambiguity не разрешает автоматический повтор.


### Durable controller удаления сессий (07.10.2026)

Принят компонент407: нормализованный maintenance Job и журнал
`prepared → create_requested → job_observed → pod_observed → verified →
cleanup_requested → cleaned`. Перед единственным create/Foreground DELETE
сохраняется намерение через exact UID/RV CAS. Потеря ответа переводит следующий
запуск в наблюдение; автоматический повтор SQL/create/delete запрещён.
Полные checkpoint/native/rotation/revision/schema/reference и runtime Secret
UID/RV связаны с командой и template SHA. Проверяется весь inventory до фильтрации,
точные Job/Pod UID/owner и свежая Node Lease. Receipt сохраняется только после
terminal exit0 и commit; cleanup требует повторного отсутствия Job и Pod.
`storage_recovery_authorized=false` сохраняется во всех результатах.

Windows:382 PASS/112.59s, без skips. Linux: те же382 проверки в двух
последовательных наборах —259 PASS/138.48s и123 PASS/57.22s; каждый укладывается
в прежний180s лимит. Это не приёмка одного совмещённого Linux-прогона за180s.
Независимое review:108 PASS/70.81s, подтверждённых замечаний нет.

Отдельный native-тест Kubernetes1.37/PG17 на временном локальном стенде:
541.56s при600s лимите. Каждый прежний родитель создан настоящим controller
по одному разу; новые проверки потери ответов prepared/create/verified/DELETE
и observers без повторных effects сохранены целиком. Actual Job проверил
сертификат своего SQL-соединения, удалил6 provider variants/expired session
и OIDC flow; контрольный chat и данные сохранены. После commit и GC отдельный
read-only Job подтвердил zero sessions/flows, прежние head/catalog и same SQL peer.
Независимо проверено отсутствие собственного namespace/PV/wrapper после cleanup.

Два полных native-прогона превысили первоначальный600s лимит и остаются FAIL.
Поздний второй результат отказал из-за отсутствующего импорта в тестовом fresh
reader; ошибка воспроизведена до SQL и исправлена только в harness. Первый
выделенный прогон отказал на лишней переменной подготовки; результат сохранён.
Отдельная компонентная проверка не заменяет полный native matrix и не закрывает T3.
Прежний Linux406 FAIL был вызван устаревшей Lease в FakeAPI: causal test подтвердил
отказ на31s; fixture моделирует heartbeat, отрицательные31s/+6s и Lease UID cases
сохранены. Production freshness gate не ослаблен.

Публичный operator CLI, независимый durable fresh-empty reader в поставляемом
коде, key/Secret и DB antirollback, реальный provider login, explicit repair
до dispatch/после неопределённого commit и terminal/app-writer/unlock остаются OPEN.
Saved runtime/Helm/external fencing в native-тесте лабораторные; appdata emptyDir,
PostgreSQL использует настоящий выделенный PVC. Домашние кластеры и действующее
приложение не обновлялись. Полный T3–T6, backup3 guard, hosted CI и выпуск OPEN.


### Отдельная fixed read-only проверка auth-таблиц (07.10.2026)

Принята основа408: `session_absence_command` и strict
`session_absence_verdict`. Программа запускается отдельным процессом, открывает
новое SQL-соединение и проверяет сертификат именно его PGconn, TLS/SCRAM,
installed image heads и прежний эталон каталога. В едином repeatable-read
READ ONLY snapshot проверяет zero `auth_sessions`/`oidc_login_flows`.
Оставшиеся записи, включая expired, приводят к отказу. DML/DDL, locks и
READ WRITE отсутствуют; receipt описывает наблюдение, не выполненный commit.
Успех выдаётся после завершения транзакции и закрытия соединения.
Прежние writer/controller/CLI сохранены; `storage_recovery_authorized=false`.

Windows183 PASS/11.82s, Linux183 PASS/13.51s, без skips. В Linux driver
осталось прежнее имя sentinel: исходный wrapper gate FAIL сохранён отдельно
от183 PASS; свежая проверка правильного marker/source/identity и cleanup PASS.
Независимый review:183 PASS/10.37s плюс34 дополнительных fault cases,
подтверждённых замечаний нет. Scoped Ruff PASS.

Actual PG17/TLS native27.98s при180s лимите: same SQL peer/new process,
отказ на nonempty providers, expired session, login flow, wrong leaf/CA,
PGHOSTADDR, head/catalog drift; auth-данные при чтении не менялись,
контрольный chat сохранён. Прежний отдельный writer удалил записи только
в выделенной лабораторной БД. После cleanup независимо проверено отсутствие
созданных PostgreSQL/reader/network. Native fixture использует synthetic
parent journal SHA; это не durable Kubernetes fresh-empty gate или promotion.

Следующий этап: самостоятельный нормализованный read-only Job с полным
cleaned-parent binding, durable prepare/create/observe/verdict/Foreground GC
и строгим inventory scope. Caller обязан независимо проверить весь
родительский journal и fencing до dispatch. Затем нужны CLI/archive integration,
key/Secret и DB antirollback, native provider login, explicit repair,
terminal/app-writer/unlock и полный T3–T6. Прежние полные600s FAIL остаются FAIL.
После интеграции407 дополнительный рабочий suite72 PASS/116.43s.
Основной код, local/Compose и домашние кластеры не менялись; commit/push/release
и hosted CI не выполнялись.


### Durable confirmation of empty auth tables and cleanup boundaries (07.10.2026)

Принят компонент421R3: отдельный read-only Job после `cleaned` удаления сессий,
с фазами `prepared → create_requested → job_observed → pod_observed → verified →
cleanup_requested → cleaned`. Проверяются полная родительская цепочка и SHA
всего cleaned журнала удаления, runtime Secret UID/RV, эталон каталога,
installed image heads и image digests. Receipt подтверждает новое соединение
с тем же TLS/SCRAM SQL peer и нулевые `auth_sessions`/`oidc_login_flows` в
READ ONLY snapshot; наблюдение не подтверждает commit. Результаты обслуживания
сохраняют `storage_recovery_authorized=false`; пользователи входят заново.

Create и UID/RV Foreground DELETE выполняются по одному разу после durable CAS.
Потеря ответа допускает только наблюдение. Перед logs/verdict и перед DELETE
повторяются captured Pod, current Node/Lease, terminal exit0 и protected context.
Во всех четырёх controller cleanup границы проверяют reader Node UID, Lease UID,
Ready и срок Lease после сохранения cleanup intent, прежде первого DELETE.
SQL/schema используют сохранённую verified запись для строгой проверки reader;
актуальные intent, context, полный inventory и metadata/RV проверяются отдельно.
Cleaned требует повторного отсутствия Job и Pod; родительские журналы сохраняются.
Operation Job повторно использует только код закреплённого модуля TLS через
существующий загрузчик. Данные шаблона, Secret, файлов и кластера проверяются заново.

Прежнее independent review:32 PASS и один P2 по пропуску reader Node/Lease
после CAS; исправление нового controller воспроизведено RED → 22 cleanup PASS
на Windows/Linux. Затем аналогичный дефект подтверждён в старых SQL/schema и
session-invalidation controller и исправлен с12 регрессиями. Final review421R3:
69 уникальных PASS, замечаний нет, остальной runtime419 сохранён AST-проекцией.
Windows/Linux покрывают135 уникальных сценариев. Windows подтверждён отдельными
неизменёнными115 сценариями и финальными12 регрессиями+8 CLI; единый зелёный
полный suite этим не заявляется. Linux135 — семь непересекающихся групп, каждая
в прежнем180s лимите; контейнеры удалены штатно, отсутствие проверено отдельно.
Первоначальные ошибки тестового формата и420 SQL180s timeout сохранены в evidence.
Синтетический профиль одного сценария:37.95 →12.42s; compile27.69 →2.46s.
Эти измерения сами по себе не подтверждают время восстановления в инфраструктуре.

Native421R3 component: 477.97s при прежних600s wrapper и
520s cooperative budget. Реальные PG17/TLS, PVC, durable parent Jobs, actual
Job/Pod UID и Node Lease; четыре lost replies, ровно one create/log/delete,
fresh zero counts и контрольные chat/SQL данные сохранены. Собственные namespace,
PV и wrapper удалены штатно и независимо проверены. Runtime/Helm/external
fencing в лабораторном сценарии используют doubles; target acceptance открыта.
Сценарий сохраняет scope прежнего419R4: исключены четыре повторные проверки
старого этапа и дублирующий graph Job, actual revision reader и migration Job
проверяют installed heads. Все проверки нового компонента сохранены.
Native419R2 FAIL464.80s (ошибка чтения краткого ответа),419R3 FAIL534.89s и
419R4 FAIL534.83s (cooperative520) остаются FAILED, как и прежние full600 matrix.
Новый scoped результат не переименовывает их и не закрывает весь T3.

OPEN: protected kit/image/reference promotion и hosted CI; key/Secret и DB
antirollback; настоящий provider login; explicit pre-dispatch repair и
неизвестный commit; terminal/app writer/unlock; полный T3–T6 и целевая приёмка.
Local/Compose, рабочее приложение и домашние кластеры не обновлялись.
Commit/push/release не выполнялись.


### Проверка ключа сессий при восстановлении (07.10.2026)

Кандидат422 добавляет отдельную проверку только для чтения
`validate_recovery_key_provenance`. Прежние v1 rotation decision/журналы и команды
совместимы; их флаги сами по себе не подтверждают смену ключа. Новый gate сверяет
ограниченное закрытое подтверждение с независимыми ожидаемыми SHA, полными native
и session decision, текущим immutable Secret (имя, namespace, UID, RV) и ключом.
Ключ, его SHA и содержимое Secret не возвращаются; writer/start/unlock запрещены.
Каноническая строка ключа сама по себе не доказывает энтропию или выполнение RNG.

Политика восстановления: явная ротация разрешена, все пользователи входят заново.
При известном прежнем ключе независимо переданный SHA должен отличаться от нового.
При недоступном прежнем ключе нужны отдельное явное разрешение и доверенное
подтверждение новой генерации; результат честно сообщает, что сравнение ключей
не выполнено. Ожидаемые SHA получают из защищённого независимого источника,
а не из восстановленной БД, backup, текущего Secret или self-hash файла.
Защищённый publisher и процедура продвижения остаются отдельной границей доверия.

Локальная проверка: 146 Windows и 93 Linux теста прошли; operator-kit format3
сохранился. Независимый review: 48 тестов прошли и 20 дополнительных подмен
отклонены. Native R2 на собственной scratch PostgreSQL17 прошёл за27.45с:
после удаления сессий старый cookie отклонён; настоящий data-only pg_dump/psql
восстановил обе auth-таблицы, и старый ключ снова принял этот cookie. Новый ключ
его отклонил, gate отказал при возврате старого ключа. Полные строки documents,
chat_sessions, chat_messages, audit_log и audit_outbox сохранились. Схема этого
стенда создана из SQLAlchemy-моделей; миграции, production grants и полный backup
приложения этим не проверены. Все временные контейнеры/сеть штатно удалены,
отсутствие проверено отдельно. R1 проверял только sentinel; этот пробел закрыт R2.

Принят компонент426R2, включающий ограниченный provenance gate422 и live CLI.
Полный backend pytest422 завершён: 5823 passed, 9 failed, 154 skipped,
60 errors, 5577.39с. Ошибки окружения не считаются успешной приёмкой:
исходные результаты сохранены. Отдельный профиль с закреплённым Helm4.3.0
и зависимостями дочернего Python, а также исправленной тестовой проверкой
«не создавать новых файлов» прошёл99 тестов за111.61с с полным conftest.
Четыре runtime CA сценария прошли за27.22с. Три исходных Windows symlink
сценария требуют прав symlink; те же неизменённые три сценария прошли на Linux
за1.16с. Windows FAIL сохранены; общий suite не объявлен зелёным. Изменены только тестовый профиль и одна фикстурная проверка, исходный
снимок422 сохранён. Hosted CI, полная T3–T6 и целевая приёмка остаются открытыми.

### Подготовленная проверка ключа через текущий Kubernetes API (07.10.2026)

Компонент426R2 добавляет отдельную команду только для чтения:
`postgres-recovery-check-key-provenance`. Она требует все17 входов recovery
и три дополнительных: `--key-provenance-record`, `--key-provenance-trust`,
`--expected-key-provenance-trust-sha256`. При недоступном прежнем ключе требуется
явный `--allow-unavailable-previous-session-key`; известный прежний ключ вместе
с этим флагом отвергается. Независимый SHA закрепляет закрытый trust manifest,
который содержит SHA подтверждения, источника генератора и прежнего ключа
или null. Ключи и их прямые SHA не передаются через argv и не публикуются. В argv
передаётся только независимо полученный seal закрытого trust manifest.

Трижды читаются вся завершённая цепочка, защищённые файлы и текущий immutable
Opaque Secret. Сравниваются полные наблюдения; смена UID/RV, данных или даже
посторонних metadata приводит к отказу. Файлы ограничены по размеру, symlink,
Windows junction/reparse и замена при чтении отвергаются. Явный пустой SHA
на прежних командах отклоняется до Manager. v1 decision, прежние журналы
и команды сохранены. Результат проверки не разрешает запись, запуск или unlock.

Windows:61 passed/2 platform skips,77.89с. Linux:62 passed/1 platform skip,
98.33с; оба symlink сценария выполнены, контейнер удалён штатно и отсутствие
проверено отдельно. Независимое ревью нашло два дефекта; Windows junction
и пустой legacy SHA воспроизведены RED и исправлены. Повторное ревью:
5 passed/0 skips, блокирующих замечаний в исправлении нет.

Нативный R4 подтвердил компонент за162.86с при прежних cooperative520s и
wrapper600s. Реальные PG17/TLS/SCRAM, PVC, мигрированная схема, завершённые
native/revision/schema/invalidation/absence журналы и immutable Secret.
Проверка ключа и metadata-RV race выполнены через обычный kubectl; стендовый
Unix socket GET proxy использован только для предварительных этапов, без
кэша ответов и открытого TCP-порта. Счётчик9662 чтений proxy не изменился
во время key check/race. Смена RV отвергнута; lock, checkpoint, Secret data
и контрольные chat/SQL данные сохранены. Product mutation count0. Собственные
namespace/PV/контейнер удалены штатно, отсутствие проверено независимо.
Runtime/Helm/external fencing используют лабораторные doubles; issuer в этом
сценарии тестовый. Production publisher, provider login и полный T3 не доказаны.

Исходные FAIL R1(540.89с/cooperative520), R2(21.45с/chunk parser) и
R3(23.31с/fixture inventory bound) сохранены. Для R2/R3 потребовалась отдельная
UID/RV очистка namespace после естественного снятия Job tracking finalizer
контроллером; force/finalizer patch не применялись. Диагностический profiling
FAIL с коллизией имени и отдельная cleanup также сохранены. Hostguard R4
отказал после успешного сценария из-за параллельного независимого коммита
Main; отдельная reconciliation проверила неизменность источников/docs,
новые HEAD до/после и свежую очистку. Исходный отказ guard не переписан.

Открыты: защищённый publisher и независимое
продвижение подтверждения, настоящий provider login,
DB/key antirollback на пути запуска, repair/unknown commit и terminal/writer/
unlock. Проверенный формат ключа не доказывает запуск RNG или доверенное
происхождение подтверждения. Лабораторный issuer не заменяет production issuer.
Local/Compose, рабочие и домашние сервисы не обновлялись. Commit/push не выполнялись.


### Durable запись наблюдения ключа восстановления (07.10.2026)

Принят компонент428R2. Команда `postgres-recovery-record-key-observation`
использует те же17 recovery inputs и три защищённых входа, что readonly
`postgres-recovery-check-key-provenance`; неизвестный прежний ключ требует
явного разрешения ротации. Она добавляет отдельное поле
`postgres_recovery_key_observation` в lock после завершённых native, decision,
revision SQL, schema, invalidation и independent absence этапов. Все шесть
полных родительских записей, Secret UID/RV и identity защищённых артефактов
связаны с операцией. Ключ, его прямой SHA и содержимое защищённых файлов
в публичный журнал не входят.

Три полных одинаковых чтения предшествуют единственной UID/RV CAS; ещё три
подтверждают результат. Потеря ответа разрешает только чтение. Совпадающая
свежая запись проверяется без повторной CAS; чужая, изменённая или истёкшая
запись отвергается. Проверяется deadline перед новым наблюдением и перед
успешным ответом обеих веток. Это запрет поздней записи/успеха, а не гарантия
принудительного прерывания всей вложенной цепочки ровно через90с.

Запись подтверждает наблюдение. `protected_publisher_verified`,
`native_key_rotation_verified`, `db_rollback_verified`, `provider_login_verified`,
разрешения storage recovery, app start, writer и unlock остаются false.
Защищённый publisher/promotion, provider login, DB/key antirollback,
repair/unknown commit и terminal/app/writer/unlock остаются открытыми.


### T4: метадатная проверка перехода PostgreSQL trust (07.10.2026)

Принята основа429: `postgres_restore_profile_sha256` и
`validate_restore_profile(..., namespace_uid=...)`. Canonical identity состоит
ровно из mode, CA/CRL SHA и CRL policy. Подтверждённый disabled отличается
от legacy unknown: неизвестный профиль не получает вымышленного hash.
Изменение профиля требует решения ≤16KiB с точными manifest/source/target
hashes, context/namespace/namespace UID и будущим UTC RFC3339 Z expires_at.
Закрытый JSON отвергает дубли, NaN, bool/float format и неизвестные поля.
TLS downgrade и удаление обязательной CRL запрещены даже при наличии решения.

Это проверка метаданных: native trust, защищённое происхождение решения и
разрешение restore остаются false. Backup3/legacy2 adoption, revalidation
Secret UID/RV/валидности/native TLS перед import/migration/start и сохранение
restore evidence ещё не подключены. Полный T4 остаётся открытым.
Windows208 PASS/72.75с; Linux208 PASS/91.81с, kit format3 сохранён.
Независимое ревью:76 новых тестов и68 дополнительных проверок,144 PASS/4.40с,
блокирующих замечаний нет. Нативное восстановление этим компонентом не проверено.


### T4: сохранённые публичные байты PostgreSQL trust (07.10.2026)

Принята основа430 `validate_backup_postgres_profile`. Format3 требует
явный PostgreSQL профиль: null означает подтверждённый disabled, verify-full
содержит ровно mode/CA SHA/CRL SHA/CRL policy. CA и обязательная CRL проверяются
по SHA исходных байтов, закрытым именам, PEM и CA/CRL ограничениям; CRL должна
быть подписана подходящим CA. Format2 без этого поля остаётся unknown.
Истёкший исторический CA/CRL допускается как сохранённый материал; это не
подтверждение текущего доверия и не разрешение восстановления. Приватный ключ
или неожиданный материал отвергается. Каждый публичный файл ограничен256KiB.

Новые40 тестов прошли RED→GREEN. Frozen Windows248 PASS/70.37с,
Linux248 PASS/88.45с; Linux wrapper завершён штатно и отсутствие подтверждено.
Независимое ревью91 PASS/2.61с, блокирующих замечаний нет. Operator-kit format3
сохранён без продвижения release. Проверка всего manifest/release, файловой
системы и потоковых hashes, producer/legacy adoption/controller/native restore
ещё предстоит. `current_trust_verified` и `restore_authorized` остаются false;
полный T4, backend suite и hosted CI остаются открытыми.


### T4: полная проверка байтов format3 (07.10.2026)

Принят компонент431 — отдельный readonly `verify_backup_format3(path)`.
Manifest и release identity имеют закрытые схемы и предел64KiB, публичные
CA/CRL/HTTP bundles —256KiB. Физические файлы должны точно совпасть с manifest;
лишние, отсутствующие, hardlink, symlink/reparse, каталоги и nonregular members
отвергаются. Проверяются исходный путь, root и родители без предварительного
resolve. Dump/tar/totals хэшируются блоками1MiB с проверкой размера и identity
открытого файла. Повторные наблюдения файлов/каталога и контрольных байтов
ловят видимую подмену во время проверки. HTTP и сохранённые PG trust bytes
проверяются отдельно; исторический срок CA/CRL не превращается в current trust.

Успешный результат подтверждает только byte integrity: protected origin, current
trust и restore authorization остаются false. Источник должен быть защищённым
неизменяемым хранилищем, а перед последующим import нужна свежая проверка.
Stat-наблюдения не являются атомарным snapshot или защитой от привилегированного
нарушителя на хосте. Семантика dump/tar/totals, target admission/digests и capacity
проверяются отдельными gates. Старые reader/producer/controller/CLI сохранены
без изменения их определений; новый helper пока не подключён к обычному restore.

Новые97 случаев: Windows91 PASS/6 platform SKIP, Linux95 PASS/2 Windows-only SKIP.
Frozen Windows339 PASS/6 SKIP/88.47с и Linux343 PASS/2 SKIP/104.85с.
Прежние lifecycle/HTTP trust70 PASS на Windows25.51с и Linux31.02с.
Первичный минимальный child dependency profile дал5 FAIL/65 PASS; отказ сохранён,
повтор выполнен с подготовленными зависимостями без изменения продукта/тестов.
Независимое ревью37 PASS/3.94с, блокирующих замечаний нет. Linux wrappers удалены
штатно, отсутствие подтверждено отдельно. Deterministic operator-kit format3
сохранён без продвижения release. Полный T4, T3 и общий backend/hosted CI открыты.
