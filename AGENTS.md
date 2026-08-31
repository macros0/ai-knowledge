# AGENTS.md — AI Knowledge Service

Сервис: загрузка документов (DOCX/XLSX/PDF), конвертация в OKF-концепты через LLM и RAG-поиск по базе знаний (Qdrant).

## Быстрый старт (все зависимости одной командой)

```powershell
.\scripts\start-all.ps1    # поднять весь стек: Qdrant, Ollama, PostgreSQL, backend, frontend
.\scripts\stop-all.ps1     # остановить всё
```

`start-all.ps1` сам запускает каждый сервис через глобальный хелпер `start-background.ps1`,
опирается на health-эндпоинты (не вслепую) и идемпотентен (старые инстансы убивает по PID/порту).

После запуска:

| Сервис | URL | Health |
|---|---|---|
| Qdrant | http://localhost:6333 | `GET /collections` |
| Ollama | http://localhost:12400 | `GET /api/tags` |
| PostgreSQL | 127.0.0.1:5432 | `pg_isready -h 127.0.0.1 -p 5432` |
| Backend (FastAPI) | http://localhost:8000 | `GET /health` |
| Frontend (Next.js) | http://localhost:3000 | `GET /` |

UI: http://localhost:3000

## Важные квирки (не исследовать заново)

- **Ollama слушает порт 12400, НЕ 11434.** Порт 11434 попадает в исключённый диапазон Windows Hyper-V
  (проверить: `netsh interface ipv4 show excludedportrange protocol=tcp`). В `.env` уже стоит
  `EMBEDDING_API_BASE=http://localhost:12400`. Запуск: `OLLAMA_HOST=127.0.0.1:12400`.
- **npx/npm shims на этой машине сломаны** (`node_modules\npm\bin\npx-cli.js` отсутствует), а
  `cmd /c "npm run dev"` ломает кавычки в хелпере. Фронтенд запускать ТОЛЬКО через
  `node node_modules/next/dist/bin/next dev` (из `frontend/`).
- LLM: `openrouter/mistralai/mistral-nemo` (OpenRouter, из `.env`). Эмбеддинги: `ollama/bge-m3` —
  модель должна быть загружена в Ollama (`ollama pull bge-m3`).
- Qdrant — локальный бинарь (не Docker): `%TEMP%\opencode\qdrant\v1.19.0\qdrant.exe`, данные в
  `%TEMP%\opencode\qdrant\storage` (сохраняются между запусками).
- **PostgreSQL 17 — portable-бинарь (не Docker, не служба)**: бинари в `C:\postgresql17\pgsql\bin`,
  данные в `C:\postgresql17\data`, порт 5432. Запуск `postgres.exe -D <data>` через хелпер
  (`scripts/start-postgres.ps1`). Суперюзер `postgres`, пароль dev-only `okf_dev_pg` (см. `DATABASE_URL`
  в `.env`). На этой машине параллельно живёт **служба PostgreSQL 10** (`postgresql-x64-10`,
  порт 5433) — **её не трогать**. `postgres.exe` отказывается работать от админа — стек поднимать
  из НЕ-elevated shell. Русские данные требуют `UTF8` (кластер инициализирован с `-E UTF8`).
- **Скрипты в `backend/scripts/`, коннектящиеся через `session_scope`, требуют доступной БД** —
  поднятый стек (`.\scripts\start-all.ps1`) либо явный `DATABASE_URL` (например, на SQLite-фолбэк
  `sqlite:///./data/app.db`). Если PG17 на :5432 не запущен (слушает только служба PG10 на :5433),
  скрипт «зависает» на коннекте — это выглядит как баг в коде, а не как проблема окружения.
- **Метаданные в реляционной БД** (Этап 2, `MIGRATION_PLAN.md`): `backend/app/db/` — синхронный
  SQLAlchemy 2.0 + psycopg3 (Postgres) / SQLite (dev). Документы/теги/staging/OKF-концепты в БД,
  бинарники и `.md`-бандлы — в FS. Таблицы создаются на старте (`init_db`/`create_all`), Alembic
  (`backend/alembic/`) — для версионированных миграций. Одноразовый перенос JSON→БД:
  `backend/scripts/migrate_json_to_db.py`; slim-payload Qdrant: `backend/scripts/migrate_payload.py`.
  Полный текст концепта — в `okf_concepts` (payload Qdrant больше не хранит `content`), поиск
  достаёт его по `(doc_id, slug)` через `services/concept_store.py`.
- **Dual-index**: Qdrant хранит два типа точек — `point_type="concept"` (LLM-выжимки) и
  `point_type="chunk"` (сырой текст чанка, минимальный payload: doc_id, chunk_index,
  tags, section_title, content). Поиск идёт по обоим типам, RRF-fusion в Python
  (services/fusion.py), merge/collapse после fusion (services/context_builder.py).
  Tags — жёсткий pre-filter для dense/bm25. Переключатели `SEARCH_*_ENABLED` —
  query-time, реиндекс не требуется.
  Реиндекс нужен только при смене `embedding_dimensions` или sparse-токенайзера.
