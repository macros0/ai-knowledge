"""Wire-level fixtures: do not use EmailMessage serialization as the oracle."""
import base64
from email.message import EmailMessage
from pathlib import Path

import pytest
from docparser import parse_document_result


def _outer(child: bytes, newline=b"\r\n", cte="8bit") -> bytes:
    encoded = base64.encodebytes(child).replace(b"\n", newline) if cte == "base64" else child
    return newline.join([
        b"From: parent@example.test", b'Content-Type: multipart/mixed; boundary="outer-wire"', b"",
        b"--outer-wire", b"Content-Type: text/plain", b"", b"Parent body.",
        b"--outer-wire", b"Content-Type: message/rfc822", b'Content-Disposition: attachment; filename="child.eml"',
        f"Content-Transfer-Encoding: {cte}".encode(), b"", encoded,
        b"--outer-wire--", b"Epilogue is not part of child",
    ])


@pytest.mark.parametrize("newline", [b"\r\n", b"\n"])
@pytest.mark.parametrize("trailing", [False, True])
@pytest.mark.parametrize("cte", ["8bit", "base64"])
def test_nested_rfc822_preserves_exact_wire_bytes_without_serialization(tmp_path, monkeypatch, newline, trailing, cte):
    child = newline.join([
        b"From: child@example.test", b"Subject: folded header", b"\tkeeps original spacing",
        b"X-Long-Field: " + b"long value " * 18,
        b"Content-Type: text/plain; charset=windows-1251", b"Content-Transfer-Encoding: quoted-printable",
        b"", b"=D1=F0=EE=EA 12 =E4=ED=E5=E9.",
    ]) + (newline if trailing else b"")
    path = tmp_path / "outer.eml"
    path.write_bytes(_outer(child, newline, cte))
    serialized = []
    original = EmailMessage.as_bytes

    def observe(message, *args, **kwargs):
        serialized.append(message.get("Subject"))
        return original(message, *args, **kwargs)

    monkeypatch.setattr(EmailMessage, "as_bytes", observe)
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert serialized == []
    marker = next(block for block in result.blocks if block.type == "attachment")
    assert Path(marker.meta["saved_path"]).read_bytes() == child
    assert any(block.text == "Срок 12 дней." and block.meta["source_id"] == "root/0" for block in result.blocks)
    assert result.warnings == []


def test_rfc822_size_is_known_before_child_materialization(tmp_path, monkeypatch):
    from docparser import eml_parser

    child = b"From: child@example.test\r\n\r\n" + b"A" * 400
    path = tmp_path / "large-child.eml"
    path.write_bytes(_outer(child))
    monkeypatch.setattr(eml_parser, "MAX_ATTACHMENT_PAYLOAD", len(child) - 1)
    decoded = []
    original = eml_parser._attachment_payload

    def observe(part):
        decoded.append(part)
        return original(part)

    monkeypatch.setattr(eml_parser, "_attachment_payload", observe)
    result = parse_document_result(path)
    assert decoded == []
    assert result.blocks[-1].meta["extraction_status"] == "skipped_size"


def test_root_header_limit_is_checked_before_decoding_headers(tmp_path, monkeypatch):
    from docparser import eml_parser

    monkeypatch.setattr(eml_parser, "MAX_MAIL_HEADER_BYTES", 128, raising=False)
    path = tmp_path / "headers.eml"
    path.write_bytes(b"From: sender@example.test\r\nX-Large: " + b"A" * 512 + b"\r\n\r\nBody")
    with pytest.raises(ValueError, match="header.*limit"):
        parse_document_result(path)


def test_nested_multipart_depth_is_bounded_before_inner_headers(tmp_path, monkeypatch):
    from docparser import eml_parser

    monkeypatch.setattr(eml_parser, "MAX_MIME_DEPTH", 4, raising=False)
    payload = b"Content-Type: text/plain\r\n\r\nFORBIDDEN INNER BODY"
    for index in range(12):
        boundary = f"nested-{index}".encode()
        payload = b'Content-Type: multipart/mixed; boundary="' + boundary + b'"\r\n\r\n--' + boundary + b"\r\n" + payload + b"\r\n--" + boundary + b"--\r\n"
    path = tmp_path / "deep-mime.eml"
    path.write_bytes(b"From: sender@example.test\r\n" + payload)
    result = parse_document_result(path)
    assert not any("FORBIDDEN INNER" in block.text for block in result.blocks)
    assert any(warning["code"] == "mime_depth_exceeded" for warning in result.warnings)


