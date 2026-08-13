"""Тесты вложений: маркер-блоки, сохранение в каталог и рекурсивный разбор встроенных xlsx/pdf."""
from pathlib import Path

from docparser import parse_document
from tests.fixtures import (
    make_docx_with_embedded_xlsx,
    make_pdf,
    xlsx_bytes,
)


class TestEmbeddedDocx:
    def test_embedded_xlsx_recursion(self, tmp_path: Path):
        docx = make_docx_with_embedded_xlsx(tmp_path / "x.docx", xlsx_bytes())
        blocks = parse_document(docx)
        types = [b.type for b in blocks]
        assert "attachment" in types
        assert "table" in types
        marker = next(b for b in blocks if b.type == "attachment")
        assert marker.meta["kind"] == "zip"
        assert marker.meta["name"] == "embedded.xlsx"
        table = next(b for b in blocks if b.type == "table")
        assert "Правило" in table.text

    def test_embedded_pdf_recursion(self, tmp_path: Path):
        pdf = make_pdf(tmp_path / "p.pdf", "Текст вложенного pdf")
        docx = make_docx_with_embedded_xlsx(
            tmp_path / "p.docx", pdf.read_bytes(), prog_id="AcroExch.Document"
        )
        blocks = parse_document(docx)
        marker = next(b for b in blocks if b.type == "attachment")
        assert marker.meta["kind"] == "pdf"
        assert any("вложенного pdf" in b.text for b in blocks)

    def test_attachments_dir_saves_file(self, tmp_path: Path):
        payload = xlsx_bytes()
        docx = make_docx_with_embedded_xlsx(tmp_path / "x.docx", payload)
        att_dir = tmp_path / "attachments"
        blocks = parse_document(docx, attachments_dir=att_dir)
        marker = next(b for b in blocks if b.type == "attachment")
        assert marker.meta["saved_path"]
        saved = Path(marker.meta["saved_path"])
        assert saved.exists()
        assert saved.read_bytes() == payload

    def test_embedded_same_stem_gets_unique_name(self, tmp_path: Path):
        docx = make_docx_with_embedded_xlsx(tmp_path / "x.docx", xlsx_bytes())
        att_dir = tmp_path / "attachments"
        parse_document(docx, attachments_dir=att_dir)
        parse_document(docx, attachments_dir=att_dir)
        names = {p.name for p in att_dir.glob("*.xlsx")}
        assert names == {"embedded.xlsx", "embedded-1.xlsx"}


class TestRecursionDepth:
    def test_attachment_marker_always_present(self, tmp_path: Path):
        docx = make_docx_with_embedded_xlsx(tmp_path / "x.docx", xlsx_bytes())
        blocks = parse_document(docx)
        assert blocks[0].type == "paragraph"  # текст до вложения
        marker_index = next(i for i, b in enumerate(blocks) if b.type == "attachment")
        # маркер идёт перед рекурсивно разобранным контентом
        assert blocks[marker_index + 1].type == "heading"
