# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT
import pytest

from app.services.mail_identity import mail_fingerprint, normalized_mail_identity


def _mail(**changes):
    envelope = {
        "subject": "Согласование лимита",
        "sender": "Иван <ivan@example.test>",
        "to": ["team@example.test"],
        "cc": [],
        "sent_at": "2026-09-25T07:30:00+00:00",
        "message_id": "<same-id@example.test>",
    }
    envelope.update(changes.pop("envelope", {}))
    return envelope, changes.pop("body", "Лимит: 12 дней.\r\n"), changes.pop("attachments", ["a" * 64])


def test_equivalent_unicode_and_newlines_have_the_same_mail_fingerprint():
    first = _mail(body="Решение: cafe\u0301\r\n")
    second = _mail(body="Решение: café\n")
    assert mail_fingerprint(*first) == mail_fingerprint(*second)


def test_message_id_alone_does_not_define_duplicate():
    first = _mail()
    changed_body = _mail(body="Лимит: 30 дней.\n")
    changed_attachment = _mail(attachments=["b" * 64])
    assert mail_fingerprint(*first) != mail_fingerprint(*changed_body)
    assert mail_fingerprint(*first) != mail_fingerprint(*changed_attachment)


def test_attachment_multiplicity_is_preserved_but_order_is_not():
    envelope, body, _ = _mail()
    assert mail_fingerprint(envelope, body, ["a", "b", "a"]) == mail_fingerprint(
        envelope, body, ["b", "a", "a"]
    )
    assert mail_fingerprint(envelope, body, ["a", "b", "a"]) != mail_fingerprint(
        envelope, body, ["a", "b"]
    )


def test_identity_keeps_header_order_and_business_whitespace():
    envelope, body, attachments = _mail(envelope={"to": ["a@example.test", "b@example.test"]})
    identity = normalized_mail_identity(envelope, "A  B\n", attachments)
    reversed_identity = normalized_mail_identity(
        {**envelope, "to": ["b@example.test", "a@example.test"]}, "A B\n", attachments
    )
    assert identity["headers"] != reversed_identity["headers"]
    assert identity["body"] == "A  B\n"


def test_incomplete_attachment_marker_never_produces_semantic_fingerprint(tmp_path):
    from types import SimpleNamespace

    from app.services.mail_identity import mail_fingerprint_from_parse

    sources = [SimpleNamespace(source_id="root", metadata={"mail": True, "subject": "Тема"})]
    blocks = [
        SimpleNamespace(type="paragraph", text="Текст", meta={"source_id": "root"}),
        SimpleNamespace(
            type="attachment",
            text="Вложение",
            meta={"attachment": True, "extraction_status": "skipped_size", "source_id": "root/0"},
        ),
    ]
    assert mail_fingerprint_from_parse(sources, blocks, tmp_path) is None


@pytest.mark.parametrize("warning", ["protected_mail", "unsupported_rtf_body"])
def test_root_mail_warning_prevents_fingerprint_of_an_incomplete_body(tmp_path, warning):
    from types import SimpleNamespace
    from app.services.mail_identity import mail_fingerprint_from_parse

    sources = [SimpleNamespace(source_id="root", metadata={"mail": True, "subject": "Same"},
                               warnings=[{"code": warning, "source_id": "root"}])]
    assert mail_fingerprint_from_parse(sources, [], tmp_path) is None


def test_real_msg_and_equivalent_eml_have_the_same_complete_fingerprint(tmp_path):
    from email.message import EmailMessage
    from pathlib import Path

    from docparser import parse_document
    from docparser.source_model import ParseContext
    from app.services.mail_identity import mail_fingerprint_from_parse

    msg = Path(__file__).resolve().parents[2] / "doc-parser" / "tests" / "fixtures" / "mail" / "outlook-sample.msg"

    def fingerprint(path: Path) -> str | None:
        attachments = tmp_path / f"attachments-{path.suffix[1:]}"
        context = ParseContext(path.name)
        blocks = parse_document(path, path.name, attachments_dir=attachments, context=context)
        return mail_fingerprint_from_parse(context.sources, blocks, attachments)

    msg_fingerprint = fingerprint(msg)
    message = EmailMessage()
    message["Subject"] = "Test Email Message"
    message["Date"] = "Sun, 22 Dec 2024 11:23:00 +0300"
    message["From"] = "M. C. Kurtuluş <muratcan.kurtulus@gmail.com>"
    message["To"] = "test.recipient@example.com"
    message["Message-ID"] = "<CAG5p-vYs4eK8i7X=N5XSB+4bHTg-RQqwmH9DpemS-dQwAU6Chw@mail.gmail.com>"
    message.set_content("This is the body of the test email message")
    eml = tmp_path / "equivalent.eml"
    eml.write_bytes(bytes(message))

    assert msg_fingerprint is not None
    assert fingerprint(eml) == msg_fingerprint
