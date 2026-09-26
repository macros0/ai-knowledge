"""Prove MSG limits at the CFB read boundary, not only on returned markers."""
import io
import struct
from pathlib import Path

import olefile
import pytest
from docparser import parse_document
from docparser.embedded import AttachmentBudget
from docparser.source_model import ParseContext

from tests.mail_fixtures import compound_bytes, nested_msg, unicode_msg


def _parse(tmp_path, payload, budget=None):
    path = tmp_path / "limits.msg"
    path.write_bytes(payload)
    context = ParseContext(path.name)
    blocks = parse_document(path, attachments_dir=tmp_path / "attachments", budget=budget, context=context)
    return blocks, context


def _observe(monkeypatch):
    opened = []
    original = olefile.OleFileIO.openstream

    def observe(ole, path):
        path = path.split("/") if isinstance(path, str) else path
        opened.append(tuple(path))
        return original(ole, path)

    monkeypatch.setattr(olefile.OleFileIO, "openstream", observe)
    return opened


def test_msg_does_not_read_forbidden_substorage_level(tmp_path, monkeypatch):
    payload = unicode_msg(body="FORBIDDEN LEVEL THREE")
    for level in (2, 1, 0):
        payload = nested_msg(unicode_msg(body=f"VISIBLE LEVEL {level}"), payload)
    opened = _observe(monkeypatch)
    blocks, context = _parse(tmp_path, payload)
    assert not any(path.count("__substg1.0_3701000D") >= 3 for path in opened)
    assert not any("FORBIDDEN" in block.text for block in blocks)
    assert len(context.sources) == 4
    assert blocks[-1].meta["extraction_status"] == "skipped_depth"


def test_msg_node_limit_precedes_attachment_properties_and_payload(tmp_path, monkeypatch):
    payload = unicode_msg(attachments={f"file-{index}.bin": bytes([index]) for index in range(4)})
    opened = _observe(monkeypatch)
    blocks, _ = _parse(tmp_path, payload, AttachmentBudget(max_nodes=2))
    assert not any(path[0] in {"__attach_version1.0_#00000002", "__attach_version1.0_#00000003"} for path in opened)
    markers = [block for block in blocks if block.type == "attachment"]
    assert [block.meta["extraction_status"] for block in markers] == ["saved", "saved", "skipped_count"]


def test_msg_declared_size_is_checked_before_open_and_sibling_survives(tmp_path, monkeypatch):
    from docparser.embedded import MAX_ATTACHMENT_PAYLOAD

    payload = unicode_msg(attachments={"large.bin": b"small fixture", "ok.bin": b"OK\0BYTES"})
    original_size = olefile.OleFileIO.get_size
    forbidden = ("__attach_version1.0_#00000000", "__substg1.0_37010102")

    def claimed_size(ole, path):
        if tuple(path) == forbidden:
            return MAX_ATTACHMENT_PAYLOAD + 1
        return original_size(ole, path)

    monkeypatch.setattr(olefile.OleFileIO, "get_size", claimed_size)
    opened = _observe(monkeypatch)
    blocks, _ = _parse(tmp_path, payload)
    assert forbidden not in opened
    markers = [block for block in blocks if block.type == "attachment"]
    assert markers[0].meta["extraction_status"] == "skipped_size"
    assert Path(markers[1].meta["saved_path"]).read_bytes() == b"OK\0BYTES"


def test_msg_attachments_share_remaining_bytes_before_open(tmp_path, monkeypatch):
    payload = unicode_msg(attachments={"first.bin": b"A" * 1000, "second.bin": b"B" * 1000})
    with olefile.OleFileIO(io.BytesIO(payload)) as ole:
        metadata_size = sum(ole.get_size(path) for path in ole.listdir() if path[-1] != "__substg1.0_37010102")
    opened = _observe(monkeypatch)
    blocks, _ = _parse(tmp_path, payload, AttachmentBudget(total=metadata_size + 1000))
    assert ("__attach_version1.0_#00000001", "__substg1.0_37010102") not in opened
    markers = [block for block in blocks if block.type == "attachment"]
    assert [block.meta["extraction_status"] for block in markers] == ["saved", "skipped_size"]


