# Домашняя приёмка первой Kubernetes-поставки

Дата: 03.10.2026. Статус: compact bundled реализован и проверен на домашнем kind;
целевые DEV/TEST/PROD ещё не проверены. Конфигурация доступа пока отсутствует.
Этап 1 целиком не закрыт; реплики frontend, API и workers остаются этапами 2–5
[плана](../plans/2026-10-03-kubernetes-rollout.md).

Этот отчёт описывает ветку `codex/kubernetes-rollout`: compact baseline
`f551f5f` и последующие незакоммиченные home tools. Код не интегрирован в `main`;
копирование отчёта и runbooks в основной checkout не меняет этот статус.
План и спецификация объединяют результаты ветки с обновлёнными требованиями.
Технические процедуры выполнять из ветки с соответствующим кодом.

## Реализовано

- Helm chart: один application Pod с backend/frontend, один Uvicorn worker,
  Recreate, отдельные PostgreSQL/Qdrant StatefulSets и четыре существующих PVC.
- Независимый `/health/live`, разделение deployment tier и model mode;
  production защитные настройки сохраняются. Отдельный synthetic HTTP/SSE stub
  для DEV/TEST; PROD требует live и запрещает fake embeddings.
- Preflight, операция с сохраняемым lock, остановка старых writers,
  согласованный backup, миграционный Job и запуск приложения после успеха.
- Restore в другой namespace с новыми PVC, проверками checksums,
  application/storage images, model identity, counts и содержимого файлов.
  Helm uninstall сохраняет внешние PVC; резервные копии находятся вне kind.
- Необязательные Gateway/VSO templates, NetworkPolicy, build args для зеркал
  базовых образов и BuildKit mounts для package credentials/публичного CA.
- Отдельный GitHub kind workflow и GitLab integration template с immutable
  release identity и обязательными платформенными adapters.

Локальные start-all скрипты и существующие Compose bundled/external сохранены.
Первая compact-поставка закоммичена отдельным запросом 03.10.2026.
Дополнительные home HTTPS/OIDC/CNI инструменты подготовлены в том же отдельном
worktree; новый коммит и публикация этого продолжения не выполнялись.
Документация и примеры не содержат реквизитов целевых сред.

## Ресурсы домашнего стенда

VM проверена через Proxmox и SSH. Для минимального прогона выделены 4 vCPU,
6 ГиБ RAM, диск около 50 ГиБ. Из-за ограниченной памяти хоста существующий
тестовый стек штатно остановлен с сохранением данных; VM стенда включена.
Модельный сервис не менялся. Дополнительно установлены home Gateway/IdP и
второй отдельный kind с Cilium. Во время одновременной приёмки трёх synthetic
compact-копий оставалось около 1,3 ГиБ доступной RAM и 16 ГиБ диска.
Это измерение малого corpus, а не sizing для обработки реальных больших документов.
После приёмки Cilium-копии сохранён проверенный backup вне kind, затем
её node-контейнер остановлен для экономии памяти. Основной cluster и PVC
обоих кластеров сохранены; доступная память выросла до 2,7 ГиБ.

Фактически использованы Docker 29.8.1, kind 0.33.0, Helm 4.3.0 и Kubernetes
1.37.0; один control-plane node. Это проверенная домашняя конфигурация,
а не подтверждение совместимости с неизвестной целевой версией.
Удаление kind уничтожит его local storage: сохранять backup снаружи обязательно.

## Фактически выполненные проверки

