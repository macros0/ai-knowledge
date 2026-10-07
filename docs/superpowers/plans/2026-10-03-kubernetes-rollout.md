# Kubernetes Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Делегирование возможно только при отдельном выборе пользователя.

**Goal:** сначала запустить существующее приложение в трёх Kubernetes-средах с внутренними PostgreSQL/Qdrant, затем по отдельным этапам подготовить frontend, API и workers к репликации; каждый этап предварительно проверять дома.

**Architecture:** первая поставка — compact Deployment с одним Pod и двумя контейнерами приложения, отдельные StatefulSets PostgreSQL/Qdrant и постоянные тома. В DEV/TEST используются модельные заглушки, в PROD — реальные API. Далее frontend отделяется от файловой диагностики, фоновая обработка получает долговечную очередь, API и workers масштабируются независимо.

**Tech Stack:** существующие Python/FastAPI, Next.js, PostgreSQL 17 и Qdrant; Helm, Kubernetes, Gateway API/kgateway, Vault Secrets Operator, GitLab/Artifactory и существующий GitHub CI. Домашний первый стенд — kind в Ubuntu VM.

**Spec:** [Архитектура и ограничения](../specs/2026-10-03-kubernetes-deployment-design.md).

**Статус, сверенный 05.10.2026:** compact-реализация подготовлена в ветке `codex/kubernetes-rollout`, базовая поставка — commit `f551f5f`; в `main` она ещё не интегрирована. Дополнительные home, operator tools/toolbox, model-budget, OIDC/audit и dependency изменения пока не закоммичены. Домашние install/update/restart/backup/restore, HTTPS/OIDC и enforcing NetworkPolicy probes пройдены в указанных ниже объёмах; это не закрывает требования задач 5а/7а или платформенную приёмку. Исходный audit file-profile gate: **13 PASS / 3 FAIL / 0 SKIP**. Ранний service/startup guard исправлен в локальном кандидате: исходные16 profiles **14 PASS / 2 FAIL**, расширенная matrix **35 PASS / 2 FAIL / 0 SKIP**; следующий file-material candidate проходит Linux68/68, network24/24, PG31/31, whole-storage2/2 и Windows101PASS/6POSIX SKIP; projected Secret/ACL/file-phase lifecycle/full/home/CI release приёмка открыта. Доступы к целевой платформе уже есть, но здесь выполняется общее решение: окруженческую интеграцию пользователь ведёт в отдельной копии. Этапы 2–5 не реализованы и требуют отдельных подробных планов. [Отчёт приёмки](../reports/2026-10-03-kubernetes-home-acceptance.md).

### Выполненная часть и оставшаяся приёмка

**Уточнения документации 04.10.2026:** [обновлённый список вопросов](../../KUBERNETES_PLATFORM_QUESTIONS.md)
частично раскрывает пункты 1, 3, 4, 6, 10, 12–15. Собственный chart предусмотрен;
обязательный прямой внешний `/api` → backend не установлен. Compact/Next
схема сохраняется предложением, допуск Pod/Jobs и окно обслуживания ещё открыты.
Добавлены проверки namespace `logging.enabled`, collector, доверия PROXY protocol,
component inputs и разделения remote mirrors/release repositories. Пункты
2, 5, 7–9, 11 не закрыты; новые механизмы не следуют автоматически из примеров. OIDC затем реализован
по отдельному утверждённому плану; audit и реплики остаются следующими этапами. Полные ссылки и реквизиты источников остаются вне Git.

Дополнительный [локальный прогон 04.10.2026](../reports/2026-10-04-kubernetes-portable-acceptance.md)
на Docker/kind проверил актуальные незакоммиченные изменения: сборку трёх
образов, минимальный lifecycle с непустым restore и PVC retention; 174
контрактных теста прошли без пропусков. Проверено повторное применение
той же версии, а не upgrade между разными версиями приложения/схемы.
На исходном прогоне npm audit сообщил 5 high в цепочке линтинга. Локальный dependency gate закрыт 05.10.2026 scoped tinyglobby migration; детали и final home proof — в [отчёте](../reports/2026-10-04-audit-outbox-acceptance.md).
Статусы окруженческой приёмки и этапов репликации не меняются.
Последующее [OIDC и release-checks испытание](../reports/2026-10-04-oidc-compatibility-acceptance.md)
подтвердило три актуальных HTTPS профиля, полный partitioned backend набор
3309 PASS / 27 SKIP и отдельное выполнение 11 PG skips. Исторический npm audit
FAIL сохранён; текущий локальный gate PASS, внешний CI/coverage и remaining platform/storage fault cases не закрыты.

| Задача | Подтверждено отчётом 03.10.2026 | Что осталось |
|---|---|---|
| 0 | Обезличенный bundled/stub/live контракт; доступы существуют | В окруженческой копии: quota, CSI/CNI, actual API discovery и владельцы целевых операций |
| 1 | VM 4 vCPU / 6 ГиБ / около 50 ГиБ; Gateway/IdP/HTTPS и отдельный Cilium kind; backup вне kind | Ресурсы для реальной нагрузки, backup вне исходной VM и отдельные VM для отказов |
| 2 | Независимый liveness, профили, HTTP/SSE stub; локальная synthetic матрица documents/fast/full | В окруженческой копии: выбранные DEV/TEST профили и доверенный model/tokenizer контракт PROD |
| 3–4 | Compact chart, bundled StatefulSets, внешние PVC/Secrets, lock/migration Jobs; install/update | Фактические admission/CSI, целевые Secrets и production auth |
| 5 | Home CA/HTTPS/OIDC/CSRF, stream/cancel/logout, enforcing Cilium probes | Целевые маршруты/IdP/CNI, VSO, максимальные upload/stream limits, mirrors и observability |
| 5а | OIDC и durable outbox реализованы в рабочей ветке; выбранные HTTPS/restore, timeout/race и whole PGDATA/WAL/cold-start/maintenance/recovery probes пройдены; [приёмка](../reports/2026-10-04-audit-outbox-acceptance.md) | Linux DNS/multihost/TLS/COMMIT matrix исправлена: 20 PASS/0 SKIP, исходный 3 FAIL/8 сохранён; Task 3a продолжен: Linux24/24 с burst/SIGTERM и native Windows PG35/35; исходный file-profile gate13 PASS/3 FAIL сохранён; candidate service-guard даёт14/2 исходных profiles (расширенная required matrix35/2), file-material repair даёт68/68, FIFO безопасно отказывает; продолжение projected Secret/ACL/file-phase lifecycle3a.5, затем production load/CSI и hosted CI; trusted-IP/live adapters, целевая регистрация, внешний collector/CI и интеграция release snapshot |
| 6 | Непустой согласованный backup/restore, counts/content/BM25, PVC retention | Interruption, rollback/RPO/RTO и восстановление при недоступной исходной VM |
| 7 | Operator archive/bootstrap, consumer template, recipe toolbox и локальный offline container smoke | В окруженческой копии: публикация, общий линтер, реальные pipelines/CI Lint, RC promotion и registry digest |
| 7а | Требования ввода в эксплуатацию внесены в план | Применимость, владельцы и внешние согласования |
| 8 | Домашний synthetic smoke, 93 связанных теста без пропусков | Полный browser flow, DEV → TEST → PROD и live модели |

