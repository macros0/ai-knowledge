# Kubernetes replicas and storage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Метод исполнения выбирается отдельно; этот документ не запускает реализацию.

**Goal:** Независимо масштабировать frontend, API и обработчики документов в Kubernetes с local/RWX/S3, собственной, внешней и смешанной поставкой хранилищ и восстановлением после сбоя.

**Architecture:** Сохраняем compact; выделяем frontend, API и workers. PostgreSQL координирует задания и владение, существующие поколения документов расширяются до переносимого хранения. Обновление допускает остановку; устойчивость подтверждается испытаниями конкретного профиля.

**Tech Stack:** FastAPI, SQLAlchemy/Alembic, PostgreSQL 17, Qdrant (текущая закреплённая версия 1.19.0), Next.js из lock-файла проекта, Helm, Kubernetes/Gateway API; S3/RWX продукты выбираются в задаче P0 по платформенному контракту.

**Spec:** [Согласованный дизайн](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

Статус 31.10.2026: реализация начата в `codex/kubernetes-replicas`.
P0: инвентарь, platform schema и fail-closed acceptance entry point реализованы;
live probes и выбор storage providers ожидают подтверждённого disposable окружения.
P1: pure profile validator, Settings/preflight wiring и Helm schema/capability guards
реализованы; Helm 4.3.0 установлен локально с проверкой SHA256, render tests выполнены.
F1/F2: внутренний транспорт и frontend-split реализованы и проверены локально;
живой Kubernetes/Gateway smoke не выполнялся без разрешённого namespace/manifest.
S1: local/RWX/S3 adapters, factory/settings и общие контрактные тесты реализованы;
проверки SDK не заменяют приёмку реального provider/CSI. Общая миграция файлов не включена.
S2 preview: original binding+receipt фиксируются с документом атомарно; повторные
загрузки, verified download и восстановление pipeline-cache проверены локально.
Новый admission default-off и запрещён в production до S3/S4 retention/migration.
S3 foundation: leases/deletion claims/checkpoint CAS реализованы, проверены
на SQLite и изолированном PostgreSQL учебной VM103. Manifest v2 preview и cold
publication/source-download реализованы; полный перенос всех потребителей ещё
не завершён. CT102 оставлен stopped по запросу пользователя.
Python/npm advisory audits после исправлений не находят известных уязвимостей;
S2–S4, runtime/operations и окруженческие проверки остаются открытыми.
Ни P0, ни P1 целиком не объявляются завершёнными до их окруженческих проверок.

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
