import hashlib
import socket
import zipfile
from email.header import Header
from email.message import EmailMessage
from pathlib import Path

from docparser import parse_document, parse_document_result

from tests.fixtures import make_docx_with_embedded_xlsx, make_xlsx, xlsx_bytes

MSG_FIXTURE = Path(__file__).parent / "fixtures" / "mail" / "outlook-sample.msg"
RTF_MSG_FIXTURE = Path(__file__).parent / "fixtures" / "mail" / "rtf-simple-sent.msg"
NESTED_MSG_FIXTURE = Path(__file__).parent / "fixtures" / "mail" / "nested-rtf.msg"


def test_msg_fixture_extracts_subject_and_body_without_outlook():
    """MIT fixture from MarkItDown: real OLE/MAPI MSG, not a mock container."""
    assert hashlib.sha256(MSG_FIXTURE.read_bytes()).hexdigest() == (
        "028d84ffe67e1865009669d13d4c12682943b32eccf7f84a8da1899db63b0131"
    )

    blocks = parse_document(MSG_FIXTURE)

    assert blocks[0].text == "Test Email Message"
    assert any("body of the test email" in block.text for block in blocks)


def test_ansi_msg_with_plain_and_rtf_bodies_is_decoded_without_outlook():
    """Apache-2.0 fixture has both Body001E and RtfCompressed, not RTF-only."""
    assert hashlib.sha256(RTF_MSG_FIXTURE.read_bytes()).hexdigest() == (
        "8adf5c3c77b46d9fa5910c3bb6ba54930160c4a9292348669f11c4d17cac8421"
    )

    blocks = parse_document(RTF_MSG_FIXTURE)

    assert blocks[0].text.startswith("(outlookEMLandMSGconverter Trial Version Import) BitDaddys Softwar")
    assert not any(0x3400 <= ord(char) <= 0x9FFF for char in blocks[0].text)
    assert any("We have added your software to our approved list." in block.text for block in blocks)


def test_msg_embedded_substorage_keeps_child_source_and_text():
    """Apache-2.0 fixture: вложенный MSG — OLE substorage, не byte stream."""
    assert hashlib.sha256(NESTED_MSG_FIXTURE.read_bytes()).hexdigest() == (
        "ee87b46667a0f262b3f119967a5854610bd197508067c51b53fb7ff0abfb8962"
    )

    result = parse_document_result(NESTED_MSG_FIXTURE)

    assert [(node.source_id, node.parent_source_id, node.kind, node.display_name) for node in result.sources] == [
        ("root", None, "document", "nested-rtf.msg"),
        ("root/0", "root", "mail", "outlookmsg2html Testmail"),
    ]
    marker = next(block for block in result.blocks if block.type == "attachment")
    assert marker.meta["source_id"] == "root/0"
    assert marker.meta["extraction_status"] == "parsed"
    child_body = next(block for block in result.blocks if block.meta.get("source_id") == "root/0" and block.type == "paragraph")
    import olefile
    with olefile.OleFileIO(NESTED_MSG_FIXTURE) as ole:
        raw_child = ole.openstream([
            "__attach_version1.0_#00000000", "__substg1.0_3701000D", "__substg1.0_1000001F",
        ]).read().decode("utf-16-le").rstrip("\0").strip()
    assert child_body.text == raw_child
    assert "This is a testmail." in child_body.text
    assert "Mail in mail." not in child_body.text  # parent's body, previously misattributed


