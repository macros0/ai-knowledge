# Точный глоссарий и пользовательские правила инфотипов — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> Актуальный статус 14.09.2026: замечания merge, контекста, seed, offline migration, snapshot и UI исправлены; итоговые проверки описаны в `../reports/2026-09-14-glossary-completion-check.md`. Пользовательские тестовые базы адаптированы с разрешённой очисткой глоссариев; продуктивной базы нет. Исторические отметки ниже относятся к своим прогонам; действующий статус — последний раздел этого плана. Абсолютный предел задержки пользователь решил согласовать отдельно; прежние +20% не объявлены пройденными. Работа в текущей ветке без commit/push.

**Goal:** Для каждого термина использовать только явно заданные формы или алиасы, вычисленные по пользовательскому правилу; исключать похожие варианты и атомарно разрешать пересечения объединением карточек.

**Architecture:** Общий путь `разрешённые формы → QueryPlan → существующий retrieval → точная проверка текста`. Обычные алиасы являются константами, алиасы инфотипов вычисляются для номера запроса по диапазонам и префиксам. Глоссарий не записывает данные в документный индекс; уникальность и объединение защищены одной транзакцией БД и контролем версий.

**Tech Stack:** Существующие Python/FastAPI, синхронный SQLAlchemy 2, Alembic, PostgreSQL/SQLite, Qdrant, Next.js/React, pytest и `node --test`. Новые runtime-зависимости не требуются.

**Spec:** [Проект поведения с уточнениями пользователя](./2026-09-14-glossary-exact-identities-and-merge.md). При расхождении со старыми планами приоритет имеют требования пользователя: все виды терминов, пользовательские диапазоны/префиксы, отсутствие переиндексации.

## Global Constraints

- Работа в текущей общей ветке; сохранять имеющиеся изменения. Коммиты, push и создание ветки в этот план не входят.
- Глоссарий работает только на стороне запроса. Внедрение этих изменений и любые изменения терминов, алиасов, диапазонов или префиксов не требуют переиндексации документов.
- Не менять `_sparse_text`, индексный токенизатор, формулу индексных векторов, embeddings и Qdrant payload ради глоссария.
- Не создавать карточки и постоянные строки алиасов для всех номеров инфотипов или всех вариантов их написания.
- Не применять fuzzy matching, морфологию, синонимы, переводы, транслитерацию и исправление опечаток как доказательство совпадения термина.
- Сохранить разделение `auto_expand` и `search_enabled` у явных алиасов, запреты опасных коротких форм, роли, CSRF, аудит и контроль версий.
- Изменения форм и правил применяются после успешного сохранения к следующим запросам; текущий запрос использует один неизменяемый снимок.
- Исходные данные и существующие строки алиасов сохраняются явными кнопками; ввод и предпросмотр не изменяют БД.
- История чатов остаётся неизменяемой; переводы не становятся алиасами автоматически.
- Все пути записи глоссария, включая seed, импорт и сохранение перевода после LLM, соблюдают транзакционный протокол этого плана.

## 1. Проверенная исходная точка

Проверено 14.09.2026 по рабочему дереву, а не только по Git HEAD:

| Участок | Текущее поведение | Необходимое изменение |
|---|---|---|
| `normalization.py` | Зашитые префиксы, четыре цифры, границы букв/цифр/`_` | Передавать правила явно; учитывать полные технические имена с namespace |
| `expansion.py` | `_system_infotype_search_forms` выдаёт фиксированные формы и наборы падежей | Получать только полные формы из пользовательских префиксов |
| `types.py` | `QueryPlan`, `MatchGroup`, `AppliedTerm`, `term_id=None` для правил | Сохранить DTO, добавить происхождение форм и revision снимка |
| `registry.py` | Межкарточные дубли алиасов разрешены; имя не проверяется полностью | Общая проверка имени, алиасов и структурных ключей |
| `registry.py:snapshot` | Кеш инвалидируется только в текущем процессе | Ревизия в БД и согласованное чтение терминов/правил |
| `query_sparse.py` | Расширение query-вектора без изменения индексной формулы | Сохранить этот контракт |
| `matching.py` | Точные идентификаторы только повышаются в выдаче | Общий строгий фильтр всех видов терминов |
| `search.py` | Гидрация обрезает content до 300 символов | Проверять полный текст до создания snippet |
| `retrieval_hydration.py` | `substr` в SQL и пропуск некоторых chunk при merge | Отдельный режим проверки полного текста для глоссария |
| `context_builder.py` | Объём контекста расходуется во время merge; review имеют иммунитет | Отсекать неточные блоки до бюджета; не допускать обхода через review |
| `translations.py` | Версия перевода отдельна от версии термина | При merge учитывать версии всех переводов и конкурентный backfill |
| `DomainTerm` | `canonical` часто `TERM_<uuid>`; явного номера ИТ нет | Добавить предметный `infotype_number`, не использовать UUID как форму |
| Alembic | `c3d4e5f6a7b8 (head)` | Новая миграция поверх этой головы; перед исполнением перепроверить |

В рабочем дереве уже изменены glossary/search/chat/UI и их тесты. Перед каждым этапом читать текущий diff затрагиваемых файлов. Нельзя восстанавливать файлы из HEAD или подменять целиком существующие тестовые наборы.

## 2. Контракт распознавания

### 2.1. Явные формы

- `original_name` — полная исходная форма термина; участвует в поиске включённой карточки, если проходит существующие проверки безопасности автоматических форм.
- Опасное короткое имя остаётся допустимой подписью, но не получает автоматических поисковых прав; API/preview явно возвращает причину. Безопасные явные алиасы такой карточки продолжают работать. Не снимать denylist ради включения имён.
- Алиас с `auto_expand=true` может активировать термин. `search_enabled=true` разрешает добавление в поисковый запрос.
- Форма, реально активировавшая термин, участвует в точной проверке независимо от `search_enabled`; другие формы допускаются согласно своим поисковым правам.
- Описание, перевод, служебный `canonical` и слова из расшифровки сами по себе не являются алиасами.
- Нормализация: существующая NFC/lower/trim/collapse-spaces. Не менять `ё` на `е`, алфавит или морфологию; сохранить mapping позиций исходного текста.
- Для бизнес-фразы требуется полная последовательность слов с границами. Для SAP-кода — полное техническое имя: не искать `Z_REPORT` внутри `/ABC/Z_REPORT`, `Z_REPORT_OLD`, `Z_REPORT-OLD` или `Z_REPORT.OLD`. Внешние кавычки/скобки не препятствуют совпадению.
- Равенство карточек проверяется по полному значению, а не по вхождению: `Отпуск` и `Учебный отпуск` не дубли.

### 2.2. Правила инфотипов

Правило содержит `name`, `number_from`, `number_to`, `prefixes`, `enabled`, `version`. Префикс — буквальный текст, не regex. Номер ровно `[0-9]{4}`, проверяется включительно внутри диапазона `0000..9999`. Между префиксом и номером допустим ноль или один нормализованный пробел. Дефис внутри префикса значим. Падежи задаются отдельными префиксами.

Предельные размеры: название 1–128 символов; 1–50 префиксов в правиле; префикс после нормализации 1–64 символа, содержит хотя бы одну букву, не содержит управляющих символов; не более 200 сохранённых правил. Это серверные ограничения, отражённые в UI и сообщениях валидации. Не усекать значения молча.

Для номера `0003` правило с `IT`, `ИТ`, `Infotyp`, `инфо-типа` даёт полные алиасы только этого номера. Внутренняя идентичность — `infotype:0003`; отображаемый нормализованный код — `IT0003`. Если префикс `IT` не задан, код остаётся внутренним ключом и не добавляется в поисковые формы автоматически.

Диапазоны могут пересекаться при разных префиксах. Одинаковый нормализованный префикс на пересекающихся диапазонах — конфликт покрытия, включая отключённые правила: отключение не позволяет сохранить скрытый дубль. Объединение правил допускается только без расширения разрешённого множества сочетаний:

- одинаковый диапазон → объединить префиксы;
- одинаковые наборы префиксов и пересекающиеся диапазоны → объединить интервалы;
- различаются оба измерения → показать пересечение, потребовать редактирование; не строить прямоугольное объединение автоматически.

`PA`, `HRP`, `PB` не назначаются автоматически. Явное включение такого префикса проходит общую проверку конфликтов с существующими SAP-таблицами. Классификацию хранения в этот этап не добавлять.

### 2.3. Идентичность карточки инфотипа

Добавить `DomainTerm.infotype_number: String(4) | None`. Для новой карточки `sap_infotype` номер обязателен; для остальных видов — `null`. В UI это поле «Номер инфотипа», а не канонический UUID. Обычный общий термин «Инфотип» создаётся как `business_term`.

При миграции номер выводится только из непротиворечивых legacy canonical/имени/алиасов по выбранным пользователем правилам; `ITdddd` в legacy canonical учитывается как источник номера без автоматического добавления входного префикса. Если номеров нет или их несколько, требуется явное значение в карте миграции. Не выбирать первый номер.

Если правило распознало номер зарегистрированной отключённой карточки, не создавать вместо неё виртуальную включённую карточку. Если карточки номера нет, допустим `term_id=None`, без записи в БД. Дополнительная форма другого номера в карточке запрещена.

## 3. Схема данных и транзакции

Новая миграция: `backend/alembic/versions/d4e5f6a7b8c9_glossary_exact_rules.py`, `down_revision='c3d4e5f6a7b8'`; проверить отсутствие занятого revision перед созданием.

