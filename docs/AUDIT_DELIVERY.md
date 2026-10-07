# Аудит и повторная доставка в stdout

Реализация находится в ветке `codex/kubernetes-rollout`. Копия документа в основном checkout не означает наличия там runtime изменений. Перед поставкой сверять [приёмку](superpowers/reports/2026-10-04-audit-outbox-acceptance.md).

## Режим и обновление

`AUDIT_STDOUT_MODE=disabled` — default: журнал БД работает, outbox для новых событий не создаётся. `durable` записывает audit + неизменяемый event payload в одной SQL транзакции и включает один dispatcher. В Helm использовать `runtime.auditStdoutMode: durable`; ключ AUDIT_STDOUT_MODE в runtime.config управляется chart и отвергается. Jobs получают тот же env, но не запускают lifespan writer.

Новый backend требует additive audit schema и при выключенной доставке. Для versioned Compose/Kubernetes выполнить штатную Alembic migration при остановленном приложении. На существующей локальной DB с create_all drift: сохранить backup, остановить все backend writer и из backend выполнить `python scripts/migrate_audit_schema.py --runtime-env-file <private-runtime.env> --confirm-stopped`. Этот инструмент обновляет только проверенную audit schema; остальные migrations не stamp-ит. Fresh local DB создаётся штатным init_db. Unknown drift требует отдельной диагностики; приложение не стартовать с durable до успешной проверки схемы.

Прежние launchers и Compose bundled/external сохраняются. Для durable использовать `python -m app.diagnostic_entrypoint` с одним worker, без hot reload: access/error logs идут в stderr, audit JSON — в stdout. Компактная topology: одна реплика, Recreate, один общий DATA_DIR. OS lock защищает только процессы с этим DATA_DIR; несколько PVC/узлов с общей DB требуют будущего replica пакета.

## Гарантия и формат

Повтор относится к committed outbox row. Dispatcher пишет полную UTF-8 JSON строку, затем отмечает stdout_written отдельной транзакцией. При аварии до marker один и тот же event_id/time/payload может выйти повторно. stdout_written подтверждает локальную запись; внешний collector/index проверяется отдельно. Между разными событиями глобальный порядок commit не гарантируется.

Schema 1: event_id, UTC timestamp, action, actor/target snapshots, outcome, method/reason, server request_id и безопасные old/new aggregates. Payload не больше 16 KiB без framing newline. Заголовки, cookies, OAuth tokens/state/code, URL credentials, тексты документов/ошибок не копируются. Поля каждой операции описаны в [каталоге](AUDIT_EVENT_CATALOG.md). Базовый client_ip=null/ip_source=unknown; входящий X-Request-ID в durable заменяется общим UUID сервера для diagnostics/audit/response. Next сохраняет validated backend request ID в API response и completion diagnostics; при отсутствии/невалидном upstream ID или network failure остаётся proxy fallback. Trusted-IP relay относится к следующему пакету.

При short write повторный framing newline отделяет повреждённый хвост от полной строки. Blank/partial frame не считается event; CRI reassembly и collector parsing проверяются в окружении. Повреждённый/неизвестный payload остаётся held с reason invalid_payload, не блокирует остальные события и автоматически не пересобирается.

Login/logout/block/unblock в durable объединены с audit/outbox. Audit failure откатывает изменение и не выдаёт новую cookie. Denial остаётся 401/403 или прежним OIDC redirect; CSRF прямой ответ тоже журналируется один раз. Запись отказа имеет отдельные конечные DB timeouts и выполняется вне event loop. При недоступной DB сохранение denial не гарантируется: фиксируется bounded audit_unavailable в доступной диагностике/счётчике памяти, без блокирующего stdout в request path. Успешные health/auth-me polls не создают события отказа; запрещённый доступ к audit UI журналируется.

## Наблюдение и ёмкость

Из backend: `python scripts/audit_delivery_status.py --runtime-env-file <private-runtime.env>`. В operation Job можно опустить путь и использовать уже injected environment. Root .env не подхватывается. Команда показывает pending/held/stdout_written, oldest pending age и фиксированные error counts. Отдельная CLI возвращает runtime_state=unknown; она не видит память живого backend. DB failure даёт nonzero exit, не нулевую очередь.

