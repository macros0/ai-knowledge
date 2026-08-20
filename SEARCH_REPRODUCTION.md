# Воспроизведение поиска по чанкам (все ветки + merge/collapse)

Практический гайд: как руками проверить, что поиск по чанкам (dual-index)
работает во всех ветках (`dense` / `bm25` / `hybrid` / `full`) и что merge/collapse
сливает концепты и чанки по всем трём случаям (A/B/C).

Все примеры ниже прогнаны на реальных данных проекта и проверены через
`POST /api/search`. Используйте **свои** query и `doc_id` — таблицы в этом
документе дают эталонный результат на момент написания.

## Предусловия

1. Стек поднят: `.\scripts\start-all.ps1` (Qdrant `:6333`, backend `:8000`).
2. Есть готовый документ (`status: "done"`) с проиндексированными чанками.
   Проверить по `data/documents.json` (поле `total_chunks > 0`).
3. В `.env`: `SEARCH_INDEX_CHUNKS_ENABLED=true`.

Для примеров ниже используется документ **`aba437ae0475408e`** —
«Спецификация типа сообщения СЭДО № 12010» (v3.3.0, 14 чанков).

## 1. Проверка, что чанки действительно проиндексированы

Чанки и концепты живут в **одной** коллекции `okf_knowledge_base`, отличаются
полем `point_type`. Подсчёт по типам:

```bash
# Всего точек
curl -X POST http://localhost:6333/collections/okf_knowledge_base/points/count \
  -H "Content-Type: application/json" -d '{"exact": true}'

# Чанки конкретного документа
curl -X POST http://localhost:6333/collections/okf_knowledge_base/points/scroll \
  -H "Content-Type: application/json" -d '{
    "filter": {"must": [
      {"key": "doc_id", "match": {"value": "aba437ae0475408e"}},
      {"key": "point_type", "match": {"value": "chunk"}}
    ]},
    "limit": 100, "with_payload": false
  }'
```

Ожидание: у `aba437ae0475408e` 14 точек `point_type="chunk"` (по числу файлов
`data/okf_bundles/aba437ae0475408e/chunks/chunk_*.md`).

## 2. Формат запроса

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" -d '{
    "query": "…",
    "top_k": 8,
    "mode": "full",
    "metadata_filter": {"doc_id": "aba437ae0475408e"}
  }'
```

Поля запроса (`SearchRequest`):

| Поле | Тип | Назначение |
| :-- | :-- | :-- |
| `query` | string | Текст запроса (эмбеддится + токенизируется в BM25) |
| `top_k` | int | Число результатов (1–50) |
| `mode` | string | Пресет: `dense` / `bm25` / `hybrid` / `full` |
| `dense` / `bm25` / `metadata` | bool? | Явные флаги веток — **переопределяют** `mode` |
| `metadata_filter` | dict | Фильтр: `type`, `doc_id`, `point_type`, `tags`, `global_tags`, `relations`, `author`, `date` |

Ключевое поле ответа — `point_type` (`concept` или `chunk`) и `chunk_index`
(номер раздела, `null` у концептов без привязки). Поле `type` в ответе —
это `"raw_text"` для чанка и `"concept"` для концепта.

## 3. Все ветки поиска (матрица)

Каждый режим = набор веток, слитых через RRF (`fusion.py`). В ответе ждите
хиты **обоих** типов точек (концепты + чанки), если в базе есть и то и другое.

| Режим | Ветки | Проверочный query | Что ожидать |
| :-- | :-- | :-- | :-- |
| `dense` | семантика | `дополнительные выходные дни для ухода за детьми-инвалидами` | Концепт-обзор по теме наверху; точные термины-коды могут не попасть (их нет в dense) |
| `bm25` | ключевые слова | `disabilityChildrenStatement` | Точные лексические попадания: `chunk` с этим именем + концепт «Атрибуты …» из того же документа |
| `hybrid` | dense + bm25 | `DisabilityChildrenRequestType` | Дубликаты, найденные обеими ветками, получают буст RRF; наверху и чанк, и концепт |
| `full` | dense + bm25 + metadata | `Клинковская` | Фамилия утверждающего, которой **нет** в концептах, находится по чанкам (см. случай C ниже) |

### Явные флаги веток (вместо пресета)

```bash
# Только dense + metadata, без bm25
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" -d '{
    "query": "Клинковская", "top_k": 5,
    "mode": "full", "dense": true, "bm25": false, "metadata": true
  }'
