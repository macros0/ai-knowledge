# Приёмка базовой поддержки Outlook-писем

Текущая итоговая проверка: [v22](2026-09-26-mail-final-acceptance-v22.md). Обязательный native named-map пробел исправлен; результаты прежних версий ниже — история.

Последнее дополнение: GPT-4.1 для генерации и строгая проверка авторства разрешены и реализованы. Финальные положительные/отрицательные контроли: 40 Windows/40 Linux и четыре реальные синтетические загрузки с GPT-4.1; [проверка авторства](2026-09-26-mail-authorship-verification.md). Полный предыдущий frozen backend2115/19skip/coverage89.04% прошёл; последние изменения авторства проверены отдельно и не выдаются за полный повтор. Свежий v21 Q01: все девять gates и SQL/Qdrant audit пройдены; Q02: 252 сценария без потерь/перестановок и неизменный snapshot — [нагрузка и replay](2026-09-26-mail-v21-load-replay.md). Общая приёмка сохраняет отдельно отмеченные открытые критерии. Предыдущие версии и ожидания решения ниже — история, не текущий статус.

## Обновление 26.09: итоговое ревью и исправления v21

M04 producer provenance закрыт в проверенном объёме: владелец подтвердил direct Outlook Save As MSG, текущий локальный v21 repeat завершён exit0 без warnings/replacement, два вложения извлечены. [Проверка реального экспорта](2026-09-26-mail-outlook-export-verification.md). Частный исходник не передавался LLM/Git/CI.

Единственное независимое whole-change ревью выявило1P1/6P2 на frozen v20,
несмотря на полные Windows2086/Linux2083 pass и CI coverage88.95%. Один проход
исправлений: plain Markdown images, mixed MIME/alternative diagnostics,
decode warnings, private attempt cleanup, central budget warnings, bounded MSG
remainder и native EX sender. Parser version v21 исключает смешение старых
checkpoint с новым canonical layout. Модели и рабочие данные не менялись.

Текущий parser: Windows384pass/1POSIXskip, Linux385pass; frontend190/build
и targeted backend83 unique pass (5Windows platform skips). Actual viewer5
static renders и positive CID browser keyboard path прошли. Full Linux v21
с CI85% и real8MiB ENOSPC повтором ещё running, не присвоен заранее.
Подробности/ограничения: [отчёт исправлений](2026-09-26-mail-final-review-fixes.md).
Общая приёмка остаётся открытой: A02/S07 решения, M04/U01 и другие partial gates.

## Обновление 26.09: клавиатурный просмотр картинок

В Tab-проходе найдены и исправлены недоступное увеличение картинок,
выход Tab из lightbox и потеря focus при замене кнопки React. EML/MSG
проходят Enter/Space, Tab/ShiftTab, Escape/Close и возврат к своей кнопке;
RU/EN reload без console errors/warnings. После последнего изменения
188 frontend tests, scoped ESLint и Next build прошли. Подробности и
границы U01: [UI отчёт](2026-09-26-mail-ui-locale-verification.md).

## Обновление 26.09: заполненный диск

Реальный Linux tmpfs8MiB выявил и позволил исправить потерю `storage_full`
в supervisor и перехват ENOSPC как повреждения вложения в embedded/MSG.
Пять сценариев прошли: upload507 без ghost, три разновидности spawn parse,
сохранение опубликованного поколения при paused и успешный resume.
Полный parser: Windows369+1skip/Linux370; связанный backend suite98+5skip/
95+8skip, skips покрыты на другой ОС. Подробности и оставшаяся DB/Qdrant/
native Windows область: [mail-disk-full-verification](2026-09-26-mail-disk-full-verification.md).
Предыдущий полный backend2084 относится к срезу до этих исправлений;
новый полный прогон пока не заявляется. L10 частичный, A02/S07 открыты.

## Обновление 26.09: язык и клавиатурная навигация

В браузере воспроизведён и исправлен stale RSC язык: `LocaleContext` теперь
вызывает `router.refresh()` после записи cookie при явной смене языка.
Fulltext/list/source headings меняются без ручного reload; сохраняются
открытый исходник, вкладка чанков, фокус и неотправленный черновик чата.
Enter-навигация к вложенному концепту/абзацу и скачивание MSG проверены;
download hash совпал с active-generation файлом. Свежие188 frontend tests,
ESLint и Next build прошли. Подробности и ограничения:
[mail-ui-locale-verification](2026-09-26-mail-ui-locale-verification.md).
U01 остаётся частичным: полный screen-reader/Tab/modal/first-load hydration
аудит не выполнен. A02/S07 остаются открытыми; модели не изменены.

Последующий U01 проход выявил и исправил общий Modal focus defect: начальный
фокус вне окна, Tab leak и потеря фокуса при Escape. На окончательном коде
exact/similar upload modal и bulk-preview→typed confirmation→возврат прошли
browser checks; отмена похожего файла не добавила документ. Операции удаления
и генерации не подтверждались. Свежие188 frontend tests/ESLint/build прошли.
Подробности, первоначальный stale-HMR повтор и ограничения общего компонента
записаны в том же [UI отчёте](2026-09-26-mail-ui-locale-verification.md).

## Обновление 26.09: окончательный технический backend suite текущего среза

**2084 passed,16 skipped,29 warnings;1472.62s;exit0**. Bare pytest из backend,
артефакты `tests/tmp/mail-final-v20-next/backend-full.{log,xml}`. Manifest327
app/tests/prompts/parser files проверен после завершения: изменений за время
прогона нет (`verification.json`). Новый прогон включает privacy12, boundary8
и adversarial7; он заменяет прежний2057 для этого текущего среза.

Это Windows technical regression gate. Linux privacy12+boundary8/adversarial7
проверены отдельно; новый полный Linux backend suite не заявляется.
Warnings/skips сохранены, failures/errors0. Все16 skips затем пройдены отдельно:
PG migrations/glossary15passed (10 нужных +5SQLite controls),29.74s; Linux
POSIX/symlink6passed,10.42s. Exact testcase IDs покрывают все исходные16,
что закреплено в `verification.json`; это дополнительные прогоны, а не
переписанный результат исходного pytest. **A02 по текущему Nemo остаётся
непройденным** в настоящих mail/DOCX LLM probes; зелёный pytest не заменяет
смысловую приёмку. Решения по генерации/авторству ещё открыты, M04/U01/final
review также требуют завершения; общая задача не объявлена готовой.

## Обновление 26.09: штатные backup/restore scripts

R04 выполнен на synthetic Windows baseline: **production restore → production
backup → второй production restore**, настоящие Bash/Docker в WSL, новые
projects/volumes/Linux data directories, без replace. Strict integrity и
real backend HTTP/SQL/Qdrant подтвердили5 hashes,3 sources/chunks/locations,
6 прежних point IDs и active generation. Негативные checksum/overlap проверки
прошли; source writers не перезапущены при отказе. Production scripts не менялись.
Подробности: [mail-production-backup-drill](2026-09-26-mail-production-backup-drill.md).

Ограничения: auth disabled/development, fake исходные LLM/embeddings,
frontend inert HTTP fixture вместо Next.js; один synthetic документ с EML/DOCX.
Это проверка scripts/данных, не production deployment. Оба новых проекта
остановлены, архивы/тома сохранены; рабочие службы не перезапускались.

## Обновление 26.09: injection генерации и расширенная приватность

**A02 остаётся непройденным для рабочей Mistral Nemo.** Реальные synthetic
письма заставляют генератор вернуть[] либо canary вместо концептов. Правило
о недоверенном содержимом добавлено в canonical/fallback prompt, но повтор
сохранил факты только2/4. Отдельное напоминание тоже не помогло (1/4).
GPT‑4.1 в изолированном сравнении прошла8/8 запросов и4/4 полных upload→chat
сценария с byte-identical входами. Рабочая генерация не изменена; пользователю
представлен отдельный выбор. Дополнительные обычные controls выявили и
ограничение GPT‑4.1: пояснение кода, которое явно не дано в исходнике.
Подробности и воспроизводимая команда:
[mail-generation-injection report](2026-09-26-mail-generation-injection.md).

A04 расширен: **12 passed Windows/37.48s**, **12 privacy+8 boundary passed
Linux/40.03s**. Проверены final HTTP chat200/503, actual provider inputs,
SQLite history, parent logs и worker stdout/stderr, malformed CFB root/child
и deadline. Заголовки Bcc/transport не попали в производные данные/ошибки;
оригиналы сохраняются. Offline boundary+PromptStore/OKF — **90 passed Windows**.
Это конкретная матрица, не обещание исчерпать все исключения сторонних библиотек.

Новый полный backend suite запущен: `tests/tmp/mail-final-v20-next/`;
результат ожидается. Предыдущие2057 не включают новые boundary/adversarial/
privacy случаи. S07 авторство также ожидает решения; остальные gates продолжаются.

## Обновление 26.09: реальные опасные входы L04 на Windows и Linux

Добавлен `backend/tests/test_mail_adversarial.py`: семь генерируемых synthetic
входов проходят настоящий production spawn worker без подмены parser target:
valid/malformed base64 вложение с decoded size51MiB (wire≈68MiB), HTML-текст
3.12MB, native CFB MSG с compressed-RTF header, объявляющим 1GiB результата,
ZIP с настоящим ratio>1000 как корень DOCX и как вложение EML, 1500 MIME
вложений. ZIP отклоняется до OOXML разборщика; readable parent и proof sibling
сохраняются. Base64 не сохраняется, HTML ограничен2M Unicode chars, RTF
явно unsupported без расширения, MIME даёт aggregate count warning.

Окончательная версия с параллельными ASGI HTTP запросами: **7 passed Windows
(27.46s)**, **7 passed Linux (26.22s)**. Во время каждого worker `/api/documents`
отвечает200 (2–41 запроса Windows, 3–41 Linux); после каждого случая реальный
worker успешно разбирает обычное письмо, API отвечает, новых live children нет.
Память ОС1024MiB, wall timeout20s — строже штатного120s. Наблюдаемый RSS максимум
**108.875MiB Windows /119.609MiB Linux**, максимальное время **9.031s /8.876s**,
CPU **8.984s /9.000s**. Сохранённые bytes максимум4225 (исходный bounded ZIP
и proof sibling); это итоговый размер артефактов, не мониторинг peak disk.
Наблюдаемая RSS выборка не является измерением точного пика между сэмплами.

Связанные supervisor/admission suites до добавления HTTP heartbeat:
**46 passed/5 skipped Windows**, **43 passed/8 skipped Linux**; skips платформенные.
Ruff clean. Production код не изменён; полный предыдущий backend2057 не включает
новые семь случаев, которые проверены отдельно. Linux: установленный image
`okf-mail-verify-tests:20260926-fonts`, network none, read-only app/parser/tests/
prompts, user1000, container2GiB/2CPU, tmpfs384MiB; ASGI/auth simulation/SQLite
локальны, внешние LLM и runtime хранилища не используются.

Стенд исправлен по наблюдаемым ошибкам: отсутствующий psutil заменён OS API;
256MiB не позволяли пройти даже обычному control из-за OpenBLAS startup и
заменены утверждённым1024MiB. Literal MIME warning исправлен на
`mime_count_exceeded`. Linux collection дважды обнаружила отсутствующие
read-only image каталоги data/prompts; использован tmp DATA_DIR и read-only
канонические промпты. Эти ошибки не выдаются за production дефекты.

Артефакты: `tests/tmp/mail-adversarial-v20/{windows-http,linux-http}.{log,xml}`,
JSON каждого случая в одноимённых каталогах (input SHA, CPU/RSS/time/disk,
HTTP heartbeat/recovery). Это матрица конкретных опасных форматов, не обещание
покрытия произвольных повреждений/всех способов исчерпания диска. S07 решение
об отдельной проверке доказательств авторства по-прежнему ожидается.

## Обновление 26.09: свежая нагрузка v20 и активное содержимое в браузере

Q01 завершён exit0 после полного backend suite: **100 входов, 86 590 043 bytes,
все девять критериев пройдены**. Обычный разбор письма p95 **0.411s**,
максимальный наблюдаемый worker RSS **96.449MiB**. Контрольные отношения p95
с включённым/выключенным mail import: DOCX **0.956**, PDF **0.945**.
Поиск до нагрузки p95 **55.606ms** (100 запросов), при нагрузке **52.801ms**
(1178 запросов), отношение **0.950**; HTTP errors/orphans/lost slots отсутствуют.
Реальные ASGI HTTP, PostgreSQL17, Qdrant1.19 и supervised spawn; LLM/8D
эмбеддинги локально подменены. Это не проверка скорости внешнего LLM.

Read-only audit подтвердил **101 документ** с control, **131 источник,
131 чанк, 121 концепт, 252 точки**, точное соответствие IDs и ownership,
done/problem=NULL и отсутствие незавершённого поколения. Артефакты:
`tests/tmp/mail-load-v20/{load-report,index-audit}.json`.

A01: через реальный Keycloak UI загружен synthetic `hostile-proxy-v20.eml`,
документ `4fe4f2c1b0394e4e`, SHA256
`ebfab1c0b487df348356933031e40f8d64ec3952789f2499a1f540723d00bad6`.
В EML и вложенном настоящем CFB MSG есть script, iframe, object, style URL,
inline SVG, remote pixel/onerror, javascript href; отдельно active SVG CID.
Проверены fulltext, оба чанка и все шесть концептов. В `.okf-markdown` нет
активных элементов, handlers или unsafe href; body marker отсутствует.
Обе локальные PNG загружены, безопасные ручные ссылки сохранены и не нажимались.
SVG сохранён как ограниченный `.bin`, root предупреждает `cid_image_unavailable`,
документ done с ожидаемым `attachment_partial_result`.

Временный ASGI observer записывает только `/api/mail-canary/`, делегируя
обычные ответы без новых routes. Положительный browser control зарегистрирован
backend (404; UI сообщает blocked-by-client для отображения), а просмотр
содержимого не дал ни одного canary запроса. Прямая попытка отдельного порта
18777 блокировалась браузером и не используется как отрицательное доказательство.
Проверка относится к доступному same-origin proxy, не ко всей внешней сети.
Первый широкий DOM selector включал SVG-иконку приложения; повтор с областью
контента подтвердил отсутствие SVG именно из документа. Первый снимок lazy
images делался до загрузки; готовые локальные изображения проверены отдельно.

SQL/FS audit: source IDs `root`, `root/0`, `root/1`, `root/2`, `root/2/0`, чанки root и root/2,
canonical SHA корректны; root original побайтно равен входу. Доказательства:
`tests/tmp/mail-ui-v20/hostile-{fulltext,scoped-views,storage}-audit.json`,
`hostile-fulltext.png`, `proxy-canary-requests.jsonl`. Это bounded synthetic
browser acceptance; prompt injection A02, полный error-log audit A04,
production restore R04 и ошибка авторства S07 остаются открыты.

После проверки отсутствия активных документов observer убран; восстановлен
обычный isolated `serve_backend.py`. Startup подтверждает v20/GPT-4.1 chat/
Mistral Nemo generation; health ok, Keycloak авторизация и источник доступны.

## Обновление 26.09: приватные заголовки и свежий поиск на старом корпусе

Новый `backend/tests/test_mail_privacy.py`: **8 passed на Windows (20.88s)**
и **8 passed на Linux (16.49s)**. EML/MSG × normal/unsupported TNEF ×
fresh/resume проходят настоящий supervised parser, SQLite, FS/publication,
построение PointStruct и RAG hydration. В MSG есть настоящий recipient
storage с `RecipientType=3`; fixture подтверждает его чтение.

На границе `_complete_once` захватываются готовые system/user промпты; ответ
LLM и Qdrant ack заменены локальными doubles. Контрольные Bcc/transport значения
не встречаются в этих промптах, canonical metadata/text, `.md/.json` бандла,
сериализованных points, RAG-контексте и родительских INFO-логах. Тело остаётся
в производных данных и отсутствует в логах. Root original побайтно сохранён,
вложенный original содержит свой Bcc — это предусмотренный download contract.

Первый усиленный Linux-прогон выявил ошибку именно стенда: settings alias
`field_table` не подменялся и пытался создать `/app/data` в read-only image.
После двух воспроизведённых failures оставшиеся случаи прерваны для диагностики;
alias исправлен, итоговые XML `privacy-boundary-{final,linux-final}.xml` зелёные.
Ruff clean. Эти тесты не закрывают все malformed/timeout/provider/error logs
или финальный chat HTTP request; A04 остаётся частично подтверждённым.

Свежий Q02 replay текущего кода на прежнем замороженном корпусе: **252 cases**,
без acceptance failures, потерь/добавлений источников и изменений порядка.
Использованы прежние 32 query vectors, LLM completion запрещён. Read-only
integrity audit после прогона подтвердил прежние SHA для **38 таблиц SQL**
и копии Qdrant. Исходные runtime хранилища не запрашивались и не менялись.
Артефакты: `tests/tmp/mail-q02-v20/{current-search,comparison,integrity}.json`.
Это regression на старом корпусе и фиксированных векторах, не latency benchmark.

Полный backend v20 session47379 завершился exit0: **2057 passed, 16 skipped,
29 warnings, 1391.39s**. Production backend/parser не менялись во время прогона.
Прогон начался до добавления privacy tests; их восемь случаев проверены отдельно.
Артефакты: `tests/tmp/mail-v20-final/backend-full.{log,xml}`.

U01: настоящий browser RED подтвердил `role=null`, `tabIndex=-1` у зоны
загрузки. Добавлены роль кнопки, Tab-фокус, Enter/Space с существующим busy
guard и видимая рамка фокуса. Независимый review выявил, что `aria-disabled`
охватывает вложенную модалку; диалог вынесен соседним элементом. Browser GREEN:
Tab от «Добавить тег» достигает зоны, обе клавиши дают filechooser events;
точный дубль s07.eml открывает отдельный enabled диалог, отмена Enter работает,
число документов осталось17. Raw DOM подтвердил отсутствие disabled/upload
ancestor у диалога. **188 frontend tests**, ESLint и окончательная Next build
успешны. `tests/tmp/mail-ui-v20/upload-keyboard*.{json,png}` и
`upload-duplicate-dialog.png`; журналы `frontend-*-final.log`.
Проверка не заменяет полную оценку accessibility, busy behavior и RU/EN hydration.

