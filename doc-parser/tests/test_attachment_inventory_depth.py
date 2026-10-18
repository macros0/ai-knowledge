import zipfile
from dataclasses import asdict

import pytest
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_policy import AssessmentLimits, TraversalLimits

from tests.attachment_fixtures import (
    mail_bytes,
    msg_bytes,
    office_container,
    spreadsheet,
)


def inspect(path, maximum=0, **kwargs):
    return inspect_attachment_inventory(path, path.name, limits=AssessmentLimits(),
                                        traversal=TraversalLimits(max_depth=maximum), **kwargs)


@pytest.mark.parametrize('kind', ['docx', 'xlsx', 'eml', 'msg', 'zip', 'pdf'])
def test_depth_boundary_never_reads_or_extracts_child_payload(tmp_path, monkeypatch, kind):
    payload = spreadsheet()
    if kind == 'docx':
        path = office_container(tmp_path / 'root.docx', [('child.xlsx', payload)])
    elif kind == 'eml':
        path = tmp_path / 'root.eml'
        path.write_bytes(mail_bytes([('child.xlsx', payload)]))
    elif kind == 'msg':
        path = tmp_path / 'root.msg'
        path.write_bytes(msg_bytes([('child.xlsx', payload)]))
    elif kind == 'pdf':
        from pypdf import PdfWriter
        path = tmp_path / 'root.pdf'
        writer = PdfWriter()
        writer.add_blank_page(100, 100)
        writer.add_attachment('child.xlsx', payload)
        writer.write(path)
    else:
        path = tmp_path / ('root.' + kind)
        with zipfile.ZipFile(path, 'w') as archive:
            if kind == 'xlsx':
                archive.writestr('xl/workbook.xml', '<workbook/>')
            archive.writestr('xl/embeddings/child.xlsx' if kind == 'xlsx' else 'child.xlsx', payload)
    calls = []
    if kind in {'docx', 'xlsx'}:
        original = zipfile.ZipFile.open
        def guarded(archive, name, *args, **kwargs):
            member = name.filename if isinstance(name, zipfile.ZipInfo) else name
            if '/embeddings/' in member:
                calls.append(member)
            return original(archive, name, *args, **kwargs)
        monkeypatch.setattr(zipfile.ZipFile, 'open', guarded)
    elif kind == 'eml':
        import docparser.eml_parser as eml
        original = eml._attachment_payload
        def guarded(*args, **kwargs):
            calls.append('decode-child')
            return original(*args, **kwargs)
        monkeypatch.setattr(eml, '_attachment_payload', guarded)
    elif kind == 'msg':
        import docparser.msg_parser as msg
        original = msg._read_stream
        def guarded(ole, parts, *args, **kwargs):
            if parts[-1] == '__substg1.0_37010102':
                calls.append('read-child-stream')
            return original(ole, parts, *args, **kwargs)
        monkeypatch.setattr(msg, '_read_stream', guarded)
    elif kind == 'pdf':
        from dataclasses import replace

        from docparser.pypdf_pdfium_provider import PypdfPdfiumDocument
        original = PypdfPdfiumDocument.iter_attachments
        def guarded(document):
            for ref in original(document):
                def read(ref=ref):
                    calls.append('read-child')
                    return ref.read_bytes()
                yield replace(ref, read_bytes=read)
        monkeypatch.setattr(PypdfPdfiumDocument, 'iter_attachments', guarded)
    else:
        from docparser.archive_zip import ZipArchiveReader
        original = ZipArchiveReader.extract_selected
        def guarded(*args, **kwargs):
            calls.append('extract-child')
            return original(*args, **kwargs)
        monkeypatch.setattr(ZipArchiveReader, 'extract_selected', guarded)
    inventory = inspect(path, attachments_dir=tmp_path / 'saved')
    assert not calls, 'Do not materialize a payload past the hard recursion boundary'
    assert inventory.nodes[0].status == 'skipped_depth'
    assert inventory.nodes[0].saved_path is None
    assert inventory.nodes[0].content_fingerprint is None


def test_inventory_freezes_effective_depth_and_branch_cannot_restart_at_zero(tmp_path):
    path = tmp_path / 'root.eml'
    path.write_bytes(mail_bytes([('child.xlsx', spreadsheet())]))
    inventory = inspect(path, maximum=1)
    assert inventory.to_dict().get('traversal_limits') == asdict(TraversalLimits(max_depth=1))
    branch = {'source_file_hash': inventory.source_file_hash, 'locator': 'mime:0',
              'parent_key': 'root', 'parent_chain': [], 'depth': 2}
    result = inspect(path, maximum=1, branch=branch)
    assert len(result.nodes) == 1
    assert result.nodes[0].status == 'skipped_depth'
    assert result.scan_bytes == 0
