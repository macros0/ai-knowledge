"""Разбор DOCX: абзацы, заголовки, таблицы, комментарии рецензентов и встроенные объекты (OLE)."""
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from docparser.blocks import Block
from docparser.embedded import Attachment, process_embedded, save_image_file

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
O_NS = "{urn:schemas-microsoft-com:office:office}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"

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


def parse_docx(path: str | Path, attachments_dir: str | Path | None = None) -> list[Block]:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    comments = _load_comments(path, doc)

    blocks: list[Block] = []
    used_comment_ids: set[str] = set()
    code_lines: list[str] | None = None
    image_count = 0

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines is not None:
            blocks.append(Block("code", "\n".join(code_lines)))
            code_lines = None

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            style = (para.style.name or "").lower() if para.style else ""
            raw = para.text
            text = raw.strip()
            cids = _comment_ids(child)
            used_comment_ids |= cids

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
                elif text:
                    blocks.append(Block("paragraph", text))

            for cid in cids:
                if cid in comments:
                    blocks.append(
                        Block("comment", comments[cid]["text"], meta={"author": comments[cid]["author"]})
                    )

            for idx, ole in enumerate(_find_ole_objects(child)):
                att = _resolve_ole_attachment(doc, ole)
                if att is not None:
                    blocks.extend(process_embedded(att.data, att.name, att.prog_id, att.caption, attachments_dir, idx))

            image_count = _emit_inline_images(blocks, doc, child, attachments_dir, image_count)

        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            md = _table_to_markdown([[cell.text.strip() for cell in row.cells] for row in table.rows])
            if md:
                blocks.append(Block("table", md))
            for cell in child.iter(qn("w:tc")):
                for cell_p in cell.iter(qn("w:p")):
                    image_count = _emit_inline_images(blocks, doc, cell_p, attachments_dir, image_count)

    flush_code()

    for cid, c in comments.items():
        if cid not in used_comment_ids:
            blocks.append(Block("comment", c["text"], meta={"author": c["author"]}))

    return blocks


# ---------------------------------------------------------------- comments
def _load_comments(path: str | Path, doc) -> dict[str, dict]:
    comments: dict[str, dict] = {}
    try:
        for c in doc.comments:
            cid = getattr(c, "comment_id", None) or getattr(c, "id", None)
            comments[str(cid)] = {"author": c.author or "", "text": c.text or ""}
    except Exception:
        pass
    if comments:
        return comments
    try:
        with zipfile.ZipFile(str(path)) as zf:
            if "word/comments.xml" in zf.namelist():
                root = ET.fromstring(zf.read("word/comments.xml"))
                for el in root.iter(f"{W_NS}comment"):
                    cid = el.get(f"{W_NS}id")
                    if cid is None:
                        continue
                    author = el.get(f"{W_NS}author", "")
                    text = "".join(t.text or "" for t in el.iter(f"{W_NS}t"))
                    comments[cid] = {"author": author, "text": text}
    except Exception:
        pass
    return comments


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