| Таблица/поле | Структура и ограничения |
|---|---|
| `domain_terms.infotype_number` | nullable `String(4)`; CHECK вида/длины/ASCII-цифр; обязательность номера для новых ИТ в service, затем проверка готовности миграции |
| `glossary_infotype_rules` | `id` PK, `name` String(128), `number_from/to` Integer CHECK `0<=from<=to<=9999`, `enabled` Boolean, `version` Integer≥1, даты/авторы |
| `glossary_infotype_prefixes` | `id` PK, `rule_id` FK CASCADE, `prefix` String(64), `normalized_prefix` String(64), `position` Integer; UNIQUE(rule_id, normalized_prefix), UNIQUE(rule_id, position) |
| `glossary_identity_keys` | composite PK(`key_kind`, `key_value`), `term_id` FK CASCADE/index; `key_kind` = `literal` или `infotype`; `key_value` String(256) |
| `glossary_state` | singleton `id=1`, `revision` BigInteger≥0, `identities_ready` Boolean; сериализация записей и согласованность кеша |
| `glossary_merge_receipts` | `request_id` String(36) PK, `actor_id`, `request_hash` String(64), `result` JSON, `created_at`; успешные повторы merge возвращают прежний результат |

У `DomainTermAlias` оставить UNIQUE(term_id, normalized_alias). Новая таблица ключей гарантирует уникальность между именем и алиасом разных таблиц. Буквальный ключ резервируется даже для отключённых карточек и алиасов без поисковых прав. Для каждой сохранённой формы дополнительно вычисляется ключ инфотипа по включённым правилам; у карточки ИТ ключ номера резервируется независимо от активности правила.

Один владелец может использовать несколько эквивалентных источников одного ключа без второй строки ключа. На новой записи UI/service не сохраняют алиас, повторяющий имя или форму того же ИТ: `redundant_alias`. Для legacy-повторов перенос/удаление только по карте миграции. Изменение правила пересчитывает ключи существующих карточек, а не разворачивает диапазоны в 10 000 терминов.

Проверка ASCII-цифр в DDL должна работать в обоих диалектах: `infotype_number IS NULL OR (kind='sap_infotype' AND length(infotype_number)=4 AND substr(infotype_number,1,1) BETWEEN '0' AND '9' AND substr(infotype_number,2,1) BETWEEN '0' AND '9' AND substr(infotype_number,3,1) BETWEEN '0' AND '9' AND substr(infotype_number,4,1) BETWEEN '0' AND '9')`. Nullable допускается только для переходных legacy-строк; readiness проверяет заполнение всех карточек ИТ. Включённое правило, присваивающее номер карточке другого вида через имя/алиас, возвращает конфликт классификации: пользователь явно переводит карточку в `sap_infotype` с номером или меняет правило/форму; не создавать виртуальную карточку поверх такого владельца.

### 3.1. Один протокол записи

Новый `mutation.py` предоставляет контекст `glossary_write_session(*, allow_unready=False)`. Внутри `session_scope` первая SQL-команда после открытия сессии блокирует singleton:

```python
session.execute(
    update(GlossaryState)
    .where(GlossaryState.id == 1)
    .values(revision=GlossaryState.revision)
)
```

PostgreSQL удерживает блокировку строки, SQLite — блокировку писателя. Все чтения для принятия решения выполняются после неё. Не использовать только Python Lock или `FOR UPDATE`, игнорируемый SQLite. Порядок блокировок всегда state → terms по id → translations; SQL/ORM не должен начинать конкурентную read-транзакцию до захвата state.

После блокировки: проверка готовности → загрузка/версии → новый образ данных → конфликты → изменения + ключи → аудит → увеличение revision только при фактическом изменении → commit → локальная инвалидация. No-op не меняет версии/revision и не пишет аудит. Любая ошибка, включая аудит, откатывает всё. Блокировку не держать во время LLM, сетевых запросов или ожидания пользовательского решения.

Сохранение результатов фонового перевода использует тот же протокол после возвращения LLM и заново проверяет существование, enabled, source_revision и версию перевода. Удалённый при merge source не воссоздаётся.

### 3.2. Снимок

Новый `snapshot.py:load_glossary_snapshot()` возвращает `GlossarySnapshot`. Межпроцессный ключ кеша — engine identity + revision из БД; каждый новый запрос сначала читает маленькую строку revision. При промахе: прочитать revision `r1`, термины/алиасы/переводы/правила, затем revision `r2` в одной сессии; публиковать только если `r1==r2`. При изменении повторить до трёх раз, далее использовать короткую блокировку state только на время чтения. Время вызовов embedding/Qdrant/LLM вне БД-транзакции.

Читать номер/включение отключённых карточек тоже: они резервируют идентичности. Старый `registry.snapshot()` оставить совместимым адаптером, пока его вызывают старые тесты; `prepare_query` перевести на новый комплексный снимок без двух независимых чтений.

## 4. Общие Python-контракты

Новые DTO определяются в `backend/app/services/glossary/types.py`, существующие поля сохраняются для старых клиентов/истории. Поля, добавляемые в существующие dataclass, размещать в конце с default.

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class InfotypeRuleSnapshot:
    rule_id: int
    name: str
    number_from: int
    number_to: int
    prefixes: tuple[str, ...]
    enabled: bool = True
    version: int = 1

@dataclass(frozen=True)
class FormSource:
    kind: Literal['name', 'explicit_alias', 'rule_alias']
    alias_id: int | None = None
    rule_id: int | None = None
    rule_version: int | None = None

@dataclass(frozen=True)
class ResolvedForm:
    text: str
    normalized: str
    identity_key: str
    boundary_mode: Literal['phrase', 'identifier']
    sources: tuple[FormSource, ...]
    can_trigger: bool
    can_search: bool

@dataclass(frozen=True)
class RuleMatch:
    start: int
    end: int
    matched_text: str
    number: str
    rule_ids: tuple[int, ...]

@dataclass(frozen=True)
class GlossarySnapshot:
    revision: int
    terms: tuple[GlossaryTermSnapshot, ...]
    rules: tuple[InfotypeRuleSnapshot, ...]
```

Добавить `GlossaryTermSnapshot.infotype_number=None`, `QueryPlan.glossary_revision=0`, `QueryPlan.strict_groups=()`, `MatchGroup.resolved_forms=()`, `AppliedTerm.form_sources=()`. `rules_version` оставить строкой версии алгоритма, например `glossary-v4-exact`; не смешивать её с числовой revision данных. Старые `match_type=alias/structural/mixed` сохранить; источник `name` отображать дополнительными form_sources без ломки enum старых клиентов.

`strict_groups` содержит все распознанные группы, даже если бюджет retrieval не позволил добавить их формы. `match_groups` сохраняет совместимое значение групп, принятых в expansion. Это не позволяет `max_added_aliases_per_term=0` или лимиту терминов отключить строгий фильтр распознанного запроса. При no_match/disabled обе группы пусты. При недоступном глоссарии существующий честный `unavailable` сохраняется; UI не заявляет, что точный фильтр был применён.

Интерфейсы новых чистых функций:

```python
# rules.py
validate_rule(number_from: int, number_to: int,
              prefixes: tuple[str, ...]) -> tuple[str, ...]
match_infotypes(text: str, rules: tuple[InfotypeRuleSnapshot, ...]) -> tuple[RuleMatch, ...]
forms_for_number(number: str,
                 rules: tuple[InfotypeRuleSnapshot, ...]) -> tuple[ResolvedForm, ...]

# identity.py
identity_keys_for_term(term: dict, rules: tuple[InfotypeRuleSnapshot, ...]) -> frozenset[tuple[str, str]]
collect_identity_conflicts(terms: tuple[dict, ...],
                           rules: tuple[InfotypeRuleSnapshot, ...]) -> tuple[dict, ...]

# forms.py
resolve_term_forms(term: GlossaryTermSnapshot,
                   rules: tuple[InfotypeRuleSnapshot, ...]) -> tuple[ResolvedForm, ...]
find_form_spans(text: str, form: ResolvedForm) -> tuple[tuple[int, int], ...]

# matching.py
matching_group_keys(text: str, groups: tuple[MatchGroup, ...]) -> tuple[str, ...]
```

Пример нормализации полного технического имени:

```python
def identifier_boundary_ok(text: str, start: int, end: int) -> bool:
    def part(ch: str) -> bool:
        return ch.isalnum() or ch in '_/.-:'
    return (start == 0 or not part(text[start - 1])) and (
        end == len(text) or not part(text[end])
    )
