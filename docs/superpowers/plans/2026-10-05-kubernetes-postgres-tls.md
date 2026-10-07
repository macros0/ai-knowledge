# Bundled PostgreSQL TLS — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans.
> Выполнять задачи последовательно в существующем `codex/kubernetes-rollout`.

**Goal:** добавить проверенный opt-in TLS для bundled PostgreSQL, сохранив
первоначальный запуск без TLS и восстановление существующих данных.

**Architecture:** один PG StatefulSet, immutable version-named TLS Secrets и HBA
ConfigMap, общий client profile для backend/Jobs. Operator kit проверяет профиль,
управляет переходами и записывает backup3; прежний kit2 сохраняется отдельно.

**Tech Stack:** Helm, Kubernetes, PostgreSQL17, Psycopg3.3.6/libpq18,
Python3.11+, PyYAML6.0.3 и явно зафиксированный cryptography.

**Spec:** [дизайн и решения проверки](../specs/2026-10-05-kubernetes-postgres-tls-design.md).

**Статус:** T1 реализована и проверена 05.10.2026: Linux120/Windows59 PASS,
без skips; actual schema3 toolbox offline smoke PASS. T2 завершена: chart/Jobs и actual
initdb/restart/projected permissions PASS; final operator349/Windows31 без skips.
T3 частично реализована: static/SCRAM/native gates и budgets проверены;
static/native proof398 завершён; checkpoint foundation проверен 06.10.2026:
final Linux445, Windows34 (13 deselected), actual journal API/CLI/restart PASS.
Сохранённое поколение проверяется readonly; current old TLS static gate реализован
и проверен. Current-state readonly gate добавлен: final Windows149/Linux634 PASS
без skips; actual1.37 API defaults/list command PASS со synthetic historical bindings.
Current runtime/HTTP trust readonly barrier проверен: Linux693/Windows208 PASS
без skips на exact344 source. Recovery claim/CAS и cooperative dispatch fence
проверены: Linux755/Windows270, exact345, actual1.37 ConfigMap CAS PASS.
Recovery-ID resume и durable stop intent проверены: Linux797/Windows312,
exact346 и actual1.37 schema2 marker CAS/fresh resume PASS.
Captured graceful stop проверен: Linux850/Windows365, exact347 и actual1.37/PG17.11
terminal regular/init statuses, Node/Lease и conditional UID/RV stop PASS.
Stopped old-template restore проверен: Linux878/Windows393, exact348 и actual1.37
UID/RV template replace при PG0/same StatefulSet/PVC/PV PASS, без нового Pod.
Bounded old-PG start проверен: Linux918/Windows433, exact349, actual1.37/PG17.11
новый Pod на same PVC/PV и сохранённая synthetic SQL строка PASS. Native old
TLS/SCRAM и read-only SQL подтверждены отдельным native lab proof (06.10.2026).
Production native preparation/dispatch/terminal verdict/owned cleanup проверены:
final Windows597/Linux1082, source354; actual marker9 UID/RV delete +fresh observer PASS.
Application SQL compatibility/integrity/session-key и terminal/unlock остаются OPEN;
full dedicated storage recovery не завершён.
Полный storage recovery T3 и T4–T6 OPEN. PG TLS ещё
нельзя включать. [Evidence и ограничения](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md).

## Global Constraints

- Compact, bundled, один backend worker и одна application replica; replicas — позднее.
- TLS выключен по умолчанию. Для первоначального disabled release TLS-задачи не
  являются prerequisite, если выбранная среда не требует PG TLS. Его audit,
  backup/restore, dependency, resource и CI gates остаются обязательными.
- Установка с TLS не выпускается по одному render/file-only proof: обязательны
  T1–T6, native matrix и полный restore с актуальными trusted materials.
- UID/GID backend1000, PG999; modes0440 и read-only mounts проверяются фактически.
- Не менять local/Compose contracts. Не применять новые values к рабочему кластеру
  до изолированной приёмки. Generic документы не содержат реквизитов инфраструктуры.
- Default audit work4s/helper1s/cleanup0.5s/HTTP<5s остаются прежними; новые
  preflight/migration budgets не выдаются за гарантии shared-engine startup.
- Commit/push, upgrade работающей установки и публикация — отдельные действия;
  проверки используют свежие owned namespaces/PVC и синтетическую PKI.

## Review Focus

| Риск | Обязательный результат | Задача |
|---|---|---|
| Первый initdb ещё не имеет runtime Secret/БД | статический preflight без SQL; повтор после VSO; native gate до migration | T2/T3 |
| Изменение HBA/Secret под работающим старым Pod | content-hash ConfigMap, immutable UID checks, old backup до изменения storage | T2/T3 |
| Backup2 без PG identity | unknown, явный legacy adoption, отсутствие auto-plaintext inference | T4 |
| Старый CRL/CA истёк к disaster recovery | byte integrity отдельно от current trust; явный связанный transition | T4/T5 |
| Старые connection pools/ошибка reload | новые Pod UID и соединения, actual pg_stat_ssl/сертификат | T3/T5 |

## T1. Profile validation и согласованный operator kit

**Files:** создать `scripts/kubernetes/postgres_tls.py`,
`backend/tests/test_kubernetes_postgres_tls.py`; изменить
`scripts/kubernetes/requirements.txt`, `scripts/kubernetes/operator_tools.py`,
`scripts/kubernetes/release.py`, `deploy/ci/operator-tools/{consume.py,Dockerfile,README.md}`,
`scripts/kubernetes/tests/toolbox_smoke.py`, `backend/tests/test_kubernetes_release.py`.

**Interfaces:** `parse_profile(pod: dict, storage: dict) -> dict | None` возвращает
проверенные пути/refs/policy; `validate_materials(profile: dict, server: dict,
client: dict, now: datetime) -> dict` возвращает public identity без secret bytes.
`None` означает явно disabled template, а не неизвестный исторический профиль.

- [x] Зафиксировать cryptography version + wheel hashes и платформы Python3.11+
  Windows/Linux. Проверить advisory/license/сборку toolbox; запретить установку
  зависимостей при запуске Pod. До этого dependency gate OPEN.
- [x] RED: parser flags/unknown fields, strict base64, limits N/N+1 до decoding,
  key/cert mismatch, encrypted private key, CA constraints/EKU/SAN, CRL подпись и
  issuer/time, mutable/foreign/recreated Secret UID, canary redaction. Пределы
  server cert256KiB/key64KiB, CA256KiB/CRL256KiB, ≤16 certificate blocks.
- [x] Минимальная реализация parser/validator; не считать статическую PEM проверку
  полной native chain validation. Хранить raw private bytes только в памяти;
  исключить их из exceptions/returned identity/evidence.
- [x] Новый release/overlay/bootstrap/toolbox metadata используют schema3 и
  exact archive allowlist с новым модулем; old kit2/archive fixtures сохранены.
  Проверить new3/new3 PASS, old2/new3 FAIL до mutations, old2/old2 PASS. Kit3
  не принимает schema2 archive под видом совместимого; backup2 разбирается T4.
- [x] GREEN: `python -m pytest backend/tests/test_kubernetes_postgres_tls.py backend/tests/test_kubernetes_release.py backend/tests/test_kubernetes_toolbox.py -q`.
  Версии и результаты standalone bootstrap вне checkout включить в evidence.

## T2. Server/client chart и Jobs

**Files:** `deploy/helm/ai-knowledge/{values.yaml,values.schema.json,Chart.yaml}`,
`templates/{storage.yaml,application.yaml,_helpers.tpl}`; создать `templates/postgres-tls.yaml`;
`scripts/kubernetes/postgres_tls.py`, `scripts/kubernetes/lifecycle.py`;
создать `backend/tests/test_kubernetes_postgres_tls_chart.py`.

**Interfaces:** `attach_client_profile(operation_spec: dict, installed_template: dict) -> None`
переносит только PG client env/mount/volume, без server key; проверяет их единство.

- [x] RED: disabled не меняет storage command/HBA/volumes; все сочетания enabled/
  refs/crlEnabled, name collision, malformed env/valueFrom/duplicates отклоняются.
  Backend/Jobs получают только client Secret; frontend/server key boundary соблюдена.
- [x] TLS server args, immutable HBA с hash в имени и сохранением старой версии,
  explicit local/TCP rules из spec; `helm.sh/resource-policy: keep` требует
  документированной последующей cleanup проверки ссылок, а не бесконечного накопления.
- [x] Клиентские PGSSLMODE/ROOTCERT/CRL/GSSENCMODE/REQUIREAUTH/SSLCERTMODE из spec,
  runtime conflict checks только выбранного TLS профиля. URL query не разрешать
  произвольно. Проверить native defaults и implicit HOME files на shipping image.
- [x] Jobs наследуют installed профиль для backup и desired для migration/restore;
  legacy template без PG projection продолжает работать. Проверить initdb с
  local `okf` и fresh/nonempty PVC; не считать pg_isready TLS проверкой.
- [x] GREEN: новые chart tests + `test_kubernetes_chart.py`,
  `test_kubernetes_temporary_storage.py`, `test_kubernetes_lifecycle.py`; Helm lint.

**Checkpoint T2, 05.10.2026:** complete. Actual shipping chart на отдельном lab
namespace: fresh initdb → commit → graceful Pod replacement → новый Pod UID и
тот же PVC → чтение сохранённой строки. PG17.11/Psycopg3.3.6/libpq180006:
TCP verify-full/SCRAM/TLSv1.3, presented leaf fingerprint; plaintext, wrong CA,
hostname/password отвергнуты до и после restart. PG root999/client root1000,
0440/read-only подтверждены фактически. Final operator349/Windows31 PASS,
без skips; exact owned namespaces/PV удалены. [Отчёт](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md).
Минимальный runner из T5 создан раньше для этого gate; полный T5 остаётся OPEN.
T3 native ordering/preflight/recovery и T4–T6 не закрыты; TLS release не разрешён.

## T3. Install/provision/upgrade и управляемое recovery

**Files:** `scripts/kubernetes/lifecycle.py`, `scripts/kubernetes/postgres_tls.py`;
создать `backend/tests/test_kubernetes_postgres_tls_lifecycle.py`,
`backend/tests/test_kubernetes_postgres_tls_checkpoint.py`.

**Interfaces:** `preflight_postgres_tls(desired, installed, require_runtime_secret)`
не выполняет SQL в fresh namespace; post-storage probe использует Job с desired
profile, выдаёт только non-secret verdict и подтверждает TCP/SCRAM/ssl.

- [x] RED: отсутствие БД не ломает static preflight; VSO runtime Secret pending
  не запускает SQL; TLS Secrets отсутствуют → отказ до storage mutations. После
  VSO повторить secret validation. Existing SCRAM verifier boolean проверяется
  до stop, initial verifier — после PG bootstrap до migration.
