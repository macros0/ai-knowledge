# Bundled PostgreSQL TLS — проверка этапов

## T1, 05.10.2026

Реализован bounded static profile/material preflight. Missing profile означает
unknown/error; явно disabled — `None`. Проверяются exact flags/keys, immutable
Secret name/namespace/UID и повторный UID binding, encoded/raw N/N+1 до decoding,
PEM/key match, SAN `postgres`, validity, CA constraints, optional serverAuth EKU,
CRL issuer/signature/time. Ответ содержит только публичные hashes/UIDs.
Полная native chain/revocation проверка остаётся T5.

Producer, direct overlay, trusted bootstrap и toolbox согласованы на schema3;
новый TLS module входит в exact archive allowlist. Сохранены реальные archives
kit1/kit2 и bootstrap fixtures. Матрица3×3: same-version PASS, mismatch отказ
до extraction/mutations. Kit3 не объявляет archive2 совместимым. Backup всё ещё
format2; PG identity/legacy adoption/transition — T4.

| Проверка | Результат |
|---|---|
| Owning TLS/release/toolbox suite, Linux CPython3.12.15 | 120 PASS, 0 skips, 158.36s |
| Owning TLS suite, Windows CPython3.12.10 | 59 PASS, 0 skips, 57.70s |
| Real Dockerfile, pinned base + verified Helm/kubectl | build exit0 |
| Exact toolbox offline, no checkout/network, UID1000/read-only | bootstrap/overlay/TLS import +6 refusals PASS |
| Operator lock audit | exit0, 4 packages, 0 known vulnerabilities |
| Scoped Ruff | PASS |
| Frozen application source | 274 hashes unchanged |

Оба pytest-прогона содержат существующее предупреждение `cache_dir` при
`no:cacheprovider`; оно не означает пропуск тестов. Windows first harness
запуски не дошли до продукта (команда и отсутствующий editable doc-parser path);
исправлены только harness inputs, затем выполнен полный owning suite.