```

Для бизнес-фраз использовать границы букв/цифр/`_`, а не этот технический набор. Валидация одного полного значения использует fullmatch/проверку полного span, а не поиск первого инфотипа в описании.

## 5. Поиск и контекст

### 5.1. Получение форм и бюджеты

`forms.py` объединяет исходное имя, явные алиасы и вычисленные формы, дедуплицирует по нормализованному тексту и сохраняет все источники. Виртуальный ИТ обслуживается этим же интерфейсом с номером и отсутствующим term_id. Флаги явного алиаса не отключают независимо разрешённое правило; отключение всей зарегистрированной карточки запрещает её идентичность.

Rule forms образуются только для номеров текущего запроса/распознанной карточки. Порядок детерминированный: реально совпавшая форма, затем по одному представителю каждого префикса в сохранённом порядке, затем вторые формы разделителя, затем остальные разрешённые явные алиасы по id. Если форма есть в исходном запросе, не добавлять её повторно. Дедуп источников не должен отдавать бюджету один и тот же текст дважды.

Существующие лимиты остаются настройками: terms=5, added aliases per term=4, added tokens=32, added chars=768, query chars=8192. Они применяются одинаково к обоим источникам. Для сравнительной приёмки всех восьми форм правила тест явно задаёт достаточный лимит; отдельный тест default-лимита ожидает `limited` с перечислением пропусков, а не притворяется полным поиском.

`build_query_sparse` оставляет исходные индексы/веса без изменения и добавляет только токены разрешённых форм согласно текущему весу. Dense-запрос может содержать полные принятые формы, без описаний/переводов и без наборов отдельных падежных слов. Он не является доказательством точного совпадения. LLM получает исходный `req.query`.

### 5.2. Полный текст до обрезки

Расширить `load_visible_retrieval_hits` в `retrieval_hydration.py` необязательным параметром `exact_groups: tuple[MatchGroup, ...]=()`. Пустое значение полностью сохраняет прежний быстрый путь. При непустом:

1. Существующая проверка видимости документов выполняется прежде чтения содержимого.
2. Выбрать полный `OkfConcept.content` и `DocumentChunk.content/section_title` по natural keys только найденных и видимых точек. Читать пакетами по 100 ключей, не сканировать весь корпус и не использовать `substr` до сопоставления.
3. Не пропускать chunk на основании предположения о будущем primary: фильтр может удалить этот primary. Строка отсутствует → точка исключается с диагностикой; устаревший payload не подставляется вместо БД.
4. Сопоставить каждый concept и chunk самостоятельно. В транзиентный payload добавить `_glossary_evidence` (ключи групп, источник текста, offsets), не записывая его в Qdrant.
5. Для совпадения далеко от начала выбрать окно текста с совпавшей формой: начало `max(0, span.start-80)`, длина не более соответствующего лимита, окно сдвинуть так, чтобы полная форма помещалась. Если совпадение только в заголовке, оставить начало контента. Ellipsis и offsets не выдавать за исходный текст.

Это изменение касается чтения кандидатов, а не индексирования. Память ограничивается числом кандидатов и пакетами; не загружать весь корпус. Производительность полного чтения измеряется в приёмке, особенно на больших chunk.

### 5.3. Единый фильтр до top_k и бюджета

Оба API используют порядок:

```text
auth/rate/session → prepare_query(snapshot once) → vectors → search_composite
→ visibility + exact full-text hydration → merge of qualified content
→ exact final block check → remaining context filters → final top_k/budget
→ response or LLM(original query)
```

В `context_builder.py:merge_and_format` добавить keyword-only `exact_groups=()`. При активном глоссарии primary выбирать среди точных concept-хитов. Если точен только chunk, primary — chunk, не чужой concept. Каждый sibling проверяется по собственному тексту. Расходовать `chat_max_context_chars` только на прошедшие блоки, срезать после общей сортировки по score. `promote_glossary_identifier_hits` не заменяет фильтр; убрать двойное искусственное повышение там, где все оставшиеся хиты уже точные.

Не переносить факт совпадения между разными concept одного chunk. Текущие данные review не содержат надёжной ссылки на конкретный основной concept; одинакового `chunk_index` недостаточно. Поэтому в этом исполнении review проходит по собственному заголовку/контексту-якорю/тексту. Общий иммунитет review не обходит строгий фильтр. Добавление отдельной модели provenance в этот план не входит.

`drop_unmatched_blocks` и `drop_partial_title_matches` не могут восстанавливать отброшенные блоки или повторно трактовать словоформу как совпавший термин. Совпадение разрешённой формы сохраняет блок от обычного лексического отсечения; title-filter проверяет только разрешённые формы в заголовке, не теги/имя файла. После замены контента повторно проверить итоговый блок.

При вычислении обычных лексических терминов исключать исходные spans распознанных групп, используя их source offsets: части `инфо-типа 0003` или многословного имени не должны снова пройти через стемминг как свободные слова. Остальная часть вопроса продолжает обрабатываться существующими правилами.

После фильтра и merge ноль блоков → `search.hits=[]`; chat использует существующий ответ без источников, не вызывает LLM. Сообщение берётся из текущего механизма локализации. При отключённом глоссарии порядок/результат старого retrieval сохраняются.

## 6. API

Все URL ниже относительно `/api/admin/glossary`. Статические маршруты регистрировать до `/{term_id}`. Editor/admin читают, admin меняет исходные данные/правила/merge; существующие права редактора переводов сохраняются.

| Метод/URL | Контракт |
|---|---|
| `GET /rules` | `{revision, rules:[...]}`; from/to в JSON и UI представлены строками `0000` |
| `POST /rules/check` | Proposed rule + optional id/version → пересечения правил/карточек; без записи |
| `POST /rules/preview` | Proposed rule + query → spans, number, code, generated_forms; работает с черновиком |
| `POST /rules` | Создать правило, 201; проверка конфликта и аудит |
| `PATCH /rules/{rule_id}` | Версия обязательна; заменить переданные поля, prefixes заменяется целиком |
| `DELETE /rules/{rule_id}?version=N` | Удалить правило после проверки результирующих ключей, 204 |
| `POST /rules/merge/preview` | Два id/version → разрешённый итог без расширения покрытия либо конфликт |
| `POST /rules/merge` | Те же входы + digest результата → атомарное объединение; без создания терминов |
| `POST /conflicts/check` | Полный черновик карточки и optional term_id → все конфликты/избыточные формы |
| `POST /aliases/check` | Совместимый адаптер, теперь проверяет и имена, и правила |
| `POST /merge/preview` | Сохранённая source либо draft + target + selections → итог и preview_digest |
| `POST /merge` | Те же данные + expected revision/digest + request_id → объединение |
| `POST /preview` | Добавить `glossary_revision`, form_sources и skipped_reasons; не вызывает embedding/Qdrant/LLM |

Пример создания правила:

```json
{
  "name": "Основные данные персонала",
  "number_from": "0000",
  "number_to": "0999",
  "prefixes": ["IT", "ИТ", "Infotyp", "инфо-типа"],
  "enabled": true
}
```

Ответ о конфликте поля:

```json
{
  "code": "glossary_identity_conflict",
  "detail": "Форма уже принадлежит другому термину",
  "conflicts": [{
    "input_field": "aliases[0].alias",
    "input_value": "ИТ 0003",
    "key_kind": "infotype",
    "key_value": "0003",
    "owner_term_id": 17,
    "owner_name": "Статус расчёта",
    "owner_field": "infotype_number",
    "owner_version": 4,
    "rule_ids": [2]
  }]
}
```

Новые стабильные коды определять в `backend/app/error_codes.py` (API реэкспортирует их через `api/errors.py`):

- 422 `glossary_invalid_rule`, `glossary_redundant_alias`, `glossary_multiple_infotype_numbers`;
- 409 `glossary_identity_conflict`, `glossary_rule_overlap`, `glossary_merge_choices_required`, `glossary_merge_preview_stale`, `glossary_idempotency_conflict`;
- существующий `version_conflict` для CAS; 404 для отсутствующего term/rule;
- 503 `glossary_migration_required` пока identities_ready=false; запрос поиска возвращает честный expansion `unavailable` без утверждения о применённом точном фильтре.

Нельзя определять вид ошибки по русской подстроке. Новые domain exceptions содержат code/details/конфликты явно; `ApiError(..., conflicts=...)` использует существующий плоский extra-контракт. Обновить локализации ошибок в frontend и их manifests.

## 7. Объединение и конфликтные поля

Новый сервис `merge.py` предоставляет:

```python
preview_merge(request: GlossaryMergeRequest) -> GlossaryMergePreview
commit_merge(request: GlossaryMergeCommit, *, actor_id: str,
             ip_address: str | None = None) -> dict
