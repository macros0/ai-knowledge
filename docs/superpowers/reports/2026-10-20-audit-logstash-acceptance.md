# Приёмка: audit/outbox и Logstash на базе main

20.10.2026. Ветка `codex/audit-logstash`, без commit/push.
Execution checkout: `C:/Users/alexey/.codex/worktrees/audit-logstash/ai-knowledge`.
База: `origin/main` = `17d94c6a0724b809309e4fc48de4b1ae1059b4c6`, подтверждена fetch.
Изменения находятся в рабочем дереве; SHA базы не описывает новые файлы.

## Реализовано

- Выборочно перенесено application-ядро аудита и прямые bounded denial helpers.
  Старые Kubernetes lifecycle/TLS/restore/private candidates не переносились.
- Новый additive Alembic head `066b1c2d3e4f` после `065b1c2d3e4f`.
  Legacy rows сохраняются без backfill; модели и функции текущего main сохранены.
- Каталог: 84 действия, включая семь действий нового main и четыре auth-события.
  Добавлены четыре RU/EN подписи, штатные ui_keys/ui_en перегенерированы.
- Audit/outbox атомарны; security login/logout/block/unblock связаны с ними.
  Сохранён best-effort logout disabled. Domain boundaries за пределами audit
  не объявляются автоматически атомарными.
- Single dispatcher, validation, OS lock, full/partial writes, retry/backoff,
  marker, hold/status/schema-repair CLI. Startup отказ выполняет cleanup.
- Native/Logstash selectable; private Python logger + python-json-logger 4.2.0,
  strict handler, детерминированная оболочка, исходные ID/time/request correlation.
- Узкие mode/format настройки существующего chart, .env.example и самостоятельный runbook.

## Подтверждённые результаты

Наборы пересекаются; числа нельзя складывать в общее количество cases.

| Проверка | Результат | Граница |
|---|---|---|
| Исходные audit/auth/authz main | 99 PASS | До переноса, новый runtime из lock |
| Native core/schema/store/dispatcher/security/hold/CLI | 44 PASS | Изолированные SQLite DB |
| Первые Logstash contract/dispatcher tests | 20 PASS | Байт-в-байт повтор, поля/типы, logger exceptions |
| Helm renders + прежние chart tests + Logstash/1000 events | 31 PASS | Helm 4.3.0, SHA256 скачанного бинарника проверен; кластер не запускался |
| Actual app subprocess + review regression + SSO | 27 PASS | Оба stdout формата, auth→DB→stdout, request ID, startup cleanup |
| PG audit/denial/service профиль, первоначальный | 42 PASS / 3 FAIL / 3 SKIP | Отказы guard order сохранены, ниже исправление |
| Guard order после исправления | 3 PASS / 11 deselected | Service profile отвергается до defaults/DB connect |
| Actor/request context + полный прежний test_audit после RU/EN | 26 PASS | Все четыре новых audit action имеют переводы |
| Fresh SQLite Alembic и SQLite/PG legacy upgrade | PASS | Тесты test_audit_main_migration; отдельный PG на 25432 |
| PG/fresh/legacy + 1000-event final tests | 11 PASS / 3 POSIX SKIP / 15 deselected | audit-pg-final.log; отдельный PG на 25432 |
| Fresh PostgreSQL: вся цепочка Alembic main→066 | PASS | Новая `audit_test_logstash_fresh`, portable PostgreSQL17 |
| Project gate | PASS | npm audit 0, ESLint, frontend 658 PASS/1 SKIP, Ruff |
| Python runtime lock audit | PASS | pip-audit: No known vulnerabilities found |
| pip check | PASS | No broken requirements found |
| Последний focused audit suite | 63 PASS | audit-focused-final.log, после review/actor/labels |
| Полный backend suite | 4671 PASS / 93 SKIP / 4 FAIL | audit-full-final2.log, 2050.51s; причины и исправления ниже |
| Связанный повтор четырёх failures | 64 PASS / 7 platform SKIP | audit-final-replay.log, штатный backend cwd, после совместимого default flag |

1000-event measurements: native 32.969s / max payload 377 bytes; Logstash 32.766s / max payload 624 bytes.
Это isolated test sink при параллельном regression load, не production SLA.

Логи находятся в `tests/tmp/audit-*.log`, `tests/tmp/logstash-*.log` этого checkout.
Конечный перечень donor hashes/решений — собственный execution ledger в
`.superpowers/sdd/2026-10-20-audit-logstash/`; runtime эти файлы не использует.

## Независимый review и причинные исправления

Один fresh reviewer изучил uncommitted tracked/untracked код. Critical не найдено.
Два Important: disabled logout стал fail-closed; preflight/lock выполнялся после
старта mutating services вне cleanup. Оба подтверждены двумя RED tests и исправлены;
27 related tests прошли. Сохранены audit-review-red.log и audit-review-green.log.

