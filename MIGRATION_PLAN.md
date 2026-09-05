# План миграции OKF Knowledge Service на внешнюю БД

Дата составления: 18.08.2026.

> **Статус: реализовано (28.08.2026).** Код по этому плану написан. Отклонения от
> исходного плана, зафиксированные при реализации:
> 1. **Синхронный SQLAlchemy 2.0** вместо async (`psycopg` v3, а не `asyncpg`/
>    `aiosqlite`) — приложение целиком синхронное (эндпоинты `def` + pipeline в
>    `threading.Thread`), перевод на async не давал выгоды.
> 2. **`tags.count` не хранится** — счётчик вычисляется на чтение агрегатом по
>    `document_tags` (нет триггера и дрейфа).
> 3. **Массивы/JSON** (`okf_concepts.tags/relations`, staging-массивы) — JSON-колонки,
>    а не нативные Postgres ARRAY (портативность с SQLite-dev).
> 4. **`documents.uploaded_by`** — строка (username из сессии), без FK; `owner_id`/
>    `org_id` — nullable FK (наполняются на Этапах 3/при авторизации в БД).
> 5. **slim payload Qdrant** оставляет `slug` + `relations` + `filepath` (маленькие,
>    нужны для graph expansion и реконструкции ссылки на источник); убираются только
>    `content`/`global_tags`/`source_document`/`attachments`. Join-ключ — `(doc_id, slug)`.
> 6. **`okf_concepts.content`** — полный текст для новых документов; для
>    существующих `.md`-бандлов восстановим только уже обрезанный текст (полный
>    текст из старых бандлов не восстановить).
> 7. `audit_log` — по-прежнему отложен (зависит от требований ИБ).
>
> См. реализацию: `backend/app/db/` (модели/сессии), `backend/alembic/`,
> `backend/scripts/migrate_json_to_db.py`, `backend/scripts/migrate_payload.py`,
> `backend/app/services/{registry,tag_registry,staging,concept_store}.py`.

Детализация Этапа 2 (`OKF_Knowledge_Service_Roadmap.md`) — «Перенос хранения данных во внешнюю БД».

## 1. Принцип

- Минимум изменений в API и `pipeline.py` — замена хранилищ через Repository pattern.
- Schema БД заводится сразу со всеми таблицами для будущей авторизации и мультитенантности (`users`, `roles`, `user_roles`, `organizations`), но **пустыми** и с nullable `owner_id`/`org_id` у документов. Реализация самой авторизации (Этап 1 roadmap) наполняет уже готовые таблицы — это снимает жёсткую зависимость «сначала auth, потом БД» на уровне миграции данных.
- Qdrant остаётся специализированным векторным хранилищем; payload точек становится «тонким», с JOIN к реляционной БД для полных данных концепта.
- Промпты (`backend/prompts/*.md`, `data/prompts/*.md`), загруженные бинарники (`data/uploads/`), бинарные вложения бандлов (`data/okf_bundles/{doc_id}/attachments/`) и аварийные дампы LLM (`data/debug/`) — остаются в FS без изменений.

## 2. Текущее состояние хранилищ (что заменяем)

| Где | Что | Кто пишет | Проблема |
|---|---|---|---|
| `data/documents.json` | реестр документов (`DocumentRegistry`) | полный перезапись файла при каждом `update()` | нет транзакций, нет concurrent writers, весь файл переписывается на каждое обновление статуса чанка; нет `owner_id` для ролей |
| `data/tags.json` | глобальный справочник тегов с частотой (`TagRegistry`) | полный перезапись | та же проблема; теги дублированы в frontmatter `.md` и в payload Qdrant — три источника правды |
| `data/staging/{doc_id}/` | `manifest.json` + `chunk_XX.json` (концепты) + `chunk_XX.md` (текст) | чекпойнтинг инкрементальной LLM-генерации | частые per-chunk writes; scope для ролей наследуется через doc_id → owner |
| `data/okf_bundles/{doc_id}/` | `*.md` (концепт + YAML frontmatter), `chunks/`, `attachments/` | финал пайплайна | первоисточник знаний; Qdrant-индекс пересобирается из них (`reindex.py`) |
| `data/uploads/{doc_id}.ext` | бинарные оригиналы документов | upload endpoint | бинарник, лучше FS/объектное хранилище |
| `data/debug/llm_raw_*.txt` | аварийные дампы LLM | pipeline | временные, `.gitignore` |
| `backend/prompts/*.md` + `data/prompts/*.md` | промпты с mtime-каскадом | вручную | версионные, оставить в FS |

Векторное хранилище Qdrant — dense + sparse (BM25), payload = дубликат метаданных из OKF.

## 3. Решения по развилкам (зафиксированы)

