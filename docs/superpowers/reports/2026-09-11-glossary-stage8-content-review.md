# Stage 8: предварительная проверка содержимого кандидатов

Дата: 11.09.2026. Статус: инженерная предварительная разметка, не утверждение
предметного эксперта и не основание для включения флага.

Источник кандидатов: `docs/superpowers/reports/2026-09-11-glossary-stage8-label-candidates.md`.
Проверка выполнена по полю `okf_concepts.content` для полной идентичности
`doc_id/slug/chunk_index`. Совпадение только в заголовке, числа или общего
слова не принималось за релевантность.

| Query/canonical | Источник | Предварительный статус | Основание |
|---|---|---|---|
| `IT0003` | `e2641ac63d514f10 / py-es-infotypes-transactions-and-reports / 24` | relevant candidate | Список явно содержит `IT0003: Payroll Status`. |
| `IT0003` | `f48f01e6814f4418 / yea-retro-trigger-vs-regular-retro / 66` | relevant candidate | Текст несколько раз описывает стандартное поведение `IT0003` при retro-расчёте. |
| `PA30` | `e2641ac63d514f10 / dump-dbifrsqlinvalidrequest-al-crear-un-infotipo-en-la-transaccin-pa30 / 13` | relevant candidate | Описание ошибки явно относится к просмотру/изменению через транзакцию `PA30`. |
| `PA30` | `e2641ac63d514f10 / dump-during-infotype-creation-at-transaction-pa30 / 15` | relevant candidate | Английский текст явно описывает создание infotype в транзакции `PA30`. |
| `PA40` | `6c23fd3550434f9f / formirovanie-soobscheniy-dlya-otpuska-uhoda-za-rebenkom-do-15-let / 3` | relevant candidate | Текст явно говорит, что мероприятие отзыва выполняется через `pa40`. |
| `PA20`, `PA30`, `PA40`, `SM30` | `f48f01e6814f4418 / sap-note-t522n-personal-name-format / 58` | relevant candidate | Текст явно связывает `SM30` и `V_T522N`, а формат — с `PA20/PA30/PA40`. |
| `SM30` | `03713ad7b0db416c / generatsiya-dialoga-vedeniya-tablitsy / 7` | relevant candidate | Текст явно описывает стандартную транзакцию `SM30` для ведения таблицы. |
| `SM30` | `814d3cae24b94d6d / us-pd-infotypes-overview / 3` | relevant candidate | Текст явно сравнивает обслуживание представления с обслуживанием через `SM30`. |
| `SE16` | `03713ad7b0db416c / webdynpro-okna / 6` | irrelevant candidate | Содержимое описывает Web Dynpro и соглашение именования; `SE16` не подтверждена. |
| `SE16` | `03713ad7b0db416c / elementy-dannyh / 5` | irrelevant candidate | Содержимое описывает элементы данных; `SE16` не подтверждена. |
| `SE16` | `03713ad7b0db416c / concept-10 / 2` | unresolved | По короткому идентификатору источника недостаточно доказательства; требуется просмотр полного содержимого/предметная проверка. |

## Что остаётся проверить

- `PA20` и `SE38`: текущие top-5 кандидаты не дают достаточного отдельного
  доказательства обязательного источника; нельзя принимать их автоматически.
- Для составных запросов нужно проверить не только наличие каждого canonical,
  но и релевантность конкретного блока в контексте всего запроса.
- Статусы выше нужно перенести в `relevance` mappings только после
  подтверждения экспертом. После этого off и on пересчитываются на одной и той
  же версии cases/labels.

## Решение для приёмки

В историческом срезе до утверждения этой разметки было `8049 unresolved`;
актуальный bounded protocol-1 содержит `7951` неразмеченное событие.
Оба числа означают отсутствие утверждённых labels, а не подтверждённые потери.
Глобальный флаг `GLOSSARY_QUERY_EXPANSION_ENABLED` остаётся выключенным.