- [x] Зафиксировать budgets: kubectl requests с request-timeout30s, client wait
  каждого bounded check ≤60s; post-storage Job activeDeadlineSeconds60 и controller
  wait≤90s. Общие storage300s/start600s/stop180s не увеличивать молча. Превышение
  не освобождает lock и не запускает следующий writer.
- [x] Durable checkpoint foundation: bounded public schema2 в protected ConfigMap,
  UID/resourceVersion CAS и lock/namespace binding; сохранение перед stop/storage/
  migration/start, повтор UID/hash/validity/values/chart checks. Отдельный readonly
  `operation-status`; incomplete TLS journal не снимается обычным recover-lock.
- [x] Отдельная offline-проверка сохранённого schema3 release/chart/operator kit:
  protected expected release SHA/revision, toolbox/image digests, bounded one-read
  inputs, exact safe archive members/JSON. Код старого kit не исполняется;
  пакет не распаковывается. Это только integrity gate, не разрешение recovery.
- [x] Readonly binding по operation ID: verified schema3 artifact render совпадает
  со всеми ресурсами saved Helm manifest, его byte hash и template/image hashes
  installed journal. Known controller-template API defaults подтверждены на
  Kubernetes1.37; неизвестные defaults/admission changes не удаляются и отказывают.
  После possible migration/terminal phase/fresh install нет разрешения recovery.
  Повтор namespace/lock/journal/values/files binding после Helm template; writes нет.
- [ ] Для управляемого storage recovery передавать protected installed release/kit
  и exact chart artifact digest; не подменять их текущим chart tree или Helm
  manifest hash. Unpinned image reference остаётся digest=null и не разрешает
  возврат старого writer. Определить проверенную нормализацию API defaults при
  сверке old rendered template с сохранённым installed template.
- [ ] Перед изменением storage сохранить operation checkpoint из spec; повторные
  UID/hash checks перед backup, mutation, migration, start. TLS false→true требует
  backup старым профилем; true→false обычным deploy отклоняется.
- [ ] RED по каждой границе: pipeline interruption, новый PG не Ready, key mismatch,
  Pod недоступен, stale UID, expired old trust, migration началась/не начиналась.
  Прежний профиль можно вернуть только отдельным recovery по operation ID; после
  migration применяется SQL compatibility policy/restore в новые PVC.
- [ ] GREEN: lifecycle tests проверяют порядок вызовов и отсутствие следующей
  мутации после отказа. Не использовать `helm rollback --force`, force-delete Pod,
  сброс lock по TTL или автоматический TLS downgrade.

**Checkpoint T3, 05.10.2026:** partial. Static preflight связывает desired и
installed profiles, namespace/Secret/HBA UID и public hashes. Fresh/VSO pending
не выполняет SQL; повторная проверка после arrival и перед дальнейшими фазами
отклоняет замену/expiry/conflicting native inputs. Existing SCRAM проверяется
boolean через owned Ready PG Pod до stop; post-storage Job подтверждает TCP,
verify-full, SCRAM и presented leaf до migration. Completed native Job сохраняется
как evidence. Sanitized failures не освобождают lock и не запускают next writer.

Final Linux398 PASS/0 skips, 78.64s, включая 49 новых lifecycle cases. Actual
Manager static/SCRAM/native gate seed+restart PASS на owned PG-only стенде;
это не полный Manager.deploy, VSO или interruption/recovery proof. API default
HBA volume0644 принимается строго; другие modes/unknown fields отвергаются.
Поздний ответ succeeded не продлевает controller deadline90s для чтения logs.
Все пять owned namespaces/PV очищены; initial FAIL сохранены.

До T4 backup3 installed-TLS deploy/backup блокируются **до lock/stop**; format2
restore в TLS target также отклоняется до import. Fresh native gate не разрешает
production TLS release или upgrade без checkpoint/recovery. Эти временные guards
снимаются только вместе с проверенным backup3, не отдельным обходом. Следующая
работа: durable operation checkpoint и recovery по operation ID; затем T4.
[Подробная приёмка](../reports/2026-10-05-kubernetes-postgres-tls-acceptance.md).

**Checkpoint foundation T3, 06.10.2026:** реализован protected namespace ConfigMap
`okf-pg-operation-<operation-id>`, schema2 (не schema operator kit/backup). Содержит
public refs/UID/hashes, chart-tree и ordered-values hashes, installed Helm manifest
hash, image refs и digest при pinning, template hashes и observed PG/PVC/Pod IDs.
Raw templates/env/keys/passwords в journal отсутствуют. Фазы монотонны;
`migration_started` пишется до writer и означает `may_have_started`, даже при
потере ответа. Lost CAS reply запрещает повтор из устаревшей памяти; новый процесс
может прочитать статус, но это не разрешение возобновить mutation.

Actual PG-only journal proof: первый PG уже создан runner; journal сохраняется
перед graceful Pod replacement. Новая Manager-инстанция и отдельный CLI читают
фазу storage_changing; unlock отказывает. После нового Pod UID/того же PVC проходят
реальные CAS updates и conditional UID/RV lock deletion; journal остаётся до owned
cleanup. Это не полный Manager.provision/deploy или recovery старого storage.
Final445 Linux/34 Windows PASS, 47 новых cases, 3 owned namespaces/PV очищены.

Next: dedicated storage recovery по operation ID с old kit/artifact/image identity,
current trust, подтверждением выхода нового Pod и SQL compatibility decision.
Первые two T3 gates и checkpoint foundation закрыты; исходный checkpoint/recovery
контракт целиком остаётся OPEN. T4 guards installed-TLS backup/upgrade не сняты.

**Checkpoint package integrity T3, 06.10.2026:** добавлен
`release.py verify-recovery-package`. Expected release SHA256/revision поступают
из protected promotion независимо от проверяемого пакета; хэши внутри release
сами по себе не дают доверия. Проверяется только schema3. Schema2 и unknown
versions отказывают; поддержка recovery старого schema2 остаётся отдельной задачей.
Результат — public identity без raw files/keys/paths. Нет extraction, исполнения
старого kit, SQL, cluster mutation или снятия lock. Пакет ещё нужно связать с
installed journal/template, нормализовать API defaults, проверить current trust,
writer absence и выход нового PG Pod перед dedicated storage recovery.

Final485 Linux PASS/0 skips, 32.14s; 39 новых package cases и отдельный CRL-expiry
regression. Windows34 PASS/5 deselected/0 skips; пять symlink cases выполнены на
Linux. Real Helm package chart0.2.0 + current kit offline PASS; все340 snapshot
files совпали, включая274 frozen backend files. Linux unit-контейнер запускался
на домашней VM с readonly source, network none и временным DATA_DIR без kubeconfig.
Это не доказательство установленного поколения или native recovery. Initial
failures сохранены, product expiry guards не ослаблены. Полный T3 и T4–T6 OPEN.

**Checkpoint saved-generation binding T3, 06.10.2026:** реализован readonly
`lifecycle.py postgres-recovery-check`. Verified package → old chart render →
exact saved Helm manifest content/byte hash → installed API template/image hashes
в journal. Это проверка **сохранённого** поколения, не current workload/writer
или certificate validity. Known defaults проверены server-side dry-run на1.37.0;
полный нормализованный template должен точно совпасть с сохранённым hash.
Неизвестные admission fields не игнорируются. Saved manifest/values должны быть
сохранены защищённо вне public journal; raw templates не копируются в journal.

Migration may_have_started/completed, terminal/provision/fresh journal, missing
UID/pinned image/manifest hash, изменённые files/namespace/lock/journal отказывают.
CLI не делает SQL, storage mutations или unlock; verdict всегда содержит
`storage_recovery_authorized: false`. Schema2 artifact recovery не поддержан.
Следующая часть T3: current old trust/native validity, отсутствие current writer,
PG/PVC/Pod UID binding, recovery checkpoint и graceful возврат storage по ID.

Final520 Linux PASS/0 skips33.39s и35 Windows PASS/0 skips0.62s. Native defaults
и actual Helm package/TLS-HBA binding PASS с **синтетическим** checkpoint на основе
реальных API-admitted template hashes; persistent objects0. Это не proof старого
installed release, native certificate recovery или full pipeline interruption.
Exact341 snapshot/274 frozen backend hashes, Ruff/YAML/whitespace PASS; CI module
добавлен, hosted CI не запускался. Полный T3 и T4–T6 OPEN, TLS release не разрешён.

**Current old trust prerequisite T3, 06.10.2026:** opt-in
`postgres-recovery-check --check-old-tls-materials` сначала проходит полный saved
package/generation gate, затем читает текущие immutable Secrets старого профиля.
UID/CA/CRL/leaf/chain hashes должны совпасть с journal, сроки проверяются по текущему
UTC. Известный отозванный сертификат в старой server chain запрещает возврат.
Delta, indirect/scoped CRL требуют отдельной поддержки и здесь отказывают.
HBA content/hash/namespace/release проверяются, UID фиксируется на время inspection;
Secrets/HBA читаются повторно. HBA UID исторически не записан в journal2: это
проверка неизменности текущего объекта, не доказательство его исторического UID.
После чтений снова проверяются protected files/ordered values и namespace/lock/journal.
Профиль без управляемого TLS отказывает: plaintext recovery не подразумевается.

Verdict разделяет `old_tls_materials_validated: true` и
`old_tls_native_verified: false`; `storage_recovery_authorized: false` сохраняется.
Это статическая проверка, не complete path builder/native libpq proof и не атомарный
снимок Kubernetes. 34 новых case: Windows69 PASS/0 skips1.19s вместе с прежними
saved-generation cases; Ruff/YAML/whitespace PASS, frozen274 unchanged. CI list
дополнен, hosted CI не выполнен. После явного разрешения на передачу исходников
проверен exact342-files snapshot в frozen Linux container:554 PASS/0 skips33.35s.
Контейнер UID1000, network none, source readonly, temp DATA_DIR, без kubeconfig.
Remote image digest совпал с ранее frozen image;274 backend hashes неизменны.
Прошлый Linux520 относится к прежнему поколению исходников.

Остаются обязательными до recovery mutation: namespace-wide отсутствие writer и
способных его запустить controllers/Jobs; актуальные PG/PVC/Pod UID и template
binding; durable recovery claim/phase journal с блокировкой исходного pipeline;
graceful выход новых PG Pods, возврат прежнего storage, native проверка старой
leaf/verify-full/SCRAM перед разрешением writer. Migration may_have_started не
сбрасывать. TLS backup3 guard сохраняется. Полный T3 и T4–T6 OPEN.

