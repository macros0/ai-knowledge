# Детализация задачи 11: безопасная публикация поколения документа

Это уточнение уже согласованного плана Outlook ingestion, а не изменение его
критерия готовности. Пока интеграция ниже не закончена, `Pipeline.regenerate`
по-прежнему считается незакрытым release gate.

## Текущий статус продолжения 26.09: comment/path repair

Итог следующего прогона: полный Windows backend **1956 passed, 7 skipped**,
coverage **89.42%** (gate 85% пройден), exec 37168 terminal. Пять opt-in PG tests
дополнительно прошли на настоящей отдельной PostgreSQL БД. Windows publication
с вложенными EML/DOCX восстановлена в новые Linux PG/Qdrant/data volumes:
identical source tree, active generation, canonical text/spans, 5 byte-identical
files, 6 search IDs, ASGI HTTP downloads и portable export. Это component-level
backup/restore с fake LLM/embedding; production shell orchestration и full SSO/
real-LLM/load gates этим не объявляются закрытыми. См. acceptance report.

Последующее подтверждение: свежий Linux image построен; Linux generation/source/
repair/schema группа 235 passed, 2 Windows-only skip. На отдельной PostgreSQL 17,
мигрированной до `010b1c2d3e4f`, и Qdrant 1.19.0 прошёл service lifecycle:
source ownership/spans, active-only dense/BM25, failure before commit, ready resume
без parser/LLM, cleanup/export. Блокировка writer подтверждена `pg_blocking_pids`.
Отчёт `tests/tmp/repair-runtime/reports/publication.json`; лог
`tests/tmp/repair-postgres-publication-lock.log`. Синтетические generation и
embeddings не заменяют HTTP/SSO/real-LLM и backup/load acceptance. Полная Windows
coverage проверка exec 37168 пока выполняется. Подробности в acceptance report.

Оба maintenance-скрипта подключены к `repair_published_document`. Более ранние
записи ниже о неподключённых скриптах описывают предыдущий этап.

- `fix_attachment_paths.py` берёт полный SQL snapshot, изменяет только известные
  маркеры/file-ссылки, пересчитывает offsets по сумме точных замен и chunk hashes.
  Частично пересекающие замену, устаревшие или нецелочисленные координаты
  отбрасываются; Python code-point offsets сохраняются, включая emoji.
  Публикуются bundle и обе поисковые ветки; повторный запуск без изменений — no-op.
  Dry-run не создаёт клиентов embedding/Qdrant; ошибки документов дают exit 1.
- `backfill_comment_concepts.py` использует private supervised parse с проверкой
  hash исходника до/после разбора и относительно сохранённого `file_hash`.
  Якорь должен встречаться ровно один раз внутри собственного source_id;
  неоднозначность/отсутствие отменяет весь ремонт документа до индексации.
  Остальные концепты и их provenance берутся из SQL, а не старого flat bundle.
  Глобальный `review` не удаляет обычные концепты. Повтор сохраняет generated_at
  неизменившихся тредов и не создаёт нового поколения. Dry-run не создаёт внешних
  клиентов, ошибка обработки возвращает exit 1.
- Изолированные тесты используют SQLite и настоящий in-memory Qdrant с dense
  и sparse индексами. Фикстура дополнена sparse config и `delete(wait=...)`;
  это были ошибки тестового адаптера. В новых RED-тестах обнаружены успешный
  exit при CLI-ошибке, создание внешних клиентов для dry-run и приведение
  строковых/дробных offsets к verified spans. Все три исправлены.
- Проверка сбоя сохраняет прежние SQL rows/active bundle. Abandoned generation
  остаётся до штатной очистки; тест отдельно выполняет cleanup и проверяет,
  что исходные файлы сохранились, а файлы неудачной попытки удалены.

Свежие результаты: **23 passed, 16 warnings, 27.21s**
(`tests/tmp/repair-edges-green.log`); расширенная группа publication/repair:
**66 passed, 52 warnings, 81.26s** (`tests/tmp/repair-publication-final.log`).
Ruff backend проходит. Frontend production build завершён exit 0
(`tests/tmp/repair-frontend-build.log`, Next 16.3.4).
Полный backend coverage запущен как exec 37168, `tests/tmp/repair-full.log`,
exit-file `tests/tmp/repair-full.exit`, JSON `tests/tmp/repair-full-coverage.json`.
До терминального результата не считать его пройденным. Live PG/Qdrant,
Linux/backup/load gates открыты. Локальный `MAIL_IMPORT_ENABLED=true` сохранён;
live-данные не ремонтировались.

