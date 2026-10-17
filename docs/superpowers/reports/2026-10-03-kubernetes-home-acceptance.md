# Домашняя приёмка первой Kubernetes-поставки

> Текущий объём и результат 10.10.2026 описаны в разделе «Компактный домашний этап — 10.10.2026» в конце отчёта. Ранние OPEN/FAIL и private-кандидаты сохраняются как история, не являются критериями этой поставки.

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


### Домашний стенд: ресурсы и producer (08.10.2026)

VM:8 vCPU,10 ГиБ RAM, диск100 ГиБ, автостарт включён. CPU изменён обычной
остановкой/запуском; сохранены UID узлов, контейнеры двух kind-кластеров и PVC/PV
bindings. До повтора: свободно4,35 CPU по requests, около4,5 ГиБ RAM и57 ГиБ
диска, Pending-подов нет. Первый FAIL900с сохранён: writer был Unschedulable
с Insufficient cpu до выполнения действий; штатная очистка подтверждена.

Отдельный повтор на точном компоненте475 прошёл за632.75с при прежних900с:
full native export/completion/retirement/publication смешанного корпуса.
Namespace, PVC/PV и wrapper удалены штатно; отсутствие проверено отдельно,
одноразовый kubeconfig удалён. UID/replicas действующих приложений и UID/PV
существующих PVC сохранены. Это model-free producer proof; UI, LLM/embedding,
restore и физическое отключение питания этим прогоном не проверены.


### Домашний полный контроллер 553 и оставшаяся интеграция

На домашней VM (8 CPU, 10 ГиБ RAM, диск 100 ГиБ) единая нативная цепочка 553 прошла исходный gate: wrapper 660,36 / 900 с, контроллер 589,65 с, 10309 native чтений. Настоящие условные stop/start, source SQL/retirement, публикация архива, reader SQL/Foreground retirement, выбранные backend/frontend Ready и backend SQL/HTTP, completed и единственный Orphan unlock выполнены в одном контроллере. Данные стенда: 2 документа, 4 чанка, 4 концепта, 8 точек Qdrant; вызовов моделей нет. Это функциональная проверка на малом корпусе, не оценка production нагрузки.

Перед запуском проверены существующие frontend root/platform/config и все 12 слоёв на узле; добавлена только отсутствующая закреплённая canonical CRI ссылка без импорта или перезаписи других ссылок. Все прежние image targets/labels, UID/replicas приложений и PVC сохранены. После теста namespace/PVC/PV, wrapper и owned handoff-том штатно удалены, временный kubeconfig удалён; новым независимым чтением подтверждено отсутствие namespace/wrapper/тома/kubeconfig. Source/tools и архив/журналы/storage UID сохранялись до штатной очистки.

С настоящим общим backend conftest дополнительно прошли 228 проверок на отдельном полном snapshot 1742: новые storage/reader/pipeline и прежние TLS/CA/checkpoint/probe/schema сценарии. В тесте отсутствия новых файлов сравнивается полный набор уже существовавших путей, включая вложенные, вместо предположения пустого tmp_path. Это focused проверка, не полный backend gate и не 180-секундный platform gate. Полный Linux backend набор с production dependencies превысил исходный safety limit 7200 с (FAILED); позднее завершение используется только для диагностики. Прежний общий Windows422 FAILED и ограничения Windows symlink сохраняются.

Установленный Helm, независимое защищённое происхождение, обычный backup CLI и полные T3/T4 остаются открытыми. Следующий интеграционный этап должен читать настоящее Helm поколение, проверять его against текущих полных native templates и отдельно получать genuine ownership ACK до первых эффектов. Текущие byte integrity результаты не подтверждают publisher/restore permission. Рабочий runtime сохраняет принятый набор 475; клиентские credentials или изменения внешних кластеров для этих проверок не использовались.


### Проверка установленного поколения: состояние на 08.10.2026

Первый кандидат 562 не принят: Windows с прежним полным набором 136 модулей и дополнительной проверкой loader превысил исходные 180 с (180,02 с, FAILED). Полный Linux backend snapshot 1742 превысил первоначальный safety limit 7200 с; исходный gate FAILED, позднее завершение оставлено только для диагностики. Прежние FAILED не заменяются частичными результатами.

