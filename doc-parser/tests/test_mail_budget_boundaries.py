"""Actual container boundaries and a shared Unicode text projection budget."""
import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest
from docparser import parse_document
from docparser.embedded import AttachmentBudget
from docparser.source_model import ParseContext
from docx import Document
from docx.opc.packuri import PackURI
from docx.opc.part import Part
from lxml import etree
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import NumberObject

from tests.mail_fixtures import unicode_msg


def _container(path, payloads, *, names=None, declared_size=None):
    names = names or [f"child-{index}.bin" for index in range(len(payloads))]
    if path.suffix == ".eml":
        message = EmailMessage()
        message.set_content("Parent")
        for name, payload in zip(names, payloads):
            message.add_attachment(payload, maintype="application", subtype="octet-stream", filename=name)
        path.write_bytes(message.as_bytes())
    elif path.suffix == ".msg":
        path.write_bytes(unicode_msg(subject="", body="Parent", attachments=dict(zip(names, payloads))))
    elif path.suffix == ".xlsx":
        workbook = Workbook()
        workbook.active.append(["Parent"])
        workbook.save(path)
        with zipfile.ZipFile(path, "a") as archive:
            for name, payload in zip(names, payloads):
                archive.writestr(f"xl/embeddings/{name}", payload)
    elif path.suffix == ".docx":
        document = Document()
        document.add_paragraph("Parent")
        relation_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        word_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        office_ns = "urn:schemas-microsoft-com:office:office"
        for name, payload in zip(names, payloads):
            part = Part(PackURI(f"/word/embeddings/{name}"), "application/octet-stream", payload, document.part.package)
            rid = document.part.relate_to(part, relation_ns + "/oleObject")
            run = etree.SubElement(document.add_paragraph()._p, f"{{{word_ns}}}r")
            obj = etree.SubElement(run, f"{{{word_ns}}}object")
            ole = etree.SubElement(obj, f"{{{office_ns}}}OLEObject")
            ole.set("Type", "Embed")
            ole.set(f"{{{relation_ns}}}id", rid)
        document.save(path)
    else:
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        for name, payload in zip(names, payloads):
            attachment = writer.add_attachment(name, payload)
            if declared_size is not None:
                attachment.size = NumberObject(declared_size)
        writer.write(path)
    return path


FORMATS = ["eml", "msg", "docx", "xlsx", "pdf"]


@pytest.mark.parametrize("format_name", FORMATS)
@pytest.mark.parametrize("depth", [1, 2, 3])
def test_attachment_depth_boundary_in_real_containers(tmp_path, monkeypatch, format_name, depth):
    from docparser import parser

    child = b"From: child@example.test\r\n\r\nCHILD EVIDENCE\r\n"
    path = _container(tmp_path / f"parent.{format_name}", [child], names=["child.eml"])
    decoded_children = []
    original = parser.parse_eml

    def observe(*args, **kwargs):
        if kwargs.get("source_id") == "root/0":
            decoded_children.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(parser, "parse_eml", observe)
    context = ParseContext(path.name)
    blocks = parse_document(path, depth=depth - 1, context=context, attachments_dir=tmp_path / "attachments")
    marker = next(block for block in blocks if block.type == "attachment")
    if depth < 3:
        assert marker.meta["extraction_status"] == "parsed"
        assert any(block.text == "CHILD EVIDENCE" and block.meta["source_id"] == "root/0" for block in blocks)
        assert Path(marker.meta["saved_path"]).read_bytes() == child
        assert not context.warnings
        assert decoded_children == [True]
    else:
        assert marker.meta["extraction_status"] == "skipped_depth"
        if format_name in {"eml", "msg"}:
            # Mail admission rejects before transfer/CFB decoding. The parent
            # remains the downloadable original container.
            assert "saved_path" not in marker.meta
            assert context.warnings == [{"code": "attachment_depth_exceeded", "source_id": "root/0"}]
        else:
            # Legacy Office/PDF containers retain bounded original bytes at
            # the boundary; they do not recursively decode the child.
            assert Path(marker.meta["saved_path"]).read_bytes() == child
            assert context.warnings == [{"code": "attachment_depth_exceeded", "source_id": "root/0"}]
        assert not any("CHILD EVIDENCE" in block.text for block in blocks)
        assert decoded_children == []


@pytest.mark.parametrize("format_name", FORMATS)
@pytest.mark.parametrize("count", [2, 3, 4])
def test_attachment_node_boundary_and_retained_bytes(tmp_path, format_name, count):
    payloads = [bytes([index, 0, 255]) for index in range(count)]
    path = _container(tmp_path / f"parent.{format_name}", payloads)
    context = ParseContext(path.name)
    # MIME's plain body is also an admitted node; allow three attachments.
    budget = AttachmentBudget(max_nodes=3 + (format_name == "eml"))
    blocks = parse_document(path, context=context, budget=budget, attachments_dir=tmp_path / "attachments")
    markers = [block for block in blocks if block.type == "attachment"]
    admitted = [block for block in markers if block.meta["extraction_status"] == "saved"]
    assert len(admitted) == min(count, 3)
    assert [Path(block.meta["saved_path"]).read_bytes() for block in admitted] == payloads[:3]
    rejected = [block for block in markers if block.meta["extraction_status"] == "skipped_count"]
    assert bool(rejected) is (count > 3)
    assert all("saved_path" not in block.meta for block in rejected)
    assert bool(context.warnings) is (count > 3)


