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
| Qdrant | http://localhost:16333 | `GET /collections` |
| Ollama | http://localhost:12400 | `GET /api/tags` |
| PostgreSQL | 127.0.0.1:5432 | `pg_isready -h 127.0.0.1 -p 5432` |
| Backend (FastAPI) | http://localhost:18000 | `GET /health` |
| Frontend (Next.js) | http://localhost:3000 | `GET /` |

UI: http://localhost:3000

## Важные квирки (не исследовать заново)

- **Ollama слушает порт 12400, НЕ 11434.** Порт 11434 попадает в исключённый диапазон Windows Hyper-V
  (проверить: `netsh interface ipv4 show excludedportrange protocol=tcp`). В `.env` уже стоит
  `EMBEDDING_API_BASE=http://localhost:12400`. Запуск: `OLLAMA_HOST=127.0.0.1:12400`.
- **npx/npm shims на этой машине сломаны** (`node_modules\npm\bin\npx-cli.js` отсутствует), а
  `cmd /c "npm run dev"` ломает кавычки в хелпере. Фронтенд запускать ТОЛЬКО через
  `node node_modules/next/dist/bin/next dev` (из `frontend/`).
- LLM: `openrouter/mistralai/mistral-nemo` (OpenRouter, из `.env`). Интерактивному RAG-чату
  можно задать модель посильнее через `LLM_CHAT_MODEL` (пусто → `LLM_MODEL`); OKF-генерация
  и batch-задачи всегда на `LLM_MODEL`. Эмбеддинги: `ollama/bge-m3` —
  модель должна быть загружена в Ollama (`ollama pull bge-m3`).
- **Backend слушает порт 18000, НЕ 8000.** Порт 8000 (как и ранее Qdrant 6333, Ollama 11434)
  периодически попадает в исключённый диапазон Windows Hyper-V/WSL (проверить:
  `netsh interface ipv4 show excludedportrange protocol=tcp`); резервации меняются от загрузки
  к загрузке. 18000 выше динамического диапазона TCP (1024–15000) — HNS его не резервирует.
  `BACKEND_URL` в `.env` и фолбэки `frontend/next.config.js` / `frontend/src/lib/backendFetch.js`
  уже на `http://localhost:18000`. Docker-compose не трогать: внутри сети `backend:8000`.
  `start-all.ps1` перед запуском проверяет все фиксированные host-порты на резервацию
  (понятная ошибка вместо `winerror 10013`).
- **Qdrant — локальный бинарь (не Docker): `%TEMP%\opencode\qdrant\v1.19.0\qdrant.exe`, данные в
  `%TEMP%\opencode\qdrant\storage` (сохраняются между запусками). Слушает порты 16333 (REST) /
  16334 (gRPC), НЕ дефолтные 6333/6334**: порт 6333 попал в исключённый диапазон Windows
  Hyper-V/WSL (проверить: `netsh interface ipv4 show excludedportrange protocol=tcp`) — bind
  падает с `os error 10013`. Резервации меняются от загрузки к загрузке, поэтому выбраны порты
  выше динамического диапазона TCP (1024–15000) — HNS их не резервирует. `QDRANT_URL` в `.env`
  уже `http://127.0.0.1:16333`.
- **`localhost` на этой машине = +2с на каждое НОВОЕ TCP-соединение (05.09.2026, диагностика
  инцидента «Готов, а поиск не находит»):** Qdrant (16333), Ollama (12400) и т.п. слушают только
  IPv4 (`127.0.0.1`), а хост `localhost` резолвится на `::1` первым — connect к `::1` «виснет»
  ~2.05с и лишь потом уходит на IPv4. Квирк бил на КАЖДЫЙ вызов `qdrant-client` (не переиспользует
  соединение: один Qdrant-запрос ≈ 2.2с, батч эмбеддингов ≈ 2.1с) — отсюда и старая пометка
  «set_payload ~2с», и растянутая до минут финализация большого документа. **Фикс:** во всех URL
  к локальным сервисам использовать `127.0.0.1`, НЕ `localhost` (`QDRANT_URL`, `EMBEDDING_API_BASE`;
  дефолты в `config.py` уже `127.0.0.1`). После фикса: `query_points` ~8мс, `get_collections` ~4мс
  (было ~2050мс). Диагностика-изолятор: сравнить тайминги `http://localhost:<port>` vs
  `http://127.0.0.1:<port>` (raw-соединение) — сразу видно 2с-провал.
  Сам write→read race в Qdrant НЕ подтверждён: при `wait=True` (дефолт qdrant-client 1.19) первая
  же попытка count/retrieve/query после ack видит точки (probe: stale_first_attempts=0).
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
  **Квирк dev-Postgres:** `create_all` создаёт только отсутствующие ТАБЛИЦЫ и **не добавляет
  колонки** к уже существующим. После добавления поля в модель и рестарта бэкенда dev-Postgres
  падает с 500 (`column <t>.<col> does not exist`). Alembic на dev-БД не работает: `alembic_version`
  отстаёт от `create_all` (таблицы вроде `chat_sessions` уже созданы → `DuplicateTable`). Лечить
  вручную: `ALTER TABLE <t> ADD COLUMN IF NOT EXISTS <col> <type> [NOT NULL] DEFAULT <d>;`
  (через `app.db.session.get_engine()`). Пример (2026-09-01): `developments.version INTEGER NOT NULL DEFAULT 1`.
  Полный текст концепта — в `okf_concepts` (payload Qdrant больше не хранит `content`), поиск
  достаёт его по `(doc_id, slug)` через `services/concept_store.py`.
