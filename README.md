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

## Статусы обработки документа

| Статус | Значение |
| :-- | :-- |
| `uploaded` / `processing` / `splitting` / `indexing` | Идёт обработка (парсинг, генерация OKF, индексация) |
| `done` | Обработан полностью, OKF и векторы готовы |
| `paused` | Приостановлен из-за ошибки или перезапуска сервера — можно «Возобновить» |
| `failed` / `error` | Критическая ошибка (например, недоступен Qdrant на финализации) |

Обработка идёт **инкрементально**: результат каждого чанка пишется в staging,
поэтому при паузе/перезапуске обработанные чанки не генерируются заново —
`resume` пропускает их. Удалить документ в процессе генерации можно из любого
статуса — генерация будет остановлена, а все связанные файлы удалены. При
перезапуске сервера зависшие статусы автоматически сбрасываются в `paused`.

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

## Конфигурация (переменные `.env`)

| Переменная | По умолчанию | Описание |
| :-- | :-- | :-- |
| `APP_NAME` | `OKF Knowledge Service` | Название сервиса |
| `API_PREFIX` | `/api` | Префикс путей API |
| `DATA_DIR` | `./data` | Корень runtime-данных (uploads, okf_bundles, staging) |
| `QDRANT_URL` | `http://localhost:6333` | Адрес Qdrant |
| `QDRANT_COLLECTION` | `okf_knowledge_base` | Коллекция Qdrant |
| `EMBEDDING_DIM` | `1024` | Размерность вектора (bge-m3=1024, text-embedding-3-small=1536) |
| `EMBEDDING_PROVIDER` | `http` | `http` (OpenAI-совместимый endpoint) или `fake` (без сети) |
| `EMBEDDING_MODEL` | `bge-m3` | Модель эмбеддингов |
| `EMBEDDING_BASE_URL` | `http://localhost:11434/v1` | Endpoint эмбеддингов |
| `EMBEDDING_API_KEY` | `ollama` | Ключ API эмбеддингов |
| `LLM_MODEL` | `ollama/qwen2.5:14b` | Модель LLM (через LiteLLM: `openai/...`, `ollama/...`, `openrouter/...`) |
| `LLM_BASE_URL` | `http://localhost:11434` | Endpoint LLM |
| `LLM_API_KEY` | `ollama` | Ключ API LLM |
| `LLM_TEMPERATURE` | `0.2` | Температура генерации |
| `LLM_MAX_TOKENS` | `4096` | Максимум выходных токенов |
| `LLM_MAX_CONCURRENCY` | `1` | Параллельность фоновых вызовов LLM (0 = без ограничений) |
| `LLM_INTERACTIVE_CONCURRENCY` | `2` | Параллельность чата (не блокируется фоновой генерацией) |
| `LLM_RETRY_ATTEMPTS` | `5` | Быстрые ретраи при ошибках LLM |
| `LLM_RETRY_BACKOFF_SECONDS` | `2` | Начальная задержка между ретраями (экспонента: 2→4→8→16→32с) |
| `LLM_TIMEOUT_SECONDS` | `120` | Устарел — заменён на стриминг с idle-timeout |
| `LLM_STREAM_IDLE_TIMEOUT_SECONDS` | `60` | Таймаут тишины между токенами стрима (нет данных = сеть/провайдер умер) |
| `LLM_MAX_TOTAL_TIMEOUT_SECONDS` | `600` | Общий жёсткий предел на весь вызов LLM (даёт медленной генерации закончиться) |
| `LLM_CHUNK_RETRY_ATTEMPTS` | `3` | Долгий контур восстановления при исчерпании быстрых ретраев |
| `LLM_CHUNK_RETRY_BACKOFF_SECONDS` | `30` | Начальная задержка контура восстановления (30→60→90с) |
| `OKF_MAX_CHUNK_CHARS` | `8000` | Макс. размер куска текста для LLM за один вызов |
| `OKF_MAX_CONCEPT_CHARS` | `4000` | Макс. длина тела концепта (избыток отбрасывается) |
| `BACKEND_URL` | `http://localhost:8000` | Адрес бэкенда для прокси `/api` в Next.js (Docker: `http://backend:8000`) |

### Модель устойчивости к сбоям LLM

Ответы LLM читаются **стримом**, таймаут считается по «тишине» между токенами —
это отличает медленную, но здоровую генерацию (токены капают, вызов живёт до
`LLM_MAX_TOTAL_TIMEOUT_SECONDS`) от оборванной сети (нет данных
`LLM_STREAM_IDLE_TIMEOUT_SECONDS`). Любой пришедший чанк сбрасывает таймер
активности, даже пустой служебный.

