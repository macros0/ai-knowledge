"""Concrete regressions reproduced by the final ingestion review."""
from email.message import EmailMessage

import pytest
from docparser import parse_document, parse_document_result
from docparser.embedded import AttachmentBudget
from docparser.source_model import ParseContext

from tests.mail_fixtures import compound_bytes, unicode_msg


@pytest.mark.parametrize("extension", ["eml", "msg"])
def test_plain_mail_cannot_turn_literal_text_into_remote_markdown_image(tmp_path, extension):
    body = "Срок проверки: 23 дня. ![pixel](https://example.invalid/pixel.png)"
    path = tmp_path / f"plain.{extension}"
    if extension == "eml":
        message = EmailMessage()
        message["From"] = "sender@example.test"
        message.set_content(body)
        payload = message.as_bytes()
    else:
        payload = unicode_msg(body=body)
    path.write_bytes(payload)

    result = parse_document_result(path)
    text = "\n".join(block.text for block in result.blocks if block.type == "paragraph")
    assert r"\!\[pixel\](https://example.invalid/pixel.png)" in text
    assert "Срок проверки: 23 дня." in text
    assert path.read_bytes() == payload


def test_mixed_mail_retains_both_plain_body_parts(tmp_path):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.make_mixed()
    for text in ("Первый факт: 23 дня.", "Второй факт: 47 дней."):
        part = EmailMessage()
        part.set_content(text)
        message.attach(part)
    path = tmp_path / "mixed.eml"
    path.write_bytes(message.as_bytes())

    result = parse_document_result(path)
    text = "\n".join(block.text for block in result.blocks)
    assert "Первый факт: 23 дня." in text
    assert "Второй факт: 47 дней." in text


@pytest.mark.parametrize("html, warning", [("<p>Decision: 23 days.</p>", False), ("<p>Decision: 47 days.</p>", True)])
def test_alternative_selects_one_body_and_warns_when_facts_differ(tmp_path, html, warning):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.set_content("Decision: 23 days.")
    message.add_alternative(html, subtype="html")
    path = tmp_path / "alternative.eml"
    path.write_bytes(message.as_bytes())
    result = parse_document_result(path)
    assert [block.text for block in result.blocks] == ["Decision: 23 days."]
    assert (any(item["code"] == "mail_alternative_mismatch" for item in result.warnings)) is warning


def test_msg_count_limit_has_one_aggregate_remainder(tmp_path):
    path = tmp_path / "many.msg"
    path.write_bytes(unicode_msg(attachments={f"file-{index}.bin": b"x" for index in range(24)}))

    context = ParseContext(path.name)
    blocks = parse_document(path, budget=AttachmentBudget(max_nodes=2), context=context)
    markers = [block for block in blocks if block.type == "attachment"]
    assert len(markers) == 3
    assert len(context.sources) == 4  # Root, two admitted attachments, one remainder.
    assert markers[-1].meta["extraction_status"] == "skipped_count"
    assert len(context.warnings) == 1


@pytest.mark.parametrize("case", ["raw_size", "depth_size", "disabled_mail", "parsed_size", "opaque_size", "count"])
def test_central_attachment_budget_rejections_always_warn(tmp_path, monkeypatch, case):
    from docparser import embedded

    context = ParseContext("root.docx", mail_enabled=case != "disabled_mail")
    payload = b"From: sender@example.test\nContent-Type: text/plain\n\nFact: 23 days."
    name = "child.eml"
    depth = 1
    budget = AttachmentBudget(total=1)
    if case == "raw_size":
        monkeypatch.setattr(embedded, "MAX_ATTACHMENT_PAYLOAD", 4)
    elif case == "depth_size":
        depth = embedded.MAX_ATTACHMENT_DEPTH
    elif case == "opaque_size":
        payload, name = b"plain bytes", "opaque.bin"
    elif case == "count":
        budget = AttachmentBudget(max_nodes=0)
    blocks = embedded.process_embedded(
        payload, name, depth=depth, budget=budget,
        attachments_dir=tmp_path / "attachments", context=context,
    )
    status = "skipped_count" if case == "count" else "skipped_size"
    assert blocks[0].meta["extraction_status"] == status
    assert context.warnings == [{
        "code": "attachment_count_exceeded" if case == "count" else "attachment_size_exceeded",
        "source_id": "root/0",
    }]


@pytest.mark.parametrize("extension", ["eml", "msg"])
def test_invalid_mail_body_encoding_has_structured_warning(tmp_path, extension):
    path = tmp_path / f"broken.{extension}"
    if extension == "eml":
        payload = b"From: sender@example.test\nContent-Type: text/plain; charset=utf-8\n\nDecision: 1\xff8 days."
    else:
        payload = compound_bytes({
            "__properties_version1.0": b"\0" * 32,
            "__substg1.0_1000001F": "Decision: 18 days.".encode("utf-16-le") + b"\xff",
        })
    path.write_bytes(payload)
    result = parse_document_result(path)
    assert "\ufffd" in "\n".join(block.text for block in result.blocks)
    assert {"code": "mail_decode_recovered", "source_id": "root"} in result.warnings


def test_native_exchange_sender_and_represented_sender_are_distinct(tmp_path):
    sender = "/O=ORG/OU=USERS/CN=NATIVE"
    represented = "/O=ORG/OU=USERS/CN=REPRESENTED"
    values = {"0C1A": "Native Sender", "0C1E": "EX", "0C1F": sender,
              "0042": "Represented Sender", "0064": "EX", "0065": represented}
    streams = {"__properties_version1.0": b"\0" * 32,
               "__substg1.0_1000001F": "Fact: 23 days.\0".encode("utf-16-le")}
    streams.update({f"__substg1.0_{tag}001F": (value + "\0").encode("utf-16-le") for tag, value in values.items()})
    path = tmp_path / "native.msg"
    path.write_bytes(compound_bytes(streams))
    metadata = parse_document_result(path).sources[0].metadata
    assert metadata["sender"] == f"Native Sender <{sender}>"
    assert metadata["sender_address_type"] == "EX"
    assert metadata["representing_sender"] == f"Represented Sender <{represented}>"
    assert metadata["representing_sender_address_type"] == "EX"
    assert "@" not in metadata["sender"]
