from email.message import EmailMessage
from pathlib import Path

import pytest
from docparser import parse_document_result
from docparser.embedded import AttachmentBudget, process_embedded
from docparser.msg_parser import _parse_message_data, _repair_rtf_mojibake
from docparser.source_model import PARSER_VERSION, ParseContext


def test_mail_parser_contract_version_marks_current_semantics():
    assert PARSER_VERSION == "mail-sources-v22"


@pytest.mark.parametrize("to_kind,cc_kind,bcc_kind", [(1, 2, 3), ("TO", "CC", "BCC")])
def test_nested_msg_recipient_fallback_never_exposes_bcc_or_guesses_unknown_types(to_kind, cc_kind, bcc_kind):
    context = ParseContext("outer.msg")
    nested = {
        "properties": {"Subject": "Nested decision", "Body": "Approved."},
        "recipients": {
            "to": {"RecipientType": to_kind, "SmtpAddress": "team@example.test"},
            "cc": {"RecipientType": cc_kind, "EmailAddress": "copy@example.test"},
            "bcc": {"RecipientType": bcc_kind, "SmtpAddress": "private@example.test"},
            "unknown": {"SmtpAddress": "unknown@example.test"},
            "reply": {"RecipientType": "ReplyTo", "SmtpAddress": "reply@example.test"},
            "invalid": {"RecipientType": True, "SmtpAddress": "invalid@example.test"},
            "malformed": None,
        },
    }
    _parse_message_data(
        {"Subject": "Outer", "Body": "See attachment.",
         "attachments": [{"AttachFilename": "nested.msg", "EmbeddedMessage": nested}]},
        attachments_dir=None, depth=0, budget=AttachmentBudget(), context=context, source_id="root",
    )
    metadata = next(source.metadata for source in context.sources if source.source_id == "root/0")
    assert metadata["to"] == ["team@example.test"]
    assert metadata["cc"] == ["copy@example.test"]
    assert "private@example.test" not in str(metadata)
    assert "unknown@example.test" not in str(metadata)


def test_eml_parent_html_is_not_replaced_by_attached_message_body(tmp_path):
    child = EmailMessage()
    child["Subject"] = "Child"
    child.set_content("CHILD BODY")
    parent = EmailMessage()
    parent["Subject"] = "Parent"
    parent.set_content("<p>PARENT HTML BODY</p>", subtype="html")
    parent.add_attachment(child)
    path = tmp_path / "parent.eml"
    path.write_bytes(bytes(parent))

    result = parse_document_result(path)

    assert [(block.text, block.meta["source_id"]) for block in result.blocks if block.type == "paragraph"] == [
        ("PARENT HTML BODY", "root"),
        ("CHILD BODY", "root/0"),
    ]


@pytest.mark.parametrize("name, fixture", [
    ("decision.eml", None),
    ("oleObject1.bin", None),
    ("decision.msg", "outlook-sample.msg"),
])
def test_disabled_embedded_mail_preserves_original_without_parsing_body(tmp_path, name, fixture):
    payload = (
        (Path(__file__).parent / "fixtures" / "mail" / fixture).read_bytes()
        if fixture else b"From: sender@example.test\nSubject: Decision\n\nPRIVATE MAIL BODY"
    )
    context = ParseContext("outer.docx", mail_enabled=False)

    blocks = process_embedded(
        payload, name, attachments_dir=tmp_path / "attachments", context=context,
    )

    assert len(blocks) == 1
    marker = blocks[0]
    assert marker.meta["source_id"] == "root/0"
    assert marker.meta["extraction_status"] == "skipped_disabled"
    assert "PRIVATE MAIL BODY" not in marker.text
    assert context.warnings == [{"code": "mail_import_disabled", "source_id": "root/0"}]
    assert context.sources[1].kind == "mail"
    assert Path(marker.meta["saved_path"]).read_bytes() == payload


def test_eml_plain_body_keeps_paragraph_boundaries(tmp_path):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["Subject"] = "Paragraphs"
    message.set_content("Первый абзац.\n\nВторой абзац.")
    path = tmp_path / "paragraphs.eml"
    path.write_bytes(bytes(message))

    result = parse_document_result(path)

    assert [(block.type, block.text) for block in result.blocks] == [
        ("heading", "Paragraphs"),
        ("paragraph", "Первый абзац."),
        ("paragraph", "Второй абзац."),
    ]


