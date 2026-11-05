# Storage delivery and recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps with checkboxes.

**Goal:** Поставлять выбранные хранилища либо подключать сервисы заказчика и восстанавливать установку из независимой ночной копии.
**Architecture:** Отдельные application/infrastructure lifecycle; ownership задаётся по компонентам. Coordinated maintenance останавливает всех writers, backup имеет manifest и проверенную точку восстановления. Resilient certification выдаётся только после отказных испытаний.
**Tech Stack:** Helm/Kubernetes, выбранные в P0 PG/Qdrant/S3/RWX реализации, текущие backup tools и Python lifecycle.
**Spec:** [Согласованный дизайн](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

## Global Constraints

Все ограничения [основного плана](2026-10-31-kubernetes-replicas.md) обязательны.
Допустима остановка приложения. Данные/операторы не удаляются при uninstall app.
Не устанавливать cluster-scoped оператор и не изменять storage nodes без отдельного
platform допуска. Bare local disk, один NFS и одна физическая машина не получают
resilient label на основании числа Pod. Лицензии и exact versions фиксируются P0.

## Review Focus

Mixed ownership ломает backup; старый worker продолжает писать после scale=0;
backup Job completed без всех объектов; target restore обращается в production;
ошибка ночной копии скрывает превышение 24 часов. O1–O3 обязаны проверить эти случаи.

## Task O1: наши storage packages и external/mixed wiring

**Files:** создать `deploy/helm/ai-knowledge-storage/Chart.yaml`, `values.yaml`,
`values.schema.json`, `templates/postgres.yaml`, `templates/qdrant.yaml`,
`templates/artifacts.yaml`, `templates/network.yaml`,
`deploy/kubernetes/replicas/storage-selection.schema.json`,
`deploy/kubernetes/profiles/distributed-managed.values.example.yaml`,
`distributed-external.values.example.yaml`, `distributed-mixed.values.example.yaml`,
`backend/tests/test_replica_storage_chart.py`, `docs/REPLICA_STORAGE_OPERATIONS.md`;
изменить application chart config/network/helpers и `SECURITY.md`.

**Interfaces:** `storage-selection` — заполненный результат P0: отдельная запись
на PG/Qdrant/RWX/S3, точные image/chart versions/digests, разрешённый install scope,
reference на platform-owned operator, disk/PVC layout, replication/consistency и
backup/restore recipe. Это обязательный input O1, а не выбор библиотеки во время
runtime. Self-managed templates реализуют конкретные выбранные recipes; product
кандидаты/версии не угадываются до получения платформенных inputs.

Приложение получает только resolved endpoints, Secret/CA refs, store IDs и profile
capabilities. `managed` разворачивается отдельным infrastructure release;
`external` не создаёт никаких storage workloads/credentials. Mixed разрешён
по каждому компоненту. Основные settings не должны зависеть от конкретного
service hostname. Schema P1 пропускает endpoints через новый validator, не старую
проверку «postgres:5432 и qdrant:6333 означают bundled».

Наши PVC создаются отдельной явной операцией provision, с сохранением при uninstall
и без автозахвата existing data. Общий оператор устанавливается платформой отдельно;
chart может создавать разрешённые namespace CR, не оператор автоматически.
Секретные значения поступают через existingSecret/VSO, не Helm release values.
Basic и resilient имеют разные storage recipes, replicas и failure model.

- [ ] `test_external_creates_no_storage`, `test_mixed_ownership_is_per_component`, `test_uninstall_preserves_data_contract`, `test_runtime_resolves_external_tls`, `test_resilience_rejects_unverified_disks`: assert external manifests не содержат StatefulSet/Cluster CR/PVC для внешнего компонента; secrets absent from rendered literals; сертификат wrong hostname отклоняется; отсутствие certified provider/input блокирует resilient.
- [ ] RED: `python -m pytest tests/test_replica_storage_chart.py -q` с Helm.
- [ ] По выбранным P0 recipes реализовать install/update/backup/restore для собственного PG, Qdrant и обоих файловых вариантов RWX/S3. Stateful service использует собственную требуемую репликацию, а не общую многописательную директорию PostgreSQL/Qdrant. Выбор TLS/auth и NetworkPolicy документировать; service доступен только необходимым ролям.
- [ ] GREEN chart suite; runner contracts/storage на managed и external endpoints, включая CA rotation, wrong credentials, network deny, storage full и перезапуск. RWX и S3 проверяются отдельно; mixed smoke должен реально использовать разные ownership, а не только отличаться values.
- [ ] Commit `feat: package managed and external replica storage`.

O1 basic/bootstrap milestone (2026-11-01): independent namespace-scoped storage
chart with explicit retained PVC provision and pinned PG17/Qdrant1.19/RustFS1.0.4
recipes; external creates no resources, mixed ownership is per component. The app
chart owns no distributed storage workloads/services. Fresh migration/bootstrap
uses exact selected inputs, verified artifact operations, empty vector schema and
serialized SQL identity; existing data/configurations require O2 adoption. Common
identity includes image/source/package/namespace/CA content. Checks:92 focused
unit/chart/lifecycle tests passed;4 required live tests passed (whole PG migration,
competing init/retry, selected S3 contract/auth and Qdrant schema/nonempty refusal).
Root audit0/frontend756+1skip/ESLint/Ruff passed. These are not full backend CI or
customer/provider certification. Managed RWX/platform permission, scoped DB/S3
credential provisioning, actual TLS/rotation/restart/full-storage and recovery
qualification are still open; O1 is not complete. See REPLICA_STORAGE_OPERATIONS.

O1 storage-only TLS/restart follow-up (2026-11-01): actual PG direct-Pod TCP
verify-full/SCRAM, Qdrant REST/gRPC and RustFS TLS passed; wrong hostname, retired
CA and credentials were rejected for their specific failures. All three storage
Pods restarted onto CA2 with the original PVCs and retained an8192-byte S3 object's
SHA-256. Required native7-test cohort and separate gRPC1 passed; final chart6 passed.
Actual frontend CNI denials passed against all three live storage endpoints from
both frontend Pods, with API positive TCP controls. Coordinated running-app
CA/identity rotation, scoped credentials, full-storage, RWX, external/mixed runtime
and independent recovery qualification are still open. This narrows the earlier
pending TLS/network items; it does not complete O1 or certify customer storage.

## Task O2: maintenance, backup и restore с ночным RPO

Current-copy restore (2026-11-02): the independent20-document/100-blob archive
was restored into a fresh isolated target with new credentials/CA and three
disjoint PVCs, while all source application/model/storage Pods were stopped.
All20 rows/100 blob hashes/40 Qdrant points were verified before explicit release;
restored work remained unexecuted. Fresh scoped SQL/S3 principals passed native
controls and25 old sessions were invalidated. After release,19 visible originals
passed search/download SHA,1 remained in trash,2 new uploads and7 runtime smoke
checks passed with2/2/2 Ready. Source96 documents/PVCs were preserved. See
[current-copy recovery](../reports/2026-11-02-replica-current-copy-recovery.json).
This does not requalify the full22-stage R5 suite on the new target or close the
remaining O2 schema/key/alerts/provider gates. Production scheduleOFF.

Offline identity adoption supports a separate
bounded operator Secret file, bound to the same verified PostgreSQL transport,
while signing the scoped application settings. Windows54 checks passed with2
POSIX-only skips; Linux56/56 passed including symlink/FIFO behaviour. This closes
the software prerequisite for the subsequent source-off drill above.

O1 scoped-runtime credentials milestone (2026-11-01): a dedicated PG17 role and
RustFS1.0.4 IAM principal passed native permissions probes (10 SQL/9 S3 denials,
positive DML/row-lock/range/conditional/multipart controls). Create-only immutable
Secrets separate application inputs from an offline-only operator DB file. Actual
transition held epoch6, stopped all application/provider roles, audited the new
common fingerprint and verified unchanged held corpus/work hashes before release.
All4 API/worker processes proved actual scoped credentials, TLS, transition audits
and retired-identity rejection. All17 previous docs retained content/generation/
source/delete state; fresh DOCX completed cleanly with1publication and SHA/search.
Shared auth passed both APIs; final2/2/2 Ready0restarts. Public result:
`../reports/2026-11-01-replica-scoped-credentials.json`. This closes scoped
credentials only for the selected dedicated managed-S3 recipe; external/mixed/RWX,
running-app CA rotation and storage-full remain O1 gates at this milestone.
Production scheduleOFF.

O1 coordinated CA follow-up (2026-11-01): actual owned managed-S3 rotation passed
under held epoch8. Independent authenticated backup checked before entry; new
immutable CA/TLS resources, normal storage stops and unchanged3 PVCs/specs/images.
Held11-table corpus,90 ready blob SHA/size values and all36 full Qdrant payloads/
vectors matched. Native PG/REST/gRPC/S3 accepted new trust and refused retired
roots/wrong names in8 specific negative checks. Identity rebind/release audited;
all4 scoped processes verified the new fingerprint and retired-identity refusal.
The retained pre-stop authenticated admin session survived. All18 previous docs
remained unchanged; one new DOCX completed with1publication and SHA/search checks.
Final2/2/2 Ready0restarts, protected PC config activated only after full verification.
The first diagnostic's optional-gRPC-port defect was corrected in a different Job;
its original Failed Job/Pod remains retained and its terminal identity/spec/process
facts are freshly checked by a narrowly scoped drain view. Public receipt:
`../reports/2026-11-01-replica-coordinated-ca-rotation.json`. This closes the selected
recipe's coordinated CA gate, not all O1. Storage-full, RWX/external/mixed, customer
PKI/provider and node/host failure qualification remain open. Production scheduleOFF.

O1 bounded S3 ENOSPC component follow-up (2026-11-02): a separate restricted
memory-only Pod (no PVC/hostPath/external endpoints) reached actual errno28 at128Mi.
The real pinned RustFS and unchanged deployed S3BlobStore refused both conditional
single and multipart writes, preserving the earlier object's SHA/range reads.
After inode-bound filler recovery, the same IDs passed writes/conditional replay/
integrity/ranges with zero native pending multipart uploads. RustFS returns opaque
500 InternalError without an ENOSPC message: safe BlobUnavailable fallback remains,
not a blanket500→storage-full mapping. Operation/action/source-bound receipt guards
prevent uncertain observation retries from repeating a native request. See
`../reports/2026-11-02-replica-bounded-s3-enospc.json`. This is only the S3 component
contract; full API/worker, PG/Qdrant and physical/provider/RWX/external/mixed gates
stay open. It does not complete O1 or certify production disk quotas/HA.

**Files:** создать `scripts/kubernetes/replica_backup.py`,
`backend/app/services/work/maintenance.py`,
`backend/alembic/versions/rpl05_maintenance_epoch.py` (rpl04 is the existing runtime identity),
`backend/tests/test_replica_backup.py`, `backend/tests/test_replica_restore.py`,
`docs/KUBERNETES_REPLICAS_RECOVERY.md`;
изменить `scripts/kubernetes/lifecycle.py`, `scripts/kubernetes/backup.sh`,
`scripts/kubernetes/restore.sh`, `backend/app/db/models.py`,
`backend/app/services/work/store.py`, `backend/app/services/work/coordination.py`,
`backend/app/services/generation_store.py`, `backend/app/services/audit_outbox.py`,
`deploy/helm/ai-knowledge-storage/templates/backup.yaml` (создать), `SECURITY.md`.

O2 compact SQL/CLI foundation (2026-11-03): a nonempty compact source after
verified S4 cutover can now install a new identity and held epoch1 under an owned
offline operator. Original logical SQL, NULL/missing generation pointers,
configuration, auth/audit and supported durable queued work remain unchanged.
Literal explicit runtime selection prevents ambient/root-dotenv redirection;
create-only identity publication can resume after SQL commit with the exact same
held proof. Real isolated PostgreSQL tests exercise the official Alembic chain,
advisory exclusion, an actual conflicting writer, atomic rollback and CLI
publication recovery. This does not implement legacy queued Job/document intent
handoff or complete the populated source-off Kubernetes adoption/provider/release
recipe. See [detail plan](2026-11-02-replica-compact-adoption.md) and
[SQL phase procedure](../../KUBERNETES_REPLICAS_RECOVERY.md#nonempty-compact-adoption-held-sql-phase).

O2 reviewed queue handoff follow-up (2026-11-03): an explicit bounded version1
plan now creates supported orphan WorkItems/frozen document inputs in the same
transaction as held identity and audit. Original jobs, approval decisions,
accepted S4 export/checkpoint/scope inputs and all original SQL rows are retained;
same-operation retry validates the pristine new rows rather than ignoring them.
CLI inspection/apply and actual PostgreSQL rollback, conflicting-writer and
publication-resume tests passed. This is still partial Task3: protocol1 diagnostic
recorder history needs its own verified migration before pending local bundles
can be handed off. Protocol2 shared-input tests do not close that gap. Running or
owned legacy operations remain explicit reconciliation gates, and populated
source-off Kubernetes/provider/restore/release acceptance is still required.
See [handoff evidence](../reports/2026-11-03-replica-compact-handoff.json).

O2 legacy diagnostic follow-up (2026-11-03): S4 now imports the complete retained
canonical journal with original expiry and freezes eligible pending bundles at
their accepted cutoff. Optional explicit frontend history is included without
recollection. Held handoff emits frozen Work2; ready/audit/success share an owned
transaction. Native PostgreSQL proves real diagnostic writer exclusion, rollback,
same-plan retry, cold execution, lost-owner fencing and subsequent store transfer.
See [journal/input evidence](../reports/2026-11-03-replica-legacy-diagnostics.json)
and [procedure](../../KUBERNETES_REPLICAS_RECOVERY.md#retained-compact-diagnostic-journal).
This completes that software prerequisite for Task3, not its actual populated
source-off Kubernetes/provider/independent-restore/release rehearsal. No profile
certification or production schedule is enabled.

O2 schema-adoption SQL foundation (2026-11-02): the specific populated
rpl04-to-rpl05 transition now has an owned offline adoption step. It claims only
the exact migration-created fence, checks original identity/storage and explicit
writer-stop receipts, refuses running/uncertain work/chat/model/storage state,
and atomically rebinds/audits while retaining maintenance at epoch2. Source and
receipt hashes bind retries across DB and/or durable outbox audit modes. The
actual PostgreSQL whole migration chain is exercised before populating the old
installation and applying the real rpl05 change, including failure rollback and
explicit release. This does not complete the Kubernetes schema-update
coordinator, compact/NULL-generation corpus adoption or other O2 gates. See
[SQL transition evidence](../reports/2026-11-02-replica-schema-adoption.json) and
[procedure boundary](../../KUBERNETES_REPLICAS_RECOVERY.md#database-adoption-after-the-rpl04--rpl05-schema-change).

O2 offline CLI follow-up (2026-11-02): the existing runtime-upgrade command now
accepts the explicit predecessor/verified writer receipts/intended counts for
the supported already-migrated installation. A separate bounded target-bound
operator file configures SQL without changing scoped signed Settings. Regular
JSON inputs are bounded8KiB, projected symlinks supported, FIFO/partial/foreign
inputs refused. Actual SQL commit followed by failed identity publication is
resumed idempotently with the same epoch/audit. The default same-schema mode and
saved-image standalone restore remain compatible. See
[CLI receipt](../reports/2026-11-02-replica-schema-upgrade-cli.json). No migration,
release or scaling is automatic; full Kubernetes schema-update and other O2
adoption/provider gates remain open.

O2 predecessor protocol foundation (2026-11-02): rpl04 has an existing S4 storage
token but no epoch. A separate format3 source type retains strict epoch0 plus
mandatory source/token/controller/CRI stop evidence without changing old archive
canonical/HMAC bytes. Source SQL/physical guards and owned audited token capture
precede export. The token transfers atomically with the real rpl05 DDL to the
fresh held epoch1 fence; target rebinding then adopts a new identity at epoch2
with auth revocation and restored-count checks. No writable gap or automatic
release is exposed. See [protocol evidence](../reports/2026-11-02-replica-predecessor-protocol.json).
This is not a completed predecessor provider archive/recovery or Kubernetes
schema-update rollout; those live checks and the remaining O2 gates stay open.

O2 actual predecessor source follow-up (2026-11-02): the historical21f5a6d
application was built from exact Git blobs with a documented Dockerfile-only
cache-order overlay;498 copied files and the real rpl04 Alembic head were checked
inside the image. Fresh native CRI config/layers/repoDigests bind the running
Pods to that image. A separate owned namespace and three new PVCs now run2/2/2
with scoped runtime SQL/S3 and an offline-only administrator file projection.
The actual old migration/bootstrap,5 denied SQL operations, shared sessions on
both APIs and2 real DOCX publish/search/download-SHA roundtrips passed without
problem codes or restarts. The immutable runtime preserves the original setup
values, including its inherited image-label hint; actual image authority is
the verified build fingerprint and native digest mapping, not that hint.
The existing restored22-document target returned to2/2/2 with9 canonical table
hashes and all3 PVC bindings unchanged after temporary idle build scaling.
[Live source evidence](../reports/2026-11-02-replica-predecessor-live-source.json).
The subsequent explicit predecessor exporter produced a genuine independent
encrypted PostgreSQL/Qdrant/S3 archive:2 documents,2 concepts,2 chunks,369 work
rows,6 audit events and10 blobs; all12 encrypted objects cold-verified. Before
each archive publication it rechecks the retained SQL operator/S4 audit/token,
original snapshot transaction/driver/backend and fresh Kubernetes/node/CRI stop.
The source stayed held, then its3 storage controllers were normally stopped;
all7 controllers now have0 replicas, no source CRI process remains,3 PVCs retained.
[Actual archive](../reports/2026-11-02-replica-predecessor-backup.json).
The [validation receipt](../reports/2026-11-02-replica-predecessor-export-validation.json)
pins the actual source overlay hashes,109 Windows passes,157 Linux passes with
8 native PostgreSQL cases and a fresh12-object cold check after source shutdown.
An explicit separate rpl04 importer subsequently restored the real PG dump,
Qdrant snapshot and10 blobs into a fresh namespace/disjoint PVCs while source
storage remained physically unavailable. It retained a target SQL advisory
operator, the exact source S4 token/audit/schema/fingerprint, all369 work rows,
9 materialized-table hashes and4 Qdrant points. Fresh source node/CRI facts,
source/target PVC bindings and stopped target controller/storage-Pod snapshots
were rechecked through provider writes. No schema/identity change or job execution
occurred; all applications remained0 at that provider-import checkpoint. See
[provider import](../reports/2026-11-02-replica-predecessor-provider-import.json).
The subsequent selected-recipe schema transition, new identity/auth cleanup and
explicit activation/functionality passed in the separate
[schema recovery report](../reports/2026-11-02-replica-predecessor-schema-recovery.json).
Production scheduleOFF.

**Interfaces:** `MaintenanceState` в PostgreSQL: installation_id, epoch bigint,
enabled, operation_id, owner_id, started_at. `enter_maintenance(session, operation_id:
str) -> int` increments epoch; `assert_writes_allowed(session, expected_epoch: int)
-> None` проверяется на admission/renew/publish/cleanup и request mutations.
Receipt содержит исходные workloads/counts и checkpoint состояния каждого writer.
Backup нельзя выполнять одновременно с миграцией/cutover/restore: один operation lock.

Все источники записи из P0 inventory, включая auth session writes, chat history и
audit dispatcher, должны быть quiesced/drained. Приложение на это время недоступно;
новые запросы не создают побочные записи. Scale=0 сам по себе не fencing.
Если node/процесс нельзя гарантированно остановить или старый внешний запрос
может менять snapshot source, backup abort до `complete`; нужна изоляция процесса,
доказанный drain либо immutable logical export с проверкой manifest. Никакого force
delete старого Pod как доказательства остановки.

`BackupManifest` version 3: installation_id, schema head, image digests, storage
identities, model/vector identity, maintenance epoch, consistent_at, started_at,
completed_at, objects/checksums/counts, component receipts, encryption/key references,
status=incomplete|complete. Secrets не содержатся; complete только после независимого
destination verification. Поддержать чтение старого format 2 для compact в отдельном
пути; смешивать его restore с новым distributed форматом нельзя.

CLI `replica_backup.py backup|verify|restore --manifest PATH --context CTX --namespace NS`;
restore дополнительно `--target-namespace NEW --apply`. До mutation проверяет новый
изолированный target, credentials и ownership. Существующий source не перезаписывается.
Restore сначала закрывает network egress, держит workers/API off, восстанавливает
PG/files и Qdrant snapshot либо reindex, сверяет checksums/counts/identities,
инвалидирует auth sessions/ротирует ключи по текущему runbook. Running claims
инвалидируются новым installation epoch; queued jobs остаются held до явного
release после проверки target model/storage endpoints. Изменить один namespace
недостаточно для изоляции от production buckets/DB/models.

Ruling,2026-11-01: the first physical recipe uses separate explicit lab commands
`scripts/kubernetes/replica_backup.py`, `replica_restore.py` and the offline
`restore_identity.py` operator. Private config files hold credentials and explicit
lab/context/namespace bindings; recorded namespace UIDs and new target volumes are
checked. Production generic CLI/activation is not implied by this lab recipe.

- [ ] `test_partial_backup_never_complete`, `test_unknown_writer_blocks_backup`, `test_restore_requires_new_target`, `test_restored_jobs_held`, `test_24h_age_from_consistent_at`, `test_maintenance_restores_intended_counts`. Assertions: corrupt/missing component→incomplete; no unverified writer; worker execution=0 до release; age=now−consistent_at; исходные counts восстановлены после успешного backup, при неизвестном исходе ничего автоматически не запускается.
- [ ] RED: `python -m pytest tests/test_replica_backup.py tests/test_replica_restore.py -q`.
- [ ] Реализовать operation lock, drain/fence, manifest/copy/verify и restore с отдельными recipes для managed/external. Внешний provider обязан предоставить пригодную согласованную копию или доступ для logical backup; иначе профиль backup unsupported. Backup destination вне рабочего failure domain, шифрование и least privilege; key recovery отдельно. Retention не должен удалить последнюю пригодную независимую копию.
- [ ] Настроить ночной CronJob только после выбора timezone/window/retention/recipient в приватном deployment config. `concurrencyPolicy=Forbid`; deadline/retry учитывают длительность. Метрика возраста последней подтверждённой consistent_at, alert на error/missing/age > 86400 s. При медленной копии/риске пропуска добавлять запасное окно: ежедневный cron не обещает строгие 24 часа автоматически.
- [ ] GREEN tests; runner operations: backup непустого корпуса/очереди, original storage недоступен, restore в новый target, функциональный search/download, measured RTO/RPO и error-night alert. Правильные count/hash без реального восстановления не считаются PASS.
- [ ] Commit `feat: recover replica deployments from independent backups`.

O2 local health/attempt milestone (2026-11-02): the read-only health CLI cold
authenticates every format3 object and explicit installation/destination binding.
Age comes from consistent_at:86401 seconds emits a stale-point alert regardless
of copy finish time. An opt-in protected latest-attempt journal persists running
before provider work, then only the verified backup ID or fixed failure code.
File locking and an exclusive write-intent marker prevent concurrent takeover or
a false-green status after uncertain replacement/fsync. Failed, overdue, foreign,
unreadable and mismatched-latest attempts cannot hide behind an older good copy.
JSON/exit codes are available for local supervision; no messages or CronJobs are
created. Cold-copy health never asserts restore or production RPO qualification.
See the local-health section in `../../KUBERNETES_REPLICAS_RECOVERY.md`.
Notification routing/delivery, independent key recovery, scheduling and the other
O2 provider/adoption/restore gates remain open. Production scheduleOFF.

O2 current-image copy/real-attempt follow-up (2026-11-02): authenticated independent
copy6bfd7725f526474990e1e706eb4201a4 held/released epoch11 and contains20 documents,
100 blobs and102 encrypted objects. All20 prior documents retained their exact
content/generation/source/delete state and SHA/search/download availability after
normal restart; four scoped processes verified the epoch and maintenance audit
pair. The actual protected attempt journal completed with the same backup ID,
and cold health returned healthy with age592.046346 seconds. It still reports
restore_verified=false and rpo_assured=false. No messages/scheduler were enabled.
See `../reports/2026-11-02-replica-before-scoped-faults-backup.json`. This exercises
the local health path on a genuine complete attempt without closing restore/key/
notification/provider gates. Production scheduleOFF.

O2 predecessor provider-import validation,2026-11-02: Windows100 passed;
Linux174 passed/0 failures/0 errors/0 skips, including11 native PostgreSQL cases.
The first Linux attempt retained3 native fixture-setup errors; explicit fixture
import fixed the test harness without changing production import code. All raw
CI artifacts remain on the training VM. Native post-import SQL independently
confirmed old rpl04/source identity/S4 token, no maintenance table/epoch column,
2 old auth sessions and0 active work. Apps remain0; schema transition, identity
rebind, auth cleanup and activation are separate open gates.
See `../reports/2026-11-02-replica-predecessor-import-validation.json`.

O2 actual predecessor schema-changing recovery,2026-11-02: source-off rpl04 copy
was migrated using the actual rpl05 Alembic revision inside the verified current
image45403fc.509 installed source files match511 exact committed build inputs;
Docker/CRI/rootfs/config mapping and live API/worker image IDs agree. Genuine S4
token transferred atomically to held epoch1, then new scoped installation8 held
epoch2;2 old sessions revoked before activation.9 canonical tables,369 work rows,
10 S3 SHA and4 full-vector points verified before explicit release. An actual old
image read-only direct guard probe refused rpl05 while all identity/storage
comparisons matched; it does not claim normal old-image startup.2/2/2 Ready with
zero restarts,2 preserved search/download SHA and2 fresh clean documents passed;
7 current-runtime smoke checks passed. Retained old source remains off with PVCs.
An operator-local variable collision stopped staging after identity CM creation,
before role changes; the same CM UID/data and unchanged role specs were checked
before resuming, without repeating schema/identity SQL or incrementing epoch.
Build capacity was returned to the other owned target's original2/2/2 and9-table
hashes/PVC/runtime identity reverified. Project audit0/frontend756+1skip/Ruff.
See `../reports/2026-11-02-replica-predecessor-schema-recovery.json`.
This closes only the selected source-off replacement-recovery recipe. Compact/
NULL-generation adoption, retained-namespace in-place update, other profiles,
key recovery/delivered backup alerts and final whole-version CI remain unproven.
Production backup scheduleOFF.

O2 training key recovery,2026-11-02: user selected a protected key directory on
the current independent PC and confirmed no production installation. Created
Git-ignored vault with inherited access disabled/current-user+SYSTEM+Admin3 rules
and a create-only32-byte training key copy; the original key is untouched. A
separate-I Python worker audited/refused original-key-directory opens and decoded
all12 real rpl04 archive objects/1,260,646 bytes into sinks using only the vault
copy. Wrong-key control failed, primary reads0, plaintext files0. Source remained
off/3 PVCs retained. [Key evidence](../reports/2026-11-02-replica-backup-key-recovery.json).
This is directory-level recovery, not a different physical escrow device or OS
sandbox; full provider restoration was already tested separately with the same
archive and was not repeated. User also explicitly approved a local PC alert
receiver. No external recipient/schedule selected.

O2 local alert milestone,2026-11-02: the completed
[local-alert plan](2026-11-02-replica-local-backup-alerts.md) delivers through a
loopback-only PC receiver using a separate protected signing key, durable pending
sender journal and authenticated matching ACKs after SQLite FULL commit. Actual
eight-step drill produced exactly4 inbox rows (3 alerts+1 recovery); unchanged
states produced none. Staleness used explicit offline age86401, and failure used
an isolated incomplete encrypted-copy candidate/attempt journal. An older good
archive did not hide the latest failed copy attempt. Original13 archive files,
key and3+3 PVCs preserved/source off. Windows107 passed/1 existing POSIX-only skip;
root audit0/frontend756+1skip/ESLint/Ruff passed. Lost-ACK same-ID replay, invalid
signatures/ACKs and uncertain writes are tested. No Linux alert cohort or actual
missed nightly provider backup is claimed. [Delivery evidence](../reports/2026-11-02-replica-local-backup-alerts.json).
This closes the selected local training delivery/key inputs, not all O2 profiles,
production monitoring/physical key escrow or strict RPO. Production scheduleOFF.

## Task O3: fault matrix, release gates и документация

O3/F2 current-source follow-up,2026-11-05:
[новая real-OIDC приёмка](../reports/2026-11-05-frontend-oidc-native-acceptance.json)
прошла12 native/common-runner этапов через approved Gateway/Keycloak с настоящим
CSRF guard и потерей frontend без model replay. Full Linux fdfae89:6782passes/
221unchanged optionalSkip/0failures/errors/87,70% coverage,7003cases точно учтены,
источники неизменны. Fixture0/3handlesclosed/3PVCretained; обе Task4loans вернули
2/2/2/runtime/PVC/9 таблиц. Это закрывает selected real-SSO/CSRF producer, сохраняя
customer provider/fault matrix, physical resilience и trusted release gates.


O1/O3 bounded S3 application full milestone,2026-11-04:
[actual ENOSPC and automatic retry](../reports/2026-11-04-replica-s3-application-full.json)
uses managed PG/Q plus external ephemeral RustFS128Mi. Pre-fault verification
found BlobUnavailable misclassified as internal_error;65d6ee2 corrects typed
transient classification while retaining integrity/missing/lease/conflict guards.
100 focused tests and full Linux6489pass/221sameoptionalSKIP/0fail/87.70% passed.
One accepted regeneration preserves the old publication while full; API rejects
a2119201-byte upload with503/artifact_unavailable and no receipt/business effect.
Independent own-file cleanup75.0757s; same work succeeds attempt3/fence3 by140.516s,
no queue/lease/slot/clock edits. Known503 upload retries with its same key once.
Four routes/4native processes verify16cold SHA,2docs/5businesswork/2receipt/audit,
scratch0 and both originals in the new ZIP. New namespace held3/all native0,
2PVCs retained;16synthetic blobs4268486bytes independently copied/rehashed on PC.
This is not format3 backup. Initial ordinary-factory maintenance refusal retained;
offline copy uses the exact operator lock/identity/store/epoch and scoped reads.
Both old r4o2t/r5o2u loans returned2/2/2/runtime/PVC/nine-table hashes; prior NAS/S3
sources and logical crossnode nodes stay off, foreign OpenSearch stays running.
No production quota/PG-Q exhaustion/provider/physical-domain certification.
Other external/mixed/adoption/profile gates remain open, production scheduleOFF.

O3 artifact-CI milestone,2026-11-02: fresh full doc-parser557/557 tests with0 skips
and frontend production standalone build19 routes passed in isolated network-none,
read-only non-root containers.97 parser and366 frontend files match the frozen
snapshot and the recorded4fd5556 revision (Git tree diff empty). Locked dependency
image was reused only after package-lock hash verification; every frontend source
input was mounted read-only and .next output was new. Raw logs/build artifacts
remain on the training VM. An initial1GiB available-memory preflight refused
before allocating a container/output; normal idle target8 application stop made
room, then original2/2/2 and9-table/PVC/identity/epoch checks were restored.
[Artifact CI](../reports/2026-11-02-replica-current-artifact-ci.json).
The public `deploy/kubernetes/replicas/support-matrix.json` lists all7 planned
profiles and pins the limited managed/S3 binding to actual topology/providers/
versions/runtime identity/evidence SHA. No profile has certification; automatic
receipt-based certification enforcement and the remaining full O3 gates are open.
See `../../KUBERNETES_REPLICAS_ACCEPTANCE.md`.

O3 policy foundation,2026-11-02: strict distributed S3/RWX binding/evidence models,
fixed required checks, exact kind/binding/artifact metadata, verified live restore,
per-component external ownership and resilient physical-domain/fault conditions
are implemented in `backend/test_scripts/replica_acceptance_report.py`.
Duplicate/unknown checks, extra fields and malformed hashes fail closed without
echoing raw inputs. Profile topology/provider/version/image/config identity/model
changes invalidate the binding; stale policy/class/hash certificates are refused.
Kind cannot qualify physical resilience. TDD33 expected missing-module failures,
then47 Windows passes including14 unchanged runner-safety cases; root audit0/
frontend756+1skip/ESLint/Ruff. No Linux cohort for this new module is claimed.
See `../reports/2026-11-02-replica-certification-policy.json`.
This is a pure policy over trusted already-verified harness metadata, not raw
receipt authentication or deployment authority. Bounded file-backed provenance/
SHA/format verification, runner integration and compact/frontend-split coverage
remain open. The actual inventory issues0 certificates and stays incomplete.

O3 trusted-file implementation follow-up,2026-11-02: bounded stable regular JSON,
actual artifact SHA and independently selected Ed25519 issuer/producer/check
authority are verified before policy. A separately authorized signed terminal
receipt set and unique acceptance ID prevent silent assembly across results or
sessions. Strict outcomes derive status; failed/skipped/pending and unverified
restore block qualification. An added parent-alias regression caught and fixed
embedded trust disguised as an external path. The common runner has an explicit
all-suite offline-only mode; no live/apply/probes/deployment action occurs.
No real issuer is configured and old reports are not signed retroactively.
[Format/runbook](../../REPLICA_EVIDENCE_FORMAT.md),
[implementation tests](../reports/2026-11-02-replica-trusted-evidence.json).
Real terminal producer integration and full native/current-profile evidence remain
gates; signatures attest configured harness observations rather than replay them.
The attempted Linux CI source handoff was rejected before execution by automatic
approval review pending explicit user authorization. No archive was transmitted
and no CI container started. The temporary idle target reduction was fully
recovered to2/2/2 with9 canonical tables/PVC/identity unchanged. This is an open
Linux check, not a successful skip. Production scheduleOFF; all actual certificates null.

O3 Linux follow-up after explicit user approval,2026-11-02: the exact authorized
1c52d26 archive plus5 Windows-validated overlays (code-equivalent to788a99b)
passed295 selected notification/health/verifier/policy/runner cases,0 failures,
errors or skips.2395 frozen files remained unchanged; actual FIFO and symlink
cases passed. Initial read-only source caused295 setup errors before scenarios;
the failed run is retained and a new writable checkout of the same on-VM archive
passed without product changes or additional source transfer. Root filesystem
read-only/network none/non-root/1GiB/1CPU retained. Same capacity operation restored
target2/2/2 with9 canonical tables/PVC/identity unchanged; source stayed off.
[Linux evidence](../reports/2026-11-02-replica-evidence-alerts-linux.json).
The Windows alert/health skip's earlier FIFO label was incorrect: it was symlink
creation permission; the verifier's Windows skip was FIFO. Both are now tested.
This resolves the selected Linux source-transfer/check gate; full final backend
suite, real evidence producers, compact/frontend-split and remaining profiles stay open.

O2 selected-runtime backup follow-up (2026-11-01): backup now resolves the common
immutable runtime from actual API/worker references and verifies live settings/
fingerprint before entry and in the frozen export. A real epoch7 copy of the
current scoped-credential target passed:18docs/18concepts/18chunks/3748work rows/
90blobs,92encrypted objects and2,586,300 plaintext bytes cold-verified on the
independent PC. All18 source documents retained content/generation/source/delete
state and active search/downloads after normal restart. Four scoped API/worker
processes proved the released epoch and maintenance audit pair;2/2/2 Ready0restarts,
common identity unchanged. Initial preflight refused the fixture's explicit stub
URL before entering maintenance; an exact-literal rule plus RED/GREEN regression
allowed this known synthetic binding while still refusing other/indirect overrides.
Report:`../reports/2026-11-01-replica-current-runtime-backup.json`. This new archive
is not restore-rehearsed (`restore_verified=false`); previous source-unavailable
restore evidence remains separate. Production scheduleOFF, O2 globally open.

O2 fence foundation (2026-11-01): process/claim epochs and PostgreSQL shared
transaction guards serialize with exclusive offline maintenance entry. Same-session
operator advisory ownership spans separately committed phases, with bounded lock
waits and no lost-connection ACK/reconnect. Intended counts and writer attestations
persist; uncertain provider slots prohibit exit. The S4 storage token excludes
parallel operations; entry/exit audit commits with state. Installed schema upgrades
preserve queued work and start held; stale runtime revalidation cannot adopt a new
epoch. Existing app images were not upgraded. Physical stop/drain collection,
provider reconciliation, adoption/update, format3 independent backup/restore,
nightly scheduling and operation acceptance remain OPEN. See the recovery runbook.

O2 format3 archive follow-up: typed complete inventory and immutable plan, bounded
authenticated encryption, independently keyed manifest, cold all-object verification,
same-source resume and recovery-point age are implemented in
`backend/app/deployment/replica_backup.py`. Source/destination guards are mandatory;
actual provider exports, physical drain, restore and held release remain open.
User chose the physically separate PC for lab backup; production scheduling is off.
This narrows the earlier archive-format item without completing O2/acceptance.

O2 managed-S3 rehearsal,2026-11-01: **actual independent recovery PASS** for the
owned synthetic single-node recipe. A retained PG snapshot, Qdrant snapshot and
15 SQL-ready artifacts produced 17 encrypted objects; cold verification passed.
Source workloads/storage were stopped and restore used a new namespace, three
new PVCs, credentials, CA and installation. SQL counts (3 documents/3 concepts/
3 chunks/298 work rows), 6 Qdrant points and all blob hashes matched. Before
release, the work ledger remained unchanged, executed jobs=0, a real write guard
denied access, and auth cleanup invalidated one source session. Linux identity
adoption retained maintenance until explicit release. All three originals passed
search/download; a fresh document completed after release (26.688s); all six app
Pods ready with zero restarts. Observed RPO age236.334s/RTO973.464s includes operator
implementation during rehearsal; no production SLA. A failed transport attempt
stayed incomplete/held and was not resumed with a lost PG snapshot. Public report:
`tests/tmp/replica-recovery-lab/recovery-report.json`. External/mixed/RWX recovery,
unknown provider reconciliation, installed upgrade, nightly error alerts/key
recovery remain open; production schedule stays OFF by explicit user choice.

O2 reconciliation/upgrade foundation (2026-11-01): a held `recovery` operation,
typed original-reservation/physical-stop proof with mandatory live guards, exact
owner/fence matching, atomic acknowledgement audit and DB/stdout-only idempotency
are implemented for the owned synthetic stub recipe. It never infers completion
from expiry. Same-schema runtime fingerprint CAS under the exact held operator
preserves installation/storage/schema/receipt, refuses owned provider slots and
commits with an upgrade audit; SQL-commit/file-publication retry is supported.
The explicit offline CLI produces an identity without activating roles. Physical
proof producer, actual new-image rollout and unknown-provider-loss recovery still
need live evidence. Ruling: R5 must exercise worker loss during a real active
provider request and retained capacity; an already-drained worker loss is not a
substitute. These foundations are prerequisites for that full scenario. Existing
live images have not been updated by the library changes.

O2 same-schema live rollout milestone (2026-11-01): the owned managed-S3 target
actually upgraded b570457→40b7972 under held epoch4, preserving installation5,
schema5, storage/credentials and corpus/work hashes. New-image bootstrap/release
Jobs computed/verified Linux identity; immutable config/identity and role image
bindings changed at replicas0. First Job failed closed on an incorrect policy
role; after the bootstrap-label fix, the identical owned operation resumed,
rechecked data and released. All six app Pods ready0restarts; all8 existing docs
preserved,7 active search/download hashes matched,1trashed stayed hidden, a fresh
doc done/problemNone/search/download PASS25.5s. Report:
`../reports/2026-11-01-replica-same-schema-upgrade.json`. This closes the actual
same-schema image-update proof for this recipe; schema upgrades/adoption, other
providers, physical unknown-provider reconciliation/nightly/key gates remain open.

O2 owned-provider physical recovery milestone (2026-11-01): actual active worker
loss retained the original global model reservation through provider finish and
lease expiry. Recovery epoch5 normally stopped all application/provider Pods;
unchanged Ready node UIDs, stopped controllers, empty service endpoints and CRI
absence of retired/unknown running processes were fetched live for each SQL guard.
Exact-token completion plus proof-digest audit committed atomically and stayed
held. An offline same-image Job checked held data hashes before release; the
interrupted document recovered automatically with a newer fence and all9 previous
documents preserved. The first observer rejected Kubernetes `endpoints:null` with
a TypeError, leaving the same epoch held; RED/GREEN fixed empty-list handling and
the identical operation resumed. Report:
`../reports/2026-11-01-replica-active-worker-loss.json`. This closes physical
reconciliation evidence only for the owned instrumented stub recipe, not unknown
real/external providers, node loss, schema adoption or the remaining O2 profiles.

**Files:** создать `docs/KUBERNETES_REPLICAS_ACCEPTANCE.md`,
`deploy/kubernetes/replicas/support-matrix.json`,
`backend/tests/test_replica_acceptance_report.py`;
изменить `backend/test_scripts/run_replica_acceptance.py`, `.github/workflows/kubernetes.yml`,
`docs/KUBERNETES_DEPLOYMENT.md`, `docs/PRODUCTION_DEPLOYMENT.md`,
`docs/REPLICA_STORAGE_OPERATIONS.md`, `SECURITY.md`.

**Interfaces:** support matrix: topology, file backend, per-component provider,
exact versions/digests, failure_domain, checks/report revision, limitations,
resource measurements. Статусы planned/tested/basic-certified/resilient-certified;
certification включает hash topology+providers+versions. Смена этих inputs отменяет
сертификацию до повторной приёмки. Просто поле `class=resilient` её не создаёт.

Минимальные реальные прогоны: compact/local legacy; frontend-split; distributed
managed+RWX; distributed managed+S3; distributed all-external; distributed mixed;
отдельный resilient профиль с потерей узла и independent restore. Каждый adapter
и каждый managed/external компонент проверен хотя бы в одном end-to-end прогоне.
Vendor-specific lab не запускается на публичном CI без доступа: CI публикует
pending gate, не зелёный пропуск. Platform acceptance привязана к окружению.

- [ ] `test_required_check_cannot_be_skipped`, `test_failed_restore_blocks_certification`, `test_profile_change_invalidates_report`: assert required skips/failed restore не дают certified; изменение version/digest/failure domain меняет fingerprint.
- [ ] RED: `python -m pytest tests/test_replica_acceptance_report.py -q`.
- [ ] Реализовать scenarios: kill Pod; drain/потеря worker node; DB/network partition и возвращение старого worker; storage full; отключение Qdrant/LLM/IdP; отмена/удаление во время публикации; backup failure; maintenance update с queued jobs; failed migration и rollback/restore. Не объявлять resilient то, что прошло только pod-kill на одном хосте.
- [ ] GREEN report suite и runner `--suite all`; общий `node scripts/check-project.mjs`, полный backend `python -m pytest tests/ -q`, doc-parser `python -m pytest tests/ -q` из `doc-parser/`, frontend production build прямым Node. Проверки с зависимостями выполняются в соответствующем lab; итог разделяет unit, integration, home и customer acceptance.
- [ ] Заполнить runbooks для setup/update/scale/drain/storage migration/backup/restore/incident, ответственность и расходы ресурсов. Зафиксировать известные ограничения active SSE, внешних моделей, basic storage и RPO breach. Commit `test: certify replica deployment and recovery profiles`.

O2 scoped same-schema image update milestone (2026-11-02): the selected owned
managed-S3 installation passed `prepare/fence/compute/stage/release/start/verify`
after credential and CA activation. An independent format3 copy contained
19 documents/95 blobs/97 encrypted objects; cold verification passed, but this
archive has not been restored. Epoch10 retained storage/PVC/Secret/trust bindings,
verified held SQL corpus/blob SHA/full vectors, and moved API/worker together to
the candidate image/config/identity. Two PC PostgreSQL-forward closures occurred
after successful durable phases: the same operation/Job was observed, the exact
forward rebound, and all held data reverified before staging/release. No new fence,
repeated compute Job or automatic application start acknowledged uncertainty.
All19 prior documents and the pre-stop authenticated session survived; four
processes proved scoped principals, transition audits and retired-identity refusal.
One new document completed with one publication and matching search/download SHA;
final2/2/2 Ready with zero restarts. Runtime/app secrets and three storage claims
remained unchanged. Earlier privilege-denial evidence is explicitly identified as
reused. See `../reports/2026-11-02-replica-scoped-image-upgrade.json` and the dated
backup report. This closes only the selected same-schema image-update recipe;
schema-changing adoption/rollback, key recovery, backup alerts, other profiles and
final combined CI/acceptance remain open. Production scheduleOFF.

## Ключевые assertions для тестов задач

O1: `assert external_owned_workloads == []`; `assert secret_value not in rendered_yaml`;
`assert claimed_resilience == certified_resilience`.
O2: `assert partial_manifest.status == "incomplete"`; `assert restored_executed_jobs == 0`
до release; `assert recovery_point_age_seconds == (now - consistent_at).total_seconds()`;
`assert alert_fired` при возрасте `86401` секунд.
O3: `assert report.skipped_required == 0` для PASS; `assert certification is None`
при failed restore; `assert previous_fingerprint != changed_provider_fingerprint`.

O1 RWX follow-up (2026-11-03): separately authorized private NFS/CSI bootstrap and
current-image application2/2/2 passed scoped bootstrap, cross-instance sessions,
upload/publication/search/download/export, server-local bounded ENOSPC507/retry,
and normal server restart. Four native processes verified all11 objects and empty
original scratch. An initial large buffered NFS write stalled in same-kernel
reclaim/COMMIT; its physical recovery and cold hash preservation are recorded,
and the large-write gate remains unqualified. All new roles/storage are stopped
under maintenance epoch1 with three retained PVCs; earlier lab2/2/2 restored. See
[RWX application receipt](../reports/2026-11-03-replica-rwx-application.json). Independent
RWX restore, prolonged outage, cross-node and real external/mixed services remain
open. This narrows O1; it does not complete it or grant certification.

O2 RWX backup milestone (2026-11-03): the existing generic format3 protocol copied
native PG17, native Qdrant and11 accepted NFS blobs into13 encrypted objects on
the user-declared independent PC. Cold decryption/authentication passed, source
physical/SQL/point/blob guards remained stable, all native owners closed and
source providers stopped under held1. Prior lab2/2/2/runtime/PVCs/nine tables were
restored. Keep the authenticated non-secret adjacent source-binding metadata with
the archive; keys never left the PC. See [backup receipt](../reports/2026-11-03-replica-rwx-independent-backup.json).
At this backup-only checkpoint RWX restore/release was open and rebind was
S3-only. Subsequent strict selected RWX support and recovery are recorded below;
equal local root paths alone never authorize relocation. No descriptor bypass.

O2 cross-provider milestone (2026-11-03): [RWX-source→real S3 recovery](../reports/2026-11-03-replica-rwx-source-s3-restore.json)
passed from the independent PC archive while source NFS/PG/Qdrant/app were
physically off. All81 tables/28 sequences/four full points/11 blobs matched
before unchanged official identity adoption; normal held2 refusals and explicit
release passed.2/2/2 verified old-cookie401, old documents/search/download/export,
new shared session and new upload/export. Four native processes verified17 hashes,
empty scratch and no legacy job re-execution. Target is stopped/held3 with three
PVCs retained, source NFS restored, prior2/2/2/PVC/runtime/nine tables unchanged.
Original failed parents/harness assertions are preserved. This closes the selected
RWX-source→S3-target recipe at ccfcf9e. Subsequent selected RWX target support
is recorded below. Failed large writes, prolonged outage/cross-node/external/mixed
and final certification gates remain open; scheduleOFF/certificationnull.

O2 selected RWX-target milestone (2026-11-03): [current-image recovery](../reports/2026-11-03-replica-rwx-target-restore.json)
passed on cf74f8a after full Linux6466 passes/221 unchanged optional skips.
Authenticated source/actual new NFS root proof guarded81 tables/28 sequences,
four full points and11 blobs, held adoption and explicit release. An actual
exit23 after a resumed SQL commit/before identity output recovered identical
identity with109 unchanged catalog entries; first adoption had already succeeded.
2/2/2 old/new HTTP and four native17-object cold checks passed. Target is now
physically off/held3/three PVCs retained; prior2/2/2/runtime/PVCs/nine tables
restored. Source claim stayed unmounted, common NFS server stayed up. Selected
RWX restore is complete; large-write/outage/cross-node/customer and overall
O1–O3 profile gates remain open. No certification or production schedule.

O1 selected Synology mixed milestone (2026-11-04): [current96931 rehearsal](../reports/2026-11-04-replica-synology-mixed-runtime.json)
passed6472 Linux cases/221 identical optional skips and actual2/2/2 on managedPG/Q
plus external DS211 NFSv4.0/CSI4.13.6. Four processes proved working child-directory
ACCESS/readiness despite root W_OK=false; scoped SQL81tables/10denials/TLS-SCRAM,
shared sessions/upload/search/download/export and real user EDQUOT507/same-key
recovery passed. All11 new blobs cold-verified; trial stopped/held1/threePVCs
retained/owned forwards and NFS mounts gone. All borrowed old services/2/2/2,
109 PG catalog entries, full Q points, nine canonical tables and eight live-bound
S3 hashes were returned/verified. Initialcf74 failure remains retained separately.
NAS independent backup/recovery, prolonged outage/cross-node/real-model/IdP and
overall qualification remain open; no certification or production schedule.

O2 NAS backup milestone (2026-11-04): [read-only-client independent copy](../reports/2026-11-04-replica-synology-independent-backup.json)
passed cold authentication/decryption for13 objects: native PG81tables/28sequences,
Q4fullpoints and11 NAS blobs. UID1031/G100 reader had actual NFSv4.0 RO/noSecrets,
15 guard cases RED→GREEN, and the key remained on the independent PC. One pre-plan
header failure left no archive/sidecar; closed owners were proved and the existing
Qsnapshot reused without another POST. Source is physically off/held1/unmounted,
completed reader retained, old2/2/2/runtime/PVC/nine tables restored. NAS server
stayed up. At this backup checkpoint NAS-source recovery remained open; the
subsequent S3 recovery is recorded below. No4.0 target-guard bypass.

O2 NAS-source recovery milestone (2026-11-04): [independent S3 target](../reports/2026-11-04-replica-synology-source-s3-restore.json)
recovered81 tables/28 sequences, four full vector points and11 blobs from the PC
archive into three disjoint512Mi claims. Source app/PG/Q stayed physically off and
the source NFS claim unmounted. NAS remained up with a host TCP positive control;
all four ordinary target API/worker processes proved2049 denied and no NFS volumes.
Scoped SQL/S3 rights, official held identity adoption, normal held startup refusals
and explicit release passed.2/2/2 old-cookie401/old search-download-export/new
shared session/new upload-export passed;17 cold hashes/scratch0 and three old
business jobs never replayed. A rejected near-duplicate fixture was retained and
replaced with different content/new key, without bypassing deduplication. Target
is off/held3/three PVCs retained; prior2/2/2/runtime/PVC/nine tables restored. This
closes this selected NAS-source→S3 recovery recipe, not NAS-target relocation,
prolonged outage/cross-node/physical domains or complete customer qualification.
Production scheduleOFF/certificationnull.

O1/O3 selected NAS component outage milestone (2026-11-04): [bounded150s recovery](../reports/2026-11-04-replica-synology-bounded-outage.json)
passed actual DROP/in-flight I/O observations through136.6s, natural element
expiry before152.7s and same-process read182.9s/write183.1s with both1MiB SHA
matches. The NAS stayed reachable from the VM; only the approved kind-node TCP
endpoint was blocked. Own table/objects/empty directories were removed; one PVC
and all three Jobs retained, CRI/mount0, original OUTPUT unchanged. A delayed
first trial and import-only failure are preserved separately. A new worker2→1
memory loan was fully returned with old2/2/2/runtime/PVC/nine tables unchanged.
This closes a component outage case; full application evidence follows below.
Longer interruptions and physical failure qualification remain open. ScheduleOFF.

O1/O3 selected NAS application outage milestone (2026-11-04):
[active-worker150s recovery](../reports/2026-11-04-replica-synology-application-outage.json)
armed only after native owned running attempt1/model-active proof. Actual DROP,
pending publication through136.5s, liveness200/readiness timeout and zero Pod
restarts were observed. Natural expiry led to completion observed by194.2s;
the same work/attempt1 and candidate generation published cleanly. Old concepts,
session, both original SHA/search, previous ZIP, same-key retry and fresh export
passed on four HTTP routes; four native processes verified12 cold hashes,
scratch0/two upload audits. No application source/dependency changes; the delayed
model fixture came from the unchanged immutable CI source. Own nft/forwards are
closed, new app/storage off under held1 with three PVCs retained, original NAS/S3
sources frozen, all capacity loans returned and old2/2/2/runtime/PVC/nine tables
plus full provider data rechecked. The selected150s application case is complete;
cross-node/physical domains, NAS-target relocation, longer interruptions and
remaining customer/profile gates stay open. ScheduleOFF/certificationnull.

O1/O2 selected all-external S3 milestone (2026-11-04):
[closed runtime and recovery](../reports/2026-11-04-replica-all-external-runtime-recovery.json)
uses actual2/2/2 and three TLS/scoped providers in a separate owned namespace; app
has no storage workload/PVC/root credentials. Frontend/credential/TLS controls
and independent encrypted format3 backup passed. Fresh-target source denials
were proved against live source positive controls before all source providers
stopped. Actual restore matched81 tables/28 sequences/two full points/six blobs;
official held adoption invalidated one session and kept172 work rows unchanged
before explicit release. Old/new HTTP and four native12-object SHA checks passed.
Source/target off held1/held3, six PVCs retained, both capacity loans returned with
original2/2/2/runtime/PVC/nine-table hashes. Current65d6ee2 product was unchanged;
its prior6710-case Linux CI applies to those exact image bytes. Customer-specific
external providers, other selected variants, physical domains, real model/IdP and
complete O1/O2/O3 profile qualification remain open; scheduleOFF/certificationnull.

O3 common frontend producer milestone (2026-11-05):
[fresh 12-stage case](../reports/2026-11-05-frontend-native-acceptance.json)
ran the actual common runner on e42007a with bound parent/native intents. Two
unbuffered NDJSON streams and one model-active UID-preconditioned frontend loss
passed; interruption did not replay generation. Replacement/session/original/
search/ZIP/logout/cold SHA/quiescence passed, five owned handles closed. Readiness
polling retained the same intents, including an unsuccessful loss observation.
All fixture controllers/native containers are off, three claims retained; the CI
loan and both live loans returned with original 2/2/2/runtime/PVC/nine tables.
Full immutable-source Linux CI:6859 cases=6638 pass+221 unchanged optional skips,
0 failures/errors, coverage87.70%; required Linux pidfd passed. This completes
the selected basic simulation producer, not real CSRF/Gateway/SSO, trusted signed
release production or other provider/profile gates. ScheduleOFF/certificationnull.
