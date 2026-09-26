# Outlook Mail Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use superpowers:subagent-driven-development only if the user chooses delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ручная загрузка EML/MSG и разбор писем внутри DOCX/XLSX/PDF с рекурсивными вложениями, точным происхождением, диагностикой неполноты и проверкой полного пользовательского пути.

**Architecture:** Одна загрузка остаётся одним Document. Общий парсер создаёт дерево источников, отдельные чанки по источникам и почтовые метаданные; SQL хранит канонический текст и связи, Qdrant остаётся поисковой проекцией. Предварительный разбор и фоновая обработка используют одинаковый ограниченный parser runtime.

**Tech Stack:** Python >=3.12, stdlib email, существующий olefile, FastAPI, SQLAlchemy/Alembic, PostgreSQL 17/SQLite, Qdrant, Next.js/React, pytest и node:test. MSG backend проходит gate задачи 1 до добавления новой зависимости.

**Spec:** [Проект устройства функции](../specs/2026-09-25-outlook-mail-ingestion-design.md).

Статус: реализация и приёмка выполняются по поручению пользователя «выполняй план автономно, останавливайся только если есть открытые вопросы». Исходный план составлен 25.09.2026; текущие доказательства и ограничения — в [отчёте приёмки](../reports/2026-09-25-mail-ingestion-acceptance.md). Незаполненные checkbox требуют сверки с доказательствами, а не автоматического повторного выполнения. Числа лимитов и производительности ниже — критерии приёмки, не результаты измерений.

Уточнения пользователя имеют приоритет: общая видимость загружаемого письма принята, отдельное подтверждение публикации всем и отдельный ACL не нужны. Локальный `MAIL_IMPORT_ENABLED=true` сохраняется до появления production; автоматически выключать его по исходной формулировке rollout gate нельзя. Коммит, push и production rollout этим поручением не запрошены.

## Global Constraints

- Один загруженный файл остаётся одним `Document`, единицей прав доступа, тегов, разработки, корзины и экспорта.
- Корень 0; уровни 1–2 разбираются; 3+ явно пропускаются.
- Вложение 50 MiB; суммарные декодированные вложения 200 MiB; 1000 MIME/MSG nodes; 2 000 000 Unicode-символов текста на корень.
- Parse/preview: 120 секунд, 1024 MiB на дочерний процесс, одновременно 2 процесса на backend.
- Существующие `max_upload_mb` и `ArchiveLimits` не увеличивать.
- Без Outlook/COM, PST/OST, Graph, OCR, внешних сетевых загрузок и автоматического обхода почтового ящика.
- Не вырезать цитируемую историю и подписи эвристикой.
- SQL — источник канонического текста; точная цитата допустима только при проверенных offsets/hash.
- Не менять модель видимости: вложение наследует эффективную видимость root; адресаты письма не ACL.
- Сохранить чужие staged/unstaged-изменения. Без запроса не делать worktree, commit, push и массовую перегенерацию.
- Тестовые данные синтетические/обезличенные; частные письма не добавлять в git, CI и внешние LLM-прогоны.
- Не вводить GPL-зависимость автоматически. Gate задачи 1 должен закрыться до MSG-интеграции.

## Review Focus

1. MSG внутри DOCX может быть OLE-объектом, а MSG внутри MSG — substorage без самостоятельных исходных байтов: задачи 1, 4, 5 и тест M08.
2. «Согласовано» с процитированной историей не должно потерять предмет согласования: задачи 3, 8 и тест S03.
3. Одинаковый Message-ID с другим вложением не является дублем: задача 9 и тест D03.
4. Предварительная проверка upload уже парсит файл до постановки в очередь: задачи 2, 7 и тесты L08/L09.
5. Новые метаданные не должны выдать неверную подсветку или пройти мимо прав root при скачивании: задачи 6, 8, 10 и тесты P04/A03.

## Порядок и точки остановки

`1 → 2 → 3/4 → 5 → 6 → 7 → 8 → 9 → 10 → 11 → 12`.

Задачи 3 и 4 можно вести независимо после фиксации контрактов, но данный план не запускает агентов. Сначала доказать MSG/OLE на реальных обезличенных образцах и выбрать backend; затем общий бюджет и дерево. UI подключается после устойчивого backend-контракта.

Точки остановки: неподходящая лицензия/несовместимый MSG backend; отсутствие обязательных fixture; нарушение лимитов; неоднозначная атрибуция; утечка через download; неполная миграция. Нельзя закрывать такую точку снятием теста или объявлением любого бинарника «успешно сохранённым».

## Общие контракты

Новые типы в `doc-parser/src/docparser/source_model.py`; `SourceNode.metadata` — JSON-совместимый словарь с полями из spec. В конечной реализации использовать dataclass/TypedDict вместо произвольного изменения словарей в разных слоях.

```python
@dataclass
class ParseWarning:
    code: str
    source_id: str
    detail: str                 # фиксированная безопасная диагностика

@dataclass
class SourceNode:
    source_id: str
    parent_source_id: str | None
    ordinal: int
    kind: str
    display_name: str
    metadata: dict
    saved_path: str | None      # только относительный путь в результате
    extraction_status: str
    artifact_kind: str
    container_source_id: str | None = None
    container_locator: str | None = None

@dataclass
class ParseResult:
    blocks: list[Block]
    sources: list[SourceNode]
    warnings: list[ParseWarning]
    parser_version: str

def parse_document_result(path, filename=None, attachments_dir=None,
                          depth=0, budget=None) -> ParseResult: ...

def render_source_sections(result: ParseResult) -> list[tuple[str, str]]: ...
# [(source_id, canonical_markdown)], структурный порядок; повторные секции
# одного source_id допустимы, объединять через чужой источник нельзя.

def chunk_source_sections(sections, max_chars: int) -> list[dict]: ...
# [{chunk_index, source_id, content, content_hash, section_title, char_count}]
```

Сигнатуры с `...` выше — декларации интерфейсов, не предлагаемые заглушки реализации. Для конечных `SourceNode` дополнительно нужны media_type/fingerprint/parser_version/warnings из spec. На wire — JSON с `schema_version=1`; не использовать pickle между worker и backend. `parse_document` продолжает возвращать прежний `list[Block]`.

## Задача 1. Корпус примеров и выбор MSG backend

