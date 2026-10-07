# Проверки Kubernetes

Процедуры соответствуют коду ветки `codex/kubernetes-rollout` (compact baseline
`f551f5f` и последующие home tools); код ещё не интегрирован в `main`.
Копия документа в основном checkout не означает наличия там runtime реализации.
Для исполнения используйте checkout этой ветки и сверяйте открытые gates
с обновлёнными планом и отчётом домашней приёмки.

Домашний стенд — выделенная Ubuntu VM, 4 vCPU, **10 ГиБ выделенной RAM**,
диск100 ГиБ после проверенного расширения07.10.2026. Около5 ГиБ RAM и58 ГиБ
диска оставались доступными после запуска обоих существующих kind-кластеров.
Прежние6 ГиБ RAM/50 ГиБ disk остаются историческим baseline. При дефиците RAM разрешено штатно остановить отдельную тестовую
инсталляцию; рабочие model services сохраняются. Адреса и номера инфраструктуры
хранятся в локальной конфигурации, не в этом документе.

Минимальный smoke: один kind control-plane, default storage, port-forward,
синтетические данные и stub. Дополнительные namespaces запускаются последовательно;
это не три независимых кластера. Kind data находятся внутри node container;
backup на VM находится вне cluster. Нельзя удалять cluster, пока единственная
копия корпуса находится в его PVC.

## Воспроизводимый минимальный прогон

[Локальный отчёт 04.10.2026](superpowers/reports/2026-10-04-kubernetes-portable-acceptance.md)
фиксирует сборку текущей ветки и полный минимальный lifecycle на отдельном
Docker/kind стенде: install, DOCX/BM25/stream, restart, повторное применение
той же поставки с backup, непустой restore и сохранение четырёх PVC при uninstall.
174 контрактных теста прошли без пропусков. Реальные модели, целевая платформа
и переход между разными app/schema версиями этим прогоном не подтверждены;
`npm audit` остаётся с пятью high замечаниями в зависимостях линтинга.

Матрица tracked-чата `documents/fast/full`, synthetic token budget и отказ
неподдерживаемого live provider описаны в
[KUBERNETES_MODEL_PROFILES.md](KUBERNETES_MODEL_PROFILES.md). Эти локальные
контракты общего решения проверяются отдельно от окруженческой приёмки.

Контракт отдельного deploy-проекта проверяется без обращения к Kubernetes:
из `backend/` запустите `python -m pytest tests/test_kubernetes_release.py -q`.
Нужны Python 3.11+, PyYAML 6.0.3, Helm и kubectl в PATH; для локального Helm
можно задать `HELM_BIN`. Subprocess-тест проверяет скачанный комплект из пустого
каталога без исходников. Проверки подмены, небезопасного архива и отсутствующего
runtime должны завершаться до создания tools directory. Это локальный контракт;
настоящую публикацию/скачивание, GitLab CI Lint и mandatory gates нужно проверять
отдельно по `deploy/ci/operator-tools/README.md`.

Нужны Docker, kind, Helm, kubectl, Python и PyYAML. Проверенная исходная матрица
домашнего запуска: Docker 29.8.1, kind 0.33.0, Helm 4.3.0, Kubernetes/kubectl 1.37.
При другой целевой версии повторите прогоны с подходящим node image.

```bash
kind create cluster --name okf-kube --image kindest/node:v1.37.0 \
  --config deploy/kubernetes/lab/kind.yaml --wait 120s
docker build -f backend/Dockerfile -t okf-backend:kube-smoke .
docker build -f frontend/Dockerfile -t okf-frontend:kube-smoke frontend
docker build -f backend/test_scripts/model_stub/Dockerfile \
  -t okf-model-stub:kube-smoke backend/test_scripts/model_stub
kind load docker-image --name okf-kube \
  okf-backend:kube-smoke okf-frontend:kube-smoke okf-model-stub:kube-smoke
python3 -m pip install -r scripts/kubernetes/requirements.txt
bash scripts/kubernetes/tests/acceptance.sh kind-okf-kube okf-lab /private/okf-lab-artifacts
```

`lab.py` принимает только kind context, создаёт новый namespace, случайные
синтетические Secrets и новые PVC. Values не содержат секретов. Только для этого
изолированного прогона выставлены `DEPLOYMENT_TIER=ci`, `ENVIRONMENT=development`
и отключён auth; использовать такой профиль для DEV/TEST/PROD нельзя.