| Проверка | Результат |
|---|---|
| Новые отрицательные проверки до реализации | Ожидаемое падение новых assertions подтверждено |
| Фокусные backend tests: health/profile/stub/chart/lifecycle/model marker | 78 passed; одно предупреждение dependency deprecation |
| Helm lint для home/dev/test/prod с синтетическими references/digests | Passed; целевые CRD/admission этим не проверяются |
| Release identity → image overlay | Passed; подмена chart отклоняется по SHA-256 |
| Shell syntax и CI YAML parse | Passed; не заменяют реальный pipeline |
| Backend Ruff | Passed |
| Frontend node tests | 371 passed, 1 skipped из 372 |
| Frontend ESLint | 0 errors, 40 существующих warnings |
| Существующая Compose backup shell regression | Passed; полный Compose запуск в этом прогоне не выполнялся |
| Чистая установка с Alembic и bundled storage | Passed; application 2/2 Ready |
| Синтетический DOCX upload → stub generation → index | Passed; источник и концепты присутствуют, problem отсутствует |
| BM25 и source inventory | Passed на непустом синтетическом corpus |
| Chat stream через standalone frontend | Passed; настоящий LLM client потребляет upstream SSE от stub |
| Restart и управляемое update с backup | Passed |
| Backup → новый namespace restore | Passed; strict counts/content, источники и функциональный smoke |
| Helm uninstall восстановленного release | Passed; все четыре PVC остались |
| Тестовый CA: baseline без доверия | Python и Node отвергают сертификат |
| Тестовый CA: build/runtime с доверием | Python HTTPS и Node HTTPS passed без отключения verify |
| Финальные штатные образы без дополнительного CA | Passed; обновление с backup и повторный DOCX/BM25/chat smoke прошли |
| Общий `node scripts/check-project.mjs` | Failed на npm audit: 5 high в существующих frontend зависимостях |
| Домашний kgateway и ListenerSet v1 | Passed; Gateway и listeners Accepted/Programmed, ClusterIP |
| OIDC через HTTPS → frontend → backend → тестовый IdP | Passed; authorization code exchange, четыре роли и отказ без группы |
| Cookie и CSRF через Gateway | Passed; Secure/HttpOnly, отказ при отсутствующем/неверном CSRF |
| HTTPS multipart и доступ viewer | Passed; DOCX >2 MiB, viewer не может загрузить; generation/sources/BM25/chat |
| Задержанный поток и отмена | Passed; задержка модели 25 s, ping, tracked cancel и stopped history после окна задержки |
| Logout приложения и IdP | Passed; точный post-logout URI, новая форма ввода credentials, corpus HTTP 401 |
| NetworkPolicy на Cilium | Passed; положительные endpoint/DNS controls и запрет outsider ingress, прямого Gateway → backend и внешнего app/stub egress |

Общий audit выявил цепочку braces → micromatch → fast-glob → Next ESLint
с GHSA-vfj7-8cjw-p6xm в существующем package-lock. Автоматическая рекомендация
force downgrade ESLint-конфигурации не применялась: она меняет текущую совместимость
с Next. Это открытый общий gate, а не успешный CI. Полный backend pytest/coverage,
реальный GitHub CI и GitLab pipeline в этом прогоне не выполнялись.

Повторная проверка registry и [официального advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm)
03.10.2026 подтвердила: latest braces — 3.0.3, опубликованной исправленной версии
нет; актуальный Next ESLint продолжает использовать эту зависимость. Audit
не исключён и порог не снижен. Обновление dependency gate ожидает совместимого исправления.

## Дополнительная домашняя приёмка доступа

Развёрнуты отдельные synthetic realm и compact release с production auth:
Gateway API 1.6.2, kgateway 2.4.5, Keycloak 26.8.0. Cluster-local DNS имена
и приватный семидневный CA не меняют домашние DNS/VPN/доверие Windows.
HTTP probe выполняет реальный кодовый OIDC обмен; browser UI им не проверяется.
Возможности ролей проверены назначением всех четырёх ролей и запретом upload
viewer, а не полной матрицей всех действий каждой роли.

Для tracked-chat теста stub дополнен `/props`, `/apply-template`, `/tokenize`.
Приближённый синтетический счётчик явно помечен test-only; profile `local_qwen`
задан только домашней копии. Точность настоящего токенизатора не проверялась,
production guard не ослаблялся. Реальные отрицательные прогоны выявили два
дефекта теста: TLS negative control использовал доверенный system bundle, и
cookie parser приводил HttpOnly к нижнему регистру. Проверки исправлены,
полный HTTPS/OIDC/25 s stream/cancel/logout прогон повторён успешно.

После единственного финального ревью исправлены идентичность CA-образа,
oracle завершённого IdP logout и отрицательный TLS oracle. Тег образа зависит
от SHA-256 публичного CA; ошибки DNS/refusal не засчитываются как проверка
сертификата. Отрицательные тесты сначала дали 6 отказов, после исправлений —
14 успешных тестов. Полный HTTPS-прогон с задержкой повторён на исправленном коде;
stub возвращён в обычный режим. Update выполнен с проверенным backup.

