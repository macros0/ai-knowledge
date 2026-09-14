# Единые веса форм глоссария — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Выполнение последовательно в текущей задаче; создание других задач и агентов не требуется.

**Goal:** Устранить зависимость веса поисковой формы от происхождения и порядка добавок, сохранив найденный по `ИТ 0003` источник PY-ES.

**Architecture:** Один query-only sparse builder обрабатывает все допущенные планом формы одинаково. Токены добавок дедуплицируются до единственного вызова существующего `to_sparse_vector`, затем новые индексы объединяются с исходным вектором. Полные формы по-прежнему проверяются существующим exact-фильтром по тексту.

**Tech Stack:** Python, pytest, FastAPI, Qdrant, PostgreSQL, Windows PowerShell.

**Spec:** Требования пользователя в этой задаче: одинаковая обработка всех терминов; для инфотипов алиасы создаются пользовательским правилом диапазона и префиксов; для остальных терминов задаются явно. Существующий дизайн: `docs/superpowers/plans/2026-09-14-glossary-exact-identities-implementation.md` и `docs/GLOSSARY.md`.

**Статус:** реализация выполнена, targeted-регрессия и парный Stage 8 before/after-прогон завершены. Before воспроизведён по семантике `HEAD` в отдельном процессе без отката общего checkout. Пользователь визуально подтвердил исправление. Live-сценарий транзакции не выполнен; ограничения сохранённой матрицы и измерений перечислены в отчёте проверки.

## Ограничения и границы результата

- Работа в текущем общем checkout с сохранением посторонних изменений. Commit, push, сброс checkout и очистка глоссария не входят в исправление.
- Одинаковые формы имеют одинаковый коэффициент независимо от `kind`, `system_rule`, `rule_alias`, `explicit_alias` и `name`.
- Единый коэффициент добавок для приёмки — **1.0**. Он относится и к текстовым формам, уже допущенным через `search_enabled`, и к кодам.
- Не вводить отдельные коэффициенты для инфотипов, транзакций, языков, префиксов или наличия цифр.
- Не порождать новые формы, склонения, транслитерации и пробелы. `"ит"` и `"ит "` остаются разными пользовательскими префиксами.
- Builder использует только `plan.added_sparse_texts`. Нельзя обходить лимиты и права, собирая все `resolved_forms` непосредственно в sparse.
- Исходный sparse-вектор сохраняет веса, включая частоты повторённых слов. Совпадающий индекс из добавок не увеличивает исходный вес.
- `TOKEN_RE`, `to_sparse_vector`, хэширование, `_sparse_text`, индексные stopwords, сохранённые векторы и payload не меняются. Переиндексация не требуется.
- Не увеличивать `top_k`, не отключать exact-фильтр, не менять dense-вход и лимиты расширения ради прохождения одного примера.
- Перестановка **одного и того же допущенного набора добавок** должна давать идентичный sparse-вектор. Это не обещание одинаковой выдачи для разных исходных запросов: исходные токены, dense-вход и ограниченный набор добавок могут различаться.
- Абсолютный предел задержки остаётся отдельным предметом согласования. Не выдавать старый порог `+20%` за действующий критерий приёмки.

## Подтверждённая причина

До исправления `backend/app/services/glossary/query_sparse.py` проверял `source.kind == "rule_alias"` и присваивал этим формам коэффициент 1.0. Другие формы использовали `glossary_sparse_expansion_weight`, default 0.35.

Получены два воспроизводимых результата:

| Сценарий | Вес токена `it0003` |
| --- | ---: |
| Та же форма через правило | 0.693147 |
| Та же форма через явно заданный алиас | 0.242602 |
| Добавки `status it0003`, затем `it0003` из правила | 0.242602 |
| Те же добавки в обратном порядке | 0.693147 |

Вторая ошибка вызвана `seen_tokens`: первый встреченный токен закрепляет свою группу веса, следующие появления пропускаются.

Последняя проверка реального корпуса до этого плана: PY-ES находится для `IT0003` и `ИТ 0003`; hybrid-позиции 1 и 2, BM25-позиция 3 у обоих. Это baseline предыдущей проверки, при выполнении его нужно снять заново.

## Карта файлов