Независимое целостное ревью обнаружило четыре Important: устаревший остаток deadline после последнего полного fence; выдача первого source receipt из GET после потерянного CREATE ACK; публичный отпечаток потенциально приватных values; неполная проверка native reader CAS ACK. Подготовлены исправления и причинные RED-проверки; связанный focused набор прошёл 626 проверок. Повторное ревью дополнительно проверяет типы ownerReferences и все точки выдачи genuine ACK receipts. Полные новые Windows/Linux gates, actual Helm/native и домашняя цепочка ещё не приняты.

Проверка защищённых values сохраняет точное сравнение файлов, дескрипторов и байтов только в памяти; публичный values_sha256 установлен в null. Потерянный или неподтверждённый ответ оставляет запись и lock для пассивного наблюдения, без повторного эффекта. Исходные deadlines и проверки namespace, UID/RV, TLS, HBA, Pod/PVC/PV и writer policy сохраняются. Рабочий runtime остаётся на 475; обычный TLS backup CLI, archive origin, restore authorization и полный T3/T4 остаются открытыми.


### Кандидат 562: единая проверка ACK и актуальные платформенные результаты

Независимое ревью точного нового кандидата: Critical0/Important0/Minor0; R1–R5 закрыты, 22 причинные проверки passed. Owning набор — 585 passed. Все нативные ConfigMap-подтверждения проверяют типизированные поля, UID и новый RV; свежий финальный кадр должен совпадать. Неизвестный ответ не даёт разрешения на повтор эффекта. Публичный fingerprint приватного values отсутствует; точное сравнение bytes/descriptor выполняется только в памяти.

Linux: 4048 passed / 5 skipped, 137 явных модулей плюс loader; wrapper 167,59 / 180 с, source guard и обычная очистка собственного контейнера/тома подтверждены. Windows: полный непересекающийся набор 136 модулей плюс loader выполнен в двух процессах с отдельными временными каталогами, 4014 собранных cases, wrapper 122,69 / 180 с; оба source guards и неизменность инструментов подтверждены. Старые однопроцессные Windows FAILED180,02 и FAILED180,01 сохранены. Двухпроцессная организация проверяет полный набор; результат однопроцессной производительности остаётся FAILED.

Старый полный Linux1742 поздно завершился: 84 failed / 9629 passed / 34 skipped, 11425,21 с. Исходный safety7200 FAILED не изменён. Собственные контейнер и три тома штатно удалены, независимое отсутствие подтверждено. Причина ошибок spawn доказана: незащищённый тестовый main повторно копировал Helm в дочернем процессе, получая PermissionError. Новый guarded-main стенд на полном snapshot1751 прошёл 3 реальные spawn-проверки за18,09 с (wrapper21,89), с фактическим общим conftest; код парсера не изменён.

Профилирование ещё 11 настоящих citation-тестов с общим conftest: passed; SQLite create_all занял12,738 с, execute12,244 с, около1,2 с на новую схему. Полный новый backend-набор пока не запускался. Следующий узкий этап: проверить атомарное создание отсутствующей SQLite-схемы одной транзакцией, включая rollback при ошибке и сохранность уже существующих таблиц; отдельная БД каждого теста, PostgreSQL, лимиты и свежие проверки сохраняются. После этого — общий backend gate и настоящие installed-entry native/home сценарии.

Рабочий runtime остаётся475; новый кандидат562 не интегрирован. Обычный CLI, защищённое происхождение, restore и полные T3/T4 остаются OPEN. Этот раздел уточняет состояние предыдущих записей, не заменяя исторические FAILED.


### Полный backend и следующий installed Helm proof — 08.10.2026

