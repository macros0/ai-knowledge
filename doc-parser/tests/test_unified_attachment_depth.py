from dataclasses import asdict

import pytest
from docparser import ParseContext, parse_document
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_policy import (
    AssessmentLimits,
    ParserProcessingPolicy,
    TraversalLimits,
)

from tests.attachment_fixtures import mail_bytes, office_container, spreadsheet


@pytest.mark.parametrize('maximum', [0, 2, 6])
def test_same_boundary_in_inventory_plain_and_scoped_parser(tmp_path, maximum):
    path = tmp_path / 'root.eml'
    payload, name = spreadsheet(), 'leaf.xlsx'
    for level in range(7, 0, -1):
        payload, name = mail_bytes([(name, payload)], body=f'LEVEL {level}'), f'level-{level}.eml'
    path.write_bytes(payload)
    inventory = inspect_attachment_inventory(path, path.name, limits=AssessmentLimits(),
        traversal=TraversalLimits(max_depth=maximum))
    snapshot = {'schema_version': 1, 'source_file_hash': inventory.source_file_hash,
        'traversal_limits': asdict(TraversalLimits(max_depth=maximum)),
        'inventory': inventory.to_dict(),
        'decisions': [{'source_key': n.source_key, 'effective_included': True} for n in inventory.nodes]}
    # Include even the boundary marker deliberately: depth must win over selection.
    for policy in (None, ParserProcessingPolicy(snapshot)):
        context = ParseContext(path.name, attachment_max_depth=maximum, processing_policy=policy)
        blocks = parse_document(path, context=context, attachments_dir=tmp_path / str(bool(policy)))
        parsed = [b for b in blocks if b.type == 'attachment' and b.meta.get('extraction_status') == 'parsed']
        skipped = [b for b in blocks if b.meta.get('extraction_status') == 'skipped_depth']
        assert len(parsed) == maximum
        assert len(skipped) == 1
        assert len(inventory.nodes) == maximum + 1
        assert inventory.nodes[-1].status == 'skipped_depth'


def test_default_plain_parser_no_longer_stops_at_third_level(tmp_path):
    child = office_container(tmp_path / 'child.docx', [('leaf.xlsx', spreadsheet())])
    parent = office_container(tmp_path / 'parent.docx', [('child.docx', child.read_bytes())])
    path = office_container(tmp_path / 'root.docx', [('parent.docx', parent.read_bytes())])
    blocks = parse_document(path)
    assert any(b.type == 'table' for b in blocks)
    assert not any(b.meta.get('extraction_status') == 'skipped_depth' for b in blocks)


def test_frozen_scope_wins_over_current_setting():
    policy = ParserProcessingPolicy({'schema_version': 1, 'source_file_hash': 'a'*64,
        'traversal_limits': {'max_depth': 2}, 'decisions': []})
    assert ParseContext('root.docx', processing_policy=policy, attachment_max_depth=6).attachment_max_depth == 2


def test_plain_checkpoint_identity_changes_with_depth():
    assert ParseContext('root.docx', attachment_max_depth=2).parser_version != ParseContext('root.docx', attachment_max_depth=6).parser_version


@pytest.mark.parametrize('kind', ['zip', '7z', 'rar'])
def test_archive_boundary_precedes_extraction(tmp_path, monkeypatch, kind):
    from docparser.archive_parser import parse_archive
    from docparser.archive_reader import ArchiveEntry
    from docparser.attachment_policy import make_source_key
    from docparser.embedded import AttachmentBudget

    class Reader:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def entries(self):
            yield ArchiveEntry('member', 'folder/subfolder/child.eml', 10)
        def extract_selected(self, *args):
            raise AssertionError('Must reject depth before extracting archive payload')

    monkeypatch.setattr('docparser.archive_parser.open_archive', lambda *a, **k: Reader())
    key = make_source_key('a'*64, ('archive:member',))
    policy = ParserProcessingPolicy({'schema_version': 1, 'source_file_hash': 'a'*64,
        'traversal_limits': {'max_depth': 0},
        'decisions': [{'source_key': key, 'effective_included': True}]})
    context = ParseContext('root.'+kind, processing_policy=policy)
    blocks = parse_archive(tmp_path / ('root.'+kind), kind, attachments_dir=tmp_path / 'saved',
        depth=0, budget=AttachmentBudget(), context=context, source_id='root')
    assert blocks[0].meta['extraction_status'] == 'skipped_depth'