**Current-state readonly prerequisite T3, 06.10.2026:** реализован
`postgres-recovery-check --check-current-recovery-state`. Флаг автоматически
включает старую TLS-проверку после полного saved-generation gate. Дважды читается
весь namespace: Deployment/ReplicaSet/StatefulSet/DaemonSet/Job/CronJob/Pod/PVC,
без label/field selector. Между проходами повторяется old TLS/HBA; HBA UID хранится
между полными проходами. Raw inventory сравнивается только в памяти; публичный
ответ не содержит Job/Pod templates, inline env/command или их hashes.
После inspection повторяются protected files/values и namespace/lock/journal.

PG StatefulSet UID совпадает с installed и observed journal. Текущий PG template
может отличаться от old только canonical TLS projection с old/desired journaled
refs; image, PGDATA, env, security, mounts и PVC bindings остаются прежними.
Только phase storage_changing допускает ещё не записанные new PG Pod UID/template;
в остальных фазах нужны observed identities. Новый unready PG допускается для
inspection; новая неисправная TLS material не должна запрещать возврат допустимой
старой. Это не разрешение пропустить native gate после возврата.

Все claims из saved application/PG/Qdrant совпадают по именам с observed journal;
каждый текущий PVC имеет тот же UID, Bound/volumeName и не удаляется. PG/Qdrant
replicas только0/1, без volumeClaimTemplates/custom ordinals. Qdrant template
точно совпадает с saved/API-normalized template; его текущий UID фиксируется
между проходами, исторический Qdrant UID journal2 не содержит. Storage Pods имеют
точного StatefulSet owner/name, совпадающие containers/volumes/security projection,
без ephemeral containers. Готовность PG не заменяет TLS/native проверку.

Application и model-stub отсутствуют или полностью остановлены (replicas/status0,
observedGeneration актуален). Application UID/template совпадает с installed;
model-stub template совпадает с saved. Остаточные ReplicaSets только0 и с known
Deployment owner UID. Все Jobs terminal без active; каждый оставшийся Job Pod
terminal с подтверждённым выходом каждого regular/init container. Неизвестные
Pods/controllers, DaemonSet или CronJob в namespace запрещают этот compact gate.
Для home stub mode перед recovery inspection нужно остановить также model-stub.
Это явная цена консервативной изоляции; ordinary deploy/local/Compose не меняются.

Inventory ограничен256 objects/8MiB; command reader держит только N+1 bytes,
подавляет stderr и останавливает/reaps oversized process. GET timeout60s/request30s,
bounded process/reader cleanup до1+1s. Inventory не пишется на диск. Reader error,
timeout, переполнение, duplicate/NaN/deep JSON или изменение между чтениями отказывают.
Verdict: current_application_writers_absent/current_storage_identity_verified=true,
old_tls_native_verified=false, storage_recovery_authorized=false. Это API snapshot
известной compact топологии, не atomic fence/physical Pod exit proof и не защита от
будущего вмешательства внешнего оператора/CRD controller.

Final634 Linux PASS/0 skips35.83s (15 operator modules), Windows149 PASS/0 skips2.60s
(current-state74 + generation48 + old-trust27); новые80 cases. Actual1.37.0 defaults
и bounded namespace-wide GET/List PASS, persistent resources0; historical checkpoint,
PVC UID/status и simulated rotation синтетические. Это не acceptance фактического
старого installed release/physical exit/native TLS. Exact343 source/274 frozen
backend hashes, Ruff/YAML/whitespace PASS; hosted CI не выполнен.

Текущие runtime Secret/ConfigMap body validity/UID/private freeze и selected HTTP
trust теперь проверяются отдельным readonly barrier (ниже). Нативный old SCRAM,
историческое происхождение ключа сессии и effects recovery ещё не разрешены.

Следующая часть T3: durable recovery claim/phases по operation ID и блокировка
исходного pipeline перед effects; подтвердить остановку original operator/CI и
отсутствие pending Helm/in-flight mutations (snapshot сам этого не доказывает).
Повторять все gates непосредственно перед effects, не сбрасывать may_have_started.
После graceful выхода captured PG Pod — возврат старого storage на тех же PVC,
новый PG Pod UID, native old-leaf/verify-full/SCRAM; только затем рассматривать
terminal journal/unlock и запуск writer отдельным проверенным pipeline. Отдельная
политика first TLS activation/plaintext downgrade и backup3 остаются обязательными.
Полный T3 и T4–T6 OPEN; installed TLS deploy/backup guard сохраняется.

## T3. Current runtime и selected HTTP trust — readonly barrier

- [x] `postgres-recovery-check --check-current-runtime` автоматически включает
  protected package/generation, old TLS и current-state checks; после runtime
  снова проверяет current-state, runtime и files/values/namespace/lock/journal.
- [x] Old backend envFrom exact ConfigMap → Secret без prefix/optional/лишних
  источников; PG POSTGRES_PASSWORD ссылается на тот же Secret/key. Inline
  credentials, frontend envFrom и PG envFrom отказывают в этом compact профиле.
- [x] Current runtime ConfigMap data exact protected saved manifest; duplicate
  config/Secret keys, controlled PG/TLS env и inline HTTP TLS disable отказывают.
  Runtime Secret Opaque, namespace/name/nondeleting/UID, bounded string data,
  valid base64/UTF8; bundled URL без query/fragment и непустой matching password,
  session key не короче32. Это проверка формы, а не фактической SQL-аутентификации.
- [x] API GET каждого объекта: stdout2MiB/N+1, timeout60s/request30s, strict JSON
  без duplicate/NaN. Два чтения и повторные полные passes фиксируют UID/data/
  type/immutable только в памяти. ResourceVersion bookkeeping может изменяться;
  UID/data не могут. Private body и производные hashes не попадают в journal,
  public verdict, argv, stdout или файлы. Secret обновление VSO во время проверки
  означает отказ и новую отдельную инспекцию; не автоматический retry effects.
- [x] При выбранном HTTP bundle требуется **независимо защищённый** ранее
  сохранённый public `--expected-http-ca-sha256`. Это не hash текущего GET и не
  доверие только имени. ConfigMap immutable, единственный PEM key, hash exact,
  каждый сертификат CA/current validity; UID/body freeze. Отсутствующий HTTP
  bundle отмечается явно; ненужный hash без bundle/inspection отказывает.
- [x] New59 runtime tests; итоговый operator Linux693/Windows208 без skips;
  exact344 snapshot/frozen application274, Ruff/CI YAML/whitespace PASS.
  CI test list дополнен; hosted CI и native runtime API/SQL/HTTPS не выполнялись.

Public verdict содержит только flags: current_runtime_validated,
selected_http_trust_validated (false если bundle отсутствует),
old_runtime_native_verified=false, storage_recovery_authorized=false.
Не заявляется историческое совпадение Secret с состоянием до аварии: checkpoint2
не хранит private body или исторический UID runtime Secret. Перед запуском old
writer нужен независимый protected Secret/VSO version provenance для session key,
либо отдельно согласованная ротация с проверкой последствий для сессий. Нельзя
объявить новый допустимый APP_SECRET_KEY прежним ключом только по длине32.
Фактические credentials/роль проверяет native old SCRAM; статический CA barrier
не доказывает HTTPS certificate path или доступность model/OIDC endpoints.

Следующий шаг T3: durable recovery claim/CAS и остановка исходного pipeline до
любых effects, immediate repeat всех gates; captured PG Pod graceful exit,
тот же StatefulSet/PVC, новый Pod UID и native old TLS/SCRAM до writer/unlock.
Protected HTTP hash берётся из доверенного release inventory, созданного до
инцидента; отсутствие этого anchor не обходится hash из текущего кластера.
Полный T3 и T4–T6 OPEN, backup3 guard и TLS disabled default сохраняются.

## T3. Durable recovery ownership и cooperative dispatch fence

- [x] `postgres-recovery-claim`: отдельная явная операция, требует operation ID,
  все protected saved inputs и protected `--pipeline-stop-record`.
  Automatically full generation/old TLS/current-state/runtime gate; pre-migration,
  installed old TLS обязателен. Plaintext/first activation не получают adoption.
- [x] Stop record bounded4KiB/regular/no symlink/strict JSON/version1 и exact keys:
  binding (context/namespace/namespace UID/release/operation/lock UID),
  checkpoint UID/SHA, original_pipeline_stopped=true. Это **подтверждение оператора**
  о фактически остановленном исходном процессе/CI, не результат автоматической
  проверки удалённого runner. Ложный boolean не является fencing in-flight Helm.
- [x] Helm status bounded2MiB/requested context+namespace/60s; matching name,
  namespace, positive integer revision, status только deployed/failed. Pending,
  uninstalling/unknown/malformed отказывают. Status/revision повторяются перед
  и после CAS. Stop record/inputs/ordered values/journal/ns/lock повторяются;
  изменение или неоднозначный ответ сохраняют lock, не разрешают следующий effect.
- [x] Один CAS `replace` существующего maintenance ConfigMap по UID/RV сохраняет
  public `data.postgres_recovery`: schema1, binding, checkpoint UID/SHA,
  recovery ID, phase=claimed, public stop-record/release/values hashes и Helm
  status/revision. Original operation ID/checkpoint2/migration не изменяются.
  No TTL/reset/retry/delete/auto-adoption; после lost response только инспекция.
- [x] Central pre-dispatch gate в текущем operator kit запрещает original TLS
  pipeline дальнейшие kubectl/Helm effects при claim/ownership loss. Auth can-i/
  whoami и rollout status readonly; auth reconcile/exec/scale/apply/delete/Helm
  upgrade/rollback effects запрещены. Claim owner также не получает effects.
- [x] `operation-status` возвращает recovery ID/phase, если есть bound claim;
  новый reader проверяет current checkpoint/namespace/lock UID и public record.
  Ordinary recover-lock/release_lock отказывают при claim, включая malformed.
- [x] New62 tests: CAS race двух owners, lost reply/wrong UID, protected inputs
  race, stop record, migration/pending Helm, malformed records, fresh reader,
  command fence/claim-owner no-effects/unlock. Linux755/Windows270 без skips;
  exact345/frozen application274; Ruff/CI YAML/whitespace PASS.
- [x] Actual1.37 namespace+две ConfigMap: native UID/RV CAS и stale-RV refusal,
  fresh reader, original Helm command отказала **до dispatch**, checkpoint data
  unchanged; namespace cleanup по captured UID/RV и absence check PASS.
  Static/runtime/Helm release gates — синтетические doubles, не acceptance
  реального historical generation, stop confirmation или TLS/SQL recovery.

