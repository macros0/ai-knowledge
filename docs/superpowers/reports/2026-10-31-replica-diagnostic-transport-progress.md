# F1 — Внутренний транспорт диагностики frontend

31.10.2026, ветка `codex/kubernetes-replicas`. Рабочая реализация, без изменения
живого кластера и без применения миграции к рабочей БД. F2 не включён.

## Реализовано

- Внутренние `/internal/diagnostics/control` и `/internal/diagnostics/events` с
  отдельным bearer token; без токена серверный endpoint отключён. API не входит
  в публичную OpenAPI schema. Новый token исключён из repr/model_dump Settings.
- Санитизация по существующему allowlist, только frontend/server events;
  100 events, 256 KiB, generic errors без исходного payload.
- SQL inbox с fingerprint и idempotent receipt. Admission commit происходит до
  spool I/O; после сбоя payload повторяется maintenance-циклом. У каждого пакета
  собственная транзакция и next_attempt_at: один сбой не блокирует другие потоки.
- Не более 10000 receipts, 20 MiB pending payload; delivered payload очищается,
  receipts хранятся семь дней. Immutable deterministic JSONL сегменты защищают
  retry между file write и SQL commit от повторного append. Сегменты учитываются
  в frontend budget и видны existing read view/snapshots.
- Frontend server-only transport: timeout 2 s, redirects запрещены, queue <=1000,
  bounded response чтение, cancel response body на non-2xx. Baseline ошибки
  отправляются отдельно от capture, чтобы пережить его остановку до доставки.
- Recorder использует транспорт при DIAGNOSTICS_INGEST_URL; старый файловый путь
  сохранён без этого opt-in. Локальный scratch нужен для recorder status/session
  bookkeeping, но общий frontend/backend volume transport больше не требуется.
- Публичный API proxy отклоняет опасные сегменты и кодирует остальные до
  формирования destination; проверено отсутствие fetch для попытки escape.
- Новая Alembic revision `rpl00_diagnostic_ingest` после текущего head 068.
  Проверена upgrade/downgrade на отдельной непустой SQLite-БД с сохранением marker.

## Проверки

- Backend regression: **77 passed, 1 skipped**: новый internal API, inbox faults,
  миграция, store/read view/control и P0/P1 regression. Log: `tests/tmp/replica-f1-final.log`.
- Frontend полный suite: **730 passed, 1 skipped**; после этого добавлена ещё одна
  проверка bounded queue, профильный transport suite **10 passed**.
- Existing recorder + transport: **32 passed, 1 skipped** до добавления последнего
  queue regression. Production build Next.js завершён успешно.
- ESLint: 0 errors (существующие warnings локалей). Ruff изменённых файлов прошёл.
- Независимый review выявил baseline loss, блокировку последующих batches и
  неосвобождённые non-2xx bodies; regression failures наблюдались и исправлены.
- Это не полная приёмка PostgreSQL, network policies или multi-Pod/Gateway.

## Открытые gates

Пользователь явно разрешил npm audit. Он выполнен и обнаружил **17 vulnerabilities
(16 moderate, 1 high)**, включая zod 4.5.4 / GHSA-mrhx-c7xc-m3w5 (исправление >=4.6.6).
Из-за high общий check-project остановлен. Минимальный `npm update zod
--package-lock-only --ignore-scripts --no-audit` отклонён автоматической проверкой:
она требует отдельного разрешения на изменение lock и registry request. Обход
не выполнялся; lock-файл не изменён, commit пока не создаётся.

Helm по-прежнему отсутствует. Реальный тестовый namespace/storage manifest и
права не предоставлены; storage vendors не выбирались вслепую.

## Решения

- Ruling: в single-backend F1 inbox/segment delivery координируется process lock;
  distributed capability закрыт. Межпроцессную координацию добавляет R4.
- Ruling: receipt означает durable admission, не обязательную немедленную запись
  сегмента. Ошибка delivery оставляет payload, bounded retry и degraded status.
- Ruling: baseline получает проекцию без capture ID, capture — отдельную копию;
  это сохраняет ошибки при остановке capture, не продлевая право на сам capture.
