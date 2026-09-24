# Concept Source Highlighting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** При переходе из OKF-концепта в «Весь документ» или чанк сразу показывать и подсвечивать проверенное место происхождения концепта.

**Architecture:** Сохранять несколько диапазонов исходного текста на концепте в PostgreSQL; при чтении сверять диапазоны с текущим чанком и отдавать `exact`, `recovered`, `chunk` или `unavailable`. Две существующие страницы просмотра принимают `?concept=<slug>`, показывают Markdown по чанкам, раскрывают целевую часть большой таблицы, прокручивают и подсвечивают цель. Путь чата к концепту и существующие обычные URL сохраняются.

**Tech Stack:** Python/FastAPI, Pydantic, SQLAlchemy 2.0, Alembic, PostgreSQL/SQLite, Next.js App Router, React, react-markdown/remark-gfm, Node test runner, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-concept-source-highlighting-design.md`

**Baseline:** Проверено 24.09.2026: `ChatSource` ссылается на OKF-файл концепта или чанк; страница концепта извлекает `chunk_index` из frontmatter и ведёт на `/fulltext` и `/chunks/{index}`. `OkfConcept`/`Concept` не хранят диапазоны; `/fulltext` отдаёт склеенную строку `document_chunks`; `ContentViewer` разрезает большие таблицы, а скрытый хвост рендерится только после кнопки.

**Implementation status (24.09.2026):** Код хранения evidence, API, переходы/подсветка в UI и миграция добавлены. Исправлены координаты программных концептов комментариев и таблиц, проверка устаревших диапазонов, отображение нескольких диапазонов и прокрутка к строкам длинной таблицы. Проверены 166 профильных backend-тестов, 172 frontend-теста, production build Next.js и ручной проход в браузере на тестовой таблице из 330 строк. В полном backend-прогоне: 1603 passed, 5 skipped, 10 failed в `test_upsert_batching.py` при одновременном изменении файлов индексирования; сразу после него отдельный прогон этого файла: 20 passed. После обнаружения drift схема локальной PostgreSQL `okf_knowledge` дополнена `source_spans JSON` вручную; Alembic migration остаётся обязательной для других БД.

## Global Constraints

- Не менять клик по источнику из чата: концептный источник ведёт к концепту; чисто чанковый остаётся чанковым.
- Цель подсветки — извлечённый Markdown на существующих страницах документа/чанка, не бинарный PDF/DOCX/XLSX.
- Диапазоны хранятся относительно канонического `DocumentChunk.content` в Python Unicode code points; API и UI явно преобразуют смещения для JavaScript UTF-16.
- URL содержит `doc_id` и `concept_slug`, но не цитату, диапазон или путь на сервере.
- Только сохранённая привязка с совпадающим хешем получает `exact`; однозначное дословное восстановление старого концепта без диапазонов получает `recovered`; при отсутствии/устаревании — `chunk`, затем `unavailable`.
- Ошибка извлечения evidence не меняет статус генерации и не удаляет сам концепт.
- Старые концепты работают с `chunk_index` без массового regenerate; Qdrant реиндексировать не нужно.
- Сохранять текущий общий `main`; не создавать ветку/worktree/commit/PR/push без отдельного запроса пользователя. В плане нет шагов commit.
- Миграция Alembic обязательна для production; локальную dev PostgreSQL с известным дрейфом схемы не обновлять вслепую.
- Backend pytest на Windows запускать с локальными `TEMP`/`TMP`, отдельным `--basetemp` и без cacheprovider; не считать статическую проверку UI доказательством реальной прокрутки.

## Review Focus

Каждый пункт закреплён тестом в указанной задаче.

1. Одинаковая цитата встречается дважды — Task 3: LLM-диапазон отбрасывается, ложной точной подсветки нет.
2. В тексте есть emoji до диапазона — Task 5: подсвечивается правильный блок после преобразования code-point смещения в UTF-16.
3. Целевая строка таблицы находится за 300-й строкой — Task 5: хвост автоматически раскрывается и цель видима после прокрутки.
4. Концепт пережил изменение текста чанка — Task 4: несовпавший SHA-256 переводит `exact` в `chunk`.
5. Устаревший или поддельный slug в URL — Task 4: endpoint не показывает данные другого документа и возвращает штатный 404.

## Файлы и границы ответственности

| Узел | Изменения |
|---|---|
| Данные | `backend/app/models/schemas.py`, `backend/app/db/models.py`, `backend/app/services/concept_store.py`, новая Alembic revision: `source_spans` концепта |
| Вычисление evidence | новый `backend/app/services/source_evidence.py`; `okf_generator.py`, `field_table.py`, `comment_concepts.py`, prompt OKF, `pipeline.py`/staging |
| Чтение | `backend/app/api/documents.py`: местоположение концепта и упорядоченные чанки полного документа; существующие plaintext endpoints остаются |
| UX | страница OKF-концепта, страницы `/fulltext` и `/chunks/[chunkIndex]`, `ContentViewer`, `MarkdownViewer`, `largeTableSplit.mjs`, отдельный клиентский контроллер прокрутки/переключения |
| Проверка | узкие backend/frontend тесты, ручная проверка в запущенном UI на PDF, DOCX и XLSX корпусе |

---

### Task 1: Контракт диапазона и сохранение в БД

**Files:**
- Modify: `backend/app/models/schemas.py`, `backend/app/db/models.py`, `backend/app/services/concept_store.py`
- Create: новая revision в `backend/alembic/versions/` от фактического `alembic heads`
- Test: `backend/tests/test_source_evidence_store.py`

**Interfaces:**
- Produces: `Concept.source_spans: list[SourceSpan]`, где `SourceSpan(start: int, end: int, chunk_hash: str)`; nullable JSON `OkfConcept.source_spans`; `replace_concepts()` переносит поля из `OkfDocument.metadata`.
- Tasks 2–4 используют те же имена полей без преобразования формы.

- [ ] **Step 1: Зафиксировать схему тестом.** Создать документ, чанк `"А😀Б"` и концепт с диапазоном `[2,3)`; сериализация `Concept` через staging и `replace_concepts`/повторное чтение должна сохранить границы и `chunk_hash`. Старый концепт без поля должен читаться как пустой список.

```python
assert restored.source_spans == [{"start": 2, "end": 3, "chunk_hash": digest}]
assert legacy.source_spans in (None, [])
```

- [ ] **Step 2: Запустить `python -m pytest tests/test_source_evidence_store.py -q` из `backend`; получить красный тест отсутствующего поля.**
- [ ] **Step 3: Добавить Pydantic-поле, SQLAlchemy JSON-колонку и передачу через `build_okf_docs()` → `OkfDocument.metadata` → `replace_concepts()`.** В новой миграции добавить nullable JSON без backfill; `upgrade/downgrade` проверять на отдельной чистой SQLite/PG тестовой базе, не на локальной drifted dev-БД.

```python
class SourceSpan(BaseModel):
    start: int
    end: int
    chunk_hash: str

