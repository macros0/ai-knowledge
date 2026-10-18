from email.message import EmailMessage

import pytest

from app.config import Settings
from app.services.processing_admission import prepare_upload_scope
from app.services.parser_supervisor import parse_document_supervised


@pytest.mark.parametrize('isolated', [False, True])
def test_env_depth_reaches_inventory_and_worker(tmp_path, monkeypatch, isolated):
    monkeypatch.setenv('ATTACHMENT_MAX_DEPTH', '0')
    settings = Settings(_env_file=None, parser_supervisor_enabled=isolated)
    assert settings.attachment_max_depth == 0
    mail = EmailMessage()
    mail.set_content('ROOT')
    mail.add_attachment(b'From: a@example.test\r\n\r\nCHILD', maintype='application',
                        subtype='octet-stream', filename='child.eml')
    path = tmp_path / 'root.eml'
    path.write_bytes(mail.as_bytes())
    inventory, _ = prepare_upload_scope(path, path.name, settings, attachments_dir=tmp_path / 'inspection')
    assert inventory.traversal_limits['max_depth'] == 0
    assert inventory.nodes[0].status == 'skipped_depth'
    if isolated:
        result = parse_document_supervised(path, path.name, attachments_dir=tmp_path / 'parse',
            timeout_seconds=20, max_memory_mb=settings.parser_max_memory_mb, attachment_max_depth=settings.attachment_max_depth)
        assert any(b.meta.get('extraction_status') == 'skipped_depth' for b in result.blocks)


@pytest.mark.parametrize('value', ['-1', '33', 'abc'])
def test_invalid_config_depth_is_rejected(monkeypatch, value):
    monkeypatch.setenv('ATTACHMENT_MAX_DEPTH', value)
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_captured_depth_survives_save_refresh_and_changed_env(tmp_path, monkeypatch):
    from app.db.models import Document
    from app.db.session import session_scope
    from app.services.processing_scope import create_review, save_review_selection, snapshot_from_review, refresh_review_inventory
    from docparser.attachment_policy import inventory_traversal_limits
    monkeypatch.setenv('ATTACHMENT_MAX_DEPTH', '2')
    settings = Settings(_env_file=None, parser_supervisor_enabled=False)
    path = tmp_path / 'root.eml'
    path.write_bytes(b'From: a@example.test\r\n\r\nROOT')
    inventory, _ = prepare_upload_scope(path, path.name, settings)
    doc_id = 'a'*16
    with session_scope() as session:
        session.add(Document(id=doc_id, filename=path.name, status='awaiting_attachments'))
        session.flush()
        row = create_review(session, doc_id, inventory, base_generation_id=None, actor='editor')
        monkeypatch.setenv('ATTACHMENT_MAX_DEPTH', '6')
        settings = Settings(_env_file=None, parser_supervisor_enabled=False)
        refreshed, _ = prepare_upload_scope(path, path.name, settings,
            traversal=inventory_traversal_limits(row.inventory_json))
        row = refresh_review_inventory(session, doc_id, row.id, row.revision, refreshed, actor='editor')
        row = save_review_selection(session, doc_id, row.id, row.revision, frozenset(), actor='editor')
        snapshot = snapshot_from_review(row)
        assert snapshot['traversal_limits']['max_depth'] == 2
        assert snapshot['inventory']['traversal_limits']['max_depth'] == 2


@pytest.mark.parametrize('maximum,depth,allowed', [(2, 3, False), (6, 6, True), (8, 7, True)])
def test_incremental_worker_uses_captured_absolute_boundary(tmp_path, maximum, depth, allowed):
    import hashlib
    import json
    from app.services.parser_supervisor import _worker
    class Output:
        def send_bytes(self, raw):
            self.result = json.loads(raw)
        def close(self):
            pass
    path = tmp_path / 'branch.eml'
    path.write_bytes(b'From: a@example.test\r\n\r\nBRANCH BODY')
    source_id = 'root' + '/0'*depth
    policy = {'schema_version': 1, 'source_file_hash': 'a'*64,
        'traversal_limits': {'max_depth': maximum},
        'decisions': [{'source_key': 'b'*64, 'effective_included': True}],
        'delta_branch': {'source_id': source_id, 'parent_source_id': source_id.rsplit('/', 1)[0],
            'ordinal': 0, 'kind': 'mail', 'source_key': 'b'*64,
            'locator_chain': [f'mime:{i}' for i in range(depth)],
            'content_fingerprint': hashlib.sha256(path.read_bytes()).hexdigest()}}
    output = Output()
    _worker(output, str(path), path.name, str(tmp_path / 'saved'), 1024**3,
            processing_policy=policy, attachment_max_depth=0)
    assert output.result['ok'] is allowed
    if allowed:
        assert any('BRANCH BODY' in b['text'] for b in output.result['blocks'])
