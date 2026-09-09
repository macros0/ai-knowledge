"""Генерация OKF-файлов (YAML-фронтматтер + Markdown) из текста документа через LLM."""
import logging
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Protocol
import yaml

from app.config import get_settings
from app.models.schemas import Concept, OkfDocument
from app.prompts.store import get_store
from app.services.comment_concepts import extract_comment_concepts
from app.services.field_table import extract_table_concepts
from app.services.json_atomic import write_json_atomic
from app.services.llm_client import LLMClient, LLMTruncationError

logger = logging.getLogger(__name__)

VALID_TYPES = {"concept", "procedure", "reference", "example", "note"}

# Тег концептов, порождённых блоками распарсованных вложений (UX-обходной путь
# до Этапа 2c provenance; детерминированно проставляется post-LLM в pipeline).
ATTACHMENT_TAG = "attachment"


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

    def attachment_shares(self, markdown_text: str, attach_spans: list[tuple[int, int]]) -> list[float]:
        """Доля символов каждого чанка, порождённая блоками вложений (0.0..1.0).

        Атрибуция происхождения для программного тега «attachment»: чанки режутся
        из markdown, но ре-джойн юнитов («\\n\\n») означает, что чанк не всегда
        точная подстрока markdown. Поэтому работаем в unit-пространстве: делим
        markdown на юниты (та же _split_units), находим каждый юнит в тексте
        последовательным find() (юниты — точные подстроки), определяем флаг
        «из вложения» пересечением со спанами, и группируем в чанки ТОЙ ЖЕ
        арифметикой _chunk_groups, что и _chunk_text — порядок и число чанков
        совпадают с chunk_text().

        Возвращает [] если вложений нет (спаны пусты) — быстрый выход для
        пайплайна. Длина результата == len(chunk_text(markdown_text)).
        """
        if not attach_spans:
            return []
        text = markdown_text.strip()
        if not text:
            return []
        if len(text) <= self.settings.okf_max_chunk_chars:
            return [_coverage(attach_spans, 0, len(text))]

        units = _split_units(text)
        unit_flags: list[bool] = []
        search_from = 0
        for unit in units:
            pos = text.find(unit, search_from)
            if pos < 0:  # защита: не должно случаться (юниты — подстроки текста)
                unit_flags.append(False)
                continue
            search_from = pos + len(unit)
            unit_flags.append(_overlaps(attach_spans, pos, pos + len(unit)))

        groups = _chunk_groups([len(u) for u in units], self.settings.okf_max_chunk_chars)
        shares: list[float] = []
        for group in groups:
            total = sum(len(units[i]) for i in group) + 2 * (len(group) - 1)
            attach = sum(len(units[i]) for i in group if unit_flags[i])
            shares.append(attach / total if total else 0.0)
        return shares

    def prompt_version(self) -> str:
        """SHA-256-префикс нормализованного набора промптов OKF-генерации.

        Провенанс (Этап 2b): fingerprint фактических шаблонов okf_system/okf_user/
        okf_chunk — нормализованных (CRLF→LF, срез хвостовых пробелов), чтобы
        незначимые различия перевода строк не меняли версию. Хранится в
        okf_concepts.prompt_version и позволяет понять «каким промптом построено».
        """
        import hashlib

        digest = hashlib.sha256()
        for key in ("okf_system", "okf_user", "okf_chunk"):
            raw = self.prompts.get(key)
            norm = "\n".join(line.rstrip() for line in raw.replace("\r\n", "\n").split("\n")).strip()
            digest.update(norm.encode("utf-8"))
            digest.update(b"\x00")
        return digest.hexdigest()[:12]

    def generate_chunk(self, chunk: str, filename: str, index: int, total: int, doc_id: str = "unknown") -> list[Concept]:
        """Генерация OKF-концептов для одного чанка (индекс — 1-based).

        Таблицы полей XML-сообщений (колонки: поле | тип | длина | кратность |
        описание) извлекаются ПРОГРАММНО (field_table.extract_field_table_concepts),
        а не LLM: 12B-модель не справляется с экстракцией всех полей большой
        таблицы (выбирает несколько и останавливается). Программная экстракция
        гарантирует все поля (включая скалярные lnState, snils, ...), а LLM
        получает остаток чанка (без таблицы) — не тонет в ней и обрабатывает
        семантику (XML-примеры, описания).

        Комментарии рецензентов (треды «вопрос → ответы») извлекаются
        программно ДО таблиц (comment_concepts.extract_comment_concepts):
        LLM обрабатывала их нестабильно (теряла решения), а порядок «до таблиц»
        обязателен — незакрытая строка markdown-таблицы «заглатывает»
        последующие строки, блок-цитата комментария после такой строки
        уходила бы в ячейку таблицы. На сыром чанке блок-цитаты контигуальны.

        Каскад отказоустойчивости при обрезании ответа LLM по лимиту токенов:
          1. chat_json сам повторяет запрос с увеличенным max_tokens (до cap);
          2. если всё ещё LLMTruncationError — чанк режется пополам и половинки
             генерируются рекурсивно (глубина <= okf_split_max_depth, до 4 кусков);
          3. если сплит невозможен/исчерпан — salvage последней надежды
             (okf_salvage_truncated): частичный результат сохраняется с WARNING,
             документ не застревает.
        """
        # Комментарии — ДО таблиц: экстракция на сыром чанке, где блок-цитаты
        # гарантированно целы (см. docstring выше).
        comment_concepts: list[Concept] = []
        if self.settings.okf_comment_concepts_enabled:
            comment_concepts, chunk = extract_comment_concepts(chunk, chunk_index=index)
        table_concepts, remainder = extract_table_concepts(
            chunk,
            chunk_index=index,
            llm=self.llm,
            use_llm_classify=self.settings.okf_table_llm_classify,
            doc_id=doc_id,
        )
        llm_concepts = self._generate_chunk_recursive(remainder, filename, index, total, doc_id, depth=0)
        return comment_concepts + table_concepts + llm_concepts

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

    def build_okf_docs(
        self,
        doc_id: str,
        filename: str,
        concepts: list[Concept],
        attachments: list[dict] | None = None,
        global_tags: list[str] | None = None,
        slugs: list[str] | None = None,
        chunk_of_slug: dict[str, int] | None = None,
        bundle_dir: Path | None = None,
    ) -> tuple[list[OkfDocument], list[dict]]:
        """Строит OkfDocument[] + manifest БЕЗ записи файлов (Этап 2b).

        Фаза 5: бандл — экспорт/архив, а не рабочее состояние. Финализация
        пайплайна строит okf_docs в памяти (для replace_concepts/index_concepts),
        а файлы .md пишутся только в dual-write (save_bundle) или при экспорте.
        filepath — синтетический путь (slug определяет stem), файл не обязателен.
        """
        global_tags = global_tags or []
        okf_docs: list[OkfDocument] = []
        seen: set[str] = set()
        manifest: list[dict] = []
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
            filepath = bundle_dir / f"{slug}.md" if bundle_dir is not None else Path(f"{slug}.md")
            chunk_index = (chunk_of_slug or {}).get(slug)
            markdown = _build_markdown(
                concept,
                filename,
                doc_id,
                attachments=attachments,
                global_tags=global_tags,
                chunk_index=chunk_index,
            )
            metadata = {
                "type": concept.type,
                "title": concept.title,
                "tags": concept.tags,
                "global_tags": global_tags,
                "source_document": {"filename": filename, "doc_id": doc_id},
                "relations": concept.relations,
                "attachments": attachments or [],
                "chunk_index": chunk_index,
            }
            okf_docs.append(
                OkfDocument(filepath=str(filepath), metadata=metadata, content=concept.content, markdown=markdown)
            )
            manifest.append(
                {
                    "filename": Path(filepath).name,
                    "title": concept.title,
                    "type": concept.type,
                    "tags": concept.tags,
                    "size": len(markdown.encode("utf-8")),
                    "chunk_index": chunk_index,
                }
            )
        return okf_docs, manifest

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
        okf_docs, manifest = self.build_okf_docs(
            doc_id,
            filename,
            concepts,
            attachments=attachments,
            global_tags=global_tags,
            slugs=slugs,
            chunk_of_slug=chunk_of_slug,
            bundle_dir=bundle_dir,
        )
        for doc in okf_docs:
            Path(doc.filepath).write_text(doc.markdown, encoding="utf-8")
        write_json_atomic(bundle_dir / "_files.json", manifest)
        return okf_docs