## Обновление 26.09: parser v20 и результаты проверок границ

Новая матрица `test_mail_budget_boundaries.py` содержит 78 случаев для EML,
MSG, DOCX, XLSX и PDF: глубина, число узлов, общий текстовый/байтовый бюджет,
Unicode code points и несовпадение объявленного/фактического размера PDF.
На запрещённой глубине Office/PDF сохраняли ограниченный оригинал и статус
`skipped_depth`, но не выдавали структурированное предупреждение. Три RED
теста подтвердили дефект; v20 добавляет `attachment_depth_exceeded`, сохраняя
порядок проверок размера/хранилища и запрет декодирования вложенного файла.

Полные parser suites последней версии: Windows **363 passed, 1 skipped**;
Linux **364 passed**. Выборочный backend: **59 passed, 10 skipped**;
пропуски относятся к POSIX и PostgreSQL migration cases, поэтому этот прогон
не заменяет прежнюю отдельную приёмку миграций на PostgreSQL. Ruff clean,
независимый review узкой дельты без замечаний.

Полный backend session45516 завершился: **2056 passed, 1 failed, 16 skipped,
29 warnings, 1376.63 s**. Сбой — гонка тестового стенда: родитель прочитал
пустой PID-файл между созданием и записью. Файл теперь публикуется атомарным
переименованием; соответствующий тест прошёл **три последовательных повтора**.
Этот полный прогон стартовал на v19 и не считается зелёной полной проверкой
v20. Свежий полный backend gate остаётся открытым.

XML и лог: `tests/tmp/mail-rag-v19-final/`. Матрица проверяет глубину через
входной параметр; физические цепочки покрыты отдельными v19 tests. Небольшие
границы узлов/байтов не заменяют проверку максимальной нагрузки или полный
adversarial corpus. Смысловой S07 остаётся открытым, GPT-4.1 применена.

После проверки точного процесса и 17 документов в `done` перезапущен только
изолированный browser backend. Startup подтверждает parser v20, GPT-4.1 для
чата и прежнюю Mistral Nemo для генерации; health `ok`. Авторизация Keycloak
сохранилась, страница S07 загрузилась с таблицей и выделением чанка2.
Снимок: `tests/tmp/mail-ui-v20/source-after-restart.png`. Это проверка чтения
после рестарта, не повтор смысловых ответов и не перегенерация старых документов.

## Обновление 26.09: GPT-4.1 применена, S07 остаётся открытым

По отдельному разрешению пользователя изменён только `LLM_CHAT_MODEL` в `.env`.
Backend синтетического стенда перезапущен; startup и реальные completion logs
подтверждают GPT-4.1. Генерация — прежняя Mistral Nemo, импорт включён.

Свежая браузерная загрузка четырёх synthetic EML и четыре новых чата с Keycloak
дали **3/4**. S03/S04/S06 верны, S07 вновь называет отправителя Петра автором
таблицы. Дополнительные повторы на GPT-4.1: isolated1/2, mixed0/2 по строгой
независимой оценке. Это не закрытая смысловая приёмка. Исторические 14/14 и
API4/4 ниже сохраняются как результат узкого benchmark, не общая гарантия.
Причина различия условий не сведена к одному фактору; см.
[обновлённый отчёт](2026-09-26-mail-chat-model-comparison.md).

Ссылка ответа на вложенную таблицу и точное выделение в чанке 2 проверены:
`tests/tmp/mail-ui-gpt41/s07-source.png`. Итог полного suite45516 приведён выше.

## Обновление 26.09: сравнение моделей по выбору пользователя

42 ответа (семь случаев × два повтора × три модели) оценены вручную двумя
рецензентами: GPT-4.1 **14/14**, GPT-4o mini **8/14**, Claude Sonnet 4.6
**12/14** по строгому критерию всех утверждений. Отдельный GPT-4.1 replay
через API — **4/4**. [Методика, ответы и ограничения](2026-09-26-mail-chat-model-comparison.md).
Пользователь отдельно разрешил применение: `.env` и перезапущенный backend
подтверждают GPT-4.1 для чата, генерация прежняя. UI-проверка выполняется.

Полный backend session41956 завершён exit1: **1 failed, 2043 passed,
16 skipped, 29 warnings, 1375.64 s**. Это снимок до окончательного P3 и D04/D05.
Сбой presence MSG+scan локализован в supervisor: empty poll → worker exit.
Повтор presence **12 passed**; окно гонки воспроизведено тремя spawn-тестами.
Исправление прошло **34 tests, 5 platform skips, 45.85 s** и независимый review.
Результат последующего suite45516 и ограничение его версии приведены выше.

## Обновление 26.09: D04/D05 и окончательный лимит контекста

Новый `test_mail_duplicate_admission.py`: **9 passed, 16.24 s**, один upstream
Starlette deprecation warning. Восемь случаев EML/MSG × exact/semantic ×
concurrent/trash подтверждают admission/consent и отсутствие ложного отказа
для корзины. Синхронизация через barrier после двух реальных parses и задержку
публикации подписи воспроизводит окно гонки; БД SQLite, один процесс, два потока.

D04 проходит upload и настоящий parser/SQL/FS/publication: одна synthetic MSG
загружена отдельно и внутри DOCX OLE. Сохраняются два doc_id, исходные root
filenames, отдельные концепты и точные bytes вложенного MSG. LLM/Qdrant test
заменены локальными doubles; внешних вызовов в этих тестах нет.

P3 лимита контекста закрыт: `limit_context` после фильтрации считает реальные
query markers и refs. **94 связанные tests passed**, независимые probes
подтвердили cap32000/3000 и корректные refs после удаления первого блока.
Смысловая проверка вторым запросом той же модели не исправила S06/S07 и в
рабочий чат не интегрирована. Архитектурный вопрос о следующем подходе задан
пользователю; рабочая модель и backend-процесс не менялись.

Полный suite41956 пока жив (последний log59%). Он стартовал до этой новой
дельты, поэтому его итог не будет полной проверкой последней рабочей копии.
Подробности: [RAG provenance](2026-09-26-mail-rag-provenance.md).

## Обновление 26.09: v19 — RAG provenance и выявленные смысловые ошибки

Полный backend до RAG-дельты завершился: **2034 passed, 16 skipped,
29 warnings, 1351.50 s** (session12055 exit0). Исправлена передача проверенной
по SQL цепочки источников/отправителя/даты, добавлен исходный mail fragment
для атрибуции; **96 целевых tests passed**, Ruff clean. Новый полный suite
session41956 ещё выполняется; его итог не подменять предыдущим результатом.

Реальная модель на synthetic corpus правильно отвечает S03, но **S04/S06/S07
не прошли**: выдумывает отмену согласования и авторов unsigned replies/таблицы.
Сохранены исходные ответы и точные prompts до/после; не объявлять семантическую
готовность по зелёным unit tests. Reviewer закрыл P2 переполнения контекста,
остаётся P3 небольшого превышения из-за query-маркеров.
Подробности и воспроизведение: [почтовое происхождение в RAG](2026-09-26-mail-rag-provenance.md).
Текущий browser backend не перезапускался для этой дельты.

## Обновление 26.09: v19 — повторный разбор Windows/Linux и EX-адреса

Добавлен воспроизводимый `doc-parser/scripts/probe_mail_reparse.py` для P05.
Корпус из 15 файлов подготовлен один раз; SHA-256 его manifest:
`b33d443e0cf6d74bc5c7eb278c97ae046bf45a5adb915932465c097d8693677a`.
Включает шесть закреплённых public/synthetic MSG, HTML-only и alternative EML,
nested RFC822, DOCX OLE/table MSG, DOCX→MSG→XLSX, PDF→EML, parent/child CID,
письмо со сканом и письмо с XLSX. У каждого input записаны origin/license/hash.

Каждый input разобран дважды в разных tempdir на каждой ОС. Сравнение проверяет
всё дерево source IDs/parents/metadata/warnings, блоки и канонический Markdown,
ссылки и SHA-256 всех извлечённых файлов. Нормализуются только физический корень
saved_path и канонические переводы строк; логические значения не удаляются.
Каждая emitted Markdown attachments-ссылка указывает на реально сохранённый файл.
Результат: **15/15 same-host и 15/15 cross-platform equal**, `differing_files=[]`.
Linux выполнялся без сети, user1000, прежний fonts image, текущий parser read-only.

S05 дополнен реальным synthetic CFB с EX recipient и отсутствующим Sender:
сырой `/O=.../OU=.../CN=...` сохраняется, SMTP и автор не выдумываются; отсутствующая
или некорректная дата остаётся unknown. **11 metadata tests passed** на Windows
(0.36 s) и Linux (0.49 s). Первичная ошибка ожидания теста `date_raw` исправлена
на optional lookup: парсер намеренно опускает неизвестное поле; production-код
по этому тесту не менялся. В этом этапе нет изменений runtime-кода.

Артефакты: `tests/tmp/mail-reparse-v19/{windows.json,linux.json,metadata-windows.xml,metadata-linux.xml}`,
`corpus/manifest.json`, builder `build_corpus.py`. Scoped Ruff clean.
Полный backend v19 **session12055 ещё работает**; его итог будет дописан отдельно.
Нельзя считать этот verifier заменой полной backend/regression/load приёмки.

## Обновление 26.09: v19 — M11, S01/S02

Точная synthetic цепочка MSG → EML → MSG → XLSX выявила чтение bytes
запрещённого XLSX до отказа по глубине (RED1). Проверка в native MSG перенесена
перед чтением attachment properties/payload. На depth2 XLSX разбирается, на
depth3 остаётся явный skipped_depth/warning, родительский MSG сохранён.

Шесть EML/MSG × empty/scan/XLSX tests воспроизвели запуск LLM только из-за Subject
(RED6). Теперь наличие текста определяется отдельно по source, без mail-heading
и изображений. Канонический текст/metadata темы сохранены. Решение передаётся
финализации, чтобы даже длинная тема не скрывала no_text_layer. Скановый PDF
дополнительно выявил перезапись source.saved_path извлечённой картинкой (RED2):
оригинал теперь определяется только attachment marker. Новые resume cases
подтверждают восстановление после publication failure без повторной генерации.

Свежие результаты: Windows parser **283 passed, 1 skipped**, 6.86 s;
Linux **284 passed**, 4.56 s, no network/nonroot. Связанные backend tests:
**34 passed**, 33.11 s; отдельно расширенная presence/resume матрица
**12 passed**, 26.37 s. Scoped Ruff clean. Предыдущий targeted run имел
**2 failed, 64 passed** до исправления PDF saved_path; это исправлено и
перепроверено указанным прогоном 34 tests. Независимый scoped reviewer
подтвердил три исправления, без подтверждённых новых замечаний.

Полный backend suite запущен в session **12055**, лог
`tests/tmp/mail-v19/backend-full.log`, XML после завершения
`tests/tmp/mail-v19/backend-full.xml`. На момент записи процесс ещё работает;
его результат не заявлен успешным. Продолжать по тому же handle, не перезапускать
из-за отсутствия новых строк. Полный frontend в этом этапе не запускался
(frontend не менялся; последний прогон v18: 188 passed).

Browser/real LLM, изолированный профиль:

- subject-only-v19.eml `93e54907342e4295`: done/no_text_layer, 0 concepts.
- scan-only-v19.eml `eb1e2e6755c046b8`: done/no_text_layer, 0 concepts;
  source download scan.pdf совпадает с оригинальным PDF, а не картинкой.
- table-only-v19.eml `095786dd64cf489e`: done/problemNULL, 1 concept только
  из root/0 XLSX, содержит значение 18; оригинал XLSX сохранён побайтово.

Артефакты `tests/tmp/mail-ui-v19/{audit.py,audit.json,presence-comparison.png}`,
`tests/tmp/mail-v19/{parser-windows,parser-linux,backend-presence,backend-presence-resume}.xml`.
Только test backend oldchild32144/parent40876 перезапущен в launcher29140;
до рестарта все 10 документов done. Original stores не изменены, mail flag=true.
Общая цель остаётся активной; смысловые S03–S07 и другие открытые gates ещё нужны.

## Обновление 26.09: v18 — M03, локальные CID-картинки

Реализован отдельный CID registry для каждого EML/MSG. PNG/JPEG/GIF/WebP
проверяются Pillow после допуска по bytes/node/depth budget и сохраняются без
перекодирования. CID без filename поддержан; дубли внутри письма неоднозначны,
совпадения между родителем/детьми/соседями изолированы. SVG, повреждённый raster,
слишком большие размеры и missing CID не становятся inline image. Предупреждение
`cid_image_unavailable` локализовано RU/EN. Ссылки на картинки не перезаписывают
saved_path самого письма и не создают вторую запись артефакта.

Три замечания scoped review воспроизведены до правок: Content-ID на multipart
скрывал body (1 RED), escaped brackets в alt оставляли image-only текст для LLM
(1 RED), fallback caption мог стать Markdown rule/list (3 RED). Исправлены,
независимые повторные probes reviewer подтвердили результат. Короткий реальный
текст рядом с картинкой продолжает обрабатываться; image-only сохраняет оригинал,
каноническую ссылку и `no_text_layer` без вызова генератора.

Свежие проверки:

- Windows parser: **281 passed, 1 skipped**, 6.77 s; Linux: **282 passed**, 4.13 s,
  current parser read-only в прежнем fonts image, network none/user 1000.
- Backend: **103 passed, 5 skipped**, 100.57 s, session 86302 terminal 0;
  mail pipeline, pipeline integration, source storage/chunking/API, supervisor.
  Один Starlette/httpx deprecation warning. Это targeted suite, не полный backend.
- Frontend: **188 passed**, 435.73 ms; scoped Ruff clean.

Browser в изолированном контуре: `cid-images-v18.eml`, doc
`362ec942c83740f2`, `done`, `attachment_partial_result` из-за намеренно missing CID.
Четыре source, два canonical chunk, семь LLM concepts; parent red PNG показан
дважды (абзац + таблица), child MSG blue PNG отдельно при одинаковом CID.
DOM: три img, все local authenticated API URLs, complete=true, 260×90.
Source PNG bytes совпадают с ожидаемыми. Это не полный аудит сетевых запросов
и не доказательство смыслового качества семи LLM-концептов.

Артефакты: `tests/tmp/mail-v18/{parser-windows,parser-linux,backend-focused}.xml`,
`frontend-final.log`, `tests/tmp/mail-ui-v18/{audit.py,audit.json,dom-audit.json,cid-images.png}`.
Перезапущен только проверенный test backend: old child2572/parent31876 → launcher40876;
перед рестартом все 9 тестовых документов done. Original PG5432/Qdrant16333
не изменялись, локальный mail flag=true. M03 закрыт для указанных fixtures;
остальные открытые критерии перечислены в requirements audit. Общая цель не завершена.

## Обновление 26.09: v17 — M03, сохранение HTML-ссылок

Общий `_MailHTML` для EML/MSG терял href и пропускал буквальный Markdown в
HTML textnodes. Добавлены 30 end-to-end parser cases (реальный MIME и
синтетический CFB/MSG): ссылки в абзацах/таблицах, http/https/mailto allowlist,
unsafe/malformed targets, nested/unclosed anchors, literal image/raw-HTML
injection, скобки/пробелы/pipe в адресах, таблицы с буквальным pipe.
Первый RED **10 failed / 14 passed**; затем независимый review воспроизвёл
повторное entity decoding (`&copy;` менял текст и query). Дополнительный RED:
**4 failed**; правило/list-prefix RED: **2 failed**. Амперсанд теперь экранируется
на Markdown-слое, не percent-encoding query separator; префиксы обрабатываются
после объединения textnodes. Reviewer перепроверил Python→ReactMarkdown+remarkGfm
на шести probes: entities/query/list/rule/image/target — без оставшихся findings.

Свежие финальные прогоны v17:

- Windows parser **255 passed / 1 symlink skip**, 6.21 s.
- Linux parser **256 passed**, 4.60 s, current read-only bind,
  `--network none --user 1000`, прежний подготовленный fonts image.
- Backend mail pipeline/source chunking/source location/supervisor:
  **76 passed / 5 POSIX-only skips**, 58.54 s, session69841 terminal0.
- Scoped Ruff пройден. Frontend production code не менялся в этом этапе;
  renderer проверен независимыми probes и настоящим браузером.

Synthetic browser upload `html-links-v17.eml`, doc `6ec1638f569244cd`,
Keycloak + real LLM, isolated PG25432/Qdrant26333: done, problem NULL, parser v17,
один canonical chunk. Read-only SQL audit подтвердил сохранённое экранирование.
Полный текст в UI: web/mailto href верны, query содержит буквальное `&copy;`,
DOM не содержит img/hr/ul/ol в main; запрещённая схема стала plain label.
GFM делает буквальный URL в тексте кликабельным, но картинку не создаёт.
Ссылки при проверке не открывались. Это не полный сетевой аудит браузера A01.
Логи отмечают JSON recovery у LLM; отсутствие problem проверено отдельно в БД,
полнота смысловой генерации этим тестом не утверждается.

Артефакты: `tests/tmp/mail-v17/{parser-windows,parser-linux,backend-focused}.xml`,
`tests/tmp/mail-ui-v17/{audit.json,dom-audit.json,html-links.png}`.
Обновлён owned synthetic backend: проверенный child25396/launcher7016 остановлен
при 8 done/no active; новый launcher31876. Worker читает свежий parser; сохранённые
chunks и rendered DOM подтверждают финальный entity fix. Исходный corpus не менялся.