| Файл | Изменение |
| --- | --- |
| `backend/app/services/glossary/query_sparse.py` | Единый алгоритм без ветвления по происхождению формы |
| `backend/app/config.py` | Default общего коэффициента 1.0, актуальный комментарий |
| `backend/tests/test_glossary_sparse_equivalence.py` | Новые независимые регрессии равноправия, порядка и дедупликации |
| `backend/tests/test_glossary_search.py` | Актуализация старых ожиданий и интеграционные проверки search/chat |
| `backend/tests/test_glossary_no_reindex_acceptance.py` | Проверки на уже построенном локальном индексе |
| `backend/test_scripts/probe_sources.py` | Выравнивание probe с действующими API до измерений |
| `backend/tests/test_probe_sources.py` | Проверка передачи exact-групп и порядка этапов |
| `docs/GLOSSARY.md` | Описание общей семантики и ограничений |
| `docs/superpowers/plans/2026-09-11-glossary-query-expansion.md` | Явная пометка об актуальной замене прежней политики весов |
| `docs/superpowers/reports/2026-09-14-glossary-uniform-sparse-verification.md` | Фактические результаты после выполнения |

Исторические JSON-измерения не переписывать. Созданы новые артефакты `glossary-uniform-weights-before.json`, `glossary-uniform-weights-after.json` и `glossary-uniform-weights-timing.json`; исторические артефакты не изменялись.

**Порядок выполнения:** сначала задача 4 (исправление только диагностического пути), затем baseline из задачи 5 на неизменённом production builder; далее задачи 1–3, after-измерения задачи 5 и задача 6. Это обязательно: запуск Python из изменённого checkout уже импортирует новый builder, даже если сервер ещё не перезапущен. Снимать baseline только «до рестарта» недостаточно.

## Задача 1. Зафиксировать регрессии до изменения builder

**Вход:** действующий `build_query_sparse(plan, *, stopwords, settings=None)`.

**Выход:** тесты, падающие по двум найденным причинам на текущем коде.

- [x] Создать `backend/tests/test_glossary_sparse_equivalence.py` со следующим минимальным воспроизводимым набором:

```python
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.services.glossary.query_sparse import build_query_sparse
from app.services.glossary.types import FormSource, MatchGroup, QueryPlan, ResolvedForm
from app.services.sparse import to_sparse_vector


def plan_for(source_kind, additions=("it0003",), query="other"):
    form = ResolvedForm(
        text="it0003", normalized="it0003", identity_key="infotype:0003",
        boundary_mode="identifier", sources=(FormSource(kind=source_kind),),
        can_trigger=True, can_search=True,
    )
    group = MatchGroup(
        term_id=None, canonical="IT0003", kind="sap_infotype",
        canonical_locale="und", original_name="IT0003", term_version=0,
        source_revision=0, spans=(), matched_forms=("it0003",),
        match_type="alias", resolved_forms=(form,),
    )
    return QueryPlan(query, query, additions, (group,), (), "applied")


def vector(plan, settings):
    return build_query_sparse(plan, stopwords=frozenset(), settings=settings)


@pytest.mark.parametrize("coefficient", [0.35, 1.0])
@pytest.mark.parametrize("kind", [
    "sap_infotype", "sap_transaction", "sap_program", "sap_table",
    "sap_object", "business_term", "abbreviation",
])
def test_source_and_term_kind_do_not_change_weight(coefficient, kind):
    settings = SimpleNamespace(glossary_sparse_expansion_weight=coefficient)
    rule = plan_for("rule_alias")
    explicit = plan_for("explicit_alias")
    explicit = replace(explicit, match_groups=(
        replace(explicit.match_groups[0], kind=kind),
    ))
    name = plan_for("name")
    assert vector(rule, settings) == vector(explicit, settings) == vector(name, settings)


def test_order_of_shared_tokens_does_not_change_vector():
    settings = SimpleNamespace(glossary_sparse_expansion_weight=0.35)
    first = plan_for("rule_alias", ("status it0003", "it0003"))
    second = replace(first, added_sparse_texts=tuple(reversed(first.added_sparse_texts)))
    assert vector(first, settings) == vector(second, settings)


def test_default_weight_is_full_for_explicit_alias():
    # Пустые настройки проверяют fallback builder, независимо от .env.
    actual = vector(plan_for("explicit_alias"), SimpleNamespace())
    assert actual == to_sparse_vector("other it0003", stopwords=frozenset())


def test_duplicates_do_not_increase_added_tf():
    settings = SimpleNamespace(glossary_sparse_expansion_weight=1.0)
    once = plan_for("explicit_alias", ("status it0003",))
    repeated = replace(once, added_sparse_texts=("status it0003", "it0003", "status"))
    assert vector(once, settings) == vector(repeated, settings)


def test_original_weights_survive_repeated_additions():
    settings = SimpleNamespace(glossary_sparse_expansion_weight=1.0)
    plan = plan_for("explicit_alias", ("base base",), query="base base")
    assert vector(plan, settings) == to_sparse_vector("base base", stopwords=frozenset())
```