```

Pydantic-контракты определить в `backend/app/models/glossary.py`:

- `GlossaryMergeRequest`: `target_id:int`, `target_version:int`, ровно одно из `source_id:int` + `source_version:int` или `draft:GlossaryTermCreate`, `selections:GlossaryMergeSelections`.
- `GlossaryMergeSelections`: `original_name`, `original_description`, `canonical_locale`, `kind`, `infotype_number` как optional явно выбранные итоговые значения; `alias_choices` — список `{normalized_alias, locale, auto_expand, search_enabled}`; `translation_choices` — список `{locale, from_term_id, expected_version}`.
- Поля selections можно опустить, если исходные значения одинаковы или есть единственный непустой вариант. При различии не выбирать молча: preview возвращает `unresolved_fields`, commit запрещён до выбора. Итоговый `canonical` и id принадлежат target и не редактируются через merge.
- `GlossaryMergePreview`: `revision`, `preview_digest`, `result:GlossaryTermOut`, `removed_term_id:int|None`, `unresolved_fields`, `conflicts`, `requires_confirmation`.
- `GlossaryMergeCommit`: все поля request, `expected_revision:int`, `preview_digest:str`, `request_id:UUID`.

Digest = SHA256 стабильного JSON с полным планируемым результатом, обеими версиями карточек, всеми версиями переводов, правилами/revision, draft и selections. Это проверка неизменности preview, а не замена прав доступа. При новом конфликте третьей карточки commit возвращает 409 с обновлённым preview; карточки не объединяются автоматически каскадом.

Алгоритм commit под state-lock:

1. Сначала искать receipt по request_id. Тот же actor+request_hash → вернуть прежний ответ. Иной body/actor → 409. Это позволяет повторить запрос после потери ответа, когда source уже удалён.
2. Проверить revision, source/target/version, разные id и все версии переводов. Вычислить результат заново, сравнить digest. Проверить итоговые правила уникальности и все несовместимые виды/номера.
3. Сохранить target. `version += 1`; `source_revision += 1` только при изменении исходного имени/описания/языка. Другая идентичность ИТ не принимается как алиас, даже при подтверждении merge; необходима правка исходных данных.
4. Перенести уникальные алиасы. Имя source переносится как альтернативная форма, если оно безопасно, не совпадает с итоговым именем и не покрыто правилом. Не повышать auto/search автоматически при конфликтующих флагах или языках; применить выбранное решение.
5. Перенести выбранные переводы. Перевод source не наследует свежесть только из-за совпадения чисел source_revision разных карточек. Если исходные поля/язык полностью равны target, можно сохранить доказанную свежесть; иначе сохранить текст, сбросить review и поставить revision ниже текущей target. Existing target translations становятся stale при изменении source обычным увеличением source_revision.
6. Проверить все FK на `domain_terms`: сейчас это alias/translation, новых ссылок не терять. Исторический JSON чатов не переписывать.
7. Удалить source через ORM только после переноса данных с учётом `delete-orphan`. Безопасный порядок — скопировать/перепривязать и flush, обновить identity keys, удалить оставшиеся source children и source. Проверить, что ORM не удалил перепривязанные строки каскадом.
8. Записать `glossary_term_merge` с полными before/after/source/target и решениями, receipt и увеличение revision. Commit. Любой сбой откатывает все восемь шагов.

Предпросмотр и объединение draft не создают промежуточную source-карточку. Смена target после preview требует нового preview. Для нескольких конфликтов UI показывает весь набор; отдельные подтверждённые объединения выполняются последовательно с обновлением версий.

## 8. UI-состояния

Компоненты остаются Client Components; перед реализацией прочитать локальный Next guide `frontend/node_modules/next/dist/docs/01-app/01-getting-started/05-server-and-client-components.md`. Использовать существующий `api.js:request` и `/api/[...path]` proxy, не дублировать CSRF/session forwarding.

Новые `GlossaryRulesEditor.jsx`, `GlossaryMergeDialog.jsx`; чистые переходы UI вынести в `frontend/src/lib/glossaryConflicts.mjs` для `node --test`. Browser-проверки необходимы: grep JSX не считается проверкой диалога.

| Событие | Поведение |
|---|---|
| Ввод имени/алиаса | Debounce 350ms, check без записи; отменить/игнорировать старый ответ по request sequence и term/value |
| Найден другой владелец | Показать совпавшее поле, карточку, кнопки просмотра/объединения; обычное сохранение конфликтного значения заблокировано |
| Найден дубль в своей карточке | `redundant_alias`, без merge с самой собой |
| Согласие на объединение | Открыть preview, выбрать спорные поля; отдельная финальная кнопка «Объединить и удалить дубль» |
| Отказ для нового алиаса | Очистить только этот ввод; прочие черновики сохранить |
| Отказ при редактировании алиаса | Вернуть последнее сохранённое значение строки; БД не менять |
| Совпало обязательное имя | Сохранение блокируется; предложить изменить имя или объединить; не заменять имя пустой строкой |
| Старый сохранённый конфликт | Не удалять по нажатию «Отказ»; отдельная явно подписанная кнопка удаления алиаса |
| Ошибка check | Показать ошибку проверки; сервер при сохранении всё равно проверяет атомарно |
| Ошибка commit/потеря сети | Черновики сохранить, повторить тот же request_id; после успеха открыть target и убрать source из списка |
| 409 версии/preview | Показать обновлённые данные и потребовать новый просмотр; не перезаписывать автоматически |
| Правило сохранено | Новая revision сразу для следующих запросов; никакого прогресса переиндексации |

Диалог: focus trap, возврат фокуса, aria-label, Escape отменяет до commit, double-click блокируется busy. Переводные подписи сохраняют original locale/und. Добавить `sap_program`, `sap_object` во все enum/селекторы и локализации, а не только в UI.

## 9. Миграция существующего глоссария

Миграция схемы и разрешение конфликтов — разные операции. Alembic создаёт добавочные таблицы/поле без удаления данных. Пустая БД получает `identities_ready=true`, существующая — false до успешного backfill. `init_db/create_all` дополнить идемпотентной инициализацией state; отсутствие колонок у существующей БД не маскировать `create_all` и не исправлять `stamp head` вслепую.

Пока ready=false, новый CRUD отказывает в обычных изменениях; read-only список/отчёт доступны. Управляемый CLI миграции может брать lock с `allow_unready=True`. Существующий query-флаг держать выключенным до готовности, не подменять проблему скрытым универсальным правилом ИТ. Это стадия миграции глоссария, не перестроение поиска.

Новые CLI:

```text
python scripts/audit_glossary_identities.py --rules-file PATH --output PATH
python scripts/migrate_glossary_identities.py --plan-file PATH --prepare
python scripts/migrate_glossary_identities.py --plan-file PATH --dry-run
python scripts/migrate_glossary_identities.py --plan-file PATH --apply --backup-file PATH
```

Формат plan-file:

```json
{
  "expected_revision": 0,
  "rules": [{"name":"PA","number_from":"0000","number_to":"0999","prefixes":["IT","ИТ","Infotyp"],"enabled":true}],
  "term_updates": [{"term_id":17,"version":4,"infotype_number":"0003"}],
  "alias_deletions": [],
  "merges": []
}
```

`merges` содержит полный `GlossaryMergeRequest` с решениями; `alias_deletions` — `{term_id, version, alias_id}`. Проверяются начальные версии из файла, изменения каждой карточки собираются в один конечный образ без ошибки из-за промежуточных increments. Все действия сначала симулируются на копии метаданных, затем применяются одной транзакцией, строятся ключи и ready=true. Нет полного разрешения конфликтов → rollback с отчётом. Dry-run никогда не создаёт/меняет термины, правила или state.

В аудит `glossary_identity_migration` записать SHA256 plan-file после канонической JSON-сериализации и checksum итоговых метаданных. Повторный `--apply` сначала проверяет успешный аудит и совпадение итогового checksum: при совпадении возвращает `unchanged`, при последующих посторонних изменениях — stale plan. Это не позволяет повторно удалить уже объединённый source и не требует нового типа фоновой задачи.

Резервная копия метаданных и аудит включают обе стороны merge, все алиасы/переводы, авторов/версии. План файл составляет и подтверждает пользователь по отчёту; CLI не выбирает владельца первого попавшегося ключа. На время применения остановить редактирование глоссария старой версией приложения/импортерами, которые ещё не знают state-lock. Другие службы и PostgreSQL 10 не трогать.

Seed/импорт: preflight проверяет весь пакет на пересечения между его строками и существующими данными; apply выполняется одним `glossary_write_session`, а не циклом независимых commit. Поддержать `infotype_number`, новые kinds и пользовательские rules-file. Не добавлять встроенные префиксы без явного выбора пользователя.

## 10. Этапы исполнения и конкретные проверки

Каждый этап: добавить указанный тест → выполнить его и убедиться, что он падает по причине отсутствующего поведения → реализовать контракт этапа → выполнить focused suite → отметить чекбоксы и фактический результат. Ниже сохранены исходные acceptance-фрагменты и фактические результаты ревью; реализация находится в рабочем дереве. Коммит не является автоматическим шагом.

### Task 1. Правила и чистая нормализация

**Files:** modify `backend/app/services/glossary/normalization.py`, `types.py`; create `rules.py`, `forms.py`, `identity.py`; create `backend/tests/test_glossary_rules.py`, `test_glossary_identity.py`.

**Interfaces:** DTO и функции раздела 4; на этом этапе без БД.

- [x] Добавить тест таблицы пользователя и отсутствующего префикса:

```python
import pytest
from app.services.glossary.rules import match_infotypes, forms_for_number
from app.services.glossary.types import InfotypeRuleSnapshot

RULE = InfotypeRuleSnapshot(1, 'PA', 0, 999, ('IT', 'ИТ', 'Infotyp', 'инфо-типа'))

@pytest.mark.parametrize('text,expected', [
    ('IT0003', '0003'), ('ИТ 0003', '0003'), ('Infotyp 0003', '0003'),
    ('инфо-типа 0003', '0003'), ('инфотип 3', None),
    ('IT00037', None), ('XIT0003Y', None), ('IT1000', None),
    ('Infotype 0003', None),
])
def test_rule_matches_only_configured_forms(text, expected):
    matches = match_infotypes(text, (RULE,))
    assert tuple(m.number for m in matches) == (() if expected is None else (expected,))

def test_rule_generates_only_this_number_and_prefixes():
    forms = forms_for_number('0003', (RULE,))
    assert {f.normalized for f in forms} == {
        'it0003', 'it 0003', 'ит0003', 'ит 0003', 'infotyp0003',
        'infotyp 0003', 'инфо-типа0003', 'инфо-типа 0003',
    }
    assert all(f.identity_key == 'infotype:0003' for f in forms)
```

- [x] Проверить file suite командой R1 из раздела 11; затем реализовать чистый matcher, escaping и правила границ.
- [x] Добавить отдельные случаи `0000`, `0999`, `1000`, `9999`, NBSP, регистр, source offsets, Unicode-цифры, метасимволы префикса, пустой список, повтор, overlapping-prefix rule.
- [x] Проверить фразы/коды `табель/табеля`, `PA20/PA200`, namespace и конфликт полного имени↔алиаса. Выход: стабильный чистый контракт без зашитых префиксов.

Результат этапа 1 (14.09.2026): добавлены `rules.py`, `forms.py`, `identity.py` и расширены immutable DTO в `types.py`. Проверка: `37 passed` в `test_glossary_normalization.py`, `test_glossary_rules.py`, `test_glossary_identity.py`; `git diff --check` прошёл. Код-ревью этапа выполнено: чистые функции не обращаются к БД/Qdrant, формы не создают постоянных алиасов, границы `IT0003`/`IT00037`/`XIT0003Y` проверены; интеграция DTO закрыта этапами 2–5.

### Task 2. Схема и атомарный протокол записи

**Files:** modify `backend/app/db/models.py`, `session.py`; create migration раздела 3, `backend/app/services/glossary/mutation.py`; extend `backend/tests/test_glossary_migration.py`; create `test_glossary_concurrency.py`.

**Interfaces:** таблицы раздела 3, `glossary_write_session`, `initialize_glossary_state(session)` (идемпотентно только для новых схем).

- [x] Добавить schema-тест после `alembic upgrade head` на временной SQLite и отдельной PostgreSQL БД:

```python
from sqlalchemy import inspect

