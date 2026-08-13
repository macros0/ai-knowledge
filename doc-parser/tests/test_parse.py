"""Тесты извлечения блоков из DOCX (текст, заголовки, таблицы, комментарии), XLSX и PDF."""
from pathlib import Path

import pytest

from docparser import ParseError, SUPPORTED_EXTENSIONS, parse_document
from tests.fixtures import (
    inject_comment,
    make_docx,
    make_pdf,
    make_xlsx,
)


class TestDispatch:
    def test_supported_extensions(self):
        assert SUPPORTED_EXTENSIONS == {".docx", ".xlsx", ".pdf"}

    def test_unknown_extension_raises(self, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("hello", encoding="utf-8")
        with pytest.raises(ParseError):
            parse_document(f, filename="doc.txt")

    def test_extension_from_filename_override(self, tmp_path: Path):
        f = tmp_path / "noext"
        f.write_bytes(b"not a real docx")
        with pytest.raises(Exception):
            parse_document(f, filename="noext.docx")


class TestDocx:
    def test_paragraphs_and_heading(self, tmp_path: Path):
        docx = make_docx(tmp_path / "doc.docx", heading="Заголовок", paragraphs=["Абзац один", "Абзац два"])
        blocks = parse_document(docx)
        types = [b.type for b in blocks]
        assert types == ["heading", "paragraph", "paragraph"]
        assert blocks[0].text == "Заголовок"
        assert blocks[0].level == 1
        assert blocks[1].text == "Абзац один"

    def test_comments(self, tmp_path: Path):
        docx = make_docx(tmp_path / "doc.docx", paragraphs=["Абзац с замечанием"])
        inject_comment(docx, author="Рецензент", text="Поправьте формулировку")
        blocks = parse_document(docx)
        comments = [b for b in blocks if b.type == "comment"]
        assert len(comments) == 1
        assert comments[0].text == "Поправьте формулировку"
        assert comments[0].meta["author"] == "Рецензент"

    def test_empty_paragraphs_skipped(self, tmp_path: Path):
        docx = make_docx(tmp_path / "doc.docx", paragraphs=["   ", "", "Текст"])
        blocks = parse_document(docx)
        assert [b.text for b in blocks if b.type == "paragraph"] == ["Текст"]

    def test_code_paragraphs_detected(self, tmp_path: Path):
        from docx import Document

        doc = Document()
        p1 = doc.add_paragraph()
        r1 = p1.add_run("def bridge():")
        r1.font.name = "Consolas"
        p2 = doc.add_paragraph()
        r2 = p2.add_run("    return True")
        r2.font.name = "Consolas"
        doc.add_paragraph("Обычный текст")
        path = tmp_path / "code.docx"
        doc.save(str(path))

        blocks = parse_document(path)
        code = [b for b in blocks if b.type == "code"]
        assert len(code) == 1
        assert "def bridge():" in code[0].text
        assert "    return True" in code[0].text
        assert [b.text for b in blocks if b.type == "paragraph"] == ["Обычный текст"]


class TestXlsx:
    def test_sheets_to_tables(self, tmp_path: Path):
        xlsx = make_xlsx(tmp_path / "t.xlsx", sheet="Правила", rows=[["Код", "Описание"], [1, "Первое"], [2, "Второе"]])
        blocks = parse_document(xlsx)
        headings = [b for b in blocks if b.type == "heading"]
        tables = [b for b in blocks if b.type == "table"]
        assert headings[0].text == "Таблица: Правила"
        assert len(tables) == 1
        assert "Код" in tables[0].text
        assert "Второе" in tables[0].text


class TestPdf:
    def test_text_by_pages(self, tmp_path: Path):
        pdf = make_pdf(tmp_path / "p.pdf", "Строка из мини-pdf")
        blocks = parse_document(pdf)
        assert blocks
        assert all(b.type == "paragraph" for b in blocks)
        assert any("мини-pdf" in b.text for b in blocks)
        assert blocks[0].meta["page"] == 1