source_spans: Mapped[list | None] = mapped_column(JSON, nullable=True)
# build_okf_docs metadata: "source_spans": [s.model_dump() for s in concept.source_spans]
# replace_concepts: source_spans=meta.get("source_spans") or None
```
- [ ] **Step 4: Повторить узкий тест и существующие `test_db.py`, `test_backfill_db_store.py`, `test_pipeline_integration.py`; проверить, что отсутствие `source_spans` не меняет исторические данные.**

### Task 2: Диапазоны программных концептов

**Files:**
- Create: `backend/app/services/source_evidence.py`
- Modify: `backend/app/services/field_table.py`, `backend/app/services/comment_concepts.py`, `backend/app/services/okf_generator.py`
- Test: `backend/tests/test_source_evidence.py`, `backend/tests/test_field_table.py`, `backend/tests/test_comment_concepts.py`

**Interfaces:**
- Consumes: `SourceSpan` из Task 1.
- Produces: `span_for_lines(raw_chunk: str, start_line: int, end_line: int) -> SourceSpan`, где `end_line` исключается; диапазоны относятся к исходному чанку до замены таблицы/комментария заглушкой.

- [ ] **Step 1: Написать тесты для строки таблицы, обзора таблицы и треда комментария.** Два одинаковых значения в разных строках должны получить разные диапазоны по позиции строки, а не поиск по значению. Одна концептная запись может иметь несколько диапазонов.

```python
assert raw_chunk[row_span.start:row_span.end].strip() == table_row
assert raw_chunk[thread_span.start:thread_span.end].startswith("> **Контекст:**")
assert first_row_span.start != second_row_span.start
```

- [ ] **Step 2: Запустить `python -m pytest tests/test_source_evidence.py -q` и подтвердить красный результат.**
- [ ] **Step 3: Передавать начальные/конечные строки исходных блоков вместе с результатом программных экстракторов.** Считать смещения по оригинальным строкам до мутаций `lines[start:i]`; для обзорного концепта использовать весь табличный блок, для полевого концепта конкретную строку, для замечания весь тред с контекстом. Добавить SHA-256 исходного чанка.

```python
def span_for_lines(raw_chunk: str, start_line: int, end_line: int) -> SourceSpan:
    offsets = [0]
    for line in raw_chunk.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))
    start = offsets[start_line]
    end = offsets[end_line]
    return SourceSpan(start=start, end=end, chunk_hash=sha256(raw_chunk.encode("utf-8")).hexdigest())
