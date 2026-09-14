# Stage 8 Table Classifier Indexing Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Устранить ложные и повторяющиеся `llm_partial_result` при классификации таблиц, восстановить полную табличную индексацию документа 3409 в изолированном контуре Stage 8 и сохранить строгую защиту генерации концептов от тихой потери JSON-хвоста.

**Architecture:** Общий `LLMClient` получает явно ограниченный режим ответа «один JSON-объект», который используется только табличным классификатором. Генерация массивов концептов остаётся в прежнем строгом режиме. Телеметрия `llm_salvage` и `classifier_fallback` агрегируется в разные problem-коды, после чего исправление проверяется сначала тестами, затем реальной регенерацией в `okf_stage8_test` и сверкой БД с `okf_knowledge_stage8_test`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, PostgreSQL 17, Qdrant 1.19, LiteLLM/OpenRouter, pytest, PowerShell.

**Spec:** Согласованный в диалоге ограниченный дизайн от 2026-09-13; отдельный spec-файл не создаётся для локального исправления существующего потока.

## Global Constraints

- Исправление вносится в общий backend и не содержит ветвлений по имени контура.
- Основной и тестовый контуры продолжают использовать отдельные БД, Qdrant-коллекции и data-dir.
- До завершения приёмки активным остаётся Stage 8: `okf_stage8_test`, `okf_knowledge_stage8_test`, `./tests/tmp/stage8-data`.
- Не ослаблять строгую обработку массивов концептов: `finish_reason="length"`, незакрытый JSON и дополнительная JSON-структура должны по-прежнему приводить к retry/`LLMTruncationError`, а salvage обязан выставлять `llm_partial_result`.
- Послабление разрешено только для контракта «ровно один объект классификации таблицы»: первый полный `dict` можно принять при лишнем структурированном хвосте, если `finish_reason != "length"`.
- Настоящий fallback табличного классификатора не скрывать: он получает отдельный problem-код и точное сообщение.
- Не очищать `documents.problem` вручную и не считать одну лишь внутреннюю согласованность 113 точек доказательством полноты.
- Не менять основную БД и основную Qdrant-коллекцию во время Stage 8-приёмки.
- Сохранить существующие незакоммиченные изменения. Не создавать worktree/ветку, не коммитить, не сливать и не открывать PR без отдельной команды пользователя.
- Все изменения выполнять через TDD: сначала наблюдаемый RED, затем минимальный GREEN.
- После каждого проверенного этапа обновлять чекбоксы этого плана; незавершённые пункты оставлять открытыми.

---

### Task 1: Разделить контракты JSON для концептов и одиночного классификатора

**Files:**
- Modify: `backend/app/services/llm_client.py:401-451`
- Modify: `backend/app/services/llm_client.py:489-625`
- Test: `backend/tests/test_llm_client.py:520-610`
- Test: `backend/tests/test_llm_client.py:660-745`

**Interfaces:**
- Consumes: существующий `LLMClient.chat_json(...)`, `_chat_json_with_truncation_retry(...)`, `_parse_json(...)`.
- Produces: необязательный аргумент `single_object: bool = False` во всех трёх функциях; в режиме `True` возвращается первый полный `dict` одиночного ответа, а режим по умолчанию полностью сохраняет прежнюю строгость.

- [x] **Step 1: Зафиксировать строгий режим массива как неизменяемый контракт**

Добавить или оставить явный регрессионный тест:

```python
def test_default_mode_rejects_closed_json_with_structured_tail(self):
    from app.services.llm_client import _parse_json

    raw = '{"concept_per_row":true,"title_col":0} {"concept_per_row":true,"title_col":1}'
    with pytest.raises(LLMTruncationError, match="после закрытой JSON-структуры"):
        _parse_json(raw)
```

- [x] **Step 2: Написать падающий тест одиночного объекта**

