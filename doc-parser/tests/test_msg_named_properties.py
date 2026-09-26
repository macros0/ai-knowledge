"""Native CFB named properties, including the shared embedded-message map."""
import io
import struct
from uuid import UUID

import olefile
import pytest
from docparser import parse_document_result

from tests.mail_fixtures import compound_bytes, nested_msg, unicode_msg

MAP = "__nameid_version1.0/"
CHILD = "__attach_version1.0_#00000000/__substg1.0_3701000D/"
INTERNET_HEADERS = UUID("00020386-0000-0000-c000-000000000046").bytes_le


def _streams(payload):
    with olefile.OleFileIO(io.BytesIO(payload)) as ole:
        return {"/".join(path): ole.openstream(path).read() for path in ole.listdir()}


def _named_message(*, embedded=False, guid=INTERNET_HEADERS, value="rpmsg.Message", header=""):
    parent = unicode_msg(body="VISIBLE PARENT" if embedded else "PRIVATE FALLBACK", content_class=header)
    payload = nested_msg(parent, unicode_msg(body="PRIVATE FALLBACK")) if embedded else parent
    streams = _streams(payload)
    name = "Content-Class".encode("utf-16-le")
    streams[MAP + "__substg1.0_00020102"] = guid
    # GUID index 3, string kind 1, property index 0 => property ID 0x8000.
    streams[MAP + "__substg1.0_00030102"] = struct.pack("<IHH", 0, 7, 0)
    streams[MAP + "__substg1.0_00040102"] = struct.pack("<I", len(name)) + name + bytes((-len(name)) % 4)
    prefix = CHILD if embedded else ""
    streams[prefix + "__substg1.0_8000001F"] = (value + "\0").encode("utf-16-le")
    return streams


@pytest.mark.parametrize("embedded", [False, True])
def test_named_content_class_blocks_fallback_body_using_root_mapping(tmp_path, embedded):
    path = tmp_path / "protected.msg"
    path.write_bytes(compound_bytes(_named_message(embedded=embedded)))
    result = parse_document_result(path)
    source_id = "root/0" if embedded else "root"
    assert result.warnings == [{"code": "protected_mail", "source_id": source_id}]
    assert result.sources[-1].metadata["content_class"] == "rpmsg.Message"
    assert not any("PRIVATE FALLBACK" in block.text for block in result.blocks)
    if embedded:
        assert result.sources[0].metadata.get("content_class") is None
        assert any(block.text == "VISIBLE PARENT" for block in result.blocks)


def test_unrelated_property_set_cannot_mark_mail_protected(tmp_path):
    path = tmp_path / "unrelated.msg"
    path.write_bytes(compound_bytes(_named_message(guid=UUID(int=42).bytes_le)))
    result = parse_document_result(path)
    assert result.warnings == []
    assert any(block.text == "PRIVATE FALLBACK" for block in result.blocks)


@pytest.mark.parametrize("native,header", [("rpmsg.message", "ordinary"), ("ordinary", "rpmsg.message")])
def test_conflicting_content_class_cannot_hide_protection(tmp_path, native, header):
    path = tmp_path / "conflict.msg"
    path.write_bytes(compound_bytes(_named_message(value=native, header=header)))
    result = parse_document_result(path)
    assert result.warnings == [{"code": "protected_mail", "source_id": "root"}]
    assert not any("PRIVATE FALLBACK" in block.text for block in result.blocks)


def test_shared_named_mapping_is_read_once_and_not_replaced_by_child_map(tmp_path, monkeypatch):
    streams = _named_message(embedded=True)
    # Embedded messages use their ancestor map. A forged local map must not
    # reclassify the parent's named property ID as an unrelated property.
    for key, value in list(streams.items()):
        if key.startswith(MAP):
            streams[CHILD + key] = UUID(int=42).bytes_le if key.endswith("00020102") else value
    path = tmp_path / "shared.msg"
    path.write_bytes(compound_bytes(streams))
    opened = []
    original = olefile.OleFileIO.openstream

    def tracked(ole, name):
        parts = name.split("/") if isinstance(name, str) else name
        opened.append("/".join(parts))
        return original(ole, name)

    monkeypatch.setattr(olefile.OleFileIO, "openstream", tracked)
    result = parse_document_result(path)
    assert result.warnings == [{"code": "protected_mail", "source_id": "root/0"}]
    assert len([name for name in opened if name.startswith(MAP)]) == 3
    assert not any(name.startswith(CHILD + MAP) for name in opened)


@pytest.mark.parametrize("stream,value", [
    ("00030102", b"\0"),
    ("00030102", struct.pack("<IHH", 0xFFFFFFFC, 7, 0)),
    ("00030102", struct.pack("<IHH", 0, 9, 0)),
    ("00040102", struct.pack("<I", 0xFFFFFFFE)),
    ("00040102", struct.pack("<I", 1) + b"x"),
    ("00040102", struct.pack("<I", 2) + b"\x00\xd8"),
])
def test_malformed_named_map_fails_without_indexing_body(tmp_path, stream, value):
    streams = _named_message()
    streams[MAP + "__substg1.0_" + stream] = value
    path = tmp_path / "malformed.msg"
    path.write_bytes(compound_bytes(streams))
    with pytest.raises(ValueError, match="named"):
        parse_document_result(path)


def test_mapping_resolves_nonzero_property_index_and_guid_index(tmp_path):
    streams = _named_message()
    streams[MAP + "__substg1.0_00020102"] = UUID(int=42).bytes_le + INTERNET_HEADERS
    streams[MAP + "__substg1.0_00030102"] = struct.pack("<IHHIHH", 123, 2, 0, 0, 9, 1)
    streams["__substg1.0_8001001F"] = streams.pop("__substg1.0_8000001F")
    path = tmp_path / "nonzero.msg"
    path.write_bytes(compound_bytes(streams))
    result = parse_document_result(path)
    assert result.warnings == [{"code": "protected_mail", "source_id": "root"}]


def test_named_map_size_is_checked_before_ole_buffering(tmp_path, monkeypatch):
    from docparser.embedded import MAX_ATTACHMENT_PAYLOAD

    path = tmp_path / "oversize.msg"
    path.write_bytes(compound_bytes(_named_message()))
    original_size = olefile.OleFileIO.get_size
    original_open = olefile.OleFileIO.openstream

    def size(ole, name):
        return MAX_ATTACHMENT_PAYLOAD + 1 if name[-1] == "__substg1.0_00030102" else original_size(ole, name)

    def guarded(ole, name):
        assert name[-1] != "__substg1.0_00030102", "oversized map must not be buffered"
        return original_open(ole, name)

    monkeypatch.setattr(olefile.OleFileIO, "get_size", size)
    monkeypatch.setattr(olefile.OleFileIO, "openstream", guarded)
    with pytest.raises(ValueError, match="limit"):
        parse_document_result(path)


def test_unknown_named_property_value_is_not_opened(tmp_path, monkeypatch):
    streams = _named_message()
    name = "X-Private-Unused".encode("utf-16-le")
    streams[MAP + "__substg1.0_00040102"] = struct.pack("<I", len(name)) + name + bytes((-len(name)) % 4)
    path = tmp_path / "unused.msg"
    path.write_bytes(compound_bytes(streams))
    original = olefile.OleFileIO.openstream

    def guarded(ole, name):
        assert name[-1] != "__substg1.0_8000001F", "unselected named values must not be read"
        return original(ole, name)

    monkeypatch.setattr(olefile.OleFileIO, "openstream", guarded)
    result = parse_document_result(path)
    assert result.warnings == []
    assert any(block.text == "PRIVATE FALLBACK" for block in result.blocks)