```
- [ ] **Step 4: Запустить `python -m pytest tests/test_source_evidence.py tests/test_field_table.py tests/test_comment_concepts.py -q` и убедиться, что текст, количество, порядок и теги концептов не изменились.**

### Task 3: Доказательство происхождения LLM-концептов

**Files:**
- Modify: `backend/app/prompts/okf.py`, `backend/app/services/okf_generator.py`, `backend/app/models/schemas.py`
- Test: `backend/tests/test_okf.py`, `backend/tests/test_source_evidence.py`

**Interfaces:**
- Consumes: `SourceSpan` и SHA-256 исходного чанка из Tasks 1–2.
- Produces: необязательные `source_quotes: list[str]` (только generation/staging) и проверенные `source_spans`; `source_quotes` не попадают в поисковый контент или OKF Markdown.

- [ ] **Step 1: Написать тесты на одну/несколько дословных цитат, отсутствующую цитату, повторяющуюся цитату, split после truncation и прежний ответ LLM без нового поля.** Ожидание: старый JSON остаётся валидным, сомнительная привязка даёт пустые `source_spans`, концепт сохраняется.

```python
assert locate_unique_quote("А Б А", "А") is None
assert locate_unique_quote("А Б В", "Б") == (2, 3)
assert len(concepts_without_quotes) == 1
assert concepts_without_quotes[0].source_spans == []
```

- [ ] **Step 2: Запустить `python -m pytest tests/test_okf.py tests/test_source_evidence.py -q` и подтвердить красный результат.**
- [ ] **Step 3: В prompt попросить до трёх коротких фрагментов, дословно присутствующих во входном тексте, отдельным необязательным полем `source_quotes`.** Не менять правила генерации `title/content`. Валидатор ищет каждый фрагмент в исходном чанке; при 0 или более 1 совпадении пропускает его. Не привязывать концепт по сходству заголовка/пересказа. При рекурсивном split привязки сверяются с первоначальным сырым чанком.

```python
def locate_unique_quote(raw_chunk: str, quote: str) -> tuple[int, int] | None:
    if not quote:
        return None
    start = raw_chunk.find(quote)
    if start < 0 or raw_chunk.find(quote, start + 1) >= 0:
        return None
    return start, start + len(quote)
