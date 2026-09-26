"""Тесты вложений: маркер-блоки, сохранение в каталог и рекурсивный разбор встроенных xlsx/pdf."""
from pathlib import Path

import pytest
from docparser import parse_document, parse_document_result

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
        assert names == {"source-root-0.xlsx", "source-root-0-1.xlsx"}


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

    @pytest.mark.parametrize("depth", [3, 4])
    def test_forbidden_depth_does_not_unwrap_and_preserves_bounded_original(self, tmp_path, monkeypatch, depth):
        from docparser import embedded
        from docparser.source_model import ParseContext

        def forbidden_unwrap(_payload):
            raise AssertionError("forbidden depth must be rejected before OLE/ZIP inspection")

        monkeypatch.setattr(embedded, "unwrap_ole", forbidden_unwrap)
        payload = b"original\0opaque\0bytes"
        blocks = embedded.process_embedded(
            payload, "deep.bin", depth=depth, attachments_dir=tmp_path,
            budget=embedded.AttachmentBudget(total=len(payload)), context=ParseContext("root.docx"),
        )
        assert len(blocks) == 1
        assert blocks[0].meta["extraction_status"] == "skipped_depth"
        assert Path(blocks[0].meta["saved_path"]).read_bytes() == payload

    def test_oversized_raw_input_is_rejected_before_unwrap(self, monkeypatch):
        from docparser import embedded

        def forbidden_unwrap(_payload):
            raise AssertionError("oversized OLE must not be opened")

        monkeypatch.setattr(embedded, "MAX_ATTACHMENT_PAYLOAD", 10)
        monkeypatch.setattr(embedded, "unwrap_ole", forbidden_unwrap)
        blocks = embedded.process_embedded(b"large input" * 10, "large.bin")
        assert blocks[0].meta["extraction_status"] == "skipped_size"

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

    def test_raw_attachments_share_byte_budget_and_return_status(self, tmp_path: Path):
        """Нераспознаваемые файлы тоже не должны обходить общий лимит."""
        from docparser.embedded import AttachmentBudget, process_embedded

        budget = AttachmentBudget(total=10)
        first = process_embedded(b"A" * 6, "first.bin", attachments_dir=tmp_path, budget=budget)
        second = process_embedded(b"B" * 5, "second.bin", attachments_dir=tmp_path, budget=budget)

        assert first[0].meta["extraction_status"] == "saved"
        assert second[0].meta["extraction_status"] == "skipped_size"
        assert not (tmp_path / "second.bin").exists()

    def test_untrusted_attachment_name_cannot_create_path_or_device_file(self, tmp_path: Path):
        from docparser.embedded import process_embedded

        blocks = process_embedded(b"safe", "..\\CON:mail.eml", attachments_dir=tmp_path)

        saved = Path(blocks[0].meta["saved_path"])
        assert saved.parent == tmp_path
        assert saved.suffix == ".bin"
        assert ":" not in saved.name

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

    def test_node_limit_stops_many_small_attachments(self):
        """Регрессия: byte budget не защищает от тысячи малых частей MIME/MSG."""
        from docparser.embedded import AttachmentBudget, process_embedded

        payload = xlsx_bytes()
        budget = AttachmentBudget(total=len(payload) * 2, max_nodes=1)

        assert "table" in [block.type for block in process_embedded(payload, "first.xlsx", budget=budget)]
        blocks = process_embedded(payload, "second.xlsx", budget=budget)

        assert len(blocks) == 1
        assert blocks[0].meta["note"] == "превышен лимит количества вложений"
        assert blocks[0].meta["extraction_status"] == "skipped_count"

    def test_spoofed_msg_name_is_saved_as_opaque_attachment_not_parsed_as_mail(self, tmp_path: Path):
        """Расширение/ProgID — подсказка; MSG требует CFB+MAPI property stream."""
        from docparser.embedded import process_embedded

        blocks = process_embedded(
            b"this is not an Outlook compound file",
            "forwarded.msg",
            prog_id="Outlook.FileMsg.15",
            attachments_dir=tmp_path,
        )

        assert len(blocks) == 1
        marker = blocks[0]
        assert marker.type == "attachment"
        assert marker.meta["kind"] == "other"
        assert marker.meta["extraction_status"] == "saved"
        assert marker.meta["saved_path"].endswith(".bin")


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

    def test_parse_result_tracks_docx_embedded_file_as_child_source(self, tmp_path: Path):
        """Регрессия: путь происхождения должен работать и вне EML."""
        docx = make_docx_with_embedded_xlsx(tmp_path / "contract.docx", xlsx_bytes())

        result = parse_document_result(docx)

        assert [(node.source_id, node.parent_source_id, node.kind, node.display_name) for node in result.sources] == [
            ("root", None, "document", "contract.docx"),
            ("root/0", "root", "attachment", "embedded.xlsx"),
        ]
        table = next(block for block in result.blocks if block.type == "table")
        assert table.meta["source_id"] == "root/0"



    def test_render_capped_at_limit(self, tmp_path: Path):
        """PDF из пустых страниц-сканов: рендер ограничен _RENDER_PAGE_LIMIT."""
        from docparser import pdf_parser
        from pypdf import PdfWriter

        writer = PdfWriter()
        for _ in range(250):
            writer.add_blank_page(width=200, height=200)
        pdf = tmp_path / "scan.pdf"
        with open(pdf, "wb") as f:
            writer.write(f)

        calls = {"n": 0}

        class FakeDocument:
            closed = False

            def page_count(self):
                return 250

            def extract_text(self, page_index):
                return ""

            def extract_images(self, page_index):
                return []

            def render_page_jpeg(self, page_index, *, dpi, quality):
                calls["n"] += 1
                return b"jpeg"

            def iter_attachments(self):
                return []

            def close(self):
                self.closed = True

        class FakeProvider:
            def open(self, path):
                return document

        document = FakeDocument()
        blocks = pdf_parser.parse_pdf(
            pdf,
            attachments_dir=tmp_path / "att",
            provider_factory=FakeProvider,
        )

        assert calls["n"] == pdf_parser._RENDER_PAGE_LIMIT
        assert len([block for block in blocks if block.type == "image"]) == pdf_parser._RENDER_PAGE_LIMIT
        assert document.closed