def test_msg_oversized_root_property_is_rejected_before_open(tmp_path, monkeypatch):
    from docparser.embedded import MAX_ATTACHMENT_PAYLOAD

    payload = unicode_msg()
    original_size = olefile.OleFileIO.get_size

    def claimed_size(ole, path):
        if list(path) == ["__substg1.0_1000001F"]:
            return MAX_ATTACHMENT_PAYLOAD + 1
        return original_size(ole, path)

    monkeypatch.setattr(olefile.OleFileIO, "get_size", claimed_size)
    opened = _observe(monkeypatch)
    with pytest.raises(ValueError, match="limit"):
        _parse(tmp_path, payload)
    assert ("__substg1.0_1000001F",) not in opened


def test_broken_child_properties_do_not_discard_healthy_sibling(tmp_path):
    payload = nested_msg(unicode_msg(), unicode_msg(body="BROKEN CHILD"))
    with olefile.OleFileIO(io.BytesIO(payload)) as ole:
        streams = {"/".join(path): ole.openstream(path).read() for path in ole.listdir()}
    streams["__attach_version1.0_#00000000/__substg1.0_3701000D/__properties_version1.0"] = b"truncated"
    with olefile.OleFileIO(io.BytesIO(unicode_msg(attachments={"ok.bin": b"OK"}))) as ole:
        for path in ole.listdir():
            if path[0].startswith("__attach"):
                streams["/".join(["__attach_version1.0_#00000001", *path[1:]])] = ole.openstream(path).read()
    blocks, context = _parse(tmp_path, compound_bytes(streams))
    markers = [block for block in blocks if block.type == "attachment"]
    assert [block.meta["extraction_status"] for block in markers] == ["skipped_parse", "saved"]
    assert Path(markers[1].meta["saved_path"]).read_bytes() == b"OK"
    assert context.warnings == [{"code": "mail_parse_failed", "source_id": "root/0"}]


def test_opaque_msg_payloads_are_budgeted_even_without_output_directory(tmp_path, monkeypatch):
    payload = unicode_msg(attachments={"first.bin": b"A" * 1000, "second.bin": b"B" * 1000})
    with olefile.OleFileIO(io.BytesIO(payload)) as ole:
        metadata_size = sum(ole.get_size(path) for path in ole.listdir() if path[-1] != "__substg1.0_37010102")
    opened = _observe(monkeypatch)
    path = tmp_path / "limits.msg"
    path.write_bytes(payload)
    blocks = parse_document(path, budget=AttachmentBudget(total=metadata_size + 1000))
    assert ("__attach_version1.0_#00000001", "__substg1.0_37010102") not in opened
    assert blocks[-1].meta["extraction_status"] == "skipped_size"


def test_unsupported_attachment_method_does_not_read_payload(tmp_path, monkeypatch):
    payload = unicode_msg(attachments={"linked.bin": b"DO NOT TREAT AS BY VALUE"})
    with olefile.OleFileIO(io.BytesIO(payload)) as ole:
        streams = {"/".join(path): ole.openstream(path).read() for path in ole.listdir()}
    streams["__attach_version1.0_#00000000/__properties_version1.0"] = bytes(8) + struct.pack("<IIQ", 0x37050003, 6, 2)
    payload = compound_bytes(streams)
    opened = _observe(monkeypatch)
    blocks, context = _parse(tmp_path, payload)
    assert ("__attach_version1.0_#00000000", "__substg1.0_37010102") not in opened
    assert blocks[-1].meta["extraction_status"] == "unsupported"
    assert context.warnings == [{"code": "unsupported_attachment_method", "source_id": "root/0"}]