В продолжении выполнено 93 связанных unit/Helm/HTTP contract tests без пропусков,
с одним существующим предупреждением Starlette. Новый код прошёл Ruff.
Процедуры: [HTTPS/OIDC](../../KUBERNETES_HOME_ACCESS.md),
[enforcing CNI](../../KUBERNETES_NETWORK_TESTING.md).

Отдельный kind Kubernetes 1.36.4 с Cilium 1.20.2 прошёл штатную установку,
fixture, DOCX/BM25/frontend chat smoke и реальные allowed/denied TCP probes.
Все запрещённые адресаты предварительно подтверждены живыми положительными
controls; отказ засчитывался только как timeout. Первый storage install
остановился с сохранённым lock из-за временных image aliases в CRI; конкретные
ошибочные записи удалены на новой node, канонический PostgreSQL digest и PVC
сохранены. После штатного recovery установка повторена успешно.

## Наблюдавшиеся сбои и исправления

При первом запуске Qdrant не мог писать в рабочий каталог непривилегированным
пользователем. Рабочий каталог перенесён на PVC, пути binary/config/storage/snapshot
заданы явно; последующий запуск Ready прошёл.

Первый guard миграции ошибочно считал пустые каталоги, созданные Settings,
существующим corpus. Отказ произошёл в настоящем Job: приложение не стартовало,
lock сохранился. Проверка исправлена на наличие файлов, выполнены recovery и
новая чистая установка. Это проверка отказа guard, а не намеренно повреждённой SQL-схемы.

Независимое ревью обнаружило четыре дефекта. Исправлены и подтверждены
отрицательными тестами: recovery не снимает lock у pending Job; обновление
не меняет PVC bindings; marker учитывает embedding provider/model и collection;
restore проверяет storage images. Итоговый домашний цикл повторён после исправлений.

Дополнительная CA-проверка выявила недоступный Node-пользователю сертификат,
скопированный из BuildKit mount. Для публичного CA установлены права чтения;
cache обеих стадий разделён аргументом SHA-256 `CA_BUNDLE_REVISION`, а runner
проверяет SHA-256 CA или отсутствие дополнительного доверия для default сборки.
Повторная отрицательная
и положительная TLS-проверка прошла. Ключ одноразового тестового сертификата
удалён вместе с временным сервером и сетью.

## Локальная проверка operator tools

В следующем продолжении задачи 7 реализованы воспроизводимый
`operator-tools.tar.gz`, `operator_tools` release identity и отдельный
consumer-шаблон GitLab. Доверенный bootstrap проверяет app revision,
schema/runtime, toolbox digest, archive/chart hashes и точный состав файлов
до извлечения и запуска lifecycle. Secret values и домашние инструменты в архив
не включаются; backend операции выполняются из соответствующего image.

Новые 21 проверки прошли. Subprocess bootstrap и извлечённый overlay исполняются
из пустого deploy-каталога без checkout исходников. Отрицательные сценарии
подмены, несовместимого schema, отсутствующего runtime и unsafe tar проходят.
Полный связанный набор: **114 passed**, без пропусков, одно предупреждение
Starlette deprecation. Helm и kubectl присутствовали в runtime. Ruff для нового
контракта прошёл. Никаких новых действий в домашней инфраструктуре не выполнялось.

Локальный архив и синтетический release не доказывают настоящую публикацию,
registry authorization, настройку runner/toolbox или GitLab CI Lint. Эти gates
сохраняются. Описание: `deploy/ci/operator-tools/README.md`.

Одно независимое итоговое ревью новых operator-tools изменений: Critical 0,
Important 0, Minor 0. Ревью было статическим, без повторного запуска тестов или
инфраструктуры. Дополнительно lifecycle/smoke CLI из извлечённого комплекта
успешно импортируются и выводят `--help` без исходного checkout. Семантика
реальных external jobs и подлинность защищённой поставки остаются за пределами
этого локального подтверждения.

## Общий контракт tracked-чата и token budget

