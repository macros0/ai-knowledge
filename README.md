# OKF Knowledge Service

Сервис для загрузки документов, преобразования их в **Open Knowledge Format (OKF)**, смысловой разбивки на концепты, индексации в RAG-хранилище (Qdrant) и умного поиска по данным через веб-чат.

## Архитектура

```
Upload (docx/xlsx/pdf)
        │
        ▼
┌──────────────────────────┐    ┌──────────────────────┐    ┌─────────────────┐
│  Document Parser         │───►│  OKF Generator (LLM) │───►│  OKF Storage    │
│  (doc-parser package,    │    │  semantic chunking   │    │  ./data/okf_    │
│   рекурсивный разбор     │    │  + YAML frontmatter  │    │  bundles/{id}/  │
│   вложений)              │    │                      │    │  + attachments/ │
└──────────────────────────┘    └──────────────────────┘    └────────┬────────┘
                                                                     │ embed
                                                                     ▼
┌─────────────────┐    ┌─────────────────────────────────────────────────┐
│  Chat Web UI    │◄───│  RAG: Qdrant (vectors + payload=YAML meta)      │
│  (Next.js + JS) │    │  hybrid search: semantic + tag filter           │
└─────────────────┘    └─────────────────────────────────────────────────┘
```

Слои:

1. **Загрузка и API** — Python **FastAPI**, приём документов через `POST /api/documents`, асинхронная обработка.
2. **Парсинг** — пакет **`doc-parser`** (editable-установка): `python-docx` (текст + комментарии рецензентов + встроенные объекты), `openpyxl` (таблицы → Markdown), `pypdf`, рекурсивный разбор вложений. Вложения сохраняются в `data/okf_bundles/{doc_id}/attachments/`.
3. **OKF-генерация** — локальная LLM (через **LiteLLM**, легко переключить на облачную) разбивает текст на смысловые концепты и формирует Markdown-файлы с YAML-фронтматтером (type, title, tags, relations, source).
4. **RAG-хранилище** — **Qdrant**: вектор (только тело Markdown) + payload (метаданные из YAML). Гибридный поиск: семантический по вектору + фильтрация по тегам.
5. **Умный поиск** — эмбеддинг запроса → поиск в Qdrant → LLM формирует ответ по найденным концептам с указанием источников.
6. **Веб-интерфейс** — **Next.js (App Router) + JavaScript**: загрузка файлов, статус обработки, чат с блоком "Источники".

## Стек

| Слой | Технология |
| :-- | :-- |
| Backend | Python 3.12, FastAPI, LiteLLM, Qdrant client |
| Frontend | JavaScript, Next.js (App Router) |
| Vector DB | Qdrant |
| OKF Storage | Файловая система `./data/okf_bundles/{doc_id}/` |
| LLM / Embeddings | Любые, совместимые с OpenAI API (Ollama, vLLM, TEI, OpenAI, YandexGPT...) |

## Быстрый старт (локально, без Docker)

```bash
# 1. Настройки
cp .env.example .env
#   отредактируйте EMBEDDING_* и LLM_* под ваши модели

# 2. Backend
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -e ../doc-parser    # пакет разбора документов (editable)
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 3. Frontend (в отдельном терминале)
cd frontend
npm install
npm run dev                     # http://localhost:3000
```

## Быстрый старт (Docker)

```bash
cp .env.example .env
# убедитесь, что QDRANT_URL=http://localhost:6333 (внутри compose он переопределяется на http://qdrant:6333)
docker compose up --build
# UI: http://localhost:8080   API docs: http://localhost:8000/docs
# BACKEND_URL внутри compose переопределяется на http://backend:8000 (прокси /api в Next.js)
```

## Как это работает

1. Загрузите `.docx` / `.xlsx` / `.pdf` в веб-интерфейсе.
2. Backend парсит файл (для docx сохраняет комментарии рецензентов), режет на главы и отправляет в LLM.
3. LLM выделяет смысловые блоки (концепты) и генерирует OKF-файлы вида:

