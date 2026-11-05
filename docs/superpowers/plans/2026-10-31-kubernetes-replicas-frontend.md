# Independent frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track steps with checkboxes.

**Goal:** Две независимые frontend-реплики без общей файловой связи с backend.
**Architecture:** Сначала один combined backend; frontend получает diagnostic control и отправляет санитизированные события по внутреннему API. Пользовательский Gateway продолжает указывать frontend Service.
**Tech Stack:** Next.js текущего lock, FastAPI, Helm/Gateway API.
**Spec:** [Согласованный дизайн](../specs/2026-10-31-kubernetes-replicas-storage-design.md).

Статус 31.10.2026: F1 реализован и проверен локально; отчёт
[diagnostic transport](../reports/2026-10-31-replica-diagnostic-transport-progress.md).
F2: frontend-split реализован локально; production/multi-Pod приёмка остаётся открытой.
Deployment backend сохраняет имя `application` для совместимости lifecycle;
в split mode он содержит только backend, новый Deployment `frontend` независим.
Это инженерное уточнение имени `backend-combined` из исходного плана, без смены роли.

04.11.2026: [выбранная F2-репетиция](../reports/2026-11-04-replica-frontend-split.json)
проверила две frontend и один combined backend на текущих неизменных образах.
Исправлена общая compact NetworkPolicy, разрешавшая frontend доступ к storage:
семь новых регрессий RED, затем34 chart checks GREEN; CNI подтвердил6 реальных
PG/Q/model отказов с TLS/model controls backend. Сессия, один upload/одна
публикация, search/download SHA, ZIP и NDJSON через обе frontend прошли.
Удаление выбранного Pod по UID при model-active=1 оборвало текущий stream;
survivor/replacement/backend сохранили сессию и данные, вызов модели не повторился.
Logout отозвал старую cookie на трёх маршрутах. Стенд выключен,3 PVC сохранены;
обе capacity loans возвращены с исходными2/2/2/runtime/PVC/9 таблицами.
На момент этой репетиции общий frontend probe проверял inventory/ownership/builds,
а functional producers оставались pending. Её focused cohort:134 pass,
0 skip. Реальный Gateway/SSO/CSRF не проверен: simulation исключён из production
CSRF middleware. Первые ошибки входов/dry-run сохранены в отчёте отдельно.

05.11.2026: [свежий общий native runner](../reports/2026-11-05-frontend-native-acceptance.json)
прошёл все 12 этапов на e42007a: shared session/upload/publication/export, два
NDJSON потока, model-gated UID loss, replacement, logout и холодная проверка.
Действия записаны до RPC; readiness наблюдалась с исходными intents, без повтора
POST/DELETE. Пять процессов закрыты, fixture выключен/3 PVC сохранены; CI и обе
live capacity loans возвращены с проверкой 2/2/2/runtime/PVC/9 таблиц. Полный Linux
CI:6638 passed/221 прежний optional skip/0 errors/failures, coverage87,70%.
Это закрывает выбранный functional producer, сохраняя реальные CSRF/Gateway/SSO
и полную F2/profile qualification открытыми.

Следующий F2 prerequisite теперь [допущен и закрыт](../reports/2026-11-05-replica-gateway-platform.json):
пользователь отдельно разрешил Gateway API1.6.2/kgateway2.4.5.18 CRD Established,
controller Ready на pinned image/non-root1000/read-only/caps0, GatewayClass Accepted;
controller затем выключен, capacity loan возвращён. Созданный им OAuth2 HMAC
Secret сохранён в его namespace, значения не включены в публичный отчёт.
[Отдельный OIDC HTTP-компонент](../reports/2026-11-05-frontend-oidc-http-ci.json)
прошёл99/99 одинаковых Windows/Linux cases с wire double и настоящим TLS.
Последующая native/common-runner интеграция и
[свежий real-Keycloak/Gateway прогон](../reports/2026-11-05-frontend-oidc-native-acceptance.json)
на fdfae89 прошли все12 этапов с production CSRF middleware: SSO, оба CSRF-отказа,
два frontend/Service, upload/publication/search/SHA/ZIP, streaming, UID loss при
активной модели без replay, replacement и logout. Полный Linux7003 cases:
6782passed/221unchanged optionalSkip/0failures/errors/87,70% coverage. Fixture
выключен/8 controllers0/3handlesclosed/3PVCretained; обе исходные2/2/2/runtime/PVC/
9 таблиц возвращены. Выбранная F2 basic/local recipe проверена; customer
Gateway/IdP, остальные bindings и доверенный release certificate остаются O3 gates.
Старые simulation/component reports сохраняют свои первоначальные ограничения.

## Global Constraints

Все ограничения и правила тестов [основного плана](2026-10-31-kubernetes-replicas.md)
обязательны. До правок Next читать локальную документацию из
`frontend/node_modules/next/dist/docs/` согласно `frontend/AGENTS.md`.
Не добавлять общий Next cache без подтверждённого использования сохраняемого cache.

## Review Focus

Истёкший capture; произвольный payload от поддельного frontend; недоступный collector;
различные cookies/builds на репликах; оборванный SSE. Эти случаи покрывают F1/F2.

## Task F1: внутренний транспорт диагностики

**Files:** создать `backend/app/api/internal_diagnostics.py`,
`backend/app/services/diagnostics/ingest.py`, `frontend/src/lib/diagnosticTransport.mjs`,
`backend/tests/test_internal_diagnostics.py`, `frontend/test/diagnosticTransport.test.mjs`;
создать `backend/alembic/versions/rpl00_diagnostic_ingest.py`;
изменить `backend/app/main.py`, `backend/app/services/diagnostics/control.py`,
`backend/app/services/diagnostics/store.py`, `frontend/src/lib/diagnosticServer.mjs`,
`frontend/src/instrumentation.js`, `backend/app/config.py`, `backend/app/db/models.py`, `SECURITY.md`.