## Контракт

Во время parse/LLM/index нового поколения активные SQL-строки, файлы и поисковые
точки старого поколения остаются доступны. Новое поколение имеет отдельный ID,
каталог и point IDs. После успешной записи файлов и Qdrant одна SQL-транзакция
заменяет canonical rows и активный ID. Сбой до commit сохраняет прежний результат;
сбой после commit допускает только повторяемую очистку прежнего поколения.

## Порядок реализации

1. Добавить additive-таблицы `document_generations` и
   `document_generation_states`. State содержит active/candidate IDs; legacy
   документ без state читается как прежде. Begin/ready/publish/abandon
   сериализуются блокировкой строки Document. Publish участвует в той же
   транзакции, что и замена sources/chunks/concepts/attachments, и проверяет
   базовое поколение и текущего кандидата. Purge удаляет обе таблицы явно также
   на SQLite без FK enforcement.
   Внутри publication-транзакции сначала вызывается `publish_generation`
   (блокирует Document), затем `replace_*`; обратный порядок создаёт риск
   инверсии блокировок с отменой/другими операциями документа.
2. Создавать вложения и bundle в `generations/<generation_id>/` внутри
   проверенного root документа. Сохранённые source paths остаются относительными.
   Чтение active bundle использует state. Cleanup разрешён только для retired
   или abandoned поколения, никогда для active/candidate; symlink/reparse
   points отклоняются. Root upload остаётся исходным файлом.
3. Добавить generation ID в Qdrant payload и детерминированные point IDs.
   До SQL-публикации новые points исключаются search-фильтром по состояниям
   поколений; retired/abandoned также исключаются до очистки. Legacy points
   исключаются для документов с опубликованным поколением до завершения их
   очистки. Hydration сверяет generation ID с canonical active ID, чтобы запрос,
   начавшийся до переключения, не соединил старые точки с новым текстом.
4. Переделать regenerate, staging/resume и finalize: новый candidate без
   удаления active; подготовка files/index; ready; единый SQL commit; cleanup.
   Startup сохраняет незаконченный candidate для resume. Cancellation удаляет
   только неактивную попытку. Отдельно проверить tag/locale/rename/trash races.
5. Приёмка: сбои на каждом переходе, restart до/после commit, два конкурентных
   запуска, неудачная очистка, смена parser version, legacy документы,
   PostgreSQL+Qdrant и Windows/Linux FS. До этого не объявлять безопасную
   регенерацию готовой.

## Почему не backup поверх старых путей

Восстановление копии после ошибки не сохраняет доступность старого результата
во время работы и оставляет окно рассинхронизации файлов/SQL/Qdrant. Immutable
generation artifacts и SQL-публикация обеспечивают проверяемую границу
переключения; Qdrant остаётся восстанавливаемой поисковой проекцией.

## Проверенные точки интеграции (исходное состояние до доработки)

- `Pipeline.regenerate` удаляет Qdrant, bundle, attachments и canonical rows
  до parse. Этот порядок заменяется созданием candidate; прежние тесты
  `test_regenerate_wipes_chunks` описывают старое поведение и должны быть
  заменены проверкой сохранности до публикации.
- `_process` уже использует `.attachments-attempt-*`, но
  `_publish_attachment_attempt` переносит его в активный `attachments` до LLM.
  Целью переноса должен стать только каталог candidate generation.
- `_finalize` сейчас делает bundle swap, затем SQL commit, затем Qdrant upsert.
  Для нового контракта порядок: файлы candidate → generation-scoped Qdrant →
  ready → SQL publication. Обновления `parser_version`, warnings, problem,
  counters и итогового статуса входят в publication-транзакцию, а не отдельный
  поздний `registry.update`.
- `StagingStore` хранит один checkpoint на документ. Ему нужен generation ID,
  чтобы restart/resume отличал preparing candidate, ready candidate и уже
  опубликованный checkpoint. В generation manifest сохраняются parse metadata
  (sources/attachments/warnings/version/hash), чтобы ready можно было
  опубликовать после restart без повторной записи его файлов.