def test_eml_exposes_subject_sender_date_and_plain_body(tmp_path: Path):
    """Регрессия: без ветки EML письмо отвергается до извлечения фактов."""
    eml = tmp_path / "agreement.eml"
    eml.write_bytes(
        b"From: =?utf-8?B?0JjQstCw0L0g0JjQstCw0L3QvtCy?= <ivan@example.test>\r\n"
        b"To: team@example.test\r\n"
        b"Subject: =?utf-8?B?0KHQvtCz0LvQsNGB0L7QstCw0L3QuNC1?=\r\n"
        b"Date: Thu, 25 Sep 2026 10:30:00 +0300\r\n"
        b"Message-ID: <agreement-42@example.test>\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"\xd0\xa1\xd0\xbe\xd0\xb3\xd0\xbb\xd0\xb0\xd1\x81\xd0\xbe\xd0\xb2\xd0\xb0\xd0\xbd\xd0\xbe: \xd0\xbb\xd0\xb8\xd0\xbc\xd0\xb8\xd1\x82 12 \xd0\xb4\xd0\xbd\xd0\xb5\xd0\xb9.\r\n"
    )

    blocks = parse_document(eml)

    assert [block.type for block in blocks] == ["heading", "paragraph"]
    assert blocks[0].text == "Согласование"
    assert blocks[0].meta == {
        "mail": True,
        "sender": "Иван Иванов <ivan@example.test>",
        "to": ["team@example.test"],
        "cc": [],
        "sent_at": "2026-09-25T07:30:00+00:00",
        "date_raw": "Thu, 25 Sep 2026 10:30:00 +0300",
        "message_id": "<agreement-42@example.test>",
        "source_id": "root",
    }
    assert blocks[1].text == "Согласовано: лимит 12 дней."


def test_eml_decodes_cp1251_body(tmp_path: Path):
    message = EmailMessage()
    message["Subject"] = "Кодировка"
    message.set_content("Срок согласования — 12 дней.", charset="cp1251")
    path = tmp_path / "cp1251.eml"
    path.write_bytes(bytes(message))

    blocks = parse_document(path)

    assert any(block.text == "Срок согласования — 12 дней." for block in blocks)


def test_eml_decodes_koi8_encoded_folded_subject_and_rfc2231_filename(tmp_path: Path):
    subject = Header("Согласование срока дней", "utf-8", maxlinelen=30).encode().replace("\n", "\r\n")
    path = tmp_path / "mime-edge-cases.eml"
    path.write_bytes(
        b"From: sender@example.test\r\n"
        + b"Subject: "
        + subject.encode("ascii")
        + b"\r\nContent-Type: text/plain; charset=koi8-r\r\n\r\n"
        + "Решение принято.".encode("koi8-r")
    )

    blocks = parse_document(path)

    assert blocks[0].text == "Согласование срока дней"
    assert any(block.text == "Решение принято." for block in blocks)

    attachment = EmailMessage()
    attachment["From"] = "sender@example.test"
    attachment.set_content("См. вложение.")
    attachment.add_attachment(
        b"opaque",
        maintype="application",
        subtype="octet-stream",
        filename="решение.xlsx",
    )
    attachment_path = tmp_path / "rfc2231.eml"
    attachment_path.write_bytes(bytes(attachment))
    marker = next(block for block in parse_document(attachment_path) if block.type == "attachment")
    assert marker.meta["name"] == "решение.xlsx"


def test_multipart_alternative_uses_one_plain_body_without_html_duplicate(tmp_path: Path):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["Subject"] = "Альтернатива"
    message.set_content("Один канонический текст.")
    message.add_alternative("<p>Один <strong>канонический</strong> текст.</p>", subtype="html")
    path = tmp_path / "alternative.eml"
    path.write_bytes(bytes(message))

    texts = [block.text for block in parse_document(path)]

    assert texts.count("Один канонический текст.") == 1


def test_eml_recursively_extracts_xlsx_attachment(tmp_path: Path):
    """Регрессия: MIME attachment не должен выпадать из общего пути вложений."""
    message = EmailMessage()
    message["From"] = "ivan@example.test"
    message["Subject"] = "Расчёт"
    message.set_content("Расчёт приложен.")
    message.add_attachment(
        xlsx_bytes(),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="report.xlsx",
    )
    eml = tmp_path / "report.eml"
    eml.write_bytes(bytes(message))

    blocks = parse_document(eml, attachments_dir=tmp_path / "attachments")

    marker = next(block for block in blocks if block.type == "attachment")
    assert marker.meta["name"] == "report.xlsx"
    assert marker.meta["parsed"] is True
    assert (tmp_path / "attachments" / "source-root-0.xlsx").is_file()
    table = next(block for block in blocks if block.type == "table")
    assert "Правило" in table.text