**Files:** Create `doc-parser/tests/mail_fixtures.py`, `doc-parser/tests/fixtures/mail/manifest.json`, `doc-parser/tests/test_mail_provider_contract.py`, `docs/MAIL_FORMAT_SUPPORT.md`. Modify `doc-parser/pyproject.toml`, `THIRD_PARTY_NOTICES.md` только после выбора зависимости.

**Interfaces:** Создать `mail_model.py` с `MailEnvelope` (subject, sender, to, cc, sent_at_raw, sent_at_utc, message_id, references, in_reply_to, body_plain, body_html, warnings) и `MailPart` (ordinal, original_name, content_type, declared_size, stream либо embedded-message handle). `mail_provider.py`: `read_eml(path, context)` и `read_msg(path, context)` возвращают envelope и ограничиваемый iterator частей. Provider сам не пишет произвольные пути и не обходит рекурсию в обход context.

- [ ] Зафиксировать исходные `git status --short`, diff имён и baseline существующих parser/backend/frontend checks из раздела команд. Отдельно записать существующие сбои.
- [ ] Создать 12 обязательных fixture: Unicode MSG, ANSI MSG, HTML-only, RTF-only, EML alternative, inline CID, embedded EML, embedded MSG substorage, MSG OLE в DOCX, MSG в ячейке DOCX, письмо в PDF, DOCX→MSG→XLSX. Имена/текст синтетические; для бинарных fixture — происхождение, SHA-256, ожидаемые узлы и права распространения в manifest. Реальные образцы до обезличивания остаются локально.
- [ ] Написать один provider-contract для всех кандидатов. Пример проверки против подготовленного бинарного fixture:

```python
def test_embedded_msg_keeps_container_and_child(mail_case):
    result = mail_case.parse("msg_with_embedded_msg")
    child = mail_case.source(result, "inner-message")
    assert child.parent_source_id == "root"
    assert child.artifact_kind == "container_only"
    assert child.container_source_id == "root"
    assert child.metadata["subject"] == "Внутреннее согласование"
    assert "Решение 3509" in mail_case.text(result, child.source_id)
```

- [ ] Проверить выбранную версию extract-msg на Windows/Python 3.12 и Linux/Python 3.12 в изолированной тестовой среде; исследовать лицензии прямых и транзитивных зависимостей, advisories и backend-only API без CLI/Outlook. Не добавлять её в продукт до зафиксированного решения по поставке.
- [ ] Если GPL-кандидат не принят, выполнить ограниченный собственный provider на `olefile` по MS-OXMSG: Unicode/ANSI strings, codepage, recipients, body/plain/HTML, attachment method 1 и 5, property streams корня и embedded message. RTF decompression/conversion квалифицировать отдельно на проверенной зависимости или ограниченной реализации; RTF-only без доказанного извлечения должен оставаться явно unsupported, а не давать успешное пустое письмо.
- [ ] Gate закрывается письменной записью в `MAIL_FORMAT_SUPPORT.md`: выбранный backend/версия, допустимая схема поставки, результаты 12 fixture, неподдерживаемые классы, точное поведение RTF-only. Если обязательные plain/HTML/embedded MSG не проходят — MSG не включать, работу по нему считать незавершённой. EML-подэтап можно проверять отдельно, но это не полное выполнение запроса.

**Tests:** Provider contract на настоящих бинарных MSG, не только mocks. Проверка отсутствия сетевых вызовов и сторонних исполняемых программ. Время задачи зависит от backend: квалификация 1–2 дня; собственный MSG-reader добавляет ориентировочно 5–10 дней и отдельные риски формата.

## Задача 2. Общий бюджет, безопасное сохранение и parser supervisor

**Files:** Modify `doc-parser/src/docparser/embedded.py`, `paths.py`, `archive_guard.py`. Create `parse_context.py`, `backend/app/services/parser_runner.py`, `doc-parser/src/docparser/worker.py`, `doc-parser/tests/test_parse_budget.py`, `backend/tests/test_parser_runner.py`. Modify `backend/app/config.py`, `.env.example`, `docs/LIMITS.md`.

**Interfaces:** `ParseContext` хранит source path, depth и единый budget. `budget.reserve_node()`, `budget.consume_bytes(n)`, `budget.consume_text(n)` поднимают `ParseLimitError(code)`; `safe_store(stream, source_id, media_type, context)` возвращает проверенный относительный путь. `run_parse(path, filename, output_dir) -> ParseResult` — общий вход backend.

- [ ] Написать тесты boundary−1/boundary/boundary+1 для depth, bytes, node count и текста; тестировать общий бюджет sibling-вложений и «тысяча пустых частей».
- [ ] Проверить failure до декодирования, когда declared size уже слишком велик; actual-byte cap ловит неверный declared size. Depth проверяется до unwrap, stream reads ограничены, остановка не вызывает последующее скрытое `.read()`.

```python
def test_siblings_share_budget():
    budget = ParseBudget(max_bytes=10, max_nodes=3, max_text_chars=100)
    budget.consume_bytes(6)
    with pytest.raises(ParseLimitError, match="size"):
        budget.consume_bytes(5)
```

- [ ] Сохранение делать по генерируемому ASCII-basename от source_id с проверенным расширением; исходное имя — только metadata. Проверить `..`, UNC, absolute POSIX/Windows, drive-relative, ADS `:`, CON/NUL, NUL-byte, одинаковые имена, bidi и длинные Unicode-имена. Ни один target не выходит из output_dir; запись не следует symlink/reparse-point.
- [ ] Учёт всех потоков: payload, opaque, CID/image, MIME decoding, MSG substorage. Считать декодированные байты один раз на ребро источника; wrapper overhead ограничить отдельно, чтобы не получить случайный двойной debit. Неизвестные объекты тоже ограничиваются.
- [ ] Реализовать supervisor без shell: `subprocess.Popen([sys.executable, "-m", "docparser.worker", ...])`; временные вход/выход только в workspace storage; JSON schema/размер ответа проверяются родителем. Worker не импортирует настройки БД/LLM, получает минимальный env без credentials. Закрывать процесс/handles на timeout/cancel/crash.
- [ ] Применить лимиты памяти ОС до чтения, timeout 120s и общий семафор 2 для preview/background. Не держать upload-admission-lock и транзакции SQL во время parse. При заполнении parse slots вернуть управляемый busy/429 с Retry-After, не бесконечно блокировать HTTP threadpool.
- [ ] Тесты worker hang/crash/invalid JSON/oversized output/memory exhaustion на обеих ОС. Fault injection доказывает завершение процесса и освобождение slot; timeout ожидания Future сам по себе тест не удовлетворяет.