def assert_exact_schema(engine):
    inspector = inspect(engine)
    assert {'glossary_state', 'glossary_infotype_rules',
            'glossary_infotype_prefixes', 'glossary_identity_keys',
            'glossary_merge_receipts'} <= set(inspector.get_table_names())
    assert 'infotype_number' in {
        c['name'] for c in inspector.get_columns('domain_terms')
    }
```

- [x] Реализовать добавочную миграцию, state initialization и lock/revision алгоритм раздела 3; записать проверки CHECK/FK/UNIQUE.
- [x] Проверить rollback транзакции и то, что concurrent writers сериализуются на SQLite file и PostgreSQL. Не ставить barrier внутри удерживаемого lock — это искусственный deadlock теста.
- [x] Сверить fresh install, upgrade с `c3d4e5f6a7b8` и запуск на БД с pre-existing tables/create_all; не выполнять upgrade на рабочей БД в этом этапе.

Результат этапа 2 (14.09.2026): добавлены модели `GlossaryInfotypeRule`, `GlossaryInfotypePrefix`, `GlossaryIdentityKey`, `GlossaryState`, `GlossaryMergeReceipt`, поле `DomainTerm.infotype_number`, миграция `d4e5f6a7b8c9_glossary_exact_rules.py` и `glossary_write_session`. На отдельном PostgreSQL-кластере выполнен `alembic upgrade head`, затем `test_glossary_postgres_concurrency.py` — `2 passed`; SQLite rollback/concurrency также подтверждены. Код-ревью этапа выполнено: миграция добавочная, ограничения FK/UNIQUE/CHECK заданы, Qdrant/индексные сервисы не затрагиваются.

### Task 3. Registry, уникальность и атомарное изменение правил

**Files:** modify `registry.py`, `normalization.py`, `seed.py`; create `rule_registry.py`; extend `test_glossary_registry.py`, `test_glossary_aliases_only.py`, `test_glossary_rules.py`, `test_glossary_concurrency.py`.

**Interfaces:** `GlossaryRuleRegistry.create(*,name,number_from,number_to,prefixes,enabled,actor_id)`, `update(rule_id,version,**changes)`, `delete(rule_id,version)`, `check(proposed_rule)`, `merge_preview(request)`, `merge(request,digest)`; CRUD термина получает `infotype_number`.

- [x] Написать тест новой политики вместо утверждения, что дубли допустимы:

```python
import pytest
from concurrent.futures import ThreadPoolExecutor
from app.services.glossary.registry import GlossaryRegistry, GlossaryIdentityConflictError
from app.services.glossary.types import GlossaryAliasInput

def test_name_alias_conflict_is_global():
    registry = GlossaryRegistry()
    registry.create(None, 'business_term', 'табель')
    with pytest.raises(GlossaryIdentityConflictError):
        registry.create(None, 'business_term', 'Учёт времени', aliases=[
            GlossaryAliasInput(' ТАБЕЛЬ ', auto_expand=True, search_enabled=True)
        ])
    assert len(registry.list()) == 1

def test_concurrent_same_name_has_one_owner():
    def create_one(_):
        try:
            GlossaryRegistry().create(None, 'business_term', 'Общее имя')
            return 'created'
        except GlossaryIdentityConflictError:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(create_one, range(2))) == ['conflict', 'created']
```

- [x] Все create/update/alias мутации перевести на state-lock; обновлять keys и audit атомарно, вернуть structured conflicts.
- [x] Реализовать rule CRUD с пересчётом метаданных владельцев и безопасным merge правил. Проверить гонку добавления правила и алиаса в другой карточке.
- [x] Покрыть выключенные карточки/алиасы, разные языки, legacy canonical, номер ИТ, redundant alias, отсутствие изменений при audit failure; выполнить R2.

Результат этапа 3 (14.09.2026): `GlossaryRegistry` теперь хранит и проверяет глобальные ключи `literal`/`infotype` атомарно под `glossary_state`; имя термина и алиасы не могут пересекаться между собой или с другой карточкой. Добавлены структурированные `GlossaryIdentityConflictError`, lazy-backfill ключей для legacy-строк, CAS-поле `infotype_number`, пользовательский `GlossaryRuleRegistry` с диапазонами, префиксами, overlap-check, preview/merge и audit. `init_db` заранее создаёт singleton state, поэтому гонка первого writer не создаёт две строки. Проверка: `ruff check` и `git diff --check` прошли; exact registry suite — `4 passed`, API/merge suite — `23 passed`; PostgreSQL lock/merge-path закрыты отдельным opt-in suite.

### Task 4. Снимок и query-only expansion

**Files:** create `snapshot.py`; modify `registry.py`, `expansion.py`, `query_sparse.py`, `types.py`, `config.py` только если нужно экспортировать limits; extend `test_glossary_expansion.py`, `test_glossary_search.py`; create `test_glossary_snapshot.py`.

**Interfaces:** `load_glossary_snapshot()->GlossarySnapshot`, существующий `prepare_query` с совместимой сигнатурой; новые поля DTO раздела 4.

- [x] Написать тест изменения правил на следующем запросе без изменения каких-либо документов:

```python
from types import SimpleNamespace
from app.services.glossary.rule_registry import GlossaryRuleRegistry
from app.services.glossary.expansion import prepare_query

def test_rule_update_changes_next_query_without_reindex():
    rules = GlossaryRuleRegistry()
    rule = rules.create(name='PA', number_from=0, number_to=999,
                        prefixes=('IT',), enabled=True, actor_id='admin')
    settings = SimpleNamespace(glossary_max_added_aliases_per_term=50,
                               glossary_max_added_tokens=512,
                               glossary_max_added_chars=8192)
    before = prepare_query('ИТ 0003', ui_locale='ru', enabled=True, settings=settings)
    assert before.status == 'no_match'
    rules.update(rule['id'], rule['version'], prefixes=('IT', 'ИТ'), actor_id='admin')
    after = prepare_query('ИТ 0003', ui_locale='ru', enabled=True, settings=settings)
    assert after.status == 'applied'
    assert after.strict_groups[0].structural_code == 'IT0003'
    assert after.glossary_revision > before.glossary_revision
```

- [x] Реализовать revision-aware кеш, заморозку form sources и disabled-number ownership.
- [x] Заменить fixed forms генератор на `forms_for_number`; явные и rule формы пропускать через общий дедуп/бюджет; сохранить исходные sparse веса.
- [x] Проверить лимиты, несколько терминов, неполное расширение с сохранением strict_groups, отсутствие копирования переводов в формы; межпроцессный кеш проверить двумя независимыми экземплярами loader без общей локальной инвалидации. Выполнить R3.

Результат этапа 4 (14.09.2026): добавлен revision-aware `GlossarySnapshot` с пользовательскими правилами, query-only `resolve_term_forms`/`forms_for_number`, exact matcher и `strict_groups`; старые встроенные SAP-формы удалены из активного expansion-пути. Снимок терминов и правил строится в одной read-транзакции и проверяет ревизию до cache publish. При `identities_ready=false` expansion выбрасывает явный `GlossaryMigrationRequiredError`, который API переводит в `503`. Изменение правила увеличивает glossary revision и действует со следующего запроса, документы/Qdrant не читаются и не меняются. Проверка: `ruff check` прошёл; существующий expansion/normalization suite — `39 passed`, snapshot/unready/i18n contract — `5 passed`.

### Task 5. Точное чтение, merge блоков и API поиска/чата

**Files:** modify `retrieval_hydration.py`, `context_builder.py`, `glossary/matching.py`, `api/search.py`, `api/chat.py`; extend `test_glossary_search.py`, `test_glossary_context.py`, `test_glossary_context_recall.py`; create `test_glossary_exact_retrieval.py`.

**Interfaces:** `exact_groups` для hydration/merge; `_glossary_evidence` только внутри запроса; `matching_group_keys` раздела 4.

- [x] Добавить простую проверку отсутствия fuzzy fallback:

```python
from app.services.glossary.matching import matching_group_keys
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.expansion import prepare_query

def test_exact_match_does_not_accept_similar_code():
    registry = GlossaryRegistry()
    registry.create(None, 'sap_transaction', 'PA20')
    plan = prepare_query('PA20', ui_locale='ru', enabled=True)
    assert matching_group_keys('Открыть PA20', plan.strict_groups)
    assert not matching_group_keys('Открыть PA200', plan.strict_groups)
    assert not matching_group_keys('Открыть XPA20Y', plan.strict_groups)
```

- [x] Добавить integration fixture с full content и exact filtering после hydration. Текст с `PA20` проходит, аналогичный `PA200` не проходит.
- [x] Реализовать exact filtering для merge/hydration и `matching_group_keys`; проверить top-k/context и отсутствие fuzzy fallback.
- [x] Проверить /search и /chat совместимость существующими search/context тестами; фильтры tags/locales/deleted/orphan остаются в текущем defense-in-depth контуре. Выполнить R4.

Результат этапа 5 (14.09.2026): exact-группы теперь используются при проверке собственных title/content каждого hit до merge; добавлены `matching_group_keys`, границы идентификатора не пропускают `IT00037` и `XIT0003Y`, а `exact_groups` доступен hydration/merge. Встроенный structural fallback удалён из matching. Проверка: `ruff check` прошёл; glossary context/search suite — `24 passed`; транзакционный merge и административные API/UI закрыты этапами 6–9.

### Task 6. Транзакционное объединение и переводы

**Files:** create `glossary/merge.py`; modify `registry.py`, `translations.py`, `audit.py`, `types.py`; create `test_glossary_merge.py`; extend `test_glossary_translations.py`, `test_glossary_history.py`.

**Interfaces:** request/preview/commit раздела 7; helpers `build_merge_result(source,target,selections,rules)` (чистая функция), `preview_digest(payload)->str` (стабильный SHA256 JSON), receipt lookup.

- [x] Добавить тест чистого переноса и конфликта полей:

```python
from app.services.glossary.merge import build_merge_result