def test_eml_recursively_extracts_nested_eml(tmp_path: Path):
    """Регрессия: письмо-файл должно быть источником текста, а не opaque blob."""
    nested = EmailMessage()
    nested["From"] = "olga@example.test"
    nested["Subject"] = "Первичное решение"
    nested.set_content("Лимит согласован: 12 дней.")

    outer = EmailMessage()
    outer["From"] = "ivan@example.test"
    outer["Subject"] = "Пересылаю решение"
    outer.set_content("См. приложенное письмо.")
    outer.add_attachment(
        bytes(nested),
        maintype="application",
        subtype="octet-stream",
        filename="decision.eml",
    )
    eml = tmp_path / "forward.eml"
    eml.write_bytes(bytes(outer))

    blocks = parse_document(eml, attachments_dir=tmp_path / "attachments")

    marker = next(block for block in blocks if block.type == "attachment")
    assert marker.meta["name"] == "decision.eml"
    assert marker.meta["parsed"] is True
    nested_heading = next(block for block in blocks if block.type == "heading" and block.text == "Первичное решение")
    assert nested_heading.meta["from_attachment"] is True
    assert nested_heading.meta["attachment_name"] == "decision.eml"
    assert any(block.text == "Лимит согласован: 12 дней." for block in blocks)


def test_eml_recursively_extracts_message_rfc822_attachment(tmp_path: Path):
    """Стандартный MIME forwarding хранит письмо как message/rfc822, не octet-stream."""
    nested = EmailMessage()
    nested["Subject"] = "Решение комиссии"
    nested.set_content("Утвердить срок 12 дней.")

    outer = EmailMessage()
    outer["Subject"] = "FW: решение"
    outer.set_content("Пересылаю письмо.")
    outer.add_attachment(nested)
    eml = tmp_path / "forwarded.eml"
    eml.write_bytes(bytes(outer))

    blocks = parse_document(eml)

    marker = next(block for block in blocks if block.type == "attachment")
    assert marker.meta["parsed"] is True
    assert marker.meta["extraction_status"] == "parsed"
    assert any(block.text == "Решение комиссии" for block in blocks)
    assert any(block.text == "Утвердить срок 12 дней." for block in blocks)


def test_parse_result_keeps_nested_mail_source_path(tmp_path: Path):
    """Регрессия: одинаковые имена не должны быть единственной связью источника."""
    nested = EmailMessage()
    nested["Subject"] = "Условия"
    nested.set_content("Срок: 12 дней.")

    outer = EmailMessage()
    outer["Subject"] = "Пересылка"
    outer.set_content("Во вложении исходное письмо.")
    outer.add_attachment(
        bytes(nested), maintype="application", subtype="octet-stream", filename="terms.eml"
    )
    eml = tmp_path / "outer.eml"
    eml.write_bytes(bytes(outer))

    result = parse_document_result(eml)

    assert [(node.source_id, node.parent_source_id, node.kind, node.display_name) for node in result.sources] == [
        ("root", None, "document", "outer.eml"),
        ("root/0", "root", "mail", "terms.eml"),
    ]
    assert result.sources[1].metadata == {
        "subject": "Условия",
        "mail": True,
        "sender": "",
        "to": [],
        "cc": [],
        "sent_at": None,
        "message_id": "",
    }
    child_heading = next(block for block in result.blocks if block.text == "Условия")
    assert child_heading.meta["source_id"] == "root/0"