def test_failed_embedded_mail_keeps_sibling_and_records_stable_warning(monkeypatch):
    from docparser.blocks import Block
    from docparser.embedded import process_embedded
    from docparser.source_model import ParseContext

    context = ParseContext("root.docx")

    def parse_or_fail(payload, ext, attachment, *_args, **_kwargs):
        if attachment.name == "broken.eml":
            raise ValueError("corrupt nested message")
        return [Block("paragraph", "Срок 12 дней.")]

    monkeypatch.setattr("docparser.embedded._parse_payload", parse_or_fail)
    broken = process_embedded(
        b"From: broken@example.test\n\ncontent",
        "broken.eml",
        context=context,
    )
    sibling = process_embedded(
        b"From: good@example.test\n\ncontent",
        "good.eml",
        context=context,
    )

    assert broken[0].meta["extraction_status"] == "skipped_parse"
    assert sibling[0].meta["extraction_status"] == "parsed"
    assert context.warnings == [{"code": "mail_parse_failed", "source_id": "root/0"}]
    assert context.sources[1].warnings == [{"code": "mail_parse_failed", "source_id": "root/0"}]


def test_direct_embedded_parse_keeps_one_budget_for_nested_mail(tmp_path: Path, monkeypatch):
    """The convenience entry point must not reset the budget in nested parsing."""
    from email.message import EmailMessage

    from docparser import embedded
    from docparser.embedded import AttachmentBudget, process_embedded

    child_payload = b"X" * 400
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["Subject"] = "Outer"
    message.set_content("Body")
    message.add_attachment(
        child_payload,
        maintype="application",
        subtype="octet-stream",
        filename="child.bin",
    )
    outer = message.as_bytes()
    assert len(outer) < 1200
    monkeypatch.setattr(embedded, "_default_budget", lambda: AttachmentBudget(total=1200))

    blocks = process_embedded(outer, "outer.eml", attachments_dir=tmp_path)

    child_marker = next(block for block in blocks if block.meta.get("name") == "child.bin")
    assert child_marker.meta["extraction_status"] == "skipped_size"
    assert not child_marker.meta.get("saved_path")
    assert not (tmp_path / "child.bin").exists()


def test_source_id_generates_storage_name_while_preserving_display_metadata(tmp_path: Path):
    from docparser.embedded import process_embedded
    from docparser.source_model import ParseContext

    context = ParseContext("root.docx")
    blocks = process_embedded(
        b"opaque",
        "..\\same name.msg",
        attachments_dir=tmp_path,
        context=context,
    )

    marker = blocks[0]
    assert marker.meta["name"] == "..\\same name.msg"
    assert Path(marker.meta["saved_path"]).name == "source-root-0.bin"


def test_attachment_storage_refuses_symlink_destination(tmp_path: Path):
    import os

    import pytest
    from docparser.embedded import process_embedded
    from docparser.source_model import ParseContext

    outside = tmp_path / "outside"
    outside.mkdir()
    destination = tmp_path / "attachments"
    try:
        os.symlink(outside, destination, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable in this environment: {exc}")

    context = ParseContext("root.eml")
    marker = process_embedded(
        b"opaque bytes",
        "attachment.bin",
        attachments_dir=destination,
        context=context,
    )[0]

    assert marker.meta["extraction_status"] == "skipped_storage"
    assert not list(outside.iterdir())
    assert context.warnings == [{"code": "attachment_storage_blocked", "source_id": "root/0"}]


def test_attachment_storage_refuses_marked_reparse_destination(tmp_path: Path, monkeypatch):
    from docparser.embedded import process_embedded
    from docparser.source_model import ParseContext

    destination = tmp_path / "attachments"
    context = ParseContext("root.eml")
    monkeypatch.setattr("docparser.embedded._is_reparse_point", lambda path: path == destination)

    marker = process_embedded(
        b"opaque bytes",
        "attachment.bin",
        attachments_dir=destination,
        context=context,
    )[0]

    assert marker.meta["extraction_status"] == "skipped_storage"
    assert not destination.exists()


def test_attachment_storage_refuses_marked_reparse_parent(tmp_path: Path, monkeypatch):
    from docparser.embedded import process_embedded
    from docparser.source_model import ParseContext

    destination = tmp_path / "redirected" / "attachments"
    reparse_parent = destination.parent
    context = ParseContext("root.eml")
    monkeypatch.setattr(
        "docparser.embedded._is_reparse_point",
        lambda path: path == reparse_parent,
    )

    marker = process_embedded(
        b"opaque bytes",
        "attachment.bin",
        attachments_dir=destination,
        context=context,
    )[0]

    assert marker.meta["extraction_status"] == "skipped_storage"
    assert not destination.exists()
