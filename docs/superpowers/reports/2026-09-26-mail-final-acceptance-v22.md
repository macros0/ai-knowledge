# Финальная проверка импорта писем, v22

## Исправление найденного пробела

При сверке задачи 4 с реализацией найдено отсутствие наследования named MAPI
properties. `Content-Class` читался только из transport headers. Бинарные
CFB fixtures воспроизвели, что IRM MSG без такого заголовка отдавал fallback
body для индексации, включая embedded MSG с общей корневой картой.
Первый прогон: 9 failed/2 passed; провалены именно новые ожидаемые защиты.

Reader теперь разрешает только выбранный `PidNameContentClass` по GUID
`00020386-0000-0000-c000-000000000046` и имени `Content-Class`, читая корневую
карту один раз. Вложенные сообщения используют эту карту и свои значения.
Посторонние named values не открываются. Проверяются индексы, длины, смещения
и UTF-16; размеры потоков проверяются до `olefile.openstream`. Повреждённая
карта отклоняется. Защита учитывается при наличии маркера в любом из двух
представлений, поэтому конфликт headers/native не скрывает IRM.

Формат сверялся с первичными спецификациями:
[MS-OXMSG root mapping](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxmsg/621801cb-b617-474c-bce6-69037d73461a),
[Index and Kind](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxmsg/abdc1a7d-5a44-4bb2-aa35-b241e4a3f0d9),
[PidNameContentClass](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxprops/cc6193d9-202d-4696-9750-d40248018194).
Код стороннего GPL parser не использовался.

Версия `mail-sources-v22` исключает смешивание старых checkpoints с новым
результатом. Старые опубликованные документы читаются; массовая перегенерация
не запускалась. Для незавершённого checkpoint старой версии нужен адресный
regenerate, а не resume с несовместимым parser contract.

## Проверки исправления

- Windows parser: 399 passed/1 POSIX skip; Linux parser: 400 passed.
- 15 новых native-map cases: root/child, ancestor map, nonzero GUID/property
  index, wrong GUID, conflicting markers, malformed maps, pre-read size limit,
  unopened unrelated named value. Все входят в полные parser suites.
- Windows backend scope: 65 passed/5 environment skips; новый двухсценарный
  pipeline test дополнительно проверен на frozen parser: 2 passed.
  Реальные spawn/SQL/FS, fake LLM/Qdrant; закрытое тело отсутствует в вызовах
  LLM, canonical chunks и concepts, warning принадлежит нужному source.
- Первый backend scope получил два отказа из-за ошибочного тестового ожидания
  string-list вместо фактического structured warnings. Проверка исправлена
  на точное сравнение code/source_id; продукт для этого не менялся.
- Полный Ruff backend app/tests и изменённых parser paths: без ошибок.
- Исходный частный Outlook Save As MSG повторно разобран v22 локально:
  64512 bytes, 4 blocks, 3 sources/2 attachments, русский текст,
  нет replacement/warnings. Без LLM/Git/CI.
- P05: неизменный корпус 15 файлов, два разных рабочих каталога на каждой OS,
  Windows/Linux logical output совпал, differing_files=[].

Неизменяемый срез `tests/tmp/mail-final-acceptance-v22-r2/source` содержит
764 файла, исключает `.env`, частные письма, `.git` и локальные зависимости.
SHA manifest, HEAD/status/diff и результаты лежат в соседнем `reports`.
Это экспорт текущего dirty checkout, не клон опубликованного коммита.
Полный Linux backend v22 завершён с exit0: **2122 passed, 19 skipped,
334 warnings, 611.18s, coverage89.06%** при обязательном пороге85%.
Все пять реальных ENOSPC tests прошли. Пропуски: девять Windows-only cases,
пять glossary PostgreSQL cases без opt-in URL и пять migration PostgreSQL
cases без opt-in admin URL. Mail migration PostgreSQL отдельно доказан R06
на изолированных данных; эти пропуски не называются выполненными тестами.
Первая Linux команда остановилась после успешного parser suite, поскольку
в verification image нет Ruff. Ruff проверен установленным Windows tool;
backend запущен отдельно, без изменения исходников или установки пакетов.

## Браузер и runtime

Подтверждённый тестовый backend PID45408 заменён PID36740 на том же isolated
`mail_http_acceptance` PG25432/Qdrant26333. Startup подтвердил v22 и обе
разрешённые модели GPT-4.1. Рабочие PG5432/Qdrant16333 и Keycloak не менялись.
Локальный mail flag остаётся включённым.