@pytest.mark.parametrize("format_name", ["eml", "msg"])
@pytest.mark.parametrize("size", [9, 10, 11])
def test_unicode_text_boundary_uses_codepoints_not_utf16(tmp_path, format_name, size):
    body = "😀Ж" * 6
    body = body[:size]
    path = tmp_path / f"unicode.{format_name}"
    if format_name == "eml":
        message = EmailMessage()
        message.set_content(body)
        path.write_bytes(message.as_bytes())
    else:
        path.write_bytes(unicode_msg(subject="", body=body))
    context = ParseContext(path.name)
    budget = AttachmentBudget(max_text_chars=10)
    blocks = parse_document(path, context=context, budget=budget)
    assert "".join(block.text for block in blocks) == body[:10]
    assert budget.remaining_text == 10 - min(size, 10)
    assert context.warnings == ([{"code": "text_limit_exceeded", "source_id": "root"}] if size > 10 else [])


@pytest.mark.parametrize("format_name", FORMATS)
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_sibling_text_budget_debited_once_and_originals_retained(tmp_path, format_name, offset):
    children = [b"From: child@example.test\r\n\r\nFIRST EVIDENCE\r\n",
                b"From: child@example.test\r\n\r\nSECOND EVIDENCE\r\n"]
    path = _container(tmp_path / f"parent.{format_name}", children, names=["first.eml", "second.eml"])
    full = parse_document(path, budget=AttachmentBudget(), attachments_dir=tmp_path / "baseline")
    full_size = sum(len(block.text) for block in full)
    budget = AttachmentBudget(max_text_chars=full_size + offset)
    context = ParseContext(path.name)
    limited = parse_document(path, budget=budget, context=context, attachments_dir=tmp_path / "limited")
    assert sum(len(block.text) for block in limited) == min(full_size, full_size + offset)
    assert budget.remaining_text == max(offset, 0)
    assert bool(context.warnings) is (offset < 0)
    markers = [block for block in limited if block.type == "attachment"]
    assert [Path(block.meta["saved_path"]).read_bytes() for block in markers] == children
    assert [source.source_id for source in context.sources] == ["root", "root/0", "root/1"]


@pytest.mark.parametrize("declared", [0, 1, 10, 11, 12])
@pytest.mark.parametrize("actual", [9, 10, 11])
def test_pdf_declared_and_actual_byte_boundaries(tmp_path, declared, actual):
    payload = b"x" * actual
    path = _container(tmp_path / "size.pdf", [payload], declared_size=declared)
    context = ParseContext(path.name)
    blocks = parse_document(path, context=context, budget=AttachmentBudget(total=10), attachments_dir=tmp_path / "attachments")
    marker = next(block for block in blocks if block.type == "attachment")
    if declared <= 10 and actual <= 10:
        assert marker.meta["extraction_status"] == "saved"
        assert Path(marker.meta["saved_path"]).read_bytes() == payload
        return
    assert marker.meta["extraction_status"] == "skipped_size"
    assert "saved_path" not in marker.meta
    # A blank PDF can generate a page raster. It is unrelated to the rejected
    # attachment; no copy of the payload is stored.
    assert all(file.read_bytes() != payload for file in (tmp_path / "attachments").rglob("*") if file.is_file())


@pytest.mark.parametrize("format_name", ["eml", "docx", "xlsx", "pdf"])
@pytest.mark.parametrize("combined_size", [9, 10, 11])
def test_sibling_actual_bytes_share_root_budget_at_boundary(tmp_path, format_name, combined_size):
    payloads = [b"a" * 4, b"b" * (combined_size - 4)]
    path = _container(tmp_path / f"parent.{format_name}", payloads)
    budget = AttachmentBudget(total=10)
    blocks = parse_document(path, budget=budget, attachments_dir=tmp_path / "attachments")
    markers = [block for block in blocks if block.type == "attachment"]
    assert Path(markers[0].meta["saved_path"]).read_bytes() == payloads[0]
    if combined_size <= 10:
        assert markers[1].meta["extraction_status"] == "saved"
        assert Path(markers[1].meta["saved_path"]).read_bytes() == payloads[1]
        assert budget.remaining == 10 - combined_size
    else:
        assert markers[1].meta["extraction_status"] == "skipped_size"
        assert "saved_path" not in markers[1].meta
        assert budget.remaining <= 6