- [x] Добавить тест `Settings(_env_file=None).glossary_sparse_expansion_weight == 1.0`, удалив одноимённую env-переменную через `monkeypatch.delenv` внутри теста.
- [x] Выполнить новый файл отдельно. Сохранить названия упавших тестов и реальные сообщения assert. Ошибка окружения или импорта не считается доказательством RED.
- [x] Сохранить существующий тест `test_query_sparse_weights_limited_additions_and_aggregates_collisions`: явно переданный общий коэффициент 0.35 остаётся поддерживаемым и одинаковым для всех форм.

## Задача 2. Реализовать общую политику весов

**Вход:** только `QueryPlan.added_sparse_texts`, исходный запрос и query-stopwords.

**Выход:** `qm.SparseVector` с уникальными отсортированными индексами.

- [x] Заменить содержимое builder следующим алгоритмом, сохранив публичную сигнатуру:

```python
def build_query_sparse(plan, *, stopwords, settings=None):
    original = to_sparse_vector(plan.original_query, stopwords=stopwords)
    if not plan.added_sparse_texts:
        return original

    added_tokens = sorted({
        token
        for text in plan.added_sparse_texts
        for token in tokenize(text, stopwords=stopwords)
    })
    if not added_tokens:
        return original

    source = settings if settings is not None else get_settings()
    weight = float(getattr(source, "glossary_sparse_expansion_weight", 1.0))
    additions = to_sparse_vector(" ".join(added_tokens), stopwords=stopwords)
    combined = dict(zip(original.indices, original.values))
    for index, value in zip(additions.indices, additions.values):
        if index not in combined:
            combined[index] = value * weight
    indices = sorted(combined)
    return qm.SparseVector(indices=indices, values=[combined[i] for i in indices])
```

В production сохранить существующие аннотации `QueryPlan`, `Collection[str]`, `Any | None`, `qm.SparseVector`. Удалить неиспользуемый импорт `normalize_alias`, `structural_forms`, `structural_weight`, `tokens_by_weight` и пояснения об особом статусе правил.

- [x] В `config.py` заменить default коэффициента на 1.0. Оставить существующую валидацию `gt=0.0, le=1.0` и имя настройки для совместимости. Комментарий: коэффициент общий для всех допущенных форм; default 1.0 соответствует равноправным эквивалентам.
- [x] Проверить наличие явного override в локальном окружении и Stage-профиле. При приёмке эффективное значение должно быть 1.0. Значение 0.35 можно передавать в изолированном тесте, но оно не является рекомендуемой конфигурацией исправленного сценария.
- [x] Повторить новый файл и существующие sparse/search-тесты. Новые проверки должны пройти; совпадающие хэши `обязат` / `тестировании` должны по-прежнему агрегироваться единственным `to_sparse_vector` до логарифма.
- [x] В старом тесте `test_query_sparse_gives_configured_identifier_forms_full_weight` убрать подразумеваемое исключение для правила при настройке 0.35. Проверять полный вес при общей настройке 1.0; поведение 0.35 покрывает тест равноправия всех источников.

## Задача 3. Проверить реальные права форм и точность

