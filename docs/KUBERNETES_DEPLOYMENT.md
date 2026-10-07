# Развёртывание в Kubernetes

Репозиторий содержит общее переносимое решение. Настройка конкретных
GitLab/кластера, доменов, registry, секретов и платформенных components
выполняется в отдельной окруженческой копии. Примеры ниже остаются обезличенными.

Процедуры соответствуют коду ветки `codex/kubernetes-rollout` (compact baseline
`f551f5f` и последующие home tools); код ещё не интегрирован в `main`.
Копия документа в основном checkout не означает наличия там runtime реализации.
Для исполнения используйте checkout этой ветки и сверяйте открытые gates
с обновлёнными планом и отчётом домашней приёмки.

Kubernetes добавляется как отдельный способ установки. Локальные `start-all.ps1` /
`start-all.sh` и Docker Compose `bundled` / `external` сохраняются. Первая версия
chart поддерживает только bundled Kubernetes и одну реплику приложения.

## Архитектура первой поставки

Один Pod `application` содержит backend и frontend. Backend запускает один
Uvicorn worker. PostgreSQL 17 и Qdrant 1.19 работают в отдельных StatefulSets.
Services — ClusterIP; наружу публикуется только frontend. Namespace предназначен
для одного release: имя `backend` используется существующим standalone frontend.

`Recreate` исключает обычное перекрытие версий при обновлении. Обновление через
операционную процедуру дополнительно дожидается удаления старых Pods и разовых
writer Pods. При недоступном узле запрещено принудительно удалять Pod и запускать
замену: пока нет распределённого владения задачами, это может создать два backend.
Эта конфигурация не обещает высокой доступности.

Данные приложения и диагностика используют отдельные RWO PVC. Backend пишет
свою диагностику, frontend — свою; каталог frontend виден backend только на
чтение, diagnostic control — frontend только на чтение. PostgreSQL и Qdrant
имеют собственные PVC. Chart не создаёт и не удаляет PVC или Secret objects.

## Контракт среды

Реальные адреса, namespace, ключи, Vault paths, registry credentials и kubeconfig
хранятся в защищённой конфигурации оператора. Не добавляйте их в Git или отчёты.
Примеры в `deploy/kubernetes/` содержат только технические placeholders.

| Параметр | Требование |
|---|---|
| Схема установки | compact, bundled, replicas=1, workers=1 |
| DEV / TEST / PROD | независимые кластеры, namespace, данные и секреты |
| Application environment | `ENVIRONMENT=production` во всех трёх средах |
| Идентификатор среды | `DEPLOYMENT_TIER=dev/test/prod` |
| Модели DEV / TEST | `MODEL_MODE=stub`, fake embeddings, синтетические ответы |
| Модели PROD | `MODEL_MODE=live`, реальные API; stub и fake запрещены |
| Образы | application и stub по digest; явные tags разрешены только home/ci |
| Хранилище | четыре внешних PVC, подходящие UID/fsGroup и CSI |
| Backup | защищённый каталог вне исходных PVC, отдельный владелец и retention |
| Публикация | готовый Gateway, поддерживаемый ListenerSet/HTTPRoute, TLS Secret |
| Аутентификация | production SSO, HTTPS cookie, явный `KEYCLOAK_GROUP_PATH_MODE` |
| Сеть | DNS, разрешённые IdP/model endpoints, применяемые CNI NetworkPolicy |

