# Приёмка трёх режимов аудита

21.10.2026. Ветка `codex/audit-storage-modes`, база `c1b321f`.
Worktree: `C:/Users/alexey/.codex/worktrees/audit-storage-modes/ai-knowledge`.
Статус: реализация и проверки завершены. Финальный полный backend: 4775 PASS / 74 SKIP, failures=0, errors=0; XML проверен.

## Реализовано

- DB-only (default), external-only и both; запрет полного отключения в Settings/Helm.
- Независимый SQL outbox v2: собственные id/event_id/created_at, без FK к журналу.
- Общая маршрутизация, одинаковый security-аудит, атомарные выбранные получатели,
  server request ID во всех режимах; processing-scope больше не обходит outbox.
- Рабочие snapshots стоп-слов и receipts миграций глоссария сохраняют history/rollback
  и идемпотентность независимо от постоянного журнала.
- Замороженная миграция 066, миграция 067, dev-инструмент, строгий startup/hold,
  сохранение payload/state/history IDs и transactional repair PostgreSQL sequences.
- Security-only no-store API режима, RU/EN UI, старые записи/CSV/блокировки,
  Helm/env и SECURITY/runbook/README.
- Pending при выключении stdout сохраняются; нет GC, backfill или ACK OpenSearch.

## Подтверждённые проверки

Наборы пересекаются; их размеры не складываются в общее количество тестов.

| Набор | Результат | Граница |
|---|---|---|
| Baseline audit/config/schema | 22 PASS | До реализации, изолированные SQLite |
| Core store/event/dispatcher/auth | 44 PASS | Первый интеграционный прогон |
| Modes/API/preflight/request context/profile | 53 PASS | До последних дополнительных cases |
| Final modes + PostgreSQL minimum selected-sink privileges | 33 PASS / 3 platform SKIP | PostgreSQL только disposable на 25432 |
| Schema/migration/restore final | 37 PASS | SQLite + PostgreSQL, v1→v2, strict drift, late rollback, sequence rollback |
| Domain broad | 112 PASS | API/CAS/история/receipts |
| Domain final new file | 18 PASS | Включая historic ID540 → v2 → external history/rollback |
| Frontend focused/i18n | 44 PASS | Behavioral component harness |
| Helm storage modes + existing chart | 29 PASS | Настоящий helm render, без deployment |
| Real process mode/format matrix + audit meta endpoints | 11 PASS | Startup/auth/JSON, все три режима × оба формата |
| Project gate | PASS | npm audit0, ESLint no errors, frontend673 PASS/1 SKIP, Ruff |
| Production frontend build | PASS | `node node_modules/next/dist/bin/next build --webpack` |
| Final Ruff | PASS | Полное backend дерево |
| Scope current replay | 172 PASS / 3 cached historical FAIL | Три migration cases повторены отдельно |
| Historical mail/scope + current head/downgrade guard | 15 PASS / 7 SKIP | Полный upgrade и запрет downgrade сохранены |
| First full backend | 4726 PASS / 74 SKIP / 33 FAIL | Запущен до последних правок; raw log сохранён |
| Final fresh full backend | 4775 PASS / 74 SKIP, failures=0/errors=0 | 1302.88s; неизменяемое дерево, 8 test-workers; приложение single-worker |

Команды и артефакты:
- `tests/tmp/{audit-core,audit-modes-extra,modes-final,mode-process-final-2,project-check,frontend-build,ruff-final}.log`.
- `backend/tests/tmp/schema-final-3.log`; точные domain команды/результаты перечислены в worker report.
- Worker reports и ledger: `.superpowers/sdd/2026-10-21-audit-storage-modes/` (локальные ignored артефакты).
- Python worktree повторно использует существующие зависимости через junction. Для full regression использован уже установленный audit-logstash runtime с pytest-xdist; `pip check` PASS, текущий doc-parser задан через PYTHONPATH. Пакеты не устанавливались/не обновлялись.

## Независимый review и исправления

Fresh reviewer выявил два воспроизводимых дефекта, оба исправлены:

1. P1: business request_id scope-операции — Hex32, его нельзя передавать как UUID
   audit request_id. Regression с настоящим Hex32 сначала дал AuditEventError;
   теперь серверный контекст отвечает за audit correlation, бизнес-ID остаётся в
   локальных meta. Профильный final modes прогон прошёл.
2. P2: лишний UNIQUE(status) пропускался validator-ом. Cases для constraint/index
   сначала подтвердили дефект, затем прошли после строгого отказа неизвестных
   ограничений. Замороженная v1-семантика не изменена.

Дополнительно PG regression выявил nontransactional setval для ранее существующей
sequence. Использован owned schema-qualified ALTER SEQUENCE RESTART; late rollback
теперь сохраняет sequence. Финальный schema набор 37 PASS включает этот случай.