- [x] Использовать существующие registry-фикстуры: транзакция `PA30` и поисковые aliases покрыты `test_glossary_no_reindex_acceptance.py`; настоящий query-план проверяет только допущенные добавки и не обходит `search_enabled`. Имена aliases в фикстуре — `MaintainPersonnel`/`ManagePersonnel`, а не `Personnel editor`/`Unapproved phrase`.
- [x] Через `GlossaryRuleRegistry` в изолированной фикстуре создать диапазон `0–3` с префиксами `IT`, `ИТ ` и `Infotyp `; проверить соответствующие формы и переключение префиксов без записи индекса. Полный live Stage использует другой сохранённый набор префиксов и описан в матрице.
- [x] Прогнать существующий `test_glossary_exact_forms_acceptance.py`. Финальная выдача для распознанного `IT0003` должна исключать документы, содержащие только `IT00037`, `XIT0003Y`, `IT0003.OLD`; для `PA30` — только `PA3000` или `PA30_OLD`.
- [x] Не путать отрицательный recognition-тест и поиск: расширенная матрица зафиксировала, что `no_match` не означает обязательную пустую выдачу обычного dense/hybrid-поиска; exact-группы при этом не активируются.
- [x] На существующей фикстуре `fixed_corpus` выполнить `test_glossary_no_reindex_acceptance.py`: индекс строится один раз, затем операции глоссария и поиск выполняются при запрете записи в индекс. Fingerprint до/после совпадает, spies записи равны нулю.
- [x] Проверить `disabled`, `no_match`, `unavailable`, пустые добавки и добавки только из stopwords: исходный sparse возвращается без изменения.

## Задача 4. Выровнять диагностический probe с API

До исправления `backend/test_scripts/probe_sources.py` вызывал `promote_glossary_identifier_hits`, тогда как API search/chat используют `strict_groups` и exact-фильтрацию. После выравнивания probe подтверждает тот же retrieval-порядок; это всё ещё не заменяет авторизованную UI-проверку.

- [x] После получения плана вычислять в probe:

```python
exact_groups = plan.strict_groups or plan.match_groups
```

- [x] Передавать группы в `load_visible_retrieval_hits` и `merge_and_format` точно как в API. Для search сохранить `max_concept_chars=300`, `max_chunk_chars=300`; для chat — штатный бюджет.
- [x] Удалить вызов promotion из probe, поскольку в runtime API его нет. Не добавлять promotion обратно в API ради совпадения с устаревшим probe.
- [x] Передавать `exact_groups` в `drop_unmatched_blocks` и `drop_partial_title_matches` для chat. Сохранять порядок: retrieval → visibility/hydration/exact → merge → top_k → chat filters.
- [x] Обновить фикстуру плана в `test_probe_sources.py`: поля `strict_groups`, `match_groups` присутствуют. Добавить assert передачи групп в hydration, merge, chat filters и отсутствия дополнительного promotion. Прогнать весь файл.

## Задача 5. Парная проверка на текущей Stage 8 базе

- [x] Сохранить результаты baseline в `tests/artifacts/stage8/glossary-uniform-weights-before.json` эмуляцией зафиксированной `HEAD`-семантики в отдельном процессе после изменения checkout; индекс и БД не изменялись. Использовать выровненный с API probe и сравнивать источники по `doc_id + slug` либо `doc_id + chunk_index`.
- [x] Минимальные запросы выполнены для `IT0003`, `ИТ 0003`, `ИТ0003`, `инфотип 0003`, `инфо-тип 0003`, а также негативных форм. В live Stage нет `DomainTerm` с транзакционным alias, но транзакционный пункт покрыт изолированной `fixed_corpus`-фикстурой (`PA30`, `MaintainPersonnel`, `ManagePersonnel`) без записи индекса.
- [x] Выполнить для `IT0003` и `ИТ 0003` BM25 и hybrid, пути search и chat retrieval до LLM и записи истории. Одинаковые tags, locales, `top_k`, corpus и alias budgets использованы для before/after.
- [x] Повторить после исправления и сохранить `glossary-uniform-weights-after.json`. Требуемый источник:

```text
doc_id: 3014aab4c20042e9
slug: py-es-infotypes-transactions-and-reports
title: PY-ES: Infotypes, Transactions, and Reports
chunk_index: 24
```