In-process dispatcher.status показывает writer state и process counters. recorded/denied учитывают только root commit; rollback и отменённые savepoints их не увеличивают. db_failure считает неудачные DB cycles dispatcher, stdout_failure — ошибки sink, retry — успешно записанные повторные попытки. Отдельный denial failure_count/audit_unavailable не объявляется committed event. Счётчики обнуляются при restart. Автоматический мониторный endpoint/credential/CNI — следующий adapter. При длительном росте pending оператор проверяет stderr/диагностику, stdout collector, место и DB доступность; при held проверяет fixed reason. Не удалять pending, held или неподтверждённые downstream stdout_written для освобождения места. Ёмкость конечна: считать measured payload + DB/index overhead и скорость событий, резервировать свободное место. DB-full останавливает обязательные security mutations; HTTP health storage contract сохраняется, stalled writer не должен вызывать restart storm.

## Restore, rollback и переключение режима

Bundled Kubernetes/Compose restore выполняет hold restored pending до первого запуска backend. Команда не меняет audit fact, UUID/time/payload и идемпотентна. Ошибка hold оставляет backend остановленным. Fixed operator source работает в saved old image без импорта новых app models.

External/manual PostgreSQL или SQLite restore: остановить все backend процессы; восстановить в отдельную пустую DB/data directory; из проверенного operator набора выполнить `python scripts/audit_hold_restored_events.py --runtime-env-file <private-runtime.env> --confirm-hold-restored-events`; проверить counts и неизменность audit/outbox payload; только затем запускать. Приложение само не отличает ручную подмену DB от restart. Смена APP_SECRET_KEY и процедуры OIDC session/flow invalidation применяются по существующему restore runbook.

Для binary rollback: остановить writer, сохранить coherent backup, удержать pending с reason binary_rollback и установить disabled. Schema downgrade/stamp не требуется. Повторный upgrade не снимает held; исторический replay и retention выполняются отдельной согласованной процедурой. Ordinary restart durable повторяет pending. Переключение на disabled сохраняет очередь, останавливает enqueue/delivery; возврат к durable продолжает pending без backfill событий периода disabled.

Audit writer требует INSERT/SELECT audit_log и необходимых sequence прав, INSERT/SELECT outbox и UPDATE всех delivery columns (status, attempt_count, next_attempt_at, last_error_code, stdout_written_at); update/delete audit_log ему не нужны. Status account — SELECT, hold maintenance account — UPDATE outbox. Снимки identity в audit не меняются при удалении/переименовании пользователя.


### File-material candidate 05.10.2026 — scoped proof, release OPEN

Task3a.5 продолжен в локальном `audit-v11-files`, image `sha256:bde6a8d1b6e7bd133938a5719f6c9281fcc822f936d8ad5aa64422f3b933a4b7`. Подготовка defaults/passfile/CA/CRL выполняется отдельным owned helper; source file I/O не выполняется в denial DB worker. Все фазы используют исходный абсолютный WORK_SECONDS=4s; helper defaults/prepare ограничены min(1s, remaining budget), cleanup укладывается в общий предел0,5s, HTTP target<5s сохранён. IPC32KiB; passfile1MiB, CA/CRL по4MiB, максимум3 copied files/9MiB. Материалы принадлежат attempt до connection.close, источники не удаляются, новый attempt не использует cache.

Поддержаны в выбранном Linux proof: explicit/env/default HOME passfile, original hostname/port при hostaddr, escaped colon/backslash, password precedence/first-match, missing и Unix mode0644 ignore, default HOME CA, реальный SCRAM/SQL/commit/response→DB→stdout и Unix socket. FIFO/nonregular/oversize/обнаруженная rotation получают fixed unsupported-profile отказ до native connect; mTLS/client key/encrypted key, sslrootcert=system, sslcrldir и sslkeylogfile также отказывают заранее, TLS не ослабляется. Штатный TLS verify-full и отрицательные hostname/CA случаи сохранены.

