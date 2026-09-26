"""Actual CFB bytes pass the native MSG reader, not a mocked message model."""
import io
import struct
from datetime import UTC, datetime
from email.utils import parseaddr
from pathlib import Path

import pytest
from docparser import parse_document_result

from tests.fixtures import make_docx_with_embedded_xlsx
from tests.mail_fixtures import compound_bytes, nested_msg, rtf_only_msg, unicode_msg

FIXTURE = Path(__file__).parent / "fixtures/mail/synthetic-unicode-attachment.msg"


@pytest.mark.parametrize("compressed", [True, False])
def test_rtf_only_msg_is_explicitly_unsupported_without_opening_rtf_stream(tmp_path, monkeypatch, compressed):
    import olefile

    payload = rtf_only_msg(compressed=compressed)
    suffix = "compressed" if compressed else "uncompressed"
    assert payload == FIXTURE.with_name(f"synthetic-rtf-only-{suffix}.msg").read_bytes()
    path = tmp_path / "rtf-only.msg"
    path.write_bytes(payload)
    original_openstream = olefile.OleFileIO.openstream

    def guarded_openstream(ole, stream_name):
        parts = stream_name.split("/") if isinstance(stream_name, str) else stream_name
        assert parts[-1] != "__substg1.0_10090102", "unsupported RTF must not be read or decompressed"
        return original_openstream(ole, stream_name)

    monkeypatch.setattr(olefile.OleFileIO, "openstream", guarded_openstream)
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == [{"code": "unsupported_rtf_body", "source_id": "root"}]
    assert not any(block.type == "paragraph" for block in result.blocks)
    assert result.sources[0].metadata["subject"] == "Решение 3509"
    marker = next(block for block in result.blocks if block.type == "attachment")
    assert Path(marker.meta["saved_path"]).read_bytes() == b"proof\0bytes"
    assert path.read_bytes() == payload


def test_root_msg_reads_filetime_after_root_property_header(tmp_path):
    import olefile

    with olefile.OleFileIO(io.BytesIO(unicode_msg())) as ole:
        streams = {"/".join(path): ole.openstream(path).read() for path in ole.listdir()}
    sent = datetime(2026, 9, 25, 7, 30, tzinfo=UTC)
    delta = sent - datetime(1601, 1, 1, tzinfo=UTC)
    ticks = (delta.days * 86400 + delta.seconds) * 10_000_000
    streams["__properties_version1.0"] += struct.pack("<IIQ", 0x00390040, 6, ticks)
    # No headers: the timestamp must come from the root fixed-property stream.
    del streams["__substg1.0_007D001F"]
    path = tmp_path / "filetime.msg"
    path.write_bytes(compound_bytes(streams))
    meta = parse_document_result(path).sources[0].metadata
    assert meta["sent_at"] == sent.isoformat()
    assert meta["date_raw"] == sent.isoformat()


def test_nested_rtf_only_msg_warns_on_child_and_keeps_sibling_bytes(tmp_path):
    path = tmp_path / "nested-rtf-only.msg"
    path.write_bytes(nested_msg(unicode_msg(body="VISIBLE PARENT"), rtf_only_msg()))
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == [{"code": "unsupported_rtf_body", "source_id": "root/0"}]
    assert [(block.meta["source_id"], block.text) for block in result.blocks if block.type == "paragraph"] == [
        ("root", "VISIBLE PARENT"),
    ]
    marker = next(block for block in result.blocks if block.meta.get("source_id") == "root/0/0")
    assert Path(marker.meta["saved_path"]).read_bytes() == b"proof\0bytes"


def test_non_message_compound_file_is_rejected_as_root_msg(tmp_path):
    path = tmp_path / "spoofed.msg"
    path.write_bytes(compound_bytes({"OtherData": b"This is not a MAPI message"}))
    with pytest.raises(ValueError, match="MAPI"):
        parse_document_result(path)