- **Dual-index**: Qdrant хранит два типа точек — `point_type="concept"` (LLM-выжимки) и
  `point_type="chunk"` (сырой текст чанка, минимальный payload: doc_id, chunk_index,
  tags, section_title, content). Поиск идёт по обоим типам, RRF-fusion в Python
  (services/fusion.py), merge/collapse после fusion (services/context_builder.py).
  Tags — жёсткий pre-filter для dense/bm25. Переключатели `SEARCH_*_ENABLED` —
  query-time, реиндекс не требуется.
  Веса веток RRF — `SEARCH_RRF_*_WEIGHT` в config.py (bm25=1.5 > dense=1.0:
  по коротким/аббревиатурным запросам dense даёт плоский шум, bm25 разделяет
  точно). **Демоция замечаний** (`SEARCH_REVIEW_CONCEPT_RANK_PENALTY=10`,
  02.09.2026): концепт-замечания (тег review) получают эффективный ранг
  +penalty в каждой ветке ДО fusion (`vector_store._demote_review_concepts`)
  — их узкий контент (дословный контекст якоря) стабильно обгонял широкий
  основной концепт и вытеснял основной контент из топа; по запросам именно
  про замечания они остаются единственными релевантными. В паре с этим
  `SEARCH_PER_BRANCH_TOP_K=40` (было 30): замечания удваивают концепт-точки
  на тему — запас, чтобы основные концепты не выпадали из выборки Qdrant.
  **Primary-выбор группы** (`context_builder._is_review_hit`,
  02.09.2026): первичный блок группы (doc, chunk_index) — первый ОСНОВНОЙ
  концепт по fused score, замечания — сиблинги; чанк + только замечания →
  primary сам чанк (kind=chunk); группа из одних замечаний (фильтр
  tags=[review]) — прежнее поведение. Без этого замечание перехватывало
  заголовок/цитату [1] группы — ответ цитировал замечание вместо основного
  источника при верном контенте.
  Контекст чата: каждый `context_block` получает в metadata
  `Matched terms: [...]` — содержательные термины запроса (токенайзер BM25 +
  фильтр служебных слов «какие/есть/…»), встречающиеся в блоке точно или по
  стемму-фолбэку `context_builder._token_in_text`/`_stem_ru` (02.09.2026:
  консервативный срез русских падежных окончаний, guard основы >= 5,
  латиница/аббревиатуры не трогаются — «табельного» ↔ «табельных»; sparse-
  токенайзер services/sparse.py НЕ трогается, там стемминг сломал бы индекс);
  подсказка LLM, какие блоки релевантны вопросу, плюс анти-мета/анти-
  атрибуционные правила в `prompts/chat_system.md`.
  **top_k — это БЛОКИ после merge, не точки Qdrant** (chat.py/search.py берут
  в `search_composite` широкий набор `per_branch_top_k` и режут `merged[:top_k]`):
  срез по точкам до группировки ронял концепты-сиблинги группы (группа
  (doc_id, chunk_index) может содержать до ~18 концептов — поля таблицы);
  сильные bm25-попадания вроде «Перечень: Наименование поля» не доживали до
  merge. `merge_and_format` эмитит сиблинг-концепты отдельными блоками
  (kind=concept, свой контент; сиблинги-замечания — kind="review") после
  первичного блока группы, а итоговый порядок
  блоков — глобальная сортировка по fused score (иначе сиблинги одной группы
  вытесняли более релевантные блоки других групп за границу top_k).
  Анти-шум: `drop_unmatched_blocks` (context_builder, вызывается из chat.py
  после среза top_k) убирает из контекста и sources блоки с пустым Matched terms,
  если есть блоки с совпадениями; при полном отсутствии лексических совпадений
  (парафразный запрос) фильтр отключается. Мотив: модели (включая gpt-4o-mini)
  стабильно вписывали семантически-смежный блок с пустым маркером в ответ не
  по теме — промпт-правила против этого вероятностны. **Иммунитет
  сиблинг-замечаний** (02.09.2026): блоки kind="review" не режутся фильтром
  при пустом маркере — их узкий текст часто в словоформе, отличной от
  запроса, и замечания пропадали из источников целиком. Иммунитет строго по
  kind, не по тегу: primary-блок группы несёт union-теги (включая review от
  замечаний-хитов) и фильтруется как обычный блок.
  Запрос-точное-имя: `drop_partial_title_matches` — если какой-то заголовок
  покрывает ВСЕ термины запроса (>= 2, точно или по стемму-фолбэку),
  контекст ограничивается блоками «про объект», а у concept+chunk-блоков контент
  подменяется на собственную выжимку концепта (`concept_content` из merge):
  сырой чанк содержит чужие подразделы раздела (таблица ЭЛН-журнала внутри
  чанка «Доработка расширения для ZPRP_DISABILITY_CHLD»), и модели сливают
  их в описание объекта. Плюс маркер `Title match` в metadata блока
  (`title_matched_terms`) — какие термины запроса стоят в заголовке.
  **Probe-приёмка поисковых правок** (`backend/scripts/probe_sources.py`,
  02.09.2026): baseline/after по 5 запросам реального корпуса
  (`--baseline` сохраняет `probe-baseline.json` — локальный артефакт
  состояния корпуса, в git не попадает). Критерий ложного срабатывания
  фиксировать ДО прогона: (а) источник baseline пропал из выдачи ЦЕЛИКОМ —
  смещение позиции/выход из топ-5 срабатыванием НЕ считается (блок может
  законно сместиться, если фильтр раньше ошибочно его вырезал: LK_STAT
  #5→#6 при возврате стеммингом лексически связанного блока); (б) появился
  источник, сматченный только стеммом, чей заголовок не относится к теме
  запроса.
  Цитаты: LLM обязана ссылаться строго `[N]`, но иногда пишет «блок с ID N» —
  фронтенд рендерит ссылки только по `[N]`; `services/citation.py` (
  `normalize_citations`, вызывается из chat.py) детерминированно приводит такие
  формулировки к `[N]` (числа вне 1..числа блоков не трогает — «блоке 3002» это
  номер отсутствия, не ссылка).
  Реиндекс нужен только при смене `embedding_dimensions` или sparse-токенайзера.
  **Sparse-текст** (BM25) строится ТОЛЬКО по единой формуле `vector_store._sparse_text`
  (title + content) — свежая индексация и backfill сходятся в ней. До 01.09.2026
  `backfill_sparse` при каждом рестарте перезаписывал sparse из полного текста .md
  (frontmatter/теги в BM25) — теперь точки с готовым sparse скипаются; одноразовая
  миграция старых испорченных векторов: `backend/scripts/rebuild_sparse.py`
  (force=True, идемпотентен). `backfill_chunks` скипает бандлы целиком ДО
  эмбеддинга, пропускает корзину/доки без строки в БД (совместим с purge-порядком
  «строка БД удаляется первой») и кладёт `dev_tags` в payload чанков.
- **Инцидент 03.09.2026 — пять независимых дефектов (Qdrant ни разу не падал,
  все 400/422 — мгновенные отказы живого сервера)**:
  1. **Upsert-батчинг** (`vector_store._upsert_batches`, UPSERT_BATCH_SIZE=256,
     env `QDRANT_UPSERT_BATCH_SIZE`): монолитный upsert 5667 концептов ≈ 120 МБ
     JSON против серверного лимита Qdrant `max_request_size_mb=32` → 400 от actix
     по Content-Length. Ретраи в `_upsert_batches`: сеть/5xx/429 — да (3 попытки),
     4xx-валидация — нет. Упавшие батчи не роняют остальные, но `index_concepts/
     index_chunks` поднимают VectorStoreError с итогами («X из Y точек») →
     документ в paused, resume доотправит (point_id uuid5, upsert идемпотентен).
  2. **Sparse-дедуп** (`sparse.to_sparse_vector`): коллизии md5-хэшей терминов
     (реальные в корпусе: «обязат»≡«тестировании», «завершения»≡«кроме»,
     «ru»≡«ильиных») давали дубли индексов → Qdrant 422 «indices: must be
     unique», валивший backfill и финализацию. Теперь TF коллидующих терминов
     складываются ДО логарифма (log1p(sum_tf)), indices всегда уникальны и
     отсортированы. Формула `_sparse_text` не менялась — реиндекс не нужен.
  3. **Честные ошибки** (`vector_store._qdrant_call`): UnexpectedResponse (4xx/5xx
     живого Qdrant) → «Qdrant отклонил запрос (HTTP N): <текст>» (дефект запроса),
     сеть/таймауты → прежнее «недоступен». До фикса 400/422 маскировались под
     «Qdrant не запущен» и сбивали диагностику.
  4. **Молчаливая неполнота при зелёном done**: (а) «дыра закрытой структуры» в
     `llm_client._parse_json` — модель выдавала ЗАКРЫТЫЙ JSON + ещё данные после
     него; проверки finish_reason/глубины это не ловили, `_recover_truncated`
     молча брал первый префикс («ФС 3509»: 4-5 концептов вместо 11 при done,
     недетерминированно). Теперь JSON-структура в хвосте → LLMTruncationError →
     штатный каскад (ретрай max_tokens → сплит чанка → salvage+WARNING); хвост
     без скобок — мусор, без потерь. (б) **Телеметрия деградации**
     (`services/gen_quality.py`, thread-local): события llm_salvage /
     classifier_fallback пишутся из llm_client/field_table, пайплайн дренирует
     после каждого `generate_chunk` в staging (`chunks_data[idx].degradation`),
     финализация агрегирует в `documents.problem` (см. ниже). (в) **problem-коды**
     (`services/problem_codes.py`, колонка `documents.problem`, NULL=ок):
     `no_concepts` (0 концептов, текст есть) / `no_text_layer` (детектор
     `pipeline._chunks_lack_text`: текст чанков минус markdown-ссылки на картинки
     < 200 симв. — скан-PDF без OCR) / `llm_partial_result` (был salvage) /
     `index_partial_failure` (чанк-индексация пропущена). «done + problem» —
     завершено без исключения, но может быть неполным; UI — янтарный бейдж
     (`problem_message` вычисляется из кода в DocumentOut), фильтр «Проблемные»
     включает problem IS NOT NULL. Ранний return «0 концептов → done без
     индексации» УДАЛЁН: чанки индексируются всегда (dual-index даёт
     поисковую представленность через chunk-ветку). Backfill тоже per-batch
     устойчив: сбойный батч логируется со счётчиками и не убивает остальные.
  5. **Дедуп table-концептов по (title, content)** (`field_table._dedup_key`,
     вскрыт regenerate-прогоном Регламента): голый title схлопывал РАЗНЫЕ
     строки перечня, когда LLM-классификатор недетерминированно выбирал
     неуникальную title-колонку (справочник «Номер ДП» 03713ad7: 1148 строк →
     46 концептов при title_col=BU_SORT2; справочник ФИО терял +79
     однофамильцев). Идентичные (title, content) — настоящий дубль (таблица,
     размазанная по чанкам) — по-прежнему схлопывается. Классификатор
     дополнительно получил None-толерантность: «title_col»: null /
     «description_cols»: null раньше валили int(None) → TypeError →
     ненужный fallback на XML-эвристику. Кэш классификаций
     (data/cache/table_classify) переживает рестарты, но НЕ regenerate
     (стирается осознанно — «пересчитать с нуля»); ключ кэша включает хэш
     промпта классификатора.
  **Инцидент ЗАКРЫТ 03.09.2026**, восстановление 03713ad7b0db416c (Регламент
  v8.9) тремя шагами: (1) resume — починка sparse/batching, 5667 концептов
  как были; (2) regenerate с парсером W6a — вскрыл дефект 5 (4573 концепта,
  chunk_04 схлопнулся в 46); (3) regenerate с дедуп-фиксом — итог:
  **5763 концепта, 5781 точка Qdrant (5763+18 чанков), problem=None**, ни
  одного salvage/classifier-fallback (обрезанные JSON вытянуты ретраями
  max_tokens). Per-chunk дифф трёх прогонов: chunk_04 1149→46→**1149**
  (дедуп-фикс), chunk_02 4240→4240→**4319** (дедуп резал и однофамильцев),
  chunk_00 2→20→2 и chunk_17 22→4→2 — LLM-вариативность на титульных
  таблицах/пустых бланках (2 концепта пустого шаблона корректны), НЕ потеря
  W6a-дыры: regenerate с починенным парсером не дал прироста сверх
  дедуп-фикса. Урок: «done без problem» после фиксов — единственный
  достоверный критерий полноты; сравнение per-chunk счётчиков между
  прогонами — способ локализации тихих потерь.
- **Комментарии рецензентов — программные концепты-треды (02.09.2026)**:
  docx-парсер собирает комментарии в треды «вопрос → ответы» по
  `commentsExtended.xml` (paraIdParent), извлекает дату, признак закрытия
  (done), контекст абзаца-якоря и секцию (meta.thread; эмит на первом
  физическом якоре любого участника; порядок тредов/записей — по числовому id,
  детерминированно — итерация set в старой версии рендерила ответ раньше
  вопроса; комментарии из ячеек таблиц — сразу после таблицы, не в конце).
  Формат блок-цитаты (`> **Контекст:** … / **Комментарий рецензента (автор,
  дата):** … / **Ответ (автор):** … / **Статус:** замечание закрыто`)
  распознаётся regex-экстрактором `services/comment_concepts.py`: в
  `generate_chunk` порядок **комментарии → таблицы → LLM** (порядок обязателен:
  незакрытая строка markdown-таблицы «заглатывает» последующие строки, при
  «таблицах первыми» блок-цитата комментария уходила в ячейку —
  `test_comment_concepts.TestExtractorOrderVsFieldTables`).   Концепт-тред:
  type note, tags `[review, comment, <фамилия автора вопроса>]` — фильтр по
  тегу `review` даёт «все замечания документа». Заголовок: короткий/неразговорный
  вопрос (< 5 слов, «Аналогично вопросу выше») дополняется префиксом контекста
  якоря — «Замечание рецензента (Если не найден ни один табельный…): Аналогично
  вопросу выше» (иначе блок неузнаваем в поиске/источниках; смена заголовков —
  re-run backfill, слаги/point_id меняются, orphan-cleanup скрипта закрывает).
  LLM получает заглушку
  `[Комментарии извлечены программно: N]` (правило 8 okf_system.md — замена,
  не дополнение). Флаг `okf_comment_concepts_enabled` (default True), кэша нет
  (детерминированный парсинг). Backfill старых документов без LLM:
  `backend/scripts/backfill_comment_concepts.py` (re-parse uploads → треды;
  chunk_index по raw-тексту вопроса в chunks/*.md; старые LLM-концепты с тегом
  review удаляются; бандл пересобирается `save_bundle`, `replace_concepts`
  перезаписывается ПОЛНЫМ списком — частичная запись стёрла бы остальные
  концепты; апсерт новых точек + orphan-cleanup, chunk-точки не трогаются;
  идемпотентен по итоговому набору).
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
- **Редактирование тегов после загрузки (Этап 4a, 31.08.2026)**:
  - `PATCH /documents/{id}/tags` — полная замена набора (роли editor/admin, любой документ);
    `POST /documents/bulk-tags` — delta `{doc_ids, add, remove}` (editor/admin, лимит
    `bulk_tags_max_docs=50`, синхронно, без four-eyes). Сервис `services/document_tag_service.py`.
  - Правка обновляет 4 слоя (дрейк тегов, MIGRATION_PLAN §10): `document_tags` (canonical),
    `okf_concepts.tags` (БД), frontmatter `.md`-бандлов (`tags`+`global_tags`, тело байт-в-байт),
    payload Qdrant `tags` (concept-точки — из `okf_concepts`, point_id = uuid5 от filepath
    бандла, БЕЗ scroll; chunk-точки — полный набор через filter set_payload). Синк Qdrant —
    фоновый (синхронно на десятки точек замораживал бы ответ; до 05.09.2026 усугублялось
    задержкой ~2с на каждый вызов из-за `localhost`-квирка), сериализован по документу
    с dirty-флагом; идемпотентен и самовосстанавливается на regenerate/resume.
  - Тег, равный номеру разработки: привязка/отвязка `development_id` + реиндекс `dev_tags`
    через `dev_sync` (без параллельного механизма); флаг `dev_tags_sync_pending` в ответе.
  - Каждая правка — `audit_log`: `document_tags_update` (одна) / `document_bulk_tags_update`
    (на каждый документ в bulk). Виджет — `TagPicker` (кастомный combobox: фильтр по подстроке,
    счётчики, топ-20 совпадений, клавиатура), общий словарь `lib/tagDictionary.js` (один
    `GET /api/tags` на страницу, `bumpTagVersion()` после любой мутации). Панель
    выделения `SelectionBar` (свёрнута под спойлер «Массовые действия», было
    `BulkActionsBar`; авто-раскрытие при появлении выделения):
    главный чекбокс страницы (indeterminate через ref — React не даёт prop),
    «Выделить все по фильтру» (кап 50 = `bulk_tags_max_docs`, тост при превышении),
    «Снять выделение»; массовые теги — одиночные
    `TagCombobox` (добавить — новый/существующий, убрать — только существующий).
    В карточке редактор тегов свёрнут под спойлер (read-only чипы + «✎»), раскрытие —
    по клику. Фильтр по тегу — селект «Все теги/тег (N)» в фильтр-баре, exact-match по
    `document_tags` (query-параметр `tag` в GET /documents); учитывается «Выделить все
    по фильтру».
  - **Чистка справочника («мусор»)**: `DELETE /tags/{tag}` и `POST /tags/cleanup` (editor/admin)
    удаляют только имена из пула автодополнения (`tags`), связи документов не трогаются.
    Счётчик и guard «используемый тег → 409» считают только АКТИВНЫЕ документы
    (`documents.deleted_at IS NULL`): тег, оставшийся только на доке(ах) в корзине,
    показывается count=0 и удаляется/чистится (06.09.2026 — баг «тег ааа не удалялся»,
    TagRegistry игнорировал deleted_at и запирал тег до purge корзины). Связь document_tags
    корзинного дока при этом не трогается: после восстановления тег снова честно
    используется (имя возвращается через счётчики `all()`/`add()` при правке тегов).
    Audit: `tag_delete`/`tag_cleanup`. UI — модалка
    `TagManagerModal` (кнопка «Справочник тегов» в фильтр-баре).
- **Корзина / soft delete (Этап 4a.2, 31.08.2026)**:
  - Удаление — мягкое: `documents.deleted_at`/`deleted_by` + payload Qdrant `deleted=true`
    (set_payload по doc_id, БЕЗ Delete Points). Восстановление — `POST /documents/{id}/restore`
    (`?force=true` пропускает дедуп-конфликт), массовое — `POST /documents/bulk-restore`.
    Список корзины — `GET /documents/trash` (объявлен ДО `/{doc_id}`!).
  - **Дисциплина `deleted`**: все пути поиска к Qdrant обязаны добавлять `must_not deleted`
    через `vector_store._not_deleted()`/`_build_search_filter()` (одно место). Дополнительно
    `/chat` и `/search` отсекают хиты, чей doc удалён в БД (защита от гонки синка payload).
    НЕ рассыпать фильтр по коду — новые сценарии поиска идут через обёртку.
  - Физическая очистка — фоновый демон-поток `services/trash.py` (`start_purge_loop` из
    lifespan) по `trash_retention_days=14`; audit `document_auto_delete` (system).
    Восстановление/масс-восстановление — `document_restore`/`document_bulk_restore`.
  - UI: переключатель «Документы/Корзина» в `DocumentsPanel.jsx` → `TrashPanel.jsx`;
    toast после удаления со ссылкой «Открыть корзину» (Toast поддерживает `action`).
- **Формы отображения: группировка + фильтры + URL-синк (Этап 5, 31.08.2026)**:
  - Группировка списка — на клиенте (`DocumentList.jsx`, `buildGroups`), НЕ SQL `GROUP BY`:
    режим «по тегам» требует multi-membership (док с N тегами входит в N групп), что JOIN
    размножает в дубли. В grouped-режиме `limit=None`, серверная пагинация отключена.
    Секции со sticky-заголовками; «Без тега»/«Без разработки» — в конце. В режиме
    «по тегам» селект тег-фильтра скрыт (вырожденный случай).
  - Фильтры дополнены статусом OKF (`status`: done / busy / failed,error) и диапазоном дат
    (`date_from`/`date_to` → `created_at`, `date_to` = конец дня UTC). Все фильтры списка
    перенесены в query-параметры URL (`q,uploader,module,tag,dev,problem,status,from,to,
    sort,page,group`) — shareable view; чтение только при init стейта, запись `router.replace`.
  - Прогресс разметки — `GET /documents/stats` → `{total, with_development}` по активной базе;
    плашка «Черновик — требует разметки» у готовых доков без `development_id`.
  - Чат (5.1): фильтр модуля — ряд «последних использованных» чипов (кап 6,
    персонально, `localStorage` `okf.recentModules.<username>`, чистая рекуррентность
    без таймстемпов; хелпер `lib/recentModules.mjs`, node-тестируемый) + inline-
    автодополнение `ModulePicker.jsx` (поиск по закрытому справочнику, не создаёт
    новые модули) вместо select/чипов всех модулей; выбор чипом или поиском идёт через
    один `onChange` (сброс `devFilter` — в ChatPanel). Бейджи «номер разработки»+
    «модуль» у цитат источников — `ChatSource` дополнен `development_number/name/module`,
    обогащается в `chat.py` из `doc_lookup`.
  - Не реализовано (отложено по решению): 5.2a (предупреждение о неточности списочных
    запросов) и 5.2b (structured-путь через реестр) — см. условие перехода в roadmap.
- **История чата (Этап 6, 31.08.2026)**:
  - Модели `chat_sessions` + `chat_messages` (`db/models.py`), Alembic-миграция
    `d1e2f3a4b5c6` (после `c9d4e5f6a7b8`). Сообщение хранит `sources` (JSON-снапшот
    источников на момент ответа, не протухает).
  - `session_id` — UUID, генерируемый **бэкендом** (`store_turn` при пустом
    `session_id`) и возвращаемый клиенту; фронт хранит его и передаёт в каждом
    следующем `/chat`. Бэкенд валидирует формат (`chat_history.is_valid_session_id`),
    **привязывает сессию к текущему `user_id`** — `store_turn`/`get_thread` бросают
    `ChatOwnershipError` при подмене чужого треда (не «присваивают» его). Ранний чек
    `check_session_state` до embed/search/LLM отсекает чужой/удалённый тред (403/409)
    без траты LLM-вызова. Запись в `/chat` — после ответа, не блокирует и не роняет
    чат (try/except → warning).
  - Сервис `services/chat_history.py` (по образцу `trash.py`): `list_sessions`,
    `get_thread`, `soft_delete_session`, `list_distinct_users`, `purge_expired_sessions`,
    `start_chat_purge_loop`. Роуты — `api/chat_history.py` (prefix `/chat`):
    own `/history`, `/history/{sid}`, `DELETE /history/{sid}`; admin
    `/admin/history/users`, `/admin/history/{user_id}`, `/admin/history/{user_id}/{sid}`
    (`require_role("security","admin")`).
  - Audit: `chat_history_view` — только при открытии чужого треда (admin-путь);
    список сессий и собственная история **не** логируются. `chat_history_auto_delete`
    (user_id=system) — автоочистка. Оба типа добавлены в `ACTION_TYPES` и в
    `EXPECTED_ACTION_TYPES` (`test_audit.py`).
  - Retention: активная история бессрочна; soft-deleted треды чистятся фоном после
    `chat_history_retention_days` (90, `.env`-override; `chat_history_purge_enabled`,
    `chat_history_purge_interval_seconds`). Purge-loop цепляется в `main.py` lifespan.
  - Фронт: `ChatContext` держит `sessionId` + `startNewChat`; `ChatPanel` — кнопки
    «История»/«Новый чат»; панели `ChatHistoryPanel` (своя, delete с подтверждением)
    и `AdminChatHistoryPanel` (поиск пользователя → сессии → read-only тред с плашкой
    «просмотр логируется»); страницы `app/chat/history/` и `app/chat/history/admin/`
    (gate `RequireRole`); ссылки в `Nav.jsx`.

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

## PostgreSQL — единственный источник истины (Этап 2b, завершён 05.09.2026)

Миграция «БД canonical / FS байты / Qdrant slim-проекция / бандлы экспорт» завершена.
Штатное чтение концептов, чанков, вложений и reindex идёт из PostgreSQL — `.md`-бандлы
НЕ участвуют в рабочем чтении.

- **Таблицы**: `document_chunks` (полный текст чанков, `UNIQUE(doc_id, chunk_index)`);
  `okf_attachments` (оживлена: `saved_path` относительный `attachments/<имя>`,
  `UNIQUE(doc_id, saved_path)`, `content_type/size/sha256/is_processable/extraction_status`);
  `okf_concepts` + provenance (`generated_at/model_id/prompt_version`). Alembic head `f0a1b2c3d4e5`.
- **FS**: только байты-источники — `data/uploads/<doc_id>.<ext>` и бинарные вложения
  `data/uploads/<doc_id>/attachments/`; `staging/`, `cache/`, `debug/`. Бандлы
  (`data/okf_bundles/`) удалены — генерация по запросу: `POST /documents/{id}/export-okf`
  или `python scripts/export_okf.py <doc_id>` (из БД).
- **Qdrant**: коллекция `okf_knowledge_base_v2` (`.env QDRANT_COLLECTION`), логические
  point_id `uuid5("okf:concept:{doc_id}:{slug}")` / `uuid5("okf:chunk:{doc_id}:{chunk_index}")`
  — единственные точки вычисления в `vector_store.concept_point_id/chunk_point_id`.
  Slim payload (без content/filepath/section_title); полный текст гидрируется из БД:
  концепты `concept_store.enrich_concept_hits` (по `(doc_id, slug)`), чанки
  `chunk_store.enrich_chunk_hits` (по `(doc_id, chunk_index)`).
- **Пайплайн**: финализация пишет чанки+концепты+вложения одной транзакцией
  (`session_scope` + session-passing `replace_chunks/replace_concepts/replace_attachments`)
  ДО Qdrant; провенанс per-chunk из `staging.chunks_data[].provenance`.
- **Скрипты**: `backfill_db_store.py` (одноразовый перенос корпуса в БД),
  `check_integrity.py` (БД↔FS↔Qdrant сверка), `rebuild_qdrant_v2.py` (пересборка
  индекса из БД), `export_okf.py`, `reindex.py` (теперь из БД). Диагностика/приёмка
  поиска — `probe_sources.py` (baseline в `scripts/probe-baseline.json`, gitignored).
- **Формула dense-эмбеддинга** едина (пайплайн = reindex = rebuild v2):
  концепт `title + "\n" + content[:okf_max_concept_chars]`, чанк `section_title + "\n" +
  text[:okf_max_chunk_index_chars]` — иначе rebuild из БД расходился бы со свежей
  индексацией.

## i18n (RU/EN) — свой лёгкий механизм (05.09.2026)

Локализация клиентского UI (RU + EN), переключатель `LocaleToggle` в топбаре сразу
после `ThemeToggle` (циклический RU → EN). Переводы — **отдельные JSON/JS-словари** в
`frontend/src/i18n/locales/` (`ru.js` — фолбэк, `en.js` — ключи ⊆ ru, `index.js` —
манифест). **Новый язык = копия словаря + одна запись в `index.js`.**

Ключевые файлы (по образцу рукописной темы, без новых зависимостей):
- `src/i18n/core.js` — чистая, node-тестируемая логика: `normalizeLocale`,
  `detectLocale` (браузер), `resolveServerLocale` (cookie + Accept-Language для SSR),
  `getMessages` (deep-merge ru+locale), `translate` (интерполяция `{param}`),
  `translatePlural` (CLDR-формы one/few/many/other), `formatDate/DateTime/Number` (Intl).
- `src/i18n/LocaleContext.jsx` — провайдер + `useI18n()` → `{ locale, t, tc, setLocale,
  fmtDate, fmtDateTime, fmtNumber }`. `setLocale` пишет localStorage **и cookie** `okf.locale`
  (cookie нужен только для SSR force-dynamic страниц), ставит `documentElement.lang` и
  обновляет `document.title`.
- `src/i18n/boot.js` — no-JS/edge фолбэк для `<html lang>`.
- `src/i18n/server.js` — `serverTranslator()` (cookie + Accept-Language) для RSC
  force-dynamic страниц (okf/fulltext/chunks) и `generateMetadata`.
- `src/i18n/titles.js` — карта pathname → ключ заголовка (клиентский `document.title`
  + `makeTitle`); node-тестируемая.

**Логика выбора языка:** `resolveServerLocale(cookie, Accept-Language)` — сохранённый
выбор (cookie/localStorage) побеждает, иначе авто по браузеру, фолбэк ru. В layout.js
`RootLayout` — **async**, читает `cookies()`/`headers()` и вычисляет `ssrLocale` → `<html
lang>` + `<LocaleProvider initialLocale={ssrLocale}>`. Это **переводит все маршруты в
dynamic-rendering (ƒ)** — осознанное решение: без SSR-локального рендера разметка (Nav,
заголовки) расходилась бы с языком клиента и давала hydration-mismatch. Для внутреннего
инструмента за авторизацией это приемлемо.

**Границы (не переводятся):** содержимое документов и LLM-ответы чата; backend-ошибки
(`err.message` в Toast остаются русскими); пользовательские данные (теги, номера/названия
разработок, модули, загрузчики). Русские код-комментарии не переводятся.

Тесты: `frontend/test/i18n.test.mjs` (`node --test`) — нормализация/детект/резолв SSR,
интерполяция, плюралы, fallback-merge, консистентность словарей (каждый en-ключ есть в
ru), полнота plural-форм.

## Многоязычность — стоп-слова (Этап 7, фаза A)

- **Индексная формула заморожена, динамика — только на стороне запроса.** Таблицы
  `locales`/`stopwords` (kind `bm25`|`marker`) + сервис `services/stopwords.py`
  (`get_stopwords(kind)` с TTL-кэшем + `invalidate()`, синхронно до ответа API).
  `sparse.to_sparse_vector`/`_sparse_text` (индексация) используют константу `_STOPWORDS`
  и **никогда** не читают таблицу — правка стоп-слов не требует реиндекса. Query-путь
  (`chat.py`/`search.py` sparse-вектор + `context_builder` маркеры Matched terms) берёт
  динамический набор. Добавление слова действует сразу (лечит EN-сценарий the/of/for);
  удаление дефолтного ru-слова возвращает его в запросы, но НЕ в индекс (предупреждение
  в UI).
- **Фолбэк при незасеянной БД:** если активных locales нет вовсе (dev `create_all` без
  `ensure_seeded`, или все отключены), `get_stopwords` возвращает ru-дефолт — поиск и
  маркеры не деградируют в пустой фильтр.
- **Сид**: `ensure_seeded()` (lifespan) — идемпотентный per-PK (проверка конкретных
  строк, не «таблица пуста»); Alembic-миграция `7a1b2c3d4e5f` (прод) сидит из того же
  единственного источника `stopwords.default_stopwords()` (ru ← `sparse._STOPWORDS` /
  `context_builder._MARKER_STOPWORDS`, en ← встроенный список). Дефолтные ru-слова,
  удалённые админом, восстанавливаются при старте (удаление слова из замороженного
  набора не имеет поискового эффекта).
- **Админ-контур** `api/admin_locales.py` (роль admin) + `GET /api/locales` (активные
  языки для LocaleToggle, ETag). Новые audit-действия `locale_*` / `stopwords_*`,
  target_type `locale`. Справочники/переводы (tag_id, UI-словари, промпты) — фазы B/C/D.
- **TODO (фаза D, 7.7):** в промптах (`okf_chunk.md`, `okf_system.md` §Language,
  `dev_number_system.md` §Language) захардкожено «исходный документ на русском /
  (Russian)». Это НЕ дрейф между источниками (файлы и код-дефолты `prompts/okf.py`
  синхронизированы построчно) — но при многоязычном корпусе станет неверным
  утверждением. Заменяется на `source_locale`/`content_language` в фазе D вместе с
  детекцией языка документа; сейчас не трогать.

## Многоязычность — теги и переводы (Этап 7, фаза B)

- **`tags` — суррогатный `id` + `canonical_text` (unique) + `canonical_locale` +
  `deleted_at`; `document_tags.tag_id` (FK).** `canonical_text` — стабильный wire- и
  поисковый идентификатор (payload Qdrant и `okf_concepts.tags` остаются текстом —
  реиндекс не нужен). `tag_translations` (PK tag_id+locale) — локализованные имена
  для не-канонических локалей. `development_translations` / `attribute_value_translations`
  — то же для названий разработок и label'ов атрибутов (канонические колонки
  `developments.name`/`attribute_values.label` не трогаются). Миграция — Alembic
  `8c3d4e5f6a7b` + companion `backend/scripts/migrate_tags_to_id.py` для dev-Postgres
  (Alembic-квирк create_all); обе сохраняют ОРФАН-теги (document_tags.tag без записи
  в пуле — создают строку tags). Миграция dev-PG выполнена 06.09.2026: 6 тегов,
  24 document_tags (1 утраченная строка — stale-ссылка на удалённый тест-тег «ааа»,
  отсутствовавший в concepts/Qdrant — безвредно).
- **Удаление тега = soft-delete (`tags.deleted_at`)**, НЕ физическое: связь
  document_tags корзинного документа не трогается (restore возвращает тег — баг
  06.09.2026 «тег ааа не удалялся» сохранён). `TagRegistry.all()` = пул
  (deleted_at IS NULL) ∪ теги, на которые ссылается АКТИВНЫЙ документ. Повторное
  использование текста тега возрождает его (`get_or_create_ids` ставит
  deleted_at=NULL). Единая точка создания тегов — `get_or_create_ids` (её зовут
  `registry.create/update` и upload); отдельный `TagRegistry.add` не нужен.
- **Провайдер переводов** `services/translation.py` (`translation_provider=llm|off`,
  `translation_model`): `translate_texts_batch` через `LLMClient(interactive=False)`
  (bulk-семафор — чат не блокируется). `backfill_reference_data(locale, entities,
  translations=None)` — idempotent: ручные переводы (reviewed_by) не перезаписываются.
  **Отклонение от плана:** backfill синхронный в admin-запросе, НЕ через job queue —
  очередь документоцентрична (submit(doc_ids)); операция не деструктивна и обратима.
- **API**: `GET /tags?needs_review=` (display по cookie `okf.locale`), `PATCH
  /tags/{id}/translations/{locale}`, `POST /tags/bulk-review` (editor/admin),
  `POST /tags/translations/backfill` (admin). Audit: `tag_translation_update/review`,
  `translations_backfill`. **НЕ реализовано (следом):** review-UI в TagManagerModal,
  display-локализация DocumentOut.tags / DevelopmentOut.name / AttributeValue (wire
  остаётся каноническим текстом — фронт совместим, поля `name`/`count` сохранены в
  TagOut).

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