Следующее продолжение задачи 2 выполнялось только локально в Kubernetes-ветке.
Уточнена область: здесь общее переносимое решение, а фактические platform
references, credentials и поставка в конкретный GitLab/кластер находятся
в отдельной окруженческой копии. Наличие доступа не означает разрешение
подключать окруженческие реквизиты к общему проекту.

В stub chart явно задан `LLM_PROFILE=local_qwen` вместе с закреплёнными
test endpoint/model. `ChatTokenBudget` отклоняет неверные формы window/template/
token IDs и marked synthetic ответы вне non-PROD `MODEL_MODE=stub`.
Сохранены output reserve + 256, старый live transport и отказ неподдерживаемого
профиля. Stub дополнен synthetic multi-batch map/synthesis и глобальными
цитатами при пропуске слишком большого источника. Общий контракт описан в
[KUBERNETES_MODEL_PROFILES.md](../../KUBERNETES_MODEL_PROFILES.md).

`backend/tests/test_kubernetes_chat_modes.py` использует настоящий budget и
`LLMClient` через HTTP/SSE. Retrieval и authenticated identity — изолированные
fixtures, поэтому результат не является приёмкой SSO/Qdrant/Gateway или live
качества. HOME/CI/DEV/TEST проверены для `documents/fast/full`; missing tokenizer,
unsupported profile и PROD live против synthetic endpoints дают отказ до
completion. Полный multi-batch ответ проверяет цитаты; отмена с поздним ответом
сохраняет stopped history и не выдаёт authoritative result.

После ожидаемых RED проверок — **46 passed** в первом целевом наборе. Затем
добавлена проверка пропуска oversized first source: первоначальный 422
`chat_evidence_invalid` устранён без изменения production validation. Общий
Kubernetes + chat regression набор: **203 passed**, без пропусков, одно
предупреждение Starlette deprecation. Backend Ruff выбранной области прошёл.
Инфраструктура и окруженческие приложения не изменялись.

Дополнительный отдельный набор selected sources / stream / stream diagnostics:
**33 passed**, без пропусков. Совокупно два непересекающихся набора дают
**236 passed**. Одно независимое итоговое read-only ревью новой области задачи 2:
Critical 0 / Important 0 / Minor 0. Проверяющий не повторял suites; фактические
SSO/Gateway/Qdrant, запуск workflow и точность live tokenizer в review не
оценивались. Новых замечаний для отложенного исправления нет.

## Непроверенные gates и следующий шаг

После сверки обновлённого плана дополнительно зафиксированы открытые gates:

- Задачи 5а/7а: public/confidential OIDC, userinfo groups, audit DB/stdout,
  trusted IP и применимые внешние согласования; home confidential realm их не закрывает.
- Публикация immutable operator tools/toolbox и доставка через настоящий
  deploy-проект; локальные manifest/schema/checksum и запуск без исходников
  подтверждены, но реальные platform adapters/CI Lint ещё не проверены.
- Реальный model-matched budget выбранных live профилей в окруженческой копии;
  локальная synthetic матрица `documents/fast/full` подтверждена, но точность
  настоящего tokenizer и возможности неизвестного provider не проверены.
- Внешний completed backup и `restore_without_source_vm` с независимым target
  и защищённым источником секретов; backup на диске исходной VM этого не доказывает.
- Для будущих этапов 3–4: fencing с первым worker, versioned queue payload,
  совместимость API/worker/DB/config schema при upgrade и rollback непустой очереди.

Уточнение этих критериев не сопровождалось новыми runtime тестами или
инфраструктурными действиями. Успешные результаты выше остаются результатами
прежней домашней приёмки, а новые gates — требованиями дальнейшей работы.

- CSI/StorageClass, admission, quota, registry/mirrors и фактические CRD версии
  DEV/TEST/PROD; нужны защищённые kubeconfig/values и ссылки на подготовленные ресурсы.
- Фактический IdP целевой платформы, полный browser UI и матрица действий ролей;
  renew сертификатов, предельные длительности потока и multipart upload.
- VSO синхронизация, соединения и политики на фактическом CNI целевых кластеров,
  подключение платформенного мониторинга и журналов.
- XLSX/PDF/MSG через полный UI, экспорт/download, trash и admin diagnostics;
  interruption generation/export/download, rollback и измеренные RPO/RTO.
