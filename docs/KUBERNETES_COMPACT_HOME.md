# Компактный домашний Kubernetes: проверенный комплект

Эта страница описывает отдельный frozen archive. Команды выполняются с его
`kit/` и встроенным runbook восстановления. Для текущего checkout используйте
[установку из исходников](KUBERNETES_DEPLOYMENT.md) и
[source restore runbook](KUBERNETES_COMPACT_RESTORE.md); их нельзя смешивать со старым backend image.

Редакция 10.10.2026. Один Linux/amd64 kind, compact/bundled, одна replica и один backend worker. Модель — stub. Комплект проверен дома; PROD, HA, реальные модели и автоматический crash recovery не приняты.

## Состав и условия

Поставка содержит `kit/` (chart 0.1.0, совместимые Python helpers, tests и этот runbook), `images.oci.tar.gz`, `manifest.json`, `SHA256SUMS`. В OCI-архиве шесть зафиксированных образов: backend `audit-v16-cleanup-budget`, frontend `audit-v3`, stub `kube-smoke`, PostgreSQL 17, Qdrant 1.19.0, Keycloak 26.8.0. Digests и SHA-256 файлов записаны в manifest; теги приложения повторно не собирать и не заменять.

Исходники operator — самостоятельный snapshot домашнего набора 04.10 с baseline `f551f5f`, обновлёнными home helpers и двумя узкими исправлениями (single-host TLS и отказ автоматического restore). Они зафиксированы файловыми SHA-256, а не объявлены идентичными текущему Git HEAD. Большой незавершённый lifecycle из рабочего дерева не входит в этот набор.

