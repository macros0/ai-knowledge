"""Генерация OKF-файлов (YAML-фронтматтер + Markdown) из текста документа через LLM."""
import logging
import re
from datetime import date
from pathlib import Path
from typing import Protocol

import yaml

from app.config import get_settings
from app.models.schemas import Concept, OkfDocument
from app.prompts.store import get_store
from app.services.field_table import extract_table_concepts
from app.services.llm_client import LLMClient, LLMTruncationError

logger = logging.getLogger(__name__)

VALID_TYPES = {"concept", "procedure", "reference", "example", "note"}


class LLMLike(Protocol):
    def chat_json(
        self,
        system: str,
        user: str,
        doc_id: str = "unknown",
        chunk_idx: int = 0,
        salvage_truncated: bool = False,
    ) -> list | dict: ...


class OKFGenerator:
    def __init__(self, llm: LLMLike | None = None, bundle_root: Path | None = None):
        self.llm = llm or LLMClient()
        self.settings = get_settings()
        self.prompts = get_store()
        self.bundle_root = bundle_root

    def generate(self, markdown_text: str, filename: str) -> list[Concept]:
        chunks = self.chunk_text(markdown_text)
        concepts: list[Concept] = []
        for i, chunk in enumerate(chunks, start=1):
            concepts.extend(self.generate_chunk(chunk, filename, i, len(chunks)))
        return concepts

    def chunk_text(self, markdown_text: str) -> list[str]:
        return _chunk_text(markdown_text, self.settings.okf_max_chunk_chars)

    def generate_chunk(self, chunk: str, filename: str, index: int, total: int, doc_id: str = "unknown") -> list[Concept]:
        """Генерация OKF-концептов для одного чанка (индекс — 1-based).

        Таблицы полей XML-сообщений (колонки: поле | тип | длина | кратность |
        описание) извлекаются ПРОГРАММНО (field_table.extract_field_table_concepts),
        а не LLM: 12B-модель не справляется с экстракцией всех полей большой
        таблицы (выбирает несколько и останавливается). Программная экстракция
        гарантирует все поля (включая скалярные lnState, snils, ...), а LLM
        получает остаток чанка (без таблицы) — не тонет в ней и обрабатывает
        семантику (XML-примеры, описания).

        Каскад отказоустойчивости при обрезании ответа LLM по лимиту токенов:
          1. chat_json сам повторяет запрос с увеличенным max_tokens (до cap);
          2. если всё ещё LLMTruncationError — чанк режется пополам и половинки
             генерируются рекурсивно (глубина <= okf_split_max_depth, до 4 кусков);
          3. если сплит невозможен/исчерпан — salvage последней надежды
             (okf_salvage_truncated): частичный результат сохраняется с WARNING,
             документ не застревает.
        """
        table_concepts, remainder = extract_table_concepts(
            chunk,
            chunk_index=index,
            llm=self.llm,
            use_llm_classify=self.settings.okf_table_llm_classify,
            doc_id=doc_id,
        )
        llm_concepts = self._generate_chunk_recursive(remainder, filename, index, total, doc_id, depth=0)
        return table_concepts + llm_concepts

    def _generate_chunk_recursive(
        self, chunk: str, filename: str, index: int, total: int, doc_id: str, depth: int
    ) -> list[Concept]:
        prompt = self._build_prompt(chunk, filename, index, total)
        system = self.prompts.get("okf_system")
        try:
            raw = self.llm.chat_json(system, prompt, doc_id=doc_id, chunk_idx=index)
            return _normalize(raw)
        except LLMTruncationError:
            halves: list[str] = []
            if self.settings.okf_split_on_truncation and depth < self.settings.okf_split_max_depth:
                halves = _split_in_half(chunk)
            if len(halves) >= 2 and all(len(h) < len(chunk) for h in halves):
                logger.warning(
                    "[%s] Чанк %s: JSON обрезан, сплит чанка пополам (depth %d, %d+%d символов)",
                    doc_id,
                    index,
                    depth + 1,
                    len(halves[0]),
                    len(halves[1]),
                )
                result: list[Concept] = []
                for half in halves:
                    result.extend(self._generate_chunk_recursive(half, filename, index, total, doc_id, depth + 1))
                return result
            if self.settings.okf_salvage_truncated:
                logger.warning(
                    "[%s] Чанк %s: сплит невозможен/исчерпан, спасаю частичный результат (данные неполные)",
                    doc_id,
                    index,
                )
                raw = self.llm.chat_json(system, prompt, doc_id=doc_id, chunk_idx=index, salvage_truncated=True)
                return _normalize(raw)
            raise

    def _build_prompt(self, chunk: str, filename: str, index: int, total: int) -> str:
        if total <= 1:
            return self.prompts.format("okf_user", filename=filename, content=chunk)
        return self.prompts.format("okf_chunk", filename=filename, index=index, total=total, content=chunk)

    def save_bundle(
        self,
        doc_id: str,
        filename: str,
        concepts: list[Concept],
        attachments: list[dict] | None = None,
        global_tags: list[str] | None = None,
        slugs: list[str] | None = None,
        chunk_of_slug: dict[str, int] | None = None,
        bundle_root: Path | None = None,
    ) -> list[OkfDocument]:
        bundle_dir = bundle_root or self.bundle_root or self.settings.okf_dir / doc_id
        bundle_dir.mkdir(parents=True, exist_ok=True)
        global_tags = global_tags or []
        okf_docs: list[OkfDocument] = []
        seen: set[str] = set()
        for i, concept in enumerate(concepts):
            if not concept.title or not concept.content:
                continue
            if slugs is not None and i < len(slugs) and slugs[i]:
                slug = slugs[i]
            else:
                slug = _slugify(concept.title) or concept.id or f"concept-{i}"
            if slug in seen:
                slug = f"{slug}-{i}"
            seen.add(slug)
            filepath = bundle_dir / f"{slug}.md"
            markdown = _build_markdown(
                concept,
                filename,
                doc_id,
                attachments=attachments,
                global_tags=global_tags,
                chunk_index=(chunk_of_slug or {}).get(slug),
            )
            filepath.write_text(markdown, encoding="utf-8")
            metadata = {
                "type": concept.type,
                "title": concept.title,
                "tags": concept.tags,
                "global_tags": global_tags,
                "source_document": {"filename": filename, "doc_id": doc_id},
                "relations": concept.relations,
                "attachments": attachments or [],
            }
            okf_docs.append(
                OkfDocument(filepath=str(filepath), metadata=metadata, content=concept.content, markdown=markdown)
            )
        return okf_docs


