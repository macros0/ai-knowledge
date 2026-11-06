# Kubernetes из исходников

## Независимые frontend-реплики (локально проверено, приёмка платформы открыта)

Overlay `deploy/kubernetes/profiles/frontend-split.values.example.yaml` включает
`deployment.mode=frontend-split` и две frontend-реплики. Deployment `application`
сохраняет один backend process, Service `backend`; новый Deployment `frontend`
выбирается Service `frontend`. Gateway по-прежнему направлен только во frontend.
Это не режим нескольких backend и не обещание HA storage.

В существующем runtime Secret до запуска нужен одинаковый для backend/frontend
`DIAGNOSTICS_INGEST_TOKEN` длиной 32–512 символов; Secret не помещать в values/Git.
Frontend получает DIAGNOSTICS_INGEST_URL и Pod UID, пишет только в свой emptyDir;
общий data/diagnostics PVC монтируется backend. Секрет проверяется lifecycle preflight.

Пользоваться lifecycle CLI с этим overlay, а не прямым Helm поверх работающих
writers. Backup/maintenance останавливает оба Deployment, сохраняет исходные
counts в lock и возвращает их после успешного backup. Drift backend count >1
отклоняется. При неизвестном исходе операции lock сохраняется для разбора.
Ручной restore split-поставки требует восстановления обоих workloads из тех же
images/values; старые compact restore-примеры не доказывают такую приёмку.

Локальные render/unit проверки не заменяют SSO/CSRF/SSE/upload smoke и остановку
одного frontend Pod через реальный Gateway. Этот gate пока открыт.

## Компактная установка

Distributed implementation status (2026-11-01): chart templates now separate
frontend/API/worker, with the backend Service selecting API only. The basic
example is `deploy/kubernetes/profiles/distributed-basic.values.example.yaml`;
it intentionally starts with application workloads disabled. Each backend Pod
uses one process, private bounded scratch, the same runtime ConfigMap/Secret and
an operator-installed common identity. The ConfigMap overrides chart-controlled
Secret keys; stub/unavailable model endpoints are explicitly pinned on containers.
Worker probes use private port8001; there is no worker Service/public route.
NetworkPolicy isolates frontend, API, worker and storage roles. CNI enforcement
must be verified separately. Manual counts and topology spread do not certify HA.

Startup requires a matching keyed configuration/build/storage receipt identity,
namespace and installed database row, plus schema `rpl05_maintenance_epoch` in
the current source. The recorded basic runtime lab used the previous rpl04 image;
it has not been upgraded by the maintenance foundation.
API/worker never bootstrap that record or call create_all as an upgrade. SQL
boundaries fence a process after installed identity changes. API readiness also
checks this binding and artifact storage. Bootstrap/storage ownership, maintenance
and real multi-Pod failure acceptance remain O1/O2/R5 requirements; these templates
are not a completed production rollout.
The [maintenance/recovery status](KUBERNETES_REPLICAS_RECOVERY.md) explains the new
epoch fence, held schema upgrade and remaining operational gates.

Operations metrics: administrator-only `/api/admin/replicas/metrics`; worker
`/metrics` on its private health port (default ingress denied, use an explicitly
authorized monitoring policy or port-forward). Shared SQL gauges report queue
depth/oldest age, running/expired claims, retry attempts and reserved/quarantined
model capacity. Aggregate repeated shared gauges using max, never sum. Process
claim/retry/lease-loss/completion counters reset on restart and may be summed by
role. Expired uncertain model requests remain reserved until provider reconciliation.
Resources in the chart are provisional until the O3 corpus/load measurement.

### Replica runtime smoke

The acceptance manifest's existing run/context/namespace/database/collection/prefix
fields are extended with `application_release`, `installation_id` (32 lowercase
hex characters), `frontend_url` (an owned HTTP loopback tunnel), and
`simulation_username`. This probe currently requires the basic managed S3 lab,
simulation authentication, model stub and fake embeddings. It does not qualify
customer SSO, a real model, RWX, external/mixed providers or resilient storage.
The namespace must carry `okf-purpose=replica-test`, the matching `okf-run-id`, and
`app.kubernetes.io/managed-by=okf-replica-acceptance`; deployment ownership is also
checked before the synthetic HTTP writes. No application credentials/cookies or
raw provider errors are included in its report.

```sh
python backend/test_scripts/run_replica_acceptance.py --suite runtime \
  --manifest /private/replica-acceptance.json --live \
  --ssh-host ubuntu@training-host --known-hosts /private/known_hosts
```