Claim не останавливает уже запущенный subprocess/Helm, older kit, внешний actor
или CRD-controller. Обязательны фактическая остановка original pipeline,
отсутствие in-flight mutations и независимый protected stop record; в этом
этапе автоматический CI cancellation adapter не реализован. API snapshot,
допустимый Helm status и ручной record сами не образуют atomic cluster fence.
Нельзя удалять/перезаписывать recovery marker или переносить lock на новый UID.
Claim phase только claimed, storage_recovery_authorized=false. Повторный claim
не является resume: owner inspection не разрешает checkpoint завершить/снять lock.

Следующая часть T3 OPEN: явное возобновление по recovery ID с immutable input
binding, отдельные durable recovery phases/CAS до effects; immediate repeat
current writer/storage/trust/runtime gates. Graceful stop captured PG Pod,
подтверждённый выход, тот же StatefulSet/PVC и новый Pod UID; native old TLS/
leaf/SCRAM до terminal/unlock/writer. Session-key provenance/согласованная
ротация и explicit plaintext activation policy также остаются обязательными.
T4 backup3 и T5–T6 OPEN; installedTLS deploy/backup guard не снят.

## T3. Recovery-ID resume и stop_prepared intent

- [x] `postgres-recovery-resume` readonly: обязательны existing recovery ID,
  original operation ID, protected saved package/manifest/values/stop record.
  Record bound к current namespace/lock/checkpoint UID/SHA; release/values/stop
  SHA и Helm revision/status должны совпасть с claim. Полные old generation,
  TLS, writer/storage и runtime gates повторяются, затем owner/inputs/Helm ещё раз.
- [x] `postgres-recovery-prepare-stop` повторяет resume дважды и записывает
  **только** public intent через lock UID/RV CAS. PostgreSQL/Pod/PVC не изменяются.
  Lost reply/conflict/changed reply or input → retained lock, no automatic retry.
- [x] Recovery marker schema1/claimed сохраняется для чтения; переход в
  marker schema2/stop_prepared добавляет captured observed PG UID/template SHA,
  Pod names/UID/owner/ready и PVC names/UID, а также protected HTTP CA hash/null.
  Это отдельная schema от checkpoint2/kit3; original checkpoint/migration неизменны.
- [x] Resume stop_prepared требует тот же captured storage binding и HTTP hash.
  Readiness может меняться: это не identity и не native TLS/auth proof.
  Pod UID, PG template/UID, PVC UID изменяться не могут. Повтор prepare отказ;
  новый reader может только инспектировать/перепроверить существующий intent.
- [x] Dispatch permit scoped к одному exact kubectl replace request и YAML bytes,
  всегда очищается finally. Он не разрешает scale/exec/apply/unlock; такие
  команды запрещены даже внутри permit и после успешной/неоднозначной записи.
- [x] New42 tests; Windows312/0skip8.73s (6 modules), Linux797/0skip49.69s
  (18 modules), exact346/frozen application274; Ruff/CI YAML/whitespace PASS.
  Actual1.37 namespace+две ConfigMap: schema2 CAS и fresh prepared resume,
  duplicate prepare/stale-RV/original dispatch отказ, checkpoint unchanged;
  owned UID/RV namespace cleanup/absence PASS. Static/Helm/storage/runtime gates
  в native metadata-only fixture синтетические; PG shutdown/SQL не выполнялись.

## T3. Captured graceful stop / observer (06.10.2026)

- [x] `postgres-recovery-stop` принимает те же protected inputs, original operation,
  existing recovery ID и stop attestation. До effects повторяются полные gates,
  PG template/UID, единственный captured scheduled Pod и fresh Ready Node/Lease.
  Unscheduled/foreign/deleting Pod, чужой finalizer, changed UID/template,
  sidecar init и grace>150s отказывают до intent. Сам attestation не отключает CI.
- [x] Public recovery marker3 `stop_requested` сохраняется UID/RV CAS **до**
  effects. Поля stop: Node name/UID, Lease UID, owned finalizer, grace, имена
  regular/init containers и proof=null. Marker1/2 readable; checkpoint2/migration
  не меняются. Это отдельный протокол от kit3/backup3.
- [x] Captured Pod удерживается собственным finalizer. Узкие условия проверяются
  на свежем объекте; Kubernetes получает полный сохранённый объект через stdin
  `kubectl replace -f -` с exact UID/RV. После защиты Pod только captured PG
  StatefulSet условно меняется replicas1→0, template/PVC не меняются. Unknown
  поля сохраняются. Exact args/body permit очищается finally; no general mutation
  permit, no force delete, no SQL/import/app start/unlock.
- [x] Confirm exit: captured UID/owner/node, same Ready Node UID/fresh bound Lease,
  terminal Pod phase и terminated status каждого regular/init container.
  Missing/running/unknown status, SIGKILL/137 и unexplained Pod absence отказывают.
  Regular ready=false; successful init ready=true допускается только при terminated.
  Public bounded exit_code/signal proof записывается `stop_completed` **до** снятия
  собственного finalizer; после этого проверяются Pod absence/PG0 и полные gates.
- [x] Timeout/lost response не повторяют stop dispatch. Existing requested/completed
  marker вызывает только observer; принятый scale/proof/cleanup может быть дочитан.
  Если intent сохранён, но finalizer/scale не отправлен, observer отказывает и
  сохраняет lock: отдельный explicit repair protocol ещё нужен. Никакой TTL/adoption.
- [x] New53 tests; Windows365/0skip15.45s (7 modules), Linux850/0skip71.45s
  (19 modules); exact347/frozen application274 unchanged. Ruff/CI YAML/whitespace
  PASS. Actual Kubernetes1.37/PostgreSQL17.11: real graceful exit0 regular/init,
  init ready=true, Node/Lease, intent-before-stop, proof-before-cleanup, fresh
  observer/no redispatch, unchanged journal и UID/RV namespace cleanup PASS.
  Native lab использует emptyDir; saved generation/TLS/runtime/Helm gates — doubles.

**Осталось до полного T3:** explicit repair для interruption до stop dispatch;
связанный native proof полных static/runtime gates на real PVC; CSI/partition/
node-reboot fencing критерии целевой среды. Ready/Lease и terminal status —
проверка доступного узла, не атомарный fence от внешнего оператора/старого kit.
RBAC: namespace-scoped get/update Pod/StatefulSet/lock/checkpoint, get Node и
get Lease в kube-node-lease; проверить минимальные права на целевой платформе,
не выдавать автоматически широкую ClusterRole.

Следующий этап — durable old-template restore на том же StatefulSet/PVC с новым
Pod UID. До изменения template повторить ownership/inputs/trust/runtime/no-writer
и stop proof; возможная миграция запрещает recovery. Далее native old-leaf/
verify-full/SCRAM/SQL integrity, только затем отдельный terminal/unlock/app-writer
protocol. Session-key provenance/согласованная ротация, plaintext activation policy
и T4–T6 OPEN. InstalledTLS backup3 guard сохранён; stop_verified не является
storage_recovery_authorized=true. Release/working home application не обновлялись.

## T3. Old-template restore при PostgreSQL0 (06.10.2026)

- [x] `postgres-recovery-restore-template` принимает existing operation/recovery ID
  и прежние protected package/chart/manifest/values/stop/HTTP trust inputs.
  До intent — full generation/trust/runtime/no-writer gates, same PG UID/template,
  PG0/no Pod, durable terminal proof и same healthy Node/fresh bound Lease.
  Possible migration, expiry/changed runtime/HTTP anchor/UID/input отказывают.
- [x] Public marker4 `template_restore_requested` записывает только
  `restore.old_template_sha256` и сохраняет stop proof. Это отдельный протокол
  от kit/backup/checkpoint. Original checkpoint/migration не изменяются.
  Старый template читается из bounded protected saved manifest, нормализуется
  известными API defaults и обязан совпасть с installed template SHA.
- [x] Перед единственной записью повторяются ownership/files/gates и captured
  template/UID/RV. Narrow ephemeral template permit допускает только installed
  template hash и PG replicas0. `kubectl replace -f -` сохраняет все остальные
  поля StatefulSet/PVC, body только stdin; оба permits очищаются finally.
  Template меняется при нулевых репликах: Pod/SQL/writer не запускаются.
- [x] Marker4 observer не повторяет restore mutation. Принятый template/lost reply
  дочитывается; pending non-applied intent сохраняет lock и требует отдельного
  explicit repair. `template_restored` пишется после exact target verification;
  PG0/no Pod, Node/Lease и все protected gates повторяются после completion.
  Readonly inventory overlay допускает only captured/old hash во requested и
  только old hash во restored; claims/UID unchanged, любые PG Pods отказывают.
- [x] New28 tests: Windows393/0skip20.93s (8 modules), Linux878/0skip91.05s
  (20 modules); exact348/frozen application274. Ruff/CI YAML/whitespace PASS.
  Actual1.37/PG17.11 on disposable PVC: graceful stop, restored template PG0,
  same StatefulSet/PVC/PV UID, no new Pod, fresh observer/no redispatch, original
  checkpoint unchanged, owned namespace/PVC/PV cleanup PASS. Full saved package/
  TLS/runtime/Helm gates native fixture — doubles; old native TLS/SQL не проверены.

Old-template PG start — реализованная часть и открытая platform приёмка:
- [x] До replicas0→1 повторяются stop/old template/PVC/PV UID, current trust/runtime,
  protected owner/inputs и namespace-wide no-writer/controller checks. Обязателен
  независимо защищённый storage-fencing-record с exact checkpoint/recovery/stop
  proof и PVC/PV binding. Он является утверждением оператора, не CSI fence.
- [ ] Проверить фактическую CSI/Node fencing policy целевой среды и её отказную
  матрицу. RWO, Node Ready/Lease или API absence не подтверждают исключительный
  доступ к storage; неизвестное состояние ограждения запрещает выдачу записи.
- [x] Marker5 start_requested предшествует единственному UID/RV CAS replicas0→1.
  Lost reply observer не повторяет dispatch; pending/non-applied intent требует
  explicit repair. Новый PG Pod: новый UID при том же owner/PVC, storage300s.
  Первый observed UID записывается в pod_observed до Ready; назначенный Node/Lease
  связывается сразу до Ready, последующая подмена/исчезновение блокирует переход.
- [x] Независимый native proof возвращает old TLS Secret на том же PG/PVC,
  подтверждает old leaf/verify-full/SCRAM/read-only SQL и сохранность synthetic
  seed; wrong CA/password/expected leaf отказывают. Это отдельный lab proof.
- [ ] Production recovery-native Job intent/UID/template/observer/completion
  protocol и application SQL compatibility/integrity до разрешения writer.
  storage_ready по-прежнему подтверждает только Ready нового Pod.
- [ ] Terminal/unlock/app writer после SQL compatibility и session-key provenance
  либо согласованной ротации. Checkpoint migration не сбрасывать. Explicit repair
  для ранее записанного, но не отправленного stop/template/start intent ещё OPEN.

