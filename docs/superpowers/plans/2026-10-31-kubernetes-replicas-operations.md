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

## Task O3: fault matrix, release gates и документация

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

## Ключевые assertions для тестов задач

O1: `assert external_owned_workloads == []`; `assert secret_value not in rendered_yaml`;
`assert claimed_resilience == certified_resilience`.
O2: `assert partial_manifest.status == "incomplete"`; `assert restored_executed_jobs == 0`
до release; `assert recovery_point_age_seconds == (now - consistent_at).total_seconds()`;
`assert alert_fired` при возрасте `86401` секунд.
O3: `assert report.skipped_required == 0` для PASS; `assert certification is None`
при failed restore; `assert previous_fingerprint != changed_provider_fingerprint`.