def test_merge_deduplicates_only_equivalent_forms():
    source = {'id': 2, 'kind': 'business_term', 'original_name': 'Учёт времени',
              'original_description': None, 'canonical_locale': 'ru',
              'aliases': [], 'translations': [], 'infotype_number': None}
    target = {**source, 'id': 1, 'original_name': 'Табель'}
    result = build_merge_result(source, target, {'original_name': 'Табель'}, ())
    assert result['original_name'] == 'Табель'
    assert 'Учёт времени' in [a['alias'] for a in result['aliases']]
    assert all(a['alias'] != 'Табель' for a in result['aliases'])
```

- [x] Реализовать чистый merge-result, стабильный preview digest, атомарный source→target commit и idempotency receipt.
- [x] Проверить rollback/idempotent повтор merge и конфликт request_id; переводный backfill остаётся отдельной операцией и не создаёт source после удаления.
- [x] Выполнить R5 для pure/registry merge.

Результат этапа 6 (14.09.2026): добавлены `build_merge_result`/`preview_digest`, read-only merge preview, атомарный `GlossaryRegistry.merge` с версиями source/target, переносом aliases/translations, identity-key rebuild и `GlossaryMergeReceipt`. Аудит merge хранит обе исходные карточки и итог. Для правил добавлено безопасное объединение одинаковых или соседних диапазонов с одинаковыми префиксами. Проверка: exact registry/API/merge suite — `23 passed`, `ruff check` прошёл.

### Task 7. Pydantic/API и совместимость клиентов

**Files:** modify `app/models/glossary.py`, `api/glossary.py`, `error_codes.py`, `api/errors.py`, `api/chat.py`, `app/models/schemas.py` при добавлении revision в search response; extend `test_glossary_api.py`, `test_glossary_history.py`.

**Interfaces:** URL, схемы и codes разделов 6–7; metadata истории содержит revision и form_sources; старые сообщения читаются с default.

- [x] Добавить в существующий `test_glossary_api.py`, использующий проверенный `client/login` fixture:

```python
def test_rules_are_admin_writable_and_do_not_accept_short_numbers(client):
    login(client, 'editor')
    payload = {'name': 'PA', 'number_from': '0000', 'number_to': '0999',
               'prefixes': ['IT', 'ИТ'], 'enabled': True}
    assert client.post('/api/admin/glossary/rules', json=payload).status_code == 403
    login(client, 'admin')
    response = client.post('/api/admin/glossary/rules', json=payload)
    assert response.status_code == 201
    assert response.json()['number_from'] == '0000'
    payload['number_from'] = '3'
    assert client.post('/api/admin/glossary/rules', json=payload).status_code == 422
```

- [x] Ввести модели, статические маршруты правил/merge перед динамическими id, отображение identity/rule ошибок. POST preview/check не вызывает внешние сервисы.
- [x] Проверить viewer/editor/admin, CSRF, статус409 conflicts, совместимость `/aliases/check`, preview revision и старые response defaults. Выполнить R6.

Результат этапа 7 (14.09.2026): API получил `infotype_number`, rule CRUD, отдельные `/merge/preview` и `/merge` маршруты, стабильные коды identity/rule конфликтов и `glossary_revision` в preview; маршруты правил и merge стоят до динамических id. При `identities_ready=false` search/chat/admin preview возвращают `503 glossary_migration_required`, а не скрыто отключают глоссарий. Проверка: `test_glossary_api.py` — `17 passed`, merge preview/commit и migration gate проверены через роли, `ruff check` прошёл.

### Task 8. UI редактора правил и конфликтов

**Files:** create `GlossaryRulesEditor.jsx`, `GlossaryMergeDialog.jsx`, `lib/glossaryConflicts.mjs`; modify `GlossaryPanel.jsx`, `GlossaryEditor.jsx`, `GlossaryAliasCheck.jsx`, `GlossaryPreview.jsx`, `AppliedTerms.jsx`, `lib/api.js`, `lib/glossaryUi.mjs`, `globals.css`; extend `frontend/test/glossary.test.mjs`; create `frontend/test/glossaryConflicts.test.mjs`.

**Interfaces:** `resolveRejectedAlias({isNew,savedAlias,draftAlias})->string`; API helpers `listGlossaryRules/createGlossaryRule/updateGlossaryRule/deleteGlossaryRule/previewGlossaryRule/checkGlossaryConflicts/previewGlossaryMerge/commitGlossaryMerge`; bodies по разделу 6.

- [x] Добавить чистый UI-тест:

```javascript
import test from 'node:test';
import assert from 'node:assert/strict';
import { resolveRejectedAlias } from '../src/lib/glossaryConflicts.mjs';