Зафиксированы cryptography50.0.2, cffi2.1.1, pycparser3.0, PyYAML6.0.3 с SHA256
официальных wheels и запретом source fallback. Hash-lock реально установлен в
отдельных Windows и Linux runtimes. Cryptography лицензирована Apache2.0 OR
BSD3-Clause, CFFI MIT-0, pycparser BSD3-Clause, PyYAML MIT; metadata проверена
на установленных пакетах. Версия/исправления сверены с
[PyPI](https://pypi.org/project/cryptography/50.0.2/) и
[официальным changelog](https://cryptography.io/en/latest/changelog/).
Это scoped operator gate; аудит/лицензии всего приложения и hosted CI не закрыты.

Первичные RED сохранены: absent module48 errors; first PKI47PASS/1fixtureFAIL;
malformed metadata58PASS/1FAIL; kit27PASS/10FAIL/1missing-toolsSKIP;
raw-before-decode/optional leaf3FAIL. Окончательные required runs без skips.

Private evidence: plan-owned `.superpowers/sdd/2026-10-05-kubernetes-postgres-tls/`;
там source SHA256, exact local image identity, dependency audit и исходные logs.
Локальный image ID и synthetic release metadata не доказывают публикацию OCI
release либо соответствие payload закоммиченной ревизии.

## Открытые gates

T2–T6 OPEN: server/client chart и Jobs, immutable HBA/initdb/native permissions,
runtime conflict checks, protected lifecycle/recovery, backup3/legacy2/current
trust transition, настоящая PKI в projected Secrets, полная установка/restore,
resource sizing, hosted CI и platform proof. TLS disabled по умолчанию;
первоначальный disabled rollout имеет собственные прежние gates.

## T2, частичный checkpoint 05.10.2026

Chart0.2.0 добавляет `storage.postgresTls` с disabled default и strict flags/refs.
TLS mode задаёт server args, immutable HBA с hash точных LF bytes и keep policy;
PG999 получает только server Secret0440, backend1000/Jobs только client Secret0440.
Frontend не получает PG projection. Client env задаёт verify-full/root/optional
CRL, GSS disable, SCRAM requirement и client-cert disable. Legacy disabled
templates сохраняют прежние storage/Jobs contracts. Runtime config/Secret и
inline Job env не могут переопределить native controls выбранного TLS профиля.

Job helper переносит установленный/желаемый профиль, отклоняет incomplete,
duplicate/valueFrom/extra PG controls, writable/subPath/неверные keys/modes и
нарушение container boundary. Входные templates не меняются при переносе.
HBA hash поправлен после реального RED на CRLF→LF Windows/Helm YAML conversion.

- Final T1+T2 owning/regression suite: **318 PASS, 0 skips, 400.69s**.
- До extra-control fix: chart/temp/lifecycle/runtime-trust197PASS/0SKIP; затем
  отдельный PGSSLKEY case RED и общий финальный GREEN. Initial chart RED26FAIL,
  1PASS (включая две исправленные fixture refs); промежуточный25PASS/2HBAFAIL.
- Runtime controls RED15FAIL; первый harness без Helm пропустил15 и не считается
  proof; required RED/GREEN выполнены с настоящим Helm.
- Real Helm4.3.0 lint TLS+CRL PASS; exact toolbox offline chart0.2.0 smoke PASS.
- Frozen backend Psycopg3.3.6/libpq180006: implicit cert/key/root/CRL отсутствуют
  и в HOME, и в passwd home UID1000; inherited PG controls отсутствуют. Это file
  baseline, а не handshake, SCRAM/revocation или pool-renewal proof.
- Scoped Ruff и diff whitespace PASS; application274source hashes unchanged.
- CI setup разделён: app dependencies отдельно от hash-locked operator install;
  новые owning tests включены в required list; GitLab/Chart versions согласованы.
  YAML consistency PASS, hosted CI не запускался.

T2 целиком остаётся OPEN: initdb и restart на пустом/непустом PVC, реальные
projected permissions/TCP TLS/SCRAM должны пройти изолированную приёмку.
T3–T6 не закрыты: Secrets UID/native preflight до mutations, checkpoint/recovery,
backup3/legacy2/current trust transition, полная установка/restore и platform CI.
Рабочее приложение не обновлялось; commits/push/publication не выполнялись.

## T2 завершена: actual native initdb/restart, 05.10.2026

В отдельном одноразовом Kubernetes namespace применены PostgreSQL StatefulSet,
Service и HBA ConfigMap, отрендеренные настоящим chart0.2.0. Chart server args,
security contexts, resources, volumes и HBA сохранены; imagePullPolicy=Never
используется для заранее проверенного локального OCI import. App release не
обновлялся. Синтетические пароль/key/CA/CRL создавались в памяти и передавались
API через stdin; private key/password не записывались в fixtures или evidence.

Два настоящих Jobs используют `operation_pod_spec` из текущего lifecycle:
первый записывает и commit-ит синтетическую строку после initial initdb,
второй читает её после graceful PG Pod deletion/recreation. Подтверждены новый
Pod UID, прежний PVC UID, fresh connections и видимость commit из другой сессии.
`pg_stat_ssl` подтверждает TLSv1.3/TLS_AES_256_GCM_SHA384; пароль использован,
PGREQUIREAUTH требует SCRAM и verifier проверен как boolean без вывода hash.
SSLRequest + отдельный проверенный handshake подтверждает actual leaf fingerprint.
Actual PG key/cert — root:999:0440/read-only, client CA/CRL — root:1000:0440/read-only.

На каждом этапе отказаны plaintext, untrusted CA, hostname и неправильный пароль
(всего 8 native negative assertions). Проверка требует соответствующую причину
отказа; timeout/refused/network failure не принимается за PKI/auth proof.
Версии: PostgreSQL17.11, Psycopg3.3.6, libpq180006, Python ssl OpenSSL3.5.7.
Для backend проверены все 274 frozen source hashes до/после restart, chart hashes
сверены с текущим кандидатом. Frozen OCI index:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.
CRI import wrapper, этот index, amd64 manifest и config связаны проверенными
SHA256 content bytes; wrapper ID не подменяется image config digest.

| Проверка | Итог |
|---|---|
| Final operator/profile/chart/lifecycle/trust/release/toolbox, Linux | 349 PASS, 0 skips, 73.44s |
| Final runner/PKI/denial guards, Windows pinned cryptography runtime | 31 PASS, 0 skips, 0.59s |
| Pre-final suite с обычным app conftest | 347 PASS, 0 skips, 436.84s; затем добавлены 2 fixture regressions |
| Actual native seed + restart Jobs | exit0, обе positive сессии и 8 negative assertions PASS |
| Owned cleanup, включая прежние FAIL | 7 namespaces удалены; оставшихся их PV — 0 |
| Scoped Ruff / whitespace / YAML / frozen source | PASS |

Final Linux команда (operator tests не требуют autouse инициализации app/БД):

```bash
python -m pytest backend/tests/test_kubernetes_postgres_tls.py \
  backend/tests/test_kubernetes_postgres_tls_chart.py \
  backend/tests/test_kubernetes_postgres_tls_runner.py \
  backend/tests/test_kubernetes_chart.py \
  backend/tests/test_kubernetes_temporary_storage.py \
  backend/tests/test_kubernetes_lifecycle.py \
  backend/tests/test_kubernetes_runtime_trust.py \
  backend/tests/test_kubernetes_release.py \
  backend/tests/test_kubernetes_toolbox.py -q --noconftest \
  -p no:cacheprovider --basetemp=/tmp/postgres-native-frozen
```

Run выполнен в frozen image с настоящими Helm/kubectl в PATH, PYTHONPATH=backend
и LITELLM_LOCAL_MODEL_COST_MAP=True. Это operator suite, не backend coverage.
Runner CLI для отдельного стенда (защищённый kubeconfig передаётся окружением):

```bash
python scripts/kubernetes/tests/postgres_tls_acceptance.py \
  --context <explicit-lab-context> \
  --backend-image <frozen-repository@sha256:digest> \
  --expected-image-id <verified-CRI-image-digest> \
  --source-manifest <protected-frozen-source-manifest> \
  --evidence-directory <new-owned-directory>
```

Сохранены initial RED16 runner cases; generated inventory RED3 и negative reason
RED8, затем GREEN. Первый actual run не нашёл canonical image ref при Never;
зарегистрирован alias для того же OCI content без изменения application image.
Второй отказал по неверному ожидаемому CRI identity: index/wrapper/config затем
проверены раздельно. Следующие FAIL выявили fixture CA с тем же issuer name и
другим RSA key: OpenSSL возвращал `invalid padding`. Нативный отказ не ослаблен;
untrusted-CA fixture теперь имеет отдельный issuer, RED→GREEN добавлен. Случай
same-subject/wrong-key и полная chain/revocation/rotation матрица остаются T5.
Первоначальная cleanup не приняла generated Endpoints/Events; исправлены guards
по service ownership/UID и исторически захваченным UID, включая deadline-удалённый
Job Pod. Foreign/recreated entities по-прежнему блокируют cleanup. Cleanup failure
не создаёт result.json; recovery первого FAIL выполнено только по captured IDs.

До/после доступны примерно 1.5GiB RAM и 9GiB диска; стенды шли последовательно.
Это не corpus sizing, disk-pressure или historical eviction proof. Проверка
postflight через неподдерживаемый status.reason field selector сохранена как FAIL;
затем inventory JSON подтвердил отсутствие оставшихся owned namespaces/PV/Pods.
Приватные logs/result/identities/source hashes находятся в plan-owned workspace;
generic документы содержат только технические результаты и placeholders.

**Открыто:** T3 static preflight до lock/storage + native gate до migration,
operation checkpoints/recovery; T4 backup3/legacy2/trust transition; остальная T5
матрица chain/expiry/revocation/leaf+CA+CRL rotation/audit; T6 lifecycle/restore,
sizing, dependency/project/coverage/Compose и hosted/platform CI. Проверенный
T2 не разрешает включать TLS в рабочем application release.

## T3 частично реализована: static/SCRAM/native gates, 05.10.2026

В `lifecycle.py` добавлен статический preflight для desired/installed профилей.
Он проверяет реальные server/client projections, immutable HBA/public material
hashes и namespace/Secret/HBA UID; fresh namespace и pending runtime Secret
не требуют SQL. VSO arrival проверяется повторно. Runtime PG controls нельзя
подменить через envFrom. Повторные bindings перед Job/mutation/start отклоняют
замену/expiry и конфликтующие входы; raw private materials не выводятся.

Existing Ready PG Pod связывается с StatefulSet controller UID; до stop выполняется
readonly boolean SCRAM verifier query без вывода password hash. После storage
создаётся новый Job с desired client projection, activeDeadlineSeconds60/backoff0.
Psycopg/libpq18 проверяет TCP+verify-full, password/SCRAM, pg_stat_ssl и отдельный
verified SSLRequest handshake с expected leaf fingerprint. Native verdict ≤1024bytes
имеет exact fields, boolean true и unique JSON keys. Completed Job сохраняется.
Request-timeout30s/client≤60s/controller wait≤90s; неуспех запрещает migration,
start и release lock. Readiness pg_isready не заменяет эту проверку.

Final home proof использовал реальный `Manager.preflight_postgres_tls`,
`check_existing_postgres_scram`, `postgres_tls_gate` через plan-owned adapter.
Job создавался настоящим Manager.apply; адаптер добавлял ownership labels для
existing guarded cleanup и заменял только job image ref/pull policy на проверенный
локальный frozen OCI. PostgreSQL StatefulSet/chart и operator sources сверены
хешами. Seed и restart дали PASS: новый PG Pod UID, тот же PVC, commit visibility,
TLS/SCRAM/actual leaf и 8 native negatives; обе дополнительные Manager Jobs PASS.
Все 274 frozen backend source hashes совпадают до/после restart. Backend OCI index
остаётся `sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`;
import wrapper/config связь — как в T2 evidence. Ключи и пароль синтетические.

| Проверка текущего кандидата | Итог |
|---|---|
| Final operator suite с новым lifecycle модулем, Linux | 398 PASS, 0 skips, 78.64s |
| Из них новый TLS lifecycle module | 49 cases |
| Actual Manager static/SCRAM/native gate, initial + restart | exit0, обе фазы PASS |
| Exact operator/chart/backend hashes | PASS, 274 backend files |
| Cleanup всех T3 запусков | 5 namespaces удалены; оставшихся их PV — 0 |
| Scoped Ruff / YAML / whitespace | PASS |

Команда — final Linux T2 operator suite выше **плюс**
`backend/tests/test_kubernetes_postgres_tls_lifecycle.py`, с теми же frozen image,
настоящими Helm/kubectl, PYTHONPATH и `--noconftest -p no:cacheprovider`.
Это operator-only suite; backend coverage и Windows T3 suite не объявляются
проверенными. Windows31 из T2 сохраняется как историческая отдельная проверка.
Plan-owned actual command использует `t3_native.py` с теми же guarded context,
image/source manifest/evidence-directory аргументами; API операции действительно
выполнялись в одноразовом namespace, а не через mocked kubectl.

Сохранены initial RED25, boundary/revalidation/format guards RED и первый реальный
FAIL. Реальный Kubernetes API добавляет HBA volume defaultMode420 (0644): strict
parser ошибочно отказал. Регрессия 1FAIL/5PASS → fix → GREEN: разрешены только
отсутствующий mode или integer0644; другие modes/types/extra controls запрещены.
Ни TLS-проверки, ни разрешения Secret не ослаблены. Три initial home FAIL сохранены;
run4 PASS, затем strict deadline regression RED1 → GREEN49 и final run5 PASS.
Deadline regression подтверждает: успешный Job response после90s не запускает
новый log read. Финальный оператор совпадает с home по SHA256 обоих модулей.

До/после стенда доступно около1.4–1.5GiB RAM и9GiB диска, четыре CPU. Стенды
запускались последовательно; current cleanup inventory не является доказательством
исторического отсутствия eviction или corpus/disk-pressure sizing. Рабочее app
release не обновлялось. Commit/push/publication не выполнялись. Generic report
не содержит реквизитов среды; private evidence остаётся в plan-owned workspace.

**Временные ограничения:** installed TLS deploy/backup отказывают до lock/stop,
пока T4 не реализует backup3. Format2 restore в TLS target отказывает до import:
старый backup не доказывает PG identity. Fresh install/native gate не разрешает
выпуск TLS. Снятие этих guards требует backup3 и owning tests.

**Открыто:** T3 checkpoint/recovery с controller interruption, сохранением old/new
storage/Pod identities и SQL compatibility decision; T4 backup3/legacy2/current
trust transition; T5 chain/expiry/revocation/leaf+CA+CRL rotation/audit; T6 full
Manager lifecycle, непустой restore/failed rotation, sizing, current dependency,
coverage/project/Compose и hosted/platform CI. Полный VSO flow и failure/lock
retention на настоящем controller также остаются для отдельной приёмки.

## T3 durable checkpoint foundation, 06.10.2026

Добавлен bounded public schema2 journal в отдельном namespaced ConfigMap.
Namespace/lock/checkpoint UID и resourceVersion связывают обновления. Запись
prepared предшествует stop, storage_changing — Helm mutation, migration_started
— writer, start_started — старту app. Переходы монотонны, при interruption после
migration_started фиксируется may_have_started. Повтор из stale памяти после
lost CAS reply отказан; persisted phase можно прочитать новой Manager-инстанцией
или отдельным CLI. Это не автоматическое возобновление операции.

Journal хранит только public Secret refs/UID/hashes, HBA, image refs/known digest,
chart-tree/ordered-values hashes, installed Helm manifest hash, template hashes,
observed PG controller/Pods/PVC. Keys/passwords/raw env/templates отсутствуют.
Exact schema/64KiB/duplicate keys/types/NaN/changed binding guards проверены.
UID replacements, changed chart/values, expired/changed TLS material, unready PG
и старый Pod при новом template запрещают следующую phase mutation. Namespace и
lock повторно сверяются после inventory, перед CAS. После journaling material
validation повторяется непосредственно перед storage/start.

`operation-status` читает только operation/phase/migration и не требует values:

```bash
python scripts/kubernetes/lifecycle.py operation-status \
  --context <explicit-context> --namespace <namespace> --release <release> \
  --operation-id <operation-id>
```

Incomplete TLS lock не снимается recover-lock или прямым release_lock. Удаление
lock pointer при сохранившемся journal не обходит guard; legacy TLS state без
journal требует отдельной recovery процедуры. После успешной terminal phase
lock удаляется через API DeleteOptions UID/resourceVersion, journal сохраняется.
Это protocol guards для maintenance operator; RBAC должен закрывать запись CM от
пользовательских workload, привилегированный администратор не считается недоверенным
владельцем этой записи. Retention не заменяется массовым удалением по labels.

| Проверка current candidate | Итог |
|---|---|
| Final Linux operator suite | 445 PASS, 0 skips, 84.04s |
| Новый checkpoint module | 47 cases |
| Windows parser/CAS/status/lock guards, pinned runtime | 34 PASS, 13 deselected, 0 skips, 0.25s |
| Actual journal schema2/API/CAS/CLI, seed + PG restart | exit0, PASS |
| Frozen source/chart/operator identity | 274 backend files и exact hashes PASS |
| Final owned cleanup | 3 namespaces удалены, оставшихся их PV — 0 |
| Scoped Ruff / CI YAML / whitespace | PASS |

Linux команда — предыдущий final operator suite плюс
`backend/tests/test_kubernetes_postgres_tls_checkpoint.py`, в том же frozen backend
image с actual Helm/kubectl и `--noconftest -p no:cacheprovider`. Полный backend
coverage здесь не запускался. Windows выборка исключает13 Helm/prepared-fixture
cases, все они обязательны и выполнены в Linux47. В pytest остаётся известное
configuration warning cache_dir от отключённого cacheprovider; failures/skips нет.
Новый module включён в required Kubernetes CI list; hosted CI не запускался.

Actual plan-owned `journal_native.py` расширяет PG-only runner: PG уже bootstrapped
перед созданием journal, app release не устанавливается. Journal сохраняется перед
graceful PG Pod replacement; свежая Manager-инстанция и отдельный CLI process
читают storage_changing. Recover-lock отказан до mutation. После нового PG Pod UID,
того же PVC и commit visibility новый native gate PASS; реальные replace/CAS
переводят journal в provision_complete, conditional delete удаляет lock, свежий
reader подтверждает сохранённый journal UID. Это прямые реальные Manager methods,
но **не полный** Manager.provision/deploy/VSO или убийство активного migration
controller. Unit API double отдельно проверяет lost reply и failures каждой фазы.

Все 8 native denial assertions и server/client read-only0440/group999/1000 повторно
пройдены. Backend OCI index остаётся
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`;
его CRI graph и все274 source hashes — как в T2 proof. Current operator оба модуля
совпадают с фактически исполненными home bytes. Final capacity: четыре CPU,
около1.4GiB доступной RAM и9GiB диска. Это не sizing/eviction/disk-pressure proof.

Initial RED и fixture failures сохранены. Parser/bindings → pipeline → storage
identity → chart/generation freeze → TLS/values repeat → missing pointer guards
прошли RED→GREEN. Для first unreleased journal1 добавлен отдельный ordered-values
identity в journal2; missing old input identity не домысливается. Первый home
journal1 и следующие journal2 run2/run3 PASS сохранены раздельно. Disabled values
regression RED показала случайный общий gate16files: теперь gate только selected
TLS, disabled path сохраняет существующие inputs. Final445/Windows34 относятся
к последнему source candidate после этого исправления; earlier438/444 не заменяют
его результат. Рабочее home application не обновлялось; commit/push/release нет.

**Открыто:** полное T3 storage recovery по operation ID требует protected old kit,
exact installed chart artifact digest/проверенной API-default нормализации и
pinned image identity. Installed Helm manifest hash не является artifact digest;
image tag даёт digest=null. Native trust/выход нового PG Pod/SQL compatibility
нужно проверить до возврата old writer. После migration_started обычный Helm
rollback запрещён; restore идёт в новые PVC по policy. Сейчас отдельная команда
возврата old storage не реализована, terminal unlock не её замена. Checkpoint
foundation не снимает T4 guards TLS backup/upgrade/legacy2-to-TLS restore. T4–T6,
full interruption/restore/audit/rotation/sizing/project/Compose/coverage и
hosted/platform gates OPEN. TLS release по-прежнему не разрешён.


## T3 package integrity, 06.10.2026

Добавлен offline CLI `release.py verify-recovery-package`: проверка saved schema3
release/chart/kit, expected SHA/revision из protected promotion, exact members,
bounded JSON/tar inputs, pinned images/toolbox и sanitized refusal. Старый код
не исполняется, extraction/SQL/API/mutations/unlock отсутствуют.

```bash
python scripts/kubernetes/release.py verify-recovery-package \
  --release <saved-release.json> --tools-archive <saved-operator-tools.tar.gz> \
  --chart <saved-chart.tgz> --expected-release-sha256 <protected-release-sha256> \
  --expected-revision <protected-full-revision> \
  --toolbox-image <saved-toolbox-repository@sha256:digest>
```

| Проверка текущего candidate | Итог |
|---|---|
| Full focused Linux operator suite | 485 PASS, 0 skips, 32.14s |
| Новый package module | 39 cases, включая symlink и CLI |
| Windows pinned package tests | 34 PASS, 5 deselected, 0 skips, 1.54s |
| Actual Helm package chart0.2.0 + kit offline | PASS, installed generation не проверена |
| Exact Linux snapshot hashes | 340 файлов PASS, включая274 frozen backend files |
| Scoped Ruff / CI YAML / whitespace | PASS |

Все12 operator modules выполнены в том же frozen backend OCI image, с actual
Helm/kubectl, network none, source readonly, временным DATA_DIR, без kubeconfig;
контейнер работает под UID1000 на домашней VM. Это unit/offline artifact acceptance,
не Kubernetes recovery или hosted CI. Backend coverage и полный project/release
suite не запускались. Новый module включён в required Kubernetes CI list.

Сохранён initial RED24 на Windows, дополнительный RED3 выявил bool-format,
fractional-schema и duplicate JSON metadata. Первый Linux focused run39 errors
остановился на неполной test bundle; bundle дополнен до полного allowlist.
Первый полный Linux run: 438 PASS/33 FAIL/13 ERROR — общая fixed-epoch CRL истекла
06.10 и временный DATA_DIR не был задан для readonly source. Lifecycle PKI fixture
теперь создаётся относительно actual clock, отдельный regression подтверждает,
что CRL спустя два дня по-прежнему блокирует next phase без mutation. Product
проверки срока действия не менялись. Известный pytest cache_dir warning остаётся
от отключённого cacheprovider; final failures/skips нет. Исходные результаты не
заменены успешным прогоном.

**Граница:** protected artifact SHA ещё нужно связать с installed journal/template
и image identity, проверить API defaults, current old trust и writer/Pod exit.
Dedicated storage recovery по operation ID не реализован; offline PASS не даёт
права на rollback, TLS downgrade или unlock. Schema2 package recovery отказывает.
Полный T3, T4 backup3/upgrade guards и T5–T6 остаются OPEN; working application
не обновлялось, новых Kubernetes ресурсов не создавалось. Commit/push/release нет.


## T3 saved-generation binding, 06.10.2026

Добавлен readonly `lifecycle.py postgres-recovery-check`: verified schema3 package
связывается с full old render, protected saved Helm manifest и installed template/
image hashes journal. После possible migration и при изменениях binding/input
вернуть старое поколение check не разрешает. При успехе verdict содержит
`storage_recovery_authorized: false`; SQL/storage/unlock не выполняются.

```bash
python scripts/kubernetes/lifecycle.py postgres-recovery-check \
  --context <explicit-context> --namespace <namespace> --release <release> \
  --operation-id <operation-id> --chart <saved-chart.tgz> \
  --values <saved-old-values.yaml> --saved-release <saved-release.json> \
  --saved-kit <saved-operator-tools.tar.gz> --saved-manifest <saved-helm-manifest.yaml> \
  --expected-release-sha256 <protected-release-sha256> \
  --expected-revision <protected-full-revision> \
  --toolbox-image <saved-toolbox-repository@sha256:digest>
```

| Проверка текущего candidate | Итог |
|---|---|
| Full focused Linux operator suite, 13 modules | 520 PASS, 0 skips, 33.39s |
| Новый generation/operation-ID/CLI module | 35 cases |
| Windows pinned runtime | 35 PASS, 0 skips, 0.62s |
| Actual Kubernetes1.37 server-side defaults | exact normalized API template hashes PASS |
| Actual Helm package + render + TLS/HBA binding | PASS, HBA mismatch refused |
| Persistent dry-run proof resources | 0, absence verified for exact names |
| Exact snapshot / frozen source | 341 files / 274 backend files PASS |
| Scoped Ruff / CI YAML / whitespace | PASS |

Full Linux suite выполнялась UID1000/network none/source readonly/temp DATA_DIR
без kubeconfig в прежнем frozen backend image. Новый module включён в required
Kubernetes CI list; hosted CI и полный backend coverage/release suite не запускались.
Сохранены RED25 parser/generation cases и RED7 Manager cases. Первая версия520
suite PASS33.21s, Ruff заметил запись после semicolon; после разбивки строк final
candidate повторно прошёл520/35. Известный cache_dir warning относится к выключенному
cacheprovider; final failures/skips нет. Gates/expiry assertions не ослаблялись.

API proof работает отдельным UID1000 контейнером с explicit home context, server-side
create --dry-run=server и проверкой отсутствия конкретных объектов после ответа.
Ни Deployment, ни StatefulSet не сохранялись. Нормализованные templates текущего
chart точно совпали с API1.37.0; настоящий package с immutable synthetic image refs
был повторно rendered и прошёл binding/HBA denial. **Checkpoint синтетический**,
с template hashes из actual API admission: он не подтверждает исторический
installed release или реальную recovery-операцию. TLS Secret UIDs/CA fingerprints
в этой fixture synthetic; certificate validity/native TLS здесь не проверялись.

**Открыто:** current old trust/native certificate proof, current writer absence,
PG/PVC/Pod UID checks, durable recovery decision/phase journal и graceful возврат
storage; explicit санкционированный downgrade при первой TLS activation. После
may_have_started требуется SQL compatibility policy/restore в новые PVC, не Helm
rollback. Сам mutating recovery пока не реализован; readonly verdict не снимает
lock или T4 guards. Schema2 artifacts отказывают. T3 и T4–T6/TLS release OPEN.
Working home application не обновлялось; commit/push/release нет.


## T3 current old TLS static prerequisite, 06.10.2026

Реализован opt-in `postgres-recovery-check --check-old-tls-materials` после полного
saved-generation gate. По текущему UTC проверяется сохранённая public TLS identity:
UIDs immutable Secrets, CA/CRL/leaf/chain hashes, сроки/key/SAN/EKU/CRL signature.
Добавлен recovery-specific отказ известному отозванному сертификату server chain;
обычный T1 validator не объявлен полным chain/revocation checker. Delta/indirect/
scoped CRL здесь не поддерживаются. Current HBA content/hash/ns/release проверяются
с повторным UID binding, Secrets читаются дважды. Исторический HBA UID journal2
не содержит. Protected files/values и journal/namespace/lock повторяются после
material inspection. Unmanaged old profile отказывает без plaintext inference.

| Проверка текущего изменения | Результат |
|---|---|
| New old-trust26 + saved-generation43, pinned Windows Python3.12.10/cryptography50.0.2 | 69 PASS, 0 skips, 1.19s |
| Scoped Ruff / CI YAML / whitespace | PASS |
| Application source | 274 frozen hashes unchanged |
| Source snapshot для следующего Linux прогона | 342 exact file hashes; локальный архив готов |
| Linux regression текущего изменения, frozen Python3.12.15/cryptography50.0.2 | 554 PASS, 0 skips, 33.35s;14 operator modules |
| Hosted CI / native old-generation recovery | НЕ ВЫПОЛНЕНЫ |

Initial25 missing-functions RED и3 protected-input race RED сохранены. Первые
Windows manager cases получили7 fixture errors из-за repository basetemp вне
существующего каталога; исправлен только путь harness в private directory.
Начальная проверка frozen manifest выбрала неверную относительную базу; повтор
из backend/ подтвердил все274 hashes. Известное предупреждение cache_dir остаётся
при no:cacheprovider, skipped tests нет. Предыдущий Linux520 PASS относится к
прежнему snapshot341. Новый Linux554 проверяет точный snapshot342 до и после
тестов; отдельная локальная повторная проверка подтвердила342 source/274 frozen
backend hashes. Контейнер UID1000/network none/read-only source/temp DATA_DIR,
без kubeconfig, pinned OCI image866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500.
Изменений Kubernetes и работающей установки в этом прогоне нет.

Первоначальная передача была отклонена автоматической проверкой разрешений.
После явного разрешения пользователя передан только подготовленный source/test
bundle на домашний стенд и выполнен этот изолированный unit-прогон. Первоначальный
локальный Docker остаётся недоступен; remote approval block для указанной передачи
снят, но это не approval рабочего deployment/native recovery.

Verdict содержит old_tls_materials_validated=true и old_tls_native_verified=false;
storage_recovery_authorized=false. Проверка не выполняет SQL/Kubernetes mutations
или unlock, не является complete path builder/атомарным API snapshot. Writer absence,
PG/PVC/Pod identity, durable recovery claim/phases и graceful/native возврат storage
ещё OPEN; installed TLS backup3 guard сохраняется. Полный T3 и T4–T6 OPEN.
Работающее приложение, deployment, commit, push и release не выполнялись.


## T3 current-state readonly prerequisite, 06.10.2026

Добавлен postgres-recovery-check --check-current-recovery-state после protected
saved-generation и old TLS gates. Два namespace-wide standard workload/PVC List
без selectors; между ними повторён old trust/HBA, затем files/values и ns/lock/journal.
Current PG UID/template, captured Pod owners, external PVC UID/Bound и отсутствие
application/Job writers проверяются строго. TLS-only change/новый unready PG Pod
разрешён только в storage_changing; остальные PG fields не игнорируются.
Application/model-stub должны отсутствовать или fully stopped, leftover RS0,
Jobs terminal/no active и все Job Pod containers exited. Unknown controllers/Pods,
CronJob/DaemonSet и ephemeral containers отказывают. Stub home recovery требует
дополнительно остановить model-stub. Обычное развертывание не изменено.

Command stdout ограничен N+1 bytes/8MiB в памяти, stderr discarded; timeout60s,
request30s и cleanup1+1s. Parser ограничен256 objects и отказывает duplicate/NaN/
continuation/deep JSON. Полный private inventory/fingerprint не сохраняется и не
выводится: terminal Job specs могут содержать inline private inputs.

| Проверка текущего изменения | Результат |
|---|---|
| Linux frozen Python3.12.15/cryptography50.0.2,15 operator modules | 634 PASS,0 skips,35.83s |
| Windows pinned Python3.12.10/cryptography50.0.2 | 149 PASS,0 skips,2.60s; current-state74/generation48/old-trust27 |
| Actual Kubernetes1.37.0 server dry-run template/Pod defaults | PASS, persistent resources0 |
| Actual bounded namespace-wide kubectl GET/JSON List command | PASS |
| Source before/after | exact343 hashes; frozen application274 unchanged |
| Scoped Ruff/CI YAML/whitespace | PASS |
| Hosted CI / real old-release recovery / physical PG exit / native old TLS after return | НЕ ВЫПОЛНЕНЫ |

API proof использует реальные API-admitted specs, но historical checkpoint UID,
PVC bindings/status и old/new Pod UID синтетические. Pure validator принимает
эти admitted specs, simulated TLS rotation/unready Pod и отказывает writer/PVC
подмене. Namespace List command реально выполнил только GET; не заявляется PASS
фактического исторического installed generation или native TLS/data recovery.

Сохранены RED45 absent function,6 manager,3 terminal Pod loopholes и6 stdout-limit;
первый synthetic fixture имел невозможную одинаковую UID у StatefulSet и PVC —
исправлены fixture UID. Initial625 Linux PASS на предыдущем snapshot сохранён.
Server dry-run выявил отсутствующие в fixture application securityContext и
Qdrant automountServiceAccountToken=false/storage mount; приведены к реальному chart.
Ещё один driver отказ вызван YAML alias от общего объекта selector/labels —
исправлено только copy.deepcopy в proof driver. Product unknown-field/alias guards
не ослаблены. Финальные634/149 относятся к exact343 snapshot2. Cache_dir warning
остаётся при no:cacheprovider, skipped tests нет.

Storage recovery authorization остаётся false; old TLS native proof false.
Это не atomic namespace fence/physical Pod exit и не блокировка будущего внешнего
actor/CRD. Durable recovery claim/phases, original operator/CI остановка и pending
Helm/in-flight mutations, graceful captured PG Pod exit и native old-leaf verification
после возврата ещё OPEN. Migration may_have_started не сбрасывается; backup3 guard
сохранён. Полный T3/T4–T6 OPEN. Рабочее приложение, SQL, Helm upgrade, commit/push
и release не выполнялись.

## Current runtime / selected HTTP trust — 06.10.2026

Реализован отдельный readonly `--check-current-runtime` с автоматическими
protected generation/old TLS/current-state gates и повтором после runtime.
Runtime ConfigMap data exact saved manifest, envFrom/PG password bindings exact;
runtime Secret UID/data/type/namespace и credential/session format проверяются.
Conflicting keys/inline HTTP TLS disable отказывают. HTTP immutable CA bundle
требует независимо сохранённый protected public hash, current CA validity и UID
freeze. Отсутствующий bundle явно отмечен, не означает native HTTPS validation.

| Проверка | Результат |
| --- | --- |
| New runtime tests | 59 PASS |
| Pinned Windows four operator modules | 208 PASS,0 SKIP,5.50s |
| Linux frozen OCI/operator16 modules | 693 PASS,0 SKIP,40.01s |
| Source before/after | exact344; frozen application274 unchanged |
| Ruff/CI YAML/whitespace | PASS |
| Hosted CI / current runtime native API-SQL-HTTPS / full recovery | НЕ ВЫПОЛНЕНЫ |

Linux прогон: отдельный sourceRO контейнер UID1000/networknone/read-only/tmpDATA,
без kubeconfig. Рабочее приложение не изменялось. Red46 missing functions и
inline-disable RED1 сохранены. Начальный Windows direct pytest запуск не нашёл
pytest в pinned venv; итоговый runner добавляет main pytest site-packages после
pinned cryptography50.0.2. Tests не ослаблены, cache_dir warning сохраняется.

Secret/ConfigMap API responses bounded2MiB/N+1/60s/request30s, strict JSON; данные
и UID/type/immutable сравниваются только в памяти, в том числе между полными
passes. ResourceVersion может меняться без изменения data. Содержимое, private
hash, URL/password не экспортируются; ошибки suppress parser cause chains.
Public verdict: current_runtime_validated=true, selected_http_trust_validated
по наличию bundle; old_runtime_native_verified=false/storage_recovery_authorized=false.

Это не исторический Secret baseline: checkpoint2 не содержит runtime private
body/исторический UID. Перед old writer остаётся protected Secret/VSO version
provenance для session key либо отдельно согласованная ротация с проверкой
последствий для сессий. Native old SCRAM подтверждает действующие SQL credentials;
HTTP static CA guard не доказывает HTTPS path. Durable claim/CAS, original
pipeline fencing, graceful PG exit, native old TLS после возврата и T4–T6 OPEN.
Backup3/installedTLS guard сохранён. Release/commit/push/upgrade не выполнялись.

## Recovery claim/CAS и cooperative fence — 06.10.2026

Реализованы explicit postgres-recovery-claim, protected bounded stop record,
полные static gates, pending Helm refusal, UID/RV CAS public marker в maintenance
lock, no-effects claim owner и original operator command fence. Stop/inputs/
ordered values/journal/ns/lock/Helm status повторяются; lost reply не повторяет
запись и не снимает lock. Fresh operation-status показывает recovery ID/claimed,
ordinary unlock отказывает. Checkpoint2/migration остаются неизменными.

| Проверка | Результат |
| --- | --- |
| New claim tests | 62 PASS |
| Pinned Windows five operator modules | 270 PASS,0 SKIP,6.60s |
| Linux frozen OCI/operator17 modules | 755 PASS,0 SKIP,44.30s |
| Source before/after | exact345; frozen application274 unchanged |
| Ruff/CI YAML/whitespace | PASS |
| Kubernetes1.37.0 owned ConfigMap CAS proof | PASS, namespace и две ConfigMap |
| UID/RV/old revision conflict, fresh reader, original command refusal | PASS |
| Owned namespace deletion UID/RV + absence | PASS |
| Hosted CI / actual original CI cancellation / native old PG recovery | НЕ ВЫПОЛНЕНЫ |

API proof использует реальный namespace/lock/checkpoint UID/RV и настоящий
Manager claim/status/dispatch gate. TLS/runtime/generation gates и Helm release
status в этом **ownership-only fixture** — doubles. Это не доказательство
historical old release или фактической остановки pipeline. PostgreSQL/SQL/PVC и
рабочее приложение не затрагивались; storage_recovery_authorized=false.

Сохранены RED36 missing claim, status/CLI/owner RED и5 protected-input race, затем
mutating auth reconcile RED1. В parser tests исправлена ошибка harness: два
динамических imports создавали разные классы ProfileError для pytest.raises.
Windows symlink fallback теперь scoped monkeypatch; native symlink отказ в Linux
без skip. Ruff сначала выявил два unused test imports; удалены. Итоговые числа
относятся к exact345 snapshot. Cache_dir warning сохраняется, skips нет.

Stop record подтверждает действие оператора, не автоматически отключает CI;
CLI принимает его только с exact current journal/operation binding. Original
cooperative dispatch fence не отменяет уже отправленные команды и не покрывает
older kit/external actors/CRDs. В этом этапе record phase только claimed, без
resume/TTL/reset/adoption и без разрешения recovery effects. Durable recovery
phases, graceful actual PG exit, native old TLS/SCRAM, session-key provenance и
T4–T6 OPEN. InstalledTLS backup3 guard сохранён. Release/commit/push не выполнялись.

## Recovery-ID resume / stop_prepared — 06.10.2026

Readonly resume повторяет ownership/protected inputs/Helm и полные static gates.
Prepare-stop пишет public marker2 с captured PG/Pod/PVC binding и HTTP CA hash/null
до каких-либо storage effects. Marker1 остаётся readable; checkpoint2/migration
не меняются. Same storage identity обязательна, Ready не является native proof.
Exact request/bytes CAS permit ограничен одной записью и очищается finally.
Повтор prepare, изменённый owner/storage/input и ambiguous reply не разрешают retry.

| Проверка | Результат |
| --- | --- |
| New resume/intent tests | 42 PASS |
| Pinned Windows six operator modules | 312 PASS,0 SKIP,8.73s |
| Linux frozen OCI/operator18 modules | 797 PASS,0 SKIP,49.69s |
| Source before/after | exact346; frozen application274 unchanged |
| Ruff/CI YAML/whitespace | PASS |
| Real Kubernetes1.37 marker2 CAS/fresh prepared resume | PASS |
| Duplicate prepare/stale-RV/original dispatch refusal | PASS |
| Checkpoint unchanged / UID-RV owned namespace cleanup/absence | PASS |
| Native PG shutdown / native old recovery / hosted CI | НЕ ВЫПОЛНЕНЫ |

API fixture использует реальный namespace/lock/checkpoint UID/RV, shipping
Manager CAS permit/status/resume/prepare. Helm status и static generation/TLS/
writer/storage/runtime gates — synthetic doubles; observed Pod/PVC IDs не являются
реальными storage objects. Это проверка metadata protocol, а не old release/SQL/
physical exit. Working application, Pods/PVC/SQL и Helm upgrade не затрагивались.

RED35 missing resume/prepare/CLI сохранён; intermediate33PASS/2CLI failures затем
GREEN. Один unused test import удалён по Ruff. Дополнительно проверены post-gate
file/values/record races, exact bytes permit rejection и fresh prepared reader.
Cache_dir warning остаётся, skips нет. No release/commit/push.

Stop intent не останавливает PG и не разрешает storage recovery. Следующий этап
stop_requested/conditional scale0 обязан подтвердить выход captured containers;
API absence без termination proof при partition/unreachable/force delete не
считается завершением. Node/lease/CSI fencing и actual graceful exit ещё OPEN.
Native old TLS/SCRAM после возврата, session-key provenance и terminal/unlock
protocol также OPEN; full T3/T4–T6, installedTLS backup3 guard сохранены.


## Captured PostgreSQL graceful stop — 06.10.2026

`postgres-recovery-stop` реализует marker3 requested→completed, captured Pod
finalizer и условную PG replicas1→0. Marker intent сохраняется до effects;
terminal regular/init proof — до owned finalizer cleanup. Original checkpoint и
migration неизменны. Existing marker3 только наблюдает, не повторяет отправку
остановки. Неполный dispatch требует отдельного repair; lock не снимается.

| Проверка | Результат |
| --- | --- |
| New stop/uncertainty/record/CLI/overlay cases | 53 PASS |
| Pinned Windows seven modules | 365 PASS,0 SKIP,15.45s |
| Linux frozen OCI/operator19 modules | 850 PASS,0 SKIP,71.45s |
| Exact source before/after / frozen backend | 347 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual Kubernetes1.37 +PostgreSQL17.11 graceful stop | PASS, regular exit0/signal0 |
| Actual init terminated /successful init ready=true | PASS, exit0/signal0 |
| Actual Node Ready /fresh bound Lease | PASS |
| Native intent before conditional stop /proof before finalizer removal | PASS |
| Fresh observer/no new dispatch /checkpoint unchanged | PASS |
| Three owned attempt namespaces UID/RV cleanup/absence | PASS |
| Old template/PVC TLS recovery /CSI fencing /hosted CI | НЕ ВЫПОЛНЕНЫ |

Native proof использует настоящий PG процесс и shipping Manager stop/CAS/observer
на отдельном emptyDir. Полные saved generation/TLS/runtime/Helm gates — fixture
doubles; это не native TLS/PVC recovery. Synthetic trust-only startup является
параметром disposable lab, не production downgrade. Рабочий release и его
Pods/PVC/SQL не изменялись; native_old_tls_recovery_verified=false и
storage_recovery_authorized=false. Source bundle SHA256:
`1606cb0b1076c89e24a10815246a4e2124968d3f8ac779c73b609ddf50ff500b`.
Frozen operator OCI:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.

Сохранены RED28 missing stop, затем 353 Windows/838 Linux PASS +8 FAIL:
новый default keyword нарушал прежний gate invocation. Исправлен только opt-in
вызов. Следующие 361 Windows/846 Linux PASS не закрыли native gate: два native
attempt отказали до finalizer, namespaces очищены. Mutation-free dry-run подтвердил
`kubectl patch --patch-file -` → open(-)/file-not-found. Final transport использует
bounded fresh object/local narrow tests +UID/RV `kubectl replace -f -`; body только
stdin, unknown fields сохранены. Дополнительные RED2 подтвердили init readiness
и namespace binding; оба исправлены без ослабления exit proof. Final53 cases,
365/850 PASS относятся к exact347 CAS snapshot. Cache_dir warning1 остаётся;
skips0. No commit/push/release.

Deadline stop observation180s; grace≤150s, storage/Node/Lease commands≤30s и
2MiB read bounds. Полные protected gates имеют отдельные bounds; это не SLA
всего CLI. Lost intent/finalizer/scale/proof/cleanup replies не дают writer permit.
SIGKILL/unknown state/partition/API absence без durable proof оставляют lock и
owned finalizer при его наличии. Explicit pre-dispatch repair, real PVC/CSI/node
failure matrix, old native TLS/SCRAM/SQL, session-key provenance и terminal/unlock
OPEN. Full T3/T4–T6 и installedTLS backup3 guard сохранены.


## Stopped old-template restore — 06.10.2026

`postgres-recovery-restore-template`: durable marker4 requested→restored, exact
protected saved-manifest/installed-template hash и один UID/RV conditional replace
при PG replicas0. Original stop proof/checkpoint/migration сохраняются. Existing
marker4 только наблюдает; timeout/pending dispatch не запускают repeat/start/unlock.

| Проверка | Результат |
| --- | --- |
| New template/uncertainty/parser/overlay/CLI/fresh-reader tests | 28 PASS |
| Pinned Windows eight modules | 393 PASS,0 SKIP,20.93s |
| Linux frozen OCI/operator20 modules | 878 PASS,0 SKIP,91.05s |
| Exact source/frozen application before/after | 348 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual Kubernetes1.37 +PG17.11 graceful stop on disposable PVC | PASS |
| Native old-template UID/RV CAS while PG0 | PASS |
| Same StatefulSet/PVC/PV UID /no new Pod | PASS |
| Fresh completed observer/no redispatch /journal unchanged | PASS |
| Owned namespace/PVC/PV deletion and absence | PASS |
| Native old TLS/SCRAM/SQL integrity /new PG start /hosted CI | НЕ ВЫПОЛНЕНЫ |

Native fixture подтверждает реальную остановку и условное изменение template на
том же disposable PVC/PV. Старый template отличается синтетической annotation;
полные saved package/generation/TLS/runtime/Helm gates — doubles. Это не actual
TLS projection rollback, данные после возврата не импортировались и SQL integrity
не проверялась. Synthetic trust-only startup применяется только к лаборатории.
Working release/Pods/PVC/SQL не затрагивались. `new_pod_started=false`,
`native_old_tls_recovery_verified=false`, `storage_recovery_authorized=false`.

Source bundle SHA256:
`8a713381336fb209b402a77121e9519a47d824cb41ce2aea04afcc31bdcc32cd`.
Frozen OCI:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.
Сохранены RED21 missing method и intermediate67PASS/7FAIL: fixture изменяла
synthetic checkpoint после того, как reader зафиксировал его SHA. Использован
fresh reader; API-double различает stop и stopped-template mutation. Product
checkpoint guard не ослаблен. Ruff unused fixture variable удалена; final28 cases
и393/878 относятся к exact348. Cache_dir warning1 остаётся, skips0.

Следующий этап — отдельный bounded PG start с новым Pod UID, CSI/Node policy и
native old TLS/SCRAM/SQL proof. Current Node/Lease — reachability check, не
атомарный fence от внешнего оператора. Explicit pre-dispatch repair, session-key
provenance и terminal/unlock остаются OPEN. Full T3/T4–T6 и installedTLS backup3
guard сохранены. No release/commit/push.


## Bounded old-PG start — 06.10.2026

`postgres-recovery-start` добавляет marker5 intent перед единственным UID/RV
replicas0→1, обязательный protected storage-fencing-record и first-observed
Pod/Node identity binding до Ready. Existing marker5/fresh reader только наблюдают;
original checkpoint не меняется, app/import/unlock не разрешены.

| Проверка | Результат |
| --- | --- |
| New start/fencing/lost-reply/UID-node-race/CLI/parser tests | 40 PASS |
| Pinned Windows nine modules | 433 PASS,0 SKIP,30.79s |
| Linux frozen OCI/operator21 modules | 918 PASS,0 SKIP,149.16s |
| Exact source /frozen application before/after | 349 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 disposable PVC stop→old template→new Pod | PASS |
| Same StatefulSet/PVC/PV /new bound Pod UID /SQL seed42 survives | PASS |
| Intent before start /fresh observer no dispatch /checkpoint unchanged | PASS |
| Owned namespace/PVC/PV deletion and absence | PASS |
| Actual old TLS/SCRAM/application integrity /hosted CI /CSI faults | НЕ ВЫПОЛНЕНЫ |

Native proof использует lab-only trust startup, synthetic old-template annotation,
сгенерированную lab fencing attestation и doubles saved package/TLS/runtime/Helm
gates. Контрольная SQL строка подтверждает сохранение volume между Pod, не полную
целостность приложения и не TCP verify-full/SCRAM. Working release не менялся.
`new_pod_ready=true`, `old_tls_native_verified=false`,
`storage_recovery_authorized=false`; lock сохранён до следующих проверок.

Source bundle SHA256:
`8ac9d45a5787b669e8e850afc26e262ed62a47bf0b1746bf9a07f18e5482b9cb`.
Frozen OCI:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.
Сохранены RED23 missing method; intermediate3FAIL/48PASS stale ConfigMap fixture
reference исправлен fresh marker read. Отдельные RED1 смены first-unready Pod и
RED1 смены Node после scheduling закрыты durable first-observed UID/node binding;
production guards не ослаблены. Final40/433/918 без skips; cache_dir warning1.

TTL решения≤15min актуален перед новым dispatch; historical observer проверяет
тот же SHA на requested_at и не повторяет scale. Фактическое physical/platform
fencing обязателен оператор подтвердить до выдачи protected файла; наличие JSON
не является проверкой CSI/partition. Native old TLS/SCRAM/SQL compatibility,
platform fault matrix, explicit pre-dispatch repair, session-key provenance и
terminal/unlock OPEN. Full T3/T4–T6 и installedTLS backup3 guard сохранены.
No release/commit/push.


## Native old TLS restoration and read-only SQL — 06.10.2026

Fixed recovery native probe и strict bounded verdict реализованы отдельно от
recovery dispatch permission. Marker5, original checkpoint и ordinary fence
не менялись; native completion/writer/app/unlock не разрешены.

| Проверка | Результат |
| --- | --- |
| New fixed command/verdict negative cases | 26 PASS |
| Pinned Windows10 modules | 459 PASS,0 SKIP,29.80s |
| Linux frozen OCI/operator22 modules | 944 PASS,0 SKIP,154.82s |
| Exact operator source /frozen backend before-after | 350 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 real server-new→server-old Secret/template recovery | PASS |
| Graceful stop/new Pod/same StatefulSet-PVC-PV/SQL42 preserved | PASS |
| Native old leaf/verify-full/SCRAM/transaction read-only/SELECT1 | PASS |
| Wrong CA/password/expected changed leaf rejected | 3 PASS |
| Source274/image identity/client-server projected permissions | PASS |
| CP/marker5 unchanged /fresh observer no redispatch /owned cleanup | PASS |
| Production native Job/completion protocol /application integrity /hosted CI | НЕ ВЫПОЛНЕНЫ |

Сервер действительно возвращает предыдущий immutable TLS Secret с другой CA/leaf,
а client использует previous CA/CRL. Positive Job фиксированного probe делает
только чтение SQL; отдельный lab socket seed42 показывает сохранение PVC между
Pod. SQL schema compatibility/totals/content/application session-key не доказаны.
Full saved package/generation/runtime/Helm gates Manager остаются fixture doubles,
fencing решение — synthetic lab attestation. No working release/Compose/local
stack upgrade. Marker5 storage_ready неизменен; native completion не journaled.

Source bundle SHA256: `1ac520cf7e8a524a4cacded46324bc0d6586ea32e948ba57465fd7e64bf5c522`.
Frozen Docker OCI index:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`.
CRI repoDigests соответствуют этому index и import_index
`sha256:4216972be8981a892d3d93021b49bbfef3405c9f84ccf357a7dfc7b9596848fc`;
config_digest
`sha256:2d07a2dad16e1e2c1ac99b9a7fd081bda9ac5fd627965cab0f043c22919d9d6b`.
Job использует pinned frozen repoDigest; runtime image и274 source повторно
проверены. Repo/index/config не выдаются за один одинаковый digest.

RED26 missing helper сохранён. Native fixture REDs: required PVC placeholders,
missing image references и ErrImageNeverPull по incorrect repository/index alias.
Read-only Helm validation/CRI metadata локализовали причины; изменены только lab
inputs. Chart/schema/native guard не ослаблены. Failed namespaces/PVC/PV очищены,
каждый повтор изолирован. Cache_dir warning1 сохраняется, final skips0.

Следующий этап — durable native Job intent/UID/template/resume/cleanup protocol
с повторами current-state/trust/runtime; затем SQL compatibility/integrity и
session-key provenance перед terminal/app/unlock. Platform fault matrix,
pre-dispatch repair, full T3/T4–T6 и installedTLS backup3 guard остаются OPEN.
No release/commit/push.


## Durable native Job preparation — 06.10.2026

`postgres-recovery-prepare-native` фиксирует exact proposal marker6 перед будущим
Job create. Сам Job не создаётся. Старые markers1–5 readable; prepared resume
повторяет protected generation, TLS/runtime и PG/node/PVC identity; checkpoint
и migration неизменны. Dispatch fence/no-active-writer inventory не ослаблены.

| Проверка | Результат |
| --- | --- |
| New marker6/Job proposal/parser/uncertainty/no-create/CLI tests | 31 PASS |
| Pinned Windows11 modules | 490 PASS,0 SKIP,41.34s |
| Linux frozen OCI/operator23 modules | 975 PASS,0 SKIP,197.78s |
| Exact source /frozen backend before-after | 351 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 old TLS recovery/read-only native positive +3 negatives | PASS |
| Actual marker6 UID/RV CAS /fresh prepare+resume /no additional Job | PASS |
| Original checkpoint unchanged /same PVC/PV /SQL42 preserved | PASS |
| Owned namespace/PVC/PV cleanup and absence | PASS |
| Production Job dispatch/UID-observer/cleanup/native completion | НЕ РЕАЛИЗОВАНЫ |

Proposal использует real old client/server TLS projection/CA/CRL и pinned saved
backend. Native lab Manager full saved package/generation/runtime/Helm prerequisites
остаются fixture doubles, physical fencing — lab attestation. Current-state
inventory full acceptance и application SQL compatibility/session-key не доказаны.
Actual4 lab Jobs подтверждают TLS/read-only probe независимо; подготовка marker6
не добавила ни одного Job. `native_job_created=false`, completion/writer/unlock false.

Source bundle SHA256: `5dbea5c36112ceea2fb3336333d0c3657c55a3061e886e2711e0870cf6499135`.
Frozen OCI index:
`sha256:866db1ee92b084ef48e6cbc0ab77bb01d5dda29f8fc9f5642669bc57e36c2500`;
actual CRI imageRef import index4216972b и frozen source274 повторно проверены.
Сохранены initial fixture7ERROR (ошибочное имя helper), RED7 missing method,
intermediate26PASS/1FAIL Windows symlink privilege1314. Windows symlink refusal
проверяется targeted fixture, Linux использует настоящий symlink; guard не ослаблен.
Additional parser empty-old-pods сразу PASS существующего stop binding, новая
runtime гипотеза не принята. Cache_dir warning1 сохраняется; final skips0.

Следующий этап — one scoped native Job dispatch с durable intent и exact captured
UID, active-readonly overlay, bounded verdict observer и owned cleanup. Нельзя
разрешать active Job по role label/name или переносить past verdict после PG/Job
подмены. Schema/SQL integrity, session-key provenance, platform fault matrix,
pre-dispatch repair, terminal/unlock и full T3–T6/backup3 guard OPEN.
Working release/local/Compose не обновлялись; no commit/push/release.

## Durable native Job dispatch — 06.10.2026

`postgres-recovery-dispatch-native`: marker7 create intent before one scoped
Job create, then first observed UID CAS; fresh observer/read-only namespace
overlay after lost reply. Markers1–6 readable; original checkpoint/migration,
local/Compose and working release unchanged. Native completion/writer/unlock false.

| Проверка | Результат |
| --- | --- |
| New dispatch/UID/lost reply/strict reader inventory/CLI/fencing tests | 42 PASS |
| Pinned Windows12 modules | 532 PASS,0 SKIP,57.82s |
| Linux frozen OCI/operator24 modules | 1017 PASS,0 SKIP,289.82 |
| Exact source /frozen backend before-after | 352 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 old TLS restore +native positive/3 negatives | PASS |
| Actual one Job create /lost response /fresh UID CAS /no redispatch | PASS |
| Exact actual admitted Job and Pod template /native readonly verdict | PASS |
| Same PVC/PV /SQL42 preserved /original checkpoint unchanged | PASS |
| Owned namespace/PVC/PV normal cleanup and independent absence | PASS |
| Production durable terminal verdict /owned Job cleanup /writer/unlock | OPEN |

Native recovery uses real client/server CA/CRL/leaf/key projections and pinned
backend; fifth Job is created through production dispatch, its lost response
is injected after actual API creation. Fresh Manager journals same Job UID,
then repeated dispatch/resume observes it without a second create. Actual fixed
probe finishes with readonly native TLS/SCRAM/SQL verdict, but production marker7
does not persist native completion. Whole package/generation/runtime/Helm gates
remain doubles; application data emptyDir, PG actual owned PVC, fencing lab-only.
Exact readonly application PVC/unknown writer boundaries covered by unit tests.
No target platform/session-key/application schema compatibility acceptance.

Source bundle SHA256: `6b937fea4926c0aa14be07baf1be8a653327ff3e2cf05db2724fb14d88948208`; frozen OCI index866db1ee and actual
equivalent CRI import index4216972b/source274 verified before-after. Capacity
measured before owned proof; no existing service stopped. Native namespace/PVC/PV
normal UID/RV cleanup and absence PASS.

Initial RED13 then RED14/21PASS retained. Expanded34PASS/1FAIL localized to Python
fixture alias between Job/root template labels; JSON API boundary fixture fixed,
production root-label binding retained. Final42 and Windows532 PASS; original generation Windows530/Linux1015 and native proof retained separately.
Valid replacement fencing record exposed version6 original-body-SHA gap and is
now refused for5/6/7; threshold/policy guards unchanged. Known cache_dir warning1,
skips0. CI module added; hosted CI not run. Full T3/T4–T6/backup3 guard OPEN.

Additional RED1 Job/1 existing PASS Pod: duplicated UID could hide a foreign Job in reader filtering. Complete UID and kind/name uniqueness now checked before filtering; two regression cases added, source re-frozen and final suites/native proof repeated.

## Durable native terminal verdict — 06.10.2026

`postgres-recovery-complete-native`: marker8 captures first reader Pod UID/node/
lease before terminal wait; bounded public native verdict journaled after exact
Job/Pod success and repeated prerequisites. Original checkpoint/migration and
working application unchanged. Job is retained; app writer/unlock false.

| Проверка | Результат |
| --- | --- |
| New native completion/Pod UID/terminal/log limits/late reply/lost CAS/CLI tests | 34 PASS |
| Pinned Windows13 modules | 566 PASS,0 SKIP,87.62s |
| Linux frozen OCI/operator25 modules | 1051 PASS,0 SKIP,382.68 |
| Exact source /frozen backend before-after | 353 /274 unchanged |
| Ruff /CI YAML /whitespace | PASS |
| Actual1.37/PG17.11 old TLS restore/native positive +3 negatives | PASS |
| Actual marker8 Pod/node UID/terminal/public verdict CAS | PASS |
| Actual lost native_verified reply /fresh observer+resume /no replay | PASS |
| Original checkpoint /same PVC/PV /SQL42 continuity | PASS |
| Owned namespace/PVC/PV normal cleanup and independent absence | PASS |
| Production scoped Job cleanup /application SQL/session/terminal-unlock | OPEN |

Production fifth Job runs fixed old-client readonly native command. After actual
Job Complete/Pod Succeeded and native verdict, production completion writes
marker8; test injects lost reply after real CAS. Fresh Manager observes exact
same Pod/Node/Job UID and stored verdict, no second logs/SQL/create. Namespace
cleanup is lab resource cleanup, not yet production native Job cleanup protocol.
Whole saved package/generation/runtime/Helm gates remain doubles, app data
emptyDir/PG real disposable PVC and physical fence lab-only. No full installed
generation, application schema/SQL integrity/session-key or platform fault proof.

Source bundle SHA256: `504fd93849f507c275741f9b84fdc076cc66a90b881adfe2fe29b07e3eeae2bf`; source353/frozen274 and frozen OCI866db1ee/
equivalent CRI import4216972b before-after verified. Initial RED14 missing completion
method; expanded32 PASS. Late-first-Pod additional RED1/unscheduled already PASS1:
bounded observer now waits only not-yet-captured Pod; captured disappearance
still refuses. Missing/running negative fixtures advance clock91s, product
budgets unchanged. Final34 cases pass, cache_dir warning1/skips0 retained.
CI module added, hosted CI not run. Local/Compose/working release not upgraded;
no commit/push/release; full T3–T6/installedTLS backup3 guard OPEN.


## Durable owned native Job cleanup — 06.10.2026

`postgres-recovery-cleanup-native`: marker9 intent before exact conditional Job
DELETE; terminal reader UID/name/node retained, Foreground GC observer waits
for both Job and captured Pod absence before cleaned CAS. Original checkpoint
and lock remain; writer/unlock/storage recovery unauthorized.

| Проверка | Результат |
| --- | --- |
| New cleanup/strict marker/UID-node-RV/lost replies/GC/CLI tests | 31 PASS |
| Pinned Windows14 modules | 597 PASS,0 SKIP,113.3s |
| Linux frozen OCI/operator26 modules | 1082 PASS,0 SKIP,460.73s |
| Exact source /frozen backend before-after | 354 /274 unchanged |
| Ruff /CI YAML /whitespace /three guarded documentation mirrors | PASS |
| Actual1.37/PG17.11 old TLS/native positive +3 negatives/marker8 | PASS |
| Actual marker9 intent/UID-RV Foreground DELETE/lost response | PASS |
| Fresh observer/both Job-Pod absence/no repeated DELETE | PASS |
| Lock/original checkpoint/same PVC-PV/SQL42 continuity | PASS |
| Owned namespace/PVC/PV normal cleanup and independent absence | PASS |
| Application SQL/schema/session-key/platform/terminal-unlock/full T3–T6 | OPEN |

Actual lost response injected AFTER conditional production DELETE, then a fresh
Manager observes exact cleanup intent and GC; no second DELETE. Fresh cleaned
resume checks both absence/full prerequisites and leaves original checkpoint/
lock. Four independent lab probe Jobs remain until normal owned namespace
cleanup, fifth production reader removed by the new protocol. Full saved
package/generation/runtime/Helm native gates doubles, application data emptyDir,
PG real disposable PVC, physical fencing lab-only. No full application integrity,
session-key or platform fault proof. No working release upgrade/commit/push.

Source bundle SHA256: `42d471adcfcb261b2f6205b034d04e36f4b26424ad00be251e4c22202f8102ed`; source354/frozen274/OCI866db1ee and verified
CRI import4216972b checked. RED12 missing cleanup/CLI retained; expanded RED1/
30PASS exposed lost captured node binding in read-only GC helper. Captured name/
UID/node comparison added, then31 PASS. Intermediate Windows563 run omitted
prior34 completion cases due harness rename; result retained and NOT accepted.
Explicit full test lists corrected to include completion and cleanup modules;
Windows597/Linux1082 accepted only after rerun. Private suite harness timeout
420→600s covers additive suite, product90/30/60 budgets unchanged. cache_dir
warning1, skips0. CI module added; hosted CI/full coverage/release not run.


## Protected session rotation readonly prerequisite — 06.10.2026

User explicitly permits rotation with mandatory re-login. New readonly command
checks independently protected public issuer evidence/current runtime Secret,
never performs rotation or grants native session/writer/unlock authorization.

| Проверка | Результат |
| --- | --- |
| New record/mechanism/controller/CLI/decision/late-expiry cases | 93 PASS |
| Pinned Windows18 modules | 690 PASS,0 SKIP,184.0s |
| Linux frozen OCI/operator30 modules | 1175 PASS,0 SKIP,718.37s |
| Exact source /frozen backend before-after | 358 /274 unchanged |
| Ruff /CI YAML /whitespace /3 guarded docs | PASS |
| Synthetic old cookie rejected/new sign-in/OIDC HMAC changes | PASS |
| Actual disposable immutable runtime Secret new UID/key, DB credentials unchanged | PASS |
| Product repeated readonly check/wrong anchor refusal/Secret UID-RV binding | PASS |
| Lock/checkpoint/Job count unchanged; owned namespace/PVC/PV cleanup and absence | PASS |
| Actual provider rollback prevention/native SSO/app SQL/full T3–T6 | OPEN |

Home rotation is performed by synthetic lab issuer via exact UID/RV conditional
DELETE/recreate of ONLY owned runtime Secret in disposable namespace. No working
application, provider or production Secret is touched. Product command is readonly;
full saved package/generation/runtime/Helm gates doubles, app data emptyDir/PG
actual PVC. Actual provider trust/rollback guarantee and native production login
not proven. Public session_rotation_native_verified/storage_recovery_authorized
remain false. Durable session decision is checked below; native login/SQL/integrity/terminal/unlock OPEN.

Source bundle SHA256: `198bcee8fe6a5c7c26d8691eb5ca2332d1bc1ec803713673ff8b095be07e14a7`; original RED37 missing validator32.00s and
RED16 missing controller/CLI13.19s retained; focused40PASS32.64s/16PASS13.50s.
Mechanism initial2PASS/1FAIL was fixture module loading/dataclass sys.modules;
fixed actual import with path assertion. HTTP ASGITransport replaces deprecated
TestClient adapter without dependency changes; mechanism3PASS0.60s. Private CI
append initially failed on backend/tests prefix (workflow runs in backend), then
only tests/ path corrected. Product functions not duplicated. cache_dir warning1/
skips0; budgets unchanged. Hosted CI/full project/coverage/release not run.

### Durable session decision and aggregate verification

| Проверка | Результат |
| --- | --- |
| Separate public decision1/CAS response binding/no overwrite | PASS |
| Actual API lost CAS response/fresh observer/no second write | PASS |
| Native9/checkpoint/runtime Secret/remaining Job count retained | PASS |
| Late issuer expiry after final full prerequisites | RED1 retained, fixed and PASS |
| Recorded historical decision/current Secret/current Lease gates | PASS |
| Original Linux observer600 timeout without captured full result | FAIL retained, not acceptance |
| Final owned container ID/image/mount/labels/command/full logs/state | PASS |
| Final exited-only normal removal; independent namespace/PV absence | PASS |

Dedicated public decision field leaves native marker9/checkpoint2/kit3 unchanged.
Existing decisions are observed without overwrite; expiry is evaluated at the
stored decision time, while current runtime identity and all recovery gates
repeat. Strict bounded CAS response with wrong UID/name/kind/same RV refuses even
if the write happened; fresh observer reads actual persisted decision. No writer
permission follows; native session/login/provider rollback proof remains OPEN.

Expanded decision test26PASS/1FAIL was a stale mocked Lease after clock advance.
The fake Lease lives in raw GET closure; fixture now simulates continuous renewal
of the same captured Lease. Production freshness guard unchanged. Initial RED14
missing decision and RED1 late expiry retained. Intermediate source356/Windows653
and native readonly proof were not accepted because final expiry defect and
missing Linux aggregate evidence remained.

Final Linux detached observer retains exact owned container ID/state/full logs,
image/config/mount/labels/command and exit0/Pid0/OOMfalse/error-empty. Only after
full1175 PASS/0skip is exact exited container normally removed. Observer900s is
private additive-suite infrastructure, not a product or performance budget.
Original timeout600 remains FAIL. No foreign resources or working application
changed; hosted CI/full project/coverage/release remains unverified.

Final source358 first native run failed before CAS: bounded stdout transport refused stdin. Fake unit kube bypassed Manager.run. Actual subprocess RED1/8PASS reproduced it; bounded input<=2MiB is now permitted only for exact active CAS argv+bytes, threaded stdin/stdout, timeout/overflow/error/nonzero refuse, stderr discarded. Nine new real transport cases retained, failed native proof cleaned normally. Windows681 before fix is intermediate, not final acceptance. Product budgets unchanged.

Detached observer first compared Docker Image to config digest and refused. Containerd-backed Docker exposes the exact frozen OCI index instead. Fresh content SHA graph index/platform/config plus strict image descriptor verified it; same captured ID observed without restart and original900s deadline retained. This harness failure is retained.

## Readonly application SQL revision foundation — 06.10.2026

| Проверка | Результат |
| --- | --- |
| New pure/inline execution native failure boundaries | 50 cases |
| Pinned Windows focused module | 50 PASS,0 SKIP,0.15s |
| Frozen Linux focused module | 50 PASS,0 SKIP,0.08s |
| Exact source/frozen backend before-after | 360/274 unchanged |
| Actual old-image graph /equal head /native TLS-SCRAM-readOnly | PASS |
| Actual missing/changed/empty/multiple DB revisions | Refused, expected exit1 |
| Owned Job/Pod terminal UID/image binding/SQL42/journals unchanged | PASS |
| Normal own namespace/PVC/PV cleanup/independent absence | PASS |
| Real application structural schema/data/native login/durable reader/full T3–T6 | OPEN |

The schema version table is synthetic lab data: graph head was read from the
trusted frozen image, then an equal or invalid revision inserted ONLY in disposable
PostgreSQL to exercise the readonly probe. No actual application schema migration
or compatibility acceptance follows. Public schema_structure_verified=false,
storage_recovery_authorized=false. Product foundation has no Job/create/exec/
app/unlock permission; kit/consumer/CI integration and durable reader remain OPEN.

Native generation/runtime/Helm gates are fixture doubles; app data emptyDir and PG
real disposable PVC, issuer/fencing synthetic lab-only. Fixed old image OCI/CRI
identity and source hashes checked. New focused50/50 are additive isolated-module
checks, not a rerun of earlier aggregate source358690/1175. Source bundle SHA256:
3ea8c5fb14713dc31f8f6a794f06ba0d7a86bc75c0cec1660a19616c09b60f48. Initial collection quoting error fixed before RED30 missing
module0.31s; GREEN30/0.14s, expanded50/0.18s. Product deadlines unchanged; warnings1,
skips0. Hosted CI/full coverage/release not verified.

## Separate public SQL reader preparation — 06.10.2026

| Проверка | Результат |
| --- | --- |
| New record phases/bindings/chronology/controller/CAS/CLI | 71 cases |
| Pinned Windows21 modules | 811 PASS,0 SKIP,195.06s |
| Frozen Linux33 modules | 1296 PASS,0 SKIP,797.58s |
| Exact source/frozen backend before-after | 361/274 unchanged |
| Existing schema3 archive member set /kit/release checks | Preserved |
| Actual prepared field CAS /lost reply /fresh observer /no second write | PASS |
| Reserved SQL Job absent /native9-session1-checkpoint unchanged | PASS |
| Actual frozen old-image SQL graph/missing/equal/changed/empty/multi | PASS |
| Own namespace/PVC/PV cleanup /independent absence | PASS |
| Captured Linux container image/mount/label/command/log/state/exited-only removal | PASS |
| Dispatch/terminal/cleanup producer /structural schema/native login/full T3–T6 | OPEN |

Decoder RED53 missing implementation1.32s; GREEN1031.29s. Chronology RED2/1.04s fixed
with unchanged5s skew, GREEN1051.31s. Preparation RED14/15.54s; intermediate10PASS/
4FAIL were stale fixture ConfigMap pointers after CAS; refresh actual object, not
production gates. Positional manifest route RED1/1.51s fixed for actual CLI shape;
CLI RED1/0.35s before routing. Final controller16/19.41s/Ruff PASS. Archive exact
member set unchanged by moving owned standalone SQL code into existing TLS member.
Windows kit/release165PASS/1POSIX SKIP/3.95s was intermediate; final required Linux
includes that skipped case. Aggregate source361 never uses prior counts as new proof.

First native launch after continuation failed before cluster action because /tmp
bundle disappeared. Read-only uptime/boot evidence confirms recent VM reboot; no
claim about its power-cause. Failed runner had no live predecessor, source bundle
SHA was retransferred unchanged to a fresh directory. Repo bind uses --mount,
so missing source fails before launch. FAIL retained, no product deadline change.

Native SQL version table remains synthetic; native generation/runtime/Helm gates
doubles, issuer/fencing lab-only, app data emptyDir and actual PG PVC. Prepared
field creates no Job/SQL/app/unlock permission. Later record phases are readable
only and not producer acceptance. Full structural/schema/data/native login/
T3–T6/backup3 guard remain OPEN. No working release upgrade/commit/push.

Source bundle SHA256: a3e040940a37f2b04e599e25c39f2569ea3c4c2ef0344b336f68e19d12aeb9f2. Private additive-suite observer900s,
product60/90/30 unchanged, cache_dir warning1/skips0. Hosted CI/full project/
coverage/release remains unverified.


## T3 SQL reader dispatch acceptance (06.10.2026)

Windows871 PASS/0skip/268.3s/24 modules; Linux1356 PASS/0skip/
349.67s/36 modules. New60 identity/controller cases, source364 and
frozen backend274. Bundle SHA256 619b7acff3b092e73ea76b610ebace34afa07c375110fe6617cb7a7f17f2acf6. Project Ruff configuration,
CI references and whitespace checked. Identity RED28 missing helpers -> GREEN34;
dispatch RED14 missing method -> GREEN14; expanded21PASS/1CLIRED -> final22PASS.
Initial pytest invocation without explicit basetemp failed in fixture setup and
was not counted as behavioral RED. A root-level Ruff invocation used another
configuration and failed; explicit repository backend configuration PASS.

Actual disposable Kubernetes/PG17 old pinned backend SQL Job: prepared->intent
CAS before create, lost reply after actual create, fresh observer actual Job UID
CAS, subsequent observer no create/CAS, exact owner/proposal/Pod/scheduler/native
terminal/strict readonly revision/leaf proof and original native9/session1/CP/SQL42
continuity PASS. Original six standalone SQL acceptance cases are also exercised.
Public SQL record remains job_observed/verdict=null; no product terminal/schema
or writer authorization is inferred. Normal owned namespace/PVC/PV cleanup and
independent absence PASS; working application unchanged.

Whole native generation/runtime/Helm gates fixture doubles; application data
emptyDir, database version table synthetic, issuer/fencing lab-only. This proves
product dispatch and strict reader identity, not real structural schema/data/FS/
Qdrant/outbox integrity, native login/provider rollback, CSI fencing, full managed
recovery or TLS rollout. Durable SQL Pod/Node/verdict/cleanup, terminal/unlock,
pre-dispatch repair, T3–T6/backup3 guard and hosted CI remain OPEN.


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


## T3 SQL completion acceptance (06.10.2026)

Source365/frozen274, bundle SHA256 980c48c603037725fd88b743a2bced4f4b6dbf26c85a351930e6cdb93a5b8f5f. Windows899 PASS/0skip/
312.16s/25 modules; Linux1384 PASS/0skip/410.78s/
37 modules. New28 cases: original17 behavioral RED (missing method), nontransition
lock context5RED/1PASS, CLI1RED, then isolated28PASS and worktree84PASS. Project
Ruff/CI refs/whitespace PASS. Independent review no blocking findings; separate
same-Pod unscheduled->scheduled->verified check PASS, one create/one logs, no edits.

Actual disposable old-image Kubernetes SQL reader: first Pod/name/UID and Node/
Lease capture, terminal/read-only equality, verified CAS, lost response and fresh
verified observer without log read/create/replace/delete PASS. Original native9,
session1/checkpoint and SQL42 seed unchanged. Public completion record retained in
private evidence. Normal owned namespace/PVC/PV cleanup plus independent absence
PASS. Full generation/runtime/Helm gates are doubles; issuer/fencing lab-only,
appdata emptyDir and version table synthetic. This does not prove structural schema,
real data/FS/Qdrant/outbox, native login/provider rollback, CSI fencing or full recovery.

SQL product cleanup (not namespace proof cleanup), terminal/unlock, pre-dispatch
repair, full T3-T6/backup3 guard and hosted CI remain OPEN. Source363 observer900
failure and source364 causal fix/accepted new run remain retained unchanged.


## T3 SQL cleanup acceptance (06.10.2026)

Source366/frozen274; bundle SHA256 a2c1863857012f9e92dca027f0f017f6db42d74362b527b971e244d0588e6515. Windows939 PASS/0skip/
340.29s/26 модулей; Linux1424 PASS/0skip/549.73s/
38 модулей. Новые40 cases: initial11 behavioral RED→GREEN, расширенные36 PASS,
CLI1RED→38PASS, затем постоянные сценарии замены Job/Pod после intent и40PASS.
Focused worktree40 PASS/82.60s. Независимый review без blocking findings; отдельная
проверка замены Job после intent PASS, DELETE0, intent/lock сохранены. Ruff/CI refs/
whitespace PASS.

Настоящий Kubernetes: cleanup intent→UID/RV Foreground DELETE→потеря ответа→fresh
observer без повторного DELETE→отсутствие Job и Pod→cleaned CAS PASS. Последующий
observer не выполняет create/delete/logs/CAS. Сохранены native9/session1/checkpoint
и SQL42 seed; public cleaned record сохранён в private evidence. Owned namespace/
PVC/PV очищены штатно; независимая проверка отсутствия PASS. Свежий image content
graph проверен; unit-контейнер удалён только после штатного exit в пределах900s.

Generation/runtime/Helm native gates — doubles; issuer/fencing лабораторные,
appdata emptyDir, version table синтетическая. Структура SQL, данные/FS/Qdrant/
outbox, native login/provider rollback, CSI fencing, terminal/writer/unlock,
pre-dispatch repair и полный T3–T6/backup3 guard/hosted CI остаются OPEN. Исходный
source363 observer900 FAIL и source364 causal fix сохранены без изменения оценки.


## T3 SQL structure foundation acceptance (06.10.2026)

Source367/frozen274; bundle SHA256 561735756d8be820b6ba9ba6e2c455e3014044bc7afe9a4c47ab0a6b1f9b0827. Windows1006 PASS/0skip/
362.76s/27 модулей; Linux1491 PASS/0skip/574.0s/39
модулей. Новые67 cases; reference26 RED, catalog13 RED, fixed probe16 RED,
затем bounded/type/deadline/parity cases PASS. Последний focused67 PASS/0.25s.

Нативная PostgreSQL17.11 в отдельном owned internal Docker network: эталон создан
реальными миграциями старого образа, clone equality и13 структурных повреждений
(включая rewrite rule) проверены read-only — всего14 cases. Фиксированная программа
через настоящие verify-full/SCRAM: equal PASS, wrong leaf/schema/reference/head/
rewrite rule FAIL с точным публичным {"ok":false}, exit1 и пустым stderr —6 cases.
Реальные данные приложения не использовались. Эталон неизменен после каждого case.
PKI volumes, containers и networks удалены штатно; независимое отсутствие всех
созданных proof scopes и свежий OCI content graph PASS.

Сохранены исходные ошибки: reviewer P1 rule omission подтверждён behavioral RED;
исправление с relhasrules сначала ошибочно отвергало схему после DROP RULE.
Нативная диагностика показала relhasrules=true и rewriteCount0; исключён только
исторический lazy flag, фактический pg_rewrite остаётся обязательным. Попытка
cross-schema sequence ownership была отвергнута самой PostgreSQL до collector;
это ограничение эксперимента, а не product behavioral RED. Initial Windows wrapper
FAIL до collection из-за неверного пути теста сохранён; исправлены только пути
harness, использован новый каталог и новый frozen bundle. Пороги не менялись.

Существующий native Kubernetes recovery/cleanup повторно PASS с original
native9/session1/checkpoint/SQL42; полные generation/runtime/Helm gates остаются
doubles, issuer/fencing лабораторные, appdata emptyDir и version table synthetic.
Структура проверена самостоятельным probe, не product Kubernetes structural Job.
Trusted producer/promotion/durable structural controller, роли/grants, данные/FS/
Qdrant/outbox, native login/antirollback, CSI fencing, terminal/writer/unlock и
полный T3–T6/backup3 guard/hosted CI остаются OPEN. Исходный source363 observer900
FAIL и последующая causal correction сохранены без изменения оценки.


## T3 durable structural reader acceptance (07.10.2026)

Source377/frozen274; bundle SHA256 53842ff31251a1dfc21063d0fa2843ce878d83497e2545c3fc8da8044f492ac7.
Windows1258 PASS/0skip/344.45s/36 модулей;
Linux1743 PASS/0skip/714.22s/48 модулей. Новые252 cases
(structural200/cache26/wait26); независимые ревью без блокирующих findings.
Ruff/CI references/whitespace проверены; hosted CI не запускался.

Actual isolated Kubernetes Job на реальной схеме миграций pinned старого образа:
disabled FK trigger отвергнут, exact restoration принята; durable intents,
Job/Pod/Node/Lease, CAS/lost replies, fresh observers без replay, Foreground
UID/RV cleanup и отсутствие Job вместе с Pod PASS. Native9/session1/checkpoint и
старый SQL42 seed сохранены. Fixed offline producer: две независимые fresh PG,
byte-identical references, повтор на непустой БД refused, каталог неизменен.
Owned namespace/PVC/PV и завершившиеся контейнеры очищены штатно;
независимое отсутствие и свежий OCI content graph PASS.

Причины задержек проверены раздельно. cProfile старого unit протокола:
737 YAML parses/7.566 из12.99 CPU секунд; immutable bounded manifest cache
снизил полный протокол до4.924 CPU секунд. Это не решило native600:
source375 late672.32s и source376 late641.98s — функциональные PASS после
непройденного времени. Original FAIL остаются сохранены.

Низконакладная диагностика source376: полный сценарий643.54s, CPU33.507s;
2790 bounded reads189.087s. Отдельное парное измерение одинаковых свежих
readonly Kubernetes чтений:40/40 pairs faster, old median63.995ms/new37.430ms,
UID/resourceVersion/output SHA совпадают. Изменено только ожидание дочернего
процесса. Новый source377 проходит исходный native600 observer за
579.84s (сам сценарий568.56s, штатное удаление контейнера580.92s);
бюджет и runtime guards не увеличены. Количество обычных status polls может
меняться со временем завершения Job; обязательные проверки не сокращены.

Generation/runtime/Helm native gates — doubles; issuer/fencing лабораторные,
appdata emptyDir. Structural verdict касается public catalog, не всех данных.
Generic owned runner/protected promotion, roles/grants/global settings,
FS/Qdrant/outbox, native login/OIDC после явной ротации/antirollback, CSI fencing,
terminal/writer/unlock, pre-dispatch repair и full T3–T6/backup3 guard OPEN.
Working application/local/Compose не обновлялись; commit/push/release не выполнялись.
Source363 observer900 и source375/376 observer600 FAIL сохранены без переоценки.


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


Проверки428R2: Windows73 passed/60.76с, Linux73 passed/76.50с;
operator-kit format3 сохранился. Независимое ревью исходного428 нашло два
нарушения deadline:3 RED при30 PASS. Собственные регрессии подтвердили2 RED;
после исправления отдельно выполнены10 fault cases ревью и19 CLI:29 PASS/38.03с.
Исходные результаты и замечания сохранены, общий backend suite и hosted CI
не объявлены успешными.

Native Kubernetes1.37/PG17/TLS/SCRAM с PVC и мигрированной схемой прошёл
за190.89с при прежних cooperative520s/wrapper600s. Реальная единственная CAS,
потерянный ответ после commit, повтор без записи и отказ после смены Secret RV
проверены обычным kubectl. Proxy применялся только к подготовительным этапам;
его счётчик9664 не изменился во время проверок ключа. Родительские журналы,
checkpoint, Secret data и контрольные chat/SQL строки сохранились. Runtime/
Helm/fencing используют laboratory doubles, issuer тестовый. Собственные
namespace, PV и wrapper удалены штатно; отсутствие подтверждено независимо.
Это приёмка компонента, полная T3–T6 и целевая приёмка остаются открытыми.


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
