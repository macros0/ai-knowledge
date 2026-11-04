# Kubernetes replicas and storage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Метод исполнения выбирается отдельно; этот документ не запускает реализацию.

**Goal:** Независимо масштабировать frontend, API и обработчики документов в Kubernetes с local/RWX/S3, собственной, внешней и смешанной поставкой хранилищ и восстановлением после сбоя.

**Architecture:** Сохраняем compact; выделяем frontend, API и workers. PostgreSQL координирует задания и владение, существующие поколения документов расширяются до переносимого хранения. Обновление допускает остановку; устойчивость подтверждается испытаниями конкретного профиля.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL 17, Qdrant (текущая закреплённая версия 1.19.0), Next.js из lock-файла проекта, Helm, Kubernetes/Gateway API; S3/RWX продукты выбираются в задаче P0 по платформенному контракту.

**Spec:** [Согласованный дизайн](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

Статус 05.11.2026: реализация продолжается в `codex/kubernetes-replicas`.
P0/P1, frontend split, local/RWX/S3 adapters, перенос файловых потребителей,
поиск orphan-объектов/безопасный GC и verified/resumable offline-миграция корпуса
реализованы. Миграция не удаляет исходник и не требует реиндексации;
[runbook](../../ARTIFACT_STORAGE_MIGRATION.md). Поддержка конкретного provider/CSI
по-прежнему требует собственной приёмки; SDK/unit tests её не заменяют.

Runtime ledger/fencing/публикация, API/worker lifecycle и общая координация
реализованы. На изолированном managed-S3 учебном профиле фактически проверены
2 frontend + 2 API + 2 worker, прерванная публикация/отмена, потеря worker,
PostgreSQL partition с возвращением старого процесса и небольшая нагрузка.
Проверены независимая копия на отдельном ПК и восстановление в новый target,
ограниченные credentials, согласованная смена CA и заполнение отдельного
ограниченного S3 filesystem. Дополнительный
[application-full прогон](../reports/2026-11-04-replica-s3-application-full.json)
на65d6ee2 подтвердил путь приложения:503 без приёма загрузки, старое поколение
сохранено и та же работа автоматически завершена на попытке3/fence3 после75с
освобождения собственного128Mi tmpfs. Это не проверка production-диска или PG/Q.
Подробные измерения, версии и ограничения сохранены в связанных runtime/operations
планах и `docs/superpowers/reports/2026-11-01-replica-*.json`/`2026-11-02-*.json`.

Сводные результаты и границы доказательств:
[матрица приёмки](../../REPLICA_ACCEPTANCE_MATRIX.md).

[Общий frontend native runner](../reports/2026-11-05-frontend-native-acceptance.json)
на e42007a прошёл все 12 этапов свежего basic frontend-split fixture, включая
model-active UID loss без автоматического повтора генерации. Сессия, оригинал,
поиск и ZIP пережили replacement; logout, cold SHA и закрытие пяти процессов
проверены. Новый fixture выключен, три PVC сохранены; все три capacity loans
возвращены с исходными 2/2/2/runtime/PVC/9 таблицами. Полный текущий Linux CI:
6859 cases,6638 passed,221 неизменный optional skip,0 failures/errors,87,70%
coverage; Windows focused170 passed/1 Linux-only skip. Default frontend probe
остаётся read-only. Реальные CSRF/Gateway/SSO и доверенный release bundle
остаются открытыми; certification=null, production scheduleOFF.

Связанный runtime-прогон R5 учебного managed-S3 профиля завершён:22/22 этапа,
0 failures,0 required skips,80 прежних документов сохранены, maintenance
epoch15→16→17 с двумя проверенными восстановительными операциями. Выполнены
масштабирование, cancel/delete, общий лимит LLM, потеря worker, PostgreSQL
partition/возврат устаревшего процесса и нагрузка1/2 workers.
[Результат](../reports/2026-11-02-replica-common-runtime.json).
Эта приёмка не распространяется на другие providers и физические домены отказа.

Открыты три крупных блока, частично выполненные:

- **O1:** RWX independent restore, Synology256MiB write/read,150с application outage
  и логический перенос между двумя узлами VM103 уже проверены;
  bounded S3 application full-storage и selected all-external S3 runtime/recovery
  также закрыты. Остаются текущая PostgreSQL/Qdrant/model interruption matrix,
  квалификация конкретных customer providers, длительных/неопределённых отказов
  и физической инфраструктуры/failure domains.
  [Двухузловой результат](../reports/2026-11-04-replica-crossnode-vm103.json) сохраняет
  первый неуспешный автоподхват и отдельный успешный повтор после исправлений DNS/PG.
- **O2:** compact/NULL-generation, managed-S3, selected RWX target и NAS/all-external
  source→S3 independent recovery проверены. Остаётся явно выбранный NAS-target
  NFSv4.0 relocation recipe, остальные выбранные customer adoption/update recipes.
  Восстановление ключа из
  отдельного защищённого каталога и доставка alerts локальному приёмнику на ПК
  проверены на учебной копии. Production-расписание выключено; production
  monitoring и независимый физический key escrow не квалифицированы.
- **O3:** объединённая fault/profile matrix, квалификация текущего runtime/провайдеров,
  реальный CSRF/Gateway/SSO producer и доверенный release issuer. Default frontend
  probe read-only; выбранный native producer закрыт новым 12-stage case. Единственное ревью всей ветки,
  единый проход пяти исправлений и полный ccfcf9e CI уже завершены.

05.11 inventory audit: [семь вариантов](../../../deploy/kubernetes/replicas/support-matrix.json)
сверены с неизменными SHA-отчётами. Шесть имеют ограниченные учебные доказательства
(`tested`), resilient остаётся planned. Устаревшие gates о непроверенных compact/
RWX/mixed restore, S3 application-full и полном backend CI заменены конкретными
незакрытыми проверками. Это инвентарь разных bindings, не общий сертификат.

O3 частичная проверка текущих источников:557/557 doc-parser tests,0 skips и новая
standalone frontend production build с19 routes и проверенными health rewrite/
revision stamp. [Artifact CI](../reports/2026-11-02-replica-current-artifact-ci.json).
Созданы [машиночитаемая матрица семи профилей](../../../deploy/kubernetes/replicas/support-matrix.json)
и [acceptance runbook](../../KUBERNETES_REPLICAS_ACCEPTANCE.md); во всех профилях
certification=null. Чистая policy проверена47 тестами. Проверка подписанных
файлов receipts и read-only режим runner реализованы; реальные доверенные
issuers/producers ещё не подключены.
[Policy validation](../reports/2026-11-02-replica-certification-policy.json).

Полный Linux backend CI исторического `5f5c43d` подтверждён:6610 точных case IDs,
6390 passed,220 skipped,0 failures/errors,87,62% покрытия при пороге85%.
Пропуски:203 opt-in PostgreSQL,12 для других ОС,5 real ENOSPC opt-in; отдельные
нативные/provider испытания остаются самостоятельными gates. Собран новый runtime
из точных Git-байтов и проверены572 файла образа, Alembic head и fingerprint.
[Текущий CI и образ](../reports/2026-11-03-replica-full-linux-backend-ci.json).
Дополнительный полный Windows-прогон дал6381 passes/228 skips и один отказ
bootstrap dry-run: запускной скрипт задал внешний SQLite `DATABASE_URL`,
перекрывший PostgreSQL URL тестового env-файла. Тот же тест прошёл после удаления
только этой переменной при неизменных остальных входах и всех2418 исходниках.
Первоначальный полный Windows-прогон сохранён как неуспешный; чистый полный
Windows-повтор не выполнялся.
[Windows CI и причинная проверка](../reports/2026-11-03-replica-windows-backend-ci.json).
Этот результат относится к указанной revision; последующие изменения кода требуют
соответствующей повторной проверки. Прогон базы `e3c7025` сохранён отдельно:
6093 случая,5910 passed,183 skipped,0 failures/errors, точные case IDs обоих shard.
[Baseline CI](../reports/2026-11-02-replica-baseline-backend-ci.json).
Предыдущий ENOSPC-прогон сохранён как неуспешный;
[восстановление ёмкости CI](../reports/2026-11-02-replica-lab-ci-capacity-recovery.json).
На учебном стенде
проверено обновление backend после scoped credentials/CA: прежние 19 документов
и сессия сохранены, новый документ имеет одну чистую публикацию, 2/2/2 Ready
без рестартов. Образ `dc3534a` включает исправление diagnostic prefix; его
развёртывание подтверждено новой общей identity и digest контейнеров.
Перед обновлением независимо скопированы 19 документов/95 blobs; эту новую
копию отдельно не восстанавливали. После обновления сделана новая независимая копия
20 документов/100 blobs; проверены сохранность исходного корпуса после restart,
backup point epoch11 и настоящий журнал попытки со статусом complete.
Последняя20-document копия фактически восстановлена в новый namespace
`replica-test-r5o2u20261102` с отдельными PVC при полностью недоступном источнике.
Проверены100 blob SHA,40 Qdrant points и20 строк документов до release;
19 действующих оригиналов проходят поиск/download SHA,1 документ остаётся в
корзине. Новые scoped SQL/S3 principals, offline operator projection, смена
identity и отзыв25 старых сессий проверены до запуска2/2/2. После release
прошли7 runtime smoke checks и2 новые загрузки. Источник с96 документами
остановлен, его PVC сохранены. Новый target не наследует22-stage R5 приёмку
источника; это самостоятельная проверка восстановления и smoke.
[Current-copy recovery](../reports/2026-11-02-replica-current-copy-recovery.json).
Production-расписание backup выключено по решению пользователя.
Для следующего schema-changing испытания отдельно поднят настоящий старый
backend из `21f5a6d`:498 файлов образа сверены с Git, выполнена его реальная
Alembic-цепочка до `rpl04_runtime_identity`. В новом namespace с отдельными PVC
проверены scoped SQL/S3,2/2/2 Ready без рестартов, общая сессия на двух API и
upload→publish→search→download SHA для2 DOCX без problem-кодов.
[Старый источник](../reports/2026-11-02-replica-predecessor-live-source.json).
Из этого источника фактически получена независимая зашифрованная копия:
PostgreSQL/Qdrant/10 blobs,12 объектов архива,2 документа/369 заданий.
[Копия rpl04](../reports/2026-11-02-replica-predecessor-backup.json).
После проверки копии источник полностью отключён с сохранением3 PVC.
Копия фактически импортирована в новый namespace с отдельными3 PVC:
2 документа/369 заданий,4 точки Qdrant и10 blob SHA проверены при выключенном
источнике. [Импорт rpl04](../reports/2026-11-02-replica-predecessor-provider-import.json).
На завершении отдельного этапа импорта target сохранял старые schema/identity/S4-token,
все приложения0.
Проверки импорта: Windows100 passed; Linux174 passed, включая11 native PostgreSQL,
0 failures/errors/skips. [Валидация импорта](../reports/2026-11-02-replica-predecessor-import-validation.json).
Последующий [schema-changing recovery](../reports/2026-11-02-replica-predecessor-schema-recovery.json)
фактически выполнен: настоящий Alembic rpl04→rpl05, удержание epoch1→2,
новая identity/scoped SQL/S3 и отзыв2 старых сессий до запуска. Проверены9
неизменных таблиц корпуса,369 work rows,10 blob SHA и4 точки с векторами.
Старый образ отверг новую schema в read-only проверке транзакционного guard
при совпадающих фактических identity/storage. После явного release2 все2/2/2
Ready без рестартов,2 старых документа проходят поиск/download SHA,2 новые
загрузки завершены без problem-кодов;7 runtime smoke checks passed.
Это восстановление с обновлением в новый target, не in-place обновление старого
namespace, не compact/NULL-generation adoption и не полная R5 приёмка нового образа.
CT102 остановлен; рабочий корпус и сервисы заказчика не используются.
Одноузловой kind не подтверждает устойчивость к потере физического узла;
Gateway/SSO, реальные model providers и инфраструктура заказчика остаются
отдельными окруженческими gates. Ни весь проект, ни resilient-профиль не завершены.

## Global Constraints

- «Простои не являются существенным ограничением. Бесшовное обновление и переключение не требуются.»
- «При полной потере рабочего хранилища допустимо восстановление из ночной копии с потерей изменений за последний день (ориентир RPO — 24 часа).»
- «SQLite сохраняется для локальной разработки; распределённая очередь требует PostgreSQL.»
- «Local-only файлы не разрешают distributed multi-node.»
- «Исполнение at-least-once; повтор LLM-вызова возможен.» Видимая публикация должна быть идемпотентной.
- Не ослаблять auth/CSRF/audit, TLS verification, retention и защиту удаления. Затронутые разделы `SECURITY.md` обновляются в той же задаче.
- Старые Compose `STORAGE_MODE=bundled|external`, host-порты и compact остаются совместимыми; новая схема включается явно.
- `UVICORN_WORKERS=1` во всех Pod. Масштабируем Pod по ролям; Redis, HPA и rolling upgrade не добавляем автоматически.
- Тесты не используют корневой `.env`, рабочую БД/индекс или настоящие модельные endpoints. Windows npm/npx shims не использовать.
- Внутренние адреса, credentials и данные заказчика не помещать в Git. Условия инфраструктуры проверять в разрешённом тестовом namespace.

## Review Focus

1. Старый worker вернулся после потери lease: не меняет канонические строки/активные файлы/индекс — R1, R2.
2. Ответ upload потерян после commit: повтор не создаёт второй документ/job; ошибка storage не даёт успешного ACK — S2, R2.
3. Delete/GC пересекается с чтением или поздним Qdrant upsert: активные данные защищены, старое поколение не появляется в поиске — S3, R2, R4.
4. Разделение процессов теряет аудит/diagnostic control или позволяет устаревшие права — F1, R4.
5. Ночная копия частична, стара или содержит исполняемые production jobs: не считается пригодной, restore изолирован — O2, O3.

## Разбиение и порядок

Один план на всю переработку был бы плохо проверяемым. Ниже четыре связанных
пакета с собственными интерфейсами, файлами и приёмкой. Выполнять последовательно
по зависимостям; инфраструктурный выбор P0 выполняется до зависимых от него работ.

| Пакет | Задачи | Результат и зависимости |
|---|---|---|
| Основание (этот файл) | P0–P1 | Контракт платформы, инвентарь состояния и безопасная schema профилей |
| [Frontend](2026-10-31-kubernetes-replicas-frontend.md) | F1–F2 | Диагностика по сети, 2 frontend + 1 combined backend; после P1 |
| [Файлы и поколения](2026-10-31-kubernetes-replicas-storage.md) | S1–S4 | Local/RWX/S3, публикация, миграция; S1 после P1, реальные provider tests после P0 |
| [API и workers](2026-10-31-kubernetes-replicas-runtime.md) | R1–R5 | Очередь, fencing, перенос фоновых задач, 2 API + 2 workers; R1 после P1, R2 после S3, R3 после S4 |
| [Хранилища и эксплуатация](2026-10-31-kubernetes-replicas-operations.md) | O1–O3 | Наши storage packages, external/mixed, backup/restore и fault tests; O1 после P0/P1, O2 после R5 |

Критический путь: P0/P1 → O1 + S1–S4 → R1–R5 → O2/O3.
Frontend — самостоятельный ранний результат. Сначала ручное число реплик.
Переход к следующему профилю только после его gate; нельзя заранее снять общий
single-process guard. Существующие данные не мигрировать до проверенного backup.

## Общие соглашения исполнения и проверки

Полные пути — от корня репозитория. Сокращённые `services/...`, `api/...`, `app/...`
в backend-перечнях означают `backend/app/services/`, `backend/app/api/`, `backend/app/`.
Одиночные generation/staging/pipeline файлы относятся к `backend/app/services/`;
`templates/...`, `values.yaml` и `values.schema.json` — к application chart
`deploy/helm/ai-knowledge/`, если storage chart не назван явно.
Python-команды выполнять из `backend/`,
Node-команды из `frontend/`, если явно не указан корень. До коммита каждой задачи:
`node scripts/check-project.mjs` из корня и её профильные проверки. Коммит содержит
только файлы задачи; имеющиеся чужие изменения не включать.

Каждая Alembic revision создаётся в своей задаче с `down_revision` фактического
head. Не менять уже применённые миграции прежних задач; проверять upgrade на
настоящей непустой БД, а не только create_all.

Каждая задача: сначала перечисленные regression tests и их RED по ожидаемой
причине, затем реализация, затем GREEN. Не считать отсутствие Helm/PG/provider
успешной проверкой: для обязательного integration job отсутствие зависимости — FAIL.
Существующая базовая pytest fixture использует SQLite: PG-межпроцессные тесты
должны иметь отдельный harness и явный URL disposable БД.

Добавляем общий `backend/test_scripts/run_replica_acceptance.py` в P0: CLI
`--suite {contracts,frontend,storage,runtime,operations,all} --manifest PATH`.
Manifest с секретами находится вне Git. CLI проверяет, что БД начинается с
`replica_test_`, namespace имеет метку `okf-purpose=replica-test`, Qdrant collection
и bucket prefix относятся к run ID; иначе прекращает работу до mutation.
Отчёт JSON: `suite`, `run_id`, `checks`, `passed`, `failed`, `skipped_required`,
`profile`, `revision`, `durations`, `recovery_point_age_seconds`; secrets редактируются.
Код 0 только при passed всех обязательных checks и `skipped_required=0`.
Команды этого runner ниже — будущие команды, не заявление об уже имеющемся инструменте.

## Task P0: проверяемые входы и инвентарь состояния

**Files:** создать `docs/REPLICA_PLATFORM_CONTRACT.md`, `docs/REPLICA_STATE_INVENTORY.md`,
`deploy/kubernetes/replicas/platform.schema.json`, `backend/test_scripts/run_replica_acceptance.py`,
`backend/tests/test_replica_acceptance_safety.py`; читать текущие services, chart и lifecycle.

**Interfaces:** `platform.schema.json` описывает `schema_version=1`, domains/nodes,
storage classes и reattach, quotas, operator permissions, registry/CA, backup
destination ownership, model limits. Для каждого собственного PG/Qdrant/S3/RWX:
`implementation`, `version`, `image_digests`, `license`, `install_scope`, `storage_requirements`,
`failure_model`, `backup_restore_recipe`, `maintenance_owner`. Секреты — references.
Output: заполненная окруженческая копия, обезличенная матрица поддержанных профилей
и runner report; отсутствие входа отмечается unsupported с причиной, не default.

- [ ] Выписать в инвентарь каждый writer/background/cache: точный модуль, тип состояния, owner, механизм отмены/повтора и задача переноса. Обязательно включить diagnostics, audit, selective processing, translations, backfills, downloads, auth и startup recovery.
- [ ] Добавить `test_rejects_live_targets_before_mutation`, `test_required_skip_is_failure`, `test_report_redacts_secrets`. Assertions: `mutations == []`, `exit_code != 0`, `secret not in report`; проверить RED/GREEN командой `python -m pytest tests/test_replica_acceptance_safety.py -q`.
- [ ] Реализовать runner/manifest validator и stage preflight без автоматической установки cluster-scoped компонентов. Исполнитель выбирает по одной реализации собственного S3/RWX и PostgreSQL management по критериям спецификации; фиксирует точные версии/лицензии/recipes, а не generic «любой S3». Другой provider потребует новой contract-приёмки.
- [ ] На disposable namespace проверить запись/повторное подключение PVC, разнесение по реальным failure domains, registry/CA и независимость backup destination. Неполученные права оформлять как gate конкретного профиля; остальные задачи приложения могут продолжаться.
- [ ] Запустить `python test_scripts/run_replica_acceptance.py --suite contracts --manifest <private-manifest>`; PASS означает все prerequisites выбранного профиля подтверждены. Сохранить санитизированный отчёт, затем commit `docs: define replica platform and state contracts`.

## Task P1: новая schema без изменения старых режимов

**Files:** создать `backend/app/deployment/replica_profile.py`, `backend/tests/test_replica_profile.py`;
изменить `backend/app/config.py`, `backend/app/deployment/storage_mode.py`,
`backend/scripts/validate_storage_mode.py`, `deploy/helm/ai-knowledge/values.yaml`,
`values.schema.json`, `templates/config.yaml`, `templates/_helpers.tpl`, `SECURITY.md`.

**Interfaces:** новый opt-in `OKF_DEPLOYMENT_SCHEMA=replicas-v1`; отсутствие сохраняет
старый validator. `APP_ROLE=combined|api|worker`, `ARTIFACT_BACKEND=local|rwx|s3`,
`POSTGRES_PROVIDER=managed|external`, `QDRANT_PROVIDER=managed|external`,
`ARTIFACT_PROVIDER=managed|external`. Старый `STORAGE_MODE` не применяется для
replicas-v1; одновременное задание противоречащих старых/новых параметров — ошибка.

Helm: `deployment.mode=compact|frontend-split|distributed`,
`replicaStorage.postgres.provider`, `.qdrant.provider`, `.artifacts.provider`,
`.artifacts.backend`, `resilience.class=basic|resilient`,
`frontend.replicas`, `api.replicas`, `worker.replicas`; compact продолжает пользоваться
старыми values. Проверка `validate_replica_profile(profile: ReplicaProfile) -> None`;
`ReplicaProfile` — frozen dataclass со всеми перечисленными полями, `database_dialect`,
`shared_artifacts_verified: bool`, `storage_certification: str | None`.

- [ ] Тесты `test_legacy_profiles_unchanged`, `test_distributed_local_rejected`, `test_sqlite_worker_rejected`, `test_mixed_providers_allowed`, `test_resilient_requires_certification`. Assertions: legacy результаты совпадают; опасные сочетания вызывают `ReplicaProfileError`; смешанный профиль проходит при валидных inputs.
- [ ] RED: `python -m pytest tests/test_replica_profile.py tests/test_storage_mode.py -q`.
- [ ] Реализовать pure validator, настройки и новую schema. До готовности соответствующих задач frontend-split/distributed отклоняются runtime/chart capability guard; capability задаётся сборкой/кодом, не обходным env пользователя. Resilience certification привязана к topology/provider/version manifest, не произвольной строке «HA=true».
- [ ] GREEN той же командой; `python -m pytest tests/test_kubernetes_chart.py -q` с установленным Helm сохраняет compact render. Новый профиль ещё не разрешает запуск нескольких старых backend.
- [ ] Commit `feat: validate opt-in replica deployment profiles`.

## Условия готовности и дальнейшее исполнение

- [ ] Все задачи ниже завершены с профильными checks, compact regression и обязательным общим check-project.
- [ ] Непустой реальный corpus проходит upload → parse → publish → search → download → export → delete/restore на RWX и S3.
- [ ] Наши/external/mixed профили имеют отдельные отчёты, выбранные версии и ограничения; результат home-кластера не объявляется приёмкой заказчика.
- [ ] Потеря Pod/узла, partition и restore подтверждены; измерены throughput и recovery time. Модельные stub tests не доказывают качество/ёмкость настоящей LLM.
- [ ] Пользователь получает runbook, список prerequisites, итоговую support matrix и измеренный размер ресурсов. Обещание RPO основано на проверенной пригодной копии.

Рекомендуемое исполнение — последовательно небольшими PR с отдельной проверкой
гонок/публикации и эксплуатации. Можно использовать subagent-driven для границ
задач либо выполнять inline; одновременно менять общие модели/publication/chart
независимым исполнителям нельзя. Реализация требует отдельного пользовательского
задания; настоящий запрос покрывает составление плана.

## Самопроверка и ключевые assertions

Проверено 31.10.2026: стадии 0–7 покрыты P0/P1, F1/F2, S1–S4, R1–R5 и O1–O3.
Все пять Review Focus имеют regression cases; используем existing generations,
scope leases и outbox. Проверены интерфейсы и ссылки между пакетами. Это проверка
документа, не выполнение тестов будущего кода.

P0: `assert mutations == []`; `assert required_skip_exit_code != 0`;
`assert secret not in report`. P1: `assert validate_replica_profile(valid_mixed) is None`;
опасные профили проверять через `pytest.raises(ReplicaProfileError)`.

03.11.2026: завершена [compact source-off репетиция](../reports/2026-11-03-replica-compact-independent-restore.json): независимое восстановление/S4 crash-resume/held handoff/adoption и функциональные2/2/2, accepted checkpoint, экспорт/frozen bundle, невыполнение approval и новая публикация на b5a9775. Затем закрыт [единый проход пяти замечаний](../reports/2026-11-03-replica-final-review-fixes.json): ccfcf9e full Linux6431 passes/221 explicit optional skips/0fail/87,67% coverage, неизменные2429 Git-файлов. Новый точный runtime собран и импортирован. Новая отдельная CI capacity-операция вернула прежние2/2/2 с теми же runtime/PVC и девятью неизменными каноническими таблицами. Compact target остаётся остановлен под held epoch3; PVC/backup сохранены. Отдельно разрешённый [NFS/CSI bootstrap](../reports/2026-11-03-replica-nfs-rwx-bootstrap.json) прошёл двух-Pod adapter contract; RWX application/restore/cross-node и остальные O1/O2/O3 gates остаются открытыми, certifications=null.

03.11.2026 RWX: [текущий application run](../reports/2026-11-03-replica-rwx-application.json) прошёл2/2/2, загрузку/публикацию/поиск/скачивание/export, отдельный реальный ENOSPC507 с неизменным корпусом и повтором после освобождения места, нормальный NFS restart. Первый большой буферизованный client write завис в kernel reclaim/COMMIT и остаётся failed/unqualified; после контролируемого восстановления холодная проверка сохранила исходные данные. Финальные11 blob SHA/два audit/три WorkItems проверены на четырёх процессах. Target сохранён выключенным под maintenance epoch1; прежние реплики восстановлены. Independent RWX restore/large-write/outage/cross-node и остальные O1/O2/O3 gates остаются открытыми.

03.11.2026 O2 RWX: [независимая format3 копия](../reports/2026-11-03-replica-rwx-independent-backup.json) сохранена и холодно проверена на отдельном ПК:13 encrypted objects,81 таблица/28 sequences, четыре полных вектора и11 blob. Ключ остаётся на ПК; physical source binding аутентифицирован manifest. Source app/storage остановлены под held1, прежние2/2/2/PVC/девять таблиц восстановлены. На этом backup-only этапе RWX restore оставался открыт, rebind был S3-only; последующая строгая physical proof и завершённая репетиция приведены ниже.

03.11.2026 O2: [RWX-source→новая реальная S3-цель](../reports/2026-11-03-replica-rwx-source-s3-restore.json)
восстановлена из независимой копии при физически выключенных source NFS/PG/Qdrant/app.
До смены identity совпали81 таблица/28 sequences, четыре полные точки и11 blobs.
Штатные held2/release, old-cookie401, прежний корпус/export, новая загрузка/export
и2/2/2 прошли; четыре процесса проверили17 SHA, пустой scratch и отсутствие
повторного выполнения старых задач. Target сохранён выключенным под held3; NFS
и прежние2/2/2/runtime/PVC/девять таблиц восстановлены. Это закрывает выбранный
RWX-source→S3-target рецепт; последующая проверка свежего RWX target приведена ниже. Failed large-write/outage/cross-node/customer gates открыты,
production scheduleOFF, certificationnull.

03.11.2026 O2: [независимый restore в свежую RWX-цель](../reports/2026-11-03-replica-rwx-target-restore.json)
завершён на cf74f8a после полного Linux CI6466 passes/221 прежний optional skip.
Manifest-bound source и фактический новый NFS export/inode проверены; до adoption
совпали81 таблица/28 sequences, четыре точки и11 blobs. Held2/release, реальный
exit23 после resumed SQL commit до output и повторный CLI с109 неизменными
записями,2/2/2 old/new HTTP и четыре native17 SHA/scratch0/задачи однократно прошли.
Target физически выключен/held3/три PVC сохранены, прежние2/2/2/runtime/PVC и
девять таблиц восстановлены. Общий NFS-сервер работал, исходный claim не был
смонтирован. Large-write/outage/cross-node/external/mixed и общие O1–O3 gates
остаются открытыми; certificationnull, production scheduleOFF.

04.11.2026: [mixed Synology runtime](../reports/2026-11-04-replica-synology-mixed-runtime.json)
прошёл2/2/2 с DS211 DSM6.2.4/NFSv4.0/CSI4.13.6 и managed PG/Q, реальную
пользовательскую EDQUOT507, сохранение старых чтений и retry. Текущий96931d1
прошёл6472 Linux tests/221 неизменный optional skip, включая шесть readiness
regressions. [Независимая NAS-копия](../reports/2026-11-04-replica-synology-independent-backup.json)
на ПК и [NAS-source→S3 restore](../reports/2026-11-04-replica-synology-source-s3-restore.json)
проверили81 таблицу/28 sequences/четыре полные точки/11 blobs до adoption;
held2/release/old-cookie401/старый и новый HTTP-корпус/экспорты прошли2/2/2.
Четыре процесса проверили17 SHA/scratch0/отсутствие replay старых работ и
недоступность NAS2049 при включённом NAS. Target сохранён выключенным/held3,
три PVC оставлены; прежние2/2/2/runtime/PVC/девять таблиц восстановлены.
Длительные outage/cross-node, NAS-target relocation и остальные customer/provider
gates открыты; итоговые O1–O3 не объявлены завершёнными, production scheduleOFF.

04.11.2026: отдельный [NAS component outage](../reports/2026-11-04-replica-synology-bounded-outage.json)
проверил150с node→NAS2049 block, native DROP и ожидающие read/write через136,6с,
естественное истечение timed set и cold SHA после чтения182,9с/записи183,1с в том
же процессе. Первый delayed-control прогон и import-only failed Job сохранены.
Оба прогона закрыты, временные nft-таблицы/свои два файла удалены, CRI/mount0,
PVC и три Job оставлены. Прежние2/2/2/runtime/PVC/девять таблиц восстановлены.
Это компонентный результат; полный application/worker case закрыт следующим
прогоном, физические отказы остаются отдельным gate. ScheduleOFF/certificationnull.

04.11.2026: [полный NAS application outage](../reports/2026-11-04-replica-synology-application-outage.json)
на текущем96931d1 подтвердил принятую/принадлежащую worker задачу и активную
модель-заглушку до150с node→NAS block. Публикация ожидала, liveness200/readiness
timeout, старые концепты неизменны и рестартов0. К194,2с наблюдалось чистое
завершение той же задачи/attempt1 с публикацией того же candidate. Сессия,
старый/новый SHA и поиск, прежний ZIP, same-key retry и новый экспорт прошли
четыре HTTP-маршрута;12 cold SHA/scratch0 проверены четырьмя процессами.
Новый стенд выключен под held1/три PVC сохранены, nft/forwards закрыты, все loans
возвращены, прежние2/2/2/runtime/PVC/девять таблиц и provider data сверены.
Выбранный150с application gate закрыт; межузловые/физические, более длительные
отказы и полная квалификация профилей остаются открытыми. Пользователь выбрал
VM103 для следующего [двухузлового учебного стенда](2026-11-04-replica-crossnode-vm103.md);
точный platform-пакет расширения диска и нового кластера подготовлен,43 объекта
прошли client schema dry-run. Применение ожидает отдельного platform-допуска;
текущий кластер не изменён. Production scheduleOFF/certificationnull.