Full T3/T4–T6 и installedTLS backup3 guard сохранены. Working home application,
release/commit/push не менялись; template_restored=true не означает writer permit.


### T3. Bounded old-PG start — 06.10.2026

- [x] `postgres-recovery-start` требует `--storage-fencing-record`; readonly resume
  marker5 требует тот же файл и SHA. JSON≤16KiB/strict fields/duplicates/NaN,
  version1, issued/expires с timezone с TTL≤15min; before-dispatch решение актуально.
  Поздний observer проверяет неизменное решение на recorded requested_at;
  просроченное решение не даёт нового dispatch. Marker5 не меняет checkpoint2.
- [x] PG1/new Pod/owner/old exact template, same Bound PVC/PV claimRef и fresh
  healthy Node/Lease повторяются; deadline300s, отдельные reads≤30s/2MiB.
  storage_ready/fresh reader не разрешают приложение, import или unlock.
- [x] New42 cases; Windows433/0skip30.79s (9 modules), Linux918/0skip149.16s
  (21 modules); exact349/frozen274 unchanged. Native1.37/PG17.11 на disposable
  PVC: graceful stop→old template→one start CAS→new Pod UID→SQL seed42 survives;
  namespace/PVC/PV cleanup и fresh observer/checkpoint unchanged PASS.
  Full native package/TLS/runtime/Helm gates doubles, fencing decision — lab
  attestation, old template отличается annotation. Native old TLS/SCRAM не доказаны.
- [ ] Операторский runbook: отдельно подтвердить остановку исходного pipeline и
  незавершённых команд/внешних акторов, физическую остановку прежнего процесса,
  исключительный доступ к data volume по принятой platform/CSI policy. Только
  после этого независимо защитить решение, ограничить TTL и доступ к файлу.
  Runtime может читать PVC/PV, Node и Lease; cluster-scoped PV get RBAC проверить
  в целевой среде без выдачи неограниченного ClusterRole. Boolean не ограждает узел.
  Схема решения: version/binding/checkpoint_uid/checkpoint_sha256/recovery_id,
  old_template_sha256/stop_proof_sha256, issued_at/expires_at,
  exclusive_storage_access_confirmed/external_actors_fenced=true,
  claims=[{name,uid,pv_name,pv_uid}]; stop_proof_sha256 — SHA256 canonical JSON
  stop.proof (sort_keys, separators comma/colon), никаких credentials/private bodies.

Следующий этап: native old TLS/SCRAM и SQL compatibility/provenance; затем terminal
protocol с app/session-key/unlock. Full T3/T4–T6 и installedTLS backup3 guard OPEN.


### T3. Native old TLS restoration proof — 06.10.2026

- [x] Fixed `postgres_recovery_probe_command` и bounded strict public verdict:
  libpq≥18; explicit verify-full/CA/CRL/SCRAM/gss-disable/clientcert-disable,
  TCP+pg_stat_ssl/password/SCRAM verifier; statement timeout3s и transaction
  read-only; SELECT1 и old server leaf SHA256. No SQL writes/application imports.
  Positive flags строго bool true; body≤1KiB, duplicate/NaN/private/extra/unknown
  fields отказывают. Credentials/errors/SQL values не возвращаются.
- [x] New26 tests: Windows459/0skip29.80s (10 modules), Linux944/0skip154.82s
  (22 modules); source350/frozen backend274. Actual1.37/PG17.11: initial new TLS
  Secret→graceful stop→old TLS template→new Pod на same PVC/PV→old leaf native
  read-only connection PASS. Wrong CA/password/expected changed leaf отказаны;
  protected CP/marker5 unchanged, namespace/PVC/PV cleanup PASS.
- [x] Подготовительный durable native intent (marker6/native_prepared) реализован
  и отдельно проверен ниже; create/exec/native completion не разрешены.
- [ ] Включить probe в dispatch/observe/cleanup recovery protocol. Ordinary
  dispatch fence не изменены: эта реализация сама не разрешает create/exec Job,
  native completion, writer или unlock. Нужен новый explicit intent до Job create,
  UID/owner/pinned image/command/template binding, bounded observer/lost-reply и
  scoped completed Job cleanup, full inventory/trust/runtime repeats; unknown
  active Job/Pod не освобождать из общего no-writer gate. Completed logs одного
  Job не доказывают состояние storage после его подмены/нового рестарта.
- [ ] Отдельно проверить application schema revision/SQL compatibility/integrity,
  session-key provenance/ротацию, platform fencing и terminal/app/unlock protocol.
  SELECT1/read-only transaction и synthetic seed не заменяют application proof.

Native lab использует настоящие Secret CA/CRL/key и read-only projections, runtime
credentials synthetic только memory/API stdin. Saved package/current generation/
runtime/Helm gates recovery Manager всё ещё doubles, platform fencing — lab
attestation. Эти ограничения сохраняются; full T3/T4–T6/backup3 guard OPEN.


### T3. Durable native Job preparation — 06.10.2026

- [x] `postgres-recovery-prepare-native` с protected saved release/kit/manifest,
  operation/recovery-ID/stop-record/values/chart и storage-fencing-record. Marker6
  native_prepared расширяет marker5 полем native={job_name,job_template_sha256,
  server_leaf_sha256}. Strict parser сохраняет old stop/Pod/node/trust bindings,
  не меняет checkpoint2/migration. Нет Job UID или фиктивного native verdict.
- [x] Proposal построен из protected saved old application/backend: exact pinned
  image из installed snapshot, whole old client/server TLS profile/HBA и public
  old leaf; fixed read-only SQL command, backoff0/deadline60/no token/restartNever.
  Data PVC/mount readOnly; frontend/server-key projection не попадают в Job.
  Normalized template SHA binds command/security/env/projections/resources.
- [x] Full protected gates+owner/files/old template/new PG UID/node/readiness/PVC/
  trust/runtime повторяются; reserved Job name должен отсутствовать. Two passes
  до единственного ConfigMap UID/RV CAS, затем повтор. Lost reply не повторяет
  CAS/create; prepared observer/fresh resume только сверяет exact proposal.
  Namespace no-writer gate продолжает отклонять active/unknown Jobs/Pods.
- [x] New31 tests, Windows490/0skip41.34s (11 modules), Linux975/0skip197.78s
  (23 modules); source351/frozen274. Native1.37/PG17.11 old TLS restore/native
  proof повторён; actual marker6 CAS/prepared fresh observer/no additional Job,
  unchanged checkpoint и namespace/PVC/PV cleanup PASS. Native full package/
  generation/runtime/Helm gates — doubles, platform fencing — lab attestation.
- [x] Следующий explicit transition marker7: durable create intent→one scoped
  Job create→captured Job UID/owner/image/command/template и narrow readonly
  current-state overlay; доказательство и ограничения приведены ниже.
- [x] Bounded terminal observer и durable native verdict marker8 реализованы
  и проверены ниже. Marker7 сам не разрешает positive native completion.
- [ ] Completed scoped Job cleanup остаётся следующим transition. Markers6–8
  не разрешают app writer или unlock. Unknown writers не исключать.

Actual restored native TLS connection и metadata intent не закрывают application
SQL compatibility/integrity, session-key provenance, platform failure matrix,
explicit pre-dispatch repair, terminal/unlock и full T3/T4–T6/backup3 guard OPEN.

### T3. Durable native Job dispatch — 06.10.2026

- [x] CLI `postgres-recovery-dispatch-native` требует protected recovery/fencing
  inputs. Marker7 native_create_requested сохраняется UID/RV CAS до единственного
  scoped create; native_job_observed сохраняет first actually observed Job UID.
  Marker6 readable, checkpoint2/migration неизменны. Existing7 никогда не
  повторяет create, даже если Job отсутствует; pending intent оставляет lock.
- [x] Exact saved proposal привязан к lock ConfigMap UID и checkpoint SHA.
  Private body только JSON stdin; permit exact argv+bytes очищается finally.
  Create выводит только public object name; actual Job читается bounded GET.
  PodReplacementPolicy=Failed, parallelism/completions1, deadline60/backoff0,
  restartNever/no token; unknown Job spec/admission/defaults отклоняются.
- [x] Native reader inventory overlay локальная: exact Job UID/owner/checkpoint/
  normalized template SHA, pinned image/command/security/env/projections и
  только один matching Job Pod. API-generated labels/defaults принимаются
  строго; extra command/container/ephemeral/token/writable/foreign owner/UID
  отказывают. Остальной namespace проходит исходный no-writer gate.
- [x] При lost create response first candidate UID читается реально и проверяется
  с exact intent на повторных full gates до UID CAS. Это readonly transient
  overlay, не label/name exemption или writer authorization. После capture
  disappearance/replacement отказывают. Создание Pod/status transition между
  inventory passes может безопасно отказать; следующий observer не redispatch.
- [x] Исправлен найденный пробел marker6 fencing: immutable body SHA повторяется
  для markers5/6/7. Другой валидный PV/fencing record не заменяет исходный.
  Validity decision остаётся связанной с start.requested_at.
- [x] New42 tests: Windows532/0skip57.82s (12 modules), Linux1017/0skip289.82
  (24 modules); source352/frozen274. Actual1.37/PG17.11: real one create+lost
  reply→fresh UID capture→observer/resume без второго create; actual Job/Pod
  admission, fixed read-only native TLS/SCRAM/SQL PASS. Same PVC/PV/SQL42,
  original checkpoint unchanged, owned namespace/PVC/PV cleanup PASS.
- [ ] Следующие gates: durable native terminal Pod UID/verdict до owned cleanup;
  schema compatibility/integrity/session-key provenance; platform fencing/fault
  matrix и app/terminal/unlock. Job creation или old TLS SELECT1 не закрывают их.

Native full package/generation/runtime/Helm prerequisites остаются doubles.
Application data в lab — emptyDir, PG — real disposable PVC; readonly application
PVC boundary отдельно проверена unit inventory tests. Physical fencing — lab
attestation. Full T3/T4–T6 и installedTLS backup3 guard OPEN.

### T3. Durable native terminal verdict — 06.10.2026

- [x] CLI `postgres-recovery-complete-native`, strict marker8
  native_pod_observed→native_verified. First actually observed reader Pod UID
  записывается до terminal wait; scheduled Node/Lease identity фиксируется
  отдельно, сразу после scheduling и до positive verdict. Captured replacement/
  disappearance отказывают, original checkpoint2/migration неизменны.
- [x] Only exact old-client Job UID/template/command/image и matching Pod:
  Job Complete/succeeded1/no active/failed, Pod Succeeded/one operation container,
  terminal exit0/signal0/reason Completed, no init/ephemeral. NodeReady/fresh bound
  Lease, same new PG UID/old template/PVC/trust/runtime/gates повторяются.
