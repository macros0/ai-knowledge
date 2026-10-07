# Домашний HTTPS и OIDC

Процедуры соответствуют коду ветки `codex/kubernetes-rollout` (compact baseline
`f551f5f` и последующие home tools); код ещё не интегрирован в `main`.
Копия документа в основном checkout не означает наличия там runtime реализации.
Для исполнения используйте checkout этой ветки и сверяйте открытые gates
с обновлёнными планом и отчётом домашней приёмки.

Этот прогон дополняет [минимальный smoke](KUBERNETES_TESTING.md). Он создаёт
отдельную compact-копию с `ENVIRONMENT=production`, `DEPLOYMENT_TIER=home`,
Keycloak OIDC и модельным stub. Это проверка production-аутентификации на
синтетических данных; реальные модели и целевая платформа проверяются отдельно.

Существующая демонстрационная установка сохраняется. Домашние DNS, VPN и
доверие Windows не меняются. Имена `knowledge` и `identity` доступны только
внутри Kubernetes через ExternalName Services. Проверка проходит в отдельном
Pod через настоящий Envoy Gateway, frontend proxy, backend и IdP. Она выполняет
HTTP-переходы login form, а не проверку интерфейса в браузере.

## Платформенные компоненты

Проверенные версии: Gateway API 1.6.2, kgateway 2.4.5, Keycloak 26.8.0.
ListenerSet использует стандартный API `gateway.networking.k8s.io/v1`.
См. [установку kgateway](https://kgateway.dev/docs/envoy/latest/install/helm/),
[ListenerSet](https://gateway-api.sigs.k8s.io/guides/user-guides/listener-set/) и
[контейнер Keycloak](https://www.keycloak.org/server/containers).

На выделенной Linux VM нужны уже подготовленные kind, kubectl, Helm, Docker,
Python/PyYAML и openssl. Перед запуском измерьте `free -m` и свободный диск;
оставьте запас для второй копии приложения, IdP, Gateway и сборки образа.
Настройки тестового IdP: heap до 384 MiB, request 512 MiB, limit 1 GiB;
контроллер и Envoy: каждый request 128 MiB, limit 512 MiB.
Лимиты основного приложения не уменьшаются для этого прогона.

Следующие команды предназначены только для выделенного домашнего kind.
Они устанавливают cluster-scoped CRD и RBAC; не запускайте их против DEV/TEST/PROD.

```bash
set -euo pipefail
context=kind-okf-kube
artifacts="$HOME/okf-home-access"
mkdir -p "$artifacts"
curl -fsSL https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.6.2/standard-install.yaml \
  -o "$artifacts/gateway-api-v1.6.2.yaml"
kubectl --context "$context" apply --server-side -f "$artifacts/gateway-api-v1.6.2.yaml"
helm upgrade -i --kube-context "$context" --create-namespace -n okf-access-system \
  --version v2.4.5 kgateway-crds oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds
helm upgrade -i --kube-context "$context" -n okf-access-system \
  --version v2.4.5 kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --set controller.resources.requests.memory=128Mi --set controller.resources.limits.memory=512Mi \
  --set controller.resources.requests.cpu=100m --set controller.resources.limits.cpu=1
kubectl --context "$context" -n okf-access-system rollout status deployment/kgateway --timeout=300s
```

Gateway слушает HTTP/HTTPS через ClusterIP. Его `allowedListeners` и HTTP
`allowedRoutes` допускают только namespace с меткой `okf-home-access=true`.
Это настройка тестового shared Gateway, а не требование менять платформенный Gateway.

## Подготовка realm и новой копии

Выполняйте из корня репозитория. Сначала соберите штатные frontend и stub,
загрузите их в kind, как в минимальном smoke. Затем:

```bash
docker pull quay.io/keycloak/keycloak:26.8.0
keycloak_image=$(docker image inspect quay.io/keycloak/keycloak:26.8.0 --format '{{index .RepoDigests 0}}')
docker save --platform linux/amd64 quay.io/keycloak/keycloak:26.8.0 -o "$artifacts/keycloak-amd64.tar"
kind load image-archive "$artifacts/keycloak-amd64.tar" --name okf-kube
docker build -f backend/test_scripts/model_stub/Dockerfile \
  -t okf-model-stub:home-tokenizer backend/test_scripts/model_stub
docker save --platform linux/amd64 okf-model-stub:home-tokenizer -o "$artifacts/stub-amd64.tar"
kind load image-archive "$artifacts/stub-amd64.tar" --name okf-kube
python3 scripts/kubernetes/home_access.py --context "$context" \
  --namespace okf-access --application-namespace okf-sso \
  --private-directory "$artifacts/private" --keycloak-image "$keycloak_image"
ca_revision=$(sha256sum "$artifacts/private/ca.crt" | cut -d ' ' -f1)
backend_tag="home-sso-$ca_revision"
docker build -f backend/Dockerfile --build-arg CA_BUNDLE_REVISION="$ca_revision" \
  --secret id=corporate_ca,src="$artifacts/private/ca.crt" -t "okf-backend:$backend_tag" .
docker save --platform linux/amd64 "okf-backend:$backend_tag" -o "$artifacts/backend-amd64.tar"
kind load image-archive "$artifacts/backend-amd64.tar" --name okf-kube
python3 scripts/kubernetes/lifecycle.py deploy --context "$context" --namespace okf-sso \
  --expected-tier home --values "$artifacts/private/base.values.yaml" \
  --values "$artifacts/private/sso.values.yaml" --backup-root "$artifacts/backups"
kubectl --context "$context" -n okf-access rollout status deployment/keycloak --timeout=300s
```

`home_access.py` требует два новых разных `okf-*` namespace, kind context и
официальный Keycloak image с digest. Повторный запуск на существующие namespace
запрещён. Приватный каталог создаётся вне репозитория с правами 0700, файлы —
0600. Там находятся ключ CA, TLS-ключ и случайные синтетические пароли.
Realm и probe credentials передаются в Secrets через stdin; values не содержат
паролей. Private CA key не передаётся в образ. TLS сертификаты действуют семь дней.
Тег backend содержит SHA-256 публичного CA и совпадает с подготовленным overlay.
Новый CA получает новый тег, поэтому повторная сборка не заменяет образ предыдущего стенда.

Realm `okf-home` допускает authorization code flow для confidential client
`knowledge`; password grant выключен. Redirect URI задан точно, claim `group`
содержит полные пути `/lab/viewer`, `/lab/editor`, `/lab/admin`, `/lab/security`.
Дополнительный пользователь без группы проверяет отказ в доступе.
Keycloak `start-dev` и его непостоянная локальная БД предназначены для этого
изолированного теста; перезапуск IdP заново импортирует realm и сбрасывает SSO-сессии.

Домашний профиль `local_qwen` использует дополнительные тестовые `/props`,
`/apply-template` и `/tokenize`. Stub возвращает детерминированное приближение
числа токенов с меткой `test_only`; это контракт для исполнения tracked chat,
а не проверка настоящего токенизатора или качества модели. Production-бюджет
и его fail-closed проверки сохраняются.

## Приёмка

```bash
python3 scripts/kubernetes/home_probe.py --context "$context" --namespace okf-sso
kubectl --context "$context" -n okf-access get gateway,listenerset,httproute
kubectl --context "$context" -n okf-sso get listenerset,httproute,pods
free -m
```

Для проверки задержки и отмены отдельно задайте fixture только тестовому stub:

```bash
kubectl --context "$context" -n okf-sso set env deployment/model-stub \
  MODEL_STUB_SCENARIO=delay MODEL_STUB_DELAY_SECONDS=25
kubectl --context "$context" -n okf-sso rollout status deployment/model-stub --timeout=300s
python3 scripts/kubernetes/home_probe.py --context "$context" --namespace okf-sso --delay-seconds 25
# Выполнить также после отказа probe: вернуть обычный режим синтетической модели.
kubectl --context "$context" -n okf-sso set env deployment/model-stub \
  MODEL_STUB_SCENARIO- MODEL_STUB_DELAY_SECONDS-
```

Проверяется соединение с задержкой ответа модели 25 секунд и keepalive,
отмена tracked попытки через тот же HTTPS-маршрут и сохранение `stopped`/пустого
ответа после окна задержки. Это не проверка максимально допустимой длительности
потока, падения узла или отмены через элементы интерфейса браузера.

Runner принимает только home/stub/OIDC профиль с тестовым realm. Он создаёт
одноразовый Job с тем же backend image и синтетическим Secret, без API token.
Успешный Job и его ConfigMap удаляются; при отказе сохраняются для диагностики.
В логах нет паролей, cookies, токенов или OAuth URL с кодами.

Probe проверяет TLS с доверенным CA и подтверждённый отказ проверки сертификата
без него; DNS и другие сетевые ошибки не засчитываются как успешный отказ TLS.
Также проверяются HTTP redirect, отказ anonymous,
вход и роли четырёх пользователей, отказ пользователю без группы, Secure/HttpOnly
cookie, отсутствующий/неверный CSRF, запрет загрузки viewer, DOCX multipart более
2 MiB, генерацию, источники, BM25, поток чата и выход из приложения и IdP.
После выхода требуется возврат на точный post-logout URI и форма повторного
ввода пароля: страница подтверждения выхода или автоматический вход дают отказ теста.
Размер 2 MiB не доказывает работу на максимальном лимите загрузки; обычный поток
не доказывает допустимость длительных соединений и корректную отмену.

Default kindnet **не исполняет NetworkPolicy**. Наличие объектов политик не
закрывает gate сетевой изоляции. Его нужно отдельно повторить на выделенном
kind с CNI, поддерживающим enforcement, или на предоставленной платформе.
VSO, отказ узла, браузерный UI и ограничения целевых сред этим прогоном не проверяются.

Отдельный enforcing-CNI прогон: [NetworkPolicy в домашнем стенде](KUBERNETES_NETWORK_TESTING.md).

Для нового прогона после истечения CA используйте свежие namespace, новый
приватный каталог и заново собранный/загруженный образ. Не удаляйте действующий
kind или PVC демонстрационной базы. Удаление тестовой копии, её данных и
приватных артефактов выполняйте отдельно после сохранения нужного backup.


## Runtime CA без пересборки приложения

Для сравнения конфигураций используйте одинаковые backend/frontend/stub образы.
`home_access.py --runtime-trust-configmap runtime-ca-original --backend-image-tag configuration-v2`
добавляет public lab CA к стандартным корням выбранного backend образа и создаёт
immutable ConfigMap в новом application namespace. Legacy режим с отдельным
CA-build tag сохраняется, если эти параметры не заданы.

Runtime bundle передаётся backend через `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE`,
frontend — через `NODE_EXTRA_CA_CERTS`. Приватный CA key остаётся только в private
каталоге оператора. `home_probe.py` использует собственный client bundle;
успех этого клиента проверяет доступ и поток, но доверие приложения дополнительно
проверяется настоящим OIDC flow, Node HTTPS вызовом и backend operation-image Job.

Для полной конфигурационной приёмки применяйте runner из раздела
[конфигурационной приёмки](KUBERNETES_TESTING.md). Он требует два независимо
проверенных комплекта, хранит backup и логи вне репозитория и не удаляет cluster.
При ошибке не очищайте PVC или maintenance lock автоматически.


## Явные OIDC профили

Общий контракт: [OIDC_CONFIGURATION.md](OIDC_CONFIGURATION.md). Home helper
по умолчанию сохраняет confidential/basic/none/auto. Для отдельной public
регистрации добавьте к home_access.py:

```bash
--client-type public --token-auth-method none --pkce-method S256 --userinfo-source endpoint
```

Для confidential/post+S256: `--token-auth-method client_secret_post --pkce-method S256`.
Каждый профиль использует свежие namespaces/PVC/Secrets/callbacks. Public realm
не содержит client secret, требует S256 и при endpoint отдаёт группы только
в UserInfo. Probe проверяет фактический authorization challenge и роли.
Опциональные одноимённые profile flags home_probe.py проверяют совпадение с
runtime ConfigMap; credentials и opaque state в вывод не попадают.

Для всех профилей используйте один новый backend/frontend image и runtime CA
через `--backend-image-tag` + `--runtime-trust-configmap`, без rebuild для CA.
Restore требует нового APP_SECRET_KEY до старта, затем инвалидизацию OIDC
sessions/flows до открытия доступа. Приёмка этой доработки: [OIDC отчёт](superpowers/reports/2026-10-04-oidc-compatibility-acceptance.md).
Ранний configuration отчёт public OIDC не подтверждает.