До первой установки проверяются фактические Kubernetes/CRD версии, CPU
architecture, quota, Pod Security, permissions, StorageClass, перенос тома после
рестарта, доступ к registry и IdP, model dimensions, timeout/rate limits и RPO/RTO.
Значения домашнего стенда не заменяют эти проверки. Поддержка ListenerSet
проверяется по установленным API и контроллеру; общего Gateway требуется
`allowedListeners`. Основа схемы: [официальный ListenerSet guide](https://gateway-api.sigs.k8s.io/guides/user-guides/listener-set/).

## Подготовка

На машине оператора нужны Python 3.11+, PyYAML, Helm и kubectl:

```bash
python3 -m pip install -r scripts/kubernetes/requirements.txt
```

Создайте выделенный namespace с платформенными labels/quota и четыре PVC по
`deploy/kubernetes/storage/claims.yaml.example`. Размеры примера — начальные
значения, их нужно сверить с corpus и quota. PVC остаются вне Helm lifecycle.
Kind PVC сохраняются после удаления release, но удаление самого kind cluster
уничтожает его local storage: копии должны находиться снаружи.

Подготовьте Opaque runtime Secret защищённым способом. Обязательные ключи:
`DATABASE_URL`, `POSTGRES_PASSWORD`, `APP_SECRET_KEY`. Bundled URL использует
пользователя `okf`, базу `okf_knowledge`, сервис `postgres:5432` и драйвер psycopg.
Пароль должен совпадать с `POSTGRES_PASSWORD`. Смена этого Secret сама по себе
не меняет пароль уже созданного PostgreSQL: это отдельная операционная задача.
Для SSO и live моделей добавляются соответствующие ключи конфигурации. Frontend
не получает runtime Secret. Не передавайте секреты через `--set`, shell history
или ConfigMap. Runtime Secret keys должны быть UPPER_CASE; chart-controlled
environment/model/storage identity в Secret запрещены. Backup восстанавливает данные; восстановление/ротация ключей
выполняется отдельно. Смена `APP_SECRET_KEY` инвалидирует подписанные сессии.

VSO может поддерживать уже существующий Secret, namespace binding и VaultAuth
задаёт платформа. Chart имеет опциональный VaultStaticSecret. Для первоначального
получения секрета используйте `python3 scripts/kubernetes/lifecycle.py provision`
с теми же `--context`, `--namespace`, `--values`: только свежий namespace,
operation lock, отключённое application и ожидание VSO/хранилищ. Затем запустите
общий deploy. Не запускайте application напрямую.
Эту bootstrap-интеграцию нужно проверить на фактическом VSO; домашний стенд
использует самостоятельно созданный синтетический Secret.

Составьте защищённый values-файл по подходящему example. Укажите ссылки на PVC и
Secret, image digests, SSO и разрешённый egress. DEV/TEST требуют отдельный digest
stub image. Продвигайте одинаковые digests backend/frontend и chart между средами;
тестовый индекс не переносится в PROD. Миграционный Job сохраняет режим моделей,
embedding provider/model, размер векторов и Qdrant collection в
`/data/.kubernetes-model-profile.json`; несовпадение этого профиля
или непустые данные без marker отклоняются. Импорт старой установки требует
отдельно спроектированной миграции, а не ручного создания marker.

## Установка и обновление

Все операции указывают context и namespace явно. На Windows используйте тот же
Python-файл; bash wrappers — тонкие Linux/macOS entrypoints.

```bash
python3 scripts/kubernetes/lifecycle.py preflight \
  --expected-tier dev --context CONTEXT --namespace NAMESPACE --values /protected/runtime.values.yaml
python3 scripts/kubernetes/lifecycle.py deploy \
  --expected-tier dev --context CONTEXT --namespace NAMESPACE --values /protected/runtime.values.yaml \
  --backup-root /protected/backups
```

Для проверенного packaged chart передайте `--chart /protected/ai-knowledge-VERSION.tgz`.
Preflight проверяет render/schema, namespace, обязательные Secret keys, PVC,
необходимые API и владельца namespace. Это не заменяет сетевую/SSO-приёмку.
`--expected-tier` должен совпадать с назначением защищённого pipeline: для TEST
укажите `test`, для PROD `prod`. Существующие PVC bindings менять при upgrade
запрещено; перенос хранилища выполняется отдельно.

Порядок обновления: атомарное создание namespace ConfigMap lock → scale application
до нуля → подтверждение выхода Pods → backup старым backend image и со старым
runtime config → установка chart с отключённым application → готовность PG/Qdrant
→ один Alembic Job → ожидание его выхода → включение application → readiness →
удаление собственного lock. Для чистой первой установки backup отсутствует.
Смена storage image отклоняется: обновление PostgreSQL/Qdrant не является
побочным эффектом обновления приложения.

При ошибке SQL/backup, потере pipeline или таймауте lock сохраняется; старое
приложение автоматически не запускается. `helm --atomic`, `helm rollback` и
удаление lock по TTL не используются. Не выполняйте параллельный `helm upgrade`
или ручной `scale` в обход процедуры. Потеря узла требует доказать остановку
старого процесса, прежде чем разрешать новую запись.

Для восстановления операционного доступа сначала изучите restricted Job logs,
его результат, Pods и сохранённые копии. Завершите/штатно удалите зависший Job,
дождитесь выхода его Pod. Только после этого можно снять конкретный lock:

```bash
python3 scripts/kubernetes/lifecycle.py recover-lock \
  --context CONTEXT --namespace NAMESPACE --values /protected/runtime.values.yaml \
  --operation-id OPERATION_ID
```

Команда проверяет владельца lock, отсутствие application Pods и активных
operation Jobs/Pods. Снятие lock не подтверждает исправность схемы. Для
несовместимой миграции безопасный путь — restore в новый namespace, проверка и
отдельное переключение маршрута. Image-only rollback допустим только после
проверки совместимости фактической схемы и соответствующих образов.

## Резервная копия и восстановление

```bash
python3 scripts/kubernetes/lifecycle.py backup \
  --context CONTEXT --namespace NAMESPACE --values /protected/runtime.values.yaml \
  --backup-root /protected/backups
python3 scripts/kubernetes/lifecycle.py restore \
  --context CONTEXT --namespace NEW_NAMESPACE --values /protected/restore.values.yaml \
  --backup /protected/backups/OPERATION_ID
```

Backup останавливает приложение и снимает PostgreSQL dump, Qdrant snapshot,
архив `/data`, totals и release identity с образами/Alembic revision. Строгая
проверка corpus должна пройти. Только после создания всех артефактов записывается
completed manifest с SHA-256. Secret objects и plaintext env не выгружаются.
Архивы содержат документы и пользовательские данные: нужны private ACL,
шифрование и защищённая доставка. На Windows обеспечьте ACL каталога сами;
POSIX mode каталога не заменяет Windows ACL или внешнюю политику хранения.

Restore требует другой namespace, новых PVC и отдельно подготовленных Secrets.
Существующие workloads, непустые FS/DB/Qdrant, неполная/испорченная копия и
несовпадение application images, storage images или model mode отклоняются. Восстановление начинается
на сохранённой версии приложения; затем выполняются Alembic и строгая проверка
counts/content. После этого приложение запускается. Исходная установка и её
backup остаются сохранены. Проверяйте также SSO/роли, источники, поиск и экспорт.

Остановка прерывает активные потоки/download. Фоновая задача не получает
распределённое владение или прозрачное продолжение: после запуска проверяйте
paused/failed и применяйте штатный resume. Конкретную репетицию interruption и
rollback записывайте отдельно от обычного smoke.

## TLS, доверенные CA и платформа

Расширение переносимых настроек описано в
[проекте спецификации конфигурации](superpowers/specs/2026-10-04-kubernetes-configuration-design.md).
Ещё не реализованные возможности — числовые probe overrides, опциональный
TrafficPolicy и runtime CA ConfigMap reference. Текущие values их не поддерживают;
проектные имена ключей нельзя использовать как готовый рецепт установки.

Уточнения документации отражены в [списке вопросов](KUBERNETES_PLATFORM_QUESTIONS.md).
Собственный chart предусмотрен платформенным процессом; требования заменить Next
на статический Nginx или публиковать `/api` напрямую в backend не установлены.
Это сохраняет выбранную схему до проверки допуска компактного Pod/Jobs,
остановки backend и bundled storage. Новые Ingress не создаются; фактические
API/CRD и admission каждой среды проверяются отдельно.

Gateway включается явно. Chart создаёт ListenerSet и HTTPRoute с числовым
Service port `3000`, optional HTTP→HTTPS redirect и настраиваемыми timeout.
Операторы, CRD, общий Gateway и сертификаты не устанавливаются chart.
DEV сертификат выпускает платформенный cert-manager; TEST/PROD требуют заранее
готовый TLS Secret. Владельцы renewal и срок сертификата фиксируются вне Git.
В DEV ручное создание/изменение TLS Secret не применяется. Документация описывает
90 дней и renewal по достижении ⅔ срока; действующие issuer и процедуру renewal
проверяет окруженческий оператор. Secret с TLS не доставляется через VSO.
Проверьте upload до допустимого лимита с multipart overhead, длинный streaming,
cancellation и SSO callbacks через реальный Gateway. Не уменьшайте лимит
приложения ради успешного короткого теста.

Dockerfile сохраняют стандартные defaults и поддерживают `PYTHON_IMAGE` /
`NODE_IMAGE` для разрешённых зеркал. BuildKit secret `corporate_ca` добавляет
публичный CA в Python system trust / Node extra trust; `pip_config` и `npmrc`
подключают настройки package registry без сохранения credentials в layers.
Пример для backend: `docker build --build-arg CA_BUNDLE_REVISION=PUBLIC_CA_SHA256
--secret id=corporate_ca,src=/protected/ca.crt
--secret id=pip_config,src=/protected/pip.conf -f backend/Dockerfile .`.
Frontend строится из `frontend/`, использует аналогичные `corporate_ca`/`npmrc`.
При добавлении/смене CA обязателен `CA_BUNDLE_REVISION` с SHA-256 публичного CA:
он отделяет cache от default образов и прежних сертификатов. Содержимое secret
само по себе не инвалидирует cache: [правила Docker](https://docs.docker.com/build/cache/invalidation/).
Проверка с одноразовым тестовым CA: `bash scripts/kubernetes/tests/test_ca.sh`.
Registry TLS на стороне builder/node настраивается отдельно. Не отключайте
TLS verification. Runtime CA можно включить в образ при сборке либо передать
защищённый полный trust bundle средствами платформы; смену доверия проверяйте
реальным HTTPS-запросом. Настоящие platform CA/mirrors ещё требуют приёмки.

NetworkPolicy ограничивает release, Gateway ingress, DNS и явный external egress;
stub имеет отдельный запрет egress. Значения для IdP/models задаются защищённым
values. Поддержка policy документом не доказывает enforcement CNI. Контейнеры
работают без root, privilege escalation и capabilities, без API service token.
Применяйте платформенные monitoring/logging: приложение не обещает `/metrics`.
`/health/live` проверяет только живой процесс; `/health/ready` — хранилища и PDF
провайдер; `/health` включает модельные зависимости. Недоступность модели не
должна превращать liveness в restart storm.

В декларации namespace включайте платформенный сбор формой `logging.enabled: true`;
сокращённое `logging: true` не является инструкцией менять labels приложения.
Путь Logging Operator → OpenSearch описан, но JSON parser, индекс, доступ,
retention и получение событий принимаются отдельно. Прямой клиент OpenSearch
и его credentials приложению для этого не требуются. Prometheus/Grafana не
подтверждают автоматический доступ к защищённым audit endpoints: нужен scrape/auth
contract. Пример ScrapeConfig для VM не доказывает подключение нашего приложения.

Диагностический NodePort Gateway может обходить внешний LB и принимать PROXY
protocol. Проверьте доступность этого пути, доверенных отправителей и очистку
IP headers до включения trusted-IP profile. Подпись Next→backend сама по себе
не доказывает адрес браузера до Next; прямой backend ingress также проверяется.

Для закрытой доставки отдельно задайте remote mirrors пакетов/базовых images
и repositories релизных артефактов. Перенос remote mirrors в другой registry
не означает переноса всех release repositories. Версии CA images и CI components
из samples не копируются без сверки доступных артефактов и inputs.

## CI и следующие этапы

[Открытые вопросы для согласования](KUBERNETES_PLATFORM_QUESTIONS.md) отделяют
правила платформы от особенностей примеров. Совместимость нашего chart/Next proxy
пока не подтверждена и не опровергнута; ответы определят необходимые адаптации.

Проекты следующих архитектурных контрактов:
[OIDC](superpowers/specs/2026-10-04-oidc-compatibility-design.md) и
[аудит/JSON stdout](superpowers/specs/2026-10-04-audit-delivery-design.md).
Это спецификации для рассмотрения, а не уже доступные env-параметры.
[Сопоставление дополнительных примеров](superpowers/reports/2026-10-04-deployment-examples-review.md)
фиксирует применимые идеи без окруженческих names/paths. Наш frontend-only
маршрут и compact writer contract сохраняются. Доставку stdout в защищённый
индекс проверяет отдельная окруженческая интеграция; приложение не получает
OpenSearch credentials и не устанавливает logging stack.

Модельные профили и ограничения tracked-чата:
[KUBERNETES_MODEL_PROFILES.md](KUBERNETES_MODEL_PROFILES.md).

`.github/workflows/kubernetes.yml` добавляет отдельный kind acceptance, существующий
CI сохраняется. `deploy/ci/gitlab-kubernetes.yml` — шаблон интеграции: BuildKit
собирает два application images и отдельный stub; Helm package и secret-free
release manifest фиксируют digests. DEV → RC security/promotion → TEST + restore
→ protected manual PROD → live smoke. Внешние adapters обязательны и блокируют
дальнейшие jobs при ошибке. Закрепите версии toolbox/component images, registry
auth через Vault, protected refs/environment и единую сериализацию операций.
Фактические approved component inputs, mirrors и настоящая pipeline-поставка
ещё не проверены; шаблон не означает готовую платформенную интеграцию.

Для отдельного deploy-проекта добавлен
`deploy/ci/gitlab-kubernetes-consumer.yml`: он получает release artifacts и
проверяет `operator-tools.tar.gz` через доверенный bootstrap закреплённого
toolbox. Revision приложения, schema и SHA-256 сверяются до lifecycle операций;
скрипты запускаются из проверенного комплекта без checkout исходников.
Контракт упаковки, protected inputs и внешние обязательные jobs описаны в
`deploy/ci/operator-tools/README.md`. Локальная проверка consumer выполнена;
добавлены общий Dockerfile toolbox и offline container smoke с пятью отказами.
Рецепт использует digest основы, обязательные SHA Helm/kubectl и build secrets;
образ работает как UID 1000. Локальная Linux-сборка и smoke выполнены;
публикация toolbox, настройка разрешений, CI Lint и реальная доставка остаются
отдельными gates. Старые локальные release/overlay сохраняются.

План репликации: [поэтапная доработка](superpowers/plans/2026-10-03-kubernetes-rollout.md).
Сначала отдельно реплицируется frontend после замены общего diagnostic FS на
внутренний протокол. Далее API/worker разделяются, вводятся durable queue,
shared limits/leases и файловый доступ; только после этого допускаются API и
worker replicas. Домашняя приёмка: [KUBERNETES_TESTING.md](KUBERNETES_TESTING.md).

## Временное хранилище приложения

Chart монтирует отдельные дисковые `emptyDir` в `/tmp` backend и frontend.
`TMPDIR`, `TEMP`, `TMP` заданы явно и запрещены в runtime ConfigMap/Secret.
Начальные значения требуют проверки на реальном корпусе:

| Контейнер | `temporaryStorage.*.sizeLimit` | `resources.*.requests.ephemeral-storage` | `resources.*.limits.ephemeral-storage` |
|---|---|---|---|
| backend | `1Gi` | `128Mi` | `2Gi` |
| frontend | `256Mi` | `64Mi` | `512Mi` |

Для этих трёх параметров chart принимает положительные целые `Mi`/`Gi`;
размер тома и request не могут превышать limit. Запас limit нужен также для
контейнерного слоя и логов; request учитывается планировщиком и не резервирует
физическое место на узле. `emptyDir.sizeLimit` не заменяет запас диска и контроль
eviction: исчерпание ephemeral-storage может привести к остановке Pod.
Использование `medium: Memory` этим профилем не предусмотрено.
[Семантика emptyDir](https://kubernetes.io/docs/concepts/storage/volumes/#emptydir),
[учёт ephemeral-storage](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/#local-ephemeral-storage).

Том сохраняется при рестарте контейнера внутри Pod и удаляется при удалении Pod.
Остатки audit snapshots после аварии могут накапливаться; безопасная durable
очистка по владельцу ещё не реализована. Не удаляйте их wildcard-командами.
Private audit copies, parser/PDF/export temp требуют замера максимального
потребления и свободного места. `/data` staging/upload и diagnostics PVC остаются
отдельными постоянными путями. Frontend `.next` cache остаётся в записываемом
слое образа: приёмка writable paths перед read-only root ещё открыта.

Миграции, backup/restore и toolbox Jobs наследуют backend `/tmp` и его ресурсы;
их temp удаляется с собственным Pod. Старый шаблон до этой функции остаётся
пригодным для backup старым image. Отдельный `/work` для backup пока не ограничен
этим sizeLimit: его пиковый размер и общий ephemeral budget требуют отдельного
расчёта перед эксплуатацией.

Пароль PostgreSQL по-прежнему передаётся через защищённый runtime Secret
(`DATABASE_URL`/`POSTGRES_PASSWORD`). Chart не создаёт `.pgpass` и не выполняет
его копирование/ротацию. Общий продуктовый профиль CA/CRL для application,
migration и backup/restore ещё не включён: текущий preflight не принимает query
параметры `DATABASE_URL`. Нельзя считать общий HTTP trust bundle настройкой libpq.
Поддержка PostgreSQL TLS потребует единого контракта, native TLS/auth/revocation
проверки и учёта restore; file-only Secret proof этого не подтверждает.


Проект следующего этапа: [opt-in PostgreSQL TLS](superpowers/specs/2026-10-05-kubernetes-postgres-tls-design.md).
Он включает серверный cert/key/HBA, единый client/Jobs profile и backup compatibility.
Это **предложение, пока не поддерживаемые values**; действующий bundled chart
не включает серверный TLS. Согласование и native/full lifecycle gates открыты.


После проверки подготовлен [план TLS T1–T6](superpowers/plans/2026-10-05-kubernetes-postgres-tls.md).
Первоначальный disabled bundled запуск не зависит от его реализации при отсутствии
требования PG TLS. Предлагаемые новые CLI/profile/schema/backup параметры пока не
поддерживаются текущими tools. Legacy backup без PG identity трактуется как unknown,
а процедура восстановления с заменой истёкшего trust входит в будущую TLS-приёмку.