def test_body_size_limit_precedes_materialization(tmp_path, monkeypatch):
    from docparser import eml_parser

    monkeypatch.setattr(eml_parser, "MAX_MAIL_BODY_BYTES", 20, raising=False)
    path = tmp_path / "body-limit.eml"
    path.write_bytes(b"From: sender@example.test\r\n\r\n" + b"A" * 21)
    materialized = []
    original = eml_parser.materialize_leaf

    def observe(part):
        materialized.append(part)
        return original(part)

    monkeypatch.setattr(eml_parser, "materialize_leaf", observe)
    with pytest.raises(ValueError, match="body.*limit"):
        parse_document_result(path)
    assert materialized == []


def test_many_mime_parts_stop_before_unadmitted_headers(tmp_path, monkeypatch):
    from docparser import parse_document
    from docparser.embedded import AttachmentBudget
    from docparser.mime_wire import BytesParser
    from docparser.source_model import ParseContext

    raw = b'From: sender@example.test\r\nContent-Type: multipart/mixed; boundary="many"\r\n\r\n'
    raw += b"--many\r\nContent-Type: text/plain\r\n\r\nVisible body\r\n"
    raw += b'--many\r\nContent-Disposition: attachment; filename="a.bin"\r\n\r\nA\r\n' * 1500
    raw += b"--many--\r\n"
    path = tmp_path / "many.eml"
    path.write_bytes(raw)
    headers = []
    original = BytesParser.parsebytes

    def observe(parser, data, *args, **kwargs):
        headers.append(data)
        return original(parser, data, *args, **kwargs)

    monkeypatch.setattr(BytesParser, "parsebytes", observe)
    context = ParseContext(path.name)
    blocks = parse_document(path, budget=AttachmentBudget(max_nodes=2), context=context,
                            attachments_dir=tmp_path / "attachments")
    assert len(headers) == 3  # root, body, first attachment; remaining 1499 unread
    assert any(block.text == "Visible body" for block in blocks)
    assert [block.meta["extraction_status"] for block in blocks if block.type == "attachment"] == ["saved", "skipped_count"]
    assert len(context.sources) == 3
    assert context.warnings[-1]["code"] == "mime_count_exceeded"


@pytest.mark.parametrize("body", [b"--broken--\r\n", b"--broken\r\n\r\nBody without closing delimiter"])
def test_malformed_root_multipart_is_rejected(tmp_path, body):
    path = tmp_path / "broken.eml"
    path.write_bytes(b'From: sender@example.test\r\nContent-Type: multipart/mixed; boundary="broken"\r\n\r\n' + body)
    with pytest.raises(ValueError, match="MIME multipart"):
        parse_document_result(path)


def test_broken_nested_multipart_keeps_parent_and_sibling(tmp_path):
    path = tmp_path / "broken-child.eml"
    path.write_bytes(
        b'From: sender@example.test\r\nContent-Type: multipart/mixed; boundary="outer"\r\n\r\n'
        b'--outer\r\nContent-Type: multipart/mixed; boundary="inner"\r\n\r\n'
        b'--inner\r\n\r\nTruncated child\r\n'
        b'--outer\r\nContent-Type: text/plain\r\n\r\nParent survives\r\n'
        b'--outer\r\nContent-Disposition: attachment; filename="proof.bin"\r\n\r\nPROOF\r\n'
        b'--outer--\r\n'
    )
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert any(block.text == "Parent survives" for block in result.blocks)
    assert not any(block.text == "Truncated child" for block in result.blocks)
    marker = next(block for block in result.blocks if block.meta.get("name") == "proof.bin")
    assert Path(marker.meta["saved_path"]).read_bytes() == b"PROOF"
    assert result.warnings[0]["code"] == "mime_part_parse_failed"


def test_digest_default_type_preserves_attached_message(tmp_path):
    child = b"From: child@example.test\r\n\r\nDigest child"
    path = tmp_path / "digest.eml"
    path.write_bytes(b'From: parent@example.test\r\nContent-Type: multipart/digest; boundary="digest"\r\n\r\n'
                     b'--digest\r\n\r\n' + child + b'\r\n--digest--\r\n')
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    marker = next(block for block in result.blocks if block.type == "attachment")
    assert Path(marker.meta["saved_path"]).read_bytes() == child
    assert any(block.text == "Digest child" and block.meta["source_id"] == "root/0" for block in result.blocks)
