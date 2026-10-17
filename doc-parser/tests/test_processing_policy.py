from dataclasses import asdict

import pytest
from docparser import ParseContext, parse_document
from docparser.attachment_assessment import recommend_scope
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_policy import (
    AssessmentLimits,
    ParserProcessingPolicy,
    TraversalLimits,
)

from tests.attachment_fixtures import mail_bytes, office_container, spreadsheet
from tests.test_archive_reader import archive_bytes


def policy(path, *, all_selected=False):
    inv = inspect_attachment_inventory(
        path, path.name, limits=AssessmentLimits(), traversal=TraversalLimits()
    )
    decisions = [asdict(d) for d in recommend_scope(inv, AssessmentLimits()).decisions]
    if all_selected:
        for d in decisions:
            d["selected"] = d["effective_included"] = True
    return ParserProcessingPolicy(
        {
            "schema_version": 1,
            "source_file_hash": inv.source_file_hash,
            "traversal_limits": asdict(TraversalLimits()),
            "decisions": decisions,
        }
    )


def test_excluded_xlsx_never_reaches_document_parser(tmp_path, monkeypatch):
    import docparser.parser

    path = office_container(tmp_path / "decision.docx", [("large.xlsx", spreadsheet(rows=700))])
    chosen = policy(path)
    monkeypatch.setattr(
        docparser.parser,
        "parse_xlsx",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("excluded parser called")),
    )
    context = ParseContext(path.name, processing_policy=chosen)
    blocks = parse_document(path, attachments_dir=tmp_path / "attachments", context=context)
    assert any("ROOT EVIDENCE" in b.text for b in blocks)
    marker = next(b for b in blocks if b.type == "attachment")
    assert marker.meta["extraction_status"] == "excluded_by_policy"
    assert not any(b.type == "table" for b in blocks)
    assert len(list((tmp_path / "attachments").iterdir())) == 1


@pytest.mark.parametrize("kind", ["zip", "7z", "rar"])
def test_policy_reaches_archive_in_mail_and_retains_light_sibling(kind, tmp_path):
    archive = archive_bytes(
        kind,
        [
            ("heavy.xlsx", spreadsheet(rows=700, text="EXCLUDED UNIQUE")),
            ("light.xlsx", spreadsheet(rows=2, text="INCLUDED UNIQUE")),
        ],
        tmp_path,
    )
    root = office_container(
        tmp_path / "decision.docx",
        [("mail.eml", mail_bytes([("files." + kind, archive.read_bytes())]))],
    )
    context = ParseContext(root.name, processing_policy=policy(root))
    blocks = parse_document(root, context=context, attachments_dir=tmp_path / "attachments")
    text = "\n".join(b.text for b in blocks)
    assert "USEFUL MAIL BODY" in text
    assert "INCLUDED UNIQUE" in text
    assert "EXCLUDED UNIQUE" not in text
    assert any(b.meta.get("extraction_status") == "excluded_by_policy" for b in blocks)
    assert all(n.metadata.get("source_key") for n in context.sources if n.source_id != "root")
    for block in blocks:
        if block.meta.get("extraction_status") == "excluded_by_policy":
            assert not block.meta.get("saved_path")  # Original remains in its archive.


def test_content_id_on_excel_does_not_bypass_exclusion(tmp_path):
    from email.message import EmailMessage

    message = EmailMessage()
    message.set_content("MAIL BODY")
    message.add_attachment(
        spreadsheet(rows=700, text="CID_EXCLUDED_CANARY"),
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="heavy.xlsx",
        cid="<spreadsheet-id>",
    )
    root = tmp_path / "decision.eml"
    root.write_bytes(message.as_bytes())
    context = ParseContext(root.name, processing_policy=policy(root))
    blocks = parse_document(root, context=context, attachments_dir=tmp_path / "attachments")
    assert not any("CID_EXCLUDED_CANARY" in b.text for b in blocks)
    assert any(b.meta.get("extraction_status") == "excluded_by_policy" for b in blocks)