Полный неизменяемый Linux-набор backend1752 прошёл: **10054 PASS, 34 SKIP**, 142 предупреждения; pytest1922,59 с, wrapper1932,58 с при исходных7200 с,2Gi/2CPU. Использован настоящий общий conftest, свежая отдельная SQLite БД каждого теста, реальные spawn-процессы parser supervisor и прежний pinned backend image. Контейнер и три собственных тома штатно удалены, независимая проверка отсутствия и source/tools SHA PASS. Исторические full1742/Windows422 FAILED сохраняются; Linux PASS не заменяет полный Windows gate или hosted CI.

SQLite init_db создаёт отсутствующие таблицы одной реальной транзакцией: при ошибке новые таблицы откатываются, существующие таблицы/строки сохраняются; повтор идемпотентен. PostgreSQL path не менялся. Независимое ревью:0Critical/0Important/0Minor;5 owning Windows и16 focused Linux PASS. На настоящих citation fixtures время setup снизилось с≈1,2 до≈0,05–0,06 с без кеширования схемы или переиспользования тестовой БД. Изменение пока находится в проверенном кандидате, рабочий runtime475 не обновлён.

Следующий native proof использует настоящий общий chart, Helm install storage→upgrade application и genuine begin_backup_installed_operation ACK перед первым эффектом; затем полный backup/reader/start/completed/unlock. Исходные900 с и ресурсы сохраняются. До запуска найден и воспроизведён один Important в R2-сценарии: сырой container state и произвольный seed JSON попадали в диагностику. R2 не запускался. Отдельный R3 выводит только фиксированные состояния, флаги и проверенные счётчики; неизвестные/неполные записи отклоняются.10 owning tests и фактический синтетический seed protocol PASS; независимое ревью R3 ещё выполняется. Это исправление приёмочного сценария, не изменение рабочего приложения.

Домашняя VM имеет8vCPU/10Gi RAM/100Gi disk и включённый автостарт; при свежей проверке доступно≈4,3Gi RAM и57Gi disk. Оба существующих кластера и приложения сохранены. Native/home installed-entry, защищённое происхождение, ordinary CLI, restore, полные T3/T4 и release acceptance остаются открытыми.


### Installed Helm: внешний CA и контрольные проверки — 08.10.2026

Фактический R3 native остановился до seed: production-конфигурация по умолчанию запрещает отключённую аутентификацию. В отдельной синтетической проверке отказ воспроизведён; корректный confidential OIDC и HTTPS проходят Settings без ослабления production guards. R4 с этой конфигурацией успешно создал тестовые данные и запустил приложение через общий Helm chart, но отказался на genuine installed-entry factory до первого backup-эффекта. Точная причина этого фактического отказа пока не подтверждена. Оба FAILED сохранены; собственные namespace/PV, контейнеры и тома штатно удалены.

Независимо воспроизведён дефект проверки внешнего HTTP CA: при trust.existingConfigMap общий chart ссылается на существующий immutable ConfigMap и не включает его в Helm manifest. Проверка ошибочно требовала встроенный объект. Новый кандидат сохраняет обязательные свежие проверки выбранного внешнего CA, PEM, размера, UID/RV, immutable и ссылок frontend; встроенный CA при наличии продолжает сравниваться. Изменены только runtime-проверка и её тесты; 46 owning tests PASS, независимое ревью 0 Critical / 0 Important / 0 Minor. Рабочий runtime475 не обновлён.

Для этого точного кандидата Windows: 4007 PASS / 19 SKIP, два процесса, полный непересекающийся набор, wrapper130,86 / 180 с. Linux R4: 4060 PASS / 5 SKIP, но wrapper195,38 / 180 с — временной gate FAILED, результат сохранён. Один контрольный Linux R5 выполнен без параллельной тестовой нагрузки с теми же исходниками, инструментами, всеми 137 модулями и исходными512Mi/1CPU/180 с: 4060 PASS / 5 SKIP, pytest166,50 с, wrapper169,19 с; source guard и штатное удаление контейнера/тома подтверждены. Это успешный контрольный прогон, а не доказательство причины прежнего замедления.