- Resume при paused до публикации продолжает тот же candidate. Resume
  опубликованного `llm_partial_result` создаёт новый candidate, переносит
  пригодные checkpoints и повторяет только partial chunks. Старый partial
  результат остаётся активным до нового успешного переключения.
- `index_document` для dedup и автоматическое присоединение development сейчас
  вызываются ещё при parse. Производные результаты новой попытки нельзя
  публиковать раньше canonical content; перенести применение в финализацию.
  Пользовательские tag/development/locale edits перечитываются перед публикацией.
- Source download уже принимает относительный путь от root документа;
  generation paths ему подходят. Плоский attachment endpoint и экспорт bundle
  должны разрешать только активные зарегистрированные файлы. При экспорте
  `sources.json.saved_path` перебазируется в `attachments/<basename>`, иначе
  manifest будет ссылаться на отсутствующий каталог generations внутри ZIP.
- Hydration выполняет несколько SQL SELECT. Проверки одного active ID перед
  этими SELECT недостаточно при concurrent publication: нужен общий snapshot
  либо блокировка читаемых Document rows на время чтения active ID и canonical
  rows. Старые Qdrant hits не должны получить текст новой версии с тем же slug.
  Это отдельный concurrency-тест до включения generation search.

## Состояние реализации

Реализованы additive-схема, сериализованные переходы, защита файловых каталогов
и generation-aware поиск. Qdrant IDs изолированы по generation ID (legacy IDs
сохранены), фильтр до ранжирования исключает preparing/ready/retired/abandoned
и прежние legacy points после публикации. Hydration повторно сверяет active ID
и читает canonical text согласованно: SQLite snapshot, PostgreSQL shared
Document locks. Конкурентная публикация проверена на обоих SQL backend.

Последний связанный Windows прогон: 150 passed / 1 skipped; после добавления
проверок missing/null legacy payload и заполнения top-k кандидатами отдельный
search suite: 11 passed. PostgreSQL: 2 hydration tests passed в отдельных
временных схемах, схемы удалены. Linux search suite не запускался: автоматическое
согласование Docker-команды завершилось ошибкой авторизации 401. Предыдущий
полный backend прогон потерял exec handle до получения итогового результата;
его нельзя считать успешным.

Подключён основной путь `regenerate`/`_process`/`_finalize`: вложения и bundle
пишутся в candidate directories; после generation-scoped Qdrant upsert и
очистки лишних точек кандидата сохраняется `publication.json`, поколение
становится ready, затем canonical rows/active pointer/итоговые поля документа
публикуются одной SQL-транзакцией. Staging хранит generation ID (миграция
`ff0b1c2d3e4f`); ready resume читает подготовленный manifest без parser/LLM.
Dedup signature и результат определения разработки тоже применяются только
при публикации. Ручные изменения языка и тегов перечитываются перед commit;
при необходимости обновляются только Qdrant points кандидата.

Скачивание вложений опубликованного поколения разрешает только зарегистрированный
активный путь. Экспорт перебазирует source paths в `attachments/<basename>`.
Фоновые chunk/sparse/relation backfill и синхронизация concept tags используют
active generation ID. Read-path не запускает повторный парсинг опубликованной
пустой версии.

Подтверждено: 157 связанных backend tests passed; отдельные итоговые 12
generation-pipeline tests passed; generation/search/backfill/tag group —
73 passed; migration/staging schema — 5 passed. Ruff и diff check проходят.
Последний live ALTER для staging.generation_id не подтверждён: соединения с
локальным стеком стали завершаться тайм-аутом, ожидавший процесс прерван.

Остаются обязательными: повторяемая очистка retired/abandoned/legacy artifacts;
полная проверка конкурентных regenerate/tag/rename/trash и post-commit restart;
согласованность export/source read при публикации; проверка целостности ready
артефактов; адаптация standalone maintenance/reindex scripts; полный backend
прогон, реальный PostgreSQL+Qdrant lifecycle и Linux/backup/load gates. Основной
путь подключён, но безопасная регенерация ещё не объявляется завершённой.


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

## 2026-09-26: legacy backfill и проекция тегов bundle