1. **СУБД:** PostgreSQL (prod) + SQLite (dev, через тот же SQLAlchemy, zero-config вход как у Qdrant-бинаря сегодня).
2. **OKF-бандлы `.md`:** БД canonical + `.md` остаются параллельно как backup/inspect (не удаляем). `reindex.py` продолжает читать `.md`.
3. **Staging:** в БД переносим только `manifest.json` → JSONB в `document_staging`. Сырые `chunk_XX.md`/`chunk_XX.json` остаются в FS под `data/staging/{doc_id}/` (большие, инспектируемые).
4. **Мультитенантность:** завести `organizations` + `users` + `roles` + `user_roles` (scope: global|org|document). Документы получают `owner_id` + `org_id` (nullable).
5. **Qdrant payload:** slim — только `concept_id` + `doc_id` + `title` + `tags` (для фильтра). Полные данные концепта достаются из БД по `concept_id` после поиска.

## 4. Стек и зависимости

Добавить в `backend/requirements.txt`:

- `sqlalchemy[asyncio]>=2.0` — ORM
- `asyncpg` — async-драйвер PostgreSQL
- `aiosqlite` — для dev-режима (SQLite)
- `alembic` — миграции схемы
- `passlib[bcrypt]` + `python-jose[cryptography]` — под будущую auth (Этап 1 roadmap), ставятся при начале авторизации, можно отложить

Переменные окружения (`.env`):

- `DATABASE_URL` — Postgres prod-инстанса.
- `DATABASE_URL_DEV` — `sqlite+aiosqlite:///./data/app.db` (по умолчанию, для локального запуска).

Сервис Postgres добавить в `scripts/start-all.ps1` через глобальный хелпер `start-background.ps1` (PID-файл `%TEMP%\opencode\postgres.pid`), health-check по `SELECT 1`, опрос с ретраями (не вслепую `Start-Sleep`).

## 5. Схема БД

```
organizations        users            roles
─────────────        ─────            ─────
id PK                id PK            id PK
name UNIQUE          email UNIQUE     name (admin/editor/viewer/security)
created_at           password_hash    permissions JSONB
                     org_id FK NULL   created_at
                     is_active
                     created_at

user_roles           documents              document_tags
───────────          ──────────             ──────────────
user_id FK           id PK                  doc_id FK CASCADE
role_id FK           org_id FK NULL         tag TEXT
scope_type           owner_id FK NULL       PK(doc_id, tag)
scope_id NULL        filename
                     content_type           tags
okf_concepts         size                   ─────
─────────────        status                 name PK
id PK                error                  count (denormalized, триггер/VIEW)
doc_id FK CASCADE    total_chunks
slug                 processed_chunks      okf_attachments
title                current_chunk          ────────────────
type                 okf_concept_count      id PK
tags[]               created_at             doc_id FK CASCADE
content TEXT         updated_at             name
relations JSONB      deleted_at             kind
chunk_index NULL     deleted_by             caption
created_at           document_staging       saved_path
                     ────────────────
                     doc_id PK FK CASCADE
                     total_chunks
                     processed_chunks INT[]
                     used_slugs TEXT[]
                     chunks_data JSONB
                     status
                     updated_at
```

Ключевые детали схемы:

- `documents.owner_id` и `org_id` — NULLABLE; миграция существующих документов проставит NULL, массовое назначение — в Этапе 1 roadmap (фактическая авторизация).
- `okf_concepts.content` — TEXT без обрезки (сегодня в Qdrant хранится `content[:4000]`, что теряет данные; БД хранит полный текст концепта).
- `okf_concepts.tags` — Postgres-массив; даёт SQL-фильтрацию по тегам в дополнение к Qdrant-фильтру.
- `document_staging.chunks_data` — JSONB, повтор структуры `manifest.json`. `processed_chunks`/`used_slugs` — массивы Postgres.
- `tags.count` — денормализованный счётчик; поддерживается триггером на `document_tags` (INSERT/DELETE) либо пересчётом в VIEW/materialized, если нагрузка на чтение тегов невысокая.
- `documents.deleted_at`/`deleted_by` — корзина / soft delete (реализована, Этап 4a.2 roadmap, 31.08.2026): `deleted_at IS NULL` = активен. Парный флаг — payload Qdrant `deleted=true`; все пути поиска обязаны фильтровать через `vector_store._not_deleted()`. Физическое удаление — фоновая автоочистка по `trash_retention_days`. См. `SECURITY.md` §5.
- `roles` содержит `security` — заготовка под роль ИБ (`audit_log` в roadmap Этап 2). Сама `audit_log` здесь не моделируется — она описана в roadmap отдельно и зависит от ИБ-требований.

## 6. Repository pattern (минимум изменений в API)

