"""CID images belong to their own mail and use admitted local raster bytes."""
import io
import socket
import struct
from email.message import EmailMessage
from pathlib import Path

import pytest
from docparser import blocks_to_markdown, parse_document, parse_document_result
from docparser.embedded import AttachmentBudget
from docparser.source_model import ParseContext
from PIL import Image

from tests.mail_fixtures import compound_bytes


def png_bytes(color="red", format_name="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color).save(buffer, format=format_name)
    return buffer.getvalue()


def eml_with_image(html, payload, cid="same@example.test", filename=None):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.set_content(html, subtype="html")
    message.add_related(payload, maintype="image", subtype="png", cid=f"<{cid}>", filename=filename)
    return message


def msg_with_image(html, payload, cid="same@example.test"):
    prefix = "__attach_version1.0_#00000000/"
    return compound_bytes({
        "__properties_version1.0": b"\0" * 32,
        "__substg1.0_10130102": html.encode("ascii"),
        prefix + "__properties_version1.0": b"\0" * 8 + struct.pack("<IIQ", 0x37050003, 0, 1),
        prefix + "__substg1.0_3712001F": (cid + "\0").encode("utf-16-le"),
        prefix + "__substg1.0_37010102": payload,
    })


def test_content_id_on_multipart_container_does_not_hide_body(tmp_path):
    message = eml_with_image('<p>ROOT BODY<img src="cid:same@example.test" alt="Diagram"></p>', png_bytes())
    message["Content-ID"] = "<container@example.test>"
    path = tmp_path / "container.eml"
    path.write_bytes(message.as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == []
    assert [b.text for b in result.blocks if b.type != "attachment"] == [
        "ROOT BODY", "![Diagram](attachments/source-root-0.png)",
    ]
    assert len(result.sources) == 2


@pytest.mark.parametrize("caption,expected", [("---", r"\---"), ("1. Item", r"1\. Item"), ("- Item", r"\- Item")])
def test_missing_cid_caption_cannot_become_markdown_structure(tmp_path, caption, expected):
    message = EmailMessage()
    message.set_content(f'<img src="cid:missing" alt="{caption}">', subtype="html")
    path = tmp_path / "caption.eml"
    path.write_bytes(message.as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == [{"code": "cid_image_unavailable", "source_id": "root"}]
    assert [b.text for b in result.blocks] == [expected]


@pytest.mark.parametrize("format_name", ["eml", "msg"])
@pytest.mark.parametrize("raster_format,extension", [("PNG", "png"), ("JPEG", "jpg"), ("GIF", "gif"), ("WEBP", "webp")])
def test_cid_image_without_filename_is_saved_once_and_rendered_locally(tmp_path, format_name, raster_format, extension, monkeypatch):
    def no_network(*_args, **_kwargs):
        raise AssertionError("CID must resolve without network")
    monkeypatch.setattr(socket, "create_connection", no_network)
    payload = png_bytes(format_name=raster_format)
    html = '<p>Before<img src="cid:same%40example.test" alt="Figure [1]">After</p>'
    raw = (eml_with_image(html, payload).as_bytes() if format_name == "eml"
           else msg_with_image(html, payload))
    path = tmp_path / f"inline.{format_name}"
    path.write_bytes(raw)
    attachments = tmp_path / "attachments"

    result = parse_document_result(path, attachments_dir=attachments)

    assert result.warnings == []
    assert [node.source_id for node in result.sources] == ["root", "root/0"]
    body = [block for block in result.blocks if block.type != "attachment"]
    assert [block.text for block in body] == [
        "Before", rf"![Figure \[1\]](attachments/source-root-0.{extension})", "After",
    ]
    assert {block.meta["source_id"] for block in body} == {"root"}
    assert all("saved_path" not in block.meta for block in body)
    marker = next(block for block in result.blocks if block.type == "attachment")
    assert marker.meta["source_id"] == "root/0"
    assert Path(marker.meta["saved_path"]).read_bytes() == payload
    assert list(attachments.iterdir()) == [Path(marker.meta["saved_path"])]


def test_same_cid_in_parent_and_child_eml_never_crosses_source_boundary(tmp_path):
    html = '<p><img src="cid:same@example.test" alt="Diagram"></p>'
    red, blue = png_bytes("red"), png_bytes("blue")
    parent = eml_with_image(html, red)
    child = eml_with_image(html, blue)
    parent.add_attachment(child, filename="child.eml")
    path = tmp_path / "nested.eml"
    path.write_bytes(parent.as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    references = [(b.meta["source_id"], b.text) for b in result.blocks if b.text.startswith("![")]
    assert references == [
        ("root", "![Diagram](attachments/source-root-0.png)"),
        ("root/1", "![Diagram](attachments/source-root-1-0.png)"),
    ]
    assert (tmp_path / "attachments/source-root-0.png").read_bytes() == red
    assert (tmp_path / "attachments/source-root-1-0.png").read_bytes() == blue


def test_child_eml_cannot_resolve_missing_cid_from_parent(tmp_path):
    parent = eml_with_image('<p>Parent</p>', png_bytes())
    child = EmailMessage()
    child["From"] = "child@example.test"
    child.set_content('<p><img src="cid:same@example.test" alt="Missing diagram"></p>', subtype="html")
    parent.add_attachment(child, filename="child.eml")
    path = tmp_path / "missing.eml"
    path.write_bytes(parent.as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == [{"code": "cid_image_unavailable", "source_id": "root/1"}]
    assert "![" not in blocks_to_markdown(result.blocks)
    assert any(b.text == "Missing diagram" and b.meta["source_id"] == "root/1" for b in result.blocks)


def test_duplicate_cid_in_one_mail_is_ambiguous_even_when_bytes_match(tmp_path):
    payload = png_bytes()
    message = eml_with_image('<p><img src="cid:same@example.test" alt="Ambiguous"></p>', payload)
    message.add_related(payload, maintype="image", subtype="png", cid="<same@example.test>")
    path = tmp_path / "duplicate.eml"
    path.write_bytes(message.as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == [{"code": "cid_image_unavailable", "source_id": "root"}]
    assert "![" not in blocks_to_markdown(result.blocks)
    assert len(list((tmp_path / "attachments").iterdir())) == 2


@pytest.mark.parametrize("payload", [b'<svg xmlns="http://www.w3.org/2000/svg"><script>evil()</script></svg>', b"not an image"])
def test_svg_or_spoofed_raster_is_never_used_as_cid_image(tmp_path, payload):
    path = tmp_path / "unsafe.eml"
    path.write_bytes(eml_with_image('<p><img src="cid:same@example.test" alt="Not rendered"></p>', payload).as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == [{"code": "cid_image_unavailable", "source_id": "root"}]
    assert "![" not in blocks_to_markdown(result.blocks)
    marker = next(b for b in result.blocks if b.type == "attachment")
    assert Path(marker.meta["saved_path"]).read_bytes() == payload


def test_cid_without_output_directory_is_diagnosed_without_fake_local_path(tmp_path):
    path = tmp_path / "no-output.eml"
    path.write_bytes(eml_with_image('<p><img src="cid:same@example.test" alt="Diagram"></p>', png_bytes()).as_bytes())
    result = parse_document_result(path)
    assert result.warnings == [{"code": "cid_image_unavailable", "source_id": "root"}]
    assert "attachments/" not in blocks_to_markdown(result.blocks)


def test_cid_in_table_preserves_columns_and_escapes_caption(tmp_path):
    html = '<table><tr><th>Image</th><th>Value</th></tr><tr><td><img src="cid:same@example.test" alt="A|B ![x] &amp;copy;"></td><td>12</td></tr></table>'
    path = tmp_path / "table.eml"
    path.write_bytes(eml_with_image(html, png_bytes()).as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    table = next(b for b in result.blocks if b.type == "table")
    assert table.text == ("| Image | Value |\n|---|---|\n"
                          r"| ![A\|B \!\[x\] \&copy;](attachments/source-root-0.png) | 12 |")


@pytest.mark.parametrize("child_format", ["eml", "msg"])
def test_cid_is_local_to_each_sibling_mail_and_not_visible_to_parent(tmp_path, child_format):
    html = '<p>Local image<img src="cid:same@example.test" alt="Diagram"></p>'
    root = EmailMessage()
    root["From"] = "sender@example.test"
    root.set_content(html, subtype="html")
    for color in ("red", "blue"):
        if child_format == "eml":
            root.add_attachment(eml_with_image(html, png_bytes(color)), filename=f"{color}.eml")
        else:
            root.add_attachment(msg_with_image(html, png_bytes(color)), maintype="application",
                                subtype="octet-stream", filename=f"{color}.msg")
    path = tmp_path / "siblings.eml"
    path.write_bytes(root.as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == [{"code": "cid_image_unavailable", "source_id": "root"}]
    assert [(b.meta["source_id"], b.text) for b in result.blocks if b.text.startswith("![")] == [
        ("root/0", "![Diagram](attachments/source-root-0-0.png)"),
        ("root/1", "![Diagram](attachments/source-root-1-0.png)"),
    ]


@pytest.mark.parametrize("limit", ["nodes", "bytes", "depth"])
def test_cid_admission_limits_precede_decode_and_raster_inspection(tmp_path, monkeypatch, limit):
    from docparser import eml_parser

    path = tmp_path / "limited.eml"
    path.write_bytes(eml_with_image('<p>Body<img src="cid:same@example.test" alt="Diagram"></p>', png_bytes()).as_bytes())
    context = ParseContext(path.name)
    # Root is free; the HTML body takes the single admitted descendant slot.
    budget = AttachmentBudget(total=0 if limit == "bytes" else 10000, max_nodes=1 if limit == "nodes" else 100)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Rejected CID image must not be decoded or inspected")
    monkeypatch.setattr(eml_parser, "_attachment_payload", forbidden)
    monkeypatch.setattr("docparser.embedded.raster_extension", forbidden)
    blocks = parse_document(path, attachments_dir=tmp_path / "attachments", budget=budget,
                            context=context, depth=2 if limit == "depth" else 0)
    assert "![" not in blocks_to_markdown(blocks)
    assert any(b.meta.get("extraction_status") == {"nodes": "skipped_count", "bytes": "skipped_size",
                                                  "depth": "skipped_depth"}[limit] for b in blocks)


def test_repeated_cid_reference_debits_and_saves_the_payload_only_once(tmp_path):
    payload = png_bytes()
    path = tmp_path / "repeated.eml"
    path.write_bytes(eml_with_image('<p><img src="cid:same@example.test"><img src="cid:same@example.test"></p>', payload).as_bytes())
    budget = AttachmentBudget(total=len(payload))
    context = ParseContext(path.name)
    blocks = parse_document(path, attachments_dir=tmp_path / "attachments", budget=budget, context=context)
    assert context.warnings == []
    assert budget.remaining == 0
    assert sum(b.text == "![](attachments/source-root-0.png)" for b in blocks) == 2
    assert len(list((tmp_path / "attachments").iterdir())) == 1


def test_cid_image_with_excessive_pixel_dimensions_stays_downloadable_but_is_not_rendered(tmp_path):
    import zlib

    payload = bytearray(png_bytes())
    payload[16:24] = struct.pack(">II", 300000, 300000)
    payload[29:33] = struct.pack(">I", zlib.crc32(payload[12:29]))
    path = tmp_path / "oversized-pixels.eml"
    path.write_bytes(eml_with_image('<img src="cid:same@example.test" alt="Large image">', bytes(payload)).as_bytes())
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert result.warnings == [{"code": "cid_image_unavailable", "source_id": "root"}]
    assert "![" not in blocks_to_markdown(result.blocks)
    marker = next(b for b in result.blocks if b.type == "attachment")
    assert Path(marker.meta["saved_path"]).read_bytes() == payload
