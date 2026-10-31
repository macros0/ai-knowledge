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