Acceptance устанавливает storage, выполняет миграцию и запускает compact Pod,
создаёт непустой deterministic corpus, проверяет файлы, BM25, DOCX upload,
stub generation/index, source inventory и chat stream через standalone frontend.
Затем выполняет restart, update с backup, дополнительный backup, restore в новый
namespace, повторный functional smoke и uninstall restored release. Четыре PVC
должны остаться. Source release и private backups сохраняются для исследования.
Повторный запуск требует нового namespace prefix и каталога, чтобы не затереть
прошлый результат. Артефакты и raw logs не публикуются.

Для открытия минимального стенда можно использовать отдельный SSH tunnel к VM
и `kubectl --context kind-okf-kube -n NAMESPACE port-forward svc/frontend
16300:3000 --address 127.0.0.1`. Это проверка через port-forward, а не Gateway.

## Gate первой версии

Для operator toolbox есть независимая проверка без кластера:
`python scripts/kubernetes/tests/toolbox_smoke.py --image <built-local-image>`.
Сначала собрать recipe из `deploy/ci/operator-tools/Dockerfile` с digest основы
и SHA инструментов, как описано в `deploy/ci/operator-tools/README.md`. Consumer
запускается без сети и checkout, с read-only root, UID 1000 и только synthetic
release artifacts. Проверяются overlay/help и пять отказов до создания tools.
Отдельная Linux probe воспроизводит PermissionError для root-owned каталога
0700 и подтверждает чтение от UID 1000 после 0755/0644 у synthetic fixtures.
GitHub job `operator-toolbox` делает ту же сборку/проверку отдельно от kind.
Публикация, provenance и фактический registry digest требуют своей приёмки.

| Gate | Минимальный дом | Полная приёмка |
|---|---|---|
| Render/schema и запрет replicas/fake PROD | unit/Helm tests | фактический admission |
| PVC, PG/Qdrant, миграции, readiness | kind | CSI и quota целевых сред |
| Upload/generation/index/search/stream | DOCX + synthetic stub | DOCX/XLSX/PDF/MSG и реальный corpus |
| Backup/restore и uninstall retention | непустой corpus | согласованные RPO/RTO, отдельное backup хранилище |
| Ошибка миграции | unit failure paths; отдельно реальный failed Job | операционная репетиция с lock/recovery |
| SSO/CSRF/роли | [отдельный домашний OIDC-прогон](KUBERNETES_HOME_ACCESS.md) | фактический IdP, login/logout, callback и роли |
| TLS/Gateway/большой upload | домашний kgateway/CA, multipart >2 MiB | cert renewal, максимальный upload и длительность stream/cancellation |
| NetworkPolicy | [отдельный Cilium-прогон](KUBERNETES_NETWORK_TESTING.md) | фактический CNI: allowed/denied probes |
| Platform CI/VSO/mirrors/CA | integration template | реальные DEV/TEST/PROD pipeline и секреты |
| Live model/dense search | не проверяются stub | PROD live smoke, размер векторов, лимиты и цитаты |
| Отказы узлов / HA | не доказаны одной VM | отдельные VM/узлы на будущих этапах |

Непроверенный gate остаётся непроверенным. Статус «готово к промышленной
эксплуатации» требует фактических DEV/TEST/PROD проверок. Формы документов,
model errors 429/503/interrupted SSE проверяются отдельными контрактными тестами;
это не оценка качества ответа реальной модели.

Полный домашний стенд дополняется Gateway API/kgateway, CNI с policy enforcement,
отдельным тестовым IdP, TLS и локальным DNS. Предварительный ориентир — 4–6 vCPU,
16 ГиБ RAM, 120–160 ГиБ disk; это оценка для планирования, не уже выделенные ресурсы.
Проверки отказов узлов требуют нескольких VM. Встроенный stub не делает внешние
запросы и никогда не используется как production provider.


## Конфигурационная приёмка schema 1/2

`python scripts/kubernetes/tests/configuration_acceptance.py --context "$LAB_CONTEXT" --artifact-root "$PRIVATE_ARTIFACT_ROOT" --kit-v1 "$KIT_V1" --kit-v2 "$KIT_V2" --trusted-inputs "$TRUSTED_INPUTS" --keycloak-image "$KEYCLOAK_IMAGE"`

