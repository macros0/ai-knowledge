"""Программное извлечение концептов из таблиц-перечней.

LLM (mistral-nemo 12B) не справляется с экстракцией всех строк большой таблицы
(40+ строк): генерирует несколько концептов и останавливается. Этот модуль
обходит LLM для таблиц-перечней (где каждая строка — отдельное понятие: поле,
ситуация, определение, элемент справочника), создавая концепт на каждую строку
программно. LLM получает остаток чанка (без таблицы).

Два режима работы:
  1. LLM-классификатор (okf_table_llm_classify=True, рекомендуется): для каждой
     markdown-таблицы LLM решает «требует ли таблица построчного анализа» и
     указывает ключевую колонку. Обобщает на любые таблицы-перечни (не только
     XML-поля). Кэш на диск (data/cache/table_classify/) переиспользуется между
     документами и перезапусками. При ошибке LLM — fallback на эвристику (режим 2).
  2. XML-эвристика (okf_field_table_min_rows>0, fallback): детектирует только
     таблицы полей XML-сообщений по заголовку (поле/элемент/атрибут + тип) и
     camelCase/кириллическим именам. Не требует LLM.

У режимов свои пороги строк: okf_table_classify_min_rows у первого,
okf_field_table_min_rows у второго. Общий порог означал бы, что включение
классификатора при выключенной эвристике (значение 0) ничего не делает.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.config import get_settings
from app.models.schemas import Concept

logger = logging.getLogger(__name__)

# Порог минимального числа строк-данных для программной обработки таблицы
# полей. Берётся из конфига (okf_field_table_min_rows); <= 0 выключает
# программную экстракцию — таблицы остаются на LLM.
def _min_rows() -> int:
    return get_settings().okf_field_table_min_rows


def _classify_min_rows() -> int:
    """Порог режима LLM-классификатора — отдельный от XML-эвристики.

    Один порог на два режима означал бы, что okf_table_llm_classify=True с
    дефолтным okf_field_table_min_rows=0 — включаемый no-op: detect_tables
    возвращал бы пустой список, и классификатор не вызывался ни разу.
    """
    return get_settings().okf_table_classify_min_rows

# XML Name (упрощённо по W3C XML 1.0 NameStartChar/NameChar): первый символ —
# буква Unicode (латиница ИЛИ кириллица — CommerceML/1С используют кириллические
# теги <Товар>, <Название>) или _; далее буквы/цифры/_/-/. Не начинается с
# цифры/-/. Поддерживает PascalCase (WSResult, RowsetWrapper).
_FIELD_NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_\-\.]*$")
# Порядковый номер в первой колонке (1, 2, 1.1, 2.3) — тогда имя во второй.
_ROW_NUMBER_RE = re.compile(r"^\d+(\.\d+)*$")
# Заголовок таблицы полей: первая колонка про поля/элементы/атрибуты.
_HEADER_FIELD_KEYS = (
    "поле", "элемент", "атрибут", "field", "element", "attribute",
    "имя", "наименован", "тег", "tag", "name",
)
# Заголовок колонки «№/номер» — означает, что имя в следующей колонке.
_HEADER_NUMBER_KEYS = ("№", "п/п", "номер", "num", "n.")
# Заголовок колонки типа.
_HEADER_TYPE_KEYS = ("тип", "type")
# Заголовок сообщения: «Вид сообщения 111», «Тип сообщения 12410».
_MSG_HEADER_RE = re.compile(r"(?:вид|тип)\s+сообщения\s*[:№]?\s*№?\s*(\d+)", re.IGNORECASE)


@dataclass
class FieldRow:
    name: str
    type: str
    length: str
    cardinality: str
    description: str


@dataclass
class TableBlock:
    start: int  # индекс строки начала таблицы (заголовок)
    end: int  # индекс строки после конца таблицы (exclusive)
    header: list[str]
    rows: list[FieldRow]


def _scan_markdown_tables(text: str) -> list[tuple[int, int, list[str], list[str]]]:
    """Найти все markdown-таблицы: список (start, end, header, raw_rows).

    Общий сканер для detect_field_tables и detect_tables: разбор структуры
    (заголовок, строка-разделитель, границы блока, многострочные ячейки) у них
    одинаков, различается только фильтрация результата. Многострочные ячейки
    (строки, не начинающиеся с `|`) склеиваются в предыдущую строку-данных.
    """
    lines = text.split("\n")
    found: list[tuple[int, int, list[str], list[str]]] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if not _is_table_row(line):
            i += 1
            continue
        # найден заголовок таблицы; ищем разделитель
        if i + 1 >= n or not _is_separator_row(lines[i + 1]):
            i += 1
            continue
        header = _parse_row_cells(line)
        start = i
        i += 2  # пропустить заголовок и разделитель
        raw_rows: list[str] = []
        while i < n:
            cur = lines[i]
            cur_stripped = cur.strip()
            # пустая строка — конец таблицы
            if cur_stripped == "":
                break
            if cur_stripped.startswith("|"):
                # начало строки-данных (с многострочной ячейкой или без)
                merged = cur
                i += 1
                # если строка не закрыта "|", склеиваем продолжение
                while i < n and not merged.rstrip().endswith("|"):
                    nxt = lines[i]
                    if nxt.strip().startswith("|") and nxt.strip() != "|":
                        # встретили новую строку-данных — текущая была незакрыта,
                        # но продолжать некуда; выходим, оставляя merged как есть
                        break
                    merged = merged + "\n" + nxt
                    i += 1
                raw_rows.append(merged)
            else:
                # строка не начинается с "|" — либо хвост незакрытой ячейки
                # последней строки, либо конец таблицы
                if raw_rows and not raw_rows[-1].rstrip().endswith("|"):
                    raw_rows[-1] = raw_rows[-1] + "\n" + cur
                    i += 1
                else:
                    break
        found.append((start, i, header, raw_rows))
    return found


def detect_field_tables(text: str) -> list[TableBlock]:
    """Найти в тексте markdown-таблицы, являющиеся спецификациями полей XML-сообщения.

    Эвристика «таблица полей XML»:
      - заголовок содержит колонку с полем/элементом/атрибутом И колонку типа;
      - первая колонка строк-данных матчит camelCase/xml-имя;
      - минимум okf_field_table_min_rows строк-данных.

    Возвращает пустой список, если okf_field_table_min_rows <= 0 (программная
    экстракция выключена — таблицы остаются на LLM).
    """
    min_rows = _min_rows()
    if min_rows <= 0:
        return []
    blocks: list[TableBlock] = []
    for start, end, header, raw_rows in _scan_markdown_tables(text):
        if not raw_rows or not _is_field_table(header, raw_rows):
            continue
        rows = [_parse_field_row(_parse_row_cells(r), header) for r in raw_rows]
        rows = [r for r in rows if r and r.name]
        if len(rows) < min_rows:
            continue
        blocks.append(TableBlock(start=start, end=end, header=header, rows=rows))
    return blocks


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|") and s.count("|") >= 2


def _is_separator_row(line: str) -> bool:
    s = line.strip()
    if not s.startswith("|"):
        return False
    cells = _parse_row_cells(s)
    return all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in cells)


def _parse_row_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_field_table(header: list[str], raw_rows: list[str]) -> bool:
    """Эвристика: таблица является спецификацией полей XML-сообщения.

    Проверяет: заголовок содержит колонку «поле/элемент/атрибут» И «тип», и
    первые строки-данных имеют XML-имя в колонке имени. Колонка имени
    определяется адаптивно: если первая колонка — порядковый номер
    (1, 2, 1.1), имя во второй колонке.
    """
    header_lower = [h.lower() for h in header]
    has_field_col = any(any(k in h for k in _HEADER_FIELD_KEYS) for h in header_lower)
    has_type_col = any(any(k in h for k in _HEADER_TYPE_KEYS) for h in header_lower)
    if not (has_field_col and has_type_col):
        return False
    # первые строки-данных: проверить имя в колонке имени
    sample = raw_rows[:3]
    matched = 0
    for r in sample:
        cells = _parse_row_cells(r)
        _col, name = _name_col_and_value(cells)
        if name and _FIELD_NAME_RE.match(name):
            matched += 1
    return matched >= min(2, len(sample))


def _name_col_and_value(cells: list[str]) -> tuple[int, str]:
    """Вернуть (индекс_колонки_имени, имя) с учётом нумерованной первой колонки.

    Если первая колонка — порядковый номер (1, 2, 1.1), имя во второй колонке.
    Иначе имя в первой колонке. Возвращает (-1, "") если имя не найдено.
    """
    if not cells:
        return (-1, "")
    if _ROW_NUMBER_RE.match(cells[0]):
        if len(cells) >= 2:
            return (1, cells[1].strip())
        return (-1, "")
    return (0, cells[0].strip())


def _parse_field_row(cells: list[str], header: list[str]) -> FieldRow | None:
    """Собрать FieldRow из ячеек строки по позиции колонок заголовка.

    Колонки определяются по заголовку (имя; тип; длина/ограничения; кратность/
    вхождения; описание). Имя-колонка адаптивна: если первая колонка — номер,
    имя во второй, остальные колонки сдвигаются на +1. Если заголовок не
    распознан специфичными ключами — используется positional fallback.
    """
    if not cells:
        return None
    name_col, name = _name_col_and_value(cells)
    if name_col < 0 or not name or not _FIELD_NAME_RE.match(name):
        return None
    # сдвиг индексов остальных колонок, если первая — номер
    shift = 1 if name_col == 1 else 0

    idx_type = _find_col(header, _HEADER_TYPE_KEYS)
    idx_len = _find_col(header, ("длина", "length", "огранич"))
    idx_card = _find_col(header, ("кратност", "обязат", "cardinality", "required", "множ", "вхож"))
    idx_desc = _find_col(header, ("описан", "description", "коммент", "comment"))

    # positional fallback для типовых таблиц полей XML (с учётом сдвига):
    # [0]=№/имя, [shift]=имя, [shift+1]=тип, [shift+2]=длина, [shift+3]=кратность, [shift+4]=описание
    if idx_type < 0 and len(cells) >= shift + 2:
        idx_type = shift + 1
    if idx_len < 0 and len(cells) >= shift + 3:
        idx_len = shift + 2
    if idx_card < 0 and len(cells) >= shift + 4:
        idx_card = shift + 3
    if idx_desc < 0:
        last = len(cells) - 1
        if last in {idx_type, idx_len, idx_card, name_col}:
            last = max(shift + 4, last)
        idx_desc = last

    def get(i: int) -> str:
        return cells[i].strip() if 0 <= i < len(cells) else ""

    type_val = get(idx_type) if idx_type >= 0 else ""
    len_val = get(idx_len) if idx_len >= 0 and idx_len != idx_type else ""
    card_val = get(idx_card) if idx_card >= 0 and idx_card != idx_type else ""
    # очистить «Тип.Длина: N» из колонки длины — часто композитное значение
    if len_val.lower().startswith("тип.длина"):
        len_val = re.sub(r"^тип\.длина:\s*", "", len_val, flags=re.IGNORECASE)
    desc_val = get(idx_desc) if idx_desc >= 0 and idx_desc != name_col else ""
    # если описание попало в ту же колонку что и имя — взять последнюю колонку
    if not desc_val and len(cells) > 1:
        desc_val = get(len(cells) - 1)
    return FieldRow(name=name, type=type_val, length=len_val, cardinality=card_val, description=desc_val)


def _find_col(header: list[str], keys: tuple[str, ...]) -> int:
    for i, h in enumerate(header):
        hl = h.lower()
        if any(k in hl for k in keys):
            return i
    return -1


def build_field_concepts(rows: list[FieldRow], code: str | None) -> list[Concept]:
    """Создать по одному концепту на каждое поле таблицы.

    title: «Атрибут {name} — {description[:80]}» (первая строка описания).
    content: markdown-таблица свойств + XML-имя. type: "reference".
    """
    concepts: list[Concept] = []
    code_tag = code or "xml"
    for r in rows:
        desc_first = _first_line(r.description).strip()
        title_desc = desc_first[:80] if desc_first else r.name
        title = f"Атрибут {r.name} — {title_desc}" if title_desc else f"Атрибут {r.name}"
        content = _build_field_content(r)
        concepts.append(
            Concept(
                id="",
                title=title,
                type="reference",
                tags=["field", "xml", code_tag],
                content=content,
                relations=[],
            )
        )
    return concepts


def _build_field_content(r: FieldRow) -> str:
    rows_md = [
        "| Свойство | Значение |",
        "|---|---|",
        f"| Имя (XML) | `{r.name}` |",
    ]
    if r.type:
        rows_md.append(f"| Тип | `{r.type}` |")
    if r.length:
        rows_md.append(f"| Длина | {r.length} |")
    if r.cardinality:
        rows_md.append(f"| Кратность | {r.cardinality} |")
    if r.description:
        rows_md.append(f"| Описание | {_first_line(r.description)} |")
    return "\n".join(rows_md)


def build_overview_concept(rows: list[FieldRow], code: str | None) -> Concept | None:
    """Создать обзорный концепт с перечислением всех полей сообщения.

    title: «Поля сообщения {code}» (или «Поля сообщения XML» если code=None).
    content: первое предложение с кодом + список `name — описание (тип, кратность)`.
    """
    if not rows:
        return None
    label = f"сообщения {code}" if code else "XML"
    title = f"Поля {label}"
    head = f"Поля {label}." if code else "Поля XML-сообщения."
    lines = [head, ""]
    for r in rows:
        desc = _first_line(r.description).strip()
        meta_bits = []
        if r.type:
            meta_bits.append(r.type)
        if r.cardinality:
            meta_bits.append(r.cardinality)
        meta = f" ({', '.join(meta_bits)})" if meta_bits else ""
        desc_part = f" — {desc}" if desc else ""
        lines.append(f"- `{r.name}`{meta}{desc_part}")
    content = "\n".join(lines)
    return Concept(
        id="",
        title=title,
        type="concept",
        tags=["fields-overview", "xml"] + ([code] if code else []),
        content=content,
        relations=[],
    )


def _first_line(text: str) -> str:
    return text.split("\n", 1)[0].strip()


def extract_field_table_concepts(
    chunk: str, chunk_index: int | None = None
) -> tuple[list[Concept], list[FieldRow], str]:
    """Извлечь концепты из таблиц полей XML-сообщения в чанке.

    Возвращает (concepts, all_rows, remainder):
      - concepts: концепты-поля + обзорные концепты (по одному на таблицу);
      - all_rows: все FieldRow (для диагностики);
      - remainder: чанк с удалёнными таблицами полей (заменены заглушкой).
    """
    blocks = detect_field_tables(chunk)
    if not blocks:
        return [], [], chunk
    concepts: list[Concept] = []
    all_rows: list[FieldRow] = []
    lines = chunk.split("\n")
    # сначала найдем code для каждого блока на оригинальных lines (до мутаций)
    codes = [_find_message_code(lines, b.start) for b in blocks]
    for bi, b in enumerate(blocks):
        code = codes[bi]
        field_concepts = build_field_concepts(b.rows, code)
        overview = build_overview_concept(b.rows, code)
        concepts.extend(field_concepts)
        if overview:
            concepts.append(overview)
        all_rows.extend(b.rows)
    # собрать remainder: заменить каждую таблицу на stub. Идём с конца — замена
    # укорачивает lines, и индексы блоков левее остаются валидными только пока
    # мы до них не дошли (blocks отсортированы по start и не пересекаются).
    for b in reversed(blocks):
        stub = f"[Таблица полей извлечена программно: {len(b.rows)} полей]"
        lines[b.start : b.end] = [stub]
    remainder = "\n".join(lines)
    logger.info(
        "Чанк %s: программно извлечено %d концептов-полей из %d таблиц",
        chunk_index,
        len(concepts),
        len(blocks),
    )
    return concepts, all_rows, remainder


def _find_message_code(lines: list[str], table_start: int) -> str | None:
    """Искать вверх от таблицы заголовок «Вид сообщения N» / «Тип сообщения N».

    Возвращает номер (строкой) или None. Идёт вверх максимум 30 строк,
    берёт первое совпадение.
    """
    for j in range(table_start - 1, max(-1, table_start - 31), -1):
        if j < 0:
            break
        m = _MSG_HEADER_RE.search(lines[j])
        if m:
            return m.group(1)
    return None


# ---------------------------------------------------------------------------
# LLM-классификатор таблиц-перечней (обобщённый режим)
# ---------------------------------------------------------------------------


class _ClassifierLLM(Protocol):
    """Минимальный протокол LLM для классификации таблиц."""

    def chat_json(
        self,
        system: str,
        user: str,
        doc_id: str = "unknown",
        chunk_idx: int = 0,
        salvage_truncated: bool = False,
    ) -> list | dict: ...


@dataclass
class RawTableBlock:
    """Неразобранная markdown-таблица (все строки-данные как есть)."""

    start: int
    end: int
    header: list[str]
    raw_rows: list[str]


@dataclass
class TableClassification:
    """Решение LLM-классификатора о таблице.

    extraction_mode:
      - "per_row" — концепт на каждую строку (поля XML, ситуации, определения).
      - "whole" — один концепт на всю таблицу (справочники, перечни кодов,
        где пользователь ищет весь справочник целиком, а не отдельный код).
    """

    concept_per_row: bool
    title_col: int = 0
    description_cols: list[int] = field(default_factory=list)
    concept_type: str = "reference"
    extraction_mode: str = "per_row"


def detect_tables(text: str) -> list[RawTableBlock]:
    """Найти ВСЕ markdown-таблицы с >= okf_table_classify_min_rows строк-данных.

    В отличие от detect_field_tables, НЕ проверяет заголовок/имена — только
    структуру markdown-таблицы и порог строк. Классификацию (таблица-перечень
    или таблица данных) делает LLM-классификатор или fallback-эвристика.

    Возвращает пустой список, если okf_table_classify_min_rows <= 0.
    """
    min_rows = _classify_min_rows()
    if min_rows <= 0:
        return []
    return [
        RawTableBlock(start=start, end=end, header=header, raw_rows=raw_rows)
        for start, end, header, raw_rows in _scan_markdown_tables(text)
        if len(raw_rows) >= min_rows
    ]


# Версия схемы классификатора. Бампить при изменении TableClassification
# (добавление/удаление/переименование полей) — старый кэш инвалидируется.
CLASSIFIER_CACHE_VERSION = "1.0"


def _get_prompt_hash() -> str:
    """SHA256 промпта классификатора (okf_table_classifier).

    Правка промпта → hash меняется → cache-key меняется → cache miss.
    """
    from app.prompts.store import get_store

    prompt_text = get_store().get("okf_table_classifier")
    return hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:16]


def _build_cache_key(header: list[str], raw_rows: list[str]) -> str:
    """Составной cache-key: version + prompt_hash + model + header + rows[:2].

    Инвалидируется автоматически при:
      - правке okf_table_classifier.md (p_hash);
      - смене llm_model в .env (model);
      - изменении CLASSIFIER_CACHE_VERSION (v) — бампить при правке TableClassification.
    """
    payload = {
        "v": CLASSIFIER_CACHE_VERSION,
        "p_hash": _get_prompt_hash(),
        "model": get_settings().llm_model,
        "header": header,
        "rows": raw_rows[:2],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    return get_settings().cache_dir / "table_classify" / f"{key}.json"


def _load_cached_classification(key: str) -> TableClassification | None:
    path = _cache_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        mode = str(data.get("extraction_mode", "per_row"))
        if mode not in ("per_row", "whole"):
            mode = "per_row"
        return TableClassification(
            concept_per_row=bool(data["concept_per_row"]),
            title_col=int(data.get("title_col", 0)),
            description_cols=list(data.get("description_cols", [])),
            concept_type=str(data.get("concept_type", "reference")),
            extraction_mode=mode,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning("Повреждённый кэш классификации %s: %s", path, e)
        return None


def _save_cached_classification(key: str, cls: TableClassification) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "concept_per_row": cls.concept_per_row,
        "title_col": cls.title_col,
        "description_cols": cls.description_cols,
        "concept_type": cls.concept_type,
        "extraction_mode": cls.extraction_mode,
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _llm_classify_table(
    header: list[str], raw_rows: list[str], llm: _ClassifierLLM, doc_id: str, chunk_idx: int
) -> TableClassification:
    """Классифицировать таблицу через LLM (с кэшем на диск).

    Возвращает TableClassification. При ошибке LLM/невалидном ответе бросает
    исключение — вызывающий код откатывается на fallback-эвристику.
    """
    key = _build_cache_key(header, raw_rows)
    cached = _load_cached_classification(key)
    if cached is not None:
        logger.debug("Кэш-попадание классификации таблицы %s", key[:12])
        return cached

    from app.prompts.store import get_store

    store = get_store()
    system = store.get("okf_table_classifier")
    sample_rows = raw_rows[:5]
    table_preview = "| " + " | ".join(header) + " |\n"
    table_preview += "| " + " | ".join("---" for _ in header) + " |\n"
    for r in sample_rows:
        table_preview += r + "\n"
    user = f"Заголовок таблицы: {header}\n\nПервые строки:\n\n{table_preview}\n\nКлассифицируй таблицу."
    raw = llm.chat_json(system, user, doc_id=doc_id, chunk_idx=chunk_idx)
    if isinstance(raw, dict):
        mode = str(raw.get("extraction_mode", "per_row"))
        if mode not in ("per_row", "whole"):
            mode = "per_row"
        cls = TableClassification(
            concept_per_row=bool(raw.get("concept_per_row", False)),
            title_col=int(raw.get("title_col", 0)),
            description_cols=[int(x) for x in raw.get("description_cols", []) if isinstance(x, (int, str))],
            concept_type=str(raw.get("concept_type", "reference")),
            extraction_mode=mode,
        )
    else:
        raise ValueError(f"LLM-классификатор вернул не dict: {type(raw)}")
    _save_cached_classification(key, cls)
    logger.info("Таблица классифицирована LLM: concept_per_row=%s title_col=%d type=%s",
                cls.concept_per_row, cls.title_col, cls.concept_type)
    return cls


def build_row_concepts(
    block: RawTableBlock, cls: TableClassification, code: str | None, lines: list[str] | None = None
) -> list[Concept]:
    """Обобщённый экстрактор концептов из таблицы-перечня.

    Режимы (cls.extraction_mode):
      - "whole": один концепт на всю таблицу. title — из markdown-заголовка
        над таблицей (lines[block.start-1] вверх до первого #/## заголовка),
        fallback на header[0]. content — вся markdown-таблица дословно.
      - "per_row": концепт на каждую строку. title из cls.title_col, content —
        таблица «Свойство | Значение» из всех колонок. Плюс обзорный.
    """
    if not cls.concept_per_row:
        return []
    tag = code or "table"
    header = block.header

    # whole: один концепт на всю таблицу
    if cls.extraction_mode == "whole":
        heading = _find_table_heading(lines, block.start) if lines else None
        title = heading or (header[0] if header else "Таблица")
        content_lines = ["| " + " | ".join(header) + " |"]
        content_lines.append("| " + " | ".join("---" for _ in header) + " |")
        for raw in block.raw_rows:
            content_lines.append(raw.strip())
        content = "\n".join(content_lines)
        return [
            Concept(
                id="",
                title=title[:200],
                type=cls.concept_type,
                tags=[tag, "table-whole"],
                content=content,
                relations=[],
            )
        ]

    # per_row: концепт на каждую строку + обзорный
    concepts: list[Concept] = []
    for raw in block.raw_rows:
        cells = _parse_row_cells(raw)
        if not cells:
            continue
        title_idx = cls.title_col if 0 <= cls.title_col < len(cells) else 0
        row_title = cells[title_idx].strip() if cells[title_idx].strip() else "?"
        content_lines = ["| Свойство | Значение |", "|---|---|"]
        for ci, val in enumerate(cells):
            col_name = header[ci] if ci < len(header) and header[ci] else f"Колонка {ci}"
            content_lines.append(f"| {col_name} | {val.strip()} |")
        content = "\n".join(content_lines)
        concepts.append(
            Concept(
                id="",
                title=row_title[:200],
                type=cls.concept_type,
                tags=[tag, "table-row"],
                content=content,
                relations=[],
            )
        )
    # обзорный концепт — title из заголовка над таблицей
    heading = _find_table_heading(lines, block.start) if lines else None
    overview_title = heading or f"Перечень: {header[0] if header else 'таблица'}"
    overview_lines = [overview_title + ".", ""]
    for raw in block.raw_rows:
        cells = _parse_row_cells(raw)
        # показывать все непустые колонки через " — ", не только title_col —
        # иначе теряются коды (01, 02) при title_col=1 (наименование)
        parts = [c.strip() for c in cells if c and c.strip()]
        if parts:
            overview_lines.append(f"- {' — '.join(parts)}")
    concepts.append(
        Concept(
            id="",
            title=overview_title[:200],
            type="concept",
            tags=[tag, "table-overview"],
            content="\n".join(overview_lines),
            relations=[],
        )
    )
    return concepts


def _find_table_heading(lines: list[str], table_start: int) -> str | None:
    """Искать вверх от таблицы markdown-заголовок (# / ## / ###).

    Возвращает текст заголовка (без #) или None. Идёт вверх максимум 15 строк,
    берёт первое совпадение (ближайший заголовок над таблицей).
    """
    for j in range(table_start - 1, max(-1, table_start - 16), -1):
        if j < 0:
            break
        line = lines[j].strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return None


def extract_table_concepts(
    chunk: str,
    chunk_index: int | None = None,
    llm: _ClassifierLLM | None = None,
    use_llm_classify: bool = False,
    doc_id: str = "unknown",
) -> tuple[list[Concept], str]:
    """Извлечь концепты из таблиц-перечней в чанке.

    Режимы:
      - use_llm_classify=True: LLM-классификатор (с кэшем) решает для каждой
        таблицы, является ли она перечнем. При ошибке LLM — fallback на
        XML-эвристику.
      - use_llm_classify=False: только XML-эвристика (detect_field_tables).

    Возвращает (concepts, remainder) — remainder с заменёнными таблицами.
    """
    if use_llm_classify and llm is not None:
        return _extract_with_llm_classify(chunk, chunk_index, llm, doc_id)
    # fallback: XML-эвристика (существующий путь)
    concepts, _rows, remainder = extract_field_table_concepts(chunk, chunk_index)
    return concepts, remainder


def _extract_with_llm_classify(
    chunk: str, chunk_index: int | None, llm: _ClassifierLLM, doc_id: str
) -> tuple[list[Concept], str]:
    """Извлечение через LLM-классификатор с fallback на XML-эвристику."""
    blocks = detect_tables(chunk)
    if not blocks:
        return [], chunk
    concepts: list[Concept] = []
    lines = chunk.split("\n")
    codes = [_find_message_code(lines, b.start) for b in blocks]
    seen_titles: set[str] = set()  # дедуп по title (дубли из разных чанков)
    extracted_blocks: list[tuple[RawTableBlock, str]] = []
    for bi, b in enumerate(blocks):
        code = codes[bi]
        try:
            cls = _llm_classify_table(b.header, b.raw_rows, llm, doc_id, chunk_index or 0)
            if cls.concept_per_row:
                row_concepts = build_row_concepts(b, cls, code, lines=lines)
                # дедуп по title: пропустить концепты с уже существующим title
                for c in row_concepts:
                    key = c.title.strip().lower()
                    if key in seen_titles:
                        logger.debug("Дедуп: пропущен дубликат title=%r", c.title)
                        continue
                    seen_titles.add(key)
                    concepts.append(c)
                extracted_blocks.append((b, f"[Таблица-перечень извлечена программно: {len(b.raw_rows)} строк]"))
            # если concept_per_row=False — таблица остаётся LLM (не извлекаем)
        except Exception as e:
            logger.warning("LLM-классификатор ошибся (%s), fallback на XML-эвристику для таблицы чанка %s", e, chunk_index)
            # fallback: проверить как таблицу полей XML
            if _is_field_table(b.header, b.raw_rows):
                rows = [_parse_field_row(_parse_row_cells(r), b.header) for r in b.raw_rows]
                rows = [r for r in rows if r and r.name]
                if len(rows) >= _classify_min_rows():
                    for c in build_field_concepts(rows, code):
                        key = c.title.strip().lower()
                        if key not in seen_titles:
                            seen_titles.add(key)
                            concepts.append(c)
                    ov = build_overview_concept(rows, code)
                    if ov:
                        concepts.append(ov)
                    extracted_blocks.append((b, f"[Таблица полей извлечена программно: {len(rows)} полей]"))
    # заменить извлечённые таблицы на stub — с конца, чтобы замена не сдвигала
    # индексы ещё не обработанных блоков (см. extract_field_table_concepts)
    for b, stub in reversed(extracted_blocks):
        lines[b.start : b.end] = [stub]
    remainder = "\n".join(lines)
    logger.info(
        "Чанк %s: LLM-классификатор извлёк %d концептов из %d таблиц",
        chunk_index, len(concepts), len(extracted_blocks),
    )
    return concepts, remainder