**M03 остаётся частичным:** CID не реализован. Следующий шаг — per-message
CID registry, одинаковые IDs у разных писем, безымянные inline MIME части,
MSG AttachContentId, raster-only local rendering без remote fetch, прежние
лимиты/read-before-admission и корректные source/artifact references.

## Обновление 26.09: v16 — M13, неподдерживаемые контейнеры

Воспроизведено молчаливое сохранение legacy DOC/XLS (CFB), ZIP и сигнатурных
PST/OST/TNEF probes: **13 failed / 6 passed** до исправления; frontend warning
mapper **1 failed / 5 passed**. Общий `process_embedded` после supported dispatch,
admission и сохранения оригинала выдаёт `unsupported_attachment_format` и
`unsupported`. Признак определяется по bytes, поэтому переименование в `.bin`
не скрывает предупреждение. Это диагностика потенциально текстового контейнера,
а не обещание декодирования или проверки его исправности. Подробные ограничения
и ссылки на спецификации — в `docs/MAIL_FORMAT_SUPPORT.md`.

Новые проверки сохраняют родительский текст, соседнее письмо и оригинальные
байты; предупреждение адресовано только проблемному source. Изображение/opaque
binary не получает warning, supported EML под именем `.doc` извлекается,
лимиты размера/глубины имеют приоритет. Pipeline-тест проходит реальный parser
и publication в тестовой БД (генератор/векторный store заменены на детерминированные):
проверены `done + attachment_partial_result`, source status, bytes, bundle manifest
и отсутствие unsupported-source в канонических чанках/концептах.

Свежие результаты:

- Windows parser: **225 passed / 1 skipped**, 6.49 s; skip symlink WinError1314.
- Linux parser: **226 passed**, 3.79 s, existing fonts image, текущий parser
  read-only bind, `--network none --user 1000`.
- Backend mail pipeline/diagnostics/sources/supervisor: **64 passed / 5 skipped**,
  52.13 s; skips относятся к POSIX resource limits/process groups на Windows.
- Frontend `node --test`: **188 passed**, 443.52 ms. Первый прогон обнаружил
  два manifest drift после нового ключа; штатный export-ui-keys устранил их.
- Scoped Ruff и git diff check прошли. Scoped reviewer не нашёл блокирующих
  дефектов; независимо проверил renamed PST/TNEF, empty ZIP и PNG.

Браузер, реальная Keycloak-сессия и LLM на изолированном synthetic storage:
`unsupported-v16.eml`, doc `0765277f4e024c57`, **2 sources / 1 chunk / 1 concept**,
`done + attachment_partial_result`, parser v16. Дерево показывает локализованную
причину и `unsupported`; проверены RU/EN. ZIP скачан через UI, bytes совпадают
с MIME payload и сохранённым generation artifact. Read-only audit подтверждает
warning/status в БД и отсутствие ZIP-текста в canonical chunk.
Артефакты: `tests/tmp/mail-v16/{parser-windows,parser-linux,backend-focused}.xml`,
`frontend-final.log`, `tests/tmp/mail-ui-v16/{audit.json,unsupported-ru.png,unsupported-en.png}`.
Предыдущая проблема U01 остаётся: серверные заголовки после переключения языка
обновляются при reload; клиентские warning labels обновляются сразу.

Обновлён только owned synthetic backend (старый child32932/launcher13644,
новый launcher7016); перед restart verified 7 документов done и нет активных.
Исходная dev-база и её schema drift не изменялись. Local mail flag true.
Полный backend/load и clean-reproduction на окончательной версии остаются
отдельными незакрытыми gates. Следующий пункт M03: текущий `_MailHTML` теряет
href/img; traversal не выделяет безымянный inline CID как вложение.

## Обновление 26.09: R06 — миграции, rollback и dev schema drift

Добавлен `backend/tests/test_mail_migration.py`: реальные Alembic переходы
`d7e8f9a0b1c2 → head → d7e8f9a0b1c2 → head` на пустой и заполненной SQLite и
PostgreSQL. PostgreSQL использует исключительно новые БД `mail_migration_<uuid>`
на изолированном acceptance server 25432; имена сохранены в JUnit properties.
Base.create_all не подменяет миграции этих БД. Сверены:

- Старые DOCX/XLSX/PDF rows, canonical text, spans/hash, metadata и attachments
  не меняются после каждого перехода; source_id старых записей остаётся NULL.
- Полный Alembic schema diff в затронутых mail/publication tables пуст, включая
  типы и unique constraints; composite PK и parent index присутствуют.
- Реальные PK/FK отказывают дублю source и cross-document parent; cascade
  удаляет только дерево своего root document. На проверочном SQLite engine FK ON.
- File-less container source, повторные display names, Unicode JSON с 1000
  References и warnings перечитываются после закрытия connection pool.

`test_document_sources.py` дополнен отрицательными деревьями, cross-document
chunk/concept/attachment references и rollback всей замены. Один RED:
descendant перед ancestor с отсутствующим parent вызывал KeyError. В
`source_store._validate_tree` все прямые связи теперь валидируются до обхода
цепочек. RED **1 failed / 17 passed**; GREEN storage/publication/mail group:
**26 passed, 1 warning**, 22.06 s, session30544 terminal exit 0.

Добавлен адресный `scripts/migrate_mail_schema.py`: dry-run по умолчанию,
полный diff целевых таблиц и Alembic version; явный --apply разрешён CLI только
для development. Добавляются только перечисленные новые таблицы/nullable
колонки/индексы. Любой incompatible drift отказывает до DDL; существующие
определения/данные/version не переписываются. Проверены create_all drift,
dry-run без DDL, успешное применение, повтор no-op и отказ неправильного
source_id INTEGER без создания новых таблиц на обеих БД.

Финальная матрица миграций/repair: **10 passed**, 10 SQLite datetime adapter
deprecation warnings, 22.58 s, session64060 terminal exit 0;
`tests/tmp/mail-v15/migration-final.xml`. Related tree tests:
`tests/tmp/mail-v15/source-tree.xml`. Scoped Ruff прошёл после сортировки imports.
Read-only reviewer подтвердил fix (6/6 перестановок invalid → ValueError,
6/6 valid → успех). Его замечание об ослабленном schema assertion устранено:
assert теперь проверяет весь diff выбранных таблиц. Новый dev script также
прошёл scoped review без подтверждённых дефектов.

Начальный глобальный schema diff обнаружил отдельное расхождение
`tags.merged_into_id` FK вне mail scope. Оно не исправлялось и не объявляется
закрытым; сравнение целевых таблиц не доказывает полного равенства всей БД моделям.

Read-only аудит настроенного оригинального dev PG подтвердил:
`alembic_version=f0a1b2c3d4e5`; отсутствуют `document_generations.publication_hash`
и `document_staging.generation_id`; nullable расходится у `created_at` в
document_chunks/okf_attachments, текущие NULL counts 0/0. `applied=false`.
Артефакт `tests/tmp/mail-v15/dev-schema-audit.json`, конкретная процедура в
[MAIL_IMPORT.md](../../MAIL_IMPORT.md). Live corpus не менялся, локальный flag true.
Downgrade tests проверяют схему и старые данные, не возможность запустить старый
binary поверх уже опубликованных новых generations; операционный rollback
по-прежнему требует проверенного backup/restore в новую БД/каталог.

## Обновление 26.09: v15 — пропуски XLSX/OLE и новая сквозная проверка

Составлена [постатейная сверка матрицы](2026-09-26-mail-requirements-audit.md).
Она отделяет доказанные сценарии от частичных проверок и остаточных ограничений.
Сверка M06/M09 выявила два реальных пропуска:

1. XLSX видел только `xl/embeddings/*.bin`. RED **5 failed / 8 passed**;
   теперь все non-directory entries проходят общий content-aware path,
   сохраняя admission до чтения. Проверены .eml/.msg/.xlsx/.BIN/без расширения.
2. OLE Package/Ole10Native не возвращал MSG/EML payload. RED **8 failed / 3 passed**;
   добавлено извлечение mail, настоящее имя `\1Ole10Native`, DWORD NativeDataSize
   вместо неверного 2-byte offset, bounded Packager fields. Unknown/truncated
   layout остаётся opaque, имена/команды из record не исполняются.

Контракт `mail-sources-v15`; ранее готовые документы автоматически не переписаны.
Тесты проверяют canonical text, source IDs, original payload bytes, malformed
outer/inner size и unterminated path. Результаты:

- Связанные parser tests **60 passed, 1 skipped**.
- Полный parser Windows **206 passed, 1 skipped** (5.97 s); Linux offline
  **207 passed** (3.77 s). `tests/tmp/mail-v15/parser-windows.xml`.
- Связанные backend **63 passed**, 41.71 s, session14452 terminal exit 0;
  `tests/tmp/mail-v15/backend-focused.xml`.
- Scoped Ruff/diff check passed. Read-only scoped reviewer не нашёл новых
  регрессий, независимый probe подтвердил bytes для 9 сочетаний
  EML/MSG/PDF × Package/native/Packager и отказ усечённых native records.
  Это не подтверждение всех разновидностей Office-экспорта.

Свежая браузерная проверка v15, реальный Keycloak/LLM и synthetic-only storage:
`embedded-mail-v15.xlsx`, doc_id `90c8a2456f3c4ba6`, SHA256
`8a58bf724014751cde374886981b44ee1962d057cae27982b81865c8a0c5a81e`.
XLSX содержит raw .msg с XLSX-вложением и Packager-wrapped EML. UI: **Готов,
4 концепта**, полное дерево из 4 источников. Read-only SQL подтвердил `problem=NULL`,
4 чанка, v15 всех sources, matching source IDs и валидные сохранённые spans/hash
всех 4 концептов. Wrapped EML: диапазон **29–72**, точный текст
`MAIL-V15 processing period is fifteen days.`; переход из концепта выделяет
этот текст в четвёртом чанке. Снимок проверен визуально, фрагмент виден в viewport.

Артефакты: `tests/tmp/mail-ui-v15/{build_fixture.py,source-audit.json,source-tree-ru.png,wrapped-mail-highlight.png}`.
Backend launcher13644, logs в том же каталоге; health всех 5 dependencies ok.
Оригинальный корпус/Keycloak не менялись, локальный mail flag true.
Остались пункты матрицы, в первую очередь R06 миграции/rollback и недоказанные
варианты. Полная готовность плана и production readiness не объявляются.

## Обновление 26.09: v14 — допуск XLSX/PDF и браузер RU/EN

В XLSX/PDF воспроизведено чтение всех восьми attachment payloads при лимите
двух узлов. Теперь обход ленивый, admission предшествует чтению; исчерпание
числа узлов создаёт один remainder source. Нулевой byte budget не читает PDF
payload даже без `/Size`. Повреждённый CRC XLSX member или `/EF` PDF attachment
получает собственный `skipped_parse`, не уничтожая родительский текст и исправный
sibling. Инфраструктурные OSError/MemoryError не маскируются под плохое вложение.
Контракт — `mail-sources-v14`.

- Регрессии до исправлений: eager reads — 2 failed; PDF без Size — 1 failed;
  повреждённый container child — 2 failed. Финальные container tests: **8 passed**.
- Полный parser: Windows **190 passed, 1 skipped**; Linux **191 passed**,
  offline container с текущими исходниками read-only. Windows JUnit:
  `tests/tmp/mail-v14/parser-windows.xml`.
- Связанные backend tests после последних изменений: **63 passed**, 42.21 s,
  `tests/tmp/mail-v14/backend-focused.xml`. Полный backend 2003/11 и coverage
  89.29% ниже относятся к v12; полного повторного v14 прогона не было.
- Scoped Ruff и diff whitespace check passed. Read-only scoped reviewer
  подтвердил закрытие missing-Size/CRC findings; это не review всей рабочей копии.
- Frontend: **188 passed**, production build exit 0; ESLint 0 errors,
  8 warnings (hooks в DocumentList и locale exports), не warning-free.

Блокировка браузерной проверки снята после ручной проверки пользователя.
Авторизованный `demo.admin`, изолированный профиль «Синтетическая приёмка писем»:
загружены synthetic `warnings.eml` (cdc3bc7c52e147de) и `rtf-warning.eml`
(ed0c946c69cd494b). Второй содержит pinned synthetic RTF-only MSG и воспроизводит
реальное предупреждение. Первый подтверждает unknown date, но его opaque
`broken.eml` имеет статус Saved и НЕ доказывает предупреждение о parse error.

Проверены EN/RU reason неподдерживаемого RTF, unknown date, сохранённое дочернее
вложение и download links. Исправлено найденное русское `problem_message` в EN
списке: badge использует локализованный problem code с безопасным unknown fallback.
Снимки `tests/tmp/mail-ui-v14/{problem-badge-en,source-warning-en,source-warning-ru}.png`.
Остаточное UI замечание: при переключении языка серверные заголовки этой страницы
обновляются после reload, тогда как client labels переключаются сразу.

Backend v14: `tests/tmp/mail-ui-v14/logs`, health=ok. Оригинальный корпус и
Keycloak не менялись; локальный mail flag true. Q01 v12 и Q02 v10 ниже остаются
версионированными доказательствами, не новым load/regression прогоном v14.
Полная готовность плана не объявляется: сверка матрицы ведётся отдельно.

## Обновление 26.09: v13 — ограничение OLE и завершение backend suite

Исправлен подтверждённый ниже DOCX node-cap defect. Допуск выполняется до
relationship/blob resolver; допущенный объект не списывает узел повторно.
Ленивый обход прекращается после одного remainder source с `skipped_count`
и `attachment_count_exceeded`, включая последующие body/header/footer.
Текст после вложений сохраняется. Контракт — `mail-sources-v13`.

- RED internal/external: 2 failed / 6 passed; GREEN: **8 passed**.
- Полный parser Windows: **182 passed, 1 skipped**; Linux offline read-only
  container: **183 passed**. Scoped Ruff и diff whitespace check passed.
- Backend v13 pipeline/parser-version/source-chunking/source-location:
  **63 passed**. Первый запуск содержал ошибочное имя test_parser_version.py,
  тесты не выполнялись; затем использованы реальные два parser-version файла.
- Полный backend v12 (до узкого parser cap fix), session56174 завершён exit 0:
  **2003 passed, 11 skipped, 9 warnings**, **89.29%** coverage (порог 85%),
  1389.30 s. Артефакты `tests/tmp/mail-v12/backend-{coverage.json,junit.xml}`.
  Полный backend после v13 повторно не запускался; отдельно выполнены 63
  связанных теста выше, плюс полные parser suites обеих ОС.
- Read-only повторная проверка `/root/review_mail_v12`: 107 linked OLE с
  body/header/footer, max_nodes=2 → 2 resolver calls, 4 sources, один remainder,
  сохранный parent text. Finding закрыт; это scoped review исправления.

Изолированный HTTP backend обновлён до v13, logs `tests/tmp/mail-ui-v13/logs`,
health=ok для LLM/Ollama/Qdrant/DB/PDF. Keycloak и оригинальный корпус не менялись.
Браузер подтвердил `demo.admin`, но взаимодействие с частью вкладок/кнопок
остаётся нестабильным: CDP timeout либо действие без изменения UI. Новая
генерация через обходной API не запускалась. Локальный mail flag остаётся true.

## Обновление 26.09: v12 — служебные маркеры и повторная нагрузка

Браузерный reference-концепт из имени вложения воспроизведён на границе
parser → chunking: `Block(type="attachment", meta.attachment=True)` попадал
в canonical chunk как «Вложение: …» и далее в LLM. `indexable_blocks` теперь
исключает только эти структурные административные маркеры. Дерево источников,
метаданные, оригинальные bytes и download не меняются. Обычные абзацы с тем же
словом и legacy attachment blocks с извлечённым текстом сохраняются.
Изменение canonical layout отмечено `mail-sources-v12`, чтобы не смешивать
новые чанки со старыми checkpoint/spans. Старые готовые документы автоматически
не переписываются; для удаления прежних служебных концептов нужен regenerate.

RED: 2 новых chunking tests failed / 2 passed. GREEN: **69 related backend
tests passed**, затем дополненная pipeline-проверка canonical text и сохранного
attachment/source manifest — **1 passed**. Полный Windows parser: **180 passed,
1 skipped**. Добавлена проверка двух одновременно живых spawned workers:
третий вызов получает Busy, после завершения нет дочерних процессов и оба slot
снова доступны; Windows **1 passed**. Linux source-chunking + supervisor:
**20 passed, 8 Windows-only skipped**, сеть контейнера отключена, текущие
исходники примонтированы read-only. Первый Linux запуск упал на отсутствующем
каталоге basetemp до выполнения тестов; исправлен запуск с `/tmp/mail-v12-tests`.

Текущая нагрузка — `tests/tmp/mail-load-v12/load-report.json`, session16949
завершилась exit 0, **phase=passed, все 9 checks=true**. Те же 100 inputs,
86 590 043 bytes и manifest SHA256
`bd2d2e70d9aaeb58fa4bdac6d30ceb34a5f28def198d29654836598ba4bb5e94`.
Изолированные PG/Qdrant `mail_load_v12`, ASGI, fake LLM/embeddings:

- Load elapsed 281.72 s; ordinary parse p95 **0.38295 s**, max 0.40979 s,
  140 observations. Все parser calls ≤120 s.
- Максимальный sampled worker RSS **81.18 MiB**, 1891 samples.
- Search before p95 0.05286 s / during 0.05141 s (1149 samples), ratio **0.97269**.
- DOCX/PDF off/on p95 ratios **1.16498 / 0.98229**, оба ≤1.2.
- Нет HTTP/search errors, orphan workers и потерянных slots.
- Отдельный read-only `index-audit.json`: **101 docs, 131 sources/chunks/concepts,
  262 points**, exact identity/ownership match, все done/problem=NULL,
  active generation без незакрытых candidates.

Сверка задачи 10 выявила отсутствовавшие явные причины warnings и неизвестную
дату. UI теперь показывает локализованные причины из allowlist (без raw detail),
unknown warning — безопасную общую подпись, отсутствующая/невалидная дата письма
— «Дата неизвестна» / “Date unknown”. RED 2 UI tests, GREEN: **188 frontend
tests**, scoped ESLint и Next production build passed; UI manifests 945 keys.
Браузерная проверка этих новых подписей ещё не подтверждена.