Предыдущий полный backend run 1157 завершён: 1847 passed, 7 skipped, 2 failed
(tests/tmp/generation-cleanup-full.log). Это snapshot до integrity изменений.
Первое падение — устаревший monkeypatch export_okf.get_registry: экспорт читает
Document в собственной согласованной SQL-сессии, зависимость удалена. Убрана
только эта подмена; assertions source tree/source IDs сохранены и прошли.
Второе — schema drift процесса, загрузившего модели до publication_hash и
увидевшего новую миграцию уже во время прогона. Свежий test_schema_drift прошёл.
Это не итоговое подтверждение всей backend suite текущего snapshot.

Lazy _backfill_chunks больше не парсит в live attachments. Парсинг идёт во
временном staging-каталоге, с supervisor для реального parser при включённом
режиме. Перед записью под Document write lock повторно проверяются deleted/missing,
active/candidate generation и уже записанные chunks. Если состояние изменилось,
результат и временные файлы отбрасываются. Legacy files добавляются исключительно
при отсутствии, одинаковые существующие байты сохраняются, конфликт SHA-256
запрещает backfill без перезаписи. Source paths перебазируются после переноса.

Ruling: при SQL failure допустимы новые полные файлы без canonical rows —
повторная попытка принимает те же bytes; не удалять их после освобождения SQL
lock, чтобы rollback-cleanup не удалил файл, уже использованный другим writer.
Частично скопированный файл удаляется в обработчике copy failure под lock.
Существующие bytes не заменяются; destructive repair требует regenerate.

Доказательства: первоначальные 3 backfill regression tests failed (red log),
затем backfill/mail/export/schema 20 passed. Расширенная backfill/files/pipeline/
mail группа 73 passed, 1 skipped (generation-backfill-related.log); после добавления
SQL failure/retry/source-path case все 8 backfill tests passed (backfill-retry-final.log).
Проверены concurrent publication, появление candidate/chunks, trash, purge,
повторный вызов для published version, конфликт/идентичность файлов и retry.

Tag payload sync удерживает canonical read lock до записи Qdrant, читает текущие
Document tags внутри той же сессии; deterministic test старого worker после
новой публикации прошёл. Bundle frontmatter также больше не применяет устаревшую
delta: под Document write lock выбирает active bundle, читает canonical tags,
удерживает lock до os.replace. Тело .md читается/пишется bytes без преобразования
переносов строк. Два новых теста сначала показали устаревшие tags и удаление
каталога между чтением и записью; после исправления metadata/document-tags группа
32 passed (bundle-projection-green.log). Frontmatter — производная проекция;
прямой экспорт продолжает читать canonical БД.

Scoped diff check и полный backend Ruff проходят. MAIL_IMPORT_ENABLED=true
в локальном .env сохранён. Live stack/schema не менялись, commit/push не делались.

Дальше: атомарность document/concept tag edit (сейчас две транзакции), source_locale
и development projections, late worker writes после purge, maintenance scripts
reindex/backfill и финальные gates исходного mail-import плана. Live PostgreSQL
колонки staging.generation_id/publication_hash всё ещё требуют подтверждения;
реальные PG/Qdrant, Linux, backup/restore и нагрузка не закрыты. Goal остаётся active.

## 2026-09-26: атомарные теги, отмена удаления, locale/dev projections

DocumentRegistry.update(tags=...) теперь рассчитывает delta после Document lock
и обновляет document_tags + okf_concepts.tags в одной транзакции. Из сервиса
удалена отдельная поздняя запись concept delta. Два RED случая показали частичный
commit при SQL failure и возврат старого тега после следующей правки; GREEN
registry/tag/publication group: 49 passed (tag-atomic-green.log).

Source locale и development payload sync перечитывают канонические значения
внутри Document write lock и удерживают его до Qdrant ack. Все пути, включая
reindex_development_documents, используют тот же путь. Устаревшие значения в
аргументах служат только совместимости старых вызывающих функций. Отсутствующий
или удалённый документ возвращает False без записи. Три RED случая (публикация
во время locale/dev sync и запоздалый locale value) исправлены. Metadata/dev/
locale/document tags group: 55 passed (metadata-projection-green.log).

_stop_task больше не разрешает удаление после timeout живого Future. Сохраняет
abort signal и владение worker, выдаёт processing_stopping (локализовано ru/en),
повтор после реального завершения разрешён. Очистка/корзина/restore сериализованы
со _start; admission повторно проверяет missing/deleted до reset staging/submit.
Обычный remove теперь удаляет canonical row до physical cleanup. Шесть RED случаев
исправлены; deletion/trash/pipeline group: 71 passed (generation-delete-green.log).
Дополнительный RED показал зависший queued после отмены уже завершившегося worker;
_run finally теперь сохраняет paused для interrupted in-flight status. Deletion+
error codes: 35 passed (cancel-status-green.log); frontend i18n: 29 passed.

