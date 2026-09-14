# Stage 8: кандидаты для предметной разметки

Дата: 11.09.2026. Источник: `tests/artifacts/stage8/probe-stage8-matrix-on.json`,
вариант `hybrid/chat`, top-5. Статус всех строк: `unresolved`; этот файл не
является утверждением релевантности и требует проверки предметным экспертом.

При разметке нужно подтвердить точную тройку `doc_id/slug/chunk_index`, а для
чанка без slug — сам `doc_id/chunk_index` и содержимое. Совпадение числа,
названия транзакции в тексте или общего слова не считается достаточным.

| Сценарий | Ожидание | Кандидаты из top-5 |
|---|---|---|
| `pos-it-space`, `pos-it-ru`, `pos-it-hyphen` | `IT0003` | `e2641ac63d514f10 / py-es-infotypes-transactions-and-reports / 24`; `f48f01e6814f4418 / yea-retro-trigger-vs-regular-retro / 66` |
| `pos-pa20-shell`, `pos-pa20-space` | `PA20` | `47fe1a1a380646f1 / rasshirenie-dlya-spravki-182-n-hrulpay2 / 0`; `814d3cae24b94d6d / chunk 0`; `f48f01e6814f4418 / chunk 52`; `f48f01e6814f4418 / chunk 61` |
| `pos-pa30-shell` | `PA30` | `e2641ac63d514f10 / dump-dbifrsqlinvalidrequest-al-crear-un-infotipo-en-la-transaccin-pa30 / 13`; `e2641ac63d514f10 / dump-during-infotype-creation-at-transaction-pa30 / 15` |
| `pos-pa40-shell` | `PA40` | `f48f01e6814f4418 / sap-note-t522n-personal-name-format / 58`; `6c23fd3550434f9f / formirovanie-soobscheniy-dlya-otpuska-uhoda-za-rebenkom-do-15-let / 3` |
| `pos-se16-shell` | `SE16` | `03713ad7b0db416c / webdynpro-okna / 6`; `03713ad7b0db416c / elementy-dannyh / 5`; `03713ad7b0db416c / concept-10 / 2`; `6c23fd3550434f9f / zsedouserdata / 9` |
| `pos-se38-shell` | `SE38` | `f48f01e6814f4418 / chunk 14`; `e2641ac63d514f10 / chunk 14` |
| `pos-sm30-shell` | `SM30` | `f48f01e6814f4418 / sap-note-t522n-personal-name-format / 58`; `814d3cae24b94d6d / us-pd-infotypes-overview / 3`; `03713ad7b0db416c / generatsiya-dialoga-vedeniya-tablitsy / 7` |
| `compound-two-codes` | `PA20`, `IT0003` | `e2641ac63d514f10 / py-es-infotypes-transactions-and-reports / 24`; `f48f01e6814f4418 / sap-note-t522n-personal-name-format / 58`; `f48f01e6814f4418 / yea-retro-trigger-vs-regular-retro / 66` |
| `compound-repeat` | `PA20`, `IT0003` | `e2641ac63d514f10 / py-es-infotypes-transactions-and-reports / 24`; `f48f01e6814f4418 / sap-note-t522n-personal-name-format / 58`; `f48f01e6814f4418 / yea-retro-trigger-vs-regular-retro / 66` |
| `compound-compare` | `PA20`, `PA30` | `f48f01e6814f4418 / sap-note-t522n-personal-name-format / 58`; `e2641ac63d514f10 / dump-dbifrsqlinvalidrequest-al-crear-un-infotipo-en-la-transaccin-pa30 / 13`; `e2641ac63d514f10 / dump-during-infotype-creation-at-transaction-pa30 / 15` |
| `compound-three` | `PA20`, `IT0003`, `PA40` | `e2641ac63d514f10 / py-es-infotypes-transactions-and-reports / 24`; `f48f01e6814f4418 / sap-note-t522n-personal-name-format / 58`; `f48f01e6814f4418 / yea-retro-trigger-vs-regular-retro / 66`; `6c23fd3550434f9f / formirovanie-soobscheniy-dlya-otpuska-uhoda-za-rebenkom-do-15-let / 3` |
| `compound-ru-shell` | `IT0003`, `SE16` | `e2641ac63d514f10 / py-es-infotypes-transactions-and-reports / 24`; `f48f01e6814f4418 / yea-retro-trigger-vs-regular-retro / 66` |

Наблюдения для следующего шага:

- Для `PA30` есть два очевидных кандидата с точным названием транзакции; их
  можно предложить эксперту на подтверждение.
- Для `IT0003` и остальных коротких обозначений верхняя выдача содержит
  числовые или общие совпадения; автоматическое принятие запрещено.
- Для `PA20`, `PA40`, `SE16`, `SE38`, `SM30` в текущем корпусе не видно
  достаточного доказательства обязательного документа. Если эксперт не
  подтвердит источник, нужно добавить герметичный fixture, как требует §15.1,
  либо убрать кейс из positive corpus acceptance и оставить его только в
  проверке распознавания.

Следующее действие: эксперт отмечает `relevant`/`irrelevant` и причину для
каждого кандидата; после этого labels переносятся в cases и выполняется новый
парный прогон off/on.