- [x] Bounded public logs≤1024 и flags TCP/TLS/SCRAM/SQL-readonly/roundtrip/old leaf
  сохраняются native_verified CAS только после terminal proof и full pre/post
  gates. Unknown/private/oversize/false/changed leaf/late logs отказывают.
  Controller90s/request30s/client≤60s не увеличены. Lost CAS reply — observer
  exact captured state, без replay logs/SQL или Job create.
- [x] First not-yet-observed Pod можно ждать в пределах90s с повторными gates.
  При unscheduled Pod UID уже durable; Node identity заполняется до verification.
  Если captured Pod исчез, API absence не считается exit/native proof.
- [x] New34 tests: Windows566/0skip87.62s (13 modules), Linux1051/0skip382.68
  (25 modules); source353/frozen274. Actual1.37/PG17.11 real marker8 Pod/node/
  terminal/verdict CAS +lost response→fresh completed observer/resume PASS.
  Exact actual Job/Pod/native old TLS/readonly connection, same PVC/PV/seed42,
  checkpoint unchanged и owned namespace/PVC/PV cleanup PASS.
- [ ] Next — durable owned Job cleanup intent/UID-RV foreground deletion/both Job
  and captured Pod absence; затем application schema/SQL integrity/session-key,
  platform failure matrix/pre-dispatch repair и app/terminal/unlock protocol.

Job сохранён после marker8: native_verified не выполняет cleanup или app start.
Native full package/generation/runtime/Helm gates — doubles; application data
emptyDir/PG actual PVC, fencing lab-only. Full T3/T4–T6/backup3 guard OPEN.

### T3. Durable owned native Job cleanup — 06.10.2026

- [x] `postgres-recovery-cleanup-native`: strict public marker9 наследует verified
  marker8; cleanup хранит только Job/Pod resourceVersion и aware requested_at.
  native_cleanup_requested CAS до единственного conditional raw Job DELETE с
  exact UID/resourceVersion и Foreground propagation. Scope permit очищается
  finally; stdout DELETE discarded. Captured Job/Pod/Node identity и terminal
  proof повторяются, чужие/заменённые объекты не удаляются.
- [x] Existing marker9 observer-only: lost intent/delete/cleaned CAS не повторяет
  DELETE, create, SQL, app start или unlock. Native cleaned требует отсутствия
  BOTH exact Job и captured Pod, full protected prerequisites/no-writer repeat,
  durable cleaned CAS и post-repeat. Lock/original checkpoint сохранены.
- [x] Нормальный foreground GC наблюдается readonly: aware deletionTimestamp и
  единственный foregroundDeletion Job finalizer допускаются только для сравнения
  ранее verified terminal objects. Finalizers/Pod не patch/delete/force-delete.
  Замена UID/name/node/template, неизвестные finalizers/status или reappearance
  после cleaned отказывают. Job absence не является container exit proof.
- [x] New31 tests; final Windows597/14modules/0skip/113.3s,
  Linux1082/26modules/0skip/460.73s, source354/frozen backend274.
  Actual1.37/PG17.11 marker9 intent/UID-RV Foreground DELETE/lost reply→fresh
  observer/both absence/lock and original checkpoint retained PASS; no retry.
- [ ] Pre-dispatch repair остаётся отдельным protocol: lost intent до отправки
  DELETE оставляет pending lock. Не вводить автоматический повтор удаления.
- [ ] Application schema/SQL integrity/session-key policy и provenance/rotation,
  platform fault matrix и terminal/app-writer/unlock; full T3/T4–T6/backup3 guard.
  Решение 06.10.2026: разрешена явная ротация APP_SECRET_KEY с обязательным новым
  входом всех пользователей; durable decision/key verification/login proof OPEN.

Source bundle SHA256: `42d471adcfcb261b2f6205b034d04e36f4b26424ad00be251e4c22202f8102ed`. Full saved package/runtime/Helm gates native
fixture doubles, app data emptyDir/PG actual disposable PVC, physical fence lab-only.
Working application/local/Compose unchanged; no commit/push/release upgrade.

### T3. Protected explicit session rotation prerequisite — 06.10.2026

- [x] Решение пользователя: явная APP_SECRET_KEY rotation при recovery разрешена;
  все пользователи должны войти заново. Старый key нельзя молча восстановить.
- [x] `postgres-recovery-check-session-rotation` readonly после marker9 native_cleaned:
  independent protected public record и expected SHA256 обязательны, вместе с
  original saved generation/stop/fencing/recovery bindings. Record1<=16KiB strict
  JSON связывает operation/checkpoint/recovery/rotation ID, current runtime Secret
  name/UID/resourceVersion, aware issued/expires TTL<=900s, три strict true flags:
  rotation completed, all sessions reauthentication, previous key reuse prevented.
- [x] Actual Secret reference берётся из protected old backend template. Bound
  GET<=2MiB/request30s, Opaque namespace/name/UID/RV, strong key/matching bundled
  credentials/no inline TLS controls; private body freeze только в памяти.
  Full protected gates, record/anchor/current Secret repeats; no mutations.
- [x] New93 cases (40 record/mechanism+16 controller/CLI+36 decision+1 expiry): full Windows690/18modules/
  0skip/184.0s, Linux1175/30modules/0skip/718.37s; source358/frozen274.
  Synthetic cookie старого key rejects, fresh synthetic sign-in works; actual
  OIDC profile binding changes. Old key reuse would revive old cookie, hence
  independently accepted provider prevention is mandatory.
- [x] Actual home: synthetic issuer заменил ONLY disposable immutable runtime
  Secret новым UID/new key; DB password/URL unchanged. Product command repeated
  readonly check and refused wrong anchor, lock/checkpoint/Job count unchanged.
- [x] Durable session decision/CAS/lost-reply observer проверены отдельно ниже.
- [ ] Accepted provider rotation/rollback prevention and full native application login/OIDC
  proof, application SQL/schema/integrity, terminal/app-writer/unlock remain OPEN.

Protected issuer confirms actual fresh key generation/delivery and rollback
prevention; booleans/current GET are not automatic historical key-difference
proof. Product command does not rotate provider/key, invalidate DB rows, write
metadata, run SQL, start app or unlock. Public session_rotation_attestation_validated
true/session_rotation_native_verified false/storage_recovery_authorized false.
Native package/runtime/Helm gates doubles, app data emptyDir/PG actual disposable
PVC, fencing/issuer synthetic lab-only; full T3–T6/backup3 guard retained.

Source bundle SHA256: `198bcee8fe6a5c7c26d8691eb5ca2332d1bc1ec803713673ff8b095be07e14a7`; no working/local/Compose/release upgrade,
no commit/push. Hosted CI/full coverage/production SSO/platform acceptance OPEN.


### T3 durable explicit session rotation decision — 06.10.2026

- [x] Отдельный public lock field `postgres_session_rotation` schema1<=4KiB:
  policy rotate_reauthenticate, canonical native9 SHA/public issuer SHA,
  operation/checkpoint/recovery/rotation and runtime Secret name-UID-RV,
  aware decision_at/reauthentication_required. No key/private derivative.
- [x] `postgres-recovery-record-session-rotation`: two full protected passes,
  current Secret private body compared only in memory, one exact lock UID/RV CAS
  <=30s/controller90s, strict bounded response and post-CAS full repeat.
  Existing decision never overwritten; lost response fresh observer no second CAS.
  Native marker9/checkpoint2/kit3 unchanged; no Secret/SQL/Job/start/unlock effects.
- [x] Late-expiry RED1 retained, fixed final boundary validation. Recorded decision
  checks issuer validity at decision_at; current Secret identity/full gates repeat.
  Historical observer renews same bound Lease; production freshness guard unchanged.
- [x] Home actual decision CAS/lost reply/fresh observer/no repeated write/native9
  retained; Windows690/18modules/184.0s and Linux1175/30modules/718.37s,
  0skip/source358/frozen274. New93 cases; original intermediate failures retained.
- [ ] Next: application SQL/schema compatibility, actual session/login/OIDC and
  data integrity; terminal/app writer/unlock and full T3–T6/backup3 guard OPEN.

Original Linux aggregate observer timed out600s; no retained ID/full result,
so exit0 Docker events were not accepted. Final run captures named owned container
ID, exact image/mount/labels/command, logs/state/elapsed, observes<=900s and normally
removes only proven exited owned container. No force; product60/90/30 or
performance thresholds unchanged. Final count/native/source gates are mandatory.


### T3. Readonly old-image SQL revision foundation — 06.10.2026

- [x] Fixed probe в protected old pinned backend image читает его Alembic graph и
  public.alembic_version через TCP verify-full/SCRAM/readOnly. Statement3000ms,
  connect3s, bounded<=9 version rows; только один image head и один equal DB head.
  Missing/empty/multi/unknown/mismatched revision отказывает. No env.py/migration/
  stamp/create_all/app startup, no private errors in output.
- [x] Public verdict<=2048 exact fields/strict booleans/unique JSON/public leaf:
  schema_revision_equal=true, schema_structure_verified=false. Equal revision
  alone is not structural/data integrity or writer authorization.
- [x] New focused50 Windows 0.15s/Linux 0.08s,0skip;
  source360/frozen274 before-after. Earlier aggregate690/1175 remains historical
  source358 acceptance; a new combined full suite is not claimed.
- [x] Actual1.37/PG17.11 old-image graph + missing/equal/changed/empty/multi cases
  use owned Jobs/Pods and frozen image/source, exit0/1 expected. DB version table
  is synthetic lab seed; this does not validate the real application schema.
  SQL42 continuity, unchanged native9/session decision/CP, normal owned cleanup
  and independent namespace/PV absence checked.
- [ ] Dedicated durable SQL reader intent/UID/Pod/terminal/cleanup protocol, scoped
  no-writer overlay, kit/consumer/CI integration remain next. Actual schema
  structure/FS/Qdrant/outbox integrity, native login/OIDC, compatible-newer-schema
  policy after possible migration, terminal/app/unlock/full T3–T6 OPEN.

SQL foundation module is independent and has no CLI create/exec/app/unlock permit.
Strict one-head rule follows current project database_revision_command contract;
unknown/multiple revisions are not inferred compatible from graph ancestry.
Source bundle SHA256: 3ea8c5fb14713dc31f8f6a794f06ba0d7a86bc75c0cec1660a19616c09b60f48. Native saved generation/runtime/Helm gates
fixture doubles, app data emptyDir/PG actual PVC; fencing/issuer synthetic lab-only.


### T3. Separate public SQL reader preparation — 06.10.2026