**Deliverable:** Неподдерживаемый/опасный объект не пишет за пределы документа и не может бесконечно занимать backend. Контроль текста HTML/RTF нельзя заменить одним лимитом compressed input.

## Задача 3. EML, MIME и безопасная нормализация текста

**Files:** Create `doc-parser/src/docparser/eml_parser.py`, `mail_text.py`, `mail_model.py`, `mail_provider.py`, `tests/test_eml_parser.py`, `tests/test_mail_text.py`. Modify `parser.py`, `extensions.py`, `__init__.py`.

**Interfaces:** `read_eml` из задачи 1; `normalize_mail_body(envelope, context) -> list[Block]`; части отправляются в общий `process_embedded`, message/rfc822 — в mail parser с увеличением depth/source path.

- [ ] RED: UTF-8, CP1251, KOI8-R, RFC encoded headers, folded headers, RFC2231 filename, base64/quoted-printable, unknown charset/invalid bytes.
- [ ] Использовать `BytesParser(policy=policy.default)` с предварительными и процессными ограничениями; проверить defects на каждом узле. Отдельно обработать multipart/alternative, related, mixed, message/rfc822 и inline части без disposition.
- [ ] Выбирать один body, сохранять таблицы/ссылки/переносы, не размножать plain+HTML. Большие заголовки/parts ограничить до индексации. Заголовки не заменяют missing body.

```python
def test_approval_keeps_quoted_context(eml_bytes, parse_bytes):
    result = parse_bytes(eml_bytes(body="Согласовано.\n\n> Лимит: 12 дней"))
    text = "\n".join(block.text for block in result.blocks)
    assert "Согласовано" in text
    assert "Лимит: 12 дней" in text
    assert text.count("Лимит: 12 дней") == 1
```

- [ ] HTML→Markdown: только разрешённые элементы/схемы URL, без raw HTML, script/style/form/iframe и remote fetch. CID изображение связывать с текущим письмом; одинаковые CID у sibling-писем не конфликтуют. Вложенные ссылки и скобки экранировать; display name не должен создавать markdown-инструкции/заголовки.
- [ ] Date парсить в UTC только с достоверной timezone; raw value сохранить. Missing date/subject/sender, несколько From, некорректные addresses и Message-ID не валят весь документ.
- [ ] Full-body и quoting сохранять; S/MIME signed-body извлекать только если доступен, без заявления о проверенной подписи; encrypted — код `encrypted_mail`, оригинал остаётся доступен.
- [ ] GREEN и regression существующего `test_markdown.py`. Проверить, что preview и actual parse дают один и тот же текст и fingerprint независимо от output_dir.

## Задача 4. MSG и вложенные message objects

**Files:** Create `doc-parser/src/docparser/msg_parser.py`, `tests/test_msg_parser.py`. Modify `mail_provider.py`, `parser.py`, `extensions.py`, выбранные зависимости задачи 1 и notices.

**Interfaces:** `read_msg` возвращает те же MailEnvelope/MailPart, что EML. Nested substorage отдаётся как handle с ancestor/container locator, а не как объект, предполагающий готовые bytes.

- [ ] RED на Unicode/ANSI MSG, SMTP/EX адресах, plain/HTML, compressed RTF, attach method 1/5 и нестандартном message class.
- [ ] Реализовать backend adapter выбранного варианта; не использовать общую `save()` библиотеки для свободной записи всего дерева. Все чтения частей проходят через бюджет/сохранение задачи 2.
- [ ] Для встроенных messages сохранить правильный property context, включая named properties предка. Проверять глубину и node count до обхода substorages. Unsupported attachment method остаётся отдельным диагностированным объектом.

```python
def test_same_named_files_in_two_messages_keep_two_origins(msg_case):
    result = msg_case.parse("two_messages_with_report_xlsx")
    reports = [s for s in result.sources if s.display_name == "report.xlsx"]
    assert len(reports) == 2
    assert len({s.source_id for s in reports}) == 2
    assert len({s.parent_source_id for s in reports}) == 2
    assert len({s.saved_path for s in reports}) == 2
```

- [ ] Повреждённый property stream, неверная length, циклическая CFB chain, обрезанный файл, password/IRM, RTF bomb: управляемая ошибка/предупреждение; worker остаётся ограниченным.
- [ ] Сравнить поля и текст с ожидаемым manifest для реальных Outlook-generated fixtures; сравнение с другим парсером — дополнительный сигнал, не единственный oracle.
- [ ] Проверить отсутствие локального Outlook на Linux image. Root MSG скачивается побайтово; embedded substorage показывает ссылку на контейнер, а не несуществующий файл.

## Задача 5. Общий рекурсивный путь DOCX/XLSX/PDF → письмо → документ

**Files:** Modify `embedded.py`, `docx_parser.py`, `xlsx_parser.py`, `pdf_parser.py`, `pypdf_pdfium_provider.py`, `parser.py`. Create `source_model.py`, `tests/test_nested_mail.py`. Extend `tests/test_attachments.py`, `tests/test_archive_guard.py`.

**Interfaces:** `parse_document_result` и SourceNode/ParseResult из общих контрактов. Сохранить сигнатуру `parse_document`; context распространяется по всему дереву через внутренние вызовы.

- [ ] RED: raw MSG bytes, Outlook OLE/ProgID, OLE Package, корректно разобранный Ole10Native с filename/data boundaries; EML без надёжного расширения и generic binary с фальшивым `.msg`.
- [ ] Определять MSG по структуре CFB/MAPI, не по CFB magic alone: DOC/XLS тоже OLE. EML определять по MIME/headers + контексту, не по одной строке Subject. ProgID/filename — подсказки, не доказательство. Ошибочные и spoofed formats дают стабильный код.
- [ ] Исправить обход OLE в таблицах DOCX; включить вложенные таблицы и dedup обхода XML nodes/relationship occurrences. Инвентаризировать embedded relations в headers/footers/textboxes: доступные реальные байты обработать с пометкой местоположения, недоступный/linked object показать явно. Не скачивать external relationships.

