"""Разбор DOCX: абзацы, заголовки, таблицы, комментарии рецензентов и встроенные объекты (OLE)."""
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from docparser.blocks import Block
from docparser.embedded import Attachment, process_embedded, save_image_file

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14_NS = "{http://schemas.microsoft.com/office/word/2010/wordml}"
W15_NS = "{http://schemas.microsoft.com/office/word/2012/wordml}"
O_NS = "{urn:schemas-microsoft-com:office:office}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

# Ограничение контекста (текста абзаца-якоря) в meta блока-комментария.
_CONTEXT_MAX_CHARS = 500

CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
    "image/x-emf": ".emf",
    "image/x-wmf": ".wmf",
    "image/svg+xml": ".svg",
}

MONO_FONTS = {
    "consolas",
    "courier",
    "courier new",
    "monospace",
    "monaco",
    "menlo",
    "liberation mono",
    "dejavu sans mono",
    "source code pro",
    "fira code",
}


def parse_docx(
    path: str | Path,
    attachments_dir: str | Path | None = None,
    depth: int = 0,
    budget=None,
) -> list[Block]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    comments = _load_comments(path, doc)
    threads = _build_threads(comments)
    thread_index: dict[str, int] = {}
    for ti, t in enumerate(threads):
        for c in t:
            thread_index[c.cid] = ti
    emitted_threads: set[int] = set()
    current_section = ""

    blocks: list[Block] = []
    code_lines: list[str] | None = None
    image_count = 0

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines is not None:
            blocks.append(Block("code", "\n".join(code_lines)))
            code_lines = None

    def emit_comment_threads(cids: set[str], context: str) -> None:
        """Эмитит треды комментариев, содержащие переданные id.

        Тред (вопрос рецензента + ответы) эмитится целиком один раз — на первом
        физическом якоре любого из участников. Порядок тредов и записей внутри
        треда — по возрастанию числового id: детерминированно (итерация set в
        старой версии рендерила ответ раньше вопроса от запуска к запуску).
        """
        for cid in sorted(cids, key=_cid_sort_key):
            ti = thread_index.get(cid)
            if ti is None or ti in emitted_threads:
                continue
            emitted_threads.add(ti)
            blocks.append(_thread_block(threads[ti], current_section, context))

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            style = (para.style.name or "").lower() if para.style else ""
            raw = para.text
            text = raw.strip()
            cids = _comment_ids(child)

            is_code = bool(text) and not style.startswith("heading") and _is_code_paragraph(para)

            if is_code:
                if code_lines is None:
                    code_lines = []
                code_lines.append(raw.rstrip())
            elif code_lines is not None and not text and not cids:
                code_lines.append("")
            else:
                flush_code()

            if not is_code and code_lines is None and (text or cids):
                if style.startswith("heading"):
                    blocks.append(Block("heading", text, level=_heading_level(style)))
                    current_section = text
                elif text:
                    blocks.append(Block("paragraph", text))

            emit_comment_threads(cids, context=text[:_CONTEXT_MAX_CHARS])

            for idx, ole in enumerate(_find_ole_objects(child)):
                att = _resolve_ole_attachment(doc, ole)
                if att is not None:
                    blocks.extend(
                        process_embedded(
                            att.data, att.name, att.prog_id, att.caption,
                            attachments_dir, idx, depth=depth + 1, budget=budget,
                        )
                    )

            image_count = _emit_inline_images(blocks, doc, child, attachments_dir, image_count)

        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            md = _table_to_markdown([[cell.text.strip() for cell in row.cells] for row in table.rows])
            if md:
                blocks.append(Block("table", md))
            cell_cids: set[str] = set()
            for cell in child.iter(qn("w:tc")):
                for cell_p in cell.iter(qn("w:p")):
                    cell_cids |= _comment_ids(cell_p)
                    image_count = _emit_inline_images(blocks, doc, cell_p, attachments_dir, image_count)
            # комментарии из ячеек таблицы — сразу после таблицы
            # (раньше сваливались в конец документа)
            emit_comment_threads(cell_cids, context="")

    flush_code()

    # комментарии, чьи якоря не найдены в теле (маркеры удалены ревизиями и т.п.)
    for ti, t in enumerate(threads):
        if ti not in emitted_threads:
            blocks.append(_thread_block(t, current_section, ""))

    return blocks


# ---------------------------------------------------------------- comments
@dataclass
class _Comment:
    cid: str
    author: str = ""
    date: str = ""
    text: str = ""
    para_ids: list[str] = field(default_factory=list)
    parent_cid: str | None = None
    resolved: bool | None = None