Defaults helper использует опубликованный ABI PQconndefaults/PQconninfoFree из закреплённого psycopg_binary.libs без импорта всего Psycopg; проверяет libpq build и однозначность library, native defaults parity PASS. Четыре параллельных defaults на2CPU: первоначально четыре timeout1.003s, после устранения тяжёлого импорта0.255–0.260s; численные budgets/acceptance thresholds не повышались. SQLAlchemy adapters остаются в parent. Helper не импортирует app settings и не выполняет SQL; PG values передаются только в private stdin, не argv/inherited environment/logs.

SQLAlchemy NullPool может поглотить DBAPI close exception. Поэтому владелец denial attempt отдельно закрывает tracked connection и проверяет cleanup, сигнализируя failure ровно один раз. Incomplete cleanup quarantines slot; quarantined материалы не запускают второй helper. Реальная PG/NullPool regression подтвердила quarantine и сохранение уже committed immutable event после post-commit cleanup failure. Не утверждать rollback подтверждённого commit.

Свежие проверки этого snapshot: required Linux all **68 PASS/0 SKIP**, network/lifecycle **24 PASS/0 SKIP**, PG faults/capacity **31 PASS/0 SKIP**, whole PGDATA/WAL/cold-start **2 PASS/0 SKIP**. Native Windows focused **101 PASS/6 SKIP** (все6 — POSIX FIFO/default HOME cases); Psycopg3.3.6/libpq180004, основной venv не обновлён. Linux Psycopg3.3.6/libpq180006/SQLAlchemy2.1.3; **274/274 packaged source hashes matched**. Scoped Ruff PASS. Required profile runner сохраняет16 исходных cases; теперь19 PG profiles+14service/startup+8protocol+19file-material+8file-lifecycle=68; default all68, regular66 диагностический, blocked2. Неявные source files заменены attempt-private snapshot/missing paths до native connect.

Исходные13/3, service35/2, первый file snapshot Windows failure, network21/3 и22/2, а также NullPool/repeated-cleanup RED66/2 сохранены в private evidence. DNS instrument теперь идентифицирует только exact resolver helper: новые file children не считаются resolver и не могут преждевременно отметить фазу SIGTERM. HTTP<5s, max4, число bursts и fault thresholds не изменены. Owned контейнеры/network/socket volume удалены после проверки identities; runtime вне этих disposable fixtures не обновлён.

**Открыто:** реальный projected Secret/symlink-generation rotation и multi-file consistency; Windows default paths/protected temp ACL и proof actual helper interpreter reap; точные N/N+1/truncated/nonzero/blocked-stdin IPC edges; blocked regular filesystem metadata/read (тест stalled helper подтверждает kill/reap, не NFS/CSI); file-phase burst/cancel/SIGTERM и ресурсы/tmp recovery после SIGKILL; local temp creation/owner marker fault и cleanup deadline extremes; GSS/SSPI/LDAP/OpenSSL-provider profiles. mTLS/system trust/CRL-directory остаются scoped unsupported. Full backend/coverage/project/Compose и dependency gate этого snapshot, новый home projected-Secret/HTTPS/backup/restore и hosted CI **не выполнены**. Домашний audit-v7 не обновлён. Новый freeze не является commit/publication или полной release приёмкой. Составные C/C.1/D/E сохраняются OPEN; дизайн и ранний config/startup path реализованы в указанном объёме.


### File rotation/lifecycle candidate (05.10.2026)