```python
def test_docx_msg_xlsx_chain(nested_case):
    result = nested_case.parse("docx_msg_xlsx")
    report = nested_case.source(result, "Расчёт.xlsx")
    letter = nested_case.source(result, "Согласование.msg")
    assert report.parent_source_id == letter.source_id
    assert letter.parent_source_id == "root"
    assert "123456" in nested_case.text(result, report.source_id)
```

- [ ] Стабильный порядок источников: структурный порядок DOCX, порядок spreadsheet embedded entries, заданный стабильный порядок PDF attachments, MIME/MSG ordinal. Повторная ссылка на один бинарник остаётся явным вхождением, но extraction не повторяется без необходимости.
- [ ] Ошибка внутри одного attachment не отменяет siblings. При лимите depth сохранить допустимые bytes и warning; при size cap не сохранять oversized payload. Документ с одними маркерами не объявлять имеющим текстовый слой.
- [ ] Регрессии DOCX-комментариев и таблиц, XLSX, PDF provider, изображений и attachment tags. PDF-провайдер остаётся pypdf+pypdfium2, PyMuPDF не добавлять.

## Задача 6. Хранение дерева и additive-миграция

**Files:** Modify `backend/app/db/models.py`, `services/attachment_store.py`, `services/chunk_store.py`, `models/schemas.py`. Create `services/source_store.py`, `backend/alembic/versions/<generated_revision>_document_sources.py`, `backend/tests/test_document_sources.py`, `backend/tests/test_mail_migration.py`. Имя revision создаёт Alembic от текущего head при выполнении, не придумывать parent сейчас.

**Interfaces:** `replace_sources(session, doc_id, rows)`, `fetch_sources(doc_id)`, `fetch_source_paths(pairs)`; последний принимает `(doc_id, source_id)` и батчево возвращает цепочки. `replace_chunks` принимает nullable source_id. Не включать doc_id другого документа в ancestor-chain.

- [ ] RED на уникальность вхождений, parent/root constraints, повторные имена, контейнер без файла, отсутствующего предка и cross-document parent. Ацикличность валидировать до INSERT, self-reference FK alone недостаточен.
- [ ] Добавить document_sources и nullable поля из spec. SQLAlchemy attribute для JSON metadata назвать `source_metadata`, DB column может называться `metadata`: не перекрывать DeclarativeBase.metadata.

```python
def test_chunk_can_only_reference_its_document_source(source_db):
    source_db.add_root("doc-a")
    source_db.add_root("doc-b")
    with pytest.raises(ValueError, match="source"):
        source_db.replace_chunk("doc-a", source_id="only-in-doc-b")
```

- [ ] Выбрать стабильный составной ключ/FK `(doc_id, source_id)`, без зависимости от autoincrement attachment id, меняющегося при replace. Порядок транзакции: снять старые chunk/attachment references → удалить старые sources → вставить sources parents-first → вставить chunks/concepts/attachments.
- [ ] Upgrade/downgrade тесты на пустой и заполненной SQLite и PostgreSQL. Старые rows имеют null source_id и продолжают читаться. Проверить schema diff, индексы `(doc_id,parent_source_id)`, каскады и размер JSON.
- [ ] Dev PG с create_all/Alembic drift: сравнить схему и alembic_version; подготовить адресный идемпотентный migration recipe, не запускать вслепую `upgrade head` и не менять live-БД на этом этапе.
- [ ] Sources без файлов и warnings сохраняются после restart; SQL rollback не оставляет часть дерева. Новый API сериализует относительные пути/IDs, а не серверные FS paths.

## Задача 7. Admission, pipeline, resume и честная неполнота

**Files:** Modify `backend/app/api/documents.py`, `services/pipeline.py`, `services/staging.py`, `services/registry.py`, `services/problem_codes.py`, `error_codes.py`, `models/schemas.py`. Create `services/parse_diagnostics.py`, `backend/tests/test_mail_pipeline.py`; extend `test_upload_similarity.py`, `test_pipeline_integration.py`, `test_pipeline_queue.py`.

**Interfaces:** Backend всегда вызывает `run_parse` задачи 2. В staging хранить schema_version/parser_version/source manifest/chunk-source mapping/parse warnings. `summarize_problems(parse_warnings, generation_problem, index_problem, text_problem) -> str | None` реализует приоритет spec и не удаляет подробности.

- [ ] RED на оба пути: preview до admission и реальная обработка. Проверить LLM-call count=0 до согласия на similar upload, отсутствие ghost document и временных файлов при отказе/ошибке.
- [ ] Подключить одинаковый parser runtime без разных настроек email preview и pipeline. Preview output не публикуется. Повторный parse допускается в первой версии, но детерминирован и ограничен; учесть двойную стоимость в нагрузке.
- [ ] Записывать данные дерева вместе с final chunks/concepts/attachments в согласованной транзакции. Manifest включает source file hash и parser_version; повторная обработка не создаёт новые случайные source_id.
- [ ] Заменить определение extraction_status по подстрокам русских `note` в `_collect_attachments` на структурированный enum из spec; старые marker blocks поддержать адаптером. Тест: смена языка/текста detail не меняет машинный статус, `saved` не превращается в `parsed`.

```python
def test_broken_nested_mail_is_visible_but_sibling_is_searchable(pipeline_case):
    doc = pipeline_case.ingest("docx_good_and_broken_msg")
    assert doc.status == "done"
    assert doc.problem == "attachment_partial_result"
    assert any(w["code"] == "mail_parse_failed" for w in doc.parse_warnings)
    assert pipeline_case.has_chunk(doc.id, "Решение исправного письма")
```

- [ ] Старый staging без parser_version: разрешить прежний resume для обычных документов; для документа, где новая версия меняет извлечение, потребовать regenerate с понятным кодом `parser_version_mismatch`. Не смешивать новые source_id со старыми cached concepts.
- [ ] Root corrupt → 422 до admission; root encrypted/unsupported body → явная ошибка неподдерживаемого содержимого; corrupt embedded → done+warning; storage/network/index failure сохраняет свои статусы. Ordinary unsupported binary не обязательно делает документ проблемным; неподдерживаемое потенциально текстовое письмо/RTF/TNEF — делает.
- [ ] Минимальный текст оценивать по содержимому body/документов, исключив искусственные заголовки/маркеры. Тесты: пустой mail; письмо только с PDF-сканом; короткое «Да» с контекстом; attachment-only XLSX с данными.
- [ ] Kill/restart после parse, после части LLM chunks, перед SQL commit, между SQL и Qdrant. Проверить resume, orphan Qdrant cleanup и отсутствие дубликатов. Qdrant failure нельзя маскировать как extraction warning.