def _load_comments(path: str | Path, doc) -> dict[str, _Comment]:
    """Читает комментарии: comments.xml (текст, автор, дата, paraId абзацев) +
    commentsExtended.xml (нити обсуждений paraIdParent, признак done).

    Основной путь — прямой парсинг zip (даёт все метаданные разом); фолбэк —
    python-docx API (случаи, когда zip-части недоступны) без нитей.
    """
    comments: dict[str, _Comment] = {}
    try:
        with zipfile.ZipFile(str(path)) as zf:
            if "word/comments.xml" in zf.namelist():
                root = ET.fromstring(zf.read("word/comments.xml"))
                for el in root.iter(f"{W_NS}comment"):
                    cid = el.get(f"{W_NS}id")
                    if cid is None:
                        continue
                    paras = [
                        "".join(t.text or "" for t in p.iter(f"{W_NS}t"))
                        for p in el.iter(f"{W_NS}p")
                    ]
                    comments[cid] = _Comment(
                        cid=cid,
                        author=el.get(f"{W_NS}author", "") or "",
                        date=el.get(f"{W_NS}date", "") or "",
                        text="\n".join(p for p in paras if p),
                        para_ids=[
                            pid
                            for p in el.iter(f"{W_NS}p")
                            if (pid := p.get(f"{W14_NS}paraId")) is not None
                        ],
                    )
    except Exception:
        comments = {}
    if not comments:
        try:
            for c in doc.comments:
                cid = getattr(c, "comment_id", None) or getattr(c, "id", None)
                d = getattr(c, "date", None)
                comments[str(cid)] = _Comment(
                    cid=str(cid),
                    author=c.author or "",
                    date=_iso_date(d),
                    text=c.text or "",
                )
        except Exception:
            pass
    # нити обсуждений: commentsExtended.xml связывает комментарии по paraId
    if comments:
        try:
            with zipfile.ZipFile(str(path)) as zf:
                if "word/commentsExtended.xml" in zf.namelist():
                    root = ET.fromstring(zf.read("word/commentsExtended.xml"))
                    cid_by_para: dict[str, str] = {}
                    for c in comments.values():
                        for pid in c.para_ids:
                            cid_by_para[pid] = c.cid
                    for ce in root.iter(f"{W15_NS}commentEx"):
                        pid = ce.get(f"{W15_NS}paraId")
                        if not pid or pid not in cid_by_para:
                            continue
                        cid = cid_by_para[pid]
                        parent_pid = ce.get(f"{W15_NS}paraIdParent")
                        if parent_pid and parent_pid in cid_by_para and cid_by_para[parent_pid] != cid:
                            comments[cid].parent_cid = cid_by_para[parent_pid]
                        if ce.get(f"{W15_NS}done") is not None:
                            comments[cid].resolved = ce.get(f"{W15_NS}done") == "1"
        except Exception:
            pass
    return comments


def _iso_date(value) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _cid_sort_key(cid: str):
    """Числовая сортировка id комментариев (у Word id — числа; вопрос
    рецензента всегда имеет меньший id, чем ответ на него)."""
    try:
        return (0, int(cid), "")
    except ValueError:
        return (1, 0, cid)


def _build_threads(comments: dict[str, _Comment]) -> list[list[_Comment]]:
    """Группирует комментарии в треды «вопрос → ответы» по parent_cid
    (из commentsExtended.xml). Комментарий без нитей — одиночный тред.
    Порядок тредов и записей внутри — по возрастанию id.
    """
    groups: dict[str, list[_Comment]] = {}
    for c in comments.values():
        cur = c
        seen: set[str] = {c.cid}
        guard = 0
        while cur.parent_cid and cur.parent_cid in comments and cur.parent_cid not in seen and guard < 32:
            cur = comments[cur.parent_cid]
            seen.add(cur.cid)
            guard += 1
        groups.setdefault(cur.cid, []).append(c)
    return [
        sorted(members, key=lambda c: _cid_sort_key(c.cid))
        for _, members in sorted(groups.items(), key=lambda kv: _cid_sort_key(kv[0]))
    ]


def _thread_block(thread: list[_Comment], section: str, context: str) -> Block:
    """Блок-комментарий = тред целиком. meta.thread — записи по порядку
    (вопрос первым), meta.resolved — все ли закрыты (если известно)."""
    first = thread[0]
    authors: list[str] = []
    for c in thread:
        if c.author and c.author not in authors:
            authors.append(c.author)
    meta: dict = {
        "author": first.author,
        "authors": authors,
        "date": first.date,
        "section": section,
        "thread": [{"author": c.author, "date": c.date, "text": c.text} for c in thread],
    }
    if any(c.resolved is not None for c in thread):
        meta["resolved"] = all(c.resolved for c in thread if c.resolved is not None)
    if context:
        meta["context"] = context
    return Block("comment", first.text, meta=meta)