Ruling: если worker не завершился за 2 секунды, destructive action отклоняется
до повторной попытки. Это сохраняет файлы/БД; ограничение предпочтительнее удаления
с возможными поздними writes. Автопурж уже повторяет неуспешные документы позже.
Локальная _start_lock — часть задокументированного single-process Pipeline;
межпроцессное управление активными workers этим не вводится.

Полный backend Ruff и scoped diff check проходят. Live probe 127.0.0.1 ports
5432/18000/16333/16300 вернул 10035 после timeout; это не доказательство остановки
сервисов. Live migrations/рестарт/данные не менялись.

Объединённый прогон tests/test_pipeline_integration.py test_generation_pipeline.py
 test_generation_deletion.py test_generation_metadata.py test_document_tags.py
 test_dev_sync.py test_source_locale.py test_trash.py запущен как exec 25152,
log tests/tmp/generation-metadata-delete-final.log, exit в одноимённом .exit.
Последняя проверка write_stdin подтвердила running; дождаться терминального итога,
не перезапускать. Позднее дополнение task.done() отличает TimeoutError завершившегося
worker от тайм-аута ожидания, требует финальной проверки.

Следующий участок подтверждён чтением кода: reindex.py/rebuild_qdrant_v2.py
отбирают только done, не передают generation_id/source_id и нумеруют chunks
заново. Нужен общий canonical reindex путь, сохраняющий active generation,
последний опубликованный результат при failed/paused новой попытке, source tree
и исходные chunk_index. Далее maintenance backfills/flat readers, полная финальная
suite, реальные PG/Qdrant, Linux, backup/restore, нагрузка. UI review и live schema
staging.generation_id/publication_hash остаются непроверенными. Не считать общий
план завершённым; локальный MAIL_IMPORT_ENABLED=true сохраняется.

Итог объединённого прогона 25152: 142 passed, 92 warnings in 140.53s;
лог generation-metadata-delete-final.log. Это изолированные SQLite/local Qdrant
проверки перечисленных сценариев, не live PostgreSQL и не полная backend suite.

## 2026-09-26: canonical maintenance scripts

`canonical_reindex.py` теперь общий путь для `reindex.py` и `rebuild_qdrant_v2.py`.
Читает опубликованные SQL-концепты/чанки под generation read lock до конца
индексации. Сохраняет generation_id, source_id и реальные chunk_index, включая
пропуски нумерации; включает опубликованные документы при paused/failed новой
попытки. Trash и пустые первые кандидаты исключаются. V2 build копирует Settings
и запрещает удаление коллекции, уже настроенной как active. Полный rebuild —
offline maintenance с остановленными writers, не online snapshot всего корпуса.
Parity сравнивает ID/type/slug/chunk_index/source_id только активной версии,
игнорирует retired/candidate points; это не сравнение содержимого векторов.

`check_integrity.py` удерживает generation read lock при SQL/Qdrant/FS проверке,
проверяет active generation points, вложенные source paths и active bundle.
При paused/failed не сравнивает счётчики новой попытки с опубликованными строками.
Существующие chunk-файлы сверяются с canonical content; отсутствие всего бандла
по-прежнему допустимо. `backup_totals` наследует этот путь проверки.
RED: 9 reindex + 3 parity/active-target + 2 integrity failures. GREEN: 37 related
tests (tests/tmp/reindex-integrity-green.log), включая hydration после reindex.

`backfill_dedup.py` берёт канонические SQL-чанки под Document write lock,
сохраняет mail_fingerprint, пишет file/content/LSH в одной транзакции и после
commit пересчитывает duplicate badges. Повторно проверяет missing/trash после
ensure_chunks. RED: 2 failures (старый flat bundle, отсутствие нового bundle).
GREEN: 27 dedup tests; расширенный maintenance group также 27 passed
(generation-maintenance-final.log), включая удаление и SQL rollback.