def _build_markdown(
    concept: Concept,
    filename: str,
    doc_id: str,
    attachments: list[dict] | None = None,
    global_tags: list[str] | None = None,
    chunk_index: int | None = None,
) -> str:
    meta = {
        "type": concept.type,
        "title": concept.title,
        "tags": concept.tags,
        "global_tags": global_tags or [],
        "source_document": {"filename": filename, "doc_id": doc_id},
        "relations": concept.relations,
        "attachments": attachments or [],
        "created_at": date.today().isoformat(),
    }
    if chunk_index is not None:
        meta["chunk_index"] = chunk_index
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    body = _truncate_content(concept.content, get_settings().okf_max_concept_chars)
    return f"---\n{frontmatter}---\n\n# {concept.title}\n\n{body}\n"


def _normalize(raw: list | dict) -> list[Concept]:
    items = raw if isinstance(raw, list) else [raw]
    concepts: list[Concept] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        ctype = str(item.get("type", "concept")).lower()
        if ctype not in VALID_TYPES:
            ctype = "concept"
        concepts.append(
            Concept(
                id=str(item.get("id", "")).strip(),
                title=str(item.get("title", "")).strip(),
                type=ctype,
                tags=[str(t).strip() for t in item.get("tags", []) if str(t).strip()],
                content=content,
                relations=[str(r).strip() for r in item.get("relations", []) if str(r).strip()],
            )
        )
    return concepts