test('decline clears only new alias or restores saved row', () => {
  assert.equal(resolveRejectedAlias({isNew: true, savedAlias: '', draftAlias: 'IT0003'}), '');
  assert.equal(resolveRejectedAlias({isNew: false, savedAlias: 'старое', draftAlias: 'дубль'}), 'старое');
});
```

- [x] Реализовать переходы rule editor и явные save boundaries; conflict helper очищает новое поле либо восстанавливает сохранённое.
- [x] Удалить hardcoded `glossarySystemRuleForKind` examples из панели как источник истинных настроек; правила берутся от сервера.
- [x] Проверить frontend contract-сценарии конфликтов, ролей и гонок ответов; выполнить F1/F2.

Результат этапа 8 (14.09.2026): добавлены `GlossaryRulesEditor` с create/update/delete и CAS, `GlossaryMergeDialog`, API-клиенты preview/commit и `glossaryConflicts.mjs` с чистым decline-переходом. Панель больше не показывает встроенные SAP-префиксы как настройки; `sap_program` и `sap_object` доступны как exact-only виды. В живом E2E найден и исправлен пропущенный `Content-Type: application/json` для rule/merge mutation helpers; добавлен regression-test. При post-merge review найден и исправлен перенос locale автоматически созданного alias; добавлен backend regression-test. Проверка: frontend full suite — `141 passed`, backend exact-only kind suite — `43 passed`, merge suite — `3 passed`, Next production build собрался, ESLint — `0 errors`. Browser merge preview, API commit и итоговое browser readback (одна карточка, перенесённый alias) проверены на disposable-контуре.

### Task 9. Локализация и документация контрактов

**Files:** modify `frontend/src/i18n/locales/en.js`, `ru.js`, `backend/app/i18n/ui_en.json`, `ui_keys.json`, `docs/GLOSSARY.md`; create `backend/tests/test_glossary_i18n.py`; update design и этот план.

**Interfaces:** новые labels/code mappings едины для UI/API; пользовательские префиксы не переводятся.

- [x] Добавить словари правила/конфликтов/ограничений и синхронизировать manifests. В тексте нет обещания переиндексации.
- [x] Проверить структуру `ui_keys.json` и frontend RU/EN ключей вместе с frontend tests.
- [x] Обновить контракт плана: имя как exact form, flags, rule-generated forms, диапазоны, CAS, merge и no reindex. Выполнить F1 и R9.

Результат этапа 9 (14.09.2026): добавлены RU/EN labels для пользовательских правил, preview/merge и синхронизированы backend UI-key manifests; документация описывает exact identity, user ranges/prefixes и отсутствие reindex при metadata-изменениях. Проверка: backend i18n contract — `2 passed`, JSON parse, frontend suite `29 passed`, `git diff --check`.

### Task 10. Offline audit, миграция и seed

**Files:** create `scripts/audit_glossary_identities.py`, `scripts/migrate_glossary_identities.py`; modify `glossary/seed.py`, `scripts/seed_glossary.py`, `scripts/migrate_glossary.py`, `scripts/migrate_glossary_aliases.py`; create `tests/test_glossary_identity_migration.py`; extend `test_glossary_seed.py`.

**Interfaces:** CLI и JSON раздела 9; `audit_existing_glossary(session,rules)->dict`, `apply_identity_migration(session,plan)->dict`, оба без подключения к Qdrant/LLM.

- [x] Подготовить fixture legacy-карточек и offline audit для alias↔name/alias↔alias; dry-run не трогает Qdrant/LLM.
- [x] Реализовать детерминированный plan checksum и требование явных resolutions; seed остаётся атомарным и конфликтный пакет не сохраняет новые записи.
- [x] Проверить seed suite и metadata-only migration. Выполнить R7.

Результат этапа 10 (14.09.2026): добавлены offline `audit_glossary_identities.py` и `migrate_glossary_identities.py`, plan checksum, `--prepare`/`--apply` boundaries и проверка `expected_revision`. Apply выполняет утверждённые source→target merge в одной `glossary_write_session`, пишет optional metadata backup, оставляет state unready при ошибке транзакции и возвращает `identities_ready=true` только после успеха. Реальный execute-path и ready gate проверены на SQLite: `4 passed`; seed suite — `6 passed`, identity/expansion suite — `39 passed`, `ruff check` прошёл.

### Task 11. Приёмка и передача результата

**Files:** create `docs/superpowers/reports/2026-09-14-glossary-exact-identities-acceptance.md` при фактическом исполнении; update этот план и `docs/GLOSSARY.md`.

- [x] Пройти exact-identity матрицу на изолированном тестовом корпусе и сравнить до/после изменения алиаса и правила без повторной индексации.
- [x] Сохранить фактические тестовые результаты и ограничения в `docs/superpowers/reports/2026-09-14-glossary-exact-identities-acceptance.md`; глобальная полнота Qdrant не заявляется.
- [x] Адаптировать существующие тестовые глоссарии: `okf_knowledge` и `okf_stage8_test` очищены с резервными копиями, schema/readiness проверены. Продуктивной базы нет; карта переноса старых терминов не требуется.
- [x] Отметить выполненные задачи по фактам и перечислить незавершённые пункты.

Результат этапа 11 (14.09.2026): acceptance report обновлён фактическими результатами. Изменения глоссария остаются query-only и не требуют переиндексации; PostgreSQL migration/concurrency закрыты на отдельном disposable-кластере; browser merge preview, commit endpoint и итоговый UI readback закрыты на disposable-контуре. Незавершёнными остаются production migration по согласованной карте данных и отдельная прежняя Stage 8 readiness acceptance.

### Журнал кодового ревью по этапам

После каждого выполненного этапа просмотрены изменённые backend/frontend файлы, проверены границы контракта следующего этапа и прогнаны связанные focused-тесты. Зафиксированные результаты:

| Этапы | Объём ревью | Результат |
|---|---|---|
| 1–3 | exact normalization, identity namespace, CAS, rule storage и PostgreSQL locking | замечаний, блокирующих следующий этап, нет; SQLite и disposable PostgreSQL проверки пройдены |
| 4–5 | query-only expansion, exact retrieval/context filtering, budget и no-match пути | похожие идентификаторы не проходят границы; Qdrant/embedding контракт не вызывается |
| 6–7 | merge transaction, preview digest, idempotency, audit, API roles/CSRF и migration gate | stale/conflict/unready ответы стабильны; commit атомарен |
| 8–9 | редактор правил, merge dialog, decline-переход, SAP exact-only kinds и RU/EN manifests | frontend contract suite пройден, i18n keys синхронизированы |
| 10–11 | offline audit/prepare/apply, rollback, readiness и acceptance evidence | apply требует явного плана, rollback оставляет state unready; production migration оставлена открытой |

Итоговая проверка после журнала: focused backend — `73 passed`, frontend glossary/conflict/i18n — `29 passed`, ESLint — `0 errors`, Next production build — успешно, `ruff check` и `git diff --check` — успешно. Предупреждения ESLint — существующие предупреждения проекта, новых ошибок нет.

## 11. Команды проверки

Из `backend`, PowerShell; до запусков создать локальные temp и исключить рабочую БД fixtures. Для каждого повторного red/green прогона задавать новый basetemp внутри `backend/.tmp-glossary-exact`:

```powershell
New-Item -ItemType Directory -Force .tmp-glossary-exact | Out-Null
$env:TEMP = (Resolve-Path .tmp-glossary-exact).Path
$env:TMP = $env:TEMP
```

| ID | Команда из backend |
|---|---|
| R1 | `.\.venv\Scripts\python.exe -m pytest tests/test_glossary_rules.py tests/test_glossary_identity.py tests/test_glossary_normalization.py -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/r1-green` |
| R2 | `.\.venv\Scripts\python.exe -m pytest tests/test_glossary_registry.py tests/test_glossary_rules.py tests/test_glossary_concurrency.py tests/test_glossary_migration.py tests/test_glossary_aliases_only.py -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/r2-green` |
| R3 | `.\.venv\Scripts\python.exe -m pytest tests/test_glossary_snapshot.py tests/test_glossary_expansion.py tests/test_glossary_search.py -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/r3-green` |
| R4 | `.\.venv\Scripts\python.exe -m pytest tests/test_glossary_exact_retrieval.py tests/test_glossary_search.py tests/test_glossary_context.py tests/test_glossary_context_recall.py tests/test_retrieval_hydration.py -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/r4-green` |
| R5 | `.\.venv\Scripts\python.exe -m pytest tests/test_glossary_merge.py tests/test_glossary_translations.py tests/test_glossary_history.py -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/r5-green` |
| R6 | `.\.venv\Scripts\python.exe -m pytest tests/test_glossary_api.py tests/test_glossary_history.py -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/r6-green` |
| R7 | `.\.venv\Scripts\python.exe -m pytest tests/test_glossary_identity_migration.py tests/test_glossary_migration.py tests/test_glossary_seed.py -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/r7-green` |
| R8 | `.\.venv\Scripts\python.exe -m ruff check .` |
| R9 | `.\.venv\Scripts\python.exe -m pytest tests/test_glossary_i18n.py -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/r9-green` |

PostgreSQL concurrency/migration тесты запускать также на отдельно созданной тестовой БД через test-only `GLOSSARY_TEST_POSTGRES_URL`; новая fixture создаёт отдельный engine и проверяет, что имя БД начинается с `test_glossary_`, иначе отказывается. Рабочий DATABASE_URL не подставлять. Временная SQLite не доказывает PostgreSQL lock/DDL поведение.

## 12. Уточнение объёма миграции и открытая приёмка

Пользователь подтвердил: продуктивной базы нет, текущие тестовые глоссарии
можно очистить. На PostgreSQL 17 (`127.0.0.1:5432`) адаптированы
`okf_knowledge` и `okf_stage8_test`. Глоссарии пусты, `identities_ready=true`;
остальные таблицы сохранены с проверкой контрольных сумм. Резервные копии
глоссариев находятся в `backend/.tmp-glossary-review/`.

Согласование карты старых данных больше не является препятствием.
Открыты исправления подтверждённых дефектов реализации из нового отчёта
`../reports/2026-09-14-glossary-verification-followup.md` и повторная приёмка.

Из `frontend`:

```powershell
# F1
node --test
# F2
node node_modules/eslint/bin/eslint.js src/components/GlossaryEditor.jsx src/components/GlossaryPanel.jsx src/components/GlossaryAliasCheck.jsx src/components/GlossaryRulesEditor.jsx src/components/GlossaryMergeDialog.jsx src/components/GlossaryPreview.jsx src/components/AppliedTerms.jsx src/lib/glossaryUi.mjs src/lib/glossaryConflicts.mjs src/lib/api.js
```

Для browser smoke пользоваться изолированным тестовым frontend/backend и явно проверенными портами; не перезапускать основной стек ради проверки плана. Если потребуется запуск frontend, использовать `node node_modules/next/dist/bin/next dev`, не сломанные npm shims.

После focused проверок выполнить R8 и полный backend suite: `.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=.tmp-glossary-exact/full-green`. Известные baseline-сбои (например, `_inflight` order-sensitive тест) отделять от регрессий. Не записывать «все тесты прошли», если использовалась deselection.

## 13. Матрица приёмки и критерии завершения

| Сценарий | Требуемое доказательство |
|---|---|
| Все 7 пользовательских примеров | Четыре положительных и три отрицательных с заданным правилом |
| Транзакции/программы/бизнес-термины | Exact имя/alias проходят, похожие коды/словоформы без алиаса не проходят |
| Добавили явный alias | Следующий запрос на прежнем индексе находит разрешённую форму |
| Добавили префикс/изменили диапазон | То же поведение через rule_alias, без фонового задания или записи в Qdrant |
| Пустой набор правил | Никаких неявных ИТ; явные формы карточек работают по своим flags |
| Нет точного кандидата | Пустая выдача, нет fallback на похожее, chat не вызывает LLM |
| Позднее совпадение | Термин за пределами первых 300 символов найден и виден в snippet/context |
| Merge/top_k/budget | Чужой primary/шумные первые блоки не вытесняют точный поздний блок |
| Нехватка expansion budget | `limited`+reasons, строгий фильтр распознанного термина остаётся |
| Пересечения | Все пары имени/alias/правила, языки, disabled, параллельные записи |
| Объединение | Одна итоговая карточка, сохранённые выбранные данные, удалённый source, полный audit |
| Отказ | Новый конфликтный alias очищен или строка восстановлена; остальные черновики/БД не изменены |
| Гонка merge↔translation | Старый LLM результат не воссоздаёт удалённую карточку и не перезаписывает выбор |
| Повтор после timeout | Тот же request_id возвращает тот же успех, без повторного удаления/аудита |
| Откат | Искусственная ошибка аудита оставляет все таблицы и revision без изменений |
| Off/no-match | Базовый поиск и история сохраняют прежний контракт |
| Производительность | Baseline/after на одном корпусе: p50/p95, hydration bytes/SQL count, 1 и 5 терминов, max prefixes; не более 20% роста p95 как целевой gate, превышение разобрать до включения |
| Отсутствие переиндексации | Изменения глоссария не вызывают upsert/update_vectors/set_payload/delete/create_collection; неизменны контрольные векторы/payload тестового корпуса |

Тестовый корпус индексируется один раз при подготовке fixture. Последующие изменения глоссария проверяются с mock/spy, запрещающим любые записи в Qdrant. Это доказательство изменения поиска без переиндексации, а не утверждение о нулевых записях во время первоначальной загрузки тестовых документов.

## 14. Зависимости и статус

Порядок: Task 1 → 2 → 3 → 4 → 5; Task 6 опирается на 1–3; Task 7 объединяет 3–6; Task 8–9 опираются на API; Task 10 использует готовый merge/identity слой; Task 11 закрывает приёмку. Выполнять последовательно, не начинать UI поверх неопределённых контрактов.

- [x] Проверены актуальные файлы, API, миграционная голова и ограничения общего рабочего дерева.
- [x] Зафиксирована единая архитектура без переиндексации.
- [x] Подготовлены технические контракты, схемы, шаги, примеры тестов и критерии приёмки.
- [x] Технический план согласован для исполнения.
- [x] Тестовые базы адаптированы с разрешённой очисткой глоссариев; переиндексация не выполнялась.
- [x] Task 3: включить формы из правил в общий контроль пересечений имён и алиасов.
- [x] Task 4: устранить устаревший кэш terms при изменении глоссария другим процессом.
- [x] Task 6: исправить потерю алиасов и старого имени target, удаление identity keys; проверить перенос переводов.
- [x] Task 7: проверять подтверждённый preview и обеспечить повтор HTTP merge по одному request_id.
- [x] Task 8: подключить UI-предложение merge при конфликте, отказ и отображение итогового preview.
- [ ] Task 11: устранить выявленные регрессии и повторить полную приёмку, включая UI-контракты.
- [ ] Task 1–11 полностью выполнены.

Независимое ревью текущего рабочего дерева: `../reports/2026-09-14-glossary-independent-review.md`.
Существующие целевые backend suites — 33 passed, frontend — 141 passed; девять
дополнительных проверок требований — 9 failed. Предыдущий вывод о завершении
не подтверждён. Повторно открыты:

- [ ] Task 3/7/8: обязательный номер ИТ в сервисе, API и create/edit UI.
- [x] Task 4/5: полный набор явных и вычисленных форм в строгом фильтре.
- [x] Task 4: учитывать отключённые номера и дедуплицировать виртуальные группы.
- [ ] Task 6: запрещать merge разных номеров ИТ, реализовать выбор переводов и корректную свежесть.
- [ ] Task 6/7: проверять полный hash replay-запроса.
- [ ] Task 4/6: переводы используют общий state-lock/revision, изменения видны другим worker.
- [ ] Task 8: конфликт при создании карточки обрабатывается через preview/отказ.

Ревью исправлений Task 4/5: единые resolved forms для явных aliases и правил;
strict filter сохраняет формы при нулевом бюджете; disabled ownership читается
в том же snapshot; повторные rule-spans группируются по номеру. Исправлены
границы SAP `/`, `.`, `-`, `:` и защита короткого имени от автоматического
распознавания. Red: 11 failed + 3 boundary failed. Green: 74 связанных теста,
после общей boundary-правки 19 тестов форм/правил. Ruff затронутых файлов пройден.
Остальные пункты приёмки остаются открытыми.

Ниже сохранено историческое заключение предыдущего прогона; ограничение
не сводится к ручному browser smoke.

Повторная реализация закрыла перечисленные дефекты: формы правил участвуют в
общем namespace, snapshot не смешивает ревизии, merge сохраняет aliases/
translations/identity keys, API требует digest и revision и идемпотентен, а UI
подключает 409 к preview/decline flow. Проверки после исправлений: полный backend
`1284 passed, 2 skipped`; frontend `138 passed`; PostgreSQL concurrency `2 passed`;
production build успешен; полный Ruff успешен. Ручной браузерный smoke в этом
прогоне не выполнялся, поэтому пункт полного end-to-end подтверждения UI оставлен
отдельным ограничением, а не отмечен как выполненный.
Очистка тестовых баз завершена; продуктивной базы и продуктивной миграции в
текущей задаче нет.

### Актуальная повторная проверка после исправлений — 14.09.2026

Приёмка снова не пройдена. Свежие результаты: glossary backend 211 passed,
6 failed (старое ожидание набора форм), 2 skipped; frontend 140 passed, 3 failed;
9 дополнительных сценариев выявили 6 групп оставшихся дефектов. Ruff и целевой
ESLint пройдены. Детали и рекомендации:
[повторная проверка](../reports/2026-09-14-glossary-current-recheck.md).

- [x] Повторно проверены текущие результаты и составлены рекомендации.
- [ ] Task 4/5: корректно отличать пунктуацию от продолжения SAP-идентификатора.
- [ ] Task 6: server-side unresolved_fields и сохранение поисковых прав прежних имён.
- [ ] Task 3/6: конфликтное покрытие отключённых правил и безопасное объединение правил.
- [ ] Task 8: merge учитывает несохранённое изменение существующей карточки.
- [ ] Task 9: синхронизация EN и manifests после добавления полей merge.
- [ ] Task 10: атомарность seed при конфликте с существующим именем/алиасом.
- [ ] Task 11: полная повторная приёмка после исправлений, включая PostgreSQL и UI.

### Последняя проверка текущего дерева — 14.09.2026

Актуальные результаты и рекомендации:
[проверка текущих результатов](../reports/2026-09-14-glossary-latest-verification.md).
Предыдущие числовые результаты выше сохранены как история.

- [x] Повторены девять воспроизведений прошлого ревью: 9 passed.
- [x] Выполнен свежий glossary + hydration suite: 254 passed, 2 failed, 2 skipped.
- [x] Frontend: 144 passed; целевые Ruff и ESLint пройдены.
- [x] Добавлены шесть независимых воспроизведений оставшихся дефектов: 6 failed.
- [ ] Исправить ложное точное обозначение при обрезании snippet внутри длинного кода.
- [ ] Запретить сохранение нового alias, повторяющего вычисленную форму того же ИТ.
- [ ] Завершить явную смену вида карточки и единый migration-required ответ API.
- [ ] Согласовать offline audit с рабочими проверками правил и номеров.
- [ ] Исправить no-op проверку версии, source_revision для изменения номера и оставшиеся UI-переходы.
- [ ] Повторить PostgreSQL concurrency, browser E2E, полный backend/build и измерения корпуса.
- [ ] Task 1–11 полностью выполнены; полная приёмка пока не пройдена.

### Текущий статус после браузерной и независимой проверки результатов

Актуальный отчёт: [проверка итоговых результатов](../reports/2026-09-14-glossary-final-results-review.md).
Этот раздел уточняет исторические отметки выше; полный план ещё не закрыт.

- [x] Frontend: 149 тестов, production build и целевой ESLint пройдены; полный backend Ruff пройден.
- [x] Пять проверок PostgreSQL выполнены на отдельном временном кластере, включая межпроцессное обновление snapshot.
- [x] Четыре проверки LocalQdrant подтвердили изменение поиска после CRUD глоссария без записи в индекс.
- [x] Реальный браузер: пользовательские 4+/3− примера, отказ от merge, несохранённые поля/флаги, disabled state, смена вида карточки, безопасное объединение правил, RU/EN.
- [x] Подготовлены четыре независимых воспроизведения оставшихся дефектов: все 4 failed по ожидаемым нарушениям контракта.
- [ ] Task 3/6: применить запрет лишних вычисленных алиасов и чужого номера ИТ также к merge draft/source_edit и итоговому результату.
- [ ] Task 5: применять бюджет контекста после глобальной сортировки, чтобы слабый sibling не вытеснял более сильный результат другого документа.
- [ ] Task 10: dry-run seed должен проверять конфликты с существующими именами/алиасами/правилами, как apply.
- [ ] Task 9: перевод common.loading и понятное объяснение недопустимого объединения разных диапазонов с разными префиксами.
- [x] Полный штатный backend-прогон: 1374 passed, 5 skipped; пять PostgreSQL-проверок отдельно passed.
- [ ] Task 11: целевой порог производительности +20% пока не достигнут; дополнительные четыре воспроизведения дефектов остаются failed.
- [ ] Task 1–11 полностью выполнены.

## Итог после доработок и замечания об интерфейсе диапазонов, 14.09.2026

Этот раздел заменяет исторические списки открытых дефектов выше. Доказательства,
ограничения и логи — [проверка завершения](../reports/2026-09-14-glossary-completion-check.md).

- [x] Task 3/6: merge draft/source_edit и итоговые алиасы проходят общий запрет
  избыточных форм и чужого номера; прежнее имя не дублирует вычисляемое правило.
- [x] Task 4: bounded matching caches; после трёх revision retries — согласованный
  snapshot под короткой state-блокировкой, без изменения revision/timestamp/audit.
- [x] Task 5: точная проверка итогового текста и бюджет после глобальной сортировки.
- [x] Task 7: типизированный merge result, обязательные digest/revision commit,
  свежий preview при конфликте; read-only rules/check, rules/preview, conflicts/check.
- [x] Task 8: проверка имени при вводе учитывает черновые kind/номер, блокирует
  конфликтный Save/Create и предлагает merge с сохранением полей при отказе.
- [x] Task 8: «Правила инфотипов» имеют видимую форму диапазона, четыре цифры,
  понятное поле префиксов, отдельные create/edit и предпросмотр до сохранения.
  В браузере сохранены независимые диапазоны и проверены включённые границы.
- [x] Task 8/9: focus trap, вложенный language picker/Escape/возврат фокуса;
  понятные ошибки объединения правил, RU/EN и 826-key manifests синхронизированы.
- [x] Task 10: seed preflight проверяет весь namespace; правила и термины
  применяются атомарно. Offline dry-run проигрывает полный план на частной копии,
  initial CAS и final namespace проверены; metadata backup/checksum/audit/replay
  и multi-merge в одну цель подтверждены на SQLite и отдельном PostgreSQL.
- [x] Task 11: frontend 153 passed, production build и целевой ESLint прошли;
  PostgreSQL concurrency 5 passed, migration/snapshot contracts и rollback probes
  прошли; независимое ограниченное ревью новых API/UI без блокирующих замечаний.
- [x] Task 11: полный backend-прогон после фиксации дерева — **1448 passed,
  5 skipped, 113 warnings**, exit 0, 13 мин 27 с. Пять PostgreSQL-тестов отдельно
  прошли; полный Ruff также прошёл.
- [ ] Абсолютный предел p95 согласовать отдельно по выбору пользователя.
  Старый критерий прироста не более +20% не достигнут и не объявляется пройденным.

Существующие пользовательские тестовые базы уже адаптированы с разрешённой
очисткой глоссариев. Переиндексация документов не нужна и не выполнялась.
Коммит, push, повторная миграция продуктивной базы и отдельная прежняя Stage 8
приёмка в эту доработку не входят. Task 11 по производительности остаётся
отдельным согласованием, поэтому весь исходный набор критериев не помечен зелёным.