`backfill_chunks.py` проверяет SQL и generation state вместо flat FS-каталога.
Заполняет только отсутствующие legacy chunks через защищённый ensure_chunks.
`--force` отклоняется с кодом 2 до изменений: старая команда удаляла файлы,
оставляя прежнюю БД; замена опубликованного текста требует regenerate.
RED: 4 failures. GREEN: 21 chunk/backfill/integrity/backup tests
(tests/tmp/chunk-cli-green.log).

`backfill_attachment_tags.py` режет reparsed text по source boundaries,
сравнивает content и source_id внутри Document write lock и использует исходные
chunk_index. Парсинг во временном каталоге, с mail mode и supervisor limits.
Qdrant projection перечитывает текущую версию под read lock до ack, адресует
generation point IDs. Повторный запуск восстанавливает payload даже если тег
уже есть в SQL; ошибка Qdrant возвращается как error, не успех. RED: 4 failures.
Первые 5 tests прошли; расширенная проверка failure/retry/dry-run/publication
идёт в maintenance-source-final.log (exec 6726), результат ещё нужно зафиксировать.

Full backend Ruff и scoped diff check прошли. MAIL_IMPORT_ENABLED=true сохранён.
Runtime inspection через Get-NetTCPConnection в sandbox запрещён (Access denied);
наличие postgres/node/python процессов не доказывает доступность нужного стека.
Запущен ограниченный read-only TCP/HTTP probe вне sandbox (exec 25205), дождаться
терминального результата. Live schema/restart/user data не менялись.

Остаются: backfill_comment_concepts, fix_attachment_paths и legacy backfill_db_store;
проверка общей формулы attachment coverage между pipeline и maintenance; финальная
suite/coverage и frontend gates, реальные PG/Qdrant, Linux, backup/restore и нагрузка.
Опубликованные метаданные/файлы нельзя ремонтировать старой flat-bundle записью.
План и goal остаются незавершёнными; commit/push/deploy не выполнялись.

Терминальный итог exec 6726: **160 passed, 8 warnings in 91.59s**,
tests/tmp/maintenance-source-final.log. Группа включает source_location/OKF:
регрессии общего слова «сообщение», title-only «Срок оплаты» и отрицания
«не согласовано», inferred status и запрет сохранения предположения как exact.
Также покрыты Qdrant failure/retry, dry-run, новая публикация между SQL tag edit
и projection, source boundaries root→child→root. Exec 25205 завершён: TCP probe
5432/18000/16300/16333 timeout (10035), HTTP URLError для трёх endpoints, в том
числе вне sandbox. Это сетевой blocker live-проверки, не доказательство причины.
Frontend node --test: **185 passed**, maintenance-frontend-test.log. ESLint идёт
exec 94512, maintenance-frontend-lint.log; дождаться окончания.

ESLint exec 94512 завершён: exit 0, **0 errors, 35 warnings**. Предупреждения
не объявляются исправленными. Все упомянутые выше test/probe sessions завершены.
Frontend build, полный backend coverage и live/runtime gates остаются впереди.

## 2026-09-26: historical migration и единый attachment coverage

`backfill_db_store.py` теперь ограничен историческим корпусом: done, вне корзины,
без active/candidate generation и без DocumentSource. Все три write entrypoints
(chunks, attachments, generated_at) получают Document write lock до проверки
этого условия и держат его до commit. --force не отменяет границу legacy.
Qdrant fallback читает только точки без generation_id. Конфликтующие bytes
вложения отклоняются; новые файлы добавляются через общий merge_legacy_backfill_files.
Повтор после SQL failure принимает идентичные файлы, не перезаписывает старые.

Fallback re-parse выполняется в приватном TemporaryDirectory, с ParseContext,
mail mode/supervisor limits. Чанки режутся по source boundaries; source tree,
attachment metadata и chunks пишутся одной транзакцией, пути перебазируются
на опубликованные legacy files. Dry-run и parse failure не публикуют bytes/rows.
Исторический Qdrant fallback всё ещё ограничен сохранённым payload (текст может
быть обрезан); полноценный оригинал предпочтителен для regenerate. Это не
универсальный ремонт опубликованной версии — для него нужен новый generation.
RED: 5 reproduced failures. GREEN: 11 migration tests, затем 23 migration/lazy
backfill tests (tests/tmp/legacy-migration-final.log), включая private parse,
dry-run/failure и legacy-only Qdrant filter.