Отдельные PG tests выявили порядок schema connect раньше service-file refusal.
Отказ теперь предшествует DB I/O; три соответствующих tests прошли. Исторический
3 FAIL не заменён исходным зелёным числом без записи причины.

При проверке переноса найдено отсутствие переводов новых auth action; добавлены
подписи RU/EN и сгенерированы словари, 26 tests прошли. Начальный параллельный
collection дал 3 errors из-за случайного UUID в param ID старого diagnostics test.
Названия четырёх случаев стабилизированы, данные/assertions сохранены.
Первый последовательный suite остановлен после review-исправлений и не является
полным результатом. Development RED/fixture failures сохранены в исходных логах.

## Полный regression: точный результат и исправления

Полный набор запущен в четырёх pytest workers из корня checkout:
4671 PASS / 93 SKIP / 4 FAIL. Сохранён исходный FAIL, не выдаётся за зелёный full suite.

- `test_diagnostics_bundle::test_zip_worker_import_does_not_load_sql_metadata_stack`
  и `test_parser_supervisor::test_windows_job_kills_worker_and_descendant_when_supervisor_dies`:
  дочерние процессы наследовали корневой cwd и не находили `app`/`tests`.
  Штатный cwd для backend pytest — `backend`. Повтор этих двух tests оттуда дал PASS
  без изменения их проверок или supervisor кода.
- Два параметра `test_glossary_initial_startup` (development/production) выявили
  новый доступ к отсутствующему optional flag в частичных Settings. Исправлено:
  `getattr(settings, "audit_stdout_mode", "disabled")`. Реальные Settings сохраняют
  строгое Literal-поле; устаревшие duck-typed adapters получают прежний disabled default.
- Связанные glossary/startup/bundle/parser/audit/SSO contracts повторены из backend:
  **64 PASS / 7 platform SKIP**, 96.73s. Логи audit-final-failures.log (2 PASS/2 FAIL до
  исправления) и audit-final-replay.log (после) сохранены.

После этих локальных исправлений весь большой набор заново не запускался.
Неразобранных failures в просмотренном прогоне не осталось; вывод о новом полностью
зелёном full suite не делается. Правильная команда дальнейшей общей проверки:

```powershell
# cwd: backend; runtime/devtools из этой ветки
.venv/Scripts/python.exe -m pytest tests -n 4 --dist worksteal -q --basetemp=../tests/tmp/pytest-runs/audit-full
```

## Незакрытые проверки среды

- На исходной Windows-машине Docker daemon был недоступен. Дополнительный
  разрешённый прогон на Proxmox VM103 закрыл Linux image/POSIX proofs: 114 PASS/2 SKIP,
  отдельный PostgreSQL run12 PASS и Docker startup regression RED1→GREEN1.
  Исправлен HOME при setpriv. Фактическая доставка1013/1013 в домашний OpenSearch,
  outage recovery и replay dedup подтверждены в [отдельном отчёте](2026-10-20-audit-logstash-linux-opensearch.md).
  Это Linux/home proof; требования целевой платформы оцениваются отдельно.
- OpenSearch/collector целевого окружения не предоставлены. AC-10 OPEN:
  parser/mapping, CRI fragments/blank frames, размер, INFO filtering, права,
  retention/dedup и фактическое появление event_id должны проверяться там.
- Дополнительные SCRAM/TLS/projected Secret/NFS/CSI профили не приняты обычным
  portable PG17 тестом. Старые отчёты донора не переносят их статус в эту ветку.

## Сохранность и использование

Основной checkout/main и основная DB/корпус не менялись. Donor worktree сохранён.
Собственный PG-кластер живёт только в tests/tmp/audit-pg-data на 127.0.0.1:25432;
после проверок он штатно остановлен через pg_ctl; тестовые данные оставлены как артефакт.
Не запускались commit, push, deployment, миграция основной БД или старые Kubernetes jobs.

Перед поставкой: backup, stopped writers, штатная migration нового audit schema,
выбор mode/format. Подробности: [AUDIT_DELIVERY](../../AUDIT_DELIVERY.md).
Форматный rollback возвращает native; binary rollback сохраняет additive schema,
удерживает pending и использует disabled. Исторический replay не включается.


## Дополнительный Linux/OpenSearch прогон

[Отчёт Proxmox](2026-10-20-audit-logstash-linux-opensearch.md) содержит image IDs,
RED/GREEN реального HOME дефекта, Linux/PG tests,1013 документов индекса,
сверку всех канонических полей и повторную доставку без дублей.
AC-10 заказчика сохраняет OPEN; данные домашнего индекса не являются его приёмкой.
