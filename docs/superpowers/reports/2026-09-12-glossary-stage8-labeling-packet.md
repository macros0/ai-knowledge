# Stage 8: пакет для утверждения предметных labels

Дата: 12.09.2026. Статус: подготовлено к проверке предметным экспертом; это
не утверждённая разметка и не основание для включения флага.

Для каждой строки эксперт должен выбрать `relevant` или `irrelevant` и
указать короткую причину. Если источник релевантен только для части compound
запроса, это нужно явно написать. Идентичность источника — все три поля
`doc_id / slug / chunk_index`. Для источника-чанка без концепта `slug` в
JSON-объекте указывается `null`, а в ключе `relevance` допускается пустой
средний сегмент: `doc_id||chunk_index`. Историческая форма
`doc_id|None|chunk_index` также принимается валидатором.

Для ускорения проверки подготовлен машинный черновик кандидатов:
[harnessfix4 labeling candidates](../../../tests/artifacts/stage8/probe-stage8-labeling-candidates-harnessfix4-20260912.json).
Он содержит 15 positive/compound кейсов, 209 уникальных источников,
наблюдения в top-5 по API/mode и off/on, а также excerpt из canonical БД.
Поле `status=draft_unapproved`: его нельзя переносить в утверждённые labels
без решения эксперта.

Для отдельной проверки изменения состава final-блоков после выравнивания
search hydration подготовлен дополнительный [final-only review packet](../../../tests/artifacts/stage8/probe-stage8-search-bounded-final-only-review-20260912.json).
Он содержит 75 case-run (21 off, 54 on): старые final-блоки остаются в raw-пуле,
полных raw source losses — `0`. Этот пакет также имеет статус
`draft_unapproved` и не заменяет предметную разметку.

## Кандидаты

### IT0003

**Источник:** `e2641ac63d514f10 / py-es-infotypes-transactions-and-reports / 24`

> `IT0000: Measures ... IT0003: Payroll Status ... IT0006: Addresses`

Инженерный статус: candidate relevant для `pos-it-space`, `pos-it-ru`,
`pos-it-hyphen`; требуется экспертное подтверждение, что перечень достаточен
для ответа на эти запросы.

**Источник:** `f48f01e6814f4418 / yea-retro-trigger-vs-regular-retro / 66`

> В описании стандартного поведения retro-расчёта указано, что при изменении
> master data в `IT0003` устанавливается самая ранняя дата изменения данных.

Инженерный статус: candidate relevant для запроса о поведении IT0003 в retro;
для простого запроса имени инфотипа может быть только допустимым эквивалентным
источником, а не обязательным.

### PA30

**Источник:** `e2641ac63d514f10 / dump-dbifrsqlinvalidrequest-al-crear-un-infotipo-en-la-transaccin-pa30 / 13`

> При просмотре или обновлении через транзакцию `PA30` инфотипа `IT0016`
> возникает dump; далее описаны причины и исправление.

**Источник:** `e2641ac63d514f10 / dump-during-infotype-creation-at-transaction-pa30 / 15`

> При создании инфотипа в транзакции `PA30` возможны short dump
> `DBIF_RSQL_INVALID_REQUEST` и `DYNPRO_FIELD_CONVERSION`.

Инженерный статус: оба источника candidate relevant для `pos-pa30-shell`; для
`compound-compare` эксперт должен подтвердить, что один из них релевантен
одновременно запросу про PA20 и PA30 или что это допустимые отдельные блоки.

### PA40

**Источник:** `6c23fd3550434f9f / formirovanie-soobscheniy-dlya-otpuska-uhoda-za-rebenkom-do-15-let / 3`

> Отзыв из отпуска по уходу за ребёнком проводится мероприятием через
> `pa40`, после чего вручную формируется сообщение для СФР.

**Источник:** `f48f01e6814f4418 / sap-note-t522n-personal-name-format / 58`

> Формат `SM30 => V_T522N` используется для master data через
> `PA20/PA30/PA40`.

Инженерный статус: первый источник candidate relevant для `pos-pa40-shell`;
второй — candidate relevant для compound/контекста про формат имён, но не
обязательно для общего запроса PA40.

### SM30

**Источник:** `03713ad7b0db416c / generatsiya-dialoga-vedeniya-tablitsy / 7`

> Для запуска диалога ведения конечным пользователем предлагается создать
> транзакцию параметров на стандартную транзакцию `SM30`.

**Источник:** `814d3cae24b94d6d / us-pd-infotypes-overview / 3`

> Внешние инфотипы поддерживаются через соответствующее представление;
> обслуживание через такие представления сопоставляется с `SM30`.

Инженерный статус: первый источник candidate relevant для `pos-sm30-shell`;
второй требует экспертной проверки связи с конкретным запросом, поскольку
контекст описывает US PD и customizing views.

## Кандидаты, которые не следует принимать автоматически

- `SE16`: кандидаты `webdynpro-okna` и `elementy-dannyh` содержат общие слова
  или число, но не доказывают релевантность транзакции `SE16`.
- `SE38`: текущие top-5 блоки не дают достаточного отдельного доказательства.
- `PA20`: найденные блоки требуют проверки, относится ли текст к PA20, а не
  только содержит соседнее упоминание.

## Формат ответа эксперта

```text
case: pos-pa30-shell
source: e2641ac63d514f10 / dump-during-infotype-creation-at-transaction-pa30 / 15
label: relevant
mandatory: yes
reason: описание создания инфотипа именно через PA30
reviewer: <имя>
reviewed_at: <ISO-8601>
```

После утверждения labels их нужно перенести в `backend/test_scripts/glossary-probe-cases.json`,
зафиксировать версию/хэш labels и заново выполнить off/on на одном snapshot.
В актуальном bounded protocol-1 quality report зафиксировано `7951 unresolved`
событие изменения; более ранние `8049` и другие числа относятся к историческим
срезам. Это отсутствие утверждённых labels, а не подтверждённые потери.
`GLOSSARY_QUERY_EXPANSION_ENABLED` остаётся выключенным.