Исторические отказы не скрываются:
- Aggregate audit: 203 PASS / 7 SKIP / 1 FAIL — старое ожидание users-directory не
  учитывало новый обязательный login audit в DB-only. Обновлено с точным count=1;
  audit meta endpoints повторены в 11-PASS прогоне.
- Расширенная process matrix сначала дала 6 FAIL: test ожидал только 3 total rows,
  хотя startup законно создаёт 4 glossary_rule_create. Исправлено ожидание auth subset
  из 3 событий, сохранив external-only log=0 и равенство event_id обеих копий в both.
- Старый alias downgrade case теперь останавливается на своей alias revision;
  fresh-head chain проверяется отдельно, v2 downgrade по-прежнему запрещён.
- Первый вызов full suite на общем venv не стартовал из-за отсутствия xdist;
  зависимости не менялись, выбран существующий совместимый test runtime.

Первый полный прогон завершился за 2198.76s. Все 33 отказа классифицированы:
1 users-directory, 2 mail roundtrip, 3 scope downgrade и 27 старых cached Hex32
scope путей. Scope свежего процесса: 172 PASS, 3 отказа исторического fixture затем
закрыты отдельным свежим прогоном 15 PASS/7 SKIP. Финальный полный прогон выполнен
после остановки всех изменений исходников: 4775 PASS / 74 SKIP за 1302.88s,
exit code=0, XML tests=4849/errors=0/failures=0/skipped=74. Результаты:
`tests/tmp/audit-full-final.log` и `tests/tmp/audit-full-final.xml`, отдельно от
первого красного прогона. Значения timeout/порогов не менялись.

## Browser → API → БД → stdout

Настоящий production frontend preview на `127.0.0.1:16401`, отдельный backend
на 18101 с simulation identity `audit-security`, отдельная SQLite и stub внешнего
vector provider (никаких данных основного корпуса). В браузере проверены RU/EN:

- «Только внешний канал» / «External only»;
- сообщение о сохранённой локальной истории, отсутствие ложного подтверждения
  OpenSearch, локальный смысл CSV/агрегатов и доступность блока блокировок;
- пустая локальная история;
- DOM clientWidth=506 и scrollWidth=506, горизонтального переполнения нет.

После browser login: AuditLog count=0; outbox 7 stdout_written (startup 4 + auth/login/
access events). Все 7 event_id очереди совпали с настоящими Logstash JSON frames
backend stdout; application=ai-knowledge. Это подтверждение локального stdout,
не внешнего индекса. Затем в preview вручную добавлена одна синтетическая legacy-строка id 901:
после reload внешний режим сохранился, историческая строка/агрегат показаны, CSV
доступен; новых AuditLog приложение не создало. Screenshot артефакты: `tests/tmp/audit-preview/external-{ru,en}.png`.

## Средовые границы

Основная PostgreSQL 5432, PG10 и пользовательский Qdrant/корпус для проверок не
использовались. PostgreSQL тестов создан только в `tests/tmp/audit-pg-data`, порт 25432,
БД `audit_test_storage_modes`, отдельные random schemas. Основной runtime env не
менялся и миграция пользовательской БД не выполнялась. Push/merge/deploy не выполнялись.

Целевой collector/OpenSearch не предоставлен: parser/mapping/fragment handling,
права, buffering/retry/retention и фактическая доставка нового v2 остаются отдельной
приёмкой. Старые отчёты v1 не доказывают v2. Автоматической очистки нет: внешний
режим убирает копию постоянного журнала, но SQL-очередь продолжает занимать место.
Старый binary не совместим со схемой 067; перед rollout нужен backup и остановка writers.

Полный regression не является целевой collector/OpenSearch приёмкой. Известные предупреждения: Authlib/Starlette/Pydantic deprecations, isolated Qdrant version probing и SQLite datetime adapter; это не failures.

74 SKIP включают: 33 tests без REFERENCE_TEST_POSTGRES_URL, 7 без
MAIL_MIGRATION_POSTGRES_ADMIN_URL, POSIX-only process proofs, отдельные private
PostgreSQL endpoints и real ENOSPC storage profile. Audit-specific PostgreSQL
migration/sequence/selected-rights проверки выполнены на disposable 25432, а не skipped.
Оба preview-сервиса и временный PostgreSQL остановлены после приёмки; данные и
логи тестов сохранены в ignored tests/tmp. Основной checkout сохранён без изменений
исходников: только ранее существовавшие pyproject.toml и untracked draft spec.

Повторный обязательный pre-commit project gate: PASS (`tests/tmp/project-check-final.log`), npm audit0, frontend673 PASS/1 SKIP, ESLint и Ruff без ошибок. Ветка сохраняется отдельным worktree для review; локальный commit оформляется после этих проверок.