Замена трёх JSON-store на DB-backed реализации с тем же интерфейсом:

| Файл | Замена | Интерфейс сохраняется |
|---|---|---|
| `services/registry.py` (`DocumentRegistry`) | `DocumentRepository` (async SQLAlchemy session) | `create`/`get`/`list`/`update`/`delete` — те же сигнатуры |
| `services/tag_registry.py` (`TagRegistry`) | `TagRepository` | `add`/`all`/`normalize_tags` |
| `services/staging.py` (`StagingStore`) | `StagingRepository` для manifest + FS-часть для `chunk_*.md`/`chunk_*.json` | `create`/`load`/`append_chunk`/`concepts`/`slugs`/`has_chunk`/`processed_chunks`/`save_chunk_text`/`remove` |

Роуты (`api/documents.py`, `api/chat.py`, `api/search.py`, `api/tags.py`) и `pipeline.py` не трогаются — они работают через `get_registry()`/`TagRegistry()`/`StagingStore(doc_id)`. Меняются только внутренности.

Сессии: async SQLAlchemy session-per-request через FastAPI dependency. Pipeline (фоновый поток) — отдельный session с явным commit после каждого чанка (поведение как у `_save()` сегодня, но без переписывания всего файла).

## 7. Qdrant slim payload

В `services/vector_store.py` (`index_concepts`) payload меняется:

```python
payload = {
    "concept_id": concept_id,  # FK в okf_concepts
    "doc_id": doc_id,
    "title": meta.get("title", ""),
    "tags": meta.get("tags", []),
}
```

`content` и прочее убираются — достаются из БД по `concept_id` после поиска. В `api/chat.py` и `api/search.py` после `vector_store.search()` — пакетный `SELECT * FROM okf_concepts WHERE id IN (...)` для получения `content` и метаданных источников. Один round-trip на top-k хитов.

Миграция существующих точек — один скрипт `scripts/migrate_payload.py`: scroll по коллекции, для каждой точки вычислить `concept_id` по `doc_id`+slug из `filepath`, `update_set_payload` с slim-набором. Идемпотентно, можно повторять.

## 8. Миграция данных (one-shot)

Скрипт `backend/scripts/migrate_json_to_db.py`:

1. Прочитать `data/documents.json` → `INSERT INTO documents` (`owner_id=NULL`, `org_id=NULL`).
2. Пройти `data/okf_bundles/{doc_id}/*.md` → парсить YAML frontmatter → `INSERT INTO okf_concepts` (`slug`=filename stem, `content`=тело после frontmatter, `tags` из YAML, `relations` из YAML, `chunk_index` из YAML).
3. `data/okf_bundles/{doc_id}/attachments/` → `INSERT INTO okf_attachments`.
4. `data/staging/{doc_id}/manifest.json` → `INSERT INTO document_staging` (JSONB). Файлы чанков оставить в FS.
5. `data/tags.json` → `UPSERT INTO tags`. `document_tags` заполняется из `okf_concepts.tags` (FK на doc_id).
6. Для Qdrant — запустить `scripts/migrate_payload.py`.

Идемпотентный (`ON CONFLICT DO NOTHING` или `TRUNCATE+INSERT` с флагом `--reset`). Резервная копия `data/*.json` сохраняется как `.bak` перед миграцией.

## 9. Этапы внедрения (рекомендуемый порядок)

1. **Схема + Alembic.** Модели SQLAlchemy, начальная миграция (`alembic revision --autogenerate`). Пустые таблицы auth/organizations (FK nullable).
2. **Repositories.** `DocumentRepository`, `TagRepository`, `StagingRepository` с тем же интерфейсом. Юнит-тесты на существующий `tests/` (добавить SQLite in-memory fixture; `test_settings` по-прежнему требует Qdrant).
3. **Миграция данных.** `scripts/migrate_json_to_db.py` + `scripts/migrate_payload.py`. Старые `.json` → `.bak`.
4. **Qdrant slim payload.** Правка `index_concepts` + fetch в `chat`/`search`.
5. **Postgres в `start-all.ps1`.** Dev-фолбэк на SQLite через `DATABASE_URL`.
6. **Документация.** Обновить `README.md` (переменная `DATABASE_URL`), `AGENTS.md` (Postgres в таблице сервисов), `OKF_Knowledge_Service_Roadmap.md`.

Реализация авторизации (Этап 1 roadmap) — отдельный эпик поверх готовой схемы.

## 10. Риски и проверки