Приёмочный runtime успешно запущен с v12: logs
`tests/tmp/mail-ui-v12/ready/logs`, health=ok, включая LLM/DB/Qdrant.
Предыдущие попытки запуска сохранены: занятый порт; ограниченный network token;
ошибка Windows Stop-Process. После проверки PID точечный taskkill завершил только
наш тестовый backend, новый helper запущен с разрешённым network access.
В новой вкладке Keycloak session сохранилась (`demo.admin`). Нативное подтверждение
regenerate не доступно browser automation (CDP timeout, getJsDialog undefined);
пользователю направлен вопрос о видимом окне. Повторную генерацию через обходной
API не запускали, чтобы не дублировать возможное подтверждение пользователя.

M10 v12 завершён: `tests/tmp/mail-chain-v12/report.json`, session83667 exit 0,
phase=passed, все 6 checks=true. Синтетическая цепочка DOCX → MSG → XLSX:
дерево источников, SHA256 скачанных оригиналов, canonical spans, поиск,
ответ реальной LLM с цитатой spreadsheet и export проверены. Это ASGI TestClient
с simulation auth, fake/8D embeddings и BM25, а не браузерная проверка SSO.

Независимое read-only review `/root/review_mail_v12` и отдельный RED выявили
оставшийся дефект DOCX node cap: при max_nodes=2 восемь OLE-вхождений дают
8 вызовов resolver вместо 2; создаются отдельные skipped_count nodes для
каждого остатка. Варианты internal/external: **2 failed, 6 passed**.
Связи и байты нужно допускать до resolver, а остаток обозначать одним маркером.
До исправления этого случая gate лимитов не закрыт.

Текущие незавершённые проверки: полный backend coverage session56174
(не запускать повторно по timeout наблюдения), исправление DOCX cap,
браузерная проверка новых подписей и итоговая сверка матрицы требований.

## Обновление 26.09: DOCX v11 и авторизованная браузерная проверка

Закрыт обнаруженный пробел M07/M12: повторный обход вложенных таблиц/textbox
мог извлекать один XML-объект дважды, а OLE в header/footer не извлекались.
Теперь учитываются уникальные XML-вхождения (разные вхождения одного relationship
сохраняются), собственные relationships частей header/footer и положение объекта.
Linked OLE получает явный unsupported marker без сетевого обращения и выдуманных
почтовых метаданных. Контракт парсера повышен до `mail-sources-v11`.

Регрессии `test_docx_mail_locations.py`: RED 5 failed / 1 passed, затем 6 passed;
последняя проверка после правки имени константы — **6 passed, 0.65 s**.
Полный parser: Windows **180 passed / 1 skipped**, Linux **181 passed**.
Связанные backend-тесты mail pipeline/parser version/sources/chunking:
**23 passed**. Полный frontend: **186 passed**; `next build` — exit 0.
Scoped Ruff E4/E7/E9/F/I — passed; старые broad-except замечания DOCX-парсера
этой проверкой не объявляются исправленными. Полный backend v10 и Q01 v10 ниже
не выдаются за повторный прогон на v11.

Пользователь вошёл через настоящий Keycloak как `demo.admin`. В браузере
подтверждён профиль «Синтетическая приёмка писем»: изолированные PG/Qdrant
`mail_http_acceptance`, fake/8D embeddings, настроенный LLM. Приёмочный backend
контролируемо перезапущен с v11; SSO сохранился. Локальный mail flag включён.

- EML `f19dc6a435ab42ee`: клавиатурный переход из концепта сертификата к полному
  тексту открыл чанк 2; DOM `.source-highlight` и screenshot подтверждают
  подсветку нужного абзаца в viewport (top 682.42, bottom 704.11, height 932).
  Это короткий документ; тест дальней автоматической прокрутки им не заменяется.
- Через UI скачан `certificate.eml`: SHA256 совпадает с сохранённым вложением,
  `e61e5fcb9243c7c50c51234eb20d301fcf3af429739e090883eb4924fd6449b3`.
- Через UI загружен синтетический `header-nested.docx`, doc
  `69cbdfe074634c2a`, done, 6 concepts, 3 chunks. Дерево содержит root и ровно
  два MSG: `root/0` «В таблице», `root/1` «В верхнем колонтитуле».
  Подписи проверены также на EN после reload: “In a table”, “In the header”.
  Русский язык восстановлен. Console errors на проверенных страницах отсутствуют.
- Публичный закреплённый `nested-rtf.msg` загружен через UI, doc
  `ed279126dd3b4e39`, done, 2 concepts. Для substorage `root/0` показано
  «Скачать контейнер». Скачанный MSG побайтно соответствует fixture по SHA256
  `ee87b46667a0f262b3f119967a5854610bd197508067c51b53fb7ff0abfb8962`.
- В чате задан синтетический вопрос о сертификате MAIL-ACCEPT-42. Настроенный
  LLM ответил «Keycloak», ответственный «Иван Тестовый», с кликабельной `[1]`.
  Цитата ведёт к концепту `bc634d8b74034f75/sertifikat-mail-accept-42`, затем
  «Показать место в документе» — к корректному абзацу чанка 2. DOM/screenshot
  подтверждают видимую подсветку (top 703.42, bottom 725.11 при height 932).
  Это проверка пользовательской цепочки, не оценка качества dense-поиска:
  приёмочный профиль использует fake embeddings.

Screenshots и синтетический DOCX: `tests/tmp/mail-ui-v11` (локальные артефакты).
Оригинальный пользовательский MSG в этих загрузках не использовался.
В результате реального LLM на коротком DOCX также появился reference-концепт
из маркера имени вложения; качество исключения таких служебных маркеров требует
отдельной оценки и не считается доказанным этим UI-прогоном.

## Обновление 26.09: Q02 на копии реального корпуса и проверка vazhno

Старые native PostgreSQL17:5432 и Qdrant:16333 были остановлены; запущены
штатными scripts/start-postgres.ps1 и start-qdrant.ps1. PG10:5433 не затронут.
Ollama bge-m3 отдельно запущен на свободном 12400 через скрытый background
helper. Backend/Frontend/Keycloak не перезапускались, mail flag не менялся.

В `tests/tmp/mail-q02-v10` создан локальный игнорируемый snapshot: pg_dump →
отдельная БД `mail_q02_v10` на acceptance PostgreSQL25432 и scroll/upsert →
одноимённая коллекция Qdrant26333. Это 26 docs, 132 chunks, 7244 concepts,
7376 points. Исходные документы/векторы не перерабатывались; точные payload и
векторы копии сверены с оригиналом. Snapshot/поисковые отчёты содержат частные
данные и остаются только локально, не включаются в Git/CI и не передавались LLM.

Baseline — backend из `git archive` commit
`51c453f6d7d3adc0e759aea9fd3dd3e48c47b485`; current — рабочая копия.
`probe_sources.py` в обеих версиях идентичен после нормализации CRLF/LF,
SHA256 `a8851e3afac6e35900ddfab20dc7cd018c4f5fa5df92198febd2bb2bde3bf902`.
Обе версии вызваны через этот probe с одной матрицей: **42 cases × 3 modes
× 2 API paths = 252**, glossary off. Включены все семь legacy queries.
32 уникальных вектора запросов вычислены локальным bge-m3 один раз и повторно
использованы для current. Это сравнение качества, не embedding latency.
Answer LLM calls запрещены в harness.

Baseline session55044 и current session22082: **terminal exit 0**, acceptance
failures=[] в обеих версиях. `comparison.json`: full_source_losses={},
group_losses={}, new_sources={}, final_only_losses={}, order_changes_only=[].
Отдельный verifier подтвердил **точное равенство упорядоченных идентичностей
raw и final источников**. В 48 вариантах выдача пуста в обеих версиях;
они не доказывают recall. Все 42 legacy-варианта имеют результаты.

`verify.py`, session45109 exit0, `verification.json phase=passed`:
все **38 SQL tables** оригинала и копии совпали; Qdrant оригинал и копия
после поиска сохранили исходный manifest hash
`35e7002a00f6bb406aef27c1b0eec6e4b6200dfe3f74e72ca4851ad8f5e7588b`.
Первый SQL verifier отличал строковые timezone представления Windows/Linux;
после `SET LOCAL TIME ZONE 'UTC'` в обеих read-only транзакциях они совпали.
Это изменение представления при проверке, не исправление данных. Начальная
подготовка также потребовала сериализации SparseVector через model_dump;
SQL dump/restore не повторялись, продолжена подтверждённо пустая test collection.

На скопированном реальном документе `1b9d6eeb57e94cb0` / `vazhno` текущая
source-location функция вернула **exact**, chunk0, spans **1895–2125** и
**2386–2687**. Quotes совпали с каноническим текстом. Эти spans уже находились
в оригинальной БД; в этом прогоне данные не редактировались. Браузерная видимость
и прокрутка этим не доказаны. Следующие gates: актуальная авторизованная
браузерная приёмка, аудит всей обязательной матрицы и финальное review.

## Обновление 26.09: Q01 завершён, SQL/Qdrant сверены

Session **68458 terminal exit 0**, `tests/tmp/mail-load-v10/load-report.json`:
`phase=passed`, все девять gates=true. Процесс завершён; повторно запускать
этот run или опрашивать его не требуется. 100 входов, 82.579 MiB, 280.30 s.
Обычные письма: 140 parse observations, p95 **0.3493 s**, max 0.3905 s.
Максимальный наблюдавшийся RSS worker **87.44 MiB** (1945 samples);
все parser calls уложились в 120 s. Orphan workers, потерянных slots,
HTTP/search errors не выявлено. Search p95: baseline 0.11848 s (100 samples),
при загрузке 0.05151 s (1148 samples), ratio 0.43475. DOCX flag-on/off p95
ratio 1.0642, PDF 0.9500; оба ниже 1.2. Это нагрузка с fake LLM/embeddings,
ASGI TestClient, настоящими PG/Qdrant и Windows parser supervisor.

Отдельная read-only сверка `backend/test_scripts/audit_mail_load.py --report
tests/tmp/mail-load-v10/load-report.json --output tests/tmp/mail-load-v10/index-audit.json`
завершилась exit 0: 101 documents с контрольным, 131 sources, 131 chunks,
131 concepts, 262 points. Все documents done/problem=NULL, parser v10,
у каждого документа active generation без candidate; точные множества
идентичностей SQL/Qdrant совпали, source ownership и generation_id согласованы.
Аудит не изменял SQL/Qdrant и не перезаписывал load-report.

Проверка пользовательских замечаний по source-location: свежие **109 backend +
33 frontend tests passed**, scoped Ruff/diff-check прошли. Три примера слабой
лексической привязки отклоняются, предположения имеют статус inferred и не
сохраняются как точные spans. Конкретный legacy `vazhno` в пользовательской
БД/браузере этим прогоном не проверен. Q02 старого корпуса и браузерная
Keycloak-приёмка остаются отдельными незакрытыми gates.

## Обновление 26.09: полный backend-прогон завершён, новый Q01 запущен

Session **71414 terminal exit 0**: **1998 passed, 8 skipped, 260 warnings**,
1316.91 s (21:56). Команда `python -m pytest -q --tb=short` с отдельным
`--basetemp=../tests/tmp/pytest-os-guards-full`. Warnings: Starlette/httpx,
Qdrant version discovery/local payload indexes, SQLite datetime adapter.
Новых backend production edits во время прогона не было; поздние дополнительные
OS-test cases проверены отдельным целевым прогоном, указанным ниже.

После завершения pytest запущен новый Q01, чтобы не смешивать нагрузку pytest
с измерениями latency. **Активный session 68458**, новая БД/коллекция
`mail_load_v10`, output `tests/tmp/mail-load-v10`, исходный пересобранный корпус
`tests/tmp/mail-load-reproduced-v10`. Schema migrations завершены; worker/HTTP
измерения выполняются. Результат Q01 пока не объявлен успешным. Продолжение
должно опрашивать именно session68458, а не повторять запуск.

## Обновление 26.09: воспроизводимость Q01 и локальный пользовательский MSG

В репозиторий вынесены `backend/test_scripts/build_mail_load_corpus.py` и
`probe_mail_load.py`: CLI принимает явные input/output/name, создаёт только новую
acceptance БД/коллекцию, не перезаписывает прежние данные. Неявная зависимость
от старого локального run_load.py устранена. CLI help, scoped Ruff/diff-check
прошли; новый load пока ожидает завершения полного backend suite, чтобы
параллельная pytest-нагрузка не искажала SLO.

Корпус пересобран в `tests/tmp/mail-load-reproduced-v10`: 100 входов,
86 590 043 bytes (82.579 MiB), 20 controls. Полный manifest равен исходному,
SHA256 `bd2d2e70d9aaeb58fa4bdac6d30ceb34a5f28def198d29654836598ba4bb5e94`.
Это доказательство воспроизводимости входов, не свежий результат Q01.

Предоставленный пользователем MSG проверен отдельно локальным supervisor с
v10 parser: 64 512 bytes, 3 source nodes, 4 blocks, 2885 text chars; два прохода
дали одинаковые logical IDs/text, replacement character отсутствует, warnings=[]
и оригинал неизменён. Внешних LLM-вызовов не было; письмо не загружалось в
тестовую БД/индекс. Временные извлечённые файлы удалены после проверки только
в проверенных каталогах workspace. Остался игнорируемый Git локальный отчёт
`tests/tmp/mail-private-v10/report.json` без текста/адресатов/темы письма.
Это smoke одного пользовательского файла, не новая публичная fixture-матрица.

Свежий browser inventory: вкладка3 browser1 по-прежнему Keycloak Sign in.
Пароли/credentials не запрашивались и UI не перенаправлялся; авторизованная
приёмка остаётся зависимой от пользовательского входа. Остальная работа идёт.

## Обновление 26.09: M10 DOCX → MSG → XLSX через API, поиск и реальный ответ

Добавлен воспроизводимый `backend/test_scripts/probe_mail_chain.py`. Команда:

```powershell
backend\.venv\Scripts\python.exe backend/test_scripts/probe_mail_chain.py --output tests/tmp/mail-chain-v10 --name mail_chain_v10
```

Для нового прогона нужны новые output/name: существующие результаты и БД
не перезаписываются. Используются только изолированные acceptance PostgreSQL
25432 / Qdrant 26333. Schema создана всеми миграциями до head; upload/preview
и pipeline идут через настоящий supervisor с текущим Job Object и v10 parser.
Авторизация simulation, HTTP transport ASGI TestClient, configured real LLM,
embeddings fake/8D; поисковый gate намеренно BM25, не dense quality.

Session **12363 terminal exit 0**, report `tests/tmp/mail-chain-v10/report.json`,
`phase=passed`, все шесть checks=true. Синтетический Document `20bdeaaab20d457b`,
никакие пользовательские письма не читались/не передавались LLM.

- Цепочка root → root/0 MSG → root/0/0 XLSX сохранена в API/SQL; чанки и
  концепты принадлежат своим source_id, parser_version=mail-sources-v10,
  нет parse warnings и document.problem.
- Поиск вернул XLSX со значением 12. Реальный ответ: «Срок, указанный в
  таблице для кода 3509, составляет 12 дней [2].» Ссылка [2] — root/0/0,
  заголовок «Таблица: Лимиты»; [1] в source list — письмо «Решение 3509».
- У табличного концепта exact source-location: offsets/quote сверены с
  каноническим SQL chunk. API download возвращает исходные DOCX, MSG и XLSX;
  ZIP export содержит всю source-chain и точные bytes вложений.
- MSG — pinned synthetic MIT fixture SHA256
  `2fa5cdebfe6526c943c788e2613c02e24fc855fae072a4fcfd50ed8dbc044229`;
  XLSX `bee5dbfcef449a6f87e530e39b81d410122425ae5d98da71533acea1c593e522`;
  DOCX этого прогона `1a8831d51dbb97b210885ff1379f811545d8814f1db57745f05847003af3fbdd`.

Это закрывает API/BM25/answer часть M10, не браузерный Keycloak flow и не dense
качество. Старый Q02 baseline найден в stage8, но его формат без manifest/corpus
snapshot; напрямую сравнивать с текущим schema3 probe нельзя. Свежая проверка
Windows listeners подтвердила только frontend16300/backend18000: native старые
PG5432/Qdrant16333/Ollama12400 не слушают. Они не запускались и не изменялись.
Q02 пока не подтверждён, требуется согласованное состояние старого корпуса.

Полный backend suite **71414 всё ещё жив**, последний проверенный прогресс ~46%
с последующими dots, без итогового результата. Не перезапускать по таймауту
наблюдения. User runtime/mail flag не менялись, commit/push не выполнялись.

## Обновление 26.09: OS-лимиты parser worker и fail-closed admission

Предыдущий этап v10 — прогресс (wire fidelity и ранние MIME limits). В этой
итерации закрыта подтверждённая кодом замена Windows RSS polling на Job Object.
Три начальных RED показали разрешённое выделение 400 MiB при лимите 256 MiB
без polling и живые subprocess после success/timeout. Первый hard-limit запуск
дополнительно выявил отказ Python/OpenBLAS ещё до worker entry при малых лимитах.
Теперь parent получает job memory notifications и различает такой отказ и crash.

`parser_limits.py`: типизированные WinAPI HANDLE/структуры, process/job memory
limits, kill-on-close, completion port. Parent не передаёт job handle в worker,
не разрешает breakaway. Event допуска освобождается только после назначения job.
Общий cleanup охватывает ошибки установки, запуск, нормальный ответ, malformed
response, crash и timeout. Slot/pipe/attempt storage освобождаются; существующая
директория артефактов не удаляется как побочный результат неудачного retry.