- Продвижение RC через реальный security pipeline и live модели PROD,
  dense retrieval, размерность векторов и качество цитирования.
- Отказы отдельных узлов и несколько реплик: одна VM этого не доказывает.

Доступы уже существуют; здесь развивается общее решение. В отдельной
окруженческой копии следующий шаг — preflight и установка в DEV, затем TEST
с восстановлением и только после приёмки PROD live smoke.
Подробные процедуры: [развёртывание](../../KUBERNETES_DEPLOYMENT.md) и
[тестирование](../../KUBERNETES_TESTING.md).

## Переносимый operator toolbox — 04.10.2026

Добавлен общий Dockerfile с allowlist build context, trusted bootstrap,
Python base по digest, PyYAML 6.0.3, Helm v4.3.0 и kubectl v1.37.0.
Checksum обоих бинарников и их реальные версии проверяются до установки;
HTTPS redirect, archive members, public CA revision и секреты сборки ограничены.
Runtime UID/GID 1000, без установки зависимостей при старте.

На локальном Docker выполнена Linux/amd64 сборка по официальным binary SHA.
Offline smoke: только synthetic release/archive/chart mounts, read-only root,
без сети/source checkout; bootstrap, overlay и lifecycle/smoke help успешны.
Все пять отказов (schema, archive, toolbox identity, PyYAML, binaries) не создали
destination. Related toolbox/release/chart/lifecycle suite: 76 passed, 0 skipped;
Ruff для нового Python кода пройден. Job `operator-toolbox` добавлен в GitHub CI,
но сам workflow целиком не запускался. Это проверка локального image ID, а не
публикация и приёмка registry manifest digest. Целевые кластеры/GitLab/Proxmox
не изменялись; полный audit gate остаётся отдельным требованием.

Независимое ревью: Critical 0, Important 1, Minor 0. Important исправлен одним
TDD проходом: temporary incoming 0700 с host UID, отличным от 1000, не читался
consumer на Linux. Для трёх synthetic файлов заданы 0644, для каталога 0755.
Unit RED → GREEN и настоящая Linux permission probe подтвердили исходный отказ
и успешное чтение после исправления; consumer повторно прошёл все сценарии.
Дополнительных незакрытых замечаний этого scope нет. Reviewer не повторял
Docker/network/CI проверки и не оценивал provenance/целевую платформу или ранее
реализованный runtime вне новых точек интеграции; эти ограничения сохранены.


### Ресурсы домашнего стенда — 07.10.2026

По поручению пользователя память выделенной VM увеличена с6 до10 ГиБ,
диск с50 до100 ГиБ. Перед изменением повторно подтверждены VM/Guest Agent/SSH
идентичность и свободные ресурсы гипервизора. Раздел/ext4 расширены онлайн,
память применена через штатную остановку и запуск без force.
Оба прежних kind node container сохранили идентичность и запущены;
PVC UID/volume/phase сохранены, оба Kubernetes Node Ready.
После запуска доступно5030 МиБ RAM и57.62 ГиБ диска. Автостарт VM включён.
Приложения/образы не обновлялись; этот ресурсный шаг не является новой
функциональной или отказоустойчивой приёмкой приложения.


Дополнительная проверка после расширения обнаружила насыщение системного
`fs.inotify.max_user_instances`:128 из128 заняты; kube-proxy отказал с
`fsnotify watcher init: too many open files`. Адреса node/API/service совпали,
смена адресов исключена. Лимит увеличен до512 и сохранён в отдельном sysctl.d
файле домашней VM; остальные inotify limits не менялись. Pod/PVC не удалялись.
Готовность приложений после автоматических повторов проверяется отдельно.


Повторная проверка готовности после выделения ресурсов завершена: основной
kind-кластер имеет20 из20 активных Deployment/StatefulSet Ready, второй —7 из7.
Kube-proxy и local-path-provisioner восстановились после штатных повторов.
Предшествующая ошибка Qdrant во втором кластере была связана с отсутствующим
containerd image alias: восстановлена только ссылка на уже сохранённый образ
с тем же pinned digest. Образ не скачивался и версия не менялась. Node container
и PVC идентичности сохранены; Pod/PVC не удалялись. Эта готовность не заменяет
функциональную проверку UI/login, backup/restore или отказоустойчивости.