Omit the SSH arguments when `kubectl` runs locally against the explicit context.
The frontend tunnel is prepared separately and remains bound to 127.0.0.1.
Adding `--idle-faults` scales API and worker 2→1→2 and normally deletes one idle Pod
of each role, then repeats upload/search/download. It refuses active document or
owned model reservations, including quarantined requests. It never uses force
deletion, clears provider slots, or claims that idle replacement proves recovery
of an in-flight operation. Unknown outcomes remain failures, with no automatic
force recovery. The run keeps its synthetic documents for inspection.

`--active-faults` requires the recorded `namespace_uid` in the manifest and an
owned `okf-model-stub:r5-active-<source-hash>` fixture with its aggregate read-only
`/test/status` counters. It creates fresh test documents, normally switches the
idle fixture to a30s synthetic delay, then cancels/deletes an update while a real
model request is active. SQL work fencing, unchanged active generation/content
hash, search visibility and download access are checked after the provider
returns. A second case starts two document workers against global LLM limit1;
both API processes must reject extra admission without contacting the provider,
and the two documents must complete with correct search/download afterwards.
No provider slot is cleared by the test. Normal fixture configuration is restored
only after verified quiescence. Remaining worker-loss, PostgreSQL partition and
stale-worker return checks stay required. The delay is fault injection, not a
measurement of production model throughput. See the
[owned live result](superpowers/reports/2026-11-01-replica-runtime-active-publication.json).

An expected exit1 with `failed=0` still means required fault gates are pending.
Inspect `checks` and `skipped_required`; a successful smoke does not make the
runtime or whole deployment certified. Reports distinguish the Git revision,
the executing runner SHA-256 and actual per-role container image digests.
Cgroup memory samples include probe overhead and short synthetic throughput uses
stub/fake model work; neither is a production sizing or linear speedup claim.
The initial lab API limit512Mi produced an OOM; the owned lab was changed to1Gi.
The chart's larger default limits were retained pending full corpus measurements.
The owned lab [report](superpowers/reports/2026-11-01-replica-runtime-basic-smoke.json)
records all smoke/idle/CNI checks passed,32 focused unit tests passed and the root
project gate passed. Five required active fault checks remain pending. The two
document cohorts took26.63s and24.09s with stub/fake models. Sampled container peaks
reached about669MiB for API and654MiB for worker, including probe overhead; those
numbers are observations of this small corpus and constrained lab only.

### Owned replica load follow-up (2026-11-01)

The later standalone owned-lab reports cover active cancel/delete and global
capacity, active worker loss, PostgreSQL partition and stale-process return.
Their scope/limitations are recorded in the [recovery runbook](KUBERNETES_REPLICAS_RECOVERY.md).
The combined runner remains conservative until O3 integrates those recipes.

`scripts/kubernetes/replica_load.py` provides `prepare`, `single`, `dual` and
`verify` phases with explicit protected config/journal, recorded namespace UID,
installed fingerprint and `--apply`. Only the owned kind test namespace is
accepted. API/frontend counts stay2 each; only worker count changes1→2 after
confirmed quiescence. Each case uploads three DOCX of the same structure and
keeps their receipts. Two concurrent per-frontend Node probes each issue20
authenticated list requests at concurrency4; cookies stay in process memory.
The final phase verifies all existing documents and source SHA/search/delete
visibility. Incomplete processing or observation does not become valid throughput;
`observe-single`/`observe-dual` inspect the same receipts without re-uploading and
refuse to present interrupted timing as successful throughput. Unknown model
capacity is never cleared to finish a measurement.

| Worker Pods | DOCX completed | Batch seconds | Documents/min | Frontend p95, each Pod |
| --- | --- | --- | --- | --- |
| 1 | 3 | 69.594 | 2.586 | 372 / 776 ms |
| 2 | 3 | 50.500 | 3.564 | 416 / 640 ms |

All80 authenticated list requests succeeded. Two distinct document owners were
observed in the two-worker case. The observed throughput ratio was1.378, with
only one sequential trial per case; this is not a statistical or linear scaling
claim. Upload p95 was2.157s and2.406s. Stub model/fake embedding work and observer
cost are included. The real model may dominate behind the unchanged global slot1.

| Role | Tested CPU limit | Tested memory limit | Highest lifetime cgroup peak |
| --- | --- | --- | --- |
| frontend | 250m | 256Mi | 111.3MiB |
| API | 500m | 1Gi | 677.6MiB |
| worker | 500m | 768Mi | 682.1MiB |