```
- [ ] **Step 4: Запустить `python -m pytest tests/test_okf.py tests/test_llm_client.py tests/test_source_evidence.py -q`; на небольшом локальном фиксированном корпусе сравнить количество концептов до/после и долю `exact` без обязательной регенерации всего корпуса.** Если новый формат повышает число ошибок/потерь концептов, сузить число/длину цитат до сохранения прежней полноты.

### Task 4: Read-only API местоположения

**Files:**
- Modify: `backend/app/api/documents.py`, `backend/app/models/schemas.py`
- Create: `backend/app/services/source_location.py`
- Test: `backend/tests/test_source_location_api.py`

**Interfaces:**
- `GET /api/documents/{doc_id}/concepts/{slug}/source-location` → `{status: "exact"|"chunk"|"unavailable", chunk_index: int|null, spans: [{start,end,quote}]}`.
- `GET /api/documents/{doc_id}/fulltext/chunks` → `[{chunk_index, content}]` по возрастанию индекса. Старые `/fulltext` и `/chunks/{index}` неизменны.
- `spans` хранят Python code-point offsets; frontend конвертирует при работе с JS string.

- [ ] **Step 1: Написать API-тесты `exact`, исторического `chunk`, mismatch хэша, повреждённых/выходящих за пределы диапазонов, отсутствующего чанка, чужого `doc_id` и slug, невалидного doc ID и упорядоченных чанков.** Повреждённая привязка не вызывает 500 и не раскрывает чужой текст.

```python
assert exact["status"] == "exact" and exact["spans"][0]["quote"] == "исходный текст"
assert stale["status"] == "chunk" and stale["spans"] == []
assert wrong_doc.status_code == 404
```

- [ ] **Step 2: Запустить `python -m pytest tests/test_source_location_api.py -q` и подтвердить красный результат.**
- [ ] **Step 3: Реализовать чтение из `OkfConcept` и `DocumentChunk` одной согласованной DB-сессией.** Проверять `doc_id`, `slug`, хэш SHA-256 фактического content, `0 <= start < end <= len(content)` и порядок диапазонов. Цитату ограничить для UI, но диапазон сохранить полный. При ошибке данных вернуть `chunk`/`unavailable`; не доверять позиции из query string.

```python
def resolve_source_location(session, doc_id: str, slug: str) -> SourceLocationOut:
    concept = session.execute(select(OkfConcept).where(
        OkfConcept.doc_id == doc_id, OkfConcept.slug == slug
    )).scalar_one_or_none()
    if concept is None:
        raise ApiError(status_code=404, code=errors.DOCUMENT_NOT_FOUND, detail="Концепт не найден")
    chunk = session.execute(select(DocumentChunk).where(
        DocumentChunk.doc_id == doc_id,
        DocumentChunk.chunk_index == concept.chunk_index,
    )).scalar_one_or_none()
    if chunk is None:
        return SourceLocationOut(status="unavailable", chunk_index=concept.chunk_index, spans=[])
    digest = sha256(chunk.content.encode("utf-8")).hexdigest()
    valid = []
    for s in concept.source_spans or []:
        if not isinstance(s, dict):
            continue
        start, end = s.get("start"), s.get("end")
        if (s.get("chunk_hash") == digest and type(start) is int and type(end) is int
                and 0 <= start < end <= len(chunk.content)):
            valid.append(s)
    return SourceLocationOut(status="exact" if valid else "chunk", chunk_index=chunk.chunk_index,
                             spans=[{"start": s["start"], "end": s["end"],
                                     "quote": chunk.content[s["start"]:s["end"]][:240]}
                                    for s in valid])