Проверенный host: Python 3.12.3 / PyYAML 6.0.1, Docker 29.8.1, kind 0.33.0, kubectl 1.37.0, Helm 4.3.0. VM имеет около 10 GiB RAM; перед второй копией были проверены свободная память и диск. Нужны Python venv/ensurepip (в Ubuntu пакет python3-venv), openssl, default StorageClass и [платформенные prerequisites](KUBERNETES_HOME_ACCESS.md#платформенные-компоненты): Gateway API 1.6.2 и kgateway 2.4.5. Они устанавливаются отдельно. Проверка не является полной чистой установкой кластера или air-gap поставкой; импорт архива проверен на втором kind.

Все следующие команды выполняет один оператор из корня распакованной поставки на выделенном домашнем Linux host. Context и два namespace должны быть новыми для приложения. Приватный каталог — вне `kit/`, mode 0700; секреты не передаются в аргументах и не входят в поставку.

## Установка

```bash
set -euo pipefail
sha256sum -c SHA256SUMS
context=kind-home
cluster=home
access=okf-home-access
app=okf-home-app
private="$HOME/okf-home-private/access"  # ещё не существует
backups="$HOME/okf-home-backups"
# На готовом выделенном kind, после prerequisites:
docker load -i images.oci.tar.gz
# OCI содержит только amd64 blobs. kind load --all-platforms не подходит.
# Один healthy single-node kind; не подставлять произвольный container.
gzip -dc images.oci.tar.gz | docker exec -i "${cluster}-control-plane" \
  ctr --namespace k8s.io images import --platform linux/amd64 -
cd kit
python3 -m venv "$HOME/.venvs/okf-home"
source "$HOME/.venvs/okf-home/bin/activate"
python3 -m pip install -r scripts/kubernetes/requirements.txt
python3 scripts/kubernetes/home_access.py --context "$context" \
  --namespace "$access" --application-namespace "$app" \
  --private-directory "$private" \
  --keycloak-image quay.io/keycloak/keycloak@sha256:b0f60d489d51c5d113390bdf5461d4c06e6051be026c05549f2e1e10ec352bcc \
  --runtime-trust-configmap compact-home-roots \
  --backend-image-tag audit-v16-cleanup-budget
values=(--values "$private/base.values.yaml" --values "$private/sso.values.yaml" --values examples/compact.values.yaml)
helm lint deploy/helm/ai-knowledge -f "$private/base.values.yaml" -f "$private/sso.values.yaml" -f examples/compact.values.yaml
helm template ai-knowledge deploy/helm/ai-knowledge -n "$app" -f "$private/base.values.yaml" -f "$private/sso.values.yaml" -f examples/compact.values.yaml > "$private/rendered.yaml"
python3 scripts/kubernetes/lifecycle.py deploy --context "$context" --namespace "$app" \
  --expected-tier home "${values[@]}" --backup-root "$backups"
kubectl --context "$context" -n "$access" rollout status deployment/keycloak --timeout=300s
python3 scripts/kubernetes/home_probe.py --context "$context" --namespace "$app" --marker CompactHomeFixture
```

У kind по умолчанию имя node-container `home-control-plane`, если cluster=home. У установленного стенда имя node-container сверять через `kind get nodes --name "$cluster"`. Не импортировать в неизвестный узел.

Имена HTTPS доступны внутри кластера. Для UI-проверки нужен браузер в этом же сетевом пространстве (в приёмке был изолированный Chromium Pod) с доверенным публичным `ca.crt`; не отключать проверку TLS. Пароли синтетических `lab-editor` и других пользователей лежат в приватном `probe.json`. Домашние DNS/VPN и trust Windows не меняются. Стенд использует отдельные leaf-сертификаты IdP и приложения с единственным SAN; срок семь дней. После истечения сертификатов нужна явная замена в тестовых namespaces.

Браузерный smoke: войти → загрузить небольшой DOCX → дождаться «Готов» → найти его → открыть «Концепты и чанки» → в чате задать вопрос с уникальным термином → открыть полученный источник. Stub подтверждает plumbing, не смысловое качество ответа.

```bash
kubectl --context "$context" -n "$app" rollout restart deployment/application
kubectl --context "$context" -n "$app" rollout status deployment/application --timeout=600s
python3 scripts/kubernetes/home_probe.py --context "$context" --namespace "$app" --marker CompactAfterRestart
```

## Обслуживание

Повторный `deploy` с теми же values проверяет guards, останавливает приложение, сохраняет прежний набор данных, мигрирует и запускает приложение. Обычное обновление сохраняет `runtime-secret/APP_SECRET_KEY`. Менять images можно только на согласованный совместимый набор; проверенный переход frontend `kube-smoke` → `audit-v3` сохранил backend, schema, документы и ключ.

```bash
python3 scripts/kubernetes/lifecycle.py backup --context "$context" --namespace "$app" \
  --expected-tier home "${values[@]}" --backup-root "$backups"
```

`backup` возвращает исходное приложение в работу после согласованной копии. Если для DR его нужно оставить остановленным, использовать явно показанные ниже `Manager.acquire()` → `stop()` → `backup_stopped()` → `release_lock()`; не удалять PVC. Format 2 включает PG dump, Qdrant snapshot, файлы, totals, release identity и при необходимости публичный trust bundle. `verify_backup` проверяет SHA-256. Скопировать закрытый каталог копии вне исходной VM, проверить SHA-256 архива на обеих сторонах. Копия содержит пользовательские данные и может содержать auth records: хранить отдельно от публичной поставки.

При Failed/timeout Job приложение остаётся остановленным и lock сохраняется. Сначала вручную сверить healthy node, writers, Job/Pods, claims и целостность данных; штатно завершить/удалить только известную собственную операцию и дождаться Pods. `recover-lock --operation-id` допускается после такого разбора, а не как resume. При неизвестном состоянии узла или writers работа прекращается. Не использовать force delete и unsafe-флаги.

## Восстановление

Автоматическая команда и библиотечный метод `restore` в этом комплекте запрещена до обращения к кластеру. Проверен ручной остановленный restore из встроенного в frozen archive `kit/docs/KUBERNETES_COMPACT_RESTORE.md`: новый namespace, четыре новых PVC, тот же набор образов/model/storage/trust, нативный PG restore одной транзакцией, Qdrant/files, totals и strict content integrity. Восстановленный audit outbox удерживается; новая session key и удаление всех `auth_sessions`/`oidc_login_flows` выполняются до первого старта. Source-политика сессий описана отдельно в [инструкции](KUBERNETES_SESSION_RECOVERY.md).

При несовместимой миграции возврат выполняется из проверенной копии в новом target. Автоматический downgrade БД и продолжение прерванного импорта не поддерживаются. После ошибки сохранять артефакты/lock для ручного разбора; следующую попытку начинать в отдельном пустом target.

## Проверки и границы

```bash
python3 -m pip install -r tests/requirements.txt
python3 -m pytest --noconftest -q tests
```

Домашняя приёмка 10.10: новая установка; HTTPS/OIDC, роли/CSRF; браузерный DOCX/search/chat/source; пересоздание Pod; обновление; контролируемый отказ миграции; непустой format-2 backup вне VM; stopped restore в новые PVC; новый вход и поиск; импорт всех шести OCI images в отдельный kind. Измерено: обычное обновление 83.17 с, импорт/проверка/start restore 82.75 с. Это длительности этих прогонов, не SLA и не полный RTO.

Ограничения: stub вместо реального LLM/embeddings; target-backup не содержал активных auth sessions (очистка до старта и новый login проверены, replay старой активной cookie — нет); NetworkPolicy enforcement зависит от CNI и здесь не принят; внешняя платформа, реальные нагрузки, HA, target backup retention и PROD acceptance отдельно. Полные backend/CI проверки и интеграция в main не выполнялись. Коммит и публикация требуют отдельной авторизации.