- [x] Public `postgres_application_sql` record1<=8KiB binds native9 SHA and
  session decision1 SHA, operation/checkpoint/recovery/runtime Secret name-UID-RV,
  unique SQL Job name/fixed readonly old-image proposal SHA/public server leaf.
  Strict reader states prepared/create_requested/job_observed/pod_observed/verified/
  cleanup_requested/cleaned; decoder alone does not implement these transitions.
- [x] Intent time cannot precede session decision, cleanup cannot precede intent;
  existing5s clock skew retained. Pod/node/Lease/terminal/verdict/cleanup fields
  obey exact phase rules. Revision verdict never claims structural compatibility.
- [x] `postgres-recovery-prepare-application-sql`: two full protected session/
  generation/runtime/trust/newPG/fencing/file/no-writer passes, absent reserved
  Job, ONE exact UID/RV public-field CAS, bounded stdin/stdout2MiB/request30s/
  controller90s; strict response and full post-repeat. Existing prepared/lost
  reply observer writes nothing; later phases are refused by prepare.
- [x] Fixed private proposal uses protected old pinned backend, client CA/CRL,
  readOnly application data PVC/mount, no service-account token/server key/frontend.
  Unique name/lock owner/checkpoint/native/session annotations, deadline60/backoff0.
  Public prepared does not create Job, execute SQL, start app or unlock.
- [x] SQL helpers relocated into already allowlisted postgres_tls.py before CLI
  integration; exact schema3 archive member set/format unchanged. Three SQL test
  modules in CI. New71 record/controller cases plus prior50 SQL probe cases.
  Current aggregate Windows811/21modules/195.06s and
  Linux1296/33modules/797.58s,0skip/source361/frozen274.
- [x] Actual own namespace: prepared CAS/lost reply/fresh observer/no second CAS,
  no reserved SQL Job, original native9/session decision/checkpoint retained.
  Fixed readonly SQL native six cases remain checked. Normal own cleanup and
  independent namespace/PV absence; frozen OCI identity/content graph verified.
- [ ] Next producer stages: SQL create intent/one dispatch/actual UID and Pod-Node
  capture/strict exact-reader inventory overlay/terminal logs/scoped cleanup.
  Schema structure/integrity/native login/OIDC/terminal/app/unlock/full T3–T6 OPEN.

Public application_sql_job_prepared=true/job_created=false/revision_verified=false/
schema_structure_verified=false/storage_recovery_authorized=false. Parser readable
states are not permission to create or resume future phases. Original native9/
checkpoint2/session decision1 are unchanged. Source bundle: a3e040940a37f2b04e599e25c39f2569ea3c4c2ef0344b336f68e19d12aeb9f2.
Native generation/runtime/Helm gates doubles, app data emptyDir/PG actual disposable
PVC, issuer/fencing lab-only. Earlier standalone SQL50 source360 is historical;
current helpers are shipped through the existing schema3 module.

## T4. Backup3, legacy2 и восстановление с заменой trust

**Files:** `scripts/kubernetes/lifecycle.py`, `scripts/kubernetes/postgres_tls.py`;
создать `backend/tests/test_kubernetes_postgres_tls_backup.py`;
обновить `backend/tests/test_kubernetes_runtime_trust.py` и release fixtures.

**Interfaces:** `validate_restore_profile(saved, target, transition, manifest_sha256,
context, namespace, now, *, namespace_uid) -> dict` возвращает проверенное решение; отсутствие
transition не разрешает изменение hashes/policy. Legacy identity обозначается
`unknown` отдельно от `None`/disabled.

- [ ] RED: backup3 missing PG identity, CA/CRL extra/missing/hash tampering,
  symlink/path traversal, raw CRLF bytes, частичная запись manifest, canary key
  в archive. `complete=true` записывается последним после выхода writer Job.
- [ ] Legacy2 unknown отказывает до lock/import. Только явный
  `--legacy-postgres-profile=disabled`, проверенный оператором исходный профиль,
  disabled target и отсутствие PG artifacts позволяют adoption. Старые format1
  продолжают использовать исторический kit; не добавлять неявную миграцию.
- [ ] RED: source backup с expired CA/CRL остаётся byte-valid; обычный restore
  с недействительным target отказывает. Предлагаемый `--postgres-tls-transition`
  принимает protected JSON из spec, проверяет exact manifest/source/target hashes,
  context/namespace/namespace UID/expiry/reason, canonical profile hashing из spec,
  current native trust, запрет downgrade/CRL removal. Reject duplicate JSON keys,
  bool version/NaN/unknown keys; legacy unknown не получает искусственного hash.
  Wrong binding/expired decision/изменённый Secret отклоняются до import.
- [ ] Сохранить решение/hash в restore evidence без key/password. Никаких загрузок
  CA по URL или копирования старого private key из backup. Доступность issuer и
  replacement leaf — часть DR prerequisites.
- [ ] GREEN: backup/TLS/runtime-trust/lifecycle tests; old kit отвергает backup3,
  new kit принимает валидный legacy2 только с явным adoption. Socket dump/restore
  и TCP metadata/integrity Jobs проверяются раздельно.

## T5. Настоящий TLS и projected Secret

**Files:** создать `scripts/kubernetes/tests/postgres_tls_acceptance.py`,
`backend/tests/test_kubernetes_postgres_tls_runner.py`,
`backend/tests/postgres_tls_probe.py`; расширить профильные backend tests при
необходимости, не менять frozen native assertions ради PASS.

- [ ] RED runner guards: explicit context/new namespace, owner+UID каждой удаляемой
  сущности, refusal foreign PVC/Secret/Pod, обязательные source/image digests,
  missing tools/fixtures → FAIL required run, cleanup failure → не PASS.
- [ ] Synthetic PKI, no real credentials: root/intermediate/leaf, revoked leaf,
  expired/not-yet-valid certificates/CRL, wrong CA/SAN/password, missing CRL,
  root0440/group999 key и backend0440/group1000, real read-only projection.
- [ ] Первоначальный initdb, restart, leaf rotation, CA overlap→switch→remove old
  trust; новое соединение принимает новый leaf и отвергает старый после удаления
  trust/revocation. GSS/plaintext не заменяют TLS. Проверить pg_stat_ssl,
  presented certificate fingerprint, SCRAM и commit; pool reuse не считается proof.
- [ ] Audit denied response→DB→stdout, UUID correlation/no replay, wrong trust
  bounded failure и recovery; failure budget измеряется без повышения thresholds.
- [ ] Evidence фиксирует frozen images/source hashes, platform/libpq/OpenSSL,
  assertions по каждому case и clean exit owned ресурсов. Private keys/contents
  документов/credentials в evidence отсутствуют. File-only result не заменяет T5.

## T6. Полный lifecycle, доставка и эксплуатация

**Files:** `.github/workflows/kubernetes.yml`, `deploy/ci/gitlab-kubernetes.yml`,
`deploy/ci/gitlab-kubernetes-consumer.yml`, `scripts/kubernetes/tests/configuration_acceptance.py`,
`docs/{KUBERNETES_DEPLOYMENT.md,KUBERNETES_TESTING.md,AUDIT_DELIVERY.md}`;
создать `docs/superpowers/reports/2026-10-05-kubernetes-postgres-tls-acceptance.md`.

- [ ] Одноразовый release: install→CRUD/BM25/SSE→restart→reapply+backup3→restore
  непустых данных в новые PVC; immutable audit rows/held historical outbox,
  integrity/totals, новый login, network isolation. Отдельно legacy2 adoption и
  disaster restore после истечения старого CRL с transition и новым leaf.
- [ ] Неудачная ротация, разрыв controller session и recovery из T3 проверяются
  на настоящих Pods. Состояние после отказа: app остановлен, lock сохранён,
  данные не импортируются повторно и два writer не возникают.
- [ ] До home proof измерить CPU/RAM/disk, `/tmp`+`/work`+logs, orphan/quarantine
  накопление и eviction; начальные 1Gi/256Mi не принимаются как corpus sizing.
  Стенды запускать последовательно; чужие сервисы не останавливать без нужды.
- [ ] Freeze exact candidate; выполнить относящиеся required audit matrices,
  backend coverage≥85%, frontend/project/Compose regression, dependency audit,
  package/standalone-consumer и hosted CI. Старые v7/v16 результаты не переносятся.
- [ ] Runbook: PKI owner, expiry/CRL alerts, issuer access, RPO/RTO и backup retention,
  protected values/keys provisioning, recovery kit2/kit3, Secret/HBA cleanup.
  Нативные platform/CSI/NetworkPolicy проверки остаются отдельной приёмкой.

## Критерий завершения и приоритет

T1–T6 отмечаются выполненными только с командами, exit codes, case counts,
image/source identity и сохранёнными исходными FAIL в отчёте. Implementation,
local proof, home proof и hosted/platform proof имеют отдельные статусы.
Review документации не закрывает ни один из этих пунктов. Новые блокировки
сначала локализуются; замена проверки фиктивным PASS или изменение threshold
не допускаются. Реплики frontend → API/worker split → backend replicas остаются
последующими этапами общего rollout; TLS не включает их автоматически.


## T3 checkpoint: SQL reader dispatch (06.10.2026)

- [x] Separate SQL create intent before one exact create, then actual Job UID CAS.
- [x] Observer-only recovery after lost intent/create/UID reply; no second create.
- [x] Independently derive protected old-image proposal and journal/native/session
  anchors before allowing the exact read-only Job/Pod in local inventory.
- [x] Check full namespace UID/name uniqueness before filtering; preserve unrelated
  resources and reject extra reader, changed owner/template/token/writable mounts.
- [x] CLI `postgres-recovery-dispatch-application-sql` requires saved generation,
  pipeline-stop/fencing and protected session record with independent anchor.
- [ ] Durable first SQL Pod/Node/Lease capture, terminal verdict and scoped cleanup.
- [ ] Structural schema compatibility, whole data/FS/Qdrant/outbox integrity and
  actual post-rotation login before terminal/app writer/unlock.

New60 cases; aggregate Windows871/268.3s and Linux1356/
349.67s, zero skips, source364/frozen274. Actual product SQL Job
create lost-response/fresh observer/no second create/Job UID and exact Pod/native
read-only terminal revision proof PASS. That native terminal observation is not
persisted as a product SQL verdict: public record remains job_observed/verdict=null.
Whole generation/runtime/Helm native gates remain fixture doubles; version table,
issuer/fencing are synthetic and appdata emptyDir. T3–T6 and backup3 guard remain OPEN.


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


## T3 checkpoint: durable SQL completion (06.10.2026)

- [x] Persist the first actual SQL Pod UID/name and scheduled Node/Lease identity.
- [x] Permit only the same unscheduled Pod to acquire its scheduled binding.
- [x] Repeat protected prerequisites, exact Job/Pod/Node and successful terminal
  proof before and after bounded2048-byte readonly revision logs.