В pipeline воспроизведена отдельная ошибка attachment coverage: число flat
chunks совпадало с source-separated chunks, но границы не совпадали. Корневой
абзац получал attachment: [True, True, False] вместо [False, True, False] при
стандартном пороге 0.9 (tests/tmp/pipeline-coverage-red.log). Теперь
attachment_shares_by_source использует те же последовательные source sections,
что chunk_blocks_by_source; pipeline и tag backfill используют общий расчёт.
Legacy root-only adapters с markdown без blocks сохраняют свой путь.
GREEN: 21 source/attachment/mail pipeline tests (source-coverage-green.log).

Расширенный pipeline/generation/mail/migration run выполняется: exec 27510,
tests/tmp/legacy-source-related.log. До терминального вывода его результат
не считать успешным. Full backend Ruff и scoped diff check прошли.
MAIL_IMPORT_ENABLED=true проверен, live schema/data не менялись.

Следующая зависимость для backfill_comment_concepts и fix_attachment_paths:
общий путь offline canonical repair через новое поколение. Нельзя ограничиться
generation-scoped point IDs при записи canonical text в прежней версии: сбой
между SQL/Qdrant/FS нарушит контракт. Нужны snapshot canonical rows/provenance,
private copy зарегистрированных файлов, candidate bundle и полный индекс,
publication manifest/hash, затем существующая единая публикация и durable cleanup.
Ремонт комментариев обязан сохранить source_spans/provenance остальных концептов,
искать уникальный якорь только в соответствующем source_id и не удалять все
концепты из-за глобального user tag review. Ремонт путей обязан обновлять
char_count/hash, пересчитывать затронутые source_spans и обе ветки embeddings.
Эти два скрипта ещё не исправлены; общий план не закрыт.

Exec 27510 завершён: **97 passed, 56 warnings in 92.02s**,
tests/tmp/legacy-source-related.log. Включает полный test_pipeline_integration,
generation pipeline, mail pipeline, source chunking, attachment backfill и legacy
migration. Это не полный backend suite и не live PG/Qdrant. Намеренно запущенных
проверок больше нет; все sessions текущего продолжения терминальны.

## 2026-09-26: shared publication и canonical repair foundation

SQL-публикация выделена в generation_publication.publish_prepared_document.
Pipeline._publish_prepared_generation делегирует ей verified files/points,
replace sources/chunks/concepts/attachments и Document fields; checkpoint cleanup
остаётся отдельно в Pipeline после commit. Повтор активного generation возвращает
False без повторной записи. Fault-injection tests перенесены на фактический
publication module, прежние assertions сохранены. Два новых service tests прошли;
generation/pipeline/cleanup/integrity/metadata группа: 45 passed
(publication-service-green.log, exec 81441 terminal).

Добавлен canonical_repair.repair_published_document для offline maintenance.
Трансформация работает с полным SQL snapshot; можно менять chunks/concepts,
источники/артефакты заменять нельзя. Начало требует done без текущего candidate;
dry-run/no-op не создают generation/files/points. Резервирование candidate
коммитится до создания файлов, поэтому aborted work остаётся discoverable.
Создание каталогов под writer lock; private files/index готовятся под shared
publication lock, чтобы старую версию можно было читать при медленном embedding.
Ready и публикация получают writer lock отдельно. Bulk repair предполагает
остановленные application writers, а hash canonical rows дополнительно защищает
от stale proposal между snapshot/prepare/publish.

Копируются исключительно зарегистрированные source/attachment paths активной
версии, без symlink/reparse/traversal. Проверяется сохранённый SHA-256 вложения
и SHA-256 обеих копий. Все пути нового поколения перебазируются, включая
source manifest вложенных путей. Концепты сохраняют source_spans и
generated_at/model_id/prompt_version; attachments сохраняют content_type,
processed_at/extracted_chars/error. Индексация включает обе ветки с реальными
chunk_index и generation/source IDs, ожидаемые IDs вычисляются независимо от
результата index_* и проверяются до ready и перед SQL commit. При правке чанков
пересчитываются hash/char_count/dedup signature с сохранённым mail_fingerprint.
После commit запускается retryable cleanup; его сбой не отменяет публикацию.