The single worker averaged494m CPU; the two workers summed940m, with cgroup
throttling observed. Kernel memory peaks cover each container's lifetime and
include Python/Node observer processes; they are not simultaneous aggregate RSS.
No OOM/restart occurred. These tested limits fit this synthetic lab only: worker
headroom was small, and PDF/XLSX, real models, larger corpora and storage resource
requirements need separate measurements. The chart's larger defaults remain
API2Gi/worker3Gi/frontend512Mi; do not lower them to the lab caps based on this run.
Requests/limits are preserved in the [public report](superpowers/reports/2026-11-01-replica-small-load.json).
All6 new documents published once, all11 previous documents stayed unchanged,
and final2 frontend/2 API/2 worker were Ready with zero restarts, the same runtime
identity/epoch and idle model capacity. Production scheduling remains off.

Поддерживаемый путь этого merge: Linux/amd64, compact/bundled, один Pod приложения,
один backend worker, Recreate, отдельные PG/Qdrant и внешние для release PVC.
Авторизация сохраняется из текущего main: confidential client, client_secret_basic,
без PKCE, auto UserInfo. Helper отвергает остальные профили до создания ресурсов. Модели stub допустимы для home/CI;
PROD и live model acceptance выполняются отдельно.

Из корня checkout на подготовленном домашнем kind с Gateway API/kgateway:

```bash
set -euo pipefail
revision=$(git rev-parse HEAD)
docker build -f backend/Dockerfile --build-arg OKF_BUILD_REVISION="$revision" -t okf-backend:kube-smoke .
docker build -f frontend/Dockerfile --build-arg OKF_BUILD_REVISION="$revision" -t okf-frontend:kube-smoke frontend
docker build -f backend/test_scripts/model_stub/Dockerfile -t okf-model-stub:kube-smoke backend/test_scripts/model_stub
kind load docker-image --name home okf-backend:kube-smoke okf-frontend:kube-smoke okf-model-stub:kube-smoke
python3 scripts/kubernetes/home_access.py --context kind-home --namespace okf-access --application-namespace okf-home \
  --private-directory "$HOME/okf-private/access" \
  --keycloak-image quay.io/keycloak/keycloak@sha256:b0f60d489d51c5d113390bdf5461d4c06e6051be026c05549f2e1e10ec352bcc \
  --runtime-trust-configmap compact-home-roots --backend-image-tag kube-smoke
python3 scripts/kubernetes/lifecycle.py deploy --context kind-home --namespace okf-home --expected-tier home \
  --values "$HOME/okf-private/access/base.values.yaml" --values "$HOME/okf-private/access/sso.values.yaml" \
  --values deploy/kubernetes/profiles/compact.values.example.yaml --backup-root "$HOME/okf-backups"
python3 scripts/kubernetes/home_probe.py --context kind-home --namespace okf-home --marker CompactMain
```

Не повторно использовать изменяемые kube-smoke tags на работающем стенде; для
постоянного развёртывания записать digests собранных images и новый values overlay.
Проверенный отдельный frozen kit и его результаты описаны в
[KUBERNETES_COMPACT_HOME.md](KUBERNETES_COMPACT_HOME.md); он не заменяет сборку этой ветки.

`deploy`/`backup` сериализуют обслуживание lock, останавливают writers и проверяют
image/model/storage/trust identity. Backup format 2: PG, Qdrant, files, totals,
release identity и публичный trust. PVC сохраняются при uninstall. Автоматический
restore запрещён CLI и library; recovery после неизвестного состояния требует
ручного разбора, без force delete. Штатный backup запускает source снова;
для остановленной source-копии см. manual runbook.

[Ручной restore](KUBERNETES_COMPACT_RESTORE.md) использует новый namespace/claims,
те же images/model/storage/trust, PG single transaction и strict content check.
Для образов, собранных из этой ветки, до первого старта toolbox выполняет:
`python scripts/invalidate_all_auth_sessions.py --confirm-stopped-and-key-rotated`.
Он удаляет все auth_sessions и persisted oidc_login_flows, если такая таблица
существует. В текущем приложении OIDC flow state хранится в signed session;
намеренная смена APP_SECRET_KEY до старта инвалидирует его. Обычное обновление
ключ сохраняет. Архивный manual cell с безусловным DELETE обеих таблиц относится
к старому frozen image; для новых images используйте эту команду и проверьте ноль
auth_sessions до start. Схема отсутствующей auth_sessions отклоняется.

Проверки: `node scripts/check-project.mjs`, backend pytest/coverage, doc-parser
pytest, frontend build и отдельный Kubernetes CI. Полный CI и локальные проверки
указываются раздельно в integration report.