V13 проверяет generation/missing input consistency; changed file set получает фиксированный отказ. Windows snapshot directory получает protected DACL до secret copy: только current token user/SYSTEM/Administrators; ошибки ACL не допускают fallback. На выбранном native Windows runtime подтверждены directory/copy ACL и actual interpreter reap. Required Linux profile86/network24/PG31/storage2 PASS, native114PASS/8POSIX SKIP. Synthetic projected layout и injected metadata/read faults не являются proof реальных Kubernetes mounts/NFS/CSI. Parent temp/marker creation, крайние cleanup faults/SIGKILL, actual CA/CRL rotation и новый full release/home/hosted CI остаются OPEN. Подробности: [отчёт](superpowers/reports/2026-10-04-audit-outbox-acceptance.md#file-rotation-и-lifecycle-05102026--scoped-proof-release-open).


### Bounded temp/owner candidate (05.10.2026)

V14 создаёт private directory/owner только в bounded helper; parent не делает temp filesystem probes/marker writes. Выбранный temp root должен быть writable: Unix TMPDIR/TEMP/TMP или/tmp, Windows TEMP/TMP илиSYSTEMROOT/Temp; silent fallback отсутствует. Missing directory подтверждает очистку, markerless/foreign directory не удаляется. Incomplete create/prepare/DNS/cleanup сохраняет owner в private process quarantine registry и удерживает slot без retry. Parent SIGKILL уничтожает registry, operational orphan cleanup требует отдельного proof. Локально profiles100/network24/PG31/storage2 PASS, native127PASS/9POSIX SKIP; actual Secret/NFS/CSI, remaining cleanup edges и full release/home/CI OPEN. [Отчёт](superpowers/reports/2026-10-04-audit-outbox-acceptance.md#bounded-temp-creation-и-cleanup-ownership-05102026--scoped-proof-release-open).


### Cleanup edge candidate (05.10.2026)

V16: exact regular32-byte owner guard, bounded marker/unlink/rmdir faults, нулевой абсолютный deadline и IPC close failure проверены. Неуспешная очистка сохраняет owner/slot; после удаления marker и failed rmdir каталог остаётся markerless/quarantined, автоматического retry/marker repair нет. Общий cleanup≤.5s сохранён, рабочая/reap доля .4/.1s (v15 .25/.25 приводила к раннему timeout в нагрузке); меньший reap reserve не отменяет quarantine при неполном завершении. Work4s/helper1s/HTTP<5s/admission4 прежние.

Controlled parent SIGKILL/external ownership witness — fixture-only, не product orphan recovery. Не очищать общий Temp по prefix/age: private registry не переживает parent death. Требуются separate ownership/lifetime/authorization proof и actual Kubernetes Secret/private temp tests, включая emptyDir container restart versus Pod delete. Scoped Linux120/network24/PG31/storage2, native144PASS/12POSIX SKIP и selected10×26PASS подтверждены; full release/home/hosted CI, actual CSI/NFS и remaining profiles OPEN. Домашний v7 не обновлён. [Отчёт](superpowers/reports/2026-10-04-audit-outbox-acceptance.md#cleanup-edges-и-budget-05102026--scoped-proof-release-open).


### Selected actual Secret/temp proof (05.10.2026)

На unchanged v16 выполнен отдельный домашний kind Pod proof: actual read-only projected Secret без subPath, UID/GID/fsGroup1000, read-only root/private memory emptyDir64Mi. Explicit/default paths, coherent CA/CRL rotation sampling, old snapshot hashes, CRL removal/reappearance и container-restart versus new-Pod temp lifetime PASS;274 running source hashes matched. Synthetic file bytes — не native TLS/CRL/auth proof. Direct0440 passfile сохраняет ignore semantics; test-only0600 staging принимает bytes, не реализует dynamic synchronizer. Product chart temp/projection/passfile configuration и operational orphan recovery, remaining profiles/full release/home app upgrade/CI OPEN. Работающее приложение не обновлено. [Отчёт](superpowers/reports/2026-10-04-audit-outbox-acceptance.md#actual-projected-secret-и-emptydir-05102026--selected-file-proof).


Lab runner выполняется на Linux host с явно выбранным разрешённым lab context,
заранее загруженным frozen backend image и manifest его packaged Python hashes
(`files`: relative app/scripts/alembic paths → SHA256). Нужны kubectl и права
создать/удалить только отдельный тестовый namespace. Runner не обновляет Helm
release, не использует работающие DB/Secrets и не выполняет SQL/TLS handshake.

```bash
python3 scripts/kubernetes/tests/audit_secret_acceptance.py \
  --context "$LAB_CONTEXT" --backend-image "$FROZEN_BACKEND_IMAGE" \
  --source-manifest "$PACKAGED_SOURCE_MANIFEST" \
  --evidence-directory "$NEW_PRIVATE_EVIDENCE_DIRECTORY"
```

Evidence directory должен быть новым; mode0700/umask077 устанавливаются runner.
PASS выдаётся только после всех выбранных проверок и удаления captured UID-owned
resources. Ошибка ownership не разрешает удалять заменённый/чужой ресурс;
неподтверждённые остатки требуют отдельного осмотра, а не общего namespace sweep.
