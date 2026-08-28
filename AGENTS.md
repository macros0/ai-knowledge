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
- **Dual-index**: Qdrant хранит два типа точек — `point_type="concept"` (LLM-выжимки) и
  `point_type="chunk"` (сырой текст чанка, минимальный payload: doc_id, chunk_index,
  tags, section_title, content). Поиск идёт по обоим типам, RRF-fusion в Python
  (services/fusion.py), merge/collapse после fusion (services/context_builder.py).
  Tags — жёсткий pre-filter для dense/bm25. Переключатели `SEARCH_*_ENABLED` —
  query-time, реиндекс не требуется.
  Реиндекс нужен только при смене `embedding_dimensions` или sparse-токенайзера.

## Фоновые процессы

Только через глобальный хелпер `start-background.ps1` (правила — в глобальном AGENTS.md).
PID-файлы: `%TEMP%\opencode\{qdrant,ollama,postgres,backend,next}.pid`
Логи: `%TEMP%\opencode\{qdrant,ollama,postgres,python,node}-{out,err}.log`

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