POSIX worker entry устанавливает RLIMIT_AS и отдельную session/process group;
ошибка установки protection больше не игнорируется. Новая ParserIsolationError
нормализуется в 503 `parser_isolation_unavailable` до document admission;
первый endpoint RED получил прежний ошибочный 422 `mail_parse_failed`.
Новый код локализован RU/EN, UI manifests экспортированы (926 keys).

Свежая проверка:

- Windows supervisor + admission: **35 passed, 5 skipped**, 39.69 s,
  1 Starlette warning. Session 91649 terminal exit 0. Включает реальную смерть
  supervisor: перед kill наблюдались живые worker и descendant, затем оба
  завершились через закрытие job handle. Прямой allocation test использует
  минимальный Python subprocess, изолируя enforcement от optional OpenBLAS bootstrap.
- Linux те же файлы до добавления Windows-only parent-death test:
  **32 passed, 7 skipped**, 59.28 s, 2 dependency deprecation warnings.
  Session 76844 terminal exit 0; network none, user1000, readonly current sources.
  Проверены RLIMIT_AS, отказ его установки и descendants success/timeout/crash.
- Frontend i18n + uploadFailures: **30 passed**; scoped Ruff/diff-check пройдены.
- **Полный Windows backend pytest ещё выполняется**, session **71414**,
  basetemp `tests/tmp/pytest-os-guards-full`. Его результат не объявлен зелёным;
  продолжение должно опросить тот же handle, а не запускать копию.

Ограничения: аварийная смерть POSIX-supervisor отдельно не квалифицирована
(обычный cleanup process group проверен); hard memory measures — committed
memory на Windows и address space на POSIX, не одинаковый RSS. Реальная
OOM/Job policy ошибка может наступить при bootstrap; default 1024 MiB прошёл
успешные парсинг/descendant проверки. Тесты не являются production deployment.
Далее дождаться полного suite, закрыть прочие L01 случаи/новую нагрузку,
M10 HTTP/search/answer, Q02 corpus, browser SSO и полный audit.

