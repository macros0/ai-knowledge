import io
import zipfile
from pathlib import Path

import py7zr
import pytest
from docparser.archive_reader import open_archive
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_policy import AssessmentLimits, TraversalLimits

from tests.attachment_fixtures import mail_bytes, office_container, spreadsheet


def archive_bytes(kind, members, tmp_path):
    out = tmp_path / ("archive." + kind)
    if kind == "zip":
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in members:
                archive.writestr(name, content)
    elif kind == "rar":
        # Minimal synthetic RAR3 STORE container: no proprietary compression.
        import struct
        import zlib

        def header(kind, flags, body):
            raw = struct.pack("<BHH", kind, flags, len(body) + 7) + body
            return struct.pack("<H", zlib.crc32(raw) & 0xFFFF) + raw

        data = b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\x00" * 6)
        for name, content in members:
            encoded = name.encode("ascii")
            body = (
                struct.pack(
                    "<IIBIIBBHI",
                    len(content),
                    len(content),
                    3,
                    zlib.crc32(content),
                    0,
                    20,
                    0x30,
                    len(encoded),
                    0x20,
                )
                + encoded
            )
            data += header(0x74, 0x8000, body) + content
        out.write_bytes(data + header(0x7B, 0, b""))
    else:
        with py7zr.SevenZipFile(out, "w") as archive:
            for name, content in members:
                archive.writestr(content, name)
    return out


@pytest.mark.parametrize("kind", ["zip", "7z", "rar"])
def test_extract_only_selected_member(kind, tmp_path):
    path = archive_bytes(
        kind,
        [(("folder" if kind == "rar" else "папка") + "/one.txt", b"ONE"), ("two.txt", b"TWO")],
        tmp_path,
    )
    with open_archive(path, format=kind, limits=TraversalLimits(), helper_path=None) as reader:
        entries = list(reader.entries())
        assert len(entries) == 2
        out = io.BytesIO()
        report = reader.extract_selected(frozenset({entries[0].entry_key}), lambda entry: out)
        assert out.getvalue() == b"ONE"
        assert report.extracted_bytes == 3


@pytest.mark.parametrize("kind", ["zip", "7z", "rar"])
def test_heavy_excel_inside_mail_archive_is_found(kind, tmp_path):
    archive = archive_bytes(kind, [("data.xlsx", spreadsheet(rows=700))], tmp_path)
    root = office_container(
        tmp_path / "decision.docx",
        [("approval.eml", mail_bytes([("files." + kind, archive.read_bytes())]))],
    )
    result = inspect_attachment_inventory(
        root, root.name, limits=AssessmentLimits(), traversal=TraversalLimits()
    )
    assert [n.kind for n in result.nodes] == ["mail", "archive", "xlsx"]
    assert result.nodes[-1].assessment == "heavy"
    assert result.nodes[-1].metrics["rows"] == 500


@pytest.mark.parametrize("name", ["rar3-solid.rar", "rar5-solid.rar", "rar5-crc.rar"])
def test_compressed_rar_runtime(name):
    path = Path(__file__).parent / "fixtures/archives" / name
    with open_archive(path, format="rar", limits=TraversalLimits(), helper_path=None) as reader:
        entries = [e for e in reader.entries() if not e.is_dir]
        assert entries
        outputs = {e.entry_key: io.BytesIO() for e in entries}
        result = reader.extract_selected(frozenset(outputs), lambda e: outputs[e.entry_key])
        assert result.extracted_bytes == sum(e.size for e in entries)
        assert any(out.getvalue() for out in outputs.values())