def _build_markdown(
    concept: Concept,
    filename: str,
    doc_id: str,
    attachments: list[dict] | None = None,
    global_tags: list[str] | None = None,
    chunk_index: int | None = None,
    generated_at: str | None = None,
) -> str:
    meta = {
        "type": concept.type,
        "title": concept.title,
        "tags": concept.tags,
        "global_tags": global_tags or [],
        "source_document": {"filename": filename, "doc_id": doc_id},
        "relations": concept.relations,
        "attachments": attachments or [],
        "created_at": generated_at or date.today().isoformat(),
    }
    if chunk_index is not None:
        meta["chunk_index"] = chunk_index
    frontmatter = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, default_flow_style=False)
    body = _truncate_content(concept.content, get_settings().okf_max_concept_chars)
    return f"---\n{frontmatter}---\n\n# {concept.title}\n\n{body}\n"


def _parse_relation(r: str) -> str:
    """Нормализует relation к чистому filepath/id.

    LLM иногда возвращает stringified dict: "{'id': 'x', 'type': 'part_of'}"
    вместо чистой строки. Извлекаем 'id' из такого dict-формата.
    Если строка не является dict-форматом, возвращаем как есть.
    """
    r = r.strip()
    if r.startswith("{") and "id" in r:
        try:
            import ast

            d = ast.literal_eval(r)
            if isinstance(d, dict) and "id" in d:
                return str(d["id"]).strip()
        except (ValueError, SyntaxError):
            pass
    return r


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
                relations=[_parse_relation(str(r)) for r in item.get("relations", []) if str(r).strip()],
            )
        )
    return concepts