Первичные контракты:
[Job limits](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information),
[job assignment](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject),
[Python resource](https://docs.python.org/3/library/resource.html).

## Обновление 26.09: исходные RFC822 bytes и ранние MIME-лимиты (v10)

Завершён незаконченный переход от повторной сериализации `message/rfc822`
к диапазонам исходного wire buffer. `mime_wire.py` разбирает только заголовки
и multipart-контейнеры; вложенное письмо остаётся leaf до admission.
Перед границей MIME удаляется только принадлежащий delimiter перевод строки,
собственный завершающий перевод строки ребёнка сохраняется.

`test_mime_wire.py` использует вручную собранные bytes, а не сериализатор
как oracle: восемь вариантов LF/CRLF × наличие завершающей строки × 8bit/base64,
folded/long headers, Windows-1251 quoted-printable внутри ребёнка. Проверяются
точные скачиваемые bytes, дочерний source_id и отсутствие `as_bytes()`.
Проверены размер RFC822 до materialization и стандартный `multipart/digest`.

Ограничены заголовки (64 KiB), структурная глубина multipart (32) и тело (50 MiB
до декодирования). Общий node budget учитывает body/container; 1500 частей при
бюджете 2 разбирают только root/body/первое вложение, остаток получает один marker.
Размер обычных вложений и вложенных сообщений проверяется до transfer decode.
Повреждённый дочерний multipart оставляет parent и соседний файл;
root без opening/closing boundary отклоняется. Это несколько проверенных классов,
а не исчерпывающая MIME-fuzz/bomb квалификация.

Свежий RED выявил порядок root-header проверки и старое ожидание node-теста;
следующие два RED — отсутствие body prelimit и принятие closing-only multipart.
После исправлений:

- Windows: **174 passed, 1 skipped**, 4.13 s; skip — symlink privilege.
- Linux: **175 passed**, 3.08 s, текущий readonly parser, network none, user 1000.
- Backend: **57 passed**, 38.36 s, 7 warnings (Starlette deprecation и Qdrant
  version discovery в тестах); admission, pipeline, parser/staging versions,
  mail identity, parse diagnostics. Session 63449 завершена, exit 0.
- Scoped Ruff прошёл. Спецификация и таблица поддержки обновлены.

`mail-sources-v10` требует штатной проверки версии checkpoint; опубликованные
документы автоматически не перегенерируются. Текущий root buffer ещё читается
целиком после upload size admission. Жёсткий Windows Job Object/cleanup gate
остаётся открытым: проверенный supervisor использует polling RSS; POSIX установка
RLIMIT_AS пока может молча не сработать. Далее OS guards, M10 HTTP/search/answer,
Q02 старый корпус, браузерная приёмка и полный audit. Пользовательские сервисы,
данные и локальный mail flag не изменялись; production build/commit/push не было.

## Обновление 26.09: отложенное чтение MSG, MIME byte leaves и обязательная upload-проверка (v9)

Предыдущий этап v8 дал прогресс по RTF-only и общему пути вложений. Продолжение
проверило внутренние вызовы чтения, а не только итоговый `skipped` marker.

`doc-parser/tests/test_msg_limits.py`: пять первоначальных RED доказали чтение
запрещённого substorage, свойств после count limit, oversized declared stream,
следующего payload после исчерпания общего бюджета и oversized root property.
Теперь MSG хранит handles вложений до admission. Shared budget включает property
streams, metadata и payload; размер проверяется перед `openstream`, затем сверяется
фактическая длина. Восемь тестов также проверяют повреждённый child + здоровый
sibling, opaque bytes без output directory и неподдерживаемый attachment method
(последний тоже прошёл отдельный RED). Корневые malformed properties дают ошибку,
дочерние — конкретный warning/marker без отмены соседнего источника.

`test_mime_limits.py`: RED по count, depth, decoded size и remaining byte budget.
MIME byte leaves проверяются до `_attachment_payload`; стандартный base64 size
вычисляется без создания decoded buffer, malformed/другие transfer encodings
используют консервативную верхнюю границу. Depth-limited часть не декодируется
ради сохранения: доступ остаётся через контейнер. Node budget резервируется один
раз при общем рекурсивном проходе. `message/rfc822` serialization ещё отдельно
ограничить: этот этап не объявляет весь MIME gate закрытым.

`backend/tests/test_mail_upload_admission.py`: первоначально 10 RED/2 GREEN.
При `dedup_enabled=false` повреждённые EML/MSG действительно публиковались с 200.
Теперь корневой mail parse обязателен до admission, независимо от dedup; RTF-only,
encrypted/protected и unsupported class получают отдельный 422. Проверены пустая
таблица документов, отсутствие файлов/тегов/заданий, читаемый parent с unsupported
child и реальный supervisor для RTF root. Таймаут/RSS limit mapping проверен
инъекцией соответствующих supervisor exceptions. После рефакторинга отдельный
RED обнаружил нежелательный запуск similarity при dedup=false; исправлено,
fingerprint/preview markdown вычисляются только при включённой дедупликации.

Добавлены шесть стабильных API codes и RU/EN сообщения; exported UI manifest
содержит 925 ключей. Это проверка локализации/контракта, не браузерная приёмка.

Свежие результаты:

- Windows parser: **157 passed, 1 skipped**, 4.00 s (Windows symlink privilege).
- Linux parser: **158 passed**, 2.67 s, fonts image + текущий read-only parser,
  network none, user 1000:1000.
- Backend: первый объединённый прогон **103 passed**, 72.48 s, 7 warnings;
  после дополнительной защиты выключенной дедупликации и новых admission cases
  финальный прогон `test_mail_upload_admission`, `test_upload_similarity`,
  `test_error_codes`: **58 passed**, 47.12 s, 1 Starlette/httpx deprecation warning.
  Результаты пересекаются, складывать их как число уникальных тестов нельзя.
- Frontend i18n/uploadFailures/sourceTree: **33 passed**. Scoped Ruff и
  scoped `git diff --check` прошли.

Полный план не завершён. Следом: bounded RFC822 serialization и ранние MIME
body/header/tree limits, malformed/bomb cases и проверка жёстких OS limits;
затем M10 поиск/ответ и Q02 старый корпус, browser SSO и финальный audit.
В этой итерации нет нового live HTTP/search прогона, production build, рестарта
пользовательских сервисов или изменения локального mail flag.

## Обновление 26.09: настоящий RTF-only и ранний отказ в общем пути вложений (v8)

Для M05 добавлены два синтетических MIT CFB fixture без plain/HTML альтернативы:
`synthetic-rtf-only-compressed.msg` (4096 bytes, SHA-256
`1c279732030c2208f8a7d879b419a0f828510d52d93c8cd96b544b7cea313553`) и
`synthetic-rtf-only-uncompressed.msg` (4096 bytes, SHA-256
`7562a73a675d8a4c18253688f3fce284763caa837dd1c927072e8238872fb1cf`).
Генератор `tests/mail_fixtures.py`, manifest и тест проверяют неизменность bytes.
RTF создаётся MIT encoder `compressed-rtf`; это не частное письмо и не Outlook export.

RED: оба теста запрещали открытие RTF-потока `10090102`, но `MsOxMessage`
открывал его ещё до policy checks. Теперь root и substorage используют собственный
CFB reader; из зависимости остаётся справочник MAPI-свойств. RTF-поток не
читается. Root/child RTF-only имеют `unsupported_rtf_body`, доступные вложения
сохраняются точно. Backend не маскирует эту причину `no_concepts`/`no_text_layer`,
показывает способ получить plain/HTML-вариант, не создаёт fingerprint неполного
письма. Это проверенный явный unsupported, не поддержка RTF conversion.

Дополнительно native reader проверен на root FILETIME после 32-byte header и
отклонении обычного CFB без MAPI property stream. При прогоне публичного ANSI
fixture обнаружен `cp28591`: пустая `b''.decode()` не проверяла codec. Исправлено
через `codecs.lookup` и документированный alias ISO-8859-1. Три старых теста
metadata больше не подменяют удалённый `MsOxMessage`; проверяют тот же контракт
даты через `_parse_message_data`, а actual FILETIME проверяется бинарным fixture.

L01/L02 в общем `process_embedded`: RED доказал вызов `unwrap_ole` при глубине
3/4 и слишком большом исходном payload. Теперь эти отказы происходят до
OLE/ZIP inspection; допустимые raw bytes на границе глубины сохраняются в пределах
общего byte budget. Запрещённый уровень не передаётся рекурсивному парсеру.
Это не закрывает лимиты native MSG/MIME: их внутреннее чтение ещё требует
отдельного отказа до decode/traversal.

Свежая проверка текущего кода:

- Windows: `backend/.venv/Scripts/python -m pytest -q` из `doc-parser` —
  **143 passed, 1 skipped**, 3.93 s; skip требует Windows symlink privilege.
- Linux: `okf-mail-verify-tests:20260926-fonts`, текущий parser mounted read-only,
  `--network none --user 1000:1000`, `pytest -q` — **144 passed**, 2.56 s.
- Backend: `pytest --no-cov -q` для `test_parse_diagnostics`, `test_mail_identity`,
  `test_mail_pipeline`, `test_document_sources`, `test_pipeline_parser_version`,
  `test_staging_parser_version` — **49 passed**, 32.30 s, 6 предупреждений
  Qdrant version discovery в изолированных тестах. LLM/vector operations в
  pipeline tests подменены; это не новая HTTP/search приёмка.
- Scoped Ruff и `git diff --check` прошли. При добавлении pytest parameterization
  исправлены ошибки imports тестового harness; финальные прогоны terminal 0.

Parser contract `mail-sources-v8`: старые checkpoints должны проходить штатную
проверку версии. Полный план остаётся открыт: внутренние лимиты MSG/MIME,
M10 search/answer, Q02 historical corpus, browser SSO и финальный audit.
Отдельно надо сверить root unsupported на upload с требованием плана об отказе
до admission: сейчас preview собирает warnings, но не отклоняет такой root;
preview также зависит от `dedup_enabled`. Тесты parser/status этого не закрывают.
Локальный флаг импорта не менялся; production image не пересобирался.

## Обновление 26.09: бинарные вложения MSG и правильный child context (v7)

Новый синтетический CFB/MAPI fixture с Unicode subject и XLSX внутри:
`doc-parser/tests/fixtures/mail/synthetic-unicode-attachment.msg`, 9216 bytes,
SHA-256 `2fa5cdebfe6526c943c788e2613c02e24fc855fae072a4fcfd50ed8dbc044229`,
MIT, код генератора `doc-parser/tests/mail_fixtures.py`. Нет частных данных
и зависимости от установленного Outlook. Manifest теперь проверяет не только
hash/license, но и фактически полученные source IDs каждого pinned fixture.

Эта проверка обнаружила два воспроизводимых дефекта зависимости:

1. `msg_parser` декодирует AttachDataObject через PtypObject, удаляя NUL bytes.
   XLSX терял целостность ZIP и сохранялся без извлечения. Адаптер теперь читает
   binary stream `37010102` напрямую. Два теста DOCX → MSG → XLSX (обычный абзац
   и ячейка) проверяют значение 3509/12, source ownership и байтовое равенство
   обоих сохранённых вложений. До исправления таблица отсутствовала, после — есть.
2. Embedded message properties читались зависимостью от корня CFB. Отдельный
   синтетический parent/child с разными subject/body воспроизвёл подстановку
   PARENT BODY в ребёнка. Дочерние свойства и recipients теперь читаются по
   полному substorage path, с типом из stream suffix и fixed MAPI properties.
   Новый тест проверяет CHILD SUBJECT/BODY, child sender и binary grandchild.
   Старый тест `nested-rtf.msg` ошибочно ожидал текст родителя «Mail in mail.»
   у ребёнка. Независимое чтение child `1000001F` показало «This is a testmail.»;
   expectation исправлен, а backend pipeline проверяет именно этот child text в SQL.

Диагностика MSG также доведена до backend/UI: SMIME opaque / Content-Class
rpmsg.message → `protected_mail`; неподдерживаемые классы (calendar/contact/task)
→ `unsupported_mail_class`. Тело таких объектов не индексируется, оригинал
остаётся доступен. Clear-signed marker сохраняет доступное тело и
`signature_verified=false`. Источник с предупреждением больше не показывается
как «Извлечено», причина не маскируется `no_text_layer/no_concepts`, неполное
письмо не получает semantic fingerprint. Проверки классов используют синтетические
маркеры, а не реальные криптографические сообщения: полная IRM/S/MIME квалификация
не объявляется закрытой.

Свежая проверка конечного кода:

- Windows parser: **135 passed, 1 skipped**, 3.97s (symlink privilege).
- Linux parser: **136 passed**, 2.77s; network none, user1000, текущий parser
  read-only bind поверх существующего fonts test image. Production image не пересобран.
- Backend (`test_document_sources`, `test_mail_identity`, `test_parse_diagnostics`,
  `test_mail_pipeline`, `test_pipeline_parser_version`, `test_staging_parser_version`):
  **44 passed, 6 warnings**, 30.40s, exec82124 terminal exit0. Warnings — Qdrant
  version discovery в изолированных тестах. RED этап диагностики: 12 failed, 1 passed.
- Frontend source-tree/i18n: **32 passed**; UI manifests экспортированы (919 keys).

Версия повышена до `mail-sources-v7`; опубликованные пользовательские документы
не перегенерировались. M08 усилен фактической проверкой child content; M10 закрыт
на уровне parser, но поиск/ответ по полной цепочке ещё требуется проверить.

**Уточнение прежних доказательств M05:** pinned `rtf-simple-sent.msg` содержит
plain `1000001E` (150 bytes) и RTF `10090102` (275 bytes). Он не является RTF-only.
Прежние положительные отчёты по этому имени доказывали извлечение ANSI plain,
не RTF-only. Manifest и документация исправлены. Истинный RTF-only остаётся
открытым gate; дочерний источник без plain/HTML получает `unsupported_rtf_body`.

Также остаются открыты отказ до decode по общему budget (зависимость пока eager),
полная матрица, Q02 по старому корпусу и браузерная SSO-приёмка. Вкладка 3 по
свежему AX всё ещё показывает Keycloak login; пользовательский вход не выполнен.

## Обновление 26.09: исторический parser baseline и даты писем

`tests/tmp/mail-baseline/compare_parser.py` завершён, exec72836 exit0;
`parser-comparison.json`: passed=true. Baseline — parser из HEAD
`51c453f6d7d3adc0e759aea9fd3dd3e48c47b485`, извлечённый через git archive
в отдельный каталог. Одинаковые установленные dependencies, 10 DOCX + 10 PDF
из фиксированного load manifest, шесть чередующихся прогонов каждой версии.
На формат — 60 наблюдений до и после; прямой прогретый parser, без supervisor startup.
DOCX p95 42.503 → 39.671 ms (ratio 0.93336), PDF 11.341 → 10.792 ms
(ratio 0.95156). Ноль расхождений canonical text/hash и количества blocks.
Это закрывает историческое сравнение этих non-mail parse controls, но не
baseline качества поиска старого корпуса. Прогон предшествует mail-metadata v6.

Аудит spec §3 выявил потерю исходной даты и threading metadata. MSG provider
может вернуть RFC-date строкой: прежний `_iso_value` превращал её в null;
datetime с часовым поясом не приводился к UTC. Девять новых тестов
`doc-parser/tests/test_mail_metadata.py` сначала упали и затем прошли:
RFC date, unknown-zone -0000, invalid date, MAPI datetime, timezone conversion,
поля In-Reply-To/References/MessageClass и дата вложенного MSG.
Читаются только разрешённые поля; Bcc/Received не попадают в metadata.
Версия `mail-sources-v6` исключает смешивание старых незавершённых checkpoints
с новой семантикой. Published данные автоматически не перегенерируются.

Windows parser: **124 passed, 1 skipped**, 3.94s (symlink privilege).
Linux parser: **125 passed**, 2.29s, существующий fonts test image,
текущий parser read-only bind, network none, user1000. Это не новая сборка
production image. Scoped Ruff прошёл. В тесте MSG/EML fingerprint добавлена
реальная дата pinned MSG в эквивалентный EML: прежняя fixture без Date
совпадала только из-за потери даты MSG.

Связанные backend tests: **34 passed, 1 skipped, 6 warnings**, 29.66s,
exec70968 terminal exit0: `test_mail_identity`, `test_mail_pipeline`,
`test_pipeline_parser_version`, `test_staging_parser_version`,
`test_parser_supervisor`, `test_document_sources` (`pytest -q --no-cov`).
Skip — POSIX resource limit на Windows; warnings — Qdrant version discovery
в изолированных тестах. Production coverage/build после v6 не переснимались.
Новая браузерная вкладка 3 открыта на форме Keycloak; вход ещё не выполнен.

## Обновление 26.09: 100 входов под нагрузкой и MSG recipient fallback

Windows ASGI load-run завершён: `tests/tmp/mail-load/load-report.json`,
`phase=passed`, все перечисленные checks=true, exec61826 terminal exit0.
Изолированные DB/collection `mail_load_acceptance`; fake LLM/embeddings,
реальные supervised parser workers, PostgreSQL17 и Qdrant1.19. HTTP загрузки
последовательны до завершения pipeline; параллельно постоянно выполняется
BM25-поиск по фиксированному контрольному документу/тегу. Этот профиль не
заявляет результат для произвольного числа одновременных upload-клиентов.

| Метрика | Результат |
|---|---|
| Corpus | 100 входов, 82.579 MiB, 70 ordinary EML / 10 attachment-only / 20 DOCX с mail |
| Продолжительность HTTP load | 253.692s |
| Parse обычного EML, preview+pipeline | 140 наблюдений, p95 **0.378s**, max 0.470s |
| RSS worker | максимум наблюдений **105.008 MiB**, 1049 samples, опрос supervisor около 50ms |
| Search baseline | 100 запросов, p95 **52.634ms** |
| Search during load | 1037 запросов, p95 **51.363ms**, max 221.093ms; ratio **0.97585** |
| DOCX controls, mail flag off/on | p95 449.703 / 435.054ms, ratio 0.96742 |
| PDF controls, mail flag off/on | p95 526.346 / 511.015ms, ratio 0.97087 |
| Дубли | 7 повторных файлов отклонены `409 duplicate`, даже с allow_similar |
| Ошибки / ресурсы | 0 неожиданных HTTP errors, 0 orphan multiprocessing workers, все parser slots возвращены |
| SQL/Qdrant audit | с 1 control: 101 done document, 131 source/chunk/concept, **262 points** |

Все факты fixture найдены в chunks/concepts; source ownership и active generation
проверены для всех points отдельным read-only `tests/tmp/mail-load/audit.py`.
Worker time/memory caps остаются 120s/1024MiB; RSS — sampled maximum, не обещание
измерения пика между отсчётами. Control flag off/on — сравнение в одной версии,
а не исторический parser binary или оценка качества поиска старого корпуса.
Scope query: BM25, один фиксированный запрос/тег; dense и реальные embeddings
этим benchmark не измерялись. Исходные corpus hashes проверены до запуска.
Первая попытка harness остановилась до baseline/load из-за отсутствующего
`SearchHit.doc_id`: исправлена проверка документного сегмента `filepath`.
Control timings сохранены, данные/БД не пересоздавались. Логи: `run.log`,
`continue.log`, `audit.log`. Runtime source был v4; последующая правка ниже
затрагивает только MSG recipient metadata и parser version, не входы EML/DOCX этого прогона.

При сверке A04 обнаружена отдельная ошибка: вложенный MSG без DisplayTo
заполнял To **всеми** записями recipients, включая Bcc/ReplyTo/unknown.
Два регрессионных теста на embedded-message path сначала упали; исправление
принимает только явные To/Cc (числовые роли 1/2 или строки TO/CC библиотеки),
не угадывает неизвестную роль. Соответствие числовых ролей проверено по
[Microsoft MS-OXOMSG](https://learn.microsoft.com/en-us/openspecs/exchange_server_protocols/ms-oxomsg/144ae256-8cf2-45a1-a297-221b44f68cfe).
Parser version повышена до `mail-sources-v5`. Существующие опубликованные
metadata этим не переписываются; для их исправления нужен штатный reparse,
никакого автоматического bulk regenerate не выполнялось.

После изменения:

- Полный Windows parser: **115 passed, 1 skipped** (платформенная symlink-ветка), 3.90s.
- Полный Linux parser: **116 passed**, 2.48s, network none, user1000,
  текущий doc-parser read-only поверх прежнего fonts test image; `msg-linux.log`.
- Backend mail/pipeline/checkpoint/parser/source/probe: **37 passed, 1 skipped**
  (POSIX limit на Windows), 36.11s; `tests/tmp/mail-http/msg-backend.log`.
- Scoped Ruff и diff whitespace check прошли. Полный coverage89.42% ниже
  относится к предыдущему source snapshot; после MSG-правки выполнены эти
  целевые проверки, а не новый полный coverage-прогон.
- Parser-only profile теперь имеет фиксированные MIME boundaries;
  `test_mail_probe.py` доказал ошибку случайных hashes и прошёл после исправления.

Открыты итоговая сверка всей обязательной матрицы, свежая браузерная приёмка,
исторический non-mail/search regression baseline и ограничения редких MSG.
Все запущенные здесь процессы терминальны. Пользовательский local mail flag
остаётся true; исходный план уточнён под это решение и принятую общую видимость.

## Обновление 26.09: реальная LLM, HTTP admission и новый процесс

Изолированный прогон завершён (`tests/tmp/mail-http/report.json`, `phase=passed`).
Новая БД `mail_http_acceptance` на тестовом PostgreSQL17 `127.0.0.1:25432`,
одноимённая коллекция Qdrant1.19 `127.0.0.1:26333`, отдельный data directory.
Приложение запускалось через ASGI TestClient с настоящим lifespan и simulation-editor.
Это проверка backend HTTP-контракта; она не заменяет браузерный SSO/Next proxy.
Эмбеддинги детерминированные fake/8D. Для генерации использована настроенная
`openrouter/mistralai/mistral-nemo`, для ответа — `openrouter/openai/gpt-4o-mini`.
Внешнему провайдеру отправлялись только созданные скриптом синтетические данные;
ключ прочитан в память из локальной конфигурации, не записан в отчёт.

Подтверждено:

- Anonymous получает 401; simulation-editor загружает EML с вложенным EML.
- Близкий файл с другим transport header получает `409 similar_document`.
  До подтверждения не меняются rows/uploads и счётчик LLM. Побайтовый дубль
  получает `409 duplicate` даже с `allow_similar=true`.
- BM25 находит факт; ответ реальной модели содержит **12 календарных дней**,
  начало отсчёта и ответственного, с ссылкой `[1]` на исходный документ.
- Source-location ranges проверены по каноническому тексту, скачивание root
  и child совпадает с сохранёнными байтами, child содержит `nosniff`.
- Подтверждённая похожая загрузка реально разобрана/сгенерирована/индексирована.
  В тесте внедрён RuntimeError перед SQL publication: документ `failed`,
  кандидат остаётся `ready`. Новый Python-процесс возобновил его через HTTP
  `/resume` с запрещёнными LLM и parser функциями; опубликован тот же кандидат.
- Trash скрывает source list/download (404); восстановление при активном
  похожем документе требует подтверждения (409), `force=true` возвращает
  прежнее поколение и те же координаты. OKF ZIP имеет доступные относительные links.
- Финальный read-only аудит: 2 документа, 4 sources, 4 chunks, 4 concepts,
  8 Qdrant points. Все points относятся к активным поколениям; chunk/source
  ownership совпадает. `sender@example.test` и `approver@example.test` не смешаны.

Команды: `backend/.venv/Scripts/python.exe -u tests/tmp/mail-http/probe.py generate`,
затем `continue`, `capture-ready`, `restart`; финальная сверка —
`tests/tmp/mail-http/audit.py`. Все процессы терминальны, restart/audit exit0.
Два уточнения проверочного скрипта сохранены в истории: source.saved_path
относителен uploads root документа; injected RuntimeError даёт `failed`, а не
`paused`. Повторная генерация первого документа не запускалась.
Тайминги первого поколения не сохранены; у второго два реальных запроса —
2.536/2.196s, ответ чата — 2.045s (`llm-calls.json`). Это отдельные наблюдения,
не статистический SLO внешнего провайдера.

Следующий gate: фиксированный корпус 100 входов / **86 590 043 bytes (82.579 MiB)**:
70 обычных EML, 10 attachment-only EML с DOCX, 20 DOCX со встроенными EML
(10 в ячейках), плюс 20 DOCX/PDF controls. Manifest hash:
`bd2d2e70d9aaeb58fa4bdac6d30ceb34a5f28def198d29654836598ba4bb5e94`.
Файлы/хэши и SLO до измерений зафиксированы в `tests/tmp/mail-load/manifest.json`.
Результат нагрузочного прогона следует оценивать отдельно, по `load-report.json`;
наличие корпуса не считается прохождением SLO.

## Обновление 26.09: полный coverage и Windows → Linux restore

Полный Windows backend snapshot завершён: **1956 passed, 7 skipped, 255 warnings**,
1458.50s, exit 0 (`tests/tmp/repair-full.log`, exec 37168 terminal).
`--cov=app --cov-fail-under=85`: **89.42%**, 13413/15000 строк;
JSON `tests/tmp/repair-full-coverage.json`. Пять opt-in PostgreSQL тестов,
пропущенных в обычном прогоне, отдельно прошли в новой
`test_glossary_mail_acceptance`: **5 passed**, 9.37s
(`tests/tmp/repair-postgres-glossary.log`, exec 41541 terminal).
Другие платформенные ветки проверены Linux-набором выше/ниже.

Проверен перенос реальной Windows-публикации синтетического EML с вложенными
EML и DOCX (кириллица, emoji) в новый Linux data volume и новые PostgreSQL/Qdrant
volumes. Windows Python/parsers работали с NTFS, а серверы хранения обоих
изолированных контуров — Docker Linux. Использованы штатные
`create_qdrant_snapshot.py`, `backup_totals.py`, `write_backup_manifest.py`,
`restore_qdrant_snapshot.py`, `check_integrity.py`, pg_dump/pg_restore и Windows tar.
Компоненты backup выполнены последовательно без работающих application writers;
production `backup-bundled.sh`/`restore-bundled.sh` целиком здесь не запускались.

Результат:

- SHA-256 всех 9 backup-артефактов проверены перед распаковкой.
- Все 5 файлов данных совпали byte-for-byte; canonical chunks/source tree,
  active generation и координаты трёх концептов идентичны Windows-baseline.
- Восстановлены 3 sources, 3 chunks, 3 concepts, 6 Qdrant points; dense/BM25
  возвращают прежние point IDs. Strict integrity + manifest: 1 документ, 0 проблем.
- После Linux startup ASGI HTTP вернул source tree, source locations и байты
  оригинального EML/вложенного EML/DOCX с исходными SHA-256 и `nosniff`.
- On-demand OKF export построен из восстановленной БД; chunks совпали, все
  source manifest links относительные, вложенные файлы существуют.

Документ `a123b456c789d012`; отчёт `tests/tmp/repair-restore/reports/restore.json`,
логи `repair-windows-create-hex.log`, `repair-windows-backup-hex.log`,
`repair-restore-integrity.log`, `repair-restore-verify.log`. Fixture scripts и
Windows-baseline: `tests/tmp/repair-windows/`; restore compose/probe:
`tests/tmp/repair-restore/`. Старый пробный backup сохранён отдельно, не перезаписан.
Первый service-only fixture имел не-hex ID; он удалён штатным Pipeline.remove
строго внутри тестового каталога, затем создан корректный upload-style ID для HTTP.

Сетевой setup: initial `internal` source network сохранял HostConfig port bindings,
но фактически давал пустой NetworkSettings.Ports. Только source test project
пересоздан с обычной сетью и `127.0.0.1:25432/26333`; volumes сохранены.
Исходный acceptance и restore проекты остались internal, без host ports.
Пользовательская БД/службы, production compose и `.env` не менялись.

Все проверки этого продолжения терминальны; активных pytest/probe процессов нет.
Три тестовых PostgreSQL/Qdrant проекта сохранены для последующей приёмки.
Открыты реальная LLM/HTTP admission цепочка, 100-letter/100-MiB нагрузка,
baseline/after search и итоговая сверка всей матрицы. Общий goal остаётся active.

## Обновление 26.09: свежий Linux и серверные PostgreSQL/Qdrant

Docker снова доступен при выполнении вне sandbox. Собраны локальные образы
из текущего кода, без пользовательского `.env`/storage. Production image:
`okf-mail-verify:20260926-repair`, ID
`sha256:81162ad43e4b54bfd2da3c1d10b59ee6f1a0523ea8e20cdcbf1cbc0874665c9b`.
Test image дополнен pytest/reportlab; отдельный слой DejaVu закрывает два
пропуска PDF с кириллицей. Хэши пяти использованных образов и 328 исходных
файлов сохранены в `tests/tmp/repair-runtime/reports/{images.txt,source-snapshot.json}`;
общий source hash `7e9d69ec5acd30c6ee5bb719989d9165620bba82a31695704e88e7c2003a8fcd`.

| Проверка | Свежий результат / доказательство |
|---|---|
| Dockerfile production build | exit 0, `repair-linux-build.log` |
| Полный parser в Linux, `--network none --user 1000` | 114 passed, без skip, 2.02s; `repair-linux-parser-fonts.log` |
| Linux source/OKF/supervisor/generation/publication/repair/schema | 235 passed, 2 Windows-only skip (NTFS junction, RSS monitor), 376.41s; `repair-linux-backend.log` |
| Разрешённый pip-audit production runtime | 101 пакетов, 0 skipped, 0 известных уязвимостей; `repair-linux-audit.log`, `reports/dependency-audit.json` |
| Чистая PostgreSQL 17: Alembic upgrade head | exit 0, revision `010b1c2d3e4f`; `repair-postgres-migration.log` |
| PostgreSQL 17 + серверный Qdrant 1.19.0 | lifecycle passed, 12.186s; `repair-postgres-publication-lock.log`, `reports/publication.json` |

Для audit перед установкой инструмента сохранён `pip freeze --exclude-editable`;
проверялся этот pinned список через `pip_audit -r ... --no-deps --disable-pip`.
Зависимости самого аудитора не включались в runtime manifest. Нулевой результат
относится к известным scanner advisories на момент запуска, не к общей безопасности.

Серверный lifecycle работает в отдельном Compose project
`okf-mail-acceptance-20260926`, без опубликованных host-портов, на internal network.
Изолированные volumes содержат только синтетические письма. Проверены:

- Структура чистых миграций против ORM; 2 sources, 2 chunks, 2 concepts и точные
  source spans; dense/BM25 возвращают только опубликованное поколение.
- Ожидание PostgreSQL writer на `FOR SHARE` подтверждено `pg_blocking_pids`
  с PID удерживающего lock reader, затем writer завершился после release.
- Инъекция сбоя перед SQL commit сохраняет прежние rows, attachment hashes и
  search visibility. Следующий экземпляр Pipeline публикует ready generation
  без parser/LLM; старое поколение очищается, экспорт содержит переносимые пути.
- Исходный EML byte hash неизменен:
  `08c42141c1cf85b79ef38ebb9d41cec5a3b06458819a4746fae6289d9ee54c4b`.
  Итоговый doc `58659c12a8494398`, active `941ed1861b0d48108712e61fe54d1166`.

Ограничения: генерация/embeddings детерминированные; проверен service layer,
а не полный HTTP/Keycloak/реальная LLM цикл. Это свежий Docker snapshot рабочей
копии, не отдельный Git clean checkout. Scripts/compose для повторения находятся
в `tests/tmp/repair-runtime/`; перенос reusable acceptance runner в versioned tests
ещё не выполнен. Тестовые PostgreSQL/Qdrant оставлены для последующих проверок
backup/load, пользовательские службы не перезапускались.

Windows full backend coverage всё ещё выполняется как exec 37168; последний
подтверждённый прогресс 58%, без failure. Его нельзя считать пройденным до exit.
Открыты full-suite/coverage, HTTP/LLM acceptance, Windows→Linux backup/restore,
100-letter/100-MiB load и baseline/after search. Локальный mail flag true.

## Обновление 26.09: проверка ремонта и production build

Comment-concept и attachment-path maintenance теперь публикуют новое поколение
через общий canonical repair. Проверены source-owned уникальные якоря,
сохранение provenance остальных концептов, global review tag, no-op повтор,
dry-run, отказ Qdrant и последующая очистка abandoned generation. Path repair
проверен на нескольких заменах, Unicode, частичных/устаревших/нецелочисленных
диапазонах. CLI ошибок завершает процесс с exit 1; dry-run не создаёт внешних
клиентов. Источники тестов — SQLite/in-memory Qdrant, не live PG/Qdrant.

Свежие результаты:

- `repair-edges-green.log`: 23 passed, 16 warnings, 27.21s.
- `repair-publication-final.log`: 66 passed, 52 warnings, 81.26s.
- `repair-frontend-build.log`: `node node_modules/next/dist/bin/next build`, exit 0,
  Next 16.3.4; выполнены compile, page data collection, static pages, optimization.
- Полный backend Ruff: passed. Локальный mail flag остаётся true.
- Full backend с `--cov=app --cov-fail-under=85` запущен (exec 37168), результат
  пока не получен. Log `tests/tmp/repair-full.log`, exit `repair-full.exit`,
  coverage JSON `repair-full-coverage.json`; завершение проверять по live handle.

Общий план пока не закрыт: нужны итог full suite/coverage, текущая live-проверка,
Linux/backup-restore/load/search acceptance. Нет live-ремонта данных, commit/push
или production rollout. Следующие разделы сохраняют хронологию прошлых проверок.

Первичная проверка: 2026-09-25; повторная проверка: 2026-09-26.
Проверка выполнена в Windows shared checkout и в Linux Docker-образе из
текущего `backend/Dockerfile`; production rollout не выполнялся.

## Подтверждённые сценарии

| Сценарий | Доказательство |
|---|---|
| Standalone EML с UTF-8/CP1251, HTML и nested RFC822 | `doc-parser/tests/test_mail_parser.py`, полный parser suite |
| Некорректный root EML | `test_root_eml_without_structural_headers_is_rejected` и API admission: `422`, без document row, файла и pipeline job |
| EML внутри PDF и XLSX `embeddings/*.bin` | `test_pdf_embedded_eml_uses_the_same_recursive_mail_path`, `test_xlsx_embedded_bin_with_rfc822_payload_is_parsed_as_mail` |
| Зашифрованный S/MIME EML | `test_encrypted_smime_eml_is_diagnosed_without_indexing_opaque_body`: `encrypted_mail`, без индексации ciphertext |
| Prompt injection в source content | `test_chat_system_prompt_treats_document_content_as_untrusted_data`: системный prompt не исполняет инструкции из context blocks |
| Bcc и transport headers | `test_bcc_and_transport_headers_never_enter_mail_metadata_or_text`: private recipients и Received не входят в metadata/канонический текст |
| Direct MSG с subject/plain-text body | BSD-2 `msg_parser` 1.2.x и fixture `outlook-sample.msg`, SHA-256 `028d84ffe67e1865009669d13d4c12682943b32eccf7f84a8da1899db63b0131` |
| Приватный Outlook Unicode MSG с вложениями | Переданный пользователем `.msg` обработан через supervised worker: корректный CFB, кириллица без U+FFFD, 2 вложения, 3 source nodes, без предупреждений. Обезличенный отчёт хранится вне Git в `tests/tmp/mail/outlook-fixture-acceptance/` |
| RTF-only ANSI MSG | MIT `compressed-rtf` 1.0.7, Apache-2.0 fixture `rtf-simple-sent.msg`, SHA-256 `8adf5c3c77b46d9fa5910c3bb6ba54930160c4a9292348669f11c4d17cac8421` |
| Embedded MSG substorage | Apache-2.0 fixture `nested-rtf.msg`, SHA-256 `ee87b46667a0f262b3f119967a5854610bd197508067c51b53fb7ff0abfb8962`; recursive source `root/0`, marker and child body verified |
| Эквивалентные MSG и EML | canonical sender/header normalization и `mail_fingerprint` совпадают на реальном MSG fixture и парном EML |
| MSG как OLE-вложение DOCX | `test_docx_recursively_extracts_embedded_msg` |
| Вложенный MSG имеет тип источника `mail` | parser regression test проверяет `root/0` и parent path |
| Границы source → chunks → concepts | `backend/tests/test_mail_pipeline.py` |
| Выключенный импорт вложенного письма | PDF с EML сохраняет исходные bytes и source `skipped_disabled`; тело письма не создаёт chunk/concept, текст родительского PDF остаётся доступен для поиска. Проверено прямым и supervised parser path, Windows и Linux |
| Источник в retrieval и source location | `test_retrieval_hydration.py`, `test_source_location.py` |
| API дерева и container download | `test_document_sources_api.py` |
| Soft delete, restore visibility и purge | `test_document_sources.py`, `test_trash.py` |
| Parser worker timeout и очистка | `test_parser_supervisor.py`: реальный spawn child завершается и temporary attachments удаляются |
| Crash без worker message | `test_parser_supervisor.py`: EOF нормализуется в `ParserWorkerError`, temporary attachments удаляются |
| Parser worker Windows RSS limit | `test_parser_supervisor.py`: child с 160 MiB завершается при 64 MiB limit через WinAPI |
| Worker IPC и secrets boundary | `test_parser_supervisor.py`: oversized/non-JSON output отклоняется с очисткой; worker не видит `DATABASE_URL` и LLM key |
| Worker error privacy | `test_worker_error_message_never_echoes_untrusted_content`: ошибка parser-библиотеки не возвращает body/source text |
| Общий лимит parser workers | preview и pipeline используют два общих неблокирующих slots на backend-процесс; третий отклоняется контролируемо |
| Аутентификация и root visibility source/download | `test_authz.py`: anonymous=401; viewer/editor/admin/security=200 для active root; trashed root=404; legacy download получил те же правила и `nosniff` |
| Hostile HTML | `test_mail_parser.py`: network socket запрещён; script/style/iframe/form, remote pixel, `javascript:` и CID не попадают в blocks |
| Spoofed MSG | `.msg`-имя и Outlook ProgID без CFB/MAPI structure сохраняются только как opaque `.bin`, не как mail source |
| 100 synthetic nested EML parser profile | `backend/test_scripts/probe_mail_ingestion.py`: 200 source nodes; 1.14854 s total; p95 0.014860 s; input SHA-256 `0fb77f14b1a7bdc22f074ca0532a171f7a623ec1ff3b6aa0e6d04f153e59e2c4` |

## Выполненные проверки

| Команда | Результат |
|---|---|
| `doc-parser: python -m pytest -q` | 82 passed |
| `backend: pytest test_document_sources.py test_source_location.py test_retrieval_hydration.py` | 44 passed |
| `backend: pytest test_document_sources.py test_trash.py` | 19 passed |
| `backend: pytest test_error_codes.py` | 28 passed |
| `frontend: node --test test/uploadFailures.test.mjs test/uploadReview.test.mjs test/i18n.test.mjs` | 34 passed |
| `frontend: next build` | compiled successfully |
| `frontend: node --test test/*.test.mjs test/*.test.js` | 185 passed |
| backend focused mail/source/error/trash suite | passed, без failures |
| `backend: pytest test_mail_identity.py test_reparse_mail_documents.py` | 7 passed |
| `backend: pytest test_mail_identity.py test_deduplication.py test_upload_similarity.py` | service/admission проверки пройдены; конкурентный recheck 12/12 |
| `backend: pytest test_schema_drift.py` | 3 passed после проверки чистого Alembic upgrade |
| `backend: pytest test_parser_supervisor.py` | 4 passed |
| `backend: pytest test_parser_supervisor.py` с POSIX case | 4 passed, 1 skipped на Windows; Linux RLIMIT_AS case добавлен, но локально не выполнен |
| `backend: pytest test_parser_supervisor.py` с concurrency limit | 5 passed, 1 skipped на Windows |
| `backend: pytest test_parser_supervisor.py` crash injection | 6 passed, 1 skipped на Windows |
| Final supervisor + upload admission run | 19 passed, 1 Windows-only POSIX skip |
| Final mail/source/schema run | 19 passed |
| Final authorization matrix | 34 passed |
| `backend: pytest test_upload_similarity.py` с parser busy | 13 passed; preview `429` не оставляет файл или ghost document |
| `backend: pytest test_mail_pipeline.py test_document_sources.py test_document_sources_api.py` | 6 passed через supervised parser path |
| `backend: pytest test_mail_pipeline.py` после MSG substorage | 2 passed; source ownership `root`/`root/0` для MSG |
| `doc-parser: pytest tests` после hostile-HTML, MSG substorage и spoofed MSG | 86 passed |
| doc-parser: pytest tests после partial-attachment diagnostics | 87 passed |
| Parser diagnostics + supervisor + mail pipeline + schema | 18 passed, 1 Windows-only POSIX skip |
| `doc-parser: pytest test_mail_parser.py` hostile HTML | 13 passed |
| `doc-parser: pytest test_attachments.py` spoofed MSG | 20 passed |
| `backend: pytest source authz + document sources API` | 3 passed; legacy download не отдаёт файл из корзины |
| Итоговый focused backend/mail набор | 9 passed, 1 skipped (POSIX RLIMIT_AS на Windows) |
| `backend: pip check` | No broken requirements found |
| `backend: probe_mail_ingestion.py --count 100` | parser-only profile passed; JSON written outside git to `tests/tmp/mail/` |
| `ruff check` для затронутых mail parser modules | passed |
| SQLite clean database: `alembic upgrade head` | `document_sources` and `okf_concepts.source_id` created |
| Dev PostgreSQL schema repair | адресно добавлены `mail_fingerprint`, три `source_id`, `parser_version` и `parse_warnings` после подтверждённого drift; backend health отвечает, DB/Qdrant/Ollama=`ok` |

## Ограничения и release gate

`MAIL_IMPORT_ENABLED=false` остаётся значением по умолчанию. В таком состоянии
standalone EML/MSG получают `503 mail_import_disabled` до записи файла; уже
обработанные документы и их источники продолжают читаться.
Вложенные EML/MSG при выключенном флаге сохраняются как оригиналы без извлечения
и индексации. Парсер `mail-sources-v4` использует отдельную версию checkpoint
`mail-sources-v4-mail-disabled`; resume между режимами отклоняется с
`parser_version_mismatch`, чтобы не смешать старые и новые source/chunk ID.

Исходное значение флага в коде остаётся `false`. В локальном `.env` пользователя
сейчас `MAIL_IMPORT_ENABLED=true` для проверки; по его прямому указанию этот
локальный флаг не возвращается в `false` до продуктивного выпуска.

До полного выпуска остаются: воспроизводимый Linux clean-checkout с полной
матрицей fixture, сквозной изолированный Qdrant/LLM acceptance run, проверка
безопасного переключения поколений при `regenerate`, измерение поиска под
нагрузкой и проверка переноса backup/restore. Linux POSIX RLIMIT_AS test уже
выполнен в свежем Docker-образе (см. повторную проверку ниже).
Локальный 100-mail parser-only profile
закрыт, но не измеряет очередь, embedding, Qdrant или LLM. Матрица текущей
ролевой модели проверена: письма входят в общую базу знаний и доступны всем
authenticated roles. Пользователь осознанно публикует загружаемое письмо для
этой аудитории; per-document ACL не входит в scope релиза.

При первичной проверке сервисы были недоступны, а позже настроенный LLM-провайдер
не получал исходящее соединение (`WinError 10013`). Эти наблюдения относятся
только к 25.09. Позднее локальный стек и LLM стали доступны: пользовательский
MSG был регенерирован через штатный pipeline, но отдельная приёмка на
изолированной БД/Qdrant со всей цепочкой поиска, восстановления и экспорта
по-прежнему не проведена. Сквозной runtime-прогон нельзя заменить unit-тестом
или parser profile.

`pip check` подтверждает согласованность установленного окружения. `pip-audit`
25.09.2026 не нашёл известных уязвимостей среди опубликованных зависимостей;
JSON-отчёт сохранён вне git в `tests/tmp/mail/`. Локальный editable-пакет
`okf-doc-parser` пропущен самим инструментом, поскольку его нет на PyPI.
`mail_identity` участвует в existing `allow_similar`
admission review только при полном извлечении всех вложений; exact byte duplicate
остаётся отдельным блокирующим SHA-256 check. PST/OST и mailbox sync не входят в
этот релиз.




## Дополнительная диагностика и checkpoint-совместимость

`documents.parser_version` и `documents.parse_warnings` сохраняют версию
контракта извлечения и структурированные предупреждения. Если вложенное письмо
не удалось разобрать, доступные sibling-источники всё равно индексируются, а
документ завершается с `attachment_partial_result`; предупреждение есть и на
`Document`, и на затронутом `DocumentSource`.

Migration `fc0b1c2d3e4f` сохраняет `parser_version` в
`document_staging`. Resume versioned partial checkpoint с другой версией
парсера возвращает `parser_version_mismatch` и требует полной регенерации;
legacy checkpoint без версии остаётся совместимым. Это исключает смешивание
старых concept/chunk checkpoint с новым деревом источников.

Migration `fd0b1c2d3e4f` сохраняет SHA-256 корневого файла в
`document_staging`. Частичный resume отклоняется до смешивания результата, если
исходные bytes изменились; versionless/hashless legacy checkpoint совместим.
OKF bundle сохраняет отдельный `sources.json` с относительными путями, а
вложения получают дисковое имя из `source_id`; unsafe reparse/symlink storage
даёт `skipped_storage` без записи через небезопасный путь.

Канонический текст корневого документа ограничен 2 000 000 Unicode-символов.
После отсечения parser сохраняет attachment/image metadata, добавляет
`text_limit_exceeded`, а pipeline показывает `text_partial_result`; это не
позволяет тихо проиндексировать неполный хвост.

S/MIME encrypted EML имеет отдельный `encrypted_mail`: исходный root доступен,
но ciphertext не декодируется и не индексируется. Проверка подписи S/MIME и
IRM/зашифрованные MSG по-прежнему не являются подтверждённой поддержкой.

Повторный parser-only профиль 100 synthetic nested EML после финальных
ограничений: 200 source nodes, total 1.14854 s, p95 0.014860 s. Это проверяет
только parser; Qdrant/LLM/очередь не входят в измерение.

Parser supervisor передаёт только bounded JSON (16 MiB), а не pickle; parent
проверяет JSON schema перед созданием Block/SourceNode. Worker очищает окружение
до системного allowlist, поэтому не наследует `DATABASE_URL` и ключи LLM.
Ошибка child передаётся как стабильная категория без `str(exc)`, поэтому
исключение parser-библиотеки не переносит в API/log pipeline фрагмент письма.

System prompt и его fallback явно объявляют source blocks недоверенными
справочными данными и запрещают исполнять инструкции из письма/вложения.

Mail metadata intentionally excludes Bcc and transport headers. A parser
regression proves that private recipient and Received values do not enter
canonical text, metadata, LLM context or index.

Бинарные MSG fixture закреплены в
`doc-parser/tests/fixtures/mail/manifest.json`; test проверяет локальные
SHA-256, происхождение, лицензию и ожидаемые source ID. Переданный пользователем
Outlook Unicode MSG проверен локально, но намеренно не включён в Git как приватный
fixture. Остаются нестандартные RTF codepage, IRM/шифрованные/подписанные MSG и
полный Windows/Linux прогон.
### Последний локальный validation run

- `doc-parser`: 101 passed, 1 skipped (Windows не позволил создать symlink;
  детерминированная проверка reparse point пройдена), включая fixture manifest
  и root-EML validation.
- Backend core (sources, diagnostics, staging/parser-version, mail pipeline, supervisor): 22 passed, 1 skip (POSIX RLIMIT_AS на Windows).
- Alembic/schema drift: 3 passed.
- API error-code/localization checks: 28 passed.
- Scoped ruff для новых backend-модулей и изменённых parser-модулей: passed.
- API malformed-root-EML admission: 14 passed; `422` не создаёт файл, row или
  pipeline job.

### Исправления после review

После review исправлены MIME boundary parent/attached message, обход вложений
в MIME containers и embedded MSG, сохранность Unicode/обычного текста MSG,
безопасная публикация parser attempt directory, проверка любого resume
checkpoint, numeric chunk ordering и DB-backed export source tree.

- `doc-parser`: 106 passed, 1 skipped (Windows symlink).
- Backend mail/source/export: 137 passed, 1 POSIX-only skip.
- Frontend mail/source tests: 37 passed.
- Приватный Unicode MSG повторно проверен через supervised parser: root + 2
  вложения, без parse warnings и символов замены. Бинарник и обезличенный
  отчёт не включены в Git.

### Повторная проверка 26.09.2026

- Свежий Linux-образ `okf-mail-verify:local` собран из текущего
  `backend/Dockerfile`. Прежний `okf-backend:local` оказался устаревшим и не
  содержал `msg_parser`/`compressed_rtf`; его сбой на embedded MSG не является
  результатом проверки текущего кода.
- В свежем Linux-образе `backend/tests/test_parser_supervisor.py` и
  `backend/tests/test_mail_pipeline.py`: **13 passed, 1 skipped**. POSIX
  `RLIMIT_AS` тест прошёл; пропущен только Windows RSS-monitor case.
- Полный `doc-parser/tests`: **110 passed, 1 skipped** на Windows и
  **109 passed, 2 skipped** в Linux-образе. Различия — платформенные skip.
- Полный backend suite после восстановления исходных координат: **1763 passed,
  6 skipped**. Отдельный focused backend run: **106 passed**. После этого
  добавлен узкий guard для mail fallback: связанная группа тестов OKF,
  source-location и mail pipeline — **99 passed**, обе новые границы
  дополнительно прошли в Linux-образе (**2 passed**); полный backend suite
  после последнего guard повторно не запускался.
- Приватный пользовательский MSG (документ `9e156bbf80b24b4c`) после
  повторной штатной генерации имеет `status=done`, `problem=NULL`, версию
  парсера `mail-sources-v3`, 3 source nodes, 5 концептов, 3 чанка и ровно
  8 точек Qdrant (проверено повторно запросами к БД и индексу). Четыре
  концепта имели сохранённые диапазоны по прежним правилам. После ужесточения
  проверки API показывает 2 `exact` и 3 `chunk`: прежние диапазоны на целый
  абзац без дословного подтверждения не выдаются за точные. Бинарник и
  содержание письма в отчёт и Git не включены.
- Указанный пользователем DOCX «Инструкция ЭЛН (тест ЕЦП) (1).docx», концепт
  `vazhno`: восстановлены два дословных непересекающихся диапазона исходного
  текста; БД содержит `source_spans`, а переход в браузере показывает оба
  выделенных места. Этот дефект не связан с форматом MSG.
- Почтовый лексический fallback больше не записывает абзац в `source_spans`.
  Предполагаемое место вычисляется при чтении и получает отдельный статус
  `inferred` с явной подписью в UI; если проверка не проходит, остаётся `chunk`.
  Требуется совпадение содержательных терминов именно в тексте концепта;
  заголовок только разрешает ничью между уже подтверждёнными кандидатами.
  Общее слово «сообщение», совпадение только заголовка и противоположное
  утверждение с «не» закреплены регрессиями. Старые почтовые диапазоны на
  целый абзац проверяются заново и понижаются до `inferred` или `chunk`.
- После этого исправления связанные backend-тесты: **144 passed** на Windows,
  `test_source_location.py` + `test_okf.py`: **103 passed** на Linux;
  frontend `node --test`: **185 passed**, Next production build успешен.
  Дополнительные 3 проверки старых ложных диапазонов (общее слово,
  совпадение только заголовка, отрицание) прошли отдельно на Windows и Linux.
  `vazhno` в отдельном DOCX по-прежнему `exact` с двумя исходными диапазонами.
- `git diff --check` не обнаружил ошибок пробелов (есть предупреждения Git
  о CRLF). Публикация в production не выполнялась.
- Linux parser-only профиль 100 синтетических EML: входной SHA-256
  `edf7b1831618fbd595fcacd058f53e9d2f60eae4ddecde592f6499805bb013ed`,
  200 source nodes, p95 `0.006332` с, total `0.45956` с. Это не измерение
  100 MiB, очереди, Qdrant или LLM.
- Локальный health вернул `ok` для database, Qdrant, Ollama, LLM и PDF parser.
  Read-only `probe_sources.py` выполнил 42 контрольных запроса без
  `acceptance_failures`; p95 полного retrieval `2811.327` мс (из них p95
  embedding `2798.122` мс, Qdrant `11.337` мс). Результат сохранён только
  локально в ignored `tests/tmp/mail/probe-current-20260926.json`.
  Сравнения с исходным baseline до почтовой доработки нет.

## Проверка выключенного импорта вложенных писем (2026-09-26)

- `MAIL_IMPORT_ENABLED` в локальном `.env` остался `true`; тесты явно задают
  `false` и не меняют runtime пользователя. По умолчанию в коде и
  `.env.example` флаг выключен.
- Реальный PDF с текстом и приложенным EML прошёл весь backend pipeline при
  выключенном флаге: статус письма `skipped_disabled`, оригинальные bytes
  скачиваются, тело письма отсутствует в chunks/concepts, текст PDF сохраняется
  в chunks. Документ имеет явный `attachment_partial_result`.
- PDF без текстового слоя не отправляет служебную пометку вложенного письма в
  LLM; обычная ссылка на страницу-изображение может остаться в root chunk.
  Backfill старых чанков также не индексирует пометку об отключённом письме.
- Проверены EML, MSG и EML в `oleObject1.bin`, а также отдельный supervised
  worker. Переключение режима отвергает старый checkpoint по версии парсера.
- Windows: полный парсер **113 passed, 1 skipped**. Все три варианта вложенных
  писем также прошли отдельно в Linux. Backend focused: **43 passed, 1 skipped**;
  frontend **185 passed**, `next build` успешен. Linux focused backend:
  **21 passed, 1 skipped**. Полный Linux parser suite с test-only `reportlab`
  во временном каталоге контейнера: **110 passed, 2 skipped**.
- Проверено чтением БД: у `vazhno` в указанном пользователем DOCX всё ещё
  сохранены два диапазона `1895–2125` и `2386–2687`. Содержимое документа
  при этой проверке не менялось.
- Полный backend suite до последнего узкого исправления fallback:
  **1777 passed, 6 skipped**. Последующее исправление учитывает legacy parser
  adapters с отдельным каноническим Markdown; связанный backend suite на
  окончательном коде: **176 passed**, Linux pipeline/scan-focused:
  **10 passed**. Scoped Ruff и `git diff --check` прошли.

## Основа безопасной регенерации (2026-09-26, интеграция ещё не завершена)

Добавлены таблицы состояния/поколений и Alembic `fe0b1c2d3e4f`. Начало попытки,
ready, публикация и отмена сериализуются на строке Document как в SQLite, так
и в PostgreSQL. Публикация участвует в транзакции замены canonical rows;
rollback сохраняет предыдущий active ID и содержимое. Устаревшая попытка,
неподготовленное поколение и второй конкурентный запуск отвергаются.

Каталоги нового поколения отделены от legacy attachments/bundle. Очистка
разрешена только для retired/abandoned, проверяет оба дерева до первого
удаления и отвергает symlink/Windows reparse point. Проверен настоящий NTFS
junction: исходные и соседние файлы сохранились.

Доказательства:

- Windows store/files/schema/trash: **35 passed, 1 skipped**; после добавления
  NTFS junction test files-suite: **9 passed, 1 skipped** (обычный symlink
  требует отсутствующей привилегии).
- Linux store/files/schema: **20 passed**, включая symlink cleanup guard
  (прогон до добавления Windows-only junction case).
- Все **8** store-тестов повторены на локальном PostgreSQL в отдельных
  временных схемах. Конкурентный begin и transaction rollback прошли;
  после прогона проверено отсутствие временных схем. Обезличенный результат:
  ignored `tests/tmp/generation-pg-result.json`.
- В рабочей PostgreSQL созданы только две новые пустые таблицы; existing
  documents/canonical rows не переносились, alembic_version не менялся.
- Scoped Ruff и `git diff --check` прошли. Полный backend-набор запущен,
  его результат на момент этой записи ещё не получен.

`Pipeline.regenerate`, файловая финализация и Qdrant пока используют прежний
путь. Наличие этих модулей не закрывает release gate безопасной регенерации.
Точный порядок дальнейшего подключения и выявленные read/write races описаны
в `docs/superpowers/plans/2026-09-26-generation-publication.md`.


2026-09-26 generation search checkpoint: Qdrant concept/chunk IDs accept an explicit generation ID while legacy UUIDs remain unchanged. Search excludes candidate/retired/abandoned generations before ranking and legacy points after publication; scoped orphan cleanup preserves other generations. Hydration rechecks active ID and reads canonical text in a SQLite snapshot or under PostgreSQL shared Document row locks. Windows related suite: 150 passed/1 skipped; final search suite including missing/null legacy payload and candidate top-k pressure: 11 passed; two hydration/concurrency tests also passed in temporary PostgreSQL schemas, which were removed. Scoped Ruff and diff check pass. Source-location/OKF review suite: 106 passed; read-only vazhno still exact at (1895,2125) and (2386,2687); local mail flag true. Linux search verification did not execute because automatic approval review failed with authorization 401. Previous full backend session 79098 is now unknown after runtime reset; no final exit/result was obtained, so it is not claimed green. Regenerate/finalize still need candidate preparation and atomic publication integration.


2026-09-26 generation pipeline integration: Replaced destructive regenerate with candidate preparation. _process uses generation-scoped attachment attempts; _finalize builds candidate bundle/index, writes publication.json, marks ready, then publishes canonical rows, active pointer, final status/parser fields, dedup signature and detected development in one transaction. Staging generation_id is migrated by ff0b1c2d3e4f. Ready resume skips parser/LLM. Late manual locale/tag edits are reconciled against candidate Qdrant metadata. Generation downloads use registered active paths; exported source manifest paths are portable. Chunk/sparse/relation backfills and tag sync use active generation IDs; empty published versions are not reparsed on read. Tests: 157 related passed, final 12 publication tests passed, 73 generation/search/backfill/tag tests passed, 5 migration/staging tests passed; Ruff/diff check passed. Local PostgreSQL ALTER was attempted but its connection never completed; process 96211 was interrupted after all three local service port probes timed out. Public schema update remains unverified and must precede restarting updated backend. Remaining: cleanup lifecycle, concurrent launches/edits/trash/read consistency, ready-artifact integrity, standalone maintenance/reindex scripts, full regression and real PostgreSQL/Qdrant/Linux/backup/load acceptance. No production readiness claim.


## 2026-09-26: очистка поколений и согласованное чтение

- Cleanup подключён после завершения worker и к фоновому повтору раз в минуту
  (включая старт приложения). Удаление идёт Qdrant → files → generation row;
  сбой оставляет durable row для повторной попытки и не меняет published status.
  Параллельные очистки повторно проверяют строку только после Document lock.
- Legacy cleanup удаляет только известные производные artifacts; исходный upload,
  active/candidate поколения и посторонние файлы сохраняются. Активные legacy
  ссылки в canonical rows запрещают очистку старых путей.
- Export удерживает согласованное чтение до копирования вложений. Source-location
  читает concept/chunk/source в одном snapshot. Download открывает файл под read
  lock и отдаёт уже открытый descriptor; сохранены HEAD, Range, If-Range, ETag,
  multipart ranges и закрытие handle при обрыве. Pathsend запрещён для этого пути.
- Ошибка чтения staging после SQL commit больше не переводит опубликованный
  документ в failed. Cleanup staging остаётся best-effort; отдельное durable
  восстановление его очистки ещё требует проверки.
- Regenerate очищает checkpoints только после admission под _start_lock.
  Queue-overload сохраняет checkpoints и статус; два одновременных regenerate
  допускают один worker и выполняют один reset.

Доказательства: cleanup/store/files 24 passed, 1 skipped; download/API/publication
65 passed; source-location/hydration/glossary 60 passed; cleanup/pipeline integration
после исправления изоляции test settings 74 passed; после переноса reset за admission
pipeline integration 63 passed и отдельный deterministic parallel-regenerate test
1 passed. Каждый исправленный дефект сначала воспроизведён падающим тестом.

Полный backend прогон предыдущего snapshot завершён: 1818 passed, 7 skipped,
1 failed (старое ожидание числа SELECT в glossary metrics). Подтверждён один новый
batch query active generations; ожидание скорректировано с 3/4 на 4/5, связанная
группа прошла. Это не итоговый full-suite результат последнего кода.

Повторный сетевой probe: 127.0.0.1:5432/18000/16333/16300 — timeout.
Нельзя считать подтверждёнными live staging.generation_id migration, UI и запуск
обновлённого backend. Локальный MAIL_IMPORT_ENABLED=true сохранён.

Открыты: целостность ready artifacts и повтор после commit; concurrent tag/locale/
rename/trash/purge и lazy-backfill paths; standalone maintenance/reindex scripts;
проверка полного final snapshot, реальный PostgreSQL+Qdrant lifecycle, Linux,
backup/restore и нагрузка. Release gate безопасной регенерации остаётся открыт.


## 2026-09-26: проверка ready artifacts и повтор публикации

Подготовленная версия теперь содержит перечень SHA-256 всех файлов вложений и
bundle и список ожидаемых point IDs. SHA-256 самого publication.json хранится
отдельно в DocumentGeneration.publication_hash (additive migration 010b1c2d3e4f,
после ff0b1c2d3e4f). Перед canonical writes под Document lock проверяются manifest,
файлы, принадлежность всех saved_path поколению и наличие/принадлежность точек
Qdrant. Несовпадение откатывает SQL-переход, оставляет прежнюю active version и
возвращает partial_regeneration_unavailable с действием полной перегенерации.
Отсутствующий digest у старого ready checkpoint тоже не допускает публикацию.

Повторная публикация уже active generation возвращает no-op до чтения manifest
и canonical writes: последующие исправления концепта сохраняются; утрата старого
подготовительного manifest не мешает такому повтору. Проверки corruption/missing
attachment, missing bundle, changed manifest, missing/foreign point, missing digest
и обоих replay-сценариев добавлены в test_generation_integrity.py. Первые 7
сценариев были воспроизведены до изменения кода (7 failed); первые integrity/store/
schema checks после исправления: 18 passed. Related run: 101 passed, 1 failed из-за
legacy test fixture, напрямую подставлявшей attachments/diagram.png. Эта фикстура
переделана на реальный parse-attempt → generation attachment transfer; отдельный
тест прошёл. Итоговая группа запущена отдельно; результат записывается ниже.

Dev PostgreSQL требует двух пока НЕ подтверждённых колонок перед рестартом:
document_staging.generation_id и document_generations.publication_hash.
Никакой live ALTER, перезапуск или перенос пользовательских данных в этом проходе
не выполнялся. Полный backend Ruff и scoped diff-check проходят.

Следующие обязательные участки: writer serialization metadata/trash/purge,
защита lazy _backfill_chunks от публикации между чтением и записью; maintenance
scripts reindex/rebuild_qdrant_v2 (сохранить active generation IDs, source IDs и
последний опубликованный результат при paused/failed новой попытке), backfill
comment/attachment tags и flat-bundle readers. Production gate ещё открыт.


Итог integrity-группы на текущем коде: 105 passed (tests/tmp/publication-integrity-final.log),
включая 9 corruption/replay cases, generation pipeline/cleanup/search, mail и schema drift.
Далее добавлен общий lock_document_write до чтения состояния: registry update,
soft-delete/restore/delete/delete_if_deleted и изменение concept tags используют
тот же Document lock, что и publication. Детерминированный тест сначала подтвердил
чтение document tags во время незавершённой публикации, после изменения прошёл.
Metadata/store/document-tags/trash suite: 53 passed (tests/tmp/generation-metadata-green.log).
Это основа сериализации, а не доказательство всех metadata/Qdrant/purge races:
фоновый tag sync, rewrite bundle frontmatter и late worker writes после purge
ещё требуют отдельной проверки. Полный backend Ruff проходит.

Полный прогон 1157 по snapshot до integrity-изменений всё ещё выполняется
(tests/tmp/generation-cleanup-full.log; был подтверждён живым write_stdin).
На 77% в логе есть F; итоговая диагностика ещё не получена. Не перезапускать
живой прогон и не считать его проверкой новой колонки publication_hash: процесс
стартовал до её добавления. Следующий шаг: получить его терминальный результат,
разобрать конкретное падение, затем защищать lazy backfill и metadata projections.

## 2026-09-26: additional concurrency verification

Private lazy chunk backfill now discards stale parsing after publication,
candidate creation, trash/purge or another chunk writer. Existing legacy bytes
are immutable on this read-time path; conflicting attachment content aborts
without overwrite. SQL rollback and retry preserve source paths and bytes.
Related suite: 73 passed, 1 skipped; final dedicated backfill suite: 8 passed.
Bundle tag rewrite now reads current canonical tags under a Document write lock
through the file replacement; 2 reproduced races fixed, 32 metadata/tag tests passed.
Full backend snapshot-before-integrity result: 1847 passed, 7 skipped, 2 failed;
fresh export/schema checks passed after removal of obsolete test monkeypatch.
The final full-suite and live/runtime gates remain open. See generation-publication
plan for detailed evidence and remaining work. MAIL_IMPORT_ENABLED=true unchanged.

## 2026-09-26: metadata/deletion verification

Canonical document/concept tags now commit atomically (SQL failure rolls both
back); stale per-concept deltas no longer replay. Locale/development projections
read current DB values under the Document lock until Qdrant acknowledges writes.
Deletion refuses a live worker timeout and returns localized processing_stopping;
retry after completion works. Admission rechecks missing/trash, cleanup retires
canonical rows before removing artifacts, and cancelled workers finish as paused.
Combined isolated SQLite/local-Qdrant group: 142 passed in 140.53s; frontend i18n:
29 passed; full backend Ruff passed. Runtime probes still time out; no live ALTER,
restart, commit or push. Full suite, real PG/Qdrant, maintenance reindex/backfills,
Linux/backup/load gates remain open. See generation-publication plan for details.

## 2026-09-26: maintenance verification

Canonical reindex now preserves generation/source identities and original chunk
indices, including published results during paused/failed regeneration. V2 rebuild
does not mutate shared Settings or delete the configured active collection. Parity
and integrity checks select active generations and their artifact paths. Related
suite: 37 passed (reindex-integrity-green.log), preceded by reproduced failures.
Dedup backfill uses SQL text and preserves mail identity; deletion/rollback checks
passed. Chunk CLI uses SQL state and refuses destructive obsolete --force; 21
chunk/backfill/integrity/backup tests passed. Attachment tag backfill now validates
source identity under a writer lock and retries generation-aware Qdrant projections.
Extended maintenance/source suite running as exec 6726, log maintenance-source-final.log;
do not count it as passed until terminal output. Backend Ruff and scoped diff pass.
Outstanding scripts and runtime/final acceptance gates are listed in the generation
publication plan. No live data writes, service restart, commit or push.

Terminal update: exec 6726 **160 passed, 8 warnings, 91.59s**. The combined group
includes the three reported false source-location matches, inferred-vs-exact,
attachment projection failure/retry and generation switch. Frontend tests:
**185 passed** (maintenance-frontend-test.log); ESLint still running exec 94512.
Read-only runtime probe outside sandbox also timed out on 5432/18000/16300/16333
and failed HTTP health checks (exec 25205 terminal). Live behavior remains unverified.

ESLint exec 94512 terminal: exit 0, 0 errors, 35 warnings. No intentional test
processes left running. Frontend build and remaining full acceptance gates are open.

## 2026-09-26: legacy migration and attachment coverage

Historical DB migration now refuses generation-owned/source-aware/deleted documents
even with --force, under a Document writer lock. Private parse preserves source
tree/attachment paths and rolls back SQL on failure; conflicting bytes rejected.
5 RED cases -> 23 migration/lazy-backfill tests passed (legacy-migration-final.log).
Pipeline attachment coverage had a reproduced equal-count/different-boundaries
false tag on root text; shared source-section coverage fixes it in pipeline and
maintenance. 21 source/attachment/mail tests passed (source-coverage-green.log).
Expanded related suite running exec 27510, legacy-source-related.log. Ruff and
scoped diff checks pass; production/live schema/data have not been changed.
Comment-concept and path-repair scripts still need canonical generation publication.

Expanded exec 27510 terminal: **97 passed, 56 warnings in 92.02s**,
legacy-source-related.log. Full backend coverage and live/runtime acceptance
remain open; all intentionally launched test sessions are now terminal.

## 2026-09-26: canonical repair foundation

Pipeline now uses a shared verified publication service; checkpoint cleanup stays
after commit. Added canonical repair preparation with durable candidate reservation,
private artifact copies, hash checks, provenance preservation, generation-aware
full indexing and canonical snapshot conflict rejection. Dry-run/no-op write nothing;
index failure preserves old publication and leaves an abandoned generation for cleanup.
Related group: 52 passed (canonical-publication-final.log). After replacing the
long preparation writer lock with a shared publication lock: 7 repair tests passed
(canonical-repair-shared-read.log). Backend Ruff passes. These are isolated tests,
not live PG/Qdrant acceptance. Comment/path maintenance scripts still need wiring
and their specific regression cases; the overall plan remains incomplete.