```

Если хотя бы один из `dense`/`bm25`/`metadata` задан явно — используется набор
флагов, пресет `mode` игнорируется (`resolve_branches` в `context_builder.py`).

### Фильтрация по метаданным (`metadata_filter`)

```bash
# Только чанки (отключить концепты)
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" -d '{
    "query": "Клинковская", "top_k": 5, "mode": "full",
    "metadata_filter": {"point_type": "chunk"}
  }'
# → все хиты с point_type="chunk"

# Один документ
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" -d '{
    "query": "Клинковская", "top_k": 5, "mode": "full",
    "metadata_filter": {"doc_id": "aba437ae0475408e"}
  }'
```

## 4. Воспроизведение merge/collapse (случаи A/B/C)

После RRF-fusion хиты группируются по `(doc_id, chunk_index)` — если концепт и
его родительский чанк попали в топ, они **сливаются в один блок**
(`merge_and_format` в `context_builder.py`). Различайте случаи по полям ответа.

### Случай A — концепт + чанк сливаются

Query, попадающий в концепт и в его сырой чанк одновременно:

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" -d '{
    "query": "DisabilityChildrenRequestType", "top_k": 10, "mode": "full",
    "metadata_filter": {"doc_id": "aba437ae0475408e"}
  }'
```

Ожидание: блок с `point_type="concept"` и title из концепта
(«Атрибуты элемента DisabilityChildrenRequestType …»), но `content` — **полный
сырой текст чанка**, а не краткая выжимка. Рядом — отдельный чанк
(концепта к нему в топе нет).

### Случай B — только концепт

То же query, но ветка/фильтр оставляют только концепты:

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" -d '{
    "query": "DisabilityChildrenRequestType", "top_k": 5, "mode": "full",
    "metadata_filter": {"doc_id": "aba437ae0475408e", "point_type": "concept"}
  }'
```

Ожидание: `point_type="concept"`, `title` + краткая `content`-выжимка,
`chunk_index` может быть `null`.

### Случай C — только чанк (деталь, которую LLM уронила)

Классика: в концептах фамилия утверждающего не попала, а в сыром чанке есть.

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" -d '{
    "query": "Клинковская", "top_k": 5, "mode": "full",
    "metadata_filter": {"doc_id": "aba437ae0475408e"}
  }'
```

Ожидание: `point_type="chunk"`, **синтетический title** вида
`Спецификация_…_12010_v3_3_0…docx (Раздел 1)`, `chunk_index=0`. Именно такие
детали (URL, коды, конфиги, ФИО) — причина, по которой чанки индексируются
отдельно от концептов.

### Как отличить случаи в ответе

| Случай | `point_type` | `title` | `content` |
| :-- | :-- | :-- | :-- |
| A (концепт+чанк) | `concept` | title из концепта | полный текст чанка |
| B (только концепт) | `concept` | title из концепта | краткая выжимка |
| C (только чанк) | `chunk` | `{filename} (Раздел {N})` | сырой текст |

## 5. Поля ответа

`SearchHit` (нормализованные поля):

| Поле | Описание |
| :-- | :-- |
| `score` | Нормализован к топу: `score / max_score` (топ = 1.0) |
| `title` | Концепт или синтетический `{filename} (Раздел N)` |
| `type` | `"raw_text"` (чанк) / `"concept"` |
| `point_type` | `chunk` / `concept` |
| `filepath` | Путь к OKF-файлу или `{doc_id}/chunks/chunk_NN.md` |
| `snippet` | Первые 300 символов content |
| `chunk_index` | Номер раздела (`null` у концептов без чанка) |
| `source_filename` | Имя исходного документа |

## 6. Troubleshooting

**В ответе нет чанков (только концепты).**
- Проверьте `SEARCH_INDEX_CHUNKS_ENABLED=true` в `.env`.
- Чанки могли не индексироваться для старых документов — добейте их:

  ```bash
  cd backend
  .venv\Scripts\activate
  python scripts/backfill_chunks.py --doc-id aba437ae0475408e
  ```

  (скрипт идемпотентен, требует остановленный/неиспользующийся Qdrant с точек).
  После смены `okf_max_chunk_index_chars` или эмбеддинг-модели — полный
  `python scripts/reindex.py`.

**Ветка `bm25` не находит ничего** — проверьте, что коллекция имеет sparse-вектор
`"sparse"` (`GET /collections/okf_knowledge_base`), и что Ollama (порт **12400**,
не 11434!) отдаёт эмбеддинги. Sparse строится локально, но dense-эмбеддинг
запроса требует живой модели.

**Qdrant не отвечает** — `GET /collections` на `:6333`. Пуск:
`.\scripts\start-all.ps1`, логи в `%TEMP%\opencode\`.