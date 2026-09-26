"""Real CFB wrappers, with native-size and Packager record boundaries."""
import struct
from pathlib import Path

import pytest
from docparser import parse_document_result

from tests.fixtures import make_docx_with_embedded_xlsx
from tests.mail_fixtures import compound_bytes, unicode_msg


def _packager_record(payload: bytes) -> bytes:
    # MS-OLEDS NativeData contains the Packager application's record.
    # Field layout cross-checked against Apache POI Ole10Native.readParsed.
    command = b"C:\\original\\message.msg\0"
    return (struct.pack("<H", 2) + b"message.msg\0C:\\original\\message.msg\0"
            + struct.pack("<HHI", 0, 0, len(command)) + command
            + struct.pack("<I", len(payload)) + payload + b"\0\0")


@pytest.mark.parametrize("mail_type", ["eml", "msg"])
@pytest.mark.parametrize("wrapper", ["package", "native", "packager", "native_alias"])
def test_docx_ole_wrapper_extracts_mail_and_retains_payload_bytes(tmp_path, mail_type, wrapper):
    payload = (unicode_msg(body="WRAPPED MAIL EVIDENCE") if mail_type == "msg" else
               b"From: sender@example.test\r\nSubject: Wrapped\r\n\r\nWRAPPED MAIL EVIDENCE\r\n")
    if wrapper == "package":
        streams = {"Package": payload}
    else:
        native = _packager_record(payload) if wrapper == "packager" else payload
        stream_name = "Ole10Native" if wrapper == "native_alias" else "\x01Ole10Native"
        streams = {stream_name: struct.pack("<I", len(native)) + native}
    original = compound_bytes(streams)
    path = make_docx_with_embedded_xlsx(
        tmp_path / "wrapped.docx", original, prog_id="Package", filename="object.bin",
    )
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert [(node.source_id, node.kind) for node in result.sources] == [
        ("root", "document"), ("root/0", "mail"),
    ]
    assert any(block.text == "WRAPPED MAIL EVIDENCE" and block.meta.get("source_id") == "root/0"
               for block in result.blocks)
    marker = next(block for block in result.blocks if block.type == "attachment")
    assert Path(marker.meta["saved_path"]).read_bytes() == payload
    assert result.warnings == []


@pytest.mark.parametrize("broken", ["outer_size", "inner_size", "unterminated_path"])
def test_invalid_native_record_stays_opaque_without_guessing_mail(tmp_path, broken):
    payload = b"From: sender@example.test\r\n\r\nDO NOT GUESS THIS BODY\r\n"
    native = _packager_record(payload)
    if broken == "inner_size":
        native = native.replace(struct.pack("<I", len(payload)), struct.pack("<I", len(payload) + 999))
    elif broken == "unterminated_path":
        native = b"\x02\0" + b"x" * 4097 + payload
    original = compound_bytes({"\x01Ole10Native": struct.pack("<I", len(native) + (broken == "outer_size")) + native})
    path = make_docx_with_embedded_xlsx(tmp_path / "broken.docx", original, filename="object.bin")
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert not any("DO NOT GUESS THIS BODY" in block.text for block in result.blocks)
    assert result.sources[1].kind == "attachment"
    marker = next(block for block in result.blocks if block.type == "attachment")
    assert Path(marker.meta["saved_path"]).read_bytes() == original
