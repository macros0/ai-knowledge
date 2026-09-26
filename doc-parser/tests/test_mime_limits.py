"""MIME transfer decoding must not precede attachment admission checks."""
from email.message import EmailMessage

import pytest
from docparser import parse_document
from docparser.embedded import AttachmentBudget
from docparser.source_model import ParseContext


def _mail(tmp_path, payloads):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.set_content("Visible parent")
    for index, payload in enumerate(payloads):
        message.add_attachment(payload, maintype="application", subtype="octet-stream", filename=f"file-{index}.bin")
    path = tmp_path / "limits.eml"
    path.write_bytes(message.as_bytes())
    return path


def _observe_decode(monkeypatch):
    from docparser import eml_parser

    decoded = []
    original = eml_parser._attachment_payload

    def observe(part):
        decoded.append(part.get_filename())
        return original(part)

    monkeypatch.setattr(eml_parser, "_attachment_payload", observe)
    return decoded


def test_mime_count_limit_precedes_transfer_decoding(tmp_path, monkeypatch):
    path = _mail(tmp_path, [b"A", b"B", b"C"])
    decoded = _observe_decode(monkeypatch)
    # The body consumes one MIME node, then the first attachment consumes one.
    # Unvisited siblings have one aggregate marker, not an unbounded marker list.
    blocks = parse_document(path, budget=AttachmentBudget(max_nodes=2), attachments_dir=tmp_path / "attachments")
    assert decoded == ["file-0.bin"]
    assert [b.meta["extraction_status"] for b in blocks if b.type == "attachment"] == ["saved", "skipped_count"]


def test_mime_forbidden_depth_precedes_transfer_decoding(tmp_path, monkeypatch):
    path = _mail(tmp_path, [b"FORBIDDEN"])
    decoded = _observe_decode(monkeypatch)
    context = ParseContext(path.name)
    blocks = parse_document(path, depth=2, context=context)
    assert decoded == []
    assert blocks[-1].meta["extraction_status"] == "skipped_depth"
    assert "saved_path" not in blocks[-1].meta
    assert context.sources[-1].parent_source_id == "root"


@pytest.mark.parametrize("size", [9, 10, 11])
def test_mime_base64_size_bound_is_checked_before_decode(tmp_path, monkeypatch, size):
    from docparser import eml_parser

    path = _mail(tmp_path, [b"A" * size])
    # Module-level cap is the admission boundary; process_embedded also checks
    # actual bytes against the real cap after decoding.
    monkeypatch.setattr(eml_parser, "MAX_ATTACHMENT_PAYLOAD", 10, raising=False)
    decoded = _observe_decode(monkeypatch)
    blocks = parse_document(path, attachments_dir=tmp_path / "attachments")
    assert decoded == ([] if size > 10 else ["file-0.bin"])
    assert blocks[-1].meta["extraction_status"] == ("skipped_size" if size > 10 else "saved")


def test_mime_remaining_bytes_are_checked_before_next_decode(tmp_path, monkeypatch):
    path = _mail(tmp_path, [b"A" * 9, b"B" * 9])
    decoded = _observe_decode(monkeypatch)
    blocks = parse_document(path, budget=AttachmentBudget(total=10), attachments_dir=tmp_path / "attachments")
    assert decoded == ["file-0.bin"]
    assert blocks[-1].meta["extraction_status"] == "skipped_size"