## Задача 8. Чанки, концепты, RAG и точные источники

**Files:** Create `backend/app/services/source_chunking.py`, `doc-parser/src/docparser/source_render.py`, `backend/tests/test_mail_retrieval.py`. Modify `doc-parser/src/docparser/markdown.py`, `services/okf_generator.py`, `chunk_store.py`, `concept_store.py`, `context_builder.py`, `source_location.py`, `models/schemas.py`, `api/chat.py`, `api/search.py`; source_evidence менять только при доказанной необходимости. Extend `test_source_location.py`.

**Interfaces:** `render_source_sections` и `chunk_source_sections` из общих контрактов. RAG source получает nullable `source_id`, `source_path`, `mail_subject`, `mail_sender`, `mail_sent_at`. `fetch_source_paths` делает bulk hydration без N+1. Названия и даты сервер берёт из SQL, LLM не сочиняет их.

- [ ] RED: две одинаковые строки в двух письмах, письмо+таблица, длинное письмо, root-текст между вложениями; проверить, что каждый новый chunk принадлежит одному source_id и offsets указывают в фактически сохранённый content.
- [ ] Обычные документы без почты сохраняют прежнюю схему чанкинга, если это не нужно для provenance; документы с письмами разделять по границам source и затем существующим chunk_text. Не менять sparse tokenizer/dimensions/RRF веса.
- [ ] Программные comment/table extractors сохраняют порядок комментарии → таблицы → LLM. Mail metadata не попадает в таблицу извлекаемых business fields. Не создавать массово концепты по строкам To/From/Message-ID.

```python
def test_evidence_hash_matches_stored_mail_chunk(retrieval_case):
    source = retrieval_case.answer_source("Лимит 12 дней")
    chunk = retrieval_case.chunk(source.doc_id, source.chunk_index)
    assert chunk.content[source.start:source.end] == "Лимит 12 дней"
    assert source.chunk_hash == hashlib.sha256(chunk.content.encode()).hexdigest()
    assert source.source_path[-1]["display_name"] == "Согласование.msg"
```

- [ ] Точный quote проверять общим resolve_source_spans; ambiguous/short quote → chunk-only. Не создавать подтверждённую атрибуцию к источнику, если chunk legacy и происхождение неизвестно.
- [ ] Сохранить quoting, даты, «предложено/согласовано/отменено» в контексте; prompt не должен превращать source text в системную инструкцию. Offline tests проверяют границы prompt и правильный source list; отдельно ручной контроль качества ответа на фиксированных вопросах.
- [ ] Атрибуция ролей: sender из ancestor mail подписывается как отправитель письма; не выдавать его за автора XLSX/цитаты. Тест: пересланный чужой расчёт сохраняет имя источника и не получает ложное авторство пересылающего.
- [ ] Провести baseline/after по существующему корпусу через `backend/test_scripts/probe_sources.py`, не меняя критерий после прогона. Отдельные mail queries: данные body, данные XLSX, противоречащие письма, одинаковая тема, короткое согласование, поиск по автору/теме. Не обещать фильтры дат/участников: это не отдельный UI этой версии.

## Задача 9. Почтовая дедупликация без потери происхождения

**Files:** Create `backend/app/services/mail_identity.py`, `backend/tests/test_mail_identity.py`. Modify `services/deduplication.py`, `api/documents.py`, `test_upload_similarity.py` с учётом текущих staged-изменений.

**Interfaces:** `mail_fingerprint(envelope, full_body, attachment_hashes) -> str`; `mail_similarity_candidates(...)` возвращает существующий upload-review response, не новый независимый upload protocol.

- [ ] RED для same bytes; одного письма MSG/EML; одинакового Message-ID с изменённым body/attachment; отсутствующего ID; общей темы; одинакового body у разных авторов/дат; attachment с тем же именем и разными bytes; перестановки одинаковых вложений и повторов.

```python
def test_message_id_alone_does_not_define_duplicate(mail_pair):
    first, changed = mail_pair.same_id_different_attachment()
    assert mail_fingerprint(*first) != mail_fingerprint(*changed)
```

- [ ] Нормализация conservative: Unicode NFC, переносы строк и структурно известные headers; не удалять числа, подписи, историю и business whitespace внутри таблиц. Состав/кратность attachment hashes входит в fingerprint. При неполном извлечении semantic fingerprint не использовать для hard reject.
- [ ] Exact byte duplicate остаётся существующим 409; semantic duplicate — существующий review/allow_similar. Два конкурентных upload проходят атомарный admission, без повторного parse внутри lock.
- [ ] Не удалять embedded source, если письмо уже загружено отдельно. Trash twin не блокирует новую загрузку по прежним правилам. В candidate response не возвращать документы, которых пользователь не может видеть.
- [ ] Проверить, что reparse/resume не меняет fingerprint из-за временных путей/системной даты; parser_version объясняет намеренные изменения нормализации.

## Задача 10. API, просмотр, скачивание и UI

**Files:** Modify `backend/app/api/documents.py`, `models/schemas.py`, `frontend/src/components/UploadZone.jsx`, `SourceLocationView.jsx`, `OkfFileList.jsx`, `MarkdownViewer.jsx`, `frontend/src/lib/api.js`, `frontend/src/i18n/locales/ru.js`, `en.js`, backend UI manifests. Create `frontend/src/components/DocumentSources.jsx`, `frontend/src/lib/sourceTree.mjs`, `frontend/test/sourceTree.test.mjs`, `backend/tests/test_document_sources_api.py`. Integrate in `frontend/src/app/documents/[docId]/okf/page.js` and current chat source cards found during implementation.

**Interfaces:** `GET /documents/{doc_id}/sources` возвращает дерево и warnings; `GET /documents/{doc_id}/sources/{source_id}/download` разрешает только зарегистрированный local artifact/container. Existing API fields остаются совместимыми, новые nullable.