```python
def test_single_object_mode_accepts_first_complete_dict_with_structured_tail(self):
    from app.services.llm_client import _parse_json
    from app.services import gen_quality

    gen_quality.drain()
    raw = (
        '{"concept_per_row":true,"title_col":0,"description_cols":[1],'
        '"concept_type":"reference","extraction_mode":"per_row"}'
        ' {"concept_per_row":true,"title_col":0}'
    )
    parsed = _parse_json(raw, single_object=True)
    assert parsed["title_col"] == 0
    assert gen_quality.drain() == []
```

- [x] **Step 3: Написать падающий тест защиты `finish_reason="length"` в новом режиме**

```python
def test_single_object_mode_never_accepts_finish_reason_length(self):
    from app.services.llm_client import _parse_json

    raw = '{"concept_per_row":true,"title_col":0}'
    with pytest.raises(LLMTruncationError, match="обрезан по лимиту токенов"):
        _parse_json(raw, finish_reason="length", single_object=True)
```

- [x] **Step 4: Запустить RED-тесты**

Run from `backend/`:

```powershell
New-Item -ItemType Directory -Force .pytest-table-json-red | Out-Null
$env:TEMP = (Resolve-Path .pytest-table-json-red).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest `
  tests/test_llm_client.py::TestParseJson::test_single_object_mode_accepts_first_complete_dict_with_structured_tail `
  tests/test_llm_client.py::TestParseJson::test_single_object_mode_never_accepts_finish_reason_length `
  -q -p no:cacheprovider --basetemp=.pytest-table-json-red/run
```

Expected: FAIL с `TypeError: _parse_json() got an unexpected keyword argument 'single_object'`. Строгий существующий тест должен оставаться PASS.

- [x] **Step 5: Реализовать минимальный режим `single_object`**

Изменить сигнатуры и проброс параметра:

```python
def chat_json(
    self,
    system: str,
    user: str,
    doc_id: str = "unknown",
    chunk_idx: int = 0,
    salvage_truncated: bool = False,
    single_object: bool = False,
) -> list | dict:
    return self._chat_json_with_truncation_retry(
        system, user, doc_id, chunk_idx, salvage_truncated, single_object
    )
```

`_chat_json_with_truncation_retry(...)` обязан передать `single_object` в `_parse_json(...)` на каждой попытке. В `_parse_json(...)` обработка `finish_reason="length"` остаётся первой и неизменной. До общей проверки `_is_truncated(fragment)` добавить узко ограниченную ветку:

```python
if single_object:
    closed = _top_level_close_pos(fragment)
    if closed != -1:
        prefix = fragment[: closed + 1].strip()
        first = _try_load(prefix)
        if isinstance(first, dict):
            tail = fragment[closed + 1 :].strip()
            if tail:
                logger.warning(
                    "[%s] Чанк %s: лишний хвост после одиночного JSON-объекта классификатора отброшен",
                    doc_id,
                    chunk_idx,
                )
            return first
```

Не записывать `gen_quality.LLM_SALVAGE`: классификатор запросил одно решение и получил полный первый объект. Если первый полный элемент не `dict`, продолжать существующий строгий каскад; не превращать список в объект.

- [x] **Step 6: Запустить GREEN и полный набор тестов парсера**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_llm_client.py `
  -q -p no:cacheprovider --basetemp=.pytest-table-json-red/run-green