def test_eml_html_only_keeps_text_and_table_without_fetching(tmp_path: Path):
    """Регрессия: HTML-only письмо не должно исчезать или требовать браузер."""
    message = EmailMessage()
    message["Subject"] = "Параметры"
    message.set_content(
        "<p>Лимит <strong>12</strong> дней.</p>"
        "<table><tr><th>Код</th><th>Срок</th></tr><tr><td>A-1</td><td>12</td></tr></table>",
        subtype="html",
    )
    eml = tmp_path / "parameters.eml"
    eml.write_bytes(bytes(message))

    blocks = parse_document(eml)

    assert [block.type for block in blocks] == ["heading", "paragraph", "table"]
    assert blocks[1].text == "Лимит 12 дней."
    assert blocks[2].text == "| Код | Срок |\n|---|---|\n| A-1 | 12 |"


def test_eml_hostile_html_stays_offline_and_omits_active_content(tmp_path: Path, monkeypatch):
    """HTML body is text-only: no requests and no script/form/iframe payload in blocks."""
    def unexpected_network(*args, **kwargs):
        raise AssertionError("mail HTML parser must not open a network connection")

    monkeypatch.setattr(socket, "create_connection", unexpected_network)
    message = EmailMessage()
    message["Subject"] = "Безопасный HTML"
    message.set_content(
        "<p>Лимит 12 дней.</p>"
        "<a href='javascript:alert(1)'>Ссылка</a>"
        "<img src='https://tracker.example.test/pixel.gif'>"
        "<img src='cid:private-image'>"
        "<script>secret_script()</script><style>.secret{display:none}</style>"
        "<iframe>secret_iframe</iframe><form>secret_form</form>"
        "<svg><script>secret_svg()</script><text>Подпись схемы</text></svg>",
        subtype="html",
    )
    path = tmp_path / "hostile.eml"
    path.write_bytes(bytes(message))

    text = "\n".join(block.text for block in parse_document(path))

    assert "Лимит 12 дней." in text
    assert "Ссылка" in text
    assert "Подпись схемы" in text
    assert all(forbidden not in text for forbidden in (
        "secret_script", "secret_iframe", "secret_form", "secret_svg", "tracker.example.test", "javascript:", "cid:"
    ))


def test_docx_table_recursively_extracts_embedded_eml(tmp_path: Path):
    """OLE в ячейке не должен обходить общий путь рекурсивных вложений."""
    message = EmailMessage()
    message["Subject"] = "Решение из ячейки"
    message.set_content("Срок — 12 дней.")
    docx = make_docx_with_embedded_xlsx(
        tmp_path / "table.docx", bytes(message), prog_id="Outlook.FileMsg.15",
        filename="decision.eml", in_table=True,
    )

    blocks = parse_document(docx)

    assert any(block.text == "Решение из ячейки" for block in blocks)
    assert any(block.text == "Срок — 12 дней." for block in blocks)


def test_docx_recursively_extracts_embedded_msg(tmp_path: Path):
    docx = make_docx_with_embedded_xlsx(
        tmp_path / "message.docx",
        MSG_FIXTURE.read_bytes(),
        prog_id="Outlook.FileMsg.15",
        filename="forwarded.msg",
    )

    result = parse_document_result(docx)

    assert any(block.text == "Test Email Message" for block in result.blocks)
    assert any("body of the test email" in block.text for block in result.blocks)
    assert [(node.source_id, node.parent_source_id, node.kind, node.display_name) for node in result.sources] == [
        ("root", None, "document", "message.docx"),
        ("root/0", "root", "mail", "forwarded.msg"),
    ]


def test_xlsx_embedded_bin_with_rfc822_payload_is_parsed_as_mail(tmp_path: Path):
    """Excel stores embedded OLE as .bin, so the type must come from content."""
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["Subject"] = "Решение из XLSX"
    message.set_content("Срок согласован: 12 дней.")
    workbook = make_xlsx(tmp_path / "embedded-mail.xlsx")
    with zipfile.ZipFile(workbook, "a") as archive:
        archive.writestr("xl/embeddings/oleObject1.bin", bytes(message))

    result = parse_document_result(workbook)

    assert [(node.source_id, node.kind) for node in result.sources] == [
        ("root", "document"),
        ("root/0", "mail"),
    ]
    assert any(block.text == "Решение из XLSX" for block in result.blocks)
    assert any(block.text == "Срок согласован: 12 дней." for block in result.blocks)