- [ ] Прочитать актуальные Next.js guides по frontend/AGENTS.md перед изменениями. RED на file acceptance, tree ordering, missing metadata, warning statuses, container-only download label и keyboard navigation.
- [ ] Разрешить `.eml,.msg` в одном источнике frontend validation/accept/help. Основная инструкция — сохранить письма из Outlook и выбрать файлы; unsupported direct drag показывает ясную ошибку, не зависает.
- [ ] Дерево показывает root → тема/автор/дата письма → вложение, извлечённые/пропущенные узлы и причины. Дата соответствует locale/timezone отображения; неизвестная дата так и называется.

```javascript
test('container-only source offers its container, not a fake original', () => {
  const item = sourceDownload({
    source_id: '0-1', artifact_kind: 'container_only',
    container_source_id: 'root', saved_path: null,
  });
  assert.equal(item.targetSourceId, 'root');
  assert.equal(item.labelKey, 'sources.downloadContainer');
});
```

- [ ] Защитить source/list/download и legacy attachment route общей эффективной проверкой доступа/trash root, включая прямой backend. Не делать вывод о защищённости по наличию одного frontend proxy. Проверить authenticated/anonymous, viewer/editor/admin, hidden/trashed/missing source.
- [ ] Отдавать безопасное Content-Disposition, nosniff; не рендерить письма HTML iframe. Проверить скрипты, javascript/data links, SVG payload, remote pixels и CID crossover. Ссылки UI строятся по ID, не из mail subject/имени файла.
- Уточнено пользователем: отдельное предупреждение/подтверждение общей видимости не требуется. Не называть письмо приватным по uploaded_by; вложения наследуют видимость root. Отдельный ACL не входит в задачу.
- [ ] RU/EN: labels, plural/counts, warnings, parse errors, source paths. Экспортировать UI manifests существующим `frontend/scripts/export-ui-keys.mjs`; проверить отсутствие server/client hydration ошибок.

## Задача 11. Жизненный цикл, старые данные и откат

**Files:** Modify `backend/app/services/trash.py`, `services/bulk_export.py` только при обнаруженной несовместимости, `services/pipeline.py`, `backend/tests/test_trash.py`, `test_bulk_export.py`, `test_export_okf.py`. Create `backend/scripts/reparse_mail_documents.py`, `backend/tests/test_reparse_mail_documents.py`, `docs/MAIL_IMPORT.md`.

**Interfaces:** Reparse helper имеет `--dry-run` по умолчанию, `--doc-id` repeatable, `--limit`, `--apply`, `--report`. Он вызывает штатную очередь regenerate, а не меняет SQL/Qdrant вручную. Разбор и генерация могут расходовать LLM-бюджет: отчёт предварительно показывает объём.

- [ ] RED: rename/tag/development/source-locale update не меняет source IDs и байты; trash скрывает все источники; restore возвращает их; purge удаляет sources/attachments/chunks и Qdrant, без удаления файла другого root.
- [ ] Regenerate использует новую generation-directory и сохраняет старые активные артефакты до успешного переключения. Проверить отмену/ошибку между FS swap и SQL/Qdrant: recovery видит incomplete generation, не оставляет смешанное дерево. Cleanup удаляет только неактивные поколения по проверенным путям.
- [ ] Raw export остаётся byte-for-byte original, вложения уже внутри него. OKF bundle включает source manifest и корректные относительные ссылки; legacy bundle ещё открывается. Ссылки после backup/restore на другой ОС не содержат старого drive/path.
- [ ] Никакого автоматического reparse на старте backend. Dry-run отбирает документы с кандидатами mail/OLE и показывает confidence; отсутствие распознанных mail в старых metadata не доказывает их отсутствие. Для точного отбора допускается bounded offline scan с отчётом.
- [ ] Rollout flags: production default `MAIL_IMPORT_ENABLED=false` до приёмки; локальное значение `true` оставить по указанию пользователя. Когда флаг выключен в тестовом/production контуре, новые standalone EML/MSG отклоняются понятным кодом, embedded mail сохраняется/отмечается как disabled, существующие уже обработанные письма продолжают читаться. Остальные форматы не перестают работать.
- [ ] Откат: выключить новые imports, остановить новые mail regeneration jobs, оставить additive schema и прочитанные данные; полный возврат на старый бинарник только с проверенной совместимостью. Не удалять колонки с данными для «быстрого rollback». Восстановление backup проверять в новой БД/каталоге.

## Задача 12. Интеграционная приёмка, производительность и выпуск

**Files:** Create `backend/test_scripts/probe_mail_ingestion.py`, `tests/artifacts/mail/README.md`, `docs/superpowers/reports/2026-09-25-mail-ingestion-acceptance.md` при выполнении. Modify `.github/workflows/ci.yml` при необходимости Windows parser job. Temporary output — `tests/tmp/mail/`.

- [ ] Прогнать матрицу ниже на Windows и Linux clean checkout; не полагаться на локально установленный Outlook, шрифт или неназванную библиотеку. Linux image строится со всеми runtime dependencies.
- [ ] На изолированной тестовой БД и Qdrant: upload → review → parse → concepts/chunks → поиск → ответ с `[N]` → переход → download → restart → resume → trash/restore → export. Реальный LLM-прогон только с синтетическими письмами; deterministic suite использует fakes.
- [ ] Измерить baseline/after на 100 письмах/до 100 MiB, включая 20 документов с вложенными письмами, 10 писем только с attachments и повторные uploads. Manifest задаёт одинаковые input hashes. Отдельно измерять parse/preview и внешние LLM/embedding delays.
- [ ] Проектные SLO: p95 parse для обычного письма ≤5 секунд; worker не выходит за 1024 MiB/120s; обычный DOCX/PDF parse p95 не ухудшается более чем на 20%; поиск p95 во время загрузки не ухудшается более чем на 20%; нет 5xx, потери slots и orphan workers. Превышение → исправление/обоснованное изменение лимита в spec с повторным измерением, не молчаливое снятие gate.
- [ ] Сверить counts по source manifest → SQL → chunks/concepts → Qdrant; нет missing-source, перепутанных авторов и ложной точной подсветки. Количество concepts может меняться из-за LLM: сравнивать наличие заранее заданных фактов и traceability, не только число.
- [ ] Security checks: hostile fixtures, network attempts=0, bounded decompression/HTML parsing, access matrix, logs без private content, dependency audit. Проверить, что пользовательские письма не попали в репозиторий/артефакты CI.
- [ ] Записать фактические команды, версии, hashes, pass/fail/skip, resource metrics и ограничения в отчёт. Не писать «полностью поддерживается Outlook», если есть непроверенные RTF/IRM/legacy варианты.
- [ ] Включать production feature flag только после отсутствия блокирующих failures. Локальное значение `true` сохранять по указанию пользователя. Коммит/пуш и production rollout — только по соответствующему запросу пользователя, со scoped staging и проверкой diff.