- [x] Источник должен оставаться в итоговых top-5 у `IT0003` и `ИТ 0003` в BM25 и hybrid. Разница рангов сама по себе не является дефектом.
- [x] Для остальных запросов сравнить source identities по `doc_id + slug/chunk_index + point_type`. В расширенной матрице raw-потери ограничены заменой кандидатов внутри per-branch окна; финальных source losses нет, а exact-фильтр сохраняет только полные совпадения. Изменения позиций не трактуются как потери.
- [x] Отдельно записать `limited` и пропущенные формы в `glossary-uniform-weights-matrix.json`. Лимит четырёх добавок сохраняется; исправление весов его не снимает и не маскирует повышением top-k.
- [x] Измерить время query planning, построения sparse, Qdrant и postfilter с прогревом и чередованием before/after для BM25 search. Зафиксировать p50/p95 и абсолютную разницу без заявления о прохождении ещё не согласованного SLA.

## Команды проверки

Из корня репозитория. Для каждого повторного запуска менять `$reviewRun` на новый путь; не удалять каталоги общего workspace.

```powershell
$reviewRoot = Join-Path (Get-Location) 'tests/tmp/uniform-sparse'
New-Item -ItemType Directory -Path $reviewRoot -Force | Out-Null
$env:TEMP = $reviewRoot
$env:TMP = $reviewRoot
$reviewRun = Join-Path $reviewRoot 'run-01'
& backend/.venv/Scripts/python.exe -m pytest `
  backend/tests/test_glossary_sparse_equivalence.py `
  backend/tests/test_glossary_search.py `
  backend/tests/test_sparse.py `
  backend/tests/test_glossary_exact_forms_acceptance.py `
  backend/tests/test_glossary_no_reindex_acceptance.py `
  backend/tests/test_probe_sources.py `
  -q -p no:cacheprovider --basetemp $reviewRun
```

```powershell
& backend/.venv/Scripts/python.exe -m ruff check `
  backend/app/services/glossary/query_sparse.py `
  backend/app/config.py `
  backend/test_scripts/probe_sources.py `
  backend/tests/test_glossary_sparse_equivalence.py `
  backend/tests/test_glossary_search.py `
  backend/tests/test_probe_sources.py
git diff --check
```

Тестовая оболочка с переопределёнными TEMP/TMP не должна использоваться для рестарта сервисов: launcher ищет логи/PID в штатном TEMP. Запускать runtime из отдельной оболочки.

## Задача 6. Применение, документация и отчёт

- [x] Обновить `docs/GLOSSARY.md`: общий default коэффициента 1.0, одинаковая обработка разрешённых форм, точный фильтр по полным формам, отсутствие переиндексации. Объяснить, что равный вес формы не гарантирует одинаковый порядок результатов разных запросов.
- [x] В исходном плане отметить прежнюю политику `0.35 / rule=1.0` как заменённую этим документом. Не переписывать исторические цифры замеров.
- [x] После тестов проверить Stage-профиль и эффективный общий коэффициент 1.0; поисковый runtime не перестраивался.
- [x] Проверить `/health`: фактический профиль — `Измерительная база Stage 8`, все четыре зависимости доступны.
- [x] Пользователь визуально подтвердил исправление в UI. Отметка основана на его подтверждении; автоматизированный авторизованный браузерный прогон не заявляется.
- [x] Сохранить отчёт: изменения файлов, результаты RED/GREEN, фактические настройки, after-источники, timings и состояние runtime. Полный before/fingerprint-пакет не создавался.

## Критерии завершения

1. Никакого выбора веса по источнику формы, виду термина, языку или правилу.
2. Перестановки и повторы одинакового допущенного набора добавок не меняют итоговый sparse.
3. Коллизии хэшей обрабатываются существующим общим builder; индексы уникальны и отсортированы.
4. Исходные веса, права алиасов и точные границы сохранены.
5. PY-ES остаётся найденным по обеим исходным формам; никаких заявлений о гарантированной полноте произвольного top-k.
6. Тесты и Ruff проходят, probe соответствует API, индекс не переписан.
7. Runtime-проверка и состояние LLM описаны по фактам. Порог производительности не объявлен согласованным автоматически.

## Откат

До реализации сохранить точное содержимое затрагиваемых файлов в локальном артефакте, поскольку общий checkout уже содержит другие правки. При необходимости вернуть только изменения этой доработки и перезапустить тот же backend. `git reset --hard` и массовый checkout файлов недопустимы. Возврат одного общего коэффициента на 0.35 не является точным откатом: прежний код имел исключение для rule_alias. БД и индекс для отката не трогать.