Два synthetic MSG загружены через авторизованный UI demo.admin. Root IRM
отклонён preflight с 422 `mail_protected`; дочерний IRM в читаемом родителе
принят как документ `bc8e5e5fd1ab47c6`: done, problem=protected_mail,
один концепт родителя. Дерево показывает защищённый child и честное скачивание
контейнера. Fulltext содержит VISIBLE PARENT и не содержит PRIVATE FALLBACK.
DOM и screenshot: `reports/named-protected-browser.{json,png}`,
`named-protected-fulltext.json`; startup/error logs сохранены локально.
Одиночный root upload дополнительно пойман в UI с локализованным отказом:
`named-root-refusal-visible.{json,png}`. Read-only SQL проверка
`browser-db-proof.json` подтвердила отсутствие строки отклонённого root,
два source nodes v22, два чанка, один концепт на root, warning на root/0
и отсутствие PRIVATE FALLBACK в обоих производных хранилищах.

## Общая приёмка и её границы

Предыдущий финальный срез v21 прошёл full backend Windows
2118/21skip/89.51% и Linux2120/19skip/89.07%, parser384+1/385,
frontend190 Windows и Linux, ESLint/build. Сохраняются предупреждения линтера
и dev Fast Refresh, они не выданы за отсутствие любых warnings.
Полный Windows suite v22 повторно не запускался: изменён parser, проверенный
полным Windows suite, и связанные backend пути. Полный Linux v22 дополняет
эти результаты. Frontend runtime code с v21 не менялся.

На v21 отдельно закрыты chain6/6 с real GPT-4.1, Q01 девять gates/100inputs,
Q02 replay252 без потерь, Windows/Linux unsafe paths14+14 и два NTFS junction,
клавиатурные RU/EN download names, пять plain-mail браузерных views без img
и запросов canary. Это конкретные измерения, не универсальные гарантии.
Доказательства: [v21 report](2026-09-26-mail-final-acceptance-v21.md),
[load/replay](2026-09-26-mail-v21-load-replay.md),
[authorship](2026-09-26-mail-authorship-verification.md).

Ограничения остаются явными: RTF-only, шифрование и неизвестные Outlook layouts
не превращаются в успешно извлечённый текст; подписи криптографически не
проверяются. GPT-4.1 прошла небольшой синтетический набор и не даёт общей
гарантии против prompt injection. Авторство публикуется только на основе
проверенных исходных цитат; неподдержанные формулировки могут дать неизвестного
автора. Risk/admin gate сохранён отдельным будущим backlog. Production rollout,
обслуживание drift рабочей БД, commit/push и массовый regenerate не входят
в выполненную приёмку и не запускались.

## Сверка двенадцати задач исходного плана

| Задача | Итог и основные доказательства |
|---|---|
| 1. Fixtures/provider | Собственный ограниченный olefile reader, BSD property map; выбор/лицензии и unsupported в MAIL_FORMAT_SUPPORT. Public/synthetic binary corpus и локальный direct Outlook Save As |
| 2. Бюджет/supervisor | Общие depth/bytes/nodes/text budgets, spawn isolation, deadline/memory/slots, безопасные имена, symlink и два настоящих NTFS junction; L01–L10 |
| 3. EML/MIME | Encoding, multipart, plain/HTML, headers/privacy, CID/local images, literal escaping; M01–M03, S01/S02, A01/A04 |
| 4. MSG/context | Fixed streams и собственные root/child значения, методы1/5; ancestor named map закрыт v22. RTF-only/protected/non-mail диагностируются |
| 5. Рекурсивные документы | DOCX OLE/table/story, XLSX/PDF embedded, bounded depth; M06–M14 и свежая реальная цепочка6/6 |
| 6. SQL/миграция | Nullable provenance/additive schema; SQLite/PostgreSQL roundtrip, drift/repair и rollback отдельно проверены R06 |
| 7. Admission/pipeline | Preflight до записи, стабильные коды, partial warnings, checkpoints/publication/retry и no false done; L/D/R suites |
| 8. RAG/spans | Source-owned chunks/concepts, exact quote/hash/Unicode offsets, legacy fallback, inferred lexical fallback; P01–P05 и chain/download/browser |
| 9. Дедупликация | Byte exact остаётся отказом; semantic match требует consent, provenance сохраняется; D01–D05 |
| 10. API/UI | Authenticated source tree/original/container downloads, RU/EN, keyboard/focus/hydration; U01/U02/A03 |
| 11. Lifecycle/rollback | Trash/restore/purge/export, backup/restore drill, dry-run reparse selector, flag compatibility; R01–R08. Локальный flag включён по указанию пользователя |
| 12. Приёмка | Immutable exported sources, full suites/CIcoverage/build, Q01 100inputs/9gates, Q02 252replays, пять Linux ENOSPC; отчёты и границы покрытия сохранены |

Все 58 строк матрицы имеют доказательство в заявленном объёме. Финальный
`reports/verification-summary.json` проверяет 764 SHA, отсутствие изменений
кода после freeze, равенство frontend к проверенному v21, JUnit без failures,
P05 и browser/SQL proofs. После freeze изменены только отчётные документы.
Согласованный план реализации и приёмки выполнен. Это не заявление о
production deployment или безусловной поддержке всех Outlook-файлов.
