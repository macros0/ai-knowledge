"""Unsupported text containers must not silently look fully extracted."""
import io
import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest
from docparser import parse_document_result
from docparser.embedded import AttachmentBudget, process_embedded
from docparser.source_model import ParseContext

from tests.mail_fixtures import compound_bytes


def _payload(format_name):
    if format_name in {"doc", "xls"}:
        # Genuine CFB layout, synthetic stream contents; not a legacy decoder test.
        stream = "WordDocument" if format_name == "doc" else "Workbook"
        return compound_bytes({stream: b"HIDDEN LEGACY TEXT"})
    if format_name == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("note.txt", "HIDDEN ARCHIVED TEXT")
        return buffer.getvalue()
    # Signature-only probes: unsupported classification, not valid mail stores.
    return (b"\x78\x9f\x3e\x22" if format_name == "tnef" else b"!BDN") + b"\0" * 32


@pytest.mark.parametrize("format_name", ["doc", "xls", "pst", "ost", "zip", "tnef"])
@pytest.mark.parametrize("renamed", [False, True])
def test_unsupported_mail_attachment_warns_without_losing_original_or_sibling(tmp_path, format_name, renamed):
    payload = _payload(format_name)
    name = "object.bin" if renamed else f"legacy.{format_name}"
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.set_content("ROOT STILL READABLE")
    message.add_attachment(payload, maintype="application", subtype="octet-stream", filename=name)
    sibling = EmailMessage()
    sibling["From"] = "approver@example.test"
    sibling.set_content("SIBLING STILL READABLE")
    message.add_attachment(sibling, filename="good.eml")
    path = tmp_path / "mixed.eml"
    path.write_bytes(message.as_bytes())

    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")

    assert result.warnings == [{"code": "unsupported_attachment_format", "source_id": "root/0"}]
    assert result.sources[1].warnings == result.warnings
    marker = next(block for block in result.blocks if block.type == "attachment")
    assert marker.meta["extraction_status"] == "unsupported"
    assert Path(marker.meta["saved_path"]).read_bytes() == payload
    assert any(block.text == "ROOT STILL READABLE" and block.meta["source_id"] == "root"
               for block in result.blocks)
    assert any(block.text == "SIBLING STILL READABLE" and block.meta["source_id"] == "root/1"
               for block in result.blocks)
    assert not any("HIDDEN" in block.text for block in result.blocks)


def test_unsupported_container_warns_without_attachment_directory():
    context = ParseContext("root.eml")
    blocks = process_embedded(_payload("zip"), "archive.zip", context=context)
    assert context.warnings == [{"code": "unsupported_attachment_format", "source_id": "root/0"}]
    assert blocks[0].meta["extraction_status"] == "unsupported"
    assert "saved_path" not in blocks[0].meta


@pytest.mark.parametrize("name,payload", [
    ("logo.png", b"\x89PNG\r\n\x1a\n"), ("opaque.bin", b"opaque bytes"),
    ("opaque.msg", b"not an MSG"),
])
def test_opaque_nontext_attachment_is_saved_without_text_loss_warning(tmp_path, name, payload):
    context = ParseContext("root.eml")
    blocks = process_embedded(payload, name, context=context, attachments_dir=tmp_path)
    assert context.warnings == []
    assert blocks[0].meta["extraction_status"] == "saved"
    assert Path(blocks[0].meta["saved_path"]).read_bytes() == payload


@pytest.mark.parametrize("options,status", [
    ({"depth": 3}, "skipped_depth"),
    ({"budget": AttachmentBudget(total=0)}, "skipped_size"),
])
def test_admission_limit_takes_priority_over_unsupported_format(tmp_path, options, status):
    context = ParseContext("root.eml")
    blocks = process_embedded(_payload("zip"), "archive.zip", context=context,
                              attachments_dir=tmp_path, **options)
    assert blocks[0].meta["extraction_status"] == status
    assert not any(warning["code"] == "unsupported_attachment_format" for warning in context.warnings)


def test_supported_mail_bytes_are_parsed_despite_unsupported_filename(tmp_path):
    context = ParseContext("root.eml")
    blocks = process_embedded(b"From: sender@example.test\r\n\r\nACTUAL MAIL\r\n",
                              "misnamed.doc", context=context, attachments_dir=tmp_path)
    assert context.warnings == []
    assert blocks[0].meta["extraction_status"] == "parsed"
    assert any(block.text == "ACTUAL MAIL" for block in blocks)