**Interfaces:** внутренние `GET /internal/diagnostics/control` и
`POST /internal/diagnostics/events`, не через публичный catch-all proxy. Auth:
отдельный Secret `DIAGNOSTICS_INGEST_TOKEN`, header `Authorization: Bearer`;
token не попадает в browser bundle. NetworkPolicy frontend→backend, маршрут не
публикуется Gateway. Control сохраняет existing policy/version/deadline semantics.
Batch `{instance_id, batch_id, events}`: max 100 events / 256 KiB, timeout 2 s,
frontend memory queue max 1000 events. Это стартовые инженерные ограничения;
oversize — 413, schema/unknown event — 422, неверный token — 401.

`ingest_events(batch: DiagnosticBatch) -> IngestReceipt` в ingest.py;
Pydantic DTO определены там: receipt `{accepted: int, duplicate: bool}`.
Повтор batch ID не удваивает события; dedup receipt живёт не меньше существующего
diagnostic retention. Для текущего single backend receipt сохраняется в БД;
`DiagnosticIngestBatch` содержит unique `(instance_id, batch_id)`, validated payload,
receipt и delivery status; повтор ID с другим payload — 409. Получение batch и receipt
атомарно через DB inbox, запись сегментов имеет stable
event IDs. R4 переносит обработку inbox на распределённый runtime.
`createDiagnosticTransport({baseUrl, token, instanceId, fetchImpl, now})` возвращает
`getControl()`, `sendBatch(batch)`, `close()`; browser reporter API сохраняется.

- [ ] Добавить `test_ingest_requires_internal_identity`, `test_batch_limits_and_schema`, `test_duplicate_batch_id`, `test_expired_control_fails_closed`. JS: timeout/queue-full не блокирует SSR, токен не появляется в serialized status. Assertions: rejected events не сохранены, один ID даёт один event, expired capture не разрешает detailed capture, SSR завершается при collector timeout.
- [ ] RED: `python -m pytest tests/test_internal_diagnostics.py -q`; из frontend `node --test test/diagnosticTransport.test.mjs`.
- [ ] Реализовать transport/inbox, использовать существующие sanitizers/allowlist. Local diagnostic spool разрешён, потеря незагруженных событий отражается coverage gap; он не является security audit. Потеря сети не включает более подробный capture. Compact файловый транспорт остаётся совместимым.
- [ ] GREEN обе команды и существующие `python -m pytest tests/test_diagnostics_control_gates.py -q`; frontend `node --test test/diagnosticTransport.test.mjs`. Проверить реальный POST через frontend proxy не открывает `/internal`.
- [ ] Commit `feat: decouple frontend diagnostic transport`.

## Task F2: frontend-split chart и проверка через Gateway

**Files:** создать `deploy/helm/ai-knowledge/templates/frontend.yaml`,
`deploy/helm/ai-knowledge/templates/backend-combined.yaml`,
`deploy/kubernetes/profiles/frontend-split.values.example.yaml`,
`backend/tests/test_frontend_split_chart.py`;
изменить `templates/application.yaml`, `templates/services.yaml`, `templates/network.yaml`,
`templates/config.yaml`, `values.yaml`, `values.schema.json`,
`scripts/kubernetes/lifecycle.py`, `backend/test_scripts/run_replica_acceptance.py`,
`docs/KUBERNETES_DEPLOYMENT.md`, `SECURITY.md`.

**Interfaces:** frontend-split: Deployments `frontend` (2) и `backend-combined` (1),
Service `frontend:3000`, `backend:8000`. `BACKEND_URL=http://backend:8000`,
`APP_ROLE=combined`, `UVICORN_WORKERS=1`; только backend монтирует data/diagnostics PVC.
Frontend имеет writable scratch без связи с backend filesystem. Pod UID — instance ID.
Образы одного revision/digest; `Recreate` для каждого Deployment допустим.
Lifecycle знает оба workload и сохраняет intended replica counts в maintenance receipt.

- [ ] Тесты `test_frontend_split_has_no_shared_mount`, `test_combined_stays_single`, `test_legacy_compact_render_unchanged`, `test_lifecycle_restores_exact_counts`. Assertions: frontend volumeMounts не содержат data/control; backend replicas=1; Service endpoints выбирают правильный role; counts после maintenance равны исходным.
- [ ] RED: `python -m pytest tests/test_frontend_split_chart.py tests/test_kubernetes_chart.py -q` с Helm.
- [ ] Добавить templates/schema/capability, readiness/startup без кругового обращения frontend→Service→frontend; разнести frontend по доступным узлам, не вводить cluster-wide права приложению. Не расширять gateway route на internal API. Проверить существующие Server Actions/cache в коде и local Next docs; сохранить один build и записать найденные требования в runbook.
- [ ] GREEN chart tests; из frontend `node node_modules/next/dist/bin/next build`. Runner `--suite frontend`: вход/выход SSO, CSRF, SSR, загрузка, streaming, остановка одной frontend-реплики и доступ ко второй. Оборванный текущий stream допустим, новые запросы работают, повторная генерация не запускается автоматически.
- [ ] Commit `feat: deploy independent frontend replicas`.

## Ключевые assertions для тестов задач

F1: `assert response.status_code == 401` без internal token;
`assert persisted_event_count == 1` после duplicate batch;
`assert expired_control_allows_capture is False`; `assert response.status_code == 413`
для превышения 256 KiB. Эти значения проверяются через реальные transport/policy APIs.
F2: `assert backend["spec"]["replicas"] == 1`;
`assert restored_counts == original_counts`; `assert "/data" not in frontend_mount_paths`.