## Матрица обязательных тестов

| ID | Сценарий | Ожидаемое поведение | Задача |
|---|---|---|---|
| M01 | EML plain UTF-8/CP1251/KOI8-R | Кириллица и headers без потерь | 3 |
| M02 | alternative/related/mixed | Тело один раз, вложения отдельно | 3 |
| M03 | HTML table, links, CID | Таблица ищется; CID локальный; сеть не вызывается | 3 |
| M04 | Unicode/ANSI MSG | Поля/текст совпадают с manifest | 1,4 |
| M05 | RTF-only/compressed RTF | Извлечение доказано либо явный unsupported | 1,4 |
| M06 | DOCX OLE/Package/Ole10Native MSG | Корректный child source, не просто .bin | 5 |
| M07 | MSG в ячейке/nested table/header | Нет пропуска/двойного обхода | 5 |
| M08 | MSG embedded substorage | Правильный parent/property context/container download | 4 |
| M09 | PDF и XLSX с EML/MSG | Общий путь рекурсии | 5 |
| M10 | DOCX→MSG→XLSX | Значение XLSX ищется с полной цепочкой | 5,8,12 |
| M11 | MSG→EML→MSG→XLSX | Граница depth 3 явно остановлена | 2,5 |
| M12 | Linked OLE/иконка/скриншот | Не выдуманы bytes и почтовые metadata | 5 |
| M13 | .doc/.xls/PST/OST/ZIP/TNEF | Явный unsupported там, где есть потенциальный текст | 4,5,7 |
| M14 | Вложение без расширения/spoof extension | Content-aware detection без ложного MSG | 5 |
| M15 | Signed/encrypted/IRM/calendar MSG | Нет заявления о verified signature; честная поддержка | 3,4 |
| S01 | Empty body, subject only | Нет ложного no_text_layer bypass | 7 |
| S02 | Только скан PDF/только XLSX | Различаются нет текста и есть текст вложения | 7 |
| S03 | «Согласовано» + quoted proposal | Сохранён предмет согласования | 3,8 |
| S04 | Два письма с противоположными решениями | Раздельные даты/авторы, нет автоотмены | 8 |
| S05 | Missing/malformed headers/date/EX-address | Unknown остаётся unknown, parse не падает | 3,4 |
| S06 | Inline reply между цитатами | Полный смысл сохранён, автора не выдумывать | 3,8 |
| S07 | Пересланный XLSX другого автора | Sender не объявляется автором вложения | 8 |
| L01 | Depth boundary −1/0/+1 | Не декодировать запрещённый уровень | 2 |
| L02 | Byte boundary/declared size lie | Cap по факту чтения; partial diagnosis | 2 |
| L03 | 1001 zero/tiny parts | Node cap действует независимо от bytes | 2 |
| L04 | Base64/RTF/ZIP bomb | Ограничены CPU/RAM/disk, backend жив | 2,4 |
| L05 | Shared budget на siblings/inline | Бюджет не сбрасывается рекурсией | 2 |
| L06 | Path traversal/ADS/device names/collisions | Ни записи вовне, ни перезаписи sibling | 2 |
| L07 | Broken attachment среди исправных | Исправные ищутся, warning виден | 5,7 |
| L08 | Preview hang/crash/large output | Timeout kill+cleanup, без ghost document | 2,7 |
| L09 | Concurrent preview/background | ≤2 parse workers, нет deadlock/slot leak | 2,7 |
| L10 | Disk full/DB/Qdrant error | Инфраструктурный статус, не ложный done | 7 |
| D01 | Тот же файл дважды | Прежний exact duplicate contract | 9 |
| D02 | Одно письмо EML/MSG | Similar review, сохранён осознанный импорт | 9 |
| D03 | Same Message-ID, другой attachment/body | Не hard duplicate | 9 |
| D04 | Standalone + внутри DOCX | Происхождение root-документа не теряется | 9 |
| D05 | Trash twin/concurrent admission | Нет новых гонок и false rejection | 9 |
| P01 | Same quote в разных source | Правильная связь chunk/source | 8 |
| P02 | Same quote дважды в одном chunk | Chunk-only вместо выдуманного exact | 8 |
| P03 | HTML cleanup/Cyrillic/emoji offsets | Hash/offset по сохранённому content | 8 |
| P04 | Legacy chunk/no metadata | Совместимый fallback без ложной цепочки | 6,8 |
| P05 | Reparse в другом temp dir/ОС | Идентичные logical IDs, переносимые links | 5,11 |
| A01 | HTML/SVG/script/pixels/URL payload | Нет исполнения и remote requests | 3,10 |
| A02 | Prompt injection в письме | Источник не становится system instruction | 8 |
| A03 | Anonymous/hidden/trashed direct download | Запрет по эффективным правам root | 10 |
| A04 | Bcc/transport headers/logging | Нет попадания в LLM/index/logs | 3,8,10 |
| R01 | Restart/resume/parser version mismatch | Нет смешения поколений | 7 |
| R02 | Regenerate with attachment removed | Старые links/points не остаются активными | 7,11 |
| R03 | Trash/restore/purge | Согласованный lifecycle всего дерева | 11 |
| R04 | Backup на Windows → restore Linux | Канонический текст и downloads доступны | 11,12 |
| R05 | Raw export + OKF export | Original bytes неизменны; source links валидны | 11 |
| R06 | Upgrade/downgrade/old schema | Нет обязательного reindex старого корпуса | 6 |
| R07 | Flag off после обработки mail | Новые imports закрыты, старые читаются | 11 |
| R08 | Перевод/изменение note у skipped attachment | Статус определяется кодом, не языком | 7 |
| U01 | RU/EN, keyboard, SSR/hydration | Нет недоступных действий/ошибок hydration | 10 |
| U02 | Container-only download | Подпись честно указывает контейнер | 10 |
| Q01 | 100 писем/100 MiB | Измеренные SLO и нулевые orphan resources | 12 |
| Q02 | Старый поиск baseline/after | Нет необъяснимой потери источников | 8,12 |