```markdown
---
type: concept
title: Настройка сетевых мостов в Proxmox
tags: [proxmox, networking, bridge]
source_document: {filename: "setup.docx", doc_id: "abc123"}
relations: [vlan-configuration.md]
created_at: 2026-08-12
---

# Настройка сетевых мостов в Proxmox
...
```

4. Каждый файл векторизуется, вектор уходит в Qdrant, а YAML-метаданные — в payload точки.
5. В чате запрос векторизуется, Qdrant возвращает релевантные концепты (с учётом фильтров по тегам), LLM синтезирует ответ и перечисляет источники.

## Обновление Qdrant

Qdrant гарантирует совместимость **storage-формата только на ±1 минорную версию**.
Прыжок через несколько миноров (например, 1.12 → 1.19) при наличии данных приведёт
к отказу сервера стартовать. Поэтому перед любым апгрейдом следуйте процедуре ниже.

Правила совместимости:
- Версии **клиента** (`qdrant-client`) и **сервера** должны совпадать по major и
  расходиться не более чем на 1 минор. Обновляйте сначала клиент, потом сервер.
- У нас один узел — апгрейд требует короткого даунтайма (это нормально).

### Процедура апгрейда с данными (через снапшот)

1. **Обновить клиент** (`backend/requirements.txt`), поставить в venv, проверить
   подключение к старому серверу.
2. **Снять снапшот коллекции** (сервер сам гасит записи и сбрасывает WAL):

   ```bash
   curl -X POST http://localhost:6333/collections/okf_knowledge_base/snapshots
   ```

   Снапшот появится в каталоге snapshots сервера — скопируйте его в бэкап
   **вне** storage-каталога.
3. **Остановить старый сервер**, обновить бинарь/образ, **удалить/затереть**
   storage (старый формат не читается новым сервером).
4. **Запустить новый сервер**, восстановить коллекцию из снапшота:

   ```bash
   curl -X PUT http://localhost:6333/collections/okf_knowledge_base/snapshots/recover \
     -H "Content-Type: application/json" \
     -d '{"location": "file:///path/to/snapshot.snapshot", "priority": "snapshot"}'
   ```

   Восстановление создаёт коллекцию и сразу строит индексы — пере-эмбеддинг не нужен.
5. **Проверить**: количество точек и выборочный payload:

   ```bash
   curl -X POST http://localhost:6333/collections/okf_knowledge_base/points/count \
     -H "Content-Type: application/json" -d '{"exact": true}'
   ```

### Страховка: пересборка индекса из источников

Векторный индекс — **производные данные**; первоисточник лежит в
`data/okf_bundles/{doc_id}/`. Если снапшот не восстановился, коллекцию можно
полностью пересобрать без потерь (только время на эмбеддинги):

```bash
cd backend
.venv\Scripts\activate
python scripts/reindex.py          # из data/ в текущем каталоге
python scripts/reindex.py --data-dir /path/to/data
```

Скрипт удаляет коллекцию, создаёт её заново и индексирует все OKF-файлы.
Перед запуском нужен работающий Qdrant и embedding-сервер (или `EMBEDDING_PROVIDER=fake`).

## Конфигурация (основные переменные `.env`)

| Переменная | Описание |
| :-- | :-- |
| `QDRANT_URL` / `QDRANT_COLLECTION` | Адрес и коллекция Qdrant |
| `EMBEDDING_PROVIDER` | `http` (OpenAI-совместимый endpoint) или `fake` (демо без сети) |
| `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` | Модель и адрес эмбеддингов |
| `EMBEDDING_DIM` | Размерность вектора (должна совпадать с моделью) |
| `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` | Модель для OKF-генерации и ответов (через LiteLLM) |
| `BACKEND_URL` | Адрес бэкенда для прокси `/api` в Next.js (dev: `http://localhost:8000`, Docker: `http://backend:8000`) |
| `OKF_MAX_CHUNK_CHARS` | Макс. размер куска текста для LLM за один вызов |

## Структура проекта