def _chunk_text(text: str, max_chars: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    units = _split_units(text)
    groups = _chunk_groups([len(u) for u in units], max_chars)
    chunks = ["\n\n".join(units[i] for i in group) for group in groups]
    return chunks or [text]


def _chunk_groups(unit_lens: list[int], max_chars: int) -> list[list[int]]:
    """Группировка индексов юнитов в чанки — та же арифметика, что в _chunk_text.

    Вынесена отдельно, чтобы attachment_shares строил доли ровно по тем же
    границам чанков, что и _chunk_text (условие переполнения `+1`, аккумуляция
    длины `+2` на разделитель «\\n\\n» — байт-в-байт исходная логика).
    """
    groups: list[list[int]] = []
    current: list[int] = []
    cur_len = 0
    for i, ulen in enumerate(unit_lens):
        if current and cur_len + ulen + 1 > max_chars:
            groups.append(current)
            current = []
            cur_len = 0
        cur_len = (cur_len + 2 + ulen) if current else ulen
        current.append(i)
    if current:
        groups.append(current)
    return groups


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(s < end and start < e for s, e in spans)


def _coverage(spans: list[tuple[int, int]], start: int, end: int) -> float:
    """Доля диапазона [start, end), покрытая спанами."""
    if end <= start:
        return 0.0
    covered = sum(max(0, min(e, end) - max(s, start)) for s, e in spans)
    return covered / (end - start)


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

# Транслитерация латинских букв в ASCII: буквы раскрываются по конвенции языка,
# а не вырезаются — иначе «Fürsorge» давал бы бессмысленный slug «frsorge».
# Немецкие умлауты — DIN 5008-2 (ä→ae, ö→oe, ü→ue, ß→ss), добавлены 08.09.2026
# вместе с ä/ö/ü/ß в алфавит токенайзера.
#
# Остальные — буквы, которые NFKD НЕ раскладывает (у них нет разложения на
# базовую букву + combining-знак: перечёркнутые и лигатуры). Без карты класс
# на шаге 3 просто вырезал бы их: «Łódź» → «odz», «Ødegård» → «degard»,
# «Đorđe» → «ore», «cœur» → «cur». Языки взяты из алфавита токенайзера
# (скандинавские, польский, хорватский, исландский) плюс французская лигатура œ.
# Диакритики С разложением (é, ç, ñ, å…) карты не требуют — их снимает NFKD.
_LATIN_TRANS = {
    "ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",
    "ø": "oe", "æ": "ae", "œ": "oe",
    "ł": "l", "đ": "d", "ð": "d", "þ": "th",
}


def _slugify(text: str) -> str:
    s = text.lower()
    # 1) Транслитерация кириллицы и латинских букв без NFKD-разложения
    #    (умлауты, ø/æ/ł/đ/þ/œ) в латиницу/ASCII.
    s = "".join(_CYR_LAT.get(ch, _LATIN_TRANS.get(ch, ch)) for ch in s)
    # 2) Прочие европейские диакритики (é, ç, ñ, å…) — NFKD-разложение + снятие
    #    combining-знаков (é→e): читаемые слаги французских/испанских заголовков.
    #    Идёт ПОСЛЕ карты, чтобы ä→ae, а не «a». Буквы, которые NFKD не
    #    раскладывает и которых нет в карте, вырежет класс на следующем шаге.
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9\s-]", "", s)           # убрать non-ascii/спецсимволы
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    # обрезать на границе слова (последний '-' до лимита), чтобы slug был читаемым
    if len(slug) > _SLUG_MAX_LEN:
        head = slug[:_SLUG_MAX_LEN]
        slug = head.rsplit("-", 1)[0] or head
    return slug or "concept"