- **Concurrent pipeline writes.** Сегодня `threading.Lock` на весь файл; в БД — row-level lock на `documents` при `UPDATE`, staging-чанк в отдельной транзакции. Проверить сценарий «несколько документов параллельно» (аналог текущего `test_pipeline`).
- **`reindex.py`.** Продолжает читать `data/okf_bundles/*.md` (т.к. `.md`-бандлы не удаляем — они синхронны с БД через пайплайн). Альтернатива — переключить на чтение из `okf_concepts`; решение за рамкой текущей миграции.
- **`backfill_sparse` в `vector_store.py`.** Читает `data/okf_bundles/*/` — остаётся рабочим, т.к. `.md` не удаляем.
- **Тест `test_settings`.** Сегодня требует Qdrant. Добавить тест на БД-слой с SQLite in-memory.
- **Дрейк тегов.** После миграции теги хранятся в: (1) `document_tags` (canonical), (2) `okf_concepts.tags` (per-concept), (3) Qdrant payload (для фильтра), (4) `.md` frontmatter. Canonical — `document_tags` + `okf_concepts.tags`; payload Qdrant и frontmatter `.md` — проекции, обновляются при правках тегов (Этап 4a roadmap). Синк Qdrant-payload при правке — **асинхронный** (фоновый поток, не fallback): на используемом окружении `set_payload` ≈ 2с/вызов, на документ приходятся десятки concept-точек с разными тегами, синхронно ответ замораживался бы. point_id concept-точек детерминирован (`uuid5(filepath)`), синк читает `okf_concepts.tags` из БД без scroll и идемпотентно приводит Qdrant к состоянию БД. Самовосстановление — только явным `regenerate`/`resume` (без фонового ретрая).
- **Связь с `audit_log`.** Таблица `audit_log` (Этап 2 roadmap) моделируется отдельно — зависит от ИБ-требований к составу записи и сроку хранения. Схема БД здесь заводится без неё; после фиксации ИБ-требований добавляется миграцией.

## 11. Этап 2b: PostgreSQL — единственный источник истины (завершён 05.09.2026)

Исходный план (§9) останавливался на «БД canonical для концептов + бандлы как
backup/inspect». Дальнейший аудит вскрыл дыры консистентности: вложения жили только
в YAML/frontmatter, чанки — только в файлах и payload Qdrant, провенанс отсутствовал.
Этап 2b доводит модель до конца: **БД — единственный источник истины для
структурированного знания; FS — байты оригиналов/вложений; Qdrant — пересобираемая
slim-проекция; YAML/Markdown — экспорт.**

Фазы (все приняты):
- **Ф0** — схема: `document_chunks`, активация+расширение `okf_attachments`,
  провенанс `okf_concepts`; Alembic `f0a1b2c3d4e5`.
- **Ф1** — пайплайн dual-write: одна транзакция финализации
  (`replace_chunks`+`replace_concepts`+`replace_attachments` через session-passing)
  ДО Qdrant; вложения → `uploads/<doc_id>/attachments/`; провенанс per-chunk.
- **Ф2** — backfill корпуса (`backfill_db_store.py`) + сверка (`check_integrity.py`) +
  baseline `probe_sources`.
- **Ф3** — чтение только из БД: гидрация чанков по `(doc_id, chunk_index)`, эндпоинты
  okf/chunks/fulltext/attachments, reindex/backfill'ы — из БД.
- **Ф4** — Qdrant v2: логические point_id (`okf:concept:{doc_id}:{slug}` /
  `okf:chunk:{doc_id}:{chunk_index}`), slim payload, rebuild из БД, переключение
  `QDRANT_COLLECTION=okf_knowledge_base_v2`.
- **Ф5** — бандлы → экспорт (`okf_write_bundles=false`, `POST /documents/{id}/export-okf`
  + `scripts/export_okf.py`), удаление транзиентных fallback'ов.

**Ключевые инварианты** (зафиксированы, не пересматривать без явной задачи):
1. Направление данных только `PostgreSQL → бандлы` (экспорт), никогда обратно —
   кроме явных backfill/импорт-скриптов.
2. point_id вычисляются только в `vector_store.concept_point_id`/`chunk_point_id`.
3. Dense-эмбеддинг — единая формула `title + "\n" + content[:okf_max_concept_chars]`
   (чанк: `section_title + "\n" + text[:okf_max_chunk_index_chars]`) для пайплайна,
   reindex и rebuild v2.
4. Гидрация поиска — natural key `(doc_id, slug)` для концептов, `(doc_id, chunk_index)`
   для чанков; surrogate id в payload не хранятся.
5. Старая коллекция `okf_knowledge_base` и legacy-каталог `data/okf_bundles/` удалены
   после финальной приёмки (rollback-вариант больше не требуется).

**Out of scope (возможный Этап 2c):** дерево `source_files`/`source_attachment_id` с
трекингом происхождения блоков в парсере (`extracted_chars` пока NULL); MinIO/S3; OCR.