Повторные попытки — два контура:

1. **Быстрые ретраи клиента** (`LLM_RETRY_ATTEMPTS=5`, экспонента
   `2→4→8→16→32с`) — сглаживают секундные скачки и лимиты (403/429/5xx).
   Фатальные ошибки (401/402/404) не ретраятся.
2. **Долгий контур пайплайна** (`LLM_CHUNK_RETRY_ATTEMPTS=3`, задержка
   `30→60→90с`) — переживает длительные сетевые обрывы; при исчерпании документ
   переходит в `paused` и его можно «Возобновить» в UI (сохранённые чанки
   пропускаются).

Каждый вызов выполняется в изолированном daemon-потоке — зависший поток не
блокирует последующие запросы и не держит слот семафора.

### Парсинг JSON из ответа LLM (каскад + переотправка при обрезании)

Модель не всегда возвращает строго валидный JSON — особенно для плотных
спецификаций (таблицы, XML-схемы, кавычки и переносы строк внутри текста).
`_parse_json` идёт каскадом:

1. **`json.loads`** — быстрый путь для валидного ответа.
2. **Проверка на обрезание** — `finish_reason == "length"` из стрима либо
   незакрытая верхняя скобка во фрагменте (счётчик глубины с учётом строк и
   экранирования, напр. `[{"a":1},{"b":2}` без внешней `]`). Обрезанный ответ
   **не сохраняется** — он неполон по определению, а `json_repair` мог бы молча
   «закрыть» оборванный массив и выкинуть хвост. Вместо этого бросается
   `LLMTruncationError`, и `chat_json` повторяет запрос с увеличенным
   `max_tokens` (формула `base * multiplier^n`, ограничено сверху
   `llm_max_tokens_cap`, чтобы не превысить контекстное окно провайдера).
3. **Восстановление хвоста + `json_repair`** — для структурно завершённого
   ответа (закрывающая скобка есть): неэкранированные управляющие символы,
   лишние/пропущенные запятые, мусор после `]`/`}` и markdown-обёртка.
   Это ремонт **без потери данных**, поэтому переотправка не тратится.
4. **Дамп + понятная ошибка** — если JSON не восстановлен ни одним из способов
   или обрезание не вылечилось за все попытки, сырой ответ сохраняется в
   `data/debug/llm_raw_{doc_id}_{chunk}_{timestamp}.txt` (каталог в
   `.gitignore`), а документ переходит в `paused` с ошибкой, содержащей первые
   200 символов ответа и путь к дампу.

Настройки обрезания (`backend/app/config.py`):
`llm_max_tokens` (базовый лимит), `llm_max_tokens_cap` (верхняя планка бампа,
по умолчанию 16384), `llm_truncation_retry_attempts` (число повторов, 2),
`llm_truncation_max_tokens_multiplier` (множитель, 1.5).

После resume такой чанк обрабатывается заново: обычно повторный запрос с большим
`max_tokens` закрывает обрезание, а если нет — файл дампа в `data/debug/`
позволяет увидеть точную причину (обрезание, невалидные символы, артефакт
провайдера).

**Прозрачность в логах:** в журнале (`backend/logs/` или stdout) сразу видно, какой
уровень сработал — `json.loads` успешен (тихо), обрезание (WARNING: «JSON обрезан,
повтор с max_tokens=N»), `_recover_truncated`/`json_repair` (INFO: «успешно
восстановлен»), дамп (WARNING: путь к файлу). Это позволяет отличать стабильно
чистые ответы модели от «спасённых» и диагностировать проблемные
провайдеры/модели.

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
├── data/               # uploads/, okf_bundles/, staging/, debug/ (runtime, в gitignore)
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
| POST | `/api/documents/{doc_id}/resume` | Возобновить приостановленную обработку |
| DELETE | `/api/documents/{doc_id}` | Удалить документ и все связанные файлы |
| GET | `/api/documents/{doc_id}/okf` | Список сгенерированных OKF-файлов |
| GET | `/api/documents/{doc_id}/okf/{filename}` | Содержимое OKF-файла |
| POST | `/api/search` | Поиск по концептам (top-k + фильтр по тегам) |
| POST | `/api/chat` | Вопрос к базе знаний (ответ + источники) |
| GET | `/api/tags` | Список тегов с частотой использования |

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