Следующий R6 использует тот же проверенный сценарий с ограниченной диагностикой только координат исходников, без locals, сообщений исключений и приватных значений. До выполнения требуется независимая проверка coordinator и свежая готовность локального кластера. Домашний сценарий дополнительно требует фактический R6 PASS, сохраняет все существующие PVC UID/PV bindings и replicas; временный kubeconfig удаляется только после точной проверки собственной файловой идентичности и подтверждения отсутствующего/завершённого тестового контейнера. Неизвестное или живое состояние сохраняет credential и завершает очистку отказом. Домашний запуск ещё не выполнялся. Защищённое происхождение, ordinary CLI, restore, полный T3/T4 и release acceptance остаются OPEN.


## Компактный домашний этап — 10.10.2026

Пользователь изменил обязательный результат: **рабочий домашний Kubernetes-стенд и комплект для переноса; приёмка PROD отдельно**. [Новый план](../plans/2026-10-03-kubernetes-rollout.md) заменяет прежнюю очередь. Исторические OPEN/FAIL, private582 и более широкие release gates выше сохранены; эти кандидаты не приняты этим прогоном.

Применён standalone operator snapshot домашнего набора 04.10, chart 0.1.0 с baseline `f551f5f` и актуальными home helpers. Snapshot не равен текущему HEAD. SHA-256 каждого поставляемого файла и OCI image digests зафиксированы в `manifest.json`. Текущий большой lifecycle в Kubernetes worktree и изменения других задач не заменялись. Две необходимые правки ограничены kit: раздельные single-host TLS leaf IdP/приложения и запрет automatic restore CLI/library до cluster calls. Зависимость kit закреплена на фактически использованном PyYAML 6.0.1.

| Результат | Фактическая проверка |
|---|---|
| Чистая домашняя копия | Отдельные новые namespaces/PVC, bundled PG/Qdrant, миграция перед первым стартом, application 2/2 Ready; измерены ресурсы VM (около 10 GiB RAM, 56 GiB свободного диска перед прогоном). Существующие установки сохранены. |
| HTTPS/OIDC и права | Нативный probe: trust/negative trust, redirect, anonymous denial, code flow, четыре роли и unmapped denial, cookies/CSRF, upload, generation, sources/BM25, chat stream, RP/IdP logout — PASS. |
| Браузер | Изолированный Chromium с default HTTP/2 и установленным публичным CA, TLS verification включена: вход → UI upload DOCX → «Готов» → поиск документа → содержимое → быстрый stub-ответ → открытие источника — PASS. Домашние DNS/VPN/Windows trust не менялись. |
| Пересоздание application Pod | Rollout restart/status — PASS; прежние PVC сохранились. Контрольный документ затем прошёл content/integrity backup и восстановление. |
| Одно обновление | Frontend `kube-smoke` → `audit-v3`, backend `audit-v16-cleanup-budget` сохранён; schema, число документов и APP_SECRET_KEY не изменились. Длительность deploy 83.17 с. |
| Отказ миграции | Настоящий одноразовый Job с exit 42 через обычную deploy/migrate последовательность: Job Failed, приложение остановлено, lock сохранён, schema/документы неизменны. Собственный terminal Job и lock разобраны вручную; обычный повторный deploy прошёл. |
| Непустой backup | Format 2: PG/Qdrant/files/totals/release/trust; strict content integrity и checksums — PASS. Источник оставлен остановленным, копия сохранена вне VM на Windows. SHA-256 закрытого архива `5d17517a9ab054d07255242ada122efb962c09502a269960de40a4ae93e5277d`. |
| Restore | Новый namespace, новые четыре PVC, прежний image/model/storage/public trust; PG single transaction, Qdrant/files, точное равенство totals, strict verify-content; outbox hold, новая key и очистка auth_sessions/oidc_login_flows до первого старта. 82.75 с для импорта/проверки/migrate/start. Application 2/2 Ready, новый login, поиск/источник/chat — PASS. Исходные PVC/копия сохранены. |
| Перенос образов | Шесть images экспортированы в OCI; archive SHA-256 `63508cacbffa83b84743742e7f07fe24445112f147c02200cad85d5a66a73575` совпадает на VM и Windows. `docker load` — PASS; импорт в image cache второго kind через `ctr --platform linux/amd64` — PASS для всех шести refs. Workloads второго kind не менялись. |
| Автономные проверки kit | 31 helper tests — PASS (включая TLS SAN isolation, CLI restore early refusal, публичный CA fixture). Full backend/CI suite не запускалась. Helm lint/render выбранного профиля проверены отдельно. |