Runner предназначен для выделенного Linux kind/Docker стенда. Private root должен
быть новым и находиться вне repository checkout; POSIX permissions — 0700/0600.
Каждый kit содержит `incoming/release.json`, `incoming/chart.tgz`,
`incoming/operator-tools.tar.gz` и `evidence.json` с полным image inventory.
Отдельный защищённый trusted-inputs JSON содержит `mode=local-lab`, а для `kits.v1`
и `kits.v2` — ожидаемые `revision`, `toolbox_image`, `actual_toolbox_image_id`,
`release_sha256`. Этот файл не формируют из скачанного комплекта при consumption.

До записи runner проверяет release/chart/tools SHA и image identities. Matching
bootstrap запускается внутри соответствующего закреплённого toolbox без сети,
под непривилегированным пользователем. Schema 1/2 lifecycle выполняются отдельно
из проверенных archives; исторический schema-1 backup не импортируется tools 2.

Локальный режим использует Docker configuration IDs с проверенными tags, чтобы
сравнить одинаковые application images. Это синтетическое lab доказательство;
registry publication и promoted OCI digests проверяются отдельным delivery gate.
Для запуска внутри отдельного lab-оператора доступны `--container-volume` и
`--run-id`: том `okf-config-<run-id>` должен иметь одноимённую owner label; все
private пути должны находиться в этом томе `/lab`. Docker socket предоставляется
только доверенному lab-оператору, application Pods его не получают.

Сценарии идут последовательно: minimal lifecycle, actual legacy restore/upgrade,
A/B HTTPS/SSO с изоляцией по уникальному маркеру, policy attachment, контролируемые
finite/zero/idle streams, реальный delayed chat/cancellation, public CA rotation,
отказ HTTPS при неверном CA и восстановление доверия, backup/restore в новые PVC.
Граница upload проверяется файлом 100 MiB плюс multipart; лимит не уменьшается.
`result.json` и command logs остаются приватными; в stdout выводятся только gates.
Cleanup выполняется только после успешных сценариев: UID и `okf-lab-run` сверяют
с inventory перед удалением созданных namespaces. При ошибке ресурсы/backup
сохраняются; существующий cluster runner не удаляет.
Enforcing CNI, target admission, live models и dependency audit остаются отдельными
обязательствами; успешный synthetic runtime не закрывает эти gates.

Фактический объём последнего прогона, сохранённые failures и открытые gates:
[отчёт конфигурационной приёмки](superpowers/reports/2026-10-04-kubernetes-configuration-acceptance.md).

## Проверка временного хранилища

Модуль `backend/tests/test_kubernetes_temporary_storage.py` проверяет реальный Helm render/schema, независимые /tmp, размеры/resources, наследование writer Jobs, отказ повреждённого контракта и legacy backup. Нужен настоящий Helm; пропуск тестов при его отсутствии не является PASS. Runtime writable/sizing и disk-pressure нового chart проверяются отдельно на одноразовом release перед upgrade домашнего приложения. [Результат выбранного пакета](superpowers/reports/2026-10-04-audit-outbox-acceptance.md#product-temporary-storage-05102026).


Для нескольких kind-кластеров на общей VM отдельно проверяйте
`fs.inotify.max_user_instances`. На домашнем стенде подтверждён предел128
при128 занятых экземплярах; установлен persistent512. Увеличение RAM
само по себе этот предел не меняет. При ошибке `fsnotify ... too many open files`
проверяйте занятые inotify instances и FD limits перед изменением настроек.


Повторная проверка готовности после выделения ресурсов завершена: основной
kind-кластер имеет20 из20 активных Deployment/StatefulSet Ready, второй —7 из7.
Kube-proxy и local-path-provisioner восстановились после штатных повторов.
Предшествующая ошибка Qdrant во втором кластере была связана с отсутствующим
containerd image alias: восстановлена только ссылка на уже сохранённый образ
с тем же pinned digest. Образ не скачивался и версия не менялась. Node container
и PVC идентичности сохранены; Pod/PVC не удалялись. Эта готовность не заменяет
функциональную проверку UI/login, backup/restore или отказоустойчивости.
