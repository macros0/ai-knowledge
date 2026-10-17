import hashlib
from dataclasses import asdict

from docparser.attachment_assessment import recommend_scope
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_policy import AssessmentLimits, TraversalLimits

from tests.attachment_fixtures import mail_bytes, office_container, spreadsheet


def inspect(path, **kwargs):
    return inspect_attachment_inventory(
        path, path.name, limits=AssessmentLimits(**kwargs), traversal=TraversalLimits()
    )


def test_inventory_stops_xlsx_at_soft_threshold_without_calling_document_parser(
    tmp_path, monkeypatch
):
    import docparser.parser

    monkeypatch.setattr(
        docparser.parser,
        "parse_document",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("full parse")),
    )
    path = office_container(tmp_path / "decision.docx", [("large.xlsx", spreadsheet(rows=7000))])
    result = inspect(path)
    assert result.source_file_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert len(result.nodes) == 1
    assert result.nodes[0].metrics["rows"] == 500
    assert result.nodes[0].precision == "lower_bound"
    assert result.nodes[0].assessment == "heavy"
    assert result.scan_bytes < 200000  # Stop reading worksheet XML at the soft bound.
    assert recommend_scope(result, AssessmentLimits()).requires_review
    assert asdict(result) == asdict(inspect(path)) | {"elapsed_seconds": result.elapsed_seconds}


def test_empty_formatted_tail_not_counted(tmp_path):
    path = office_container(
        tmp_path / "decision.docx", [("small.xlsx", spreadsheet(rows=4, tail=True))]
    )
    result = inspect(path)
    assert result.nodes[0].metrics["rows"] == 4
    assert result.nodes[0].metrics["cells"] == 8
    assert result.nodes[0].assessment == "small"


def test_root_xlsx_is_not_an_attachment(tmp_path):
    path = tmp_path / "root.xlsx"
    path.write_bytes(spreadsheet(rows=600))
    assert inspect(path).nodes == []


def test_nested_mail_keeps_body_selected_and_identifies_child(tmp_path):
    data = mail_bytes([("same.xlsx", spreadsheet(rows=600)), ("same.xlsx", spreadsheet(rows=2))])
    path = office_container(tmp_path / "decision.docx", [("approval.eml", data)])
    result = inspect(path)
    assert [n.kind for n in result.nodes] == ["mail", "xlsx", "xlsx"]
    assert result.nodes[1].parent_key == result.nodes[0].source_key
    assert result.nodes[1].source_key != result.nodes[2].source_key
    assert [d.selected for d in recommend_scope(result, AssessmentLimits()).decisions] == [
        True,
        False,
        True,
    ]


def test_global_byte_budget_does_not_silently_include_uninspected_entries(tmp_path):
    path = office_container(
        tmp_path / "decision.docx",
        [("a.xlsx", spreadsheet(rows=10)), ("b.xlsx", spreadsheet(rows=10))],
    )
    result = inspect(path, scan_bytes=128)
    assert not result.complete
    assert result.nodes
    assert recommend_scope(result, AssessmentLimits()).requires_review


def test_recognized_presentation_without_parser_is_unsupported(tmp_path):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", '<Types/>')
        archive.writestr("ppt/presentation.xml", '<presentation/>')
    path = tmp_path / "message.eml"
    path.write_bytes(mail_bytes([("slides.pptx", buffer.getvalue())]))
    inventory = inspect(path)
    node = inventory.nodes[0]
    assert node.kind == "pptx"
    assert node.supported is False
    assert node.assessment == "unsupported"
    assert recommend_scope(inventory, AssessmentLimits()).decisions[0].selected is False