- **Справочник разработок + дедупликация (Этап 4, 30.08.2026)**:
  - Каноническая связь — `documents.development_id` (FK → `developments`). Проекция для
    поиска — payload Qdrant `dev_tags=[number,name,module]` на ВСЕХ точках (отдельное поле,
    НЕ сливается с `tags`); pre-filter матчит тег, если он в `tags` ИЛИ `dev_tags`.
    Переименование разработки = `set_payload(dev_tags=...)` по `doc_id` без пере-эмбеддинга
    (`services/dev_sync.py`).
  - `developments.module` — строка, мягко валидируемая против generic-справочника
    `attribute_values` (`attribute_key='module'`, `org_id` nullable = привязка к инсталляции).
    Значение вне справочника → 422. Сид `PY/PT/OM/PA` — **отдельный скрипт**
    `backend/scripts/seed_attribute_values.py` (не входит в Alembic-миграцию).
  - Автоопределение: regex по имени файла (upload) + LLM с титульного листа (pipeline, тот же
    `llm_model`); кандидат без совпадения — в `documents.development_suggestion`.
  - Дедупликация: `documents.file_hash` (SHA-256, блокирующий Level 1), `content_hash` +
    `minhash` (k=128, `mmh3`) + `document_lsh_buckets` (strict 8×16 / loose 16×8).
    Level 2/3 кандидаты — `GET /documents/{id}/duplicates`.

## Фоновые процессы

Только через глобальный хелпер `start-background.ps1` (правила — в глобальном AGENTS.md).
PID-файлы: `%TEMP%\opencode\{qdrant,ollama,postgres,backend,next}.pid`
Логи: `%TEMP%\opencode\{qdrant,ollama,postgres,python,node}-{out,err}.log`

### Пайплайн: программные триггеры обязаны ждать завершения

Пайплайн документов работает в **daemon-потоке** внутри процесса сервера. Вызов
`Pipeline().regenerate/ingest/resume` из **отдельного** скрипта возвращается сразу,
а поток умирает вместе со скриптом — документ зависает в промежуточном статусе
(`processing`/`splitting`/`indexing`). Через API проблемы нет (сервер живёт постоянно),
риск — только во внешних батч/диагностических скриптах.

Правило: любой программный вызов пайплайна обязан держать процесс живым до
терминального статуса. Использовать `Pipeline.wait_for(doc_id, timeout)`:

```python
p = Pipeline()
p.regenerate(doc_id)
result = p.wait_for(doc_id)   # блокирует до done/error/failed/paused
```

Диагностика зависших документов (рантайм-залипание без рестарта сервера) — read-only
скрипт `backend/scripts/check_stuck_documents.py` (статусы `processing`/`splitting`/
`indexing` старше порога, `--hours`). Это осознанная дешёвая альтернатива полноценному
варианту B (персистентный флаг + фоновый пересчёт по TTL): автоматизировать не начинали,
пока зависания не стали реальной проблемой. Не переписывай и не выкидывай скрипт без
явной задачи — он уже покрывает ручную проверку.

## Кодировка .ps1-скриптов (важно)

PowerShell 5.1 читает `.ps1`-файлы **без BOM** как ANSI (CP1251) — кириллица в них ломает
парсинг строк (симптом: `CommandNotFoundException` на случайном токене строки). Любой
`.ps1` с русским текстом в `scripts/` должен сохраняться **в UTF-8 с BOM**.
Если правил скрипт инструментом записи (пишет без BOM) — пересохрани с BOM:

```powershell
$c = [IO.File]::ReadAllText($path, [Text.Encoding]::UTF8)
[IO.File]::WriteAllText($path, $c, (New-Object Text.UTF8Encoding($true)))
```

## Структура

- `backend/` — FastAPI: пайплайн OKF-генерации, LiteLLM-шлюз, векторный поиск
- `doc-parser/` — парсер DOCX/XLSX/PDF с рекурсивным извлечением вложений
- `frontend/` — Next.js UI (список документов, чанки OKF, чат)
- `data/` — runtime: `uploads/`, `okf_bundles/`, `staging/`, `documents.json`
- `scripts/` — старт/стоп всего стека

## Тесты

```powershell
python -m pytest tests/ -q   # из backend/
```

Один тест (`test_settings`) требует запущенного Qdrant — без него падает по `WinError 10061`.

## Безопасность (SECURITY.md)

Модель безопасности зафиксирована в корневом `SECURITY.md`. **Любое изменение,
затрагивающее**: аутентификацию/авторизацию, сетевую топологию/exposure, секреты,
шифрование, retention/soft delete данных или журнал ИБ (audit log) — обязано в том же
PR/коммите обновить соответствующий раздел `SECURITY.md`, а не откладываться. Если
изменение находит новую проблему — фиксировать её в разделе «Журнал security-изменений»
с датой и кратким описанием причины и исправления.