def _comment_ids(p_el) -> set[str]:
    ids: set[str] = set()
    for el in p_el.iter():
        if el.tag in (f"{W_NS}commentRangeStart", f"{W_NS}commentReference"):
            cid = el.get(f"{W_NS}id")
            if cid is not None:
                ids.add(cid)
    return ids


def _heading_level(style: str) -> int:
    m = re.search(r"(\d+)", style)
    return int(m.group(1)) if m else 2


def _is_code_paragraph(para) -> bool:
    """Определяет код-абзац: моноширинный шрифт или заметный ведущий отступ."""
    text = para.text
    if not text:
        return False
    names: list[str] = []
    for run in para.runs:
        if run.font.name:
            names.append(run.font.name)
    style_font = getattr(para.style, "font", None) if para.style is not None else None
    if style_font is not None and getattr(style_font, "name", None):
        names.append(style_font.name)
    if any(str(n).strip().lower() in MONO_FONTS for n in names):
        return True
    if text[:1] == "\t" or text.startswith("    "):
        return True
    return False


# ---------------------------------------------------------------- OLE objects
def _find_ole_objects(p_el) -> list[dict]:
    """Ищет w:object → o:OLEObject в абзаце и возвращает r_id/prog_id/подпись."""
    results: list[dict] = []
    for wobj in p_el.iter(f"{W_NS}object"):
        caption = ""
        docpr = wobj.find(f"{W_NS}docPr")
        if docpr is not None:
            caption = docpr.get("name", "")
        for ole in wobj.iter(f"{O_NS}OLEObject"):
            results.append(
                {
                    "r_id": ole.get(f"{R_NS}id"),
                    "prog_id": ole.get("ProgID", ""),
                    "caption": caption,
                }
            )
    return results


def _resolve_ole_attachment(doc, ole: dict) -> Attachment | None:
    r_id = ole.get("r_id")
    if not r_id:
        return None
    rel = doc.part.rels.get(r_id)
    if rel is None:
        return None
    try:
        if rel.is_external:
            return None
        data = rel.target_part.blob
    except Exception:
        return None
    if not data:
        return None
    target_name = Path(rel.target_ref).name
    return Attachment(name=target_name, prog_id=ole.get("prog_id", ""), caption=ole.get("caption", ""), data=data)


# ---------------------------------------------------------------- inline images
def _emit_inline_images(
    blocks: list[Block],
    doc,
    p_el,
    attachments_dir: str | Path | None,
    image_count: int,
) -> int:
    """Извлекает inline-картинки из абзаца (w:drawing → a:blip) и добавляет image-блоки.

    Возвращает обновлённый счётчик сохранённых изображений.
    """
    for img in _find_inline_images(p_el):
        resolved = _resolve_image(doc, img["r_id"])
        if resolved is None:
            continue
        data, ext = resolved
        saved = save_image_file(data, attachments_dir, image_count, preferred_name=img["name"], ext=ext)
        image_count += 1
        meta: dict = {"kind": "image", "name": img["name"] or "", "caption": img["caption"]}
        if saved is not None:
            meta["name"] = saved.name
            meta["saved_path"] = str(saved)
        blocks.append(Block("image", "", meta=meta))
    return image_count


def _find_inline_images(p_el) -> list[dict]:
    """Ищет a:blip в абзаце и возвращает r_id/имя/подпись изображения."""
    results: list[dict] = []
    for blip in p_el.iter(f"{A_NS}blip"):
        r_id = blip.get(f"{R_NS}embed") or blip.get(f"{R_NS}link")
        if not r_id:
            continue
        caption = ""
        docpr = blip.getparent()
        while docpr is not None and docpr.tag != f"{W_NS}docPr":
            docpr = docpr.getparent()
        if docpr is not None:
            caption = docpr.get("name", "")
        name = ""
        parent = blip.getparent()
        if parent is not None:
            pic = parent.getparent()
            if pic is not None:
                name = pic.get("name", "")
        results.append({"r_id": r_id, "caption": caption, "name": name})
    return results


def _resolve_image(doc, r_id: str) -> tuple[bytes, str] | None:
    rel = doc.part.rels.get(r_id)
    if rel is None:
        return None
    try:
        data = rel.target_part.blob
    except Exception:
        return None
    if not data:
        return None
    return data, _image_ext(rel)


def _image_ext(rel) -> str:
    try:
        ext = Path(rel.target_ref or "").suffix.lower()
        if ext and len(ext) <= 6:
            return ext
    except Exception:
        pass
    try:
        ct = (getattr(rel.target_part, "content_type", "") or "").lower()
        if ct in CONTENT_TYPE_EXT:
            return CONTENT_TYPE_EXT[ct]
    except Exception:
        pass
    return ".png"


# ---------------------------------------------------------------- tables
def _table_to_markdown(rows: list[list[str]]) -> str:
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
