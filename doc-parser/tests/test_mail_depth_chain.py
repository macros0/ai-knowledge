"""M11: real mixed MSG -> EML -> MSG -> XLSX stops before depth-three reads."""
from email.message import EmailMessage
from pathlib import Path

import pytest
from docparser import parse_document_result

from tests.fixtures import xlsx_bytes
from tests.mail_fixtures import unicode_msg


@pytest.mark.parametrize("outer_msg", [False, True])
def test_mixed_mail_chain_stops_xlsx_before_read_at_depth_three(tmp_path, monkeypatch, outer_msg):
    sheet = xlsx_bytes(rows=[["Code", "Value"], [3509, "FORBIDDEN XLSX VALUE"]])
    inner = unicode_msg(subject="Inner MSG", body="INNER VISIBLE", attachments={"data.xlsx": sheet})
    middle = EmailMessage()
    middle["From"] = "middle@example.test"
    middle["Subject"] = "Middle EML"
    middle.set_content("MIDDLE VISIBLE")
    middle.add_attachment(inner, maintype="application", subtype="vnd.ms-outlook", filename="inner.msg")
    payload = middle.as_bytes()
    if outer_msg:
        payload = unicode_msg(subject="Outer MSG", body="OUTER VISIBLE", attachments={"middle.eml": payload})
    path = tmp_path / ("chain.msg" if outer_msg else "chain.eml")
    path.write_bytes(payload)

    from docparser import msg_parser
    actual_read = msg_parser._read_stream
    read_xlsx = []

    def observe(ole, stream_path, *args, **kwargs):
        raw = actual_read(ole, stream_path, *args, **kwargs)
        if raw == sheet:
            read_xlsx.append(True)
        return raw

    monkeypatch.setattr(msg_parser, "_read_stream", observe)
    result = parse_document_result(path, attachments_dir=tmp_path / "attachments")
    assert read_xlsx == ([] if outer_msg else [True])
    text = "\n".join(block.text for block in result.blocks)
    assert "MIDDLE VISIBLE" in text and "INNER VISIBLE" in text
    assert ("FORBIDDEN XLSX VALUE" in text) is (not outer_msg)
    markers = [block for block in result.blocks if block.type == "attachment"]
    if outer_msg:
        assert [node.source_id for node in result.sources] == ["root", "root/0", "root/0/0", "root/0/0/0"]
        assert markers[-1].meta["extraction_status"] == "skipped_depth"
        assert result.warnings == [{"code": "attachment_depth_exceeded", "source_id": "root/0/0/0"}]
        assert "saved_path" not in markers[-1].meta
        assert Path(markers[1].meta["saved_path"]).read_bytes() == inner
    else:
        assert result.warnings == []
        assert Path(markers[-1].meta["saved_path"]).read_bytes() == sheet