def _chunk_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    for unit in _split_units(text):
        if len(current) + len(unit) + 1 > max_chars and current:
            chunks.append(current)
            current = ""
        current = f"{current}\n\n{unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks or [text]


def _split_in_half(text: str) -> list[str]:
    """Режет текст примерно пополам по границе неделимых единиц (_split_units).

    Единицы копятся в первую половину, пока суммарный размер не дойдёт до
    половины; единица, которая перевалит за середину, уходит во вторую половину
    целиком (никогда не разрывается). Возвращает либо [first, second] (обе части
    непустые и строго меньше исходного текста), либо [text] — когда текст
    неделим (одна атомарная единица) и резать нечего.
    """
    units = _split_units(text.strip())
    if len(units) < 2:
        return [text]
    target = len(text) // 2
    first: list[str] = []
    size = 0
    for unit in units:
        if first and size + len(unit) >= target:
            break
        first.append(unit)
        size += len(unit)
    rest = units[len(first):]
    if not rest:
        return [text]
    return ["\n\n".join(first), "\n\n".join(rest)]


def _split_units(text: str) -> list[str]:
    """Резка на неделимые единицы по строкам.

    Единица — группа строк без пустых строк. ```-фенс-блоки и таблицы (строки
    без пустых строк внутри) остаются целыми: пустые строки внутри ```-блока не
    разрывают его. Отступы строк сохраняются (bелые пробелы не срезаются).
    """
    lines = text.split("\n")
    units: list[str] = []
    current: list[str] = []
    in_fence = False

    def flush() -> None:
        if current:
            units.append("\n".join(current))
            current.clear()

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_fence:
                current.append(line)
                flush()
                in_fence = False
            else:
                flush()
                current.append(line)
                in_fence = True
        elif in_fence:
            current.append(line)
        elif stripped:
            current.append(line)
        else:
            flush()
    flush()
    return units


def _truncate_content(text: str, max_chars: int) -> str:
    """Обрезает текст до max_chars по границе строки, не разрывая ```-блок кода.

    Если граница попадает внутрь открытого фенса, неполный блок отбрасывается
    целиком и обрезается по началу этого блока.
    """
    if len(text) <= max_chars:
        return text.rstrip()
    lines = text.split("\n")
    kept: list[str] = []
    chars = 0
    in_fence = False
    block_start = 0
    for line in lines:
        newline_offset = 1 if kept else 0
        if chars + newline_offset + len(line) > max_chars:
            if in_fence:
                kept = kept[:block_start]
            break
        if kept:
            chars += 1
        chars += len(line)
        kept.append(line)
        if line.strip().startswith("```"):
            if not in_fence:
                in_fence = True
                block_start = len(kept) - 1
            else:
                in_fence = False
    return "\n".join(kept).rstrip()


# Транслитерация кириллицы → латиница (ГОСТ-стиль, без внешних зависимостей).
# Применяется в _slugify для человекочитаемых имён .md-файлов на кириллических
# концептах (Товар → tovar, Название → nazvanie). W3C XML разрешает кириллицу в
# именах тегов/атрибутов (CommerceML, 1С), но slug в Latin-ASCII удобнее для
# файловой системы и путей.
_CYR_LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Максимум символов в slug: защита от превышения Windows MAX_PATH (~260) при
# длинных title-предложениях от LLM. Полный путь = data/okf_bundles/{doc_id}/{slug}.md,
# doc_id (~16) + пути (~100) + slug → ограничиваем slug до 80.
_SLUG_MAX_LEN = 80


def _slugify(text: str) -> str:
    s = text.lower()
    s = "".join(_CYR_LAT.get(ch, ch) for ch in s)  # транслитерация кириллицы
    slug = re.sub(r"[^a-z0-9\s-]", "", s)           # убрать non-ascii/спецсимволы
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    # обрезать на границе слова (последний '-' до лимита), чтобы slug был читаемым
    if len(slug) > _SLUG_MAX_LEN:
        head = slug[:_SLUG_MAX_LEN]
        slug = head.rsplit("-", 1)[0] or head
    return slug or "concept"