Все статусы относятся к объёму, указанному в отчёте; неполная задача не отмечается целиком выполненной. Пять high findings исходного dependency audit устранены локальной tinyglobby migration 05.10.2026; прежний FAIL сохранён в истории. Исходный DNS/multihost FAIL сохранён в истории; текущий audit-v7 проходит Linux network/lifecycle 24/24 и native Windows PG 35/35. Полный backend — 3408 PASS/83 SKIP, coverage 88%; новый home upgrade/HTTPS/response→DB→stdout пройден, 102 immutable rows сохранены. Новый home restore audit-v7 подтвердил136 immutable rows/new PVC/keys/no replay и off-VM backups; новая Linux SCRAM/passfile/socket matrix13 PASS/3 FAIL выявила блокирующие file profiles, следующий repair описан в3a.5; production load/CSI и hosted CI остаются открыты, см. [Task 3a audit плана](2026-10-04-audit-outbox.md#task-3a-общий-deadline-postgresql-denial--предлагаемый-bounded-repair). Внешний required CI и повтор всех обязательных checks на фактическом release snapshot остаются открытыми; домашняя приёмка и платформенный линтер их не заменяют. Чекбоксы ниже — оставшиеся и повторяемые критерии поставки, а не инструкция заново создавать уже реализованный код.

### Ближайшая последовательность работ после проверки плана 05.10.2026

1. Закрыть решения файлового слоя и выполнить [Task 3a.5 audit-плана](2026-10-04-audit-outbox.md#3a5-файловый-этап-конфигурацииconnect--следующий-scoped-repair): ранний service отказ, сохранение trust/credentials, единый budget и очистка. Старые network PASS не закрывают новый file-profile FAIL.
2. Подтвердить repair через строгий протокол tests, настоящий HTTP и startup, Linux/Windows и новый image freeze. Затем домашние projected Secret/rotation/HTTPS/restore и полный release gate. Файловые probes с обычным connect или внешним kill ребёнка недостаточны; точные критерии и команды находятся в 3a.5.
3. По этому snapshot отдельно подтвердить required CI и согласовать интеграцию/публикацию. GitLab deploy-копия потребляет те же проверенные digests; наличие workflow в исходниках не доказывает выполнение checks или branch protection. Окруженческие collector/trusted-IP/CSI/load и DEV→TEST→PROD остаются отдельными открытыми критериями.
4. К подробным планам frontend/API/workers replicas переходить после компактной поставки и её эксплуатационной приёмки. Реплики и новая очередь не включаются в file repair; текущий singleton/Compact/Recreate контракт сохраняется.

Это очередность оставшихся работ, а не новый результат тестов. Исторические строки отчётов и их failed evidence сохраняются; неактуальные требования повторно реализовать уже проверенный passfile/Unix socket устранены в audit-плане.

## Global Constraints

- Сохранить локальные `start-all.ps1` / `start-all.sh` и Docker Compose `bundled` / `external`.
- Этап 1: `deployment.mode=compact`, `application.replicas=1`, `UVICORN_WORKERS=1`; HPA отсутствует. Распределённая очередь не блокирует эту поставку.
- DEV, TEST и PROD — независимые кластеры; в каждом собственные PG/Qdrant/PVC, данные, ключи и SSO-конфигурация.
- Во всех трёх средах `ENVIRONMENT=production`. Название среды задаётся отдельно, без ослабления auth/CSRF/TLS.
- Настоящие LLM/embeddings доступны только в PROD. DEV/TEST используют явный stub/fake профиль. Тестовые embeddings, jobs и базы не переносятся в PROD.
- Продвигаются одни и те же application image digests и версия chart; между TEST и PROD нет пересборки. Миграции используют соответствующий backend image.
- Код и целевая доставка находятся в разных репозиториях. Этот checkout хранит переносимые исходники и шаблоны; отдельный deploy-проект потребляет версионированные артефакты и защищённую конфигурацию.
- Проектная policy: сборка `linux/amd64`, разрешённые зеркала и CA; общий линтер блокирует доставку; DEV запускается вручную, TEST/PROD вручную из проверенного release tag. Примеры платформы с `on_success` не отменяют эту policy и не доказывают обязательность manual/CPU architecture во всех проектах. Применимость внешних правил и фактическую архитектуру узлов сверить отдельно.
- Первая поставка поддерживает bundled Kubernetes. Kubernetes external — последующее расширение; Compose external проходит регрессию уже сейчас.
- До разрешения backend replicas нельзя принудительно запускать замену, пока предыдущий процесс может работать. `Recreate` и RWO не подменяют распределённое владение.
- Пользовательские PVC и backup переживают удаление Helm release. Восстановление выполняется в новый namespace/тома/БД, без перезаписи исходной инсталляции.
- Документы и примеры анонимны: никаких названий организаций, внутренних доменов/IP, закрытых ссылок, аккаунтов, токенов или реальных Vault paths. Только технические требования и placeholders.
- При исполнении изменения сетевой модели/секретов/retention отражаются в `SECURITY.md` в том же изменении.
- Текущее уточнение разрешает обновление документов. Ранее разрешённая compact-реализация и домашняя приёмка учтены в статусах; новые auth/audit/delivery/replication требования не объявляются реализованными. Коммит, интеграция ветки и публикация — отдельные действия. Посторонние изменения рабочего дерева сохраняются.

## Review Focus

- Потеря миграционного Job либо повтор pipeline не запускают старый и новый backend одновременно и не повторяют разрушительную операцию: задачи 4 и 6.
- DEV/TEST успешно работают со stub, но PROD случайно получает fake embeddings либо старый тестовый индекс: задачи 2, 3 и 8.
- Работает port-forward, но Gateway обрезает upload/SSE либо не даёт корректный SSO callback: задачи 5 и 8.
- DEV использует public OIDC client, приложение требует secret; группы есть только в userinfo; аудит теряет пользователя/IP или не выходит в stdout: задача 5а.
- CI пропускает общий линтер или считает созданный RC registry доказательством доставки в TEST: задача 7.
- Восстановлен PostgreSQL без соответствующих файлов/Qdrant или потеряны данные после удаления release: задачи 3 и 6.
- Одна домашняя VM проходит smoke, но CSI/CNI/Vault/admission и CI цепочка целевой среды отличаются: задачи 0, 1, 5, 7 и 8.
- Deploy-проект получил chart/images без совместимых operator tools; tracked chat не имеет доверенного token budget; заменённый worker пытается опубликовать старое поколение: задачи 7, 2/8 и этап 3.
- Новая версия worker не читает накопленные jobs либо backup теряется вместе с исходной VM: этапы 3–4 и задача 6.

## Карта этапов

| Этап | Выполняемые задачи | Поставляемый результат | Зависимость |
|---|---|---|---|
| 0 | 0–1 | Контракт среды, изолированный домашний kind | Нужен перед функциональными проверками |
| 1 | 2–8, включая 5а и 7а | Первая поставка: дом → DEV → TEST → PROD | Не зависит от будущих реплик; 7а начинается параллельно с задачей 0 |
| 2 | Отделение frontend и его диагностики | 2 frontend, 1 backend | Этап 1 принят |
| 3 | Отделение API/worker и долговечная очередь | 1 API, 1 worker | Этап 1; frontend может внедряться независимо |
| 4 | Общее состояние API и файловый доступ | 2 API, 1 worker | Этап 3 и стенд с несколькими VM |
| 5 | Владение задачами, несколько workers, эксплуатация | Несколько API/workers с проверенными отказами | Этап 4 |

## Структура будущих изменений

### Обобщение для нескольких платформ

Выбранные направления из [оценки и критериев](../../KUBERNETES_PLATFORM_QUESTIONS.md)
включены в roadmap продукта. Открытый вопрос о конкретной платформе не блокирует
проектирование общей функции, но продолжает блокировать неподтверждённую
окруженческую приёмку. Перенос в план не означает реализацию: чекбоксы относятся
к завершённой функции и её проверке.

**Критерий:** следующая инсталляция использует ту же поставку application images,
chart/operator artifacts и исходников, меняя поддерживаемые параметры,
Secret/PVC references и разрешённый adapter. Отдельный deploy-проект хранит
конфигурацию; правки Python/Next и форк общих templates для каждой инсталляции
не являются обобщением. kgateway, VSO, OpenSearch и конкретные GitLab components
не становятся обязательными для всех платформ. Поддержка любых Gateway/IdP/CSI/
CPU не обещается; поддерживаемые сочетания фиксируются явно.

| Вопросы | Общая работа и место в плане | Критерий проверки |
|---|---|---|
| 1, 3, 4, 6 | Пакет конфигурации, задачи 3/5/8 | Два обезличенных поддерживаемых профиля на одной поставке; invalid values отклоняются до изменения ресурсов |
| 5 | Kubernetes external, новая задача 3а | Bundled сохранён; external не создаёт/удаляет чужие БД/тома; явный backup ownership и restore contract |
| 7–8 | Общий OIDC profile, отдельный подробный план задачи 5а | Public/confidential, PKCE/token auth method и authoritative claims проверены тестовым IdP; production protections сохранены |
| 9–10 | Общая audit schema/JSON после commit, задача 5а | Stable event ID/allowlist; DB audit сохранён; rollback не публикует success; collector настраивается снаружи |
| 11 | Отдельный выбор best-effort/outbox | Loss/duplicate policy и recovery проверены для выбранного варианта; stdout не равен downstream ACK |
| 12 | Отдельный trusted-IP profile при необходимости | Peer/unknown различаются; relay включён только с доказанной upstream boundary; spoof/replay отклоняются |
| 13–14 | CI/Secrets/CA adapters, задачи 5/7 | Общие release/lifecycle команды и digests; config и rotation задаются без форка приложения |
| 15 | Model capabilities и метрики, задачи 2/5/8 | Поддерживаемый provider/counter проверен; scrape/auth явные; отсутствующие metrics/live quality не объявлены подтверждёнными |
| 2 | Recreate первой поставки; zero-downtime в этапах 3–5 | Один writer подтверждённо остановлен; RollingUpdate не разрешён без shared state/ownership/fencing |

**Порядок и предварительные трудозатраты из оценки:**

1. Интеграция уже сделанной ветки — 1–3 ч.д. один раз при отсутствии значимых
   конфликтов/регрессий: review фактического снимка, закрытие dependency gate,
   local/Compose regression. Commit/merge/push не выполняются автоматически
   вследствие добавления оценки в план.
2. Пакет конфигурации — 4–7 ч.д.: reuse готовых values/settings, schema,
   precedence, безопасные probe/traffic overrides, документация и проверки.
   Это пересекающиеся работы 1/4/6/14/15; новые OIDC/audit/metrics не включены.
3. OIDC 7–8 — 6–10 ч.д. общим пакетом. Public/PKCE/UserInfo выбраны как общая
   возможность, применение зависит от регистрации. Refresh/back-channel logout
   и немедленный revoke не входят в пакет.
4. Audit 9–10 — 7–14 ч.д. с best-effort JSON после commit либо 12–24 ч.д. всего
   с outbox/recovery. Варианты альтернативны, не суммируются. Failure policy
   выбирается до плана dispatcher; trusted IP и downstream ACK не включены.
5. External storage — 5–10 ч.д. отдельным расширением после спецификации
   ownership/backup. Включено в roadmap, не блокирует первую bundled поставку.
   Compose external не является готовым Kubernetes external.
6. Окруженческая работа учитывается отдельно: CI adapter 2–5 ч.д., совокупная
   инфраструктурная приёмка DEV/TEST/PROD 3–6 ч.д., содержательная live-model
   приёмка 3–7 ч.д. плюс API расходы. Работа платформенных администраторов
   и ожидание согласований не включены.

Диапазоны инженерные, не календарный график. Общие пакеты учитываются один раз,
adapter/config и приёмка — для каждой инсталляции. Подробные планы составляются
по одному подпроекту до реализации; roadmap не фиксирует невыбранные интерфейсы.

**Дополнение к задачам 3/5/8 — пакет конфигурации:**

Подготовлен [проект контракта ближайшего пакета](../specs/2026-10-04-kubernetes-configuration-design.md):
probe defaults/overrides, precedence, формат durations, опциональный kgateway
adapter, runtime CA reference и A/B acceptance. Пакет реализован в
`codex/kubernetes-rollout`; [конфигурационная приёмка](../reports/2026-10-04-kubernetes-configuration-acceptance.md)
проверила minimal/legacy/A/B, maximum upload, stream timers и CA recovery.
Целевая платформа и остальные подпроекты roadmap этим не закрываются.
Next build limits и максимальный multipart upload выделены как проверяемые
границы, без обещания runtime override.
После проверки 04.10 добавлены trust для операционных Jobs, immutable CA rotation
через deploy и восстановление прежнего bundle, согласование probes/grace с
operator budgets, опциональный `0s` с idle policy и переход bootstrap/toolbox на
schema 2. `proxyTimeout` внешнего Next rewrite не считается deadline API fetch.
Подготовлен [подробный implementation plan пакета](2026-10-04-kubernetes-configuration.md):
baseline/schema-1 recovery kit, probes/budgets, traffic preflight, application/Job
trust, rotation/restore, bootstrap/toolbox 2 и последовательная runtime приёмка.
Задачи подробного плана 0–6 выполнены; regression 316 PASS/0 skips, fixture
stability 10×43 и 10×12. Приёмка завершена с сохранёнными failures и явным
продолжением; независимый review/полный CI остаются открытыми.
Проверка подробного плана добавила независимый backup
format=2, recovery после failed CA rotation, проверяемый состав двух комплектов,
сохранение полного CI regression и проверку TLS отдельно от readiness.

- [x] Зафиксировать поддерживаемую матрицу и precedence существующих Settings,
  ConfigMap/Secret, chart-controlled env и overlays; не создавать второй loader
  и не разрешать Secret переопределить environment/model/storage identity.
- [x] Добавить только числовые probe timing overrides с schema, сохранив текущие
  defaults, пути `/health/live`/`/health/ready`, порты и frontend TCP probe.
  Произвольные команды/URLs и отключение проверок через values запрещены.
  До acquire проверять budget startup/readiness в пределах 600 секунд и grace
  текущего/желаемого Pod с резервом в пределах 180 секунд; правила заданы в spec.
- [x] Описать согласованность upload/multipart, request/backendRequest,
  stream-idle/heartbeat, LLM timeouts и grace period. TrafficPolicy — опциональный
  adapter конкретной CRD schema; выключенный adapter не требует kgateway API.
  `0s` разрешать только через explicit opt-in с конечной idle policy, корректно
  различать отключение таймера и положительные durations; область действия —
  весь frontend HTTPRoute. Проверить active/stalled stream и cancel.
  Неизвестные внешние лимиты остаются непроверенными.
- [x] Переиспользовать existing Secret/CA/mirrors и управляемый restart/rotation;
  CA env/read-only volume должны переноситься вместе в migration/backup/restore
  Jobs из соответствующего старого или нового Pod template. Ротация использует
  существующий deploy с backup/migration, immutable ConfigMap и сохранением
  предыдущего bundle; recovery metadata и restore в новый namespace включают trust.
  Повторный deploy с old reference допустим при исправном текущем backup path;
  иначе — pre-change backup в новый namespace/PVC, проверка копии, отдельное
  переключение доступа. Старые Secret/ConfigMap refs не менять до конца backup.
  scrape/auth документировать явно. Пример manifest не создаёт отсутствующий
  `/metrics`; новые audit metrics относятся к audit пакету.
- [x] Выпустить schema 2 вместе с release metadata, operator archive, trusted
  consume.py и matching toolbox; проверить матрицу consumer/release 1/2.
  Сохранить старый комплект для restore backup schema-1 release, затем проверить
  upgrade на 2. Новые tools первоначально поддерживают `[2]`, без скрытого `[1,2]`.
  Backup независимо получает `manifest.format=2` с обязательной trust identity
  или null; старый reader должен отказать новому backup, а не игнорировать CA.
  Старые backups остаются format=1 и восстанавливаются исходным комплектом.
- [x] Для недостающих overrides сначала RED assertions в
  `backend/tests/test_kubernetes_chart.py`: invalid timing, altered probe path,
  missing enabled adapter API, unsupported replicas и PROD synthetic profile;
  затем минимальная реализация и GREEN. Прежние защитные проверки сохраняются.
- [x] Проверить два обезличенных профиля A/B на одной неизменной поставке:
  разные namespace, host/callback, Secret/PVC references и поддерживаемые
  resources/timing; независимые БД, индексы и PVC. Upload/NDJSON/upstream SSE/cancel,
  повторный deploy, CA rotation/recovery и непустой backup/restore проходят
  без правок кода и секретов в output. Отдельно выполнить runtime smoke без
  Gateway/Vault/runtime trust. Это не замена live/SSO/platform приёмки.

Сначала сверить перечисленные пути с веткой реализации. Уже существующие chart/scripts/tests дополняются по новым критериям; новые файлы создаются только при отсутствии. Jobs базовой поставки формируются `lifecycle.py` из rendered application contract; не вводить вторую независимую реализацию YAML-шаблонов.

| Файл или каталог | Ответственность |
|---|---|
| `deploy/helm/ai-knowledge/{Chart.yaml,values.yaml,values.schema.json,templates/}` | Приложение, Services, StatefulSets, probes, ConfigMap, NetworkPolicy; references на существующие Secrets/PVC |
| `deploy/kubernetes/profiles/{home,dev,test,prod}.values.example.yaml` | Обезличенные профили, значения ресурсов, режим моделей и публикации |
| `deploy/kubernetes/platform/{listenerset,httproute,traffic-policy,vault-static-secret}.yaml.example` | Необязательные интеграции платформы, без установки операторов/CRD и реальных реквизитов |
| `deploy/kubernetes/storage/claims.yaml.example` | PVC вне lifecycle application release |
| `deploy/kubernetes/lab/{kind.yaml,README.md}` | Воспроизводимый домашний стенд и границы его доказательности |
| `scripts/kubernetes/lifecycle.py`, `backend/scripts/{kubernetes_migrate,backup_totals,create_qdrant_snapshot,restore_qdrant_snapshot,check_integrity}.py` | Разовые Jobs из rendered image/env/PVC contract и существующие backend команды операций |
| `scripts/kubernetes/{preflight,deploy,backup,restore,smoke}.sh` | Одна операционная процедура для домашнего запуска и CI |
| `backend/tests/test_kubernetes_lifecycle.py` | Порядок операций, lock, PVC bindings, ошибки и повторный запуск; использовать существующие Python tests |
| `deploy/ci/gitlab-kubernetes.yml` | Обезличенный шаблон контракта build/deploy проектов; доставка подключается в отдельном deploy-репозитории |
| `.github/workflows/kubernetes.yml` | Рендер/валидация Helm и изолированный kind smoke; текущий CI сохраняется |
| `backend/test_scripts/model_stub/{server.py,fixtures.py,Dockerfile}` | Отдельный HTTP/SSE stub image с синтетическими ответами и сценариями ошибок |
| `backend/app/config.py`, `backend/app/main.py` | Разделение runtime environment/model mode и независимый liveness endpoint |
| `backend/tests/test_deployment_profile.py`, `test_model_stub_contract.py`, `test_kubernetes_chart.py` | Матрица окружений, текущие LLM контракты, смысловые проверки rendered manifests |
| `backend/tests/test_health_readiness.py` | Регрессия разделения liveness/readiness/dependency health |
| `docs/KUBERNETES_DEPLOYMENT.md`, `docs/KUBERNETES_TESTING.md`, `SECURITY.md` | Установка, эксплуатация, критерии приёмки, безопасность |
| `deploy/ci/operator-tools/`, `scripts/kubernetes/release.py`, `backend/tests/test_kubernetes_release.py` | Версионированный комплект operator tools, bootstrap, recipe toolbox и проверки потребления из deploy-проекта |

Не переписывать `Dockerfile` только ради Kubernetes. Для целевой сборки обязательны зеркала base images/зависимостей и CA: добавлять совместимые build args или отдельный тонкий platform build target с сохранением существующих defaults; подтвердить работу Python trust store, Node trust store и сборочных менеджеров пакетов.

## Этап 0 и первая поставка

### Задача 0. Зафиксировать обезличенный контракт трёх сред

**Files:** Create `deploy/kubernetes/profiles/*.values.example.yaml`, `docs/KUBERNETES_DEPLOYMENT.md`.

**Interfaces:** produces deployment contract: Kubernetes/CRD versions, CPU architecture, namespace quota, CSI/accessMode, registry references, Gateway/TLS ownership, Vault references, IdP and model connectivity, maintenance window, backup destination, RPO/RTO. Реальные значения хранятся в защищённой конфигурации оператора, не в Git.

- [ ] Read-only проверить версии/доступы в каждом контексте, allowed Pod Security/UID/fsGroup, PVC provisioning/reattach и возможность StatefulSets. Если доступа ещё нет, записать конкретный непроверенный пункт; не считать документацию проверкой кластера.
- [ ] Зафиксировать выбранный bundled профиль для всех сред; подтвердить ёмкость, backup ownership и защищённое место хранения копий вне исходных PVC.
- [ ] До подготовки новых заявок проверить существующие namespace, registries, Vault и права; отсутствие старой MR-ветки не доказывает отсутствие ресурса. Для kubectl задавать явный context и ограниченный request timeout.
- [ ] Подтвердить размещение продуктивного PostgreSQL: измерить latency/IOPS/fsync выбранного CSI и restore под нагрузкой; согласовать bundled с учётом рекомендации выделенного сервера БД. При отказе остановить PROD-путь и отдельно пересмотреть storage, не менять молча выбранную топологию.
- [ ] Назначить владельцев доступа к логам/метрикам, backup/restore/rollback и сертификатам. Проверить реальные права, индекс логов и безопасное хранение kubeconfig; персональные данные исполнителей оставить в защищённом реестре.
- [ ] Подготовить namespace declarations для DEV/TEST/PROD: pool, team, logging, quota. Никаких создания общих CRD/Gateway/Vault операторов приложением.
- [ ] Проверить actual API schema ListenerSet/HTTPRoute/TrafficPolicy/VSO через API discovery; не выводить поддержку ListenerSet только из версии Kubernetes.
- [ ] Отдельно записать: DEV TLS выпускается платформой; TEST/PROD требуют готовый сертификат. Назначить срок обновления сертификатов и владельца операции.
- [ ] Зафиксировать PROD model API protocol, model IDs, embedding dimension, CA/auth, rate/concurrency limits; эти реквизиты остаются вне документации.

**Приёмка:** заполненный защищённый deployment contract и обезличенный перечень выполненных/невыполненных проверок. Нет обязательного поля, незаметно подставленного от домашнего стенда.

### Задача 1. Подготовить домашний стенд на существующей VM

**Files:** Create `deploy/kubernetes/lab/kind.yaml`, `deploy/kubernetes/lab/README.md`, home values example.

**Interfaces:** consumes contract задачи 0; produces именованный kube-context лаборатории, namespace, test registry/image import, PVC, доступный Gateway и test IdP, адреса stub/live для тестов.

- [ ] Проверить VM `training-ubuntu`, её фактический Proxmox-узел, доступ, свободные CPU/RAM/disk и установленные версии. Не обращаться к VM по одному неподтверждённому номеру.
- [ ] По замеру 03.10.2026 VM имеет 4 vCPU / 6 ГиБ / около 50 ГиБ, а не исторические 8 ГиБ. Перед следующим прогоном повторить инвентаризацию. Для полного стенда согласовать расширение до ориентиров 4–6 vCPU / 16 ГиБ / 120–160 ГиБ; это оценка, а не уже выделенные ресурсы.
- [ ] Создать отдельный kind cluster с pinned node image, совместимый с целевой версией; выбрать CIDR без пересечения с LAN/VPN и каталог persistent данных вне удаляемого контейнера kind.
- [ ] Установить Gateway API/kgateway для последующей проверки маршрута, CNI с NetworkPolicy enforcement и storage provisioner. Минимальный smoke допускает временный port-forward с явной отметкой, что Gateway ещё не проверен.
- [ ] Подготовить отдельный realm/client тестового IdP, тестовые роли, TLS/локальный DNS или SSH-туннель. Не менять существующие домашние DNS/VPN/Compose staging/model services.
- [ ] Проверить запись/чтение небольшого PVC после перезапуска Pod, доступ из Pod к IdP и образам. Данные теста отдельные; не использовать рабочий corpus.
- [ ] Выполнять тяжёлые bundled-проверки последовательно в фактическом resource budget. Дополнительный Cilium kind после проверенного backup остановлен для экономии памяти; перед повторным использованием запустить его и повторить smoke/probes. Результаты и backup сохранять вне kind; до off-VM drill не считать backup на диске этой VM защитой от её потери.

**Приёмка:** reproducible lab setup и фактический resource budget; при нехватке ресурсов полный тест остаётся невыполненным, лимиты не уменьшаются ради зелёного статуса.

### Задача 2. Создать модельный тестовый контур и разделить health

**Локальный прогресс общего решения (03.10.2026):** дополнены stub профиль
и проверки tracked `documents/fast/full` через настоящий HTTP/SSE адаптер.
`backend/tests/test_kubernetes_chat_modes.py` не подменяет budget или LLMClient;
fixture подменяет retrieval и identity в isolated SQLite. Выбранный synthetic
контракт описан в [KUBERNETES_MODEL_PROFILES.md](../../KUBERNETES_MODEL_PROFILES.md).
Дополнительно проверяются malformed budget, multi-batch full, global citations
после пропуска oversized source и cancelled attempt после позднего ответа.
Реальные provider/template/tokenizer проверяются отдельно в окруженческой копии;
неподдерживаемый live профиль остаётся fail-closed.

**Files:** Create `backend/test_scripts/model_stub/*`, `backend/tests/test_model_stub_contract.py`, `backend/tests/test_deployment_profile.py`; Modify `backend/app/config.py`, `backend/app/main.py`, `backend/tests/test_health_readiness.py`.

**Interfaces:** `GET /health/live` → HTTP 200, `{"status":"alive"}`, без вызова БД/Qdrant/моделей. Existing `/health/ready` unchanged. Settings: `deployment_tier` defaults `local`, allowed local/home/ci/dev/test/prod; `model_mode` defaults `live`, allowed live/stub/unavailable. PROD requires live and non-fake embeddings. Stub exposes `/v1/models` and `/v1/chat/completions` on 8000, no outbound calls.

- [ ] Сначала написать отрицательные tests: `test_liveness_ignores_dependency_outage`, `test_readiness_still_fails_for_storage_outage`, `test_prod_rejects_stub_or_fake`, `test_existing_local_and_compose_defaults_unchanged`. Запустить и подтвердить ожидаемое падение новых assertions.
- [ ] Реализовать независимый liveness и конфигурационные проверки; не менять существующий смысл `ENVIRONMENT` и защит production auth.
- [ ] Проверить текущие `llm_client.py`, `llm_profiles.py`, `embedder.py` и task schemas; fixtures соответствуют generation/classification/development/translation и чатовым ответам. Статические ответы не выдают себя за извлечённые реальные знания.
- [ ] Написать tests: `test_stub_stream_consumed_by_real_llm_client`, `test_stub_generation_matches_current_schema`, `test_stub_translation_preserves_input_count`, `test_stub_429_503_and_interrupted_sse`. Проверять через настоящий адаптер LLM, включая finish_reason и `[DONE]`; GET `/models` недостаточно.
- [x] Локальная матрица `documents/fast/full` для HOME/CI/DEV/TEST stub с явным `local_qwen` transport; `test_tracked_chat_requires_trusted_token_budget` включает missing tokenizer, unknown provider и PROD live против marked synthetic endpoints; `test_cancel_keeps_stopped_history_after_late_result` проходит реальную HTTP/SSE completion. Источник synthetic window/template/counter фиксируется в общей спецификации; guard не отключается.
- [ ] В окруженческой копии для каждого выбранного live профиля подтвердить реальный context window и соответствующий модели template/token counter; при необходимости отдельно спроектировать неподдерживаемый provider. Локальная synthetic матрица не доказывает точность live tokenizer.
- [ ] Home `/props`, `/apply-template`, `/tokenize` и profile `local_qwen` уже проверены со synthetic счётчиком, но не означают поддержку всех DEV/TEST/PROD профилей. Подробный model budget contract фиксировать в `backend/app/services/chat_token_budget.py` и тестах модели; fake счётчик запрещён для PROD и не доказывает точность токенизации.
- [ ] Реализовать stub только для синтетических фикстур, предусмотреть точный выбор error/delay сценариев в test configuration. Запретить внешние egress; image отдельный, не в production application image.
- [ ] В dev/test example задать `MODEL_MODE=stub`, fake embeddings выбранной размерности, BM25 и test model ID; live production example требует реальные endpoints и отдельный пустой индекс. Результат stub содержит явный маркер тестовых данных.
- [ ] Запустить из `backend/`: `python -m pytest tests/test_health_readiness.py tests/test_deployment_profile.py tests/test_model_stub_contract.py -q`. Проверить реальные HTTP/SSE и понятную ошибку в unavailable mode.

**Приёмка:** DEV/TEST могут пройти функциональный цикл без доступа к настоящим моделям; это не отчёт о качестве поиска/генерации. PROD не принимает тестовый профиль, существующие local/Compose конфигурации сохраняются.

### Задача 3. Упаковать compact bundled развёртывание

**Files:** Create chart, PVC examples, `backend/tests/test_kubernetes_chart.py`; references existing `backend/Dockerfile`, `frontend/Dockerfile`, `backend/app/deployment/storage_mode.py`.

**Interfaces:** chart values: `deployment.environment`, `deployment.mode=compact`, `application.enabled`, `application.replicas=1`, `storage.mode=bundled`, image repository/digest per container, `secrets.existingSecret`, `persistence.data.existingClaim`, `persistence.diagnostics.existingClaim`, `models.mode`, optional platform resources. Service names backend/postgres/qdrant scoped by namespace.

- [ ] Сначала tests rendered manifests: 2 application containers, ровно 1 application replica, Recreate, HPA absent, backend:8000, postgres:5432/qdrant:6333, secrets только по reference, PVC не удаляются chart, no hostPath в production.
- [ ] Добавить schema rejection для replicas>1, workers>1, split mode до реализации, prod+stub/fake, отсутствующих digest/claim/secret references и неподдерживаемого storage mode.
- [ ] Создать ServiceAccounts без автоматического API token; ConfigMap с корректными DATA_DIR/diagnostic paths, envFrom Secret, production app mode во всех целевых средах. Не передавать backend secrets frontend контейнеру.
- [ ] Добавить chart contract `imagePullSecrets`: required для DEV registry с auth, пустой список допустим для подтверждённого анонимного TEST/PROD зеркала. Проверить pull всех app/storage/stub/job images из целевого Pod; не переносить DEV токен между средами.
- [ ] Создать непривилегированный initContainer для диагностических подкаталогов, shared mounts с существующими read-only границами; проверить fsGroup на выбранном CSI. Права на `/tmp` и parser subprocess не ломаются.
- [ ] Создать PostgreSQL/Qdrant StatefulSets с сохранением PVC и fixed versions из Compose. PostgreSQL/Qdrant port не публиковать наружу. Credential rotation и major upgrades БД не включать в обычное обновление приложения.
- [ ] Настроить backend direct startup/live/ready и frontend TCP probes. Проверить отсутствие цикла frontend health → backend Service → ещё неготовый Pod.
- [ ] Выполнить `helm lint deploy/helm/ai-knowledge`, render всех примеров с тестовыми placeholders, schema validation против целевых Kubernetes+CRD schemas и pytest chart tests. Не выдавать `helm lint` за кластерную проверку.

**Приёмка:** deployment стартует с разрешённым UID без privileged/root init, Ready зависит от собственных хранилищ, LLM outage не вызывает restart storm. Delete/reinstall release с прежними PVC сохраняет тестовый corpus.

### Задача 3а. Спроектировать Kubernetes external как общее расширение

Статус: включено по пункту 5 списка вопросов; не реализовано, не входит в
пакет небольших settings и не блокирует первую согласованную bundled поставку.

**Files:** Create `docs/superpowers/specs/2026-10-04-kubernetes-external-storage-design.md`
и отдельный подробный план после рассмотрения спецификации. Область будущей
реализации: chart `values/schema`, `templates/{storage,config,application,network}.yaml`,
`scripts/kubernetes/lifecycle.py`, `backend/app/deployment/storage_mode.py`,
backend backup/restore scripts и `backend/tests/test_kubernetes_{chart,lifecycle}.py`.
Здесь перечислены планируемые изменения, новые runtime параметры не объявлены.

**Interfaces:** два явных режима `bundled` и `external`, сохранённый compact
single-writer lifecycle; адреса/TLS/credentials передаются поддерживаемой
конфигурацией и Secret references. External не создаёт PG/Qdrant StatefulSets
или их PVC; Data/diagnostics остаются собственными томами приложения.
Использовать готовый runtime `STORAGE_MODE`, не копировать автоматически
Compose orchestration в Kubernetes.

- [ ] Описать supported external PostgreSQL/Qdrant connectivity/auth/TLS,
  ownership базы/коллекции и миграционных прав; mixed topology не поддерживать
  молча. Scope backup/restore приложения и платформенного storage разделить.
- [ ] Учесть существующие ограничения: chart/schema фиксируют bundled, env
  фиксирует Service endpoints, preflight проверяет bundled credentials,
  backup/restore работают через собственные storage Pods. Замена URL одна
  не делает lifecycle совместимым с external.
- [ ] Утвердить согласованный backup/restore contract SQL + FS + Qdrant:
  остановку writers, версии, totals/content, ответственность и подтверждение
  внешних копий. Если операция не поддержана выбранным внешним сервисом,
  она отклоняется до мутаций; пустой PASS или только FS backup недопустимы.
- [ ] Перед кодом описать failure tests: external создаёт ноль storage workloads,
  отсутствующий TLS/credential contract отклонён, чужой непустой target не
  перезаписан, unsupported backup/restore отказал до действий, bundled unchanged.
- [ ] Для приёмки использовать внешние синтетические PG/Qdrant в отдельном
  лабораторном контуре; приложение не владеет их deployment lifecycle.
  Пройти install/update/restore на новых target данных и проверить bundled,
  Compose external/bundled и local regression. HA/failover и чужие RPO/RTO
  этим сценарием не подтверждаются.

**Результат этой задачи проектирования:** рассмотренная спецификация с
ownership/backup/failure contracts и подробный план. Само включение задачи
не разрешает `storage.mode=external` в текущем chart/schema.

### Задача 4. Обеспечить порядок установки и миграций


**Files:** Create `scripts/kubernetes/preflight.sh`, `deploy.sh`, `scripts/kubernetes/tests/test_lifecycle.sh`, migration Job template.

**Interfaces:** существующий `lifecycle.py` принимает operation `preflight|provision|deploy|backup|restore|recover-lock`, обязательные `--context`, `--namespace`, `--values`, optional `--release`, `--chart`, `--expected-tier`. `deploy`/`backup` требуют `--backup-root`, restore — `--backup`, recovery — `--operation-id`. Shell wrappers передают тот же CLI; отдельного `--operation install|upgrade` нет. Root `.env` не является production configuration. Migration Job и lock содержат operation ID; повтор проверяет реальное состояние предыдущей операции.

- [ ] Написать fake-command tests: `migration_failure_never_starts_app`, `upgrade_waits_for_old_pod_exit`, `parallel_operation_refused`, `wrong_context_refused`, `existing_secret_never_rotated`, `second_backend_never_started_on_unknown_old_pod`.
- [ ] Реализовать namespace operation lock с сохранением owner/operation ID. Прерванная операция остаётся распознаваемой; lock не снимается по одному таймеру без проверки, выполняется ли владелец.
- [ ] Install: preflight → wait for required Secret/PVC → chart с application.enabled=false → healthy PG/Qdrant → отдельный migrate Job → wait Complete + Alembic head → application.enabled=true → wait/smoke. Storage wait ограничен по времени и выдаёт причину отказа.
- [ ] Upgrade: maintenance → stop existing application and confirm exit → backup задачи 6 → migrate → start → smoke. Не запускать новую версию после partial/unknown migration. Повтор не создает второй активный migrate Job.
- [ ] Preflight проверяет установленные CRD, нужный namespace RBAC, наличие Secret keys без вывода значений, release/storage/profile compatibility, image availability. Bootstrap preflight и проверки уже созданных ресурсов различаются, чтобы install не требовал ещё не созданный Secret от VSO.
- [ ] Ввести stop condition при недоступном старом узле: оператор подтверждает остановку/изоляцию узла по runbook; force-delete не заменяет это подтверждение.
- [ ] Выполнить shell tests и реальный install/update в kind. Проверить не только readiness, но и число действующих backend процессов во время обновления.

**Приёмка:** штатная установка не содержит ручного недокументированного шага; любая ошибка миграции оставляет приложение остановленным, данные и отчёт доступны. На первой поставке измеряется окно обслуживания; нулевой простой не обещается.

### Задача 5. Подключить Gateway, Vault, CA и наблюдаемость

**Files:** Create platform manifest examples; Modify chart optional templates, deployment docs и `SECURITY.md`; при необходимости backward-compatible Dockerfile build args/CA target.

**Interfaces:** platform config с placeholders: Gateway parent reference, hostname, TLS Secret reference, Vault mount/path and existing Secret names. Ни один оператор, общий Gateway или namespace не создаётся application chart автоматически.

- [ ] Написать manifest checks: no Ingress, no TLS Secret templates, same-namespace backendRefs, numeric port, HTTPS Terminate, hostname required, redirect sectionName=http, route request timeout>=backendRequest.
- [ ] Разделить DEV automatic TLS и TEST/PROD existing TLS. Проверять ListenerSet Accepted/Programmed и HTTPRoute Accepted/ResolvedRefs; доступ к cert-manager API не предполагать.
- [ ] Настроить VSO Opaque runtime secrets; ждать их синхронизации до PG startup и миграций. Namespace/team binding находится во внешней подготовке. TLS material не передавать через application values или stdout.
- [ ] Проверить загрузку разрешённого максимального файла, multipart overhead, длинный SSE, cancellation, SSO login/logout/callback и CSRF через Gateway. Не переносить пример buffer 32Mi без проверки текущего upload contract.
- [ ] Различать HTTP idle 180s на порту 80 и HTTPS tunnel idle 1h на 443; зафиксировать также Gateway request/streamIdle timeouts. Проверить SSE с ответом дольше 180s, паузой до первого события и разрывом потока. Не вводить общий 180s deadline для HTTPS по устаревшему пересказу.
- [ ] Проверить DNS и фактический путь нового домена сразу через Gateway. Заявку на переключение балансировщика включать только для существующего Ingress-домена; при такой миграции старый маршрут сохраняется до проверки нового.
- [ ] Добавить CA в нужные trust stores с проверкой TLS без `verify=false`. Сборка и runtime должны работать с разрешёнными registry/package mirrors и без скачивания зависимостей при запуске Pod.
- [ ] Проверить NetworkPolicy реальными разрешёнными/запрещёнными соединениями на CNI с enforcement. DEV/TEST не обращаются к PROD models/data; в PROD stub отсутствует.
- [ ] Подключить платформенные Pod metrics и безопасные logs; приложение не обещает существование своего `/metrics`, если его нет. Проверить вывод request IDs и отсутствие секретов/текстов документов; support bundle по-прежнему доступен штатным admin flow.
- [ ] В отдельной декларации namespace по каждой среде включить сбор через `logging.enabled: true`; не подменять его сокращением `logging: true` или произвольным label application chart. Проверить Logging Operator → OpenSearch, parser/mapping и появление synthetic event UUID; договориться о правах/retention. Для защищённых метрик подтвердить scrape/auth и выбор ScrapeConfig, не копировать примерный interval `30s` как требование.
- [ ] Проверить контролируемую ротацию env Secret: Pod получает новые значения только после предусмотренного lifecycle restart, старый backend подтверждённо остановлен. Пароли и session keys не регенерируются при обычном upgrade.

**Приёмка:** установка через Gateway работает с production auth, а network/TLS policy подтверждена. Домашняя эмуляция не заменяет VSO/admission проверку в DEV.

### Задача 5а. Закрыть контракты OIDC и аудита до платформенной приёмки

До фиксации обязательного объёма уточнить [открытые вопросы](../../KUBERNETES_PLATFORM_QUESTIONS.md).
Chart examples не доказывают несовместимость нашего compact/Next решения.
Public/confidential/PKCE и authoritative claims source выбраны как общий пакет
по пунктам 7–8; его проектирование не ждёт регистрации конкретной среды.
Полезность общего расширения не означает несовместимость текущего confidential
flow. Audit foundation/JSON выбраны по пунктам 9–10. Durable outbox и trusted-IP
adapter остаются отдельными решениями по failure/upstream contract; пример
не делает их обязательными для каждой инсталляции.

**Проекты спецификаций 04.10.2026:**
[совместимость OIDC](../specs/2026-10-04-oidc-compatibility-design.md) и
[аудит/доставка событий](../specs/2026-10-04-audit-delivery-design.md).
[OIDC implementation plan](2026-10-04-oidc-compatibility.md) исполнен в рабочей
ветке: Settings, atomic DB state, Authlib signed-token matrix, UserInfo,
commit-safe sessions и home profiles. Итоги и версии images приведены в
[OIDC отчёте](../reports/2026-10-04-oidc-compatibility-acceptance.md); целевая
регистрация и delivery gates остаются открытыми. Изменения не интегрированы.
Для аудита пользователь выбрал durable outbox с повтором. Подготовлен отдельный
[план DB/stdout аудита](2026-10-04-audit-outbox.md): event schema/allowlist,
security transactions, bounded dispatcher, retries и recovery. Ядро audit runtime реализовано в рабочем дереве и проверено локально и на домашних confidential/basic/PKCE none и public/none/S256/UserInfo-only HTTPS путях: [отчёт](../reports/2026-10-04-audit-outbox-acceptance.md). Nonempty PostgreSQL restore/old-image hold и отдельный полный home PVC/Secrets/key restore подтверждены; 67 confidential и 36 public immutable audit rows сохранены, historical pending held, старые cookie/flow отказаны, новый login UUID совпал с DB/stdout. Дополнительный PG fault runner прошёл 31 cases без skips: outbox-only disk-full, denial и security-mutation statement/lock timeout, connect faults, mixed block/unblock и recovery. Подготовлены portable PG и отдельный capacity CI jobs; hosted CI не проверена. Задача 5а целиком остаётся открытой. Trusted-IP relay проектируется отдельно; пока
адрес отмечается peer/unknown, без доверия произвольным forwarded headers. Примеры chart/deploy/logging
проанализированы в [обезличенном отчёте](../reports/2026-10-04-deployment-examples-review.md);
они не заменяют требования проекта и фактическую платформенную приёмку.

**Files:** отдельные подробные спецификации/планы для auth и аудита перед реализацией; область проверки: `backend/app/config.py`, `backend/app/auth/{api.py,service.py,providers/keycloak_oidc.py}`, `backend/app/services/audit.py`, `backend/app/db/models.py`, `backend/app/api/users.py`, `frontend/src/app/api/[...path]/route.js`, `SECURITY.md`. Будущие проверки: `backend/tests/test_platform_oidc_contract.py`, `backend/tests/test_platform_audit_contract.py`, `frontend/test/trustedProxyHeaders.test.mjs`.

**Interfaces:** согласованные public/confidential OIDC profiles, issuer и источник групп; профиль audit event (action, actor ID/name snapshot/type, timestamp, trusted client IP, applicable old/new values, method/reason), committed DB event и соответствующий структурированный stdout event. Точные схемы, доставка при сбое stdout и политика повторов определяются отдельной спецификацией, а не импровизируются в Helm-задаче.

- [x] Воспроизвести несовместимость public client без секрета с исходным валидатором; проверить discovery, callback, PKCE согласно регистрации клиента, confidential authentication, expiry/logout и userinfo-only groups при наличии встроенного token userinfo. Согласовать профиль DEV без отключения production protections.
- [ ] Составить матрицу обязательных событий: успешный вход/метод, отказ/причина, отказ API-аутентификации, изменения учётных записей/прав и доменные create/delete. Разделить события приложения и внешнего IdP; неприменимость account CRUD подтверждать владельцем профиля, не фиктивными событиями.
- [ ] Спроектировать передачу IP только от доверенных прокси; тесты на реальный IP, подставленный X-Forwarded-For и прямой обход Next. Существующее удаление forwarded headers не отменять без замены защитного контракта.
- [ ] Проверить каждый внешний путь, включая возможные legacy Ingress/прямой backend route; исключить обход Gateway sanitization до включения trusted-IP profile. Не считать Next Request источником проверенного TCP peer.
- [ ] Учесть диагностический NodePort самого Gateway и PROXY protocol: подтвердить сетевой допуск и доверенных отправителей, отклонить поддельный PROXY header и spoofed IP. Отличать этот путь от прямого доступа к frontend/backend; наличие протокола не устанавливает доверенную границу.
- [ ] На полном Gateway маршруте проверить buffer maxRequestSize, request timeout отдельно от stream-idle timeout, upload memory и cancellation. Значения и маршруты примера не подменяют лимиты нашего runtime; local Gateway token bucket не заменяет общий admission budget.
- [ ] Проверить новые OIDC/audit параметры от Settings до фактического rendered container env: значение в values не доказывает его передачу через фиксированный ConfigMap/Secret. Не публиковать полный env dump.
- [ ] Проверить БД + JSON stdout на одном event ID, отсутствие токенов/текстов документов, поведение rollback транзакции и ошибки доставки. Проверить сохранение снимка имени после удаления пользователя; текущей модели без FK не добавлять FK только для формального соответствия.
- [ ] Согласовать счётчик событий безопасности и внутреннюю доступность метрик. Проверить отсутствие внешнего неавторизованного доступа, ограниченную cardinality и фактическое появление JSON событий в платформенном сборщике.
- [ ] Для выбранного durable варианта проверить rollback/savepoint, pending outbox после рестарта, crash после stdout до marker и повтор с тем же event_id. Для best-effort подтвердить DB persistence, commit-aware JSON и принятое окно потери stdout без обещания повтора. Stdout flush не считать downstream ACK; historical replay/retention после restore выполняются отдельно.

**Приёмка:** DEV SSO подтверждён с зарегистрированным типом клиента; принят применимый профиль аудита и проверены его каналы/события/гарантии. Два канала, durable retry и доверенный IP проверяются, если они входят в согласованный профиль; Logging Operator сам по себе их не требует. Пока применимые подзадачи не реализованы и не проверены, общая платформенная готовность остаётся неподтверждённой.

### Задача 6. Backup, restore и поведение незавершённых задач

**Files:** Create `scripts/kubernetes/backup.sh`, `restore.sh`, backup/restore Job templates; Extend lifecycle tests; reuse reviewed functions/scripts in `backend/scripts/` for manifests, Qdrant snapshots и counts.

**Interfaces:** backup принимает explicit context/namespace/release/backup-root и общий operation ID. Артефакт содержит PostgreSQL dump, Qdrant snapshot, `/data`, checksums, versions/digests/Alembic revision, totals и подтверждение завершения. Restore требует новый target namespace/claims; существующий непустой target отклоняется.

- [ ] Tests: `incomplete_backup_not_restorable`, `checksum_mismatch_refused`, `nonempty_restore_target_refused`, `active_download_or_generation_handled`, `helm_uninstall_preserves_data`, `restore_uses_no_production_credentials`.
- [ ] Остановить приложение, дождаться выхода контейнеров и доступности PVC для Job; только после этого снимать согласованный набор трёх хранилищ. Не запускать два writer Job на одном томе.
- [ ] Записывать completed marker только после всех проверок. Backup не содержит Secret objects/plaintext env; отдельно документируется защищённое восстановление ключей/паролей и ACL резервного хранилища.
- [ ] Перед PROD подтвердить backup destination вне исходной VM/дисков с согласованными ACL, шифрованием и retention. Зафиксировать автоматический или операторский запуск по согласованному расписанию и проверку свежести completed backup; ручной разовый smoke не доказывает соблюдение частоты/RPO.
- [ ] Провести `restore_without_source_vm`: исходная VM недоступна, новый независимый target восстанавливается только из внешнего completed backup и отдельного защищённого источника секретов. Подтвердить decryption/access, согласованность PG/Qdrant/files и функциональный smoke; измерить RPO/RTO. Не считать home backup вне kind выполнением этого сценария.
- [ ] Восстановить непустой corpus в новый target: проверить количество документов/концептов/чанков, исходные файлы, ссылки, BM25, роли и экспорт. Не ограничиваться отсутствием ошибок pg_restore.
- [ ] Прервать generation/export/download во время обновления и документировать фактические paused/failed/retry состояния. Повтор генерации выполняется только по существующему контракту; не объявлять прозрачное продолжение.
- [ ] Проверить image-only rollback на совместимой схеме и полный restore для несовместимой; `helm rollback` не подменяет database rollback. Зафиксировать измеренные время и объём потери данных, сравнить с согласованными RPO/RTO.

**Приёмка:** подтверждён restore drill после отказа и отдельный off-VM drill; резервные копии не зависят от доступности исходных PVC/VM, секреты восстанавливаются защищённо, удаление release не уничтожает корпус. Пока независимый backup destination не предоставлен, этот gate остаётся открытым.

### Задача 7. Встроить проверки и цепочку поставки

**Локальный прогресс operator tools (04.10.2026):** реализованы воспроизводимая
упаковка, release identity, доверенный bootstrap и
`deploy/ci/gitlab-kubernetes-consumer.yml` для отдельного deploy-проекта.
`backend/tests/test_kubernetes_release.py` проверяет запуск без исходников,
подмену archive/chart, schema/runtime и небезопасные записи архива.
Описание: `deploy/ci/operator-tools/README.md`. Проверка опубликованной поставки,
реальные платформенные adapters и CI Lint остаются открытыми; задача 7 целиком
не завершена. Новые файлы: `scripts/kubernetes/operator_tools.py`,
`deploy/ci/operator-tools/consume.py`, `deploy/ci/operator-tools/README.md`,
`deploy/ci/gitlab-kubernetes-consumer.yml`, `backend/tests/test_kubernetes_release.py`.
Добавлены `deploy/ci/operator-tools/Dockerfile`, allowlist контекста,
`install_tools.py`, `scripts/kubernetes/tests/toolbox_smoke.py` и
`backend/tests/test_kubernetes_toolbox.py`. Локальная Linux-сборка и offline
nonroot consumer пройдены; 76 focused tests без пропусков. Linux probe подтвердил
доступ UID 1000 к synthetic fixtures после явных 0755/0644 при другом host UID.
GitHub workflow
дополнен обязательным job сборки/приёмки; полный запуск workflow не выполнен.

**Files:** Create `.github/workflows/kubernetes.yml`, `deploy/ci/gitlab-kubernetes.yml`, `deploy/ci/readiness.example.json`, `deploy/ci/sonar-project.properties.example`; preserve `.github/workflows/ci.yml`; update anonymous deployment docs. Фактические include/reference и паспорт по схеме общего линтера подключаются в целевых GitLab проектах; реальные пути и реквизиты сюда не копируются.

**Interfaces:** release manifest содержит app revision, backend/frontend image digests, chart version/digest, stub digest для DEV/TEST, storage images, config schema revision и `operator_tools`: `format=1`, `source_revision`, `archive_reference`, `sha256`, `supported_config_schema_versions`, `toolbox_image_digest`. Комплект `operator-tools.tar.gz` сохраняет структуру `scripts/kubernetes/` и содержит нужные для smoke test fixtures; backend operation scripts исполняются из соответствующего backend image, а не независимой копии. Python/PyYAML/Helm/kubectl берутся из закреплённого toolbox. Protected environment config содержит component references/runner tags/registry/Vault references. Secret values отсутствуют.

- [ ] Проверить актуальные README утверждённых GitLab components и mapping для двух Dockerfile/build contexts; не предполагать корневой Dockerfile или ветку master вместо фактической default branch.
- [ ] Развести версии компонентов в документации и samples: не закреплять примерные pins без README/inputs и проверки артефакта. Различие `v1.0.1`/`1.0.1` не исправлять молча. Собственный chart проходит lint/config scan/package и применимый Security-Pipeline; sample DEV `on_success` не подтверждает TEST/PROD rules или promotion digest.
- [ ] Отдельно зафиксировать разрешённые remote package/image mirrors и release repositories: миграция зеркал не означает переноса всех релизных хранилищ. Сверить доступную версию CA image и механизм BuildKit secrets; содержимое готового ZIP sample не доказывает сборку, provenance или retention нашего operator archive.
- [ ] Разделить build и delivery projects; задать передачу release manifest/chart по версии и digest в deploy-проект. Проверить настоящий default branch в rules semantic-release и commitlint, Conventional Commit/MR headers до 72 символов без конечной точки; не переименовывать main под старый шаблон.
- [x] Локальные tests для `deploy_project_without_source_checkout`, `missing_operator_tools_refused`, `operator_tools_digest_mismatch_refused`, `operator_schema_version_mismatch_refused`: упаковка в `scripts/kubernetes/operator_tools.py`, bootstrap в `deploy/ci/operator-tools/`, расширение `release.py`. Consumer проверяет identities до мутаций и исполняет извлечённый overlay из пустого каталога без checkout. Шаблон подключает извлечённый lifecycle; полный запуск lifecycle из опубликованного комплекта в настоящем deploy-проекте проверяется следующим пунктом. Независимая ручная копия скриптов запрещена.
- [x] Общий рецепт toolbox и локальная container acceptance: Python digest, обязательные SHA/version Helm/kubectl, PyYAML 6.0.3, trusted bootstrap, build secret mounts, UID 1000; consumer без сети/source mount и пять отрицательных сценариев. Публикация и provenance остаются в окруженческой копии.
- [ ] Запустить artifact-consumer тест на опубликованном immutable комплекте, включая отсутствие Python/PyYAML/Helm/kubectl зависимости, несовместимый config schema и подмену scripts. Отсутствие/несовместимость инструмента останавливает deploy до lock, backup и миграции. В air-gap контуре комплект и toolbox проходят тот же разрешённый путь доставки.
- [ ] Подключить общий линтер, job-token allowlist, паспорт применимости по актуальной схеме и исходный снимок долга в накопительном режиме. Подтвердить поддерживаемую ветку обновлений либо согласованный immutable ref; отдельно фиксировать фактически исполненную ревизию правил. Не копировать линтер в продукт и не путать политику его обновления с pinning delivery components.
- [ ] Сделать общий линтер обязательной зависимостью всех deploy jobs, без `allow_failure` и optional зависимости. Проверить GitLab CI Lint и отрицательные сценарии: линтер удалён, упал или пропущен; deploy не становится доступным. Настроить штатный pre-push hook с учётом core.hooksPath; не отключать существующие проверки проекта.
- [ ] Собрать application images один раз; chart и stub проходят собственные проверки. Pin component versions; registry auth через Vault, без токенов в Git.
- [ ] Проверить полностью закрытую сборку `linux/amd64`: registry, PyPI/npm и bootstrap package manager используют разрешённые зеркала, build/runtime доверяют CA, Python HTTP-клиенты получают корректный CA bundle, Node получает доверенный CA. Jobs, клонирующие инструменты, содержат git; общая before_script не ломает чужие component images. Поле registry credential в Vault имеет ожидаемый компонентом ключ `token`, без вывода значения.
- [ ] Подключить SonarQube конфигурацию с UTF-8/LF и разрешение job token для security pipeline; проверить состав проверок образов, chart и архивов. Отдельно подтвердить включённое зеркалирование RC и доступность app/PG/Qdrant/stub/job artifacts из TEST. Успешная сборка и созданный registry этих проверок не заменяют.
- [ ] Добавить CI: Helm render/schema/negative assertions → shell lifecycle tests → kind install → fixture flow → restart → update → nonempty restore. CRD schema validation выполняется со схемами фактической целевой платформы.
- [ ] Сохранить действующие backend/frontend/parser/audit/Compose bundled+external jobs. Перед коммитом при реализации выполнить `node scripts/check-project.mjs` по AGENTS.md; полный CI отдельно от узких тестов.
- [ ] GitLab: проверки/build → manual DEV deploy/tests → release tag, RC security checks и перенос артефактов → manual TEST deploy/restore drill из тега → manual protected PROD deploy из того же релиза → restricted live smoke. Если платформенный cd-helm не поддерживает orchestration задачи 4, использовать последовательные разрешённые jobs; не менять порядок ради удобства компонента.
- [ ] Сериализовать jobs установки, backup и restore одной среды; не отменять миграцию автоматически приходом нового pipeline. При остановленном pipeline повтор проверяет operation lock и реальные Jobs.
- [ ] Проверить одинаковые digests при продвижении, отсутствие PROD secrets в DEV/TEST и запрет deploy stub в PROD. Stub image для TEST тоже должен пройти допустимый путь доставки.

**Приёмка:** успешный настоящий pipeline отдельно от локальных проверок; нет ручной пересборки образа для PROD и публикации из непроверенной ветки. Actual publish/deploy выполняется лишь при исполнении согласованного этапа.

**Миграция tinyglobby (локальная приёмка 05.10.2026):** плановая оценка —
**1–2 человеко-дня**. Адаптер ограничен dependency плагина Next ESLint;
собственный fork правил и изменение runtime framework не требуются.

- [x] Реализовать scoped adapter с сохранением upstream plugin и ESLint rules; clean npm ci и Docker context включают local package до установки.
- [x] Проверить rootDir на Windows/Linux: 18 regressions, включая absolute/relative/drive root, glob/brace/array, backslashes, hidden entries и symlink/junction traversal. Unsupported API останавливает lint явно.
- [x] Удалить старую braces/micromatch chain, повторить полный audit:0vulnerabilities. Windows project check:393PASS/1oldSKIP, Linux frontend:394PASS/0SKIP, lint0errors/40oldwarnings; effective67rules совпали. Runtime dependency entries не менялись.
- [x] Собрать final frontend audit-v3; home managed upgrade с coherent backup, actual running compiled hashes308/308, полный HTTPS/OIDC и response/DB/stdout smoke. Completed backup проверен вне VM, старые images/backups сохранены.
- [ ] Повторить required checks на фактически интегрированном release snapshot и внешнем CI. Локальный PASS не означает commit/merge/publication или completion задачи7. Удалить adapter/override после совместимого upstream исправления только с теми же regressions/audit.

### Задача 7а. Подготовить ввод в эксплуатацию параллельно техническим работам

**Files:** обезличенный checklist в `docs/KUBERNETES_DEPLOYMENT.md`; сами согласования, роли исполнителей с именами, ссылки на заявки и реквизиты хранятся во внешнем защищённом реестре. Начало одновременно с задачей 0, не после реализации задачи 7.

**Interfaces:** перечень применимых документов, их владельцы и статус согласования; явные gates до PROD install, опытной и промышленной эксплуатации. Наличие шаблона не означает согласование.

- [ ] Подтвердить применимость частного ТЗ и согласовать требования, безопасность, лицензии и поддержку до разрешённого PROD-развёртывания.
- [ ] Подготовить ролевую модель с соответствием группам IdP, профиль audit events, регламент backup/restore и архитектурную схему потоков/зон/портов. Использовать действующие утверждённые формы во внешнем контуре; сюда перенести только обезличенный статус и критерии.
- [ ] Назначить исполнителей backup/restore/rollback с подтверждёнными PROD правами, сроки/частоту/retention и проверку копий. Сопоставить требования RPO/RTO с измерениями задачи 6.
- [ ] Отдельно отметить согласования сертификатов, выделенного пула/VM, доступов к метрикам/логам и ИБ-приёмку. Не требовать отдельное согласование на изменение для каждого обычного выката на общий пул, если этого не требует процесс системы.
- [ ] Зафиксировать ограниченный круг пользователей опытной эксплуатации, порядок устранения замечаний и разрешение промышленного запуска. Изменение ролей/событий/архитектуры после согласования требует актуализации пакета.

**Приёмка:** формальные блокеры закрыты соответствующими владельцами; технически работающий Pod не подменяет разрешение эксплуатации.

### Задача 8. Провести домашнюю и окруженческую приёмку первой версии

**Files:** Create `scripts/kubernetes/smoke.sh`, `docs/KUBERNETES_TESTING.md`, report under `docs/superpowers/reports/` без реквизитов.

**Interfaces:** smoke принимает explicit target/environment/model mode и fixture set; результат разделён на infrastructure, auth, model-contract, live-model, restore и regression. Любой skipped gate остаётся непроверенным.

- [ ] Дом: install/upgrade/restart/restore, синтетические DOCX/XLSX/PDF/MSG, источники, поиск BM25, stub SSE/ошибки, диагностика и квоты. Дополнительный прогон на домашних настоящих моделях записать отдельно.
- [ ] DEV: реальная доставка через Artifactory/Vault/Gateway, SSO, writable PVC, TLS/CA, container policy и NetworkPolicy. Проверить model outage и отсутствие restart storm; stub outage не скрывать зелёным полным health.
- [ ] TEST: те же RC app digests; репетиция обновления, отказа миграции, backup/restore на непустом тестовом корпусе. Документировать ограничения модели stub.
- [ ] PROD: предварительно проверить модельные endpoints, размеры embeddings и модельные IDs; на ограниченной синтетической выборке подтвердить parsing→generation→index→dense/hybrid search→chat/citations, SSE, лимиты и таймауты настоящих сервисов. Не переносить индекс DEV/TEST; при несовместимости остановить ввод корпуса и применить согласованный rollback.
- [ ] Через браузер выполнить login/logout, upload, generation, source viewer, export/download, trash, admin diagnostics. Проверить роли, CSRF и отсутствие секретов в диагностике.
- [ ] Для DEV/TEST и отдельно PROD проверить `documents/fast/full`, context-window guard, соответствующий модели token counter, отсутствие/ошибку tokenizer, отмену tracked attempt и поздний ответ после отмены. Home synthetic tokenizer не закрывает PROD gate; неподдерживаемый provider оставляет соответствующий режим явно неподтверждённым, а не зелёным по обычному SSE.
- [ ] Проверить public/confidential OIDC и userinfo groups по задаче 5а, audit DB + JSON stdout и доверенный IP на полном маршруте. Проверить доступ оператора к логам/метрикам и результаты общего линтера, SonarQube и security pipeline.
- [ ] Сопоставить каждый применимый критерий с автоматической проверкой или ручным сценарием с причиной выбора. Новые/изменённые приёмочные сценарии проверить на стабильность десятью последовательными прогонами в изолированном тестовом контуре; нестабильность блокирует приёмку до исправления либо явного карантина с владельцем/причиной. Этот gate не означает десять повторных PROD smoke или всей дорогостоящей regression matrix.
- [ ] Зафиксировать requests/limits/PVC по измерениям, startup/shutdown/downtime и backup times; ограничить поддерживаемую матрицу реальными версиями. Обновить `docs/PRODUCTION_DEPLOYMENT.md`, `AGENTS.md`, `SECURITY.md` ссылками на общий Kubernetes runbook без идентифицирующих сведений.

**Gate этапа 1:** есть воспроизводимая поставка в PROD и live-model smoke, восстановление непустых данных и сохранённые проверки старых способов запуска; закрыты OIDC/audit контракты задачи 5а, CI gates задачи 7, применимые формальные gates задачи 7а и пригодность bundled storage. Пока PROD модели недоступны проверке, статус — «инфраструктура и stub проверены, реальная интеграция не подтверждена».

## Этап 2. Независимые реплики frontend

**Результат:** отдельный frontend Deployment с двумя replicas; backend пока один. Это самостоятельная поставка после этапа 1.

- [ ] Спроектировать и реализовать внутренний diagnostic control/ingestion API вместо shared filesystem между frontend и backend: instance IDs, авторизация, versioned policy, TTL capture, bounded queues/quotas, sanitization, fail-closed при просрочке разрешения.
- [ ] Область изменений: `frontend/src/lib/diagnosticServer.mjs`, `frontend/scripts/diagnostics-runner.mjs`, `backend/app/services/diagnostics/`, `backend/app/api/diagnostics.py`, chart и `SECURITY.md`.
- [ ] Проверить actual Next caches/Server Actions и единый build/deployment identity; общий cache вводить только при используемых server caches. Тестировать обновление смешанных версий и пользовательское состояние.
- [ ] Убрать frontend shared PVC dependency; включить `deployment.mode=split` для frontend при одном backend. Проверить startup/logout/revocation, SSE и поддержку сбора диагностики со всех экземпляров.
- [ ] Приёмка: поочерёдная остановка каждой frontend replica и rolling update; запросы продолжаются через оставшуюся, support bundle содержит обе, duplicate writers/утечки/потеря сессий отсутствуют. Прерванное активное соединение обрабатывается явно.

## Этап 3. Выделить API и обработчик задач

**Результат:** роли `combined`, `api`, `worker` одного backend image, начально 1 API + 1 worker. Combined сохраняет простой local/Compose запуск.

- [ ] Выполнить полный аудит startup/import-time side effects: pipeline, bulk jobs, exports, backfills, purge, translations, diagnostic bundles и recovery. Область: `main.py`, `job_queue.py`, `pipeline.py`, `export_queue.py`, `registry.py`, `staging.py`, DB models/Alembic.
- [ ] Утвердить очередь на PostgreSQL как исходный вариант; сравнить с Redis до добавления новой обязательной зависимости. Зафиксировать claim/lease/heartbeat/owner/fencing/cancel/retry contracts в отдельной спецификации.
- [ ] Реализовать durable admission/claim, владение документом, отмену через общее состояние, recovery только просроченного владельца. Приложение не сбрасывает все running/processing при старте API.
- [ ] Минимальный fencing реализовать уже с первым worker: проверка текущего owner/lease/generation при SQL commit и атомарной публикации FS/Qdrant результата. Если нельзя оградить внешнюю запись, использовать изолированные поколения и CAS публикации; старый owner не пишет активное поколение. На этапе 5 расширяются число workers и отказные сценарии, а не впервые появляется эта защита.
- [ ] До реализации утвердить versioned job payload и таблицу совместимости API/worker/DB/config schema; определить порядок expand → upgrade → contract и окно rollback. Tests `new_worker_reads_queued_old_payload`, `unknown_job_version_not_claimed`, `expired_owner_cannot_publish`, `migration_failure_starts_no_worker` сначала должны падать. Неизвестная версия сохраняет задание и останавливает claim с диагностикой, без сброса/потери данных.
- [ ] Согласовать файловый доступ API/worker: RWX на разных узлах либо ограниченное совместное размещение до перехода на storage adapter. Процессный filesystem lock не считать межузловой блокировкой.
- [ ] Приёмка: рестарт API не повреждает живую worker задачу; остановка worker до/после claim и до/после публикации сохраняет восстанавливаемое состояние; duplicate request не публикует дубль. Shared global admission не растёт от числа процессов.
- [ ] Приёмка совместимости: непустая очередь и running attempt переживают управляемое обновление и допустимый rollback; возобновившийся просроченный owner не публикует старое поколение. Несовместимая DB/job schema блокирует запуск новой роли до согласованного drain/миграции/restore.

## Этап 4. Разрешить реплики backend API

**Результат:** 2 API + 1 worker, отдельные сервисы и lifecycle. До включения закрыть все типы локального состояния, а не только очередь.

- [ ] Вынести rate/admission/LLM budgets, per-document locks, upload/dedup/staging ownership, chat cancellation, export и diagnostic download leases в общий контур.
- [ ] Добавить согласованное обновление config/cache/словарей и единственного владельца cleanup/purge; проверить auth session keys и отзыв прав на обеих репликах.
- [ ] Повторить таблицу совместимости этапа 3 на mixed API/worker versions и непустой очереди; rolling допускается только для подтверждённых пар. Неподдерживаемая комбинация требует окна обслуживания. Проверить старый API → новый worker и новый API → старый worker только там, где пара объявлена поддерживаемой; иначе deployment отклоняется до запуска.
- [ ] Устранить зависимость API от локальных файлов конкретной replica; обеспечить чтение source/export и атомарную публикацию независимо от выбранного API Pod.
- [ ] Область: `rate_limiter.py`, `llm_client.py`, `deduplication.py`, `document_tag_service.py`, `chat_history.py`, `trash.py`, `api/chat.py`, `export_queue.py`, diagnostics, storage layer и chart. Аудит может расширить список до составления подробного плана.
- [ ] Приёмка: чередование API replicas и kill одной во время upload/chat/download/delete, конкурентные правки/переиндексация/отмена; права и лимиты общие, старый writer не публикует данные, lease защищает активную загрузку от очистки.
- [ ] SSE при падении обслуживающего Pod может прерваться; UI сообщает об этом, история не повреждается. Прозрачное продолжение генерации — отдельная возможность, не подразумеваемое свойство реплик.

## Этап 5. Несколько workers и проверенная эксплуатация

- [ ] Расширить уже реализованные на этапе 3 at-least-once ownership/fencing/generation guards на несколько workers; проверить идемпотентные видимые результаты SQL/FS/Qdrant. LLM вызовы могут повториться, их стоимость входит в global budget.
- [ ] Проверить потерю lease, длительную паузу/сетевое разделение, возобновление старого worker, отмену одновременно с publish, исчерпание диска и повторное выполнение после сбоя. Просроченный worker не может повредить новое поколение.
- [ ] Начать с ручного числа workers. HPA вводить после измерений по длине/возрасту очереди и ресурсам модельных endpoints, с верхним пределом общей нагрузки.
- [ ] Добавить topology spread/PDB и проверку drain; оценить HA PostgreSQL/Qdrant/CSI/модельных API отдельно. Реплики приложения не доказывают HA всего сервиса.
- [ ] Повторить backup/restore уже с очередью и worker ownership; восстановленная тестовая копия не запускает production jobs.

## Расширение домашнего стенда для этапов 3–5

До отказных межузловых проверок подготовить 1 control-plane VM (2 vCPU, 4 ГиБ RAM, 32 ГиБ disk) и 2 worker VM (каждая 4 vCPU, 8 ГиБ RAM, 80 ГиБ disk). Итого ориентир 10 vCPU / 20 ГиБ RAM / 192 ГиБ disk плюс общее хранилище. Ресурсы подтвердить инвентаризацией до выделения. Existing training VM можно переиспользовать в одной роли после проверки и сохранения нужных данных; не пересоздавать её автоматически.

Отдельно предусмотреть CSI, при необходимости RWX storage/NAS, backups вне исходных дисков, тестовый Gateway/IdP и метрики. Домашний storage VM может потребовать ещё 2 vCPU / 4 ГиБ / 100–200 ГиБ. Один control-plane достаточен для app failover при его доступности; HA control-plane требует трёх. Несколько VM на одном физическом Proxmox-узле проверяют отказ VM/Pod, но не отказ физического хоста.

## Проверка плана перед исполнением

- [ ] Получены отсутствующие capabilities и ресурсные ограничения без добавления реквизитов в Git.
- [ ] Зафиксирован объём первого исполнения: этапы 0–1 либо выбранная их часть; реплики не включены случайно вместе с Helm.
- [ ] Для каждого последующего этапа составлен подробный план по текущему коду с миграциями, совместимостью и failure tests.
- [ ] Есть отдельные rollback решения для приложения, схемы и данных; backup drill не заменён snapshot VM.
- [ ] В финальном отчёте разделены «описано», «реализовано», «проверено дома», «проверено в DEV/TEST», «проверено на настоящих PROD моделях».


Checkpoint 05.10.2026 — file-material candidate: audit-v11-files, packaged274/274; Linux required profiles68, network24, PostgreSQL31, whole-storage2 PASS без пропусков; Windows focused101PASS/6POSIX SKIP. Новый helper ограничивает source read/config, snapshots живут до close, incomplete cleanup quarantines admission. Runtime реализован в рабочей ветке, в main синхронизирована документация. Следующий приоритет: file-phase lifecycle и projected Secret/Windows ACL, затем полный release/home/CI. Не закрывать реплики или целевую платформенную приёмку этим scoped proof.


Checkpoint 05.10.2026 — file rotation/lifecycle candidate: audit-v13-file-lifecycle, packaged274/274; Linux required profiles86, network24, PostgreSQL31, whole-storage2 PASS без пропусков; native Windows114PASS/8POSIX SKIP. Missing/generation rotation отказ, protected Windows DACL, exact IPC и injected metadata/read burst/cancel/SIGTERM проверены. Следующий приоритет: bounded temp/owner creation, cleanup/SIGKILL, actual projected Secret/CA/CRL; затем full release/home/CI. Synthetic filesystem fixtures не подтверждают реальную CSI, новый candidate дома ещё не развёрнут. Runtime рабочей ветки и docs mirror в main различать; replicas и target platform acceptance остаются OPEN.


Checkpoint 05.10.2026 — bounded temp/owner candidate: audit-v14-file-creation, packaged274/274; profiles100/network24/PG31/storage2 PASS без пропусков, native Windows127PASS/9POSIX SKIP. Creation и marker I/O перенесены в helper; незавершённый create/prepare/DNS/cleanup сохраняет exact owner и удерживает slot. Следующий приоритет: крайние cleanup/partial marker faults и parent SIGKILL/orphans, actual projected Secret CA/CRL; затем full release/home/CI. Домашний runtime не обновлён, rollout/replica gates не закрыты.


Checkpoint 05.10.2026 — cleanup edges/budget candidate: audit-v16-cleanup-budget, packaged274/274; required Linux profiles120/network24/PG31/storage2 PASS без пропусков, native Windows144PASS/12POSIX SKIP, выбранная stability10×26=260PASS/0SKIP. Marker/IPC/deadline/partial cleanup и controlled SIGKILL witness проверены. V15 cleanup timeout FAIL сохранён; общий cleanup .5s перераспределён .4work/.1reap без повышения общего лимита. Production orphan recovery остаётся OPEN. Следующий приоритет: actual projected passfile/CA/CRL/default/explicit/read-only/nonroot/fsGroup и writable private temp mount (chart пока без explicit /tmp emptyDir), container restart versus Pod delete; затем оставшиеся native/operational gates, полный release/home/CI. Домашний runtime не обновлён; replica/collector/platform gates не закрыты. [Proof](../reports/2026-10-04-audit-outbox-acceptance.md#cleanup-edges-и-budget-05102026--scoped-proof-release-open).


### Selected actual Secret/temp proof (05.10.2026)

На unchanged v16 выполнен отдельный домашний kind Pod proof: actual read-only projected Secret без subPath, UID/GID/fsGroup1000, read-only root/private memory emptyDir64Mi. Explicit/default paths, coherent CA/CRL rotation sampling, old snapshot hashes, CRL removal/reappearance и container-restart versus new-Pod temp lifetime PASS;274 running source hashes matched. Synthetic file bytes — не native TLS/CRL/auth proof. Direct0440 passfile сохраняет ignore semantics; test-only0600 staging принимает bytes, не реализует dynamic synchronizer. Product chart temp/projection/passfile configuration и operational orphan recovery, remaining profiles/full release/home app upgrade/CI OPEN. Работающее приложение не обновлено. [Отчёт](../reports/2026-10-04-audit-outbox-acceptance.md#actual-projected-secret-и-emptydir-05102026--selected-file-proof).

#### Product temp layout 05.10.2026 — selected package

Generic chart получил отдельные bounded disk emptyDir /tmp для backend/frontend,
TMPDIR/TEMP/TMP и ephemeral resources; backend contract наследуется writer Jobs,
legacy backup старым template сохранён. Начальные1Gi/256Mi — не measured sizing.
Password остаётся runtime Secret env. Продуктовый CA/CRL profile application/Jobs/
restore, native TLS/auth/revocation, disk-pressure и orphan ownership остаются OPEN.
App home v7 не обновлено, v16 app sources неизменны. [Приёмка и границы](../reports/2026-10-04-audit-outbox-acceptance.md#product-temporary-storage-05102026).

#### PostgreSQL CA/CRL: найденный prerequisite 05.10.2026

По исходникам bundled PostgreSQL ещё не включает TLS и server cert/key/HBA.
Preflight запрещает query в DATABASE_URL; Jobs не переносят PG trust identity.
pg_dump/pg_restore используют local socket внутри PG Pod и не подтверждают TCP
TLS. Поэтому клиентский CA/CRL mount сам по себе не является работающим профилем.

Подготовлен [проект полного opt-in bundled TLS](../specs/2026-10-05-kubernetes-postgres-tls-design.md):
server certificate/key и hostssl SCRAM, client verify-full и optional CRL,
immutable version-named Secrets, общий app/Job contract, schema3 operator kit
с явным PEM parser, backup format3 и legacy2=unknown с отдельным disabled adoption.
Не объявлять TLS обязательным внешним требованием; disabled сохраняет первый запуск.

Письменный дизайн исправлен при проверке; исполняемый план T1–T6 добавлен.
При реализации пройти последовательно operator validation → chart/server/Jobs →
backup/restore compatibility → valid native crypto/projected Secret proof →
полный одноразовый lifecycle и ресурсную приёмку. File-only v16, prior regular-file
native profiles и локальные render tests не закрывают эти новые gates.
Это проектирование; runtime/chart/toolbox/backup format не изменены этим пакетом.
Home upgrade, целевая платформа, реплики, orphan cleanup и dependency gates OPEN.

#### Проверка плана 05.10.2026 — приоритет и закрытые пробелы проектирования

Подробный [план PostgreSQL TLS](2026-10-05-kubernetes-postgres-tls.md) разделён на
T1 profile/kit, T2 chart/Jobs, T3 lifecycle/recovery, T4 backup/restore,
T5 native projected crypto proof и T6 full release. Это предстоящая реализация.

Opt-in TLS не блокирует первый disabled bundled запуск, если выбранная среда
не требует PG TLS; его собственные audit/backup/resources/dependency/CI gates
остаются открытыми до проверки. TLS release требует весь T1–T6. Неиспользуемые
native auth profiles не добавляются молча в поддерживаемую поставку: зафиксировать
поддерживаемую матрицу и ранний отказ остальных; наличие лабораторного profile
не означает его production support.

Уточнены: legacy backup2=unknown и explicit adoption; restore с истёкшей старой
PKI через привязанный trust transition; actual first initdb/VSO ordering;
immutable versioned HBA; новые Pod/connection proofs при rotation; checkpoint и
ручной recovery до/после migration; отсутствие automatic downgrade; exact schema2/
schema3 recovery kits; PG GSS/SCRAM/defaults; срок жизни ключей/CRL и реальные RPO/RTO.
Runtime в этой проверке не менялся; документирование не закрывает implementation
или platform gates. Предшествующие checkpoint ниже/выше — исторические результаты.