- [x] One constrained verified CAS; a verified observer repeats current gates and
  terminal identity, without another SQL Job, log read or journal write.
- [x] Compare all non-SQL lockdata and stable metadata across transitions; ignore
  only SQL field/resourceVersion/managedFields. SQL Job/Pod RVs typed and bounded.
- [x] CLI requires the same old package, manifest, pipeline-stop/fencing, protected
  session record and independent anchor as SQL dispatch.
- [ ] Next: scoped SQL cleanup, both Job/Pod absence and one UID/RV Foreground DELETE.
- [ ] Structural schema and whole data/FS/Qdrant/outbox integrity, actual session
  login/provider rollback, terminal/app writer/unlock; full T3-T6 remain OPEN.

New28 cases; source365/frozen274. Windows899/312.16s/25 modules,
Linux1384/410.78s/37 modules, zero skips. Actual product SQL Pod/Node
binding, successful readonly revision and durable verified CAS/lost reply/fresh
observer without logs or write PASS; original native9/session1/CP unchanged. The
version table is synthetic; full native generation/runtime/Helm gates are doubles,
issuer/fencing lab-only, appdata emptyDir. No structural/writer authorization.


## T3 checkpoint: удаление SQL reader (06.10.2026)

- [x] После verified сохранить отдельный cleanup intent с фактическими Job/Pod RV.
- [x] Перед удалением повторить защищённые gates и точную идентичность объектов.
- [x] Один Foreground DELETE с preconditions UID/RV; сохранённый intent допускает
  только наблюдение, без повторного DELETE после прерывания или потери ответа.
- [x] cleaned CAS только после отсутствия и Job, и Pod; повторная проверка после CAS.
- [x] GC допускает только известные поля удаления; чужие объекты, замена Job/Pod,
  неизвестные finalizers и повторное появление после cleaned запрещены.
- [ ] Структурная совместимость SQL, целостность данных/FS/Qdrant/outbox, фактический
  login после ротации и защита от возврата старого ключа, terminal/writer/unlock.

Новые40 тестов; source366/frozen274. Windows939/26 модулей/
340.29s; Linux1424/38 модулей/549.73s, без skips.
В настоящем Kubernetes прошли условный DELETE, потеря ответа и новый observer
без повторного удаления. Native9/session1/checkpoint/SQL42 сохранены. Полные
native generation/runtime/Helm gates остаются doubles, версия БД синтетическая,
issuer/fencing лабораторные, appdata emptyDir. T3–T6 и backup3 guard остаются OPEN.


## T3 checkpoint: проверка структуры SQL (06.10.2026)

- [x] Строгий публичный эталон public-schema-1: digest образов backend/PostgreSQL,
  версия PostgreSQL, единственная Alembic head, hash каталога и ограниченные counts.
  Ожидаемый SHA эталона поступает независимо из защищённого выпуска.
- [x] Фиксированный read-only REPEATABLE READ probe с verify-full/SCRAM и точным
  leaf SHA; сравнение relations/columns/defaults/constraints/indexes/sequences/
  triggers/routines/RLS policies/types/extensions/rewrite rules. Установлены N+1
  пределы, 30s бюджет, statement timeout3s, lock timeout1s, общий предел1MiB.
- [x] Только hash/counts/version выходят из collector; ошибка даёт {"ok":false}.
  Структурное совпадение не разрешает writer или unlock.
- [ ] Создание эталона в отдельной пустой БД миграциями закреплённого старого образа;
  независимый protected promotion SHA, воспроизводимость и проверка происхождения.
- [x] Dedicated структурный Job/controller: durable intent, UID/Pod/Node/Lease,
  строгий verdict, lost-response observer, owned cleanup; исходный SQL record1
  остаётся revision-only. Не получать эталон из восстанавливаемой БД.
- [ ] Роли/grants и конфигурация БД, реальные данные/FS/Qdrant/outbox, login после
  ротации и защита от старого ключа, terminal/writer/unlock, полный T3–T6.

Source367/frozen274; новые67 cases, Windows1006/27 модулей/362.76s,
Linux1491/39 модулей/574.0s, без skips. В отдельной PostgreSQL17.11
прошли14 проверок каталогов и6 запусков фиксированного TLS probe. Создана настоящая
схема миграциями в тестовой БД; повреждения столбцов, ограничений, trigger, RLS,
sequence, policy и rewrite rule отвергаются. Существующий native recovery/cleanup
повторно проверен, его ограничения сохранены. На этом историческом checkpoint product structural Job ещё не был создан;
текущий статус приведён ниже.


## T3 checkpoint: durable структурный reader и ограниченное ожидание (07.10.2026)

- [x] Fixed offline producer создаёт reference1 только миграциями pinned старого
  backend в пустой scratch-БД. Две отдельные PostgreSQL дали byte-identical
  reference; повтор на непустой БД отвергнут, каталог после отказа неизменен.
- [x] Отдельный postgres_application_schema record1 после native9/session
  decision1/CLEANED revision SQL1: SHA журналов, recovery/checkpoint, current
  Secret UID/RV и independently protected reference SHA/backend/PG/head.
  Старый revision-only SQL record и остальные поля lock не меняются.
- [x] prepare/dispatch/complete/cleanup-application-schema: durable intent до
  единственного create, Job/Pod/Node/Lease binding, readonly verdict, Foreground
  UID/RV DELETE, отсутствие Job и Pod до CLEANED. Lost replies наблюдаются без
  повторного create/delete. Namespace inventory проверяется до фильтрации.
- [x] Actual Kubernetes structural Job на реальной схеме миграций старого образа:
  disabled FK trigger отвергнут, exact restoration принята; CAS/lost replies/
  fresh observers/owned cleanup. Storage writer и unlock остаются запрещены.
- [x] Source377/frozen274, новые252 cases. Windows1258/36 модулей/
  344.45s; Linux1743/48 модулей/714.22s,
  без skips. Независимые review structural/cache/wait без блокеров.
- [x] Original native600 gate пройден новым кандидатом без увеличения бюджета.
  Ранние source375/376 observer600 FAIL и source363 observer900 FAIL сохранены.
- [ ] Общий owned offline build runner и independently protected promotion:
  фиксированный producer helper не подтверждает изоляцию внешнего pipeline.
  Printed self-hash не становится trusted expected SHA.
- [ ] Roles/grants/global DB settings, реальные данные/FS/Qdrant/outbox,
  native login/OIDC после явной ротации APP_SECRET_KEY и antirollback,
  terminal/app-writer/unlock, pre-dispatch repair и полный T3–T6/backup3 guard.

Bundle SHA256: 53842ff31251a1dfc21063d0fa2843ce878d83497e2545c3fc8da8044f492ac7. Общие generation/runtime/Helm native
checks ещё doubles; issuer/fencing лабораторные, appdata emptyDir. Проверка
каталога public не означает полную приёмку восстановления. Working deployment,
локальный запуск и Compose не обновлялись; hosted CI ещё не подтверждён.

## T3: общий offline build и независимое подтверждение эталона

- [x] Offline CLI в прежнем модуле operator kit; schema3/allowlist не изменены.
- [x] Проверка actual root/selected-platform image identity, собственные internal
  networks, tmpfs, nonroot/read-only, без host ports/binds и implicit pull.
- [x] Два fresh PG, побайтно одинаковый эталон, повтор отвергнут до миграций,
  каталог неизменен; bounded execution и штатная cleanup с независимым absence.
- [x] Atomic write-once artifacts, strict внешний SHA/ref/image/head verifier;
  Windows/Linux contracts и реальное native доказательство.
- [x] Прежний kit consumer/bootstrap, module loading и воспроизводимый archive
  проверены. Новый CLI работает из извлечённого комплекта без source checkout.
- [x] Обезличенная [операторская инструкция](../../KUBERNETES_SCHEMA_REFERENCE.md),
  включая отказ/retained IDs и узкое ручное pre-dispatch cleanup решение.
- [ ] Protected pipeline promotion: независимые expected SHA/immutable storage,
  builder/toolbox/image/head identity и права promotion. Self-hash не даёт trust.
- [ ] Реальная проверка roles/grants/settings, данных/FS/Qdrant/outbox,
  all-provider invalidation и native login/OIDC/antirollback после разрешённой
  ротации ключа; terminal/writer/unlock, pre-dispatch operator repair, T4–T6.

Source392r5 adopted07.10.2026: focused509 PASS/0skip на Windows4.57s и Linux10.64s;
collection1664/2149. Actual native23.16s и independent absence PASS. Kit Windows/
Linux64 PASS+1skip (нет Helm/kubectl в unit окружении); этот standalone bootstrap
сценарий отдельно выполнен настоящим pinned toolbox PASS. Synthetic revision
не опубликован. Original r2/r3/r4 FAIL и ошибочный kit observer count сохранены.
Рабочее приложение/local/Compose не обновлялись, writer=false, fullT3=false;
полный прежний runtime matrix source377 используется отдельно, hostedCI OPEN.



Проверка package layout, 07.10.2026. После переноса source377 найден collection ERROR: три теста импортировали соседний helper без относительного пути. Исправлены только эти три импорта; существующий tracked пустой tests/__init__.py включён в manifest source378. Runtime-файлы побайтно совпадают с принятой версией377. Windows: collection1258 PASS, smoke58 PASS/3.00s, изменённые модули37 PASS/1.39s; Linux: collection1743 PASS и focused89 PASS/5.33s. Независимое ревью:37 PASS и collection1258 PASS, блокирующих замечаний нет. Завершённый собственный Linux-контейнер удалён штатно, независимое отсутствие ресурсов проверено.

Первоначальный collection ERROR сохранён; ошибочное сообщение wrapper о58 PASS до исправления явно отозвано (фактически0). Полный runtime/native matrix для test-only изменения не повторялся: используется ранее принятый source377. Hosted CI, generic owned builder/protected promotion и полная приёмка T3 остаются OPEN. Разрешение на запуск приложения и снятие запрета записи не выдано этой проверкой.


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


- [x] Закрытый bounded record и явный CLI с одной lock CAS; прежний readonly путь сохранён.
- [x] Deadline regression RED→GREEN, Windows/Linux73, fault/CLI29.
- [x] Native lost reply/idempotent/RV race и независимая штатная очистка.
- [ ] Protected publisher/promotion, provider login, antirollback, repair и activation/unlock.


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


- [x] T4 closed canonical profile и проверка protected transition metadata, включая namespace UID.
- [x] RED76→GREEN76, combined208 Windows/Linux, independent144.
- [ ] T4 archive byte protocol/backup3/legacy adoption/controller/native restore — следующие этапы.


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