```
- [ ] **Step 4: Повторить API-тесты и существующие проверки чтения OKF/чанков/полного текста.**

### Task 5: Переходы и подсветка на страницах просмотра

**Files:**
- Modify: `frontend/src/app/documents/[docId]/okf/[...filename]/page.js`, `frontend/src/app/documents/[docId]/fulltext/page.js`, `frontend/src/app/documents/[docId]/chunks/[chunkIndex]/page.js`
- Modify: `frontend/src/components/ContentViewer.jsx`, `frontend/src/components/MarkdownViewer.jsx`, `frontend/src/lib/largeTableSplit.mjs`
- Create: `frontend/src/components/SourceLocationView.jsx`, `frontend/src/lib/sourceLocation.mjs`
- Modify: UI-переводы `backend/app/i18n/ui_ru.json` и `backend/app/i18n/ui_en.json`
- Test: `frontend/test/sourceLocation.test.mjs`, `frontend/test/largeTableSplit.test.mjs`; браузерная проверка ниже

**Interfaces:**
- Концептная страница строит `/documents/{docId}/fulltext?concept={slug}` и `/documents/{docId}/chunks/{index}?concept={slug}`; существующие ссылки без query остаются прежними.
- `SourceLocationView` получает упорядоченные чанки и ответ Task 4; для `exact` рендерит подсвеченные семантические блоки, для `chunk` скроллит к началу чанка.

- [ ] **Step 1: Написать чистые тесты преобразования code-point → UTF-16, выбора целевой части таблицы и нормализации состояния (`exact/chunk/unavailable`).** Добавить тест целевой строки после 300-й с автоматическим раскрытием хвоста. Проверить обычный рендер без `concept`.

```js
assert.equal(codePointToUtf16Offset("А😀Б", 2), 3);
assert.equal(targetPartForRange(parts, sourceRangeAfterRow300).type, "tableRemainder");
```

- [ ] **Step 2: Запустить Node-тесты и подтвердить красный результат.**
- [ ] **Step 3: На странице концепта добавить блок «Место в источнике» и две ссылки с `concept_slug`.** Получать состояние/цитату через Task 4; не менять `sourceHref()` чата. Отсутствие чанка скрывает только действие «Показать в чанке» и объясняет причину.

```js
const sourceQuery = `?concept=${encodeURIComponent(slug)}`;
const documentHref = `/documents/${docId}/fulltext${sourceQuery}`;
const chunkHref = chunkIndex == null ? null : `/documents/${docId}/chunks/${chunkIndex}${sourceQuery}`;
```
- [ ] **Step 4: На страницах документа/чанка загружать местоположение только при наличии `concept`.** Для fulltext рендерить упорядоченные чанки из Task 4 с DOM-якорем на каждом чанке. В `largeTableSplit` сохранить соответствие нарезанных строк исходным смещениям, в `ContentViewer` автоматически раскрыть нужный хвост. Если ручной URL указывает на иной чанк, перейти на канонический `chunk_index` концепта без подсветки чужого чанка.

```js
const target = location.status === "exact" ? location.spans[0] : null;
const targetChunk = location.chunk_index;
const parts = splitLargeTables(chunk.content);
const openPart = target ? targetPartForRange(parts, target) : null;
```
- [ ] **Step 5: В Markdown-рендере помечать пересекающиеся с диапазоном абзац, строку таблицы, цитату или code block.** После появления целевого DOM-узла выполнить `scrollIntoView({block: "center"})`; для нескольких диапазонов показать «N из M» и кнопки предыдущий/следующий. Использовать заметную устойчивую подсветку и `aria-current`/доступный текст состояния. В raw-режиме подсвечивать тот же диапазон в escaped-тексте. Если точный узел не появился, перейти к `chunk` и показать честную пометку.

```js
useEffect(() => {
  targetRef.current?.scrollIntoView({ block: "center", behavior: "auto" });
}, [activeSpanIndex, expandedPart]);
```
- [ ] **Step 6: Запустить Node-тесты, ESLint и Next build; затем вручную проверить прямой URL, перезагрузку, браузерный Back, `render/raw`, длинную таблицу, мобильную ширину и сохранение подсветки при переключении мест.** Статические тесты не заменяют ручную проверку фактического скролла.

### Task 6: Сквозная приёмка и документация

**Files:**
- Test: `backend/tests/test_pipeline_integration.py`, `backend/tests/test_source_location_api.py`, `frontend/test/sourceLocation.test.mjs`
- Modify: `docs/OKF_User_Guide.md` с описанием страницы концепта и точности перехода
- Modify: этот план — отмечать задачи только после свежей проверки

**Interfaces:**
- Сквозной критерий: чатный источник открывает концепт; обе кнопки концепта открывают нужный документ/чанк, подсветка видна без ручного поиска.

- [ ] **Step 1: Собрать отдельный маленький корпус: абзац DOCX, строка XLSX после 300-й, страница PDF с текстовым слоем, тред комментария, концепт с двумя evidence и старый концепт без evidence.** Измерить отсутствие ложного `exact` для повторяющейся цитаты и устаревшего хэша.
- [ ] **Step 2: Запустить backend узкие тесты с локальными временными каталогами, frontend `node --test` и ESLint; затем проверку изменённого UI в работающем приложении по цепочке «чат → концепт → документ/чанк».** Зафиксировать число успешных тестов и фактическое поведение каждого из трёх состояний.
- [ ] **Step 3: Проверить миграцию на отдельной чистой БД, отсутствие изменения старых chat source URL и отсутствие новой нагрузки на Qdrant; описать ограничения нативных файлов и старых концептов в пользовательском документе.**
- [ ] **Step 4: Провести `git diff --check` и сверить каждый пункт спецификации с выполненными задачами.** Не выполнять commit/push без отдельного запроса.

## Порядок запуска тестов на Windows

Из корня репозитория создать `tests/tmp/source-highlight`, затем в той же PowerShell-сессии:

```powershell
$taskTmp = (Resolve-Path 'tests/tmp/source-highlight').Path
$env:TEMP = $taskTmp
$env:TMP = $taskTmp
Push-Location backend
python -m pytest tests/test_source_evidence_store.py tests/test_source_evidence.py tests/test_source_location_api.py --basetemp=../tests/tmp/source-highlight/pytest -p no:cacheprovider -q
Pop-Location
Push-Location frontend
node --test test/sourceLocation.test.mjs test/largeTableSplit.test.mjs
node node_modules/eslint/bin/eslint.js src/components/SourceLocationView.jsx src/lib/sourceLocation.mjs
node node_modules/next/dist/bin/next build
Pop-Location
```

Если текущая dev PostgreSQL имеет дрейф относительно Alembic, для миграционного теста использовать отдельную чистую БД; `create_all()` не заменяет миграцию существующих колонок.