def test_pdf_embedded_eml_uses_the_same_recursive_mail_path(tmp_path: Path):
    from pypdf import PdfWriter

    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["Subject"] = "Решение из PDF"
    message.set_content("Подтверждён срок 12 дней.")
    path = tmp_path / "embedded-mail.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_attachment("forwarded.eml", bytes(message))
    with path.open("wb") as target:
        writer.write(target)

    result = parse_document_result(path)

    assert [(node.source_id, node.kind) for node in result.sources] == [
        ("root", "document"),
        ("root/0", "mail"),
    ]
    assert any(block.text == "Решение из PDF" for block in result.blocks)
    assert any(block.text == "Подтверждён срок 12 дней." for block in result.blocks)


def test_root_eml_without_structural_headers_is_rejected(tmp_path: Path):
    import pytest
    from docparser.parser import ParseError

    broken = tmp_path / "broken.eml"
    broken.write_bytes(b"Subject: this alone is not a message\n\nopaque bytes")

    with pytest.raises(ParseError, match="RFC 5322"):
        parse_document(broken)


def test_root_text_budget_truncates_canonical_mail_text_once(tmp_path: Path, monkeypatch):
    from docparser import embedded

    monkeypatch.setattr(embedded, "MAX_TEXT_CHARS", 20)
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["Subject"] = "Заголовок"
    message.set_content("Согласовано: срок составляет двенадцать дней.")
    path = tmp_path / "long.eml"
    path.write_bytes(bytes(message))

    result = parse_document_result(path)

    assert sum(len(block.text) for block in result.blocks) == 20
    assert result.warnings == [{"code": "text_limit_exceeded", "source_id": "root"}]


def test_text_budget_keeps_attachment_metadata_after_text_is_exhausted(tmp_path: Path, monkeypatch):
    from docparser import embedded

    monkeypatch.setattr(embedded, "MAX_TEXT_CHARS", 1)
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message.set_content("body")
    message.add_attachment(
        b"opaque",
        maintype="application",
        subtype="octet-stream",
        filename="proof.bin",
    )
    path = tmp_path / "attachment-after-limit.eml"
    path.write_bytes(bytes(message))

    blocks = parse_document(path, attachments_dir=tmp_path / "attachments")

    marker = next(block for block in blocks if block.type == "attachment")
    assert marker.text == ""
    assert marker.meta["name"] == "proof.bin"
    assert marker.meta["saved_path"]


def test_encrypted_smime_eml_is_diagnosed_without_indexing_opaque_body(tmp_path: Path):
    path = tmp_path / "encrypted.eml"
    path.write_bytes(
        b"From: sender@example.test\r\n"
        b"Subject: Confidential\r\n"
        b"Content-Type: application/pkcs7-mime; smime-type=enveloped-data\r\n"
        b"\r\n"
        b"opaque-encrypted-ciphertext"
    )

    result = parse_document_result(path)

    assert [block.text for block in result.blocks] == ["Confidential"]
    assert result.warnings == [{"code": "encrypted_mail", "source_id": "root"}]


def test_bcc_and_transport_headers_never_enter_mail_metadata_or_text(tmp_path: Path):
    path = tmp_path / "private-routing.eml"
    path.write_bytes(
        b"From: sender@example.test\r\n"
        b"To: team@example.test\r\n"
        b"Bcc: private@example.test\r\n"
        b"Received: from internal.example.test by gateway.example.test\r\n"
        b"Subject: Public decision\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        b"Approved for publication."
    )

    result = parse_document_result(path)

    assert result.sources[0].metadata == {
        "subject": "Public decision",
        "mail": True,
        "sender": "sender@example.test",
        "to": ["team@example.test"],
        "cc": [],
        "sent_at": None,
        "message_id": "",
    }
    text = "\n".join(block.text for block in result.blocks)
    assert "private@example.test" not in text
    assert "internal.example.test" not in text