```

Expected: PASS; особенно должны остаться зелёными `test_closed_array_with_json_tail_raises_truncation`, `test_closed_array_with_open_json_tail_raises_truncation`, `test_salvage_closed_array_json_tail_returns_prefix_and_records` и retry-тесты.

- [x] **Step 7: Проверить diff только по Task 1**

```powershell
git diff --check -- backend/app/services/llm_client.py backend/tests/test_llm_client.py
git diff -- backend/app/services/llm_client.py backend/tests/test_llm_client.py
```

Убедиться, что `single_object=False` сохраняет бинарно прежнюю ветку поведения, кроме уточнённых docstring/type hints.

---

### Task 2: Включить одиночный JSON-контракт только для табличного классификатора

**Files:**
- Modify: `backend/app/services/field_table.py:424-436`
- Modify: `backend/app/services/field_table.py:593-635`
- Test: `backend/tests/test_field_table.py:443-490`
- Test: `backend/tests/test_field_table.py:590-675`

**Interfaces:**
- Consumes: `LLMClient.chat_json(..., single_object=True)` из Task 1.
- Produces: `_llm_classify_table(...) -> TableClassification`, который явно запрашивает одиночный объект; все тестовые реализации `_ClassifierLLM` принимают новый необязательный параметр.

- [x] **Step 1: Обновить тестовые doubles только на уровне сигнатуры**

Во всех `chat_json` внутри `backend/tests/test_field_table.py` добавить `single_object=False`, не меняя возвращаемых данных:

```python
def chat_json(
    self,
    system,
    user,
    doc_id="unknown",
    chunk_idx=0,
    salvage_truncated=False,
    single_object=False,
):
    ...
