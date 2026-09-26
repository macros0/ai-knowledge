"""Mail provenance must retain dates and threading without copying headers wholesale."""
import struct
from datetime import UTC, datetime, timedelta, timezone

import pytest
from docparser import parse_document_result
from docparser.msg_parser import _parse_message_data
from docparser.source_model import ParseContext

from tests.mail_fixtures import compound_bytes


@pytest.mark.parametrize("date", [None, "not a date"])
def test_native_msg_keeps_exchange_recipient_without_inventing_sender_or_smtp(tmp_path, date):
    exchange_address = "/O=SYNTHETIC/OU=EXCHANGE/CN=RECIPIENTS/CN=REVIEWER"
    recipient = "__recip_version1.0_#00000000/"
    streams = {
        "__properties_version1.0": b"\0" * 32,
        "__substg1.0_1000001F": "Meaningful body.\0".encode("utf-16-le"),
        recipient + "__properties_version1.0": b"\0" * 8 + struct.pack("<IIQ", 0x0C150003, 0, 1),
        recipient + "__substg1.0_3002001F": "EX\0".encode("utf-16-le"),
        recipient + "__substg1.0_3003001F": (exchange_address + "\0").encode("utf-16-le"),
    }
    if date is not None:
        streams["__substg1.0_007D001F"] = (f"Date: {date}\r\n\0").encode("utf-16-le")
    path = tmp_path / "exchange.msg"
    path.write_bytes(compound_bytes(streams))

    parsed = parse_document_result(path)

    metadata = parsed.sources[0].metadata
    assert metadata["sender"] == ""
    assert metadata["to"] == [exchange_address]
    assert metadata["sent_at"] is None
    assert metadata.get("date_raw") == date
    assert "@" not in str(metadata)
    assert any(block.text == "Meaningful body." for block in parsed.blocks)


@pytest.mark.parametrize("date,expected", [
    ("Fri, 25 Sep 2026 10:30:00 +0300", "2026-09-25T07:30:00+00:00"),
    ("Fri, 25 Sep 2026 10:30:00 -0000", None),
    ("not a date", None),
])
def test_eml_preserves_original_date_and_thread_ids(tmp_path, date, expected):
    path = tmp_path / "thread.eml"
    path.write_bytes((
        f"From: sender@example.test\r\nDate: {date}\r\n"
        "In-Reply-To: <parent@example.test>\r\n"
        "References: <first@example.test>\r\n <parent@example.test>\r\n"
        "Bcc: private@example.test\r\nReceived: private routing\r\n\r\nBody."
    ).encode())
    meta = parse_document_result(path).sources[0].metadata
    assert meta["sent_at"] == expected
    assert meta["date_raw"] == date
    assert meta["in_reply_to"] == "<parent@example.test>"
    assert meta["references"].split() == ["<first@example.test>", "<parent@example.test>"]
    assert "private" not in str(meta)


@pytest.mark.parametrize("sent,expected", [
    ("Fri, 25 Sep 2026 10:30:00 +0300", "2026-09-25T07:30:00+00:00"),
    ("Fri, 25 Sep 2026 10:30:00 -0000", None),
    ("not a date", None),
    (datetime(2026, 9, 25, 7, 30, tzinfo=UTC).replace(tzinfo=None), "2026-09-25T07:30:00+00:00"),
    (datetime(2026, 9, 25, 10, 30, tzinfo=timezone(timedelta(hours=3))), "2026-09-25T07:30:00+00:00"),
])
def test_msg_metadata_keeps_date_and_selected_properties(sent, expected):
    message = {"properties": {
            "Subject": "Decision", "Sender": "sender@example.test",
            "SentAt": sent, "MessageId": "<message@example.test>",
            "Body": "Body.", "MessageClass": "IPM.Note",
            "InReplyToId": "<parent@example.test>",
            "InternetReferences": "<first@example.test> <parent@example.test>",
            "TransportMessageHeaders": "Bcc: private@example.test\r\nReceived: private routing",
        }}
    context = ParseContext("mail.msg")
    _parse_message_data(message, attachments_dir=None, depth=0, budget=None, context=context, source_id="root")
    meta = context.sources[0].metadata
    assert meta["sent_at"] == expected
    assert meta["date_raw"] == (sent.isoformat() if isinstance(sent, datetime) else sent)
    assert meta["message_class"] == "IPM.Note"
    assert meta["in_reply_to"] == "<parent@example.test>"
    assert meta["references"] == "<first@example.test> <parent@example.test>"
    assert "private" not in str(meta)


def test_nested_msg_uses_submission_time_before_delivery_time():
    context = ParseContext("outer.msg")
    nested = {"properties": {
        "Body": "Child.", "ClientSubmitTime": datetime(2026, 9, 25, 7, 30, tzinfo=UTC),
        "DeliverTime": datetime(2026, 9, 25, 8, tzinfo=UTC), "MessageClass": "IPM.Note",
        "InReplyToId": "<parent@example.test>",
    }}
    _parse_message_data(
        {"Body": "Parent.", "attachments": [{"EmbeddedMessage": nested}]},
        attachments_dir=None, depth=0, budget=None, context=context, source_id="root",
    )
    meta = context.sources[1].metadata
    assert meta["sent_at"] == "2026-09-25T07:30:00+00:00"
    assert meta["message_class"] == "IPM.Note"
    assert meta["in_reply_to"] == "<parent@example.test>"