RED: 3 отсутствовавших repair сценария; затем обнаружено принятие файла с
изменённым относительно recorded hash содержимым. Исправлено. Отдельное падение
теста noncontiguous indices было ошибкой его подготовки (все SQL chunks ставились
в один index), исправлена фикстура с сохранением проверки точных IDs/файлов.
GREEN: 52 related tests (canonical-publication-final.log, exec 55519 terminal).
После перехода длительной подготовки на shared lock: 7 repair tests passed
(canonical-repair-shared-read.log, exec 74156 terminal). Полный backend Ruff и
scoped diff check проходят; MAIL_IMPORT_ENABLED=true сохранён.

Пока это foundation: backfill_comment_concepts.py и fix_attachment_paths.py
ещё не используют новый сервис. Следующий шаг — подключить оба скрипта и
регрессии на source ownership/неоднозначные якоря, global review tag, идемпотентность,
сохранность unrelated concepts и перерасчёт spans после правки путей. Старые
flat-bundle test fixtures нужно перевести на canonical SQL и active bundle path,
не ослабляя проверки полноты. Full backend coverage/frontend build/live PG+Qdrant/
Linux/backup/load gates открыты. Live data/schema/restart/commit/push не выполнялись.

## 2026-09-27: UX отмены обновления и сохранения опубликованной версии

Пользователь одобрил два действия: «Отменить обновление» во время regeneration
и «Оставить предыдущую версию» после её сбоя. Для первой загрузки действия нет.
После успешной публикации возврат к более ранней версии не предусмотрен.

DocumentUpdateAttempt хранит снимок опубликованных счётчиков/предупреждений и
durable cancel_requested. Отмена и публикация используют общий Document writer
lock: победившая отмена запрещает позднюю публикацию. Каждая новая regeneration
получает новый update_id; resume сохраняет его. Старый диалог не может отменить
новую попытку. editor/admin API и аудит записывают отмену одной транзакцией.
Worker сначала останавливается, затем восстанавливаются status=done и прежние
поля, удаляются candidate/checkpoints; опубликованные файлы, SQL-концепты и
поисковые точки остаются доступны. Startup и periodic cleanup повторяют
незавершённое восстановление. Ручные изменения тегов/языка не откатываются.

Список показывает «Обновляется», доступность прежней версии и нужную кнопку;
после запроса — «Отменяем обновление…». Подтверждение использует обычный Modal.
Страница концептов продолжает polling отмены даже после failed/error. Тексты
обычной и массовой regeneration больше не утверждают, что старая версия удаляется.
Старое предупреждение неполноты сохраняется; checkpoints выборочной догенерации
старой версии не восстанавливаются, для её исправления может понадобиться полная
regeneration.

Browser acceptance на изолированном SQLite/Qdrant-stub стенде: отмена в ходе
индексации и отказ от failed update вернули «Готов»; исходный Published decision
остался доступен на реальной странице концептов. Скриншоты:
tests/tmp/cancel-update-running.png, cancel-update-failed.png,
cancel-update-restored.png. Frontend: 216 tests passed, production build passed;
backend: 86 generation/API/audit tests и 9 queue/schema tests passed; Ruff passed.
Регрессии проверяют позднюю публикацию, queued cancellation,
restart recovery, audit rollback, stale tokens, старые interrupted generations,
сохранность published data и manual metadata. Повторный review двух исправлений
(token rotation и detail polling) существенных оставшихся замечаний не выявил.

Рабочий backend не перезапущен: на момент проверки один документ генерировался,
ещё четыре стояли в очереди. Новые API/table будут доступны после безопасного
перезапуска backend; development create_all создаёт новую таблицу, production
использует additive Alembic migration 020b1c2d3e4f. Live data/schema не менялись,
commit/push не выполнялись.

Финальный общий backend run: 2346 passed, 23 skipped, 2 failed
(tests/tmp/cancel-update-backend-full.log). Оба падения — старые Pipeline.__new__
test doubles без settings/SQL registry. Fixtures дополнены реальными settings и
изолированным DocumentRegistry; проверки конкуренции/abort не ослаблены.
Повтор всего test_pipeline_integration.py: 50 passed
(tests/tmp/cancel-update-integration-green.log). Общий suite после этой правки
только тестовых fixtures не запускался повторно; runtime правки дополнительно
проверены указанными выше 95 tests. Все запущенные проверки завершены;
изолированные acceptance backend/frontend остановлены. ESLint: 0 errors,
36 прежних warnings. Финальные Ruff и git diff --check прошли.
