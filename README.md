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
│  Chat Web UI    │◄───│  RAG: Qdrant (vectors + payload=YAML meta)     │
│  (Vite + JS)     │    │  hybrid search: semantic + tag filter          │
└─────────────────┘    └─────────────────────────────────────────────────┘
```

Слои:

1. **Загрузка и API** — Python **FastAPI**, приём документов через `POST /api/documents`, асинхронная обработка.
2. **Парсинг** — пакет **`doc-parser`** (editable-установка): `python-docx` (текст + комментарии рецензентов + встроенные объекты), `openpyxl` (таблицы → Markdown), `pypdf`, рекурсивный разбор вложений. Вложения сохраняются в `data/okf_bundles/{doc_id}/attachments/`.
3. **OKF-генерация** — локальная LLM (через **LiteLLM**, легко переключить на облачную) разбивает текст на смысловые концепты и формирует Markdown-файлы с YAML-фронтматтером (type, title, tags, relations, source).
4. **RAG-хранилище** — **Qdrant**: вектор (только тело Markdown) + payload (метаданные из YAML). Гибридный поиск: семантический по вектору + фильтрация по тегам.
5. **Умный поиск** — эмбеддинг запроса → поиск в Qdrant → LLM формирует ответ по найденным концептам с указанием источников.
6. **Веб-интерфейс** — **Vite + JavaScript**: загрузка файлов, статус обработки, чат со стримингом ответа и блоком "Источники".

## Стек

| Слой | Технология |
| :-- | :-- |
| Backend | Python 3.12, FastAPI, LiteLLM, Qdrant client |
| Frontend | JavaScript, Vite |
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
npm run dev                     # http://localhost:5173
```

## Быстрый старт (Docker)

```bash
cp .env.example .env
# убедитесь, что QDRANT_URL=http://localhost:6333 (внутри compose он переопределяется на http://qdrant:6333)
docker compose up --build
# UI: http://localhost:8080   API docs: http://localhost:8000/docs
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

## Конфигурация (основные переменные `.env`)

| Переменная | Описание |
| :-- | :-- |
| `QDRANT_URL` / `QDRANT_COLLECTION` | Адрес и коллекция Qdrant |
| `EMBEDDING_PROVIDER` | `http` (OpenAI-совместимый endpoint) или `fake` (демо без сети) |
| `EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` | Модель и адрес эмбеддингов |
| `EMBEDDING_DIM` | Размерность вектора (должна совпадать с моделью) |
| `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` | Модель для OKF-генерации и ответов (через LiteLLM) |
| `OKF_MAX_CHUNK_CHARS` | Макс. размер куска текста для LLM за один вызов |

## Структура проекта

```
├── backend/            # Python FastAPI
│   └── app/
│       ├── api/        # routes: documents, search, chat
│       ├── models/     # Pydantic-схемы
│       ├── prompts/    # промпты OKF-генерации и чата
│       ├── services/   # OKF, эмбеддинги, Qdrant, пайплайн
│       ├── config.py   # настройки из .env
│       └── main.py     # точка входа FastAPI
├── doc-parser/         # standalone-пакет разбора документов (editable)
│   └── src/docparser/  # парсеры docx/xlsx/pdf, вложения, CLI
├── frontend/           # Vite + JavaScript (чат, загрузка, источники)
├── data/               # uploads/ и okf_bundles/ (runtime, в gitignore)
├── docker-compose.yml
└── .env.example
```

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