```

Перед правкой найти все реализации:

```powershell
rg -n "def chat_json" backend/tests backend/app/services/field_table.py
```

- [x] **Step 2: Написать падающий контрактный тест классификатора**

```python
def test_table_classifier_requests_single_json_object(self, tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.field_table.get_settings", lambda: get_settings())
    settings = get_settings()
    monkeypatch.setattr(settings, "okf_field_table_min_rows", 5)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    class RecordingLLM:
        single_object = None

        def chat_json(
            self,
            system,
            user,
            doc_id="unknown",
            chunk_idx=0,
            salvage_truncated=False,
            single_object=False,
        ):
            self.single_object = single_object
            return {
                "concept_per_row": True,
                "title_col": 0,
                "description_cols": [4],
                "concept_type": "reference",
                "extraction_mode": "per_row",
            }

    llm = RecordingLLM()
    concepts, _ = extract_table_concepts(
        FIELD_TABLE, chunk_index=1, llm=llm, use_llm_classify=True
    )
    assert llm.single_object is True
    assert len(concepts) >= 6
```

- [x] **Step 3: Запустить RED-тест**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_field_table.py::TestLLMClassifier::test_table_classifier_requests_single_json_object `
  -q -p no:cacheprovider --basetemp=.pytest-table-json-red/field-red
```

Expected: FAIL `assert False is True` либо `assert None is True`, потому что `_llm_classify_table` пока не передаёт новый режим.

- [x] **Step 4: Обновить production-протокол и единственный вызов**

В `_ClassifierLLM.chat_json` добавить `single_object: bool = False`. В `_llm_classify_table` заменить вызов на:

```python
raw = llm.chat_json(
    system,
    user,
    doc_id=doc_id,
    chunk_idx=chunk_idx,
    single_object=True,
)
```

Оставить существующую валидацию `isinstance(raw, dict)`, нормализацию `None` и cache-key без изменений.

- [x] **Step 5: Запустить GREEN и все тесты таблиц**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_field_table.py `
  -q -p no:cacheprovider --basetemp=.pytest-table-json-red/field-green
```

Expected: PASS. Отдельно проверить тесты fallback, cache hit/miss, дедупликацию по `(title, content)` и `None`-толерантность.

- [x] **Step 6: Проверить отсутствие случайного включения режима для генерации концептов**

```powershell
rg -n "single_object=True" backend/app
```

Expected: ровно один production-вызов в `field_table._llm_classify_table`. `okf_generator` и прочие вызовы `chat_json` не должны использовать этот режим.

---

### Task 3: Развести настоящую частичную генерацию и fallback классификатора

**Files:**
- Modify: `backend/app/services/problem_codes.py:15-43`
- Modify: `backend/app/services/pipeline.py:557-580`
- Modify: `backend/app/services/gen_quality.py:1-29`
- Modify: `backend/app/db/models.py:147-151`
- Test: `backend/tests/test_pipeline_integration.py:258-305`

**Interfaces:**
- Consumes: события `gen_quality.LLM_SALVAGE` и `gen_quality.CLASSIFIER_FALLBACK`, сохранённые в `chunks_data[*].degradation`.
- Produces: `problem_codes.LLM_CLASSIFIER_FALLBACK = "llm_classifier_fallback"` и чистая функция `_generation_problem(chunks_data: dict) -> str | None`.

- [x] **Step 1: Написать падающий тест для одного classifier fallback**

Добавить рядом с существующим `test_degradation_events_aggregate_to_problem`:

```python
def test_classifier_fallback_has_accurate_problem_code(self, isolated_env, monkeypatch):
    from app.services import gen_quality

    reg, src = isolated_env
    doc_id = "classifier-fallback"
    reg.create(doc_id, "test.doc", "doc", 100)

    def generate_with_classifier_fallback(*args, **kwargs):
        gen_quality.record(gen_quality.CLASSIFIER_FALLBACK, "chunk 1: test")
        return [_concept()]

    pipeline = Pipeline()
    pipeline.okf_generator.generate_chunk = generate_with_classifier_fallback
    pipeline.vector_store.ensure_collection = lambda: None
    pipeline.vector_store.delete_document = lambda *a, **k: None
    pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
    pipeline.vector_store.index_concepts = lambda *a, **k: set()
    pipeline.vector_store.index_chunks = lambda *a, **k: set()

    gen_quality.drain()
    pipeline._process(doc_id, src, "test.doc", [], resume=False)

    doc = reg.get(doc_id)
    assert doc["status"] == "done"
    assert doc["problem"] == "llm_classifier_fallback"
```

- [x] **Step 2: Написать падающий тест приоритета salvage**

```python
def test_salvage_problem_precedes_classifier_fallback(self, isolated_env, monkeypatch):
    from app.services import gen_quality

    reg, src = isolated_env
    doc_id = "salvage-and-classifier"
    reg.create(doc_id, "test.doc", "doc", 100)

    def generate_with_both(*args, **kwargs):
        gen_quality.record(gen_quality.CLASSIFIER_FALLBACK, "chunk 1: classifier")
        gen_quality.record(gen_quality.LLM_SALVAGE, "chunk 1: salvage")
        return [_concept()]

    pipeline = Pipeline()
    pipeline.okf_generator.generate_chunk = generate_with_both
    pipeline.vector_store.ensure_collection = lambda: None
    pipeline.vector_store.delete_document = lambda *a, **k: None
    pipeline.vector_store.delete_orphaned_points = lambda *a, **k: None
    pipeline.vector_store.index_concepts = lambda *a, **k: set()
    pipeline.vector_store.index_chunks = lambda *a, **k: set()

    gen_quality.drain()
    pipeline._process(doc_id, src, "test.doc", [], resume=False)
    assert reg.get(doc_id)["problem"] == "llm_partial_result"
```

- [x] **Step 3: Запустить RED-тесты**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_pipeline_integration.py::TestPipelineNoConcepts::test_classifier_fallback_has_accurate_problem_code `
  tests/test_pipeline_integration.py::TestPipelineNoConcepts::test_salvage_problem_precedes_classifier_fallback `
  -q -p no:cacheprovider --basetemp=.pytest-table-json-red/problem-red
```

Expected: первый тест FAIL, потому что classifier-only сейчас ошибочно даёт `llm_partial_result`; второй уже может быть PASS и фиксирует неизменяемый приоритет настоящего salvage.

- [x] **Step 4: Добавить новый problem-код и сообщение**

В `problem_codes.py`:

```python
LLM_CLASSIFIER_FALLBACK = "llm_classifier_fallback"
```

Сообщение:

```python
LLM_CLASSIFIER_FALLBACK: (
    "Классификатор таблиц использовал резервную эвристику — индекс создан, "
    "но часть табличных концептов может быть менее точной или отсутствовать."
),
```

Текст не должен утверждать, что концепты были «сохранены частично после обрезания».

- [x] **Step 5: Реализовать детерминированную агрегацию событий**

В `pipeline.py` добавить рядом с другими module-level helpers:

```python
def _generation_problem(chunks_data: dict) -> str | None:
    events = {
        item.get("event")
        for info in chunks_data.values()
        for item in ((info or {}).get("degradation") or [])
        if isinstance(item, dict)
    }
    if gen_quality.LLM_SALVAGE in events:
        return problem_codes.LLM_PARTIAL_RESULT
    if gen_quality.CLASSIFIER_FALLBACK in events:
        return problem_codes.LLM_CLASSIFIER_FALLBACK
    return None
```

Во `_finalize` заменить `elif any(...degradation...)` на вычисление `_generation_problem(chunks_data)`. Сохранить приоритет:

```text
no_text_layer / no_concepts
    > llm_partial_result
    > llm_classifier_fallback
    > index_partial_failure
```

`problem = problem or INDEX_PARTIAL_FAILURE` оставить без изменения.

- [x] **Step 6: Обновить комментарии без изменения схемы БД**

Обновить перечни допустимых кодов и docstring в `models.py`, `gen_quality.py`, `pipeline.py`, `problem_codes.py`. Новая миграция не нужна: колонка `documents.problem` уже `VARCHAR(64)` и принимает новый строковый код.

- [x] **Step 7: Запустить GREEN и регрессию problem-кодов**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/test_pipeline_integration.py `
  tests/test_schema_drift.py `
  -q -p no:cacheprovider --basetemp=.pytest-table-json-red/problem-green
```

Expected: PASS; существующий salvage-тест по-прежнему ожидает `llm_partial_result`, чистый прогон — `None`.

---

### Task 4: Сквозная регрессия общего backend

**Files:**
- Verify: `backend/app/services/llm_client.py`
- Verify: `backend/app/services/field_table.py`
- Verify: `backend/app/services/pipeline.py`
- Verify: `backend/app/services/problem_codes.py`
- Verify: `backend/tests/test_llm_client.py`
- Verify: `backend/tests/test_field_table.py`
- Verify: `backend/tests/test_pipeline_integration.py`

**Interfaces:**
- Consumes: изменения Tasks 1-3.
- Produces: проверенный общий backend без contour-specific логики.

- [x] **Step 1: Запустить объединённый целевой набор**

From `backend/`:

```powershell
New-Item -ItemType Directory -Force .pytest-table-classifier-final | Out-Null
$env:TEMP = (Resolve-Path .pytest-table-classifier-final).Path
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe -m pytest `
  tests/test_llm_client.py `
  tests/test_field_table.py `
  tests/test_pipeline_integration.py `
  tests/test_schema_drift.py `
  -q -p no:cacheprovider --basetemp=.pytest-table-classifier-final/run
```

Expected: PASS без warnings/errors, относящихся к новому контракту.

- [x] **Step 2: Запустить полный backend-suite**

```powershell
.\.venv\Scripts\python.exe -m pytest `
  -q -p no:cacheprovider --basetemp=.pytest-table-classifier-final/full
```

Expected: PASS. Если проявится известный order-sensitive `_inflight_over_limit_is_logged`, отдельно зафиксировать:

1. результат изолированного теста;
2. результат suite с этим одним тестом deselected;
3. не приписывать этот флейк текущему исправлению без воспроизводимой связи.

- [x] **Step 3: Проверить статический diff и отсутствие секретов/contour names в production-коде**

```powershell
git diff --check
rg -n "okf_stage8_test|okf_knowledge_stage8_test" backend/app
git diff -- backend/app/services/llm_client.py backend/app/services/field_table.py backend/app/services/pipeline.py backend/app/services/problem_codes.py backend/app/services/gen_quality.py backend/app/db/models.py backend/tests/test_llm_client.py backend/tests/test_field_table.py backend/tests/test_pipeline_integration.py
```

Expected: `rg` не находит имён тестового контура в `backend/app`; исправление действительно глобальное.

- [x] **Step 4: Обновить `AGENTS.md` после подтверждённой регрессии**

Добавить в описание инцидента краткое правило:

```text
Табличный классификатор ожидает один JSON-объект и может отбросить лишний
структурированный хвост только в single_object-режиме. Генерация массивов
концептов остаётся строгой. classifier_fallback и llm_salvage — разные
problem-коды; первый означает резервную эвристику таблиц, второй — возможную
реальную потерю хвоста концептов.
```

Не отмечать инцидент закрытым до runtime-приёмки Task 5.

---

### Task 5: Развернуть исправленный backend в Stage 8 и переиндексировать документ 3409

**Files / Runtime targets:**
- Execute: `tests/scripts/stage8/start-stage8-test.ps1`
- Execute: `tests/scripts/stage8/check-stage8-test.ps1`
- Execute: `tests/scripts/stage8/run-stage8-test.ps1`
- Execute: `backend/scripts/check_integrity.py`
- Inspect: `%TEMP%\opencode\python-err.log`
- Mutate only: PostgreSQL `okf_stage8_test`, Qdrant `okf_knowledge_stage8_test`, `tests/tmp/stage8-data`

**Interfaces:**
- Consumes: проверенный код Tasks 1-4; документ `d30837ec6e97406e`.
- Produces: `status=done`, 8/8 чанков, фактически восстановленные табличные концепты и согласованные Qdrant-точки в Stage 8.

- [x] **Step 1: Снять воспроизводимый before-снимок тестового документа**

Зафиксировать текущие значения:

```text
doc_id: d30837ec6e97406e
status: done
problem: llm_partial_result
concepts: 113
chunks: 8
table rows by chunk: 0=10, 1=0, 4=29, 6=0, 7=27
```

Запустить read-only integrity:

```powershell
.\tests\scripts\stage8\run-stage8-test.ps1 `
  -PythonScript backend/scripts/check_integrity.py `
  -PythonArgumentLine '--doc-id d30837ec6e97406e --json'
```

Сохранить вывод в отчёте выполнения плана. `ok` здесь доказывает только согласованность БД/Qdrant, а не полноту 113 концептов.

- [x] **Step 2: Перезапустить именно Stage 8 backend с новым кодом**

From repository root:

```powershell
.\tests\scripts\stage8\start-stage8-test.ps1
.\tests\scripts\stage8\check-stage8-test.ps1
```

Expected profile:

```text
Knowledge profile: Измерительная база Stage 8
Database: okf_stage8_test
Qdrant collection: okf_knowledge_stage8_test
Data dir: ./tests/tmp/stage8-data
```

Дополнительно создать manifest runtime-снимок:

```powershell
.\tests\scripts\stage8\run-stage8-test.ps1 `
  -PythonScript backend/test_scripts/stage8_manifest.py `
  -PythonArgumentLine '--output scripts/stage8-manifest-table-fix-before.json'
```

Проверить секцию `runtime`; если она указывает не на Stage 8, остановиться до любой регенерации.

- [x] **Step 3: Запустить полную регенерацию документа через существующий UI/API**

В авторизованном UI Stage 8 (либо через тот же backend pipeline entrypoint с блокирующим ожиданием) выполнить «Переиндексировать» для `d30837ec6e97406e`. Не переключать contour и не запускать параллельную регенерацию других документов. В этой приёмке использован штатный `Pipeline.regenerate` + `wait_for` из отдельного процесса с профилем Stage 8.

Ожидать терминальный статус через существующий polling списка. Успех транспортного POST не считать завершением; дождаться `done`, `failed` или `paused`.

- [x] **Step 4: Проверить терминальное состояние и точность problem-кода**

Через профиль из `stage8-test-profile.ps1` выполнить read-only SQL без печати пароля:

```powershell
. .\tests\scripts\stage8\stage8-test-profile.ps1
$profile = Get-Stage8TestProfile
$env:PGPASSWORD = $profile.DatabasePassword
try {
  & 'C:\postgresql17\pgsql\bin\psql.exe' `
    -h $profile.DatabaseHost -p $profile.DatabasePort `
    -U $profile.DatabaseUser -d $profile.DatabaseName `
    -c "select id,status,problem,total_chunks,processed_chunks,okf_concept_count,updated_at from documents where id='d30837ec6e97406e'"
} finally {
  Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
```

Acceptance:

- `status = done`;
- `total_chunks = processed_chunks = 8`;
- предпочтительно `problem IS NULL`;
- если произошёл настоящий fallback классификатора, допустим только точный `llm_classifier_fallback`, но это не закрывает цель полной табличной индексации;
- `llm_partial_result` допустим только при подтверждённом `LLM_SALVAGE` в новом логе и означает, что приёмка не пройдена.

- [x] **Step 5: Проверить восстановление табличных концептов по clean-reference**

Чанки основной и тестовой копий имеют одинаковые SHA-256. Чистая основная индексация задаёт минимумы, ниже которых Stage 8 принимать нельзя:

```text
chunk 0: table-row >= 8,  table-overview >= 1
chunk 1: table-row >= 14, table-overview >= 1
chunk 4: table-row >= 34, table-overview >= 2
chunk 6: table-row >= 62, table-overview >= 1
chunk 7: table-row >= 22, table-overview >= 1
```

SQL:

```sql
select
  chunk_index,
  count(*) filter (where tags::jsonb ? 'table-row') as table_rows,
  count(*) filter (where tags::jsonb ? 'table-overview') as table_overviews,
  count(*) as total
from okf_concepts
where doc_id = 'd30837ec6e97406e'
group by chunk_index
order by chunk_index;
```

Не использовать один общий `okf_concept_count` как единственный критерий: недетерминированная LLM может менять обычные концепты, тогда как строки программно извлечённых таблиц должны сохраняться.

- [x] **Step 6: Сверить PostgreSQL и Qdrant**

```powershell
.\tests\scripts\stage8\run-stage8-test.ps1 `
  -PythonScript backend/scripts/check_integrity.py `
  -PythonArgumentLine '--doc-id d30837ec6e97406e --json'
```

Expected:

- `issues = []`;
- `qdrant_unavailable = false`;
- число concept-точек равно строкам `okf_concepts`;
- число chunk-точек равно 8 строкам `document_chunks`.

- [x] **Step 7: Проверить лог именно последнего прогона**

Перед регенерацией записать размер/время лога, после неё анализировать только новый хвост. Проверить doc-id:

```powershell
$log = Join-Path $env:TEMP 'opencode\python-err.log'
rg -n "d30837ec6e97406e|llm_partial_result|llm_classifier_fallback|спасаю частичный результат|fallback на XML" $log
```

Acceptance: нет `LLM_SALVAGE`; нет fallback для chunk 1/4/6, вызванного дополнительной закрытой JSON-структурой. Обычная строка о том, что лишний хвост одиночного объекта отброшен, допустима и не должна создавать degradation-event.

- [x] **Step 8: Снять after-manifest и зафиксировать результат**

```powershell
.\tests\scripts\stage8\run-stage8-test.ps1 `
  -PythonScript backend/test_scripts/stage8_manifest.py `
  -PythonArgumentLine '--output scripts/stage8-manifest-table-fix-after.json'
```

В отчёте указать:

- итоговый problem-код;
- число концептов по каждому чанку;
- table-row/table-overview по каждому чанку;
- Qdrant concept/chunk counts;
- наличие/отсутствие salvage и classifier fallback;
- before/after manifest paths.

- [x] **Step 9: Обработать неуспешную runtime-приёмку без маскировки**

Если после исправления остаётся `llm_classifier_fallback`, сохранить точный новый ответ/ошибку классификатора и вернуться к фазе диагностики; не очищать problem вручную и не повторять регенерацию бесконечно. Если остаётся `llm_partial_result`, исследовать только настоящий salvage основной генерации как отдельный дефект. После трёх независимых неуспешных прогонов остановиться и обсудить модель/архитектуру вместо четвёртого случайного retry.

---

### Task 6: Зафиксировать глобальную применимость и границы выкладки

**Files:**
- Update status: `docs/superpowers/plans/2026-09-13-stage8-table-classifier-indexing-fix.md`
- Verify: `AGENTS.md`

**Interfaces:**
- Consumes: успешную Stage 8-приёмку.
- Produces: понятный статус исправления для обоих контуров без мутации основной базы.

- [x] **Step 1: Подтвердить отсутствие contour-specific кода**

```powershell
rg -n "okf_stage8_test|okf_knowledge_stage8_test" backend/app
```

Expected: нет совпадений. Следовательно, тот же код начнёт работать в основном контуре при следующем запуске `scripts/start-all.ps1`.

- [x] **Step 2: Не переиндексировать основную базу автоматически**

Основной документ `d183200d2a814983` уже имеет `problem = NULL`, 8 чанков и 191 концепт. Не запускать его регенерацию без отдельного запроса пользователя. Выкладка кода в основной contour и регенерация данных — разные действия.

- [x] **Step 3: Обновить итоговые чекбоксы и статус плана**

В конец документа добавить фактический итог:

```markdown
## Execution Status

- Code tests: PASS/FAIL + command
- Stage 8 profile: verified/not verified
- Document d30837ec6e97406e: status/problem/concepts
- PostgreSQL vs Qdrant integrity: PASS/FAIL
- Table coverage: PASS/FAIL per chunk
- Main contour data touched: no/yes with explanation
- Open follow-ups: none or exact unchecked tasks
```

Не писать «план выполнен», пока Task 5 не подтвердил одновременно problem-семантику, табличное покрытие и Qdrant integrity.

## Execution Status

- Code tests: целевой набор `130 passed, 2 warnings`; свежий полный backend-suite `1201 passed, 6 warnings` (exit code 0).
- Stage 8 profile: verified (`Измерительная база Stage 8`, `okf_stage8_test`, `okf_knowledge_stage8_test`, `tests/tmp/stage8-data`).
- Document `d30837ec6e97406e`: before `done/problem=llm_partial_result/113 concepts`; after `done/problem=NULL/200 concepts`, `8/8` chunks processed.
- PostgreSQL vs Qdrant integrity: PASS (`issues=[]`, `qdrant_unavailable=false`); exact document counts are `200` concept points and `8` chunk points.
- Table coverage: PASS against clean-reference minima: chunk 0 `10/1`, chunk 1 `14/1`, chunk 4 `35/2`, chunk 6 `72/1`, chunk 7 `27/2` (`table-row/table-overview`).
- Last regeneration output showed accepted duplicate classifier JSON tails for affected chunks; final `problem=NULL` confirms no salvage or classifier-fallback degradation was persisted. Historical entries in `%TEMP%\opencode\python-err.log` were not used as evidence for the final run because that file predates the direct runner.
- Manifests: `tests/artifacts/stage8/stage8-manifest-table-fix-before.json` and `tests/artifacts/stage8/stage8-manifest-table-fix-after.json`; both record the Stage 8 runtime.
- Main contour data touched: no. Main document `d183200d2a814983` remains `done`, `problem=NULL`, `8/8`, `191` concepts.
- Open follow-ups: none for the requested Stage 8 indexing fix. Changes remain uncommitted in the shared `main` worktree by user instruction.