def test_html_line_break_keeps_words_separate(tmp_path):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.set_content("<p>Срок: 12 дней<br>Ответственный: Иванов</p>", subtype="html")
    path = tmp_path / "line-break.eml"
    path.write_bytes(bytes(message))

    result = parse_document_result(path)

    assert any(block.text == "Срок: 12 дней Ответственный: Иванов" for block in result.blocks)


def test_html_nested_table_keeps_outer_and_inner_cell_text(tmp_path):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.set_content(
        "<table><tr><td>OUTER BEFORE<table><tr><td>INNER DATA</td></tr></table>OUTER AFTER</td><td>FINAL CELL</td></tr></table>",
        subtype="html",
    )
    path = tmp_path / "nested-table.eml"
    path.write_bytes(bytes(message))

    result = parse_document_result(path)

    text = "\n".join(block.text for block in result.blocks)
    for value in ("OUTER BEFORE", "INNER DATA", "OUTER AFTER", "FINAL CELL"):
        assert value in text


def test_eml_walks_multipart_containers_to_find_nested_mail_attachment(tmp_path):
    mixed = EmailMessage()
    mixed.set_content("SIGNED BODY")
    mixed.add_attachment(
        b"From: sender@example.test\nSubject: Decision\n\nAPPROVED",
        maintype="application",
        subtype="octet-stream",
        filename="decision.eml",
    )
    signature = EmailMessage()
    signature.set_content(b"signature", maintype="application", subtype="pkcs7-signature")
    signed = EmailMessage()
    signed["From"] = "sender@example.test"
    signed["Subject"] = "Signed"
    signed.set_type("multipart/signed")
    signed.attach(mixed)
    signed.attach(signature)
    path = tmp_path / "signed.eml"
    path.write_bytes(bytes(signed))

    result = parse_document_result(path)

    assert any(block.text == "Decision" and block.meta["source_id"] == "root/0" for block in result.blocks)
    assert any(block.text == "APPROVED" and block.meta["source_id"] == "root/0" for block in result.blocks)


def test_embedded_msg_normalizes_dict_attachments_at_each_depth(tmp_path):
    context = ParseContext("outer.msg")
    message = {
        "Subject": "OUTER",
        "Body": "OUTER BODY",
        "attachments": [{
            "DisplayName": "INNER",
            "EmbeddedMessage": {
                "properties": {"Subject": "INNER", "Body": "INNER BODY"},
                "recipients": {},
                "attachments": {
                    "__attach_version1.0_00000000": {
                        "AttachLongFilename": "proof.eml",
                        "AttachDataObject": b"From: sender@example.test\nSubject: PROOF\n\nDECISION",
                    }
                },
            },
        }],
    }

    blocks = _parse_message_data(
        message,
        attachments_dir=tmp_path / "attachments",
        depth=0,
        budget=AttachmentBudget(),
        context=context,
        source_id="root",
    )

    assert [(node.source_id, node.parent_source_id) for node in context.sources] == [
        ("root", None), ("root/0", "root"), ("root/0/0", "root/0")
    ]
    assert any(block.text == "PROOF" and block.meta["source_id"] == "root/0/0" for block in blocks)
    assert any(block.text == "DECISION" and block.meta["source_id"] == "root/0/0" for block in blocks)


def test_msg_plain_body_with_angle_brackets_is_not_treated_as_html():
    blocks = _parse_message_data(
        {"Subject": "Contact", "Body": "Contact <alice@example.test> for approval."},
        attachments_dir=None,
        depth=0,
        budget=AttachmentBudget(),
        context=ParseContext("message.msg"),
        source_id="root",
    )

    assert any(block.text == r"Contact \<alice@example.test\> for approval." for block in blocks)


def test_msg_unicode_text_is_never_reinterpreted_as_mojibake():
    assert _repair_rtf_mojibake("中文测试中文测试中文测试中文测试中文") == "中文测试中文测试中文测试中文测试中文"
