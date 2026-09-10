# doc-parser

Standalone-приложение для извлечения содержимого документов. Выделено из сервиса
AI Knowledge, чтобы изучать и развивать парсинг отдельно — без LLM, Qdrant и веб-UI.

## Возможности

- **DOCX** — абзацы, заголовки, таблицы, **комментарии рецензентов**, **встроенные объекты (OLE)**.
- **XLSX** — листы → Markdown-таблицы, встроенные объекты (`xl/embeddings`).
- **PDF** — текст по страницам, вложенные файлы (`pypdf.reader.attachments`).

### Вложения (docx/xlsx/pdf)

Встроенный файл — это файл внутри zip-пакета (`word/embeddings/*.bin` для docx,
`xl/embeddings/*.bin` для xlsx) в формате OLE Compound File. Парсер:

1. Находит `o:OLEObject` в абзаце, через rels достаёт `.bin`.
2. `olefile` открывает `.bin`; стрим `Package` (Office 2007+) — это OOXML-пакет,
   который **рекурсивно разбирается нашим же парсером** (встроенный xlsx/pdf/docx
   попадает в базу знаний как обычные блоки).
3. Fallback на `Ole10Native`/`CONTENTS` (Office 97-2003), определение по магическим
   байтам (`%PDF`, `PK\x03\x04`) и по `ProgID`.
4. Что разобрать не удалось — сохраняется в `--attachments-dir` с маркером-блоком
   «Вложение: имя (тип)».

## Установка и запуск (отдельно от сервиса)

```bash
cd doc-parser
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -e ".[dev]"
```

## CLI

```bash
# Разобрать документ → Markdown
doc-parser parse path/to/sample.docx

# Разобрать → JSON (структура блоков)
doc-parser parse path/to/sample.docx --format json

# Разобрать и сохранить вложения в папку
doc-parser parse path/to/sample.docx --attachments-dir out/attachments

# Записать результат в файл
doc-parser parse path/to/sample.docx -o out/sample.md

# Статистика: блоки по типам, комментарии, вложения
doc-parser info path/to/sample.docx
```

## Библиотека

```python
from docparser import Block, blocks_to_markdown, parse_document

blocks = parse_document("sample.docx", attachments_dir="out/attachments")
for b in blocks:
    print(b.type, "|", b.text[:80])
markdown = blocks_to_markdown(blocks)
```

Контракт: `Block(type, text, level, meta)`, `parse_document(path, filename=None,
attachments_dir=None) -> list[Block]`, `blocks_to_markdown(blocks) -> str`.
`type` ∈ `heading | paragraph | table | comment | attachment`.

## Тесты

```bash
doc-parser> pytest -q
```

Фикстуры создаются на лету: docx с комментариями, docx со встроенными xlsx/pdf,
xlsx, мини-pdf. Отдельного окружения (LLM/Qdrant) не требуется.

## Развитие пакета

- Правки в `src/docparser/` — сервис (`backend/`) подхватывает через editable-установку
  (`pip install -e doc-parser`).
- Версия — в `pyproject.toml` (`project.version`). При желании позже можно вынести
  в отдельный git-репозиторий или приватный index — пакет самодостаточен.

## Ограничения

### Безопасность входных архивов

DOCX и XLSX считаются недоверенными ZIP-контейнерами. До передачи файла в
`python-docx` или `openpyxl` парсер проверяет central directory: число members,
размер каждого member, суммарный распакованный размер, compression ratio,
дублирующиеся имена и traversal-пути. Небезопасный контейнер отклоняется через
`ArchiveLimitError`. Для вложений действует тот же контроль перед чтением bytes.

Дополнительные лимиты XLSX ограничивают число строк и ячеек. Лимиты являются
частью security-модели сервиса; их изменение требует capacity review и обновления
корневого `SECURITY.md` и `docs/PRODUCTION_DEPLOYMENT.md`.

- `pptx` (PowerPoint) не входит в `SUPPORTED_EXTENSIONS` — встроенные презентации
  сохраняются как вложение, но не разбираются.
- OCR изображений из `word/media/` пока не реализован.
