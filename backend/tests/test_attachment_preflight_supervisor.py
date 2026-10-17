from dataclasses import asdict
from email.message import EmailMessage
import io

from openpyxl import Workbook

from app.services.parser_supervisor import inspect_attachments_supervised, parse_document_supervised
from docparser.attachment_assessment import recommend_scope
from docparser.attachment_policy import AssessmentLimits, TraversalLimits


def test_supervised_inventory_then_scoped_parse(tmp_path):
    book = Workbook()
    for _ in range(600):
        book.active.append(["EXCLUDED EVIDENCE"])
    data = io.BytesIO()
    book.save(data)
    message = EmailMessage()
    message["Subject"] = "Decision"
    message.set_content("ROOT EVIDENCE")
    message.add_attachment(
        data.getvalue(), maintype="application", subtype="octet-stream", filename="heavy.xlsx"
    )
    path = tmp_path / "letter.eml"
    path.write_bytes(message.as_bytes())
    limits = AssessmentLimits()
    inventory = inspect_attachments_supervised(
        path,
        path.name,
        attachments_dir=tmp_path / "inspection",
        limits=limits,
        traversal=TraversalLimits(),
        timeout_seconds=15,
        max_memory_mb=1024,
    )
    assert inventory.nodes[0].assessment == "heavy"
    decisions = [asdict(d) for d in recommend_scope(inventory, limits).decisions]
    result = parse_document_supervised(
        path,
        path.name,
        attachments_dir=tmp_path / "parsed",
        timeout_seconds=30,
        max_memory_mb=1024,
        processing_policy={
            "schema_version": 1,
            "source_file_hash": inventory.source_file_hash,
            "decisions": decisions,
            "traversal_limits": asdict(TraversalLimits()),
        },
    )
    assert "ROOT EVIDENCE" in "\n".join(b.text for b in result.blocks)
    assert "EXCLUDED EVIDENCE" not in "\n".join(b.text for b in result.blocks)