## Команды проверки при реализации

Команды запускаются в указанном cwd. Перед тестами создать `tests/tmp/pytest-runs` и `tests/tmp/mail` от корня; `$env:TEMP`/`$env:TMP` направить в workspace `tests/tmp/mail` только для тестового shell. Общий фиксированный basetemp нельзя использовать одновременно двумя процессами; в параллельных запусках передавать уникальный `--basetemp`.

| Где | Команда | Когда |
|---|---|---|
| repo root | `git status --short` и `git diff --check` | baseline/final, без staging |
| doc-parser | `..\backend\.venv\Scripts\python.exe -m pytest -q tests/test_eml_parser.py tests/test_msg_parser.py tests/test_nested_mail.py tests/test_parse_budget.py` | после parser этапов |
| doc-parser | `..\backend\.venv\Scripts\python.exe -m pytest -q` | все parser regressions |
| backend | `.\.venv\Scripts\python.exe -m pytest -q tests/test_mail_pipeline.py tests/test_document_sources.py tests/test_mail_identity.py tests/test_mail_retrieval.py tests/test_document_sources_api.py tests/test_parser_runner.py` | backend integration |
| backend | `.\.venv\Scripts\python.exe -m pytest -q tests/test_source_location.py tests/test_attachments_api.py tests/test_upload_similarity.py tests/test_trash.py tests/test_bulk_export.py` | критические регрессии |
| backend | `.\.venv\Scripts\python.exe -m pytest -q --cov=app --cov-report=term-missing --cov-fail-under=85` | итоговый CI-equivalent |
| backend | `.\.venv\Scripts\python.exe -m ruff check .` | итоговый lint |
| frontend | `node --test` | все frontend unit tests |
| frontend | `node node_modules/eslint/bin/eslint.js .` | lint, обход сломанного npm shim |
| frontend | `node scripts/export-ui-keys.mjs` | после RU/EN изменений |
| frontend | `node node_modules/next/dist/bin/next build` | итоговая сборка |
| frontend | `node node_modules/next/dist/bin/next dev -p 16300` | только если нужен локальный UI |

В Linux CI использовать `python -m pytest` после установки package/deps, стандартные npm ci/test/build. Новые test filenames появляются в соответствующих задачах; команды сейчас не запускались. Миграционные PostgreSQL тесты работают на отдельной test-БД, а не на текущей базе пользователя. Установка pytest-cov/audit tools выполняется по manifest тестового окружения, не предполагается молча установленной.

Полный набор команд дополнить вызовом provider-contract, mail text, migration и reparse tests через полные suites. Проверить frontend script path перед запуском; при изменении структуры использовать фактическую команду из package/scripts. API probes используют `127.0.0.1:18000`, UI — порт 16300, Qdrant — 16333, Ollama — 12400; сервисы без необходимости не перезапускать.

## Реестр рисков и меры

| Риск | Мера/проверка | Остаточное ограничение |
|---|---|---|
| Неподходящая MSG-зависимость/лицензия | Gate 1, notices, версии, dependency audit | Может потребоваться собственный reader и пересмотр оценки |
| OLE ≠ MSG и embedded MSG ≠ готовый файл | Структурное распознавание и container locator | Не все объекты можно скачать как самостоятельный оригинал |
| Повреждение/особенности RTF/IRM/TNEF | Fixtures, per-node status, ограниченный worker | Часть вариантов первой версии явно unsupported |
| Потеря смысла при очистке переписки | Полный body, conservative normalization, S03/S06 | Повторные цитаты могут оставаться в поиске |
| Неверное объединение писем | Message-ID не unique key, similar review | Межформатные дубли не всегда распознаются |
| Ложная атрибуция и exact quote | Source boundaries + canonical hash/offset | Для legacy/ambiguous остаётся chunk-only |
| DoS до admission | Supervisor на preview и pipeline, hard limits | Масштабные письма могут требовать ручного разделения |
| Path traversal/XSS/remote image | Safe names, no raw HTML, no fetch, nosniff | Пользователь сам решает открыть скачанный файл вне сервиса |
| Утечка персональной переписки | Права root, access tests, видимость/LLM в UI | Общая база не становится личным почтовым архивом |
| Изменение staging/DB/Qdrant | Versioning, recovery tests, additive migration | Старые вложенные письма требуют явного regenerate |
| Стоимость LLM | Чанки по source, лимиты, dry-run оценки | Больше источников может увеличить число вызовов |
| Удаление чужих изменений/артефактов | Scoped diff, generation paths, safe cleanup | Реализация идёт в общем dirty checkout |
| Слишком широкий объём первого релиза | Отдельные задачи и gates, EML/MSG подпроверки | Нельзя назвать EML-only завершением всего запроса |

## Оценка и критерий завершения

Будущая архитектурная доработка по предложению пользователя 26.09:
[проверка всех чанков и решение администратора перед генерацией](2026-09-26-generation-risk-review-backlog.md).
Проект включает checkpoint/resume, права, аудит и исключение pending чанков
из нового RAG-поколения. Реализация сейчас не начата; одобрение источника
не заменяет защиту модели от инструкций в его содержимом.

Планировочный диапазон: 12–20 инженерных дней при подходящем готовом MSG backend и доступных fixture; собственный reader добавляет ориентировочно 5–10 дней. Это не обещание срока: после задачи 1 оценка пересчитывается по бинарным образцам и состоянию зависимости. Основная сложность — MSG/OLE, provenance и совместимость pipeline, а не добавление расширения в input.

Готовность означает одновременно: обязательные сценарии M01–M10 проходят; unsupported варианты явно диагностированы; все security/lifecycle gates проходят; реальные цепочки проверены end-to-end; старые DOCX/XLSX/PDF не регрессируют; migration/rollback опробованы на изолированных данных; есть отчёт с измерениями и оставшимися ограничениями. Прохождение только parser unit tests недостаточно.

Полностью исключить неизвестные риски заранее невозможно. Этот план фиксирует проверенные точки интеграции, известные классы отказов и gates, не позволяющие выдать частичную поддержку за завершённую функцию.