def test_depth_six_is_allowed_seven_is_visible_and_excluded(tmp_path):
    data = spreadsheet(rows=2, text="DEPTH_CANARY")
    name = "leaf.xlsx"
    for level in range(6):
        data = mail_bytes([(name, data)])
        name = f"level{level}.eml"
    root = tmp_path / "root.eml"
    root.write_bytes(data)
    inv = inspect_attachment_inventory(
        root, root.name, limits=AssessmentLimits(), traversal=TraversalLimits()
    )
    assert len(inv.nodes) == 6 and inv.nodes[-1].kind == "xlsx"
    assert any(
        "DEPTH_CANARY" in b.text
        for b in parse_document(
            root, context=ParseContext(root.name, processing_policy=policy(root))
        )
    )
    root.write_bytes(mail_bytes([(name, data)]))
    inv = inspect_attachment_inventory(
        root, root.name, limits=AssessmentLimits(), traversal=TraversalLimits()
    )
    assert len(inv.nodes) == 7 and inv.nodes[-1].status == "skipped_depth"
    assert not any(
        "DEPTH_CANARY" in b.text
        for b in parse_document(
            root, context=ParseContext(root.name, processing_policy=policy(root))
        )
    )


@pytest.mark.parametrize("kind", ["zip", "7z", "rar"])
def test_docx_msg_archive_excel_obeys_same_selection(tmp_path, kind):
    from tests.attachment_fixtures import msg_bytes

    archive = archive_bytes(
        kind,
        [
            ("heavy.xlsx", spreadsheet(rows=700, text="MSG_EXCLUDED_CANARY")),
            ("light.xlsx", spreadsheet(rows=2, text="MSG_INCLUDED_EVIDENCE")),
        ],
        tmp_path,
    )
    root = office_container(
        tmp_path / "decision.docx",
        [("approval.msg", msg_bytes([("files." + kind, archive.read_bytes())]))],
    )
    inventory = inspect_attachment_inventory(
        root, root.name, limits=AssessmentLimits(), traversal=TraversalLimits()
    )
    assert [n.kind for n in inventory.nodes] == ["mail", "archive", "xlsx", "xlsx"]
    context = ParseContext(root.name, processing_policy=policy(root))
    blocks = parse_document(root, context=context)
    text = "\n".join(b.text for b in blocks)
    assert "USEFUL MSG BODY" in text and "MSG_INCLUDED_EVIDENCE" in text
    assert "MSG_EXCLUDED_CANARY" not in text


def test_manually_selected_heavy_file_can_be_parsed(tmp_path):
    root = office_container(tmp_path / "decision.docx", [("heavy.xlsx", spreadsheet(rows=501))])
    blocks = parse_document(
        root, context=ParseContext(root.name, processing_policy=policy(root, all_selected=True))
    )
    assert any(b.type == "table" for b in blocks)


@pytest.mark.parametrize('kind',['7z','rar'])
def test_archive_in_ole_package_is_recognized_before_msg_sniff(tmp_path,kind):
    from tests.attachment_fixtures import msg_bytes
    archive=archive_bytes(kind,[('heavy.xlsx',spreadsheet(rows=700,text='OLE_EXCLUDED_CANARY'))],tmp_path)
    root=office_container(tmp_path/'decision.docx',[('archive.bin',msg_bytes([],package=archive.read_bytes()))])
    inventory=inspect_attachment_inventory(root,root.name,limits=AssessmentLimits(),traversal=TraversalLimits())
    assert [n.kind for n in inventory.nodes]==['archive','xlsx']
    blocks=parse_document(root,context=ParseContext(root.name,processing_policy=policy(root)))
    assert not any('OLE_EXCLUDED_CANARY' in b.text for b in blocks)


@pytest.mark.parametrize('kind',['zip','7z','rar'])
def test_archive_mail_archive_excel_uses_one_policy_tree(tmp_path,kind):
    inner=archive_bytes(kind,[('heavy.xlsx',spreadsheet(rows=700,text='CHAIN_EXCLUDED_CANARY')),
                              ('light.xlsx',spreadsheet(rows=2,text='CHAIN_INCLUDED_EVIDENCE'))],tmp_path)
    inner_data=inner.read_bytes()
    outer=archive_bytes(kind,[('mail.eml',mail_bytes([('inner.'+kind,inner_data)]))],tmp_path)
    root=office_container(tmp_path/'decision.docx',[('outer.'+kind,outer.read_bytes())])
    inventory=inspect_attachment_inventory(root,root.name,limits=AssessmentLimits(),traversal=TraversalLimits())
    assert [n.kind for n in inventory.nodes]==['archive','mail','archive','xlsx','xlsx']
    blocks=parse_document(root,context=ParseContext(root.name,processing_policy=policy(root)))
    text='\n'.join(b.text for b in blocks)
    assert 'CHAIN_INCLUDED_EVIDENCE' in text and 'CHAIN_EXCLUDED_CANARY' not in text
