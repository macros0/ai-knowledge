# Kubernetes replicas and storage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Метод исполнения выбирается отдельно; этот документ не запускает реализацию.

**Goal:** Независимо масштабировать frontend, API и обработчики документов в Kubernetes с local/RWX/S3, собственной, внешней и смешанной поставкой хранилищ и восстановлением после сбоя.

**Architecture:** Сохраняем compact; выделяем frontend, API и workers. PostgreSQL координирует задания и владение, существующие поколения документов расширяются до переносимого хранения. Обновление допускает остановку; устойчивость подтверждается испытаниями конкретного профиля.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL 17, Qdrant (текущая закреплённая версия 1.19.0), Next.js из lock-файла проекта, Helm, Kubernetes/Gateway API; S3/RWX продукты выбираются в задаче P0 по платформенному контракту.

**Spec:** [Согласованный дизайн](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

Статус 02.11.2026: реализация продолжается в `codex/kubernetes-replicas`.
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
ограниченного S3 filesystem. Последнее подтверждает контракт S3-компонента,
а не весь путь публикации приложения при заполнении production-диска.
Подробные измерения, версии и ограничения сохранены в связанных runtime/operations
планах и `docs/superpowers/reports/2026-11-01-replica-*.json`/`2026-11-02-*.json`.

Сводные результаты и границы доказательств:
[матрица приёмки](../../REPLICA_ACCEPTANCE_MATRIX.md).

Связанный runtime-прогон R5 учебного managed-S3 профиля завершён:22/22 этапа,
0 failures,0 required skips,80 прежних документов сохранены, maintenance
epoch15→16→17 с двумя проверенными восстановительными операциями. Выполнены
масштабирование, cancel/delete, общий лимит LLM, потеря worker, PostgreSQL
partition/возврат устаревшего процесса и нагрузка1/2 workers.
[Результат](../reports/2026-11-02-replica-common-runtime.json).
Эта приёмка не распространяется на другие providers и физические домены отказа.

Открыты три крупных блока, частично выполненные:

- **O1:** RWX, external/mixed end-to-end, заполнение хранилищ на уровне приложения
  и квалификация инфраструктуры/failure domains выбранных профилей.
- **O2:** schema-changing update/adoption существующих установок, key recovery,
  доставка backup-error/age alerts в мониторинг и оставшиеся profile-specific
  backup/restore recipes. Локальный cold-check и журнал попыток реализованы;
  production-расписание и доставка уведомлений ещё не включены.
- **O3:** полный CI итоговой версии, объединённая fault/profile matrix, итоговые
  runbooks и свежее ревью всей ветки.

Полный backend CI итоговой версии ещё не подтверждён. Прогон базы `e3c7025`
завершён:6093 случая,5910 passed,183 skipped,0 failures/errors; оба shard имеют
точное совпадение собранных и выполненных case IDs. Последующие исправления
snapshot/capacity/scoped restore проверены собственными Windows/Linux cohorts.
Это не заменяет полный CI финальной версии.
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
Target пока сохраняет старые schema/identity/S4-token, все приложения0.
Проверки импорта: Windows100 passed; Linux174 passed, включая11 native PostgreSQL,
0 failures/errors/skips. [Валидация импорта](../reports/2026-11-02-replica-predecessor-import-validation.json).
Переход Kubernetes на rpl05, новая identity, отзыв старой авторизации и запуск
с функциональной приёмкой ещё не выполнены.
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