@pytest.mark.parametrize("in_table", [False, True])
def test_docx_msg_xlsx_preserves_ownership_and_original_bytes(tmp_path, in_table):
    docx = make_docx_with_embedded_xlsx(
        tmp_path / "approval.docx", FIXTURE.read_bytes(), prog_id="Outlook.File.msg.15",
        filename="approval.msg", in_table=in_table,
    )
    result = parse_document_result(docx, attachments_dir=tmp_path / "attachments")
    assert [(node.source_id, node.parent_source_id, node.kind) for node in result.sources] == [
        ("root", None, "document"), ("root/0", "root", "mail"), ("root/0/0", "root/0", "attachment"),
    ]
    assert result.warnings == []
    meta = result.sources[1].metadata
    assert meta["subject"] == "Решение 3509"
    assert parseaddr(meta["sender"])[1] == "approver@example.test"
    assert meta["sent_at"] == "2026-09-25T07:30:00+00:00"
    tables = [block for block in result.blocks if block.type == "table"]
    assert any("3509" in block.text and "12" in block.text and block.meta["source_id"] == "root/0/0" for block in tables)
    markers = {block.meta["source_id"]: block for block in result.blocks if block.type == "attachment"}
    assert Path(markers["root/0"].meta["saved_path"]).read_bytes() == FIXTURE.read_bytes()
    import olefile
    with olefile.OleFileIO(FIXTURE) as ole:
        original_xlsx = ole.openstream("__attach_version1.0_#00000000/__substg1.0_37010102").read()
    assert Path(markers["root/0/0"].meta["saved_path"]).read_bytes() == original_xlsx


@pytest.mark.parametrize("message_class,warning", [
    ("IPM.Note.SMIME", "protected_mail"),
    ("ipm.note.smime", "protected_mail"),
    ("IPM.Appointment", "unsupported_mail_class"),
    ("IPM.Contact", "unsupported_mail_class"),
    ("IPM.Task", "unsupported_mail_class"),
])
def test_unsupported_msg_is_explicit_and_does_not_index_body(tmp_path, message_class, warning):
    path = tmp_path / "unsupported.msg"
    path.write_bytes(unicode_msg(message_class=message_class, body="DO NOT INDEX THIS BODY"))
    result = parse_document_result(path)
    assert result.warnings == [{"code": warning, "source_id": "root"}]
    assert all("DO NOT INDEX" not in block.text for block in result.blocks)
    assert result.sources[0].metadata["message_class"] == message_class


def test_clear_signed_msg_keeps_accessible_text_without_claiming_signature_verification(tmp_path):
    path = tmp_path / "signed.msg"
    path.write_bytes(unicode_msg(message_class="IPM.Note.SMIME.MultipartSigned", body="VISIBLE SIGNED BODY"))
    result = parse_document_result(path)
    assert any("VISIBLE SIGNED BODY" in block.text for block in result.blocks)
    assert result.sources[0].metadata["signature_verified"] is False


def test_rights_managed_msg_does_not_index_fallback_body(tmp_path):
    path = tmp_path / "protected.msg"
    path.write_bytes(unicode_msg(content_class="rpmsg.message", body="PROTECTED FALLBACK BODY"))
    result = parse_document_result(path)
    assert result.warnings == [{"code": "protected_mail", "source_id": "root"}]
    assert all("PROTECTED FALLBACK" not in block.text for block in result.blocks)


def test_embedded_msg_never_reuses_parent_properties_or_attachment_bytes(tmp_path):
    parent = unicode_msg(subject="PARENT SUBJECT", body="PARENT BODY")
    child = unicode_msg(subject="CHILD SUBJECT", body="CHILD BODY", sender="child@example.test",
                        attachments={"child.bin": b"child\0binary\0payload"})
    path = tmp_path / "nested-distinct.msg"
    path.write_bytes(nested_msg(parent, child))
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert [node.metadata.get("subject") for node in result.sources[:2]] == ["PARENT SUBJECT", "CHILD SUBJECT"]
    assert parseaddr(result.sources[1].metadata["sender"])[1] == "child@example.test"
    paragraphs = [(block.meta["source_id"], block.text) for block in result.blocks if block.type == "paragraph"]
    assert paragraphs == [("root", "PARENT BODY"), ("root/0", "CHILD BODY")]
    marker = next(block for block in result.blocks if block.meta.get("source_id") == "root/0/0")
    assert marker.meta["name"] == "child.bin"
    assert Path(marker.meta["saved_path"]).read_bytes() == b"child\0binary\0payload"


def test_protected_nested_msg_keeps_parent_searchable_and_warns_on_child_only(tmp_path):
    path = tmp_path / "nested-protected.msg"
    path.write_bytes(nested_msg(
        unicode_msg(subject="PARENT", body="VISIBLE PARENT"),
        unicode_msg(subject="CHILD", body="DO NOT INDEX CHILD", message_class="IPM.Note.SMIME"),
    ))
    result = parse_document_result(path)
    assert result.warnings == [{"code": "protected_mail", "source_id": "root/0"}]
    assert result.sources[0].warnings == []
    assert any(block.text == "VISIBLE PARENT" for block in result.blocks)
    assert not any("DO NOT INDEX" in block.text for block in result.blocks)