```
├── backend/            # Python FastAPI
│   ├── app/
│   │   ├── api/        # routes: documents, search, chat
│   │   ├── models/     # Pydantic-схемы
│   │   ├── prompts/    # промпты OKF-генерации и чата (store + дефолты)
│   │   ├── services/   # OKF, эмбеддинги, Qdrant, пайплайн
│   │   ├── config.py   # настройки из .env
│   │   └── main.py     # точка входа FastAPI
│   └── prompts/        # канонические файлы промптов (.md, версионируются)
├── doc-parser/         # standalone-пакет разбора документов (editable)
│   └── src/docparser/  # парсеры docx/xlsx/pdf, вложения, CLI
├── frontend/           # Next.js (App Router) + JavaScript (чат, загрузка, источники)
├── data/               # uploads/ и okf_bundles/ (runtime, в gitignore)
├── docker-compose.yml
└── .env.example
```

## Промпты (настройка без рестарта)

Промпты хранятся во внешних Markdown-файлах и перечитываются при каждом обращении
по изменению `mtime` — правка подхватывается следующим запросом без перезапуска
бэкенда.

**Каскад разрешения** (первый найденный валидный файл имеет приоритет):

1. `<data_dir>/prompts/<key>.md` — runtime-оверрайд (правится на лету, `.gitignore`).
2. `backend/prompts/<key>.md` — канонический файл (версионируется в git).
3. Дефолт-константа в `backend/app/prompts/okf.py` — всегда валидный fallback.

**Файлы промптов:**

| Ключ | Файл | Обязательные плейсхолдеры |
| :-- | :-- | :-- |
| `chat_system` | `chat_system.md` | — |
| `chat_user` | `chat_user.md` | `{context}`, `{query}` |
| `okf_system` | `okf_system.md` | — |
| `okf_user` | `okf_user.md` | `{filename}`, `{content}` |
| `okf_chunk` | `okf_chunk.md` | `{filename}`, `{index}`, `{total}`, `{content}` |

**Как править:**

- Локально: правьте `backend/prompts/<key>.md` (попадёт в git) или создайте
  `data/prompts/<key>.md` — он перекроет канонический без коммита.
- В Docker: том `./data:/data` уже смонтирован, поэтому `data/prompts/<key>.md`
  правится с хоста без пересборки контейнера.

**Защита от ошибок:** если файл пустой, не в UTF-8 или не читается — возвращается
предыдущее валидное значение (или следующий уровень каскада). Если в файле
пропущен обязательный плейсхолдер — в лог пишется `WARNING` и используется дефолт.
Опечатка в плейсхолдере (например, `{контекст}`) не роняет запрос — токен
сохраняется дословно. Новые файлы сидируются из дефолтов автоматически при старте
(`PromptStore.ensure()`).

## API

| Метод | Путь | Описание |
| :-- | :-- | :-- |
| POST | `/api/documents` | Загрузка документа (multipart) |
| GET | `/api/documents` | Список документов и статусов |
| GET | `/api/documents/{doc_id}` | Статус обработки документа |
| GET | `/api/documents/{doc_id}/okf` | Список сгенерированных OKF-файлов |
| GET | `/api/documents/{doc_id}/okf/{filename}` | Содержимое OKF-файла |
| POST | `/api/search` | Поиск по концептам (top-k + фильтр по тегам) |
| POST | `/api/chat` | Вопрос к базе знаний (ответ + источники) |

## Пакет doc-parser (standalone)

Парсер вынесен в отдельный пакет `doc-parser/` (src-layout) — его можно развивать
и проверять без LLM, Qdrant и веб-UI. Подробности: [doc-parser/README.md](doc-parser/README.md).

```bash
cd doc-parser
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"

doc-parser parse path/to/sample.docx                 # разбор → Markdown
doc-parser parse path/to/sample.docx --format json   # разбор → блоки JSON
doc-parser parse path/to/sample.docx --attachments-dir out/attachments
doc-parser info path/to/sample.docx                  # статистика, комментарии, вложения
pytest -q
```

Бэкенд подключает пакет через editable-установку (`pip install -e ../doc-parser`),
а в Docker — через `COPY doc-parser` + `pip install -e ./doc-parser`.