Браузер сначала воспроизвёл auth-navigation 404 с общим двуххостовым сертификатом. HTTP/1 Chromium control прошёл; после отдельного single-SAN leaf для каждого hostname обычный HTTP/2 Chromium прошёл полный UI сценарий. Это минимальная правка фактического отказа; TLS verification не отключалась. NetworkPolicy enforcement этим прогоном не подтверждён.

Restore test helper сначала остановился **до импорта** из-за bytes/string assertion. После исправления проверены пустая БД, точные claims, Ready node и отсутствие application/operation actors; известный собственный pre-import lock освобождён явно. Это разбор одного наблюдаемого случая, не automatic recovery. Default `kind load image-archive` попытался импортировать все платформы и отказал на отсутствующем non-amd64 blob; документирован и проверен явный amd64 import. Обе неудачные попытки сохранены в закрытых evidence logs.

Восстановленная копия работает; source application оставлен остановленным после final backup. Это не пользовательский локальный Windows стек и не прежние домашние установки. Сертификаты синтетического стенда действуют семь дней. Публичный kit не содержит backup, паролей, CA private key, credentials или production endpoints.

[Один путь установки и обслуживания](../../KUBERNETES_COMPACT_HOME.md), [ручной restore](../../KUBERNETES_COMPACT_RESTORE.md). Локальные артефакты этой приёмки находятся в игнорируемом `.superpowers/sdd/2026-10-03-kubernetes-rollout/minimal-20261010/`: `package/manifest.json`, `package/SHA256SUMS`, `compact-home-kit-20261010.tar.gz`, закрытый `backup-off-vm.tar.gz` и evidence. Manifest/архив kit можно передавать; backup передавать только как отдельно защищённые данные.

Ограничения: stub вместо реальных LLM/embeddings; replay активной восстановленной cookie не проверен, поскольку в backup обе auth-таблицы были пустыми (очистка и свежий ключ до старта/новый login проверены). Измеренные интервалы не являются SLA/полным RTO. Кластерные prerequisites Gateway API/kgateway установлены заранее и не входят в images archive; полный air-gap/clean-cluster bootstrap не принят. Обезличенные manual cells объединяют фактически проверенные шаги, целиком на третьем target не запускались. PROD, нагрузки, HA, external mode, универсальный crash recovery, platform PKI и реальные модели требуют отдельной приёмки. Интеграция в main, коммит, push и публикация не выполнялись.


Итоговое независимое read-only review: Critical нет. Замечание о python-docx воспроизведено в чистом Windows venv (1 failed / 26 passed); после явного `tests/requirements.txt` — 31 passed / 0 failed. Различие kind contexts переоценено как существенное из-за cluster-scoped prerequisites; regression-проверка сначала FAILED, после единого `kind-home` — PASS. Linux host не имел pytest и venv/ensurepip; этот факт сохранён, зависимости не ставились в системный Python, OS packages не менялись. Exact распакованный kit на Linux прошёл help/lint/render/preflight с PyYAML 6.0.1. Minor отложен: готовая команда интерактивного браузера для оператора не входит в kit; UI smoke фактически выполнен в отдельном Chromium Pod с доверенным CA.


**Этап закрыт.** В публичном archive 33 manifest files плюс `manifest.json` и `SHA256SUMS`; размер 795268113 байт. SHA-256 `compact-home-kit-20261010.tar.gz`: `6313c0a2442c33abfcf28fb48e2406467e0262502fc82c48a57623d1b6a962a8`. Проверены allowlist архива, checksums, относительные ссылки и compile трёх manual Python cells. Private/evidence/backup и исторические tooling/tests в архив не входят. Временные Chromium Pods этой проверки удалены штатно; application восстановленной копии Ready, source остаётся остановленным, node Ready. Четыре задачи нового плана закрыты без открытия следующего этапа.
