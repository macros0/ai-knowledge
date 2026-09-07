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

    def test_deep_nesting_stops_at_depth_limit(self, tmp_path: Path):
        """docx → docx → docx → xlsx: уровни 1-2 разбираются, уровень 3 — маркер."""
        # Внутренний docx B со встроенной таблицей (его xlsx — уровень 3).
        inner = make_docx_with_embedded_xlsx(tmp_path / "b.docx", xlsx_bytes())
        # Средний docx A содержит B как встроенный docx (уровень 2).
        middle = make_docx_with_embedded_xlsx(tmp_path / "a.docx", inner.read_bytes())
        # Внешний docx содержит A (уровень 1).
        outer = make_docx_with_embedded_xlsx(tmp_path / "top.docx", middle.read_bytes())

        blocks = parse_document(outer)

        markers = [b for b in blocks if b.type == "attachment"]
        assert markers, "маркеры вложений должны присутствовать на каждом уровне"
        # Уровень 1 (A) и уровень 2 (B) разбираются — их тексты видны.
        texts = "\n".join(b.text for b in blocks)
        assert "Перед встроенной таблицей" in texts
        # Уровень 3 (xlsx внутри B) — только маркер, контент таблицы не разбирается.
        assert "table" not in [b.type for b in blocks]
        deep_markers = [b for b in markers if b.meta.get("note") == "превышена глубина вложенности"]
        assert deep_markers, "уровень 3 должен получить маркер с note"

    def test_deep_chain_content_one_level_parsed(self, tmp_path: Path):
        """Прямое вложение (уровень 1) и его ребёнок (уровень 2) разбираются."""
        inner = make_docx_with_embedded_xlsx(tmp_path / "inner.docx", xlsx_bytes())
        outer = make_docx_with_embedded_xlsx(tmp_path / "top.docx", inner.read_bytes())

        blocks = parse_document(outer)
        # Уровень 2 — xlsx внутри inner: разбирается (2 < 3), таблица видна.
        assert "table" in [b.type for b in blocks]


class TestAttachmentLimits:
    """Анти-DoS: кумулятивный бюджет и лимит одного вложения (zip-bomb)."""

    def test_budget_exhaustion_marks_attachment(self):
        from docparser.embedded import AttachmentBudget, process_embedded

        payload = xlsx_bytes()
        budget = AttachmentBudget(total=len(payload))  # хватает ровно на одно вложение
        blocks_first = process_embedded(payload, "first.xlsx", budget=budget)
        assert "table" in [b.type for b in blocks_first]

        blocks_second = process_embedded(payload, "second.xlsx", budget=budget)
        markers = [b for b in blocks_second if b.type == "attachment"]
        assert len(blocks_second) == 1  # без рекурсии
        assert markers[0].meta["note"] == "превышен лимит размера вложений"

    def test_single_payload_cap(self, monkeypatch):
        from docparser import embedded
        from docparser.embedded import process_embedded

        monkeypatch.setattr(embedded, "MAX_ATTACHMENT_PAYLOAD", 10)
        blocks = process_embedded(xlsx_bytes(), "huge.xlsx")
        assert len(blocks) == 1
        marker = blocks[0]
        assert marker.type == "attachment"
        assert marker.meta["note"] == "превышен лимит размера вложений"

    def test_oversized_raw_attachment_not_saved(self, tmp_path: Path, monkeypatch):
        from docparser import embedded
        from docparser.embedded import process_embedded

        monkeypatch.setattr(embedded, "MAX_ATTACHMENT_PAYLOAD", 10)
        # kind="other" → ветка сохранения сырых данных.
        blocks = process_embedded(b"M" * 100, "raw.bin", attachments_dir=tmp_path)
        assert len(blocks) == 1
        assert blocks[0].meta.get("note") == "превышен лимит размера вложений"
        assert not blocks[0].meta.get("saved_path")
        assert not list(tmp_path.iterdir()), "файл сверх лимита не должен попадать на диск"


class TestAttachmentOriginMeta:
    """from_attachment / attachment_name в meta — основа программного тега «attachment»."""

    def test_parsed_blocks_marked_as_from_attachment(self, tmp_path: Path):
        docx = make_docx_with_embedded_xlsx(tmp_path / "x.docx", xlsx_bytes())
        blocks = parse_document(docx)
        marker = next(b for b in blocks if b.type == "attachment")
        assert marker.meta["from_attachment"] is True
        assert marker.meta["attachment_name"] == "embedded.xlsx"

        table = next(b for b in blocks if b.type == "table")
        assert table.meta.get("from_attachment") is True
        assert table.meta.get("attachment_name") == "embedded.xlsx"

        # блоки самого родителя (до/после вложения) — без признака
        parent_blocks = [b for b in blocks if not b.meta.get("from_attachment")]
        assert parent_blocks, "текст родителя не должен помечаться как вложение"

    def test_attachment_name_is_basename_without_absolute_path(self, tmp_path: Path):
        docx = make_docx_with_embedded_xlsx(tmp_path / "x.docx", xlsx_bytes())
        blocks = parse_document(docx, attachments_dir=tmp_path / "att")
        for b in blocks:
            name = b.meta.get("attachment_name")
            if name is not None:
                assert ":" not in name
                assert "/" not in name
                assert "\\" not in name

    def test_marker_always_carries_from_attachment(self, tmp_path: Path):
        from docparser.embedded import process_embedded

        # kind="other" → сохраняется сырым, маркер без рекурсии
        blocks = process_embedded(b"M" * 16, "raw.bin", attachments_dir=tmp_path)
        assert len(blocks) == 1
        assert blocks[0].meta["from_attachment"] is True
        assert blocks[0].meta["attachment_name"] == "raw.bin"

    def test_nested_attachment_inner_wins(self, tmp_path: Path):
        """Вложенный docx содержит xlsx: блоки уровня 2 сохраняют имя внутреннего."""
        inner = make_docx_with_embedded_xlsx(tmp_path / "inner.docx", xlsx_bytes())
        outer = make_docx_with_embedded_xlsx(tmp_path / "top.docx", inner.read_bytes())
        blocks = parse_document(outer)
        tables = [b for b in blocks if b.type == "table"]
        assert tables, "таблица внутреннего xlsx должна быть распарсена"
        assert tables[0].meta.get("from_attachment") is True
        assert tables[0].meta.get("attachment_name") == "embedded.xlsx"



    def test_render_capped_at_limit(self, tmp_path: Path, monkeypatch):
        """PDF из пустых страниц-сканов: рендер ограничен _RENDER_PAGE_LIMIT."""
        from pypdf import PdfWriter

        from docparser import pdf_parser

        writer = PdfWriter()
        for _ in range(250):
            writer.add_blank_page(width=200, height=200)
        pdf = tmp_path / "scan.pdf"
        with open(pdf, "wb") as f:
            writer.write(f)

        calls = {"n": 0}
        monkeypatch.setattr(pdf_parser, "_open_render_doc", lambda path: object())
        monkeypatch.setattr(
            pdf_parser, "_render_page_image", lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
        )

        parse_document(pdf, attachments_dir=tmp_path / "att")

        assert calls["n"] == pdf_parser._RENDER_PAGE_LIMIT
