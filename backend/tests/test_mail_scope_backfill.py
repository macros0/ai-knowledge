"""Metadata backfill is read-only by default and changes exactly two payload fields."""
from copy import deepcopy
from types import SimpleNamespace
import json
import uuid

import pytest
from qdrant_client.http import models as qm
from app.db.models import Document, DocumentChunk, DocumentSource
from app.db.session import session_scope
from app.services.generation_store import begin_generation
from tests.test_mail_scope import scope_store as scope_store


def _setup(store):
    with session_scope() as session:
        for doc_id, kind in [('document', 'document'), ('mail', 'mail')]:
            session.add(Document(id=doc_id, filename=doc_id))
            session.flush()
            session.add(DocumentSource(doc_id=doc_id, source_id='root', kind=kind, display_name=doc_id))
            session.add(DocumentChunk(doc_id=doc_id, chunk_index=0, source_id='root', content='topic'))
    ids = {}
    for doc_id in ('document', 'mail'):
        ids[doc_id] = next(iter(store.index_chunks(doc_id, 'file', ['topic'], [], [[1, 0]], source_ids=['root'])))
    return ids


def _cli(monkeypatch, store, args, capsys):
    from scripts import backfill_mail_scope as script
    settings = SimpleNamespace(knowledge_profile='synthetic', database_url='sqlite:///fixture',
                               qdrant_collection=store.collection, search_index_chunks_enabled=True)
    monkeypatch.setattr(script, 'get_settings', lambda: settings)
    monkeypatch.setattr(script, 'VectorStore', lambda: store)
    monkeypatch.setattr(store, 'ensure_mail_scope_indexes', lambda: None if '--apply' in args else pytest.fail('dry-run attempted index mutation'))
    code = script.main(args)
    captured = capsys.readouterr()
    return code, json.loads(captured.out), captured.err


@pytest.mark.parametrize('args', [[], ['--dry-run'], ['--dry-run', '--batch-size', '1']])
def test_default_and_dry_run_have_zero_mutations(monkeypatch, scope_store, capsys, args):
    _setup(scope_store)
    def forbidden(*a, **kw):
        pytest.fail('dry-run attempted mutation')
    for method in ('create_collection', 'create_payload_index', 'set_payload', 'upsert',
                   'delete_payload', 'overwrite_payload', 'delete'):
        monkeypatch.setattr(scope_store.client, method, forbidden)
    code, report, _ = _cli(monkeypatch, scope_store, args, capsys)
    assert code == 0
    assert report['scanned'] == 2 and report['would_change'] == 2
    assert report['mail'] == report['document'] == 1
    assert report['updated'] == 0 and report['missing_active_points'] == 0


def test_apply_idempotent_and_preserves_ids_vectors_and_every_other_field(monkeypatch, scope_store, capsys):
    _setup(scope_store)
    before, _ = scope_store.client.scroll(scope_store.collection, limit=100, with_vectors=True)
    before = deepcopy(before)
    assert _cli(monkeypatch, scope_store, ['--apply', '--batch-size', '1'], capsys)[0] == 0
    after, _ = scope_store.client.scroll(scope_store.collection, limit=100, with_vectors=True)
    assert {r.id for r in before} == {r.id for r in after}
    for original, updated in zip(before, after):
        assert original.vector == updated.vector
        assert {k:v for k,v in original.payload.items() if not k.startswith('mail_scope')} == {
            k:v for k,v in updated.payload.items() if not k.startswith('mail_scope')}
    code, report, _ = _cli(monkeypatch, scope_store, ['--apply'], capsys)
    assert code == 0 and report['would_change'] == report['updated'] == 0
    assert _cli(monkeypatch, scope_store, [], capsys)[1]['would_change'] == 0


def test_doc_filter_does_not_write_other_contour_or_document(monkeypatch, scope_store, capsys):
    ids = _setup(scope_store)
    code, report, _ = _cli(monkeypatch, scope_store, ['--apply', '--doc-id', 'document'], capsys)
    assert code == 0 and report['scanned'] == 1 and report['updated'] == 1
    other = scope_store.client.retrieve(scope_store.collection, [ids['mail']])[0]
    assert other.payload['mail_scope'] == 'unknown'
    assert report['target']['database_fingerprint'] and 'database_url' not in report['target']


def test_orphan_missing_identity_nonactive_and_missing_active_points(monkeypatch, scope_store, capsys):
    ids = _setup(scope_store)
    scope_store.client.delete(scope_store.collection, points_selector=[ids['mail']])
    with session_scope() as session:
        candidate = begin_generation(session, 'document').id
    scope_store.index_chunks('document', 'a', ['candidate'], [], [[1, 0]], generation_id=candidate)
    scope_store.index_chunks('orphan', 'a', ['orphan'], [], [[1, 0]])
    scope_store.index_chunks('document', 'a', ['missing canonical'], [], [[1, 0]], chunk_indices=[8])
    scope_store.client.upsert(scope_store.collection, points=[qm.PointStruct(
        id=str(uuid.uuid4()), vector={'': [1,0]}, payload={'doc_id': 'document', 'point_type': 'chunk', 'chunk_index': True})])
    code, report, _ = _cli(monkeypatch, scope_store, [], capsys)
    assert code == 0
    assert report['skipped_nonactive'] == 1
    assert report['orphan'] == 1 and report['missing_canonical'] >= 1
    assert report['invalid_identity'] >= 1
    assert report['missing_active_points'] == 1


def test_payload_reverse_mismatch_is_detected(monkeypatch, scope_store, capsys):
    ids = _setup(scope_store)
    scope_store.client.set_payload(scope_store.collection, payload={'mail_scope':'mail','mail_scope_version':1}, points=[ids['document']])
    code, report, _ = _cli(monkeypatch, scope_store, [], capsys)
    assert code == 0 and report['would_change'] == 2
    assert report['document'] == 1


def test_partial_failure_reports_error_then_retry_finishes(monkeypatch, scope_store, capsys):
    _setup(scope_store)
    original = scope_store.patch_mail_scopes
    calls = []
    def patch(mapping):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError('readback failed')
        return original(mapping)
    monkeypatch.setattr(scope_store, 'patch_mail_scopes', patch)
    code, report, _ = _cli(monkeypatch, scope_store, ['--apply', '--batch-size', '1'], capsys)
    assert code == 1 and report['failed_batches'] == 1 and report['updated'] == 1
    monkeypatch.setattr(scope_store, 'patch_mail_scopes', original)
    assert _cli(monkeypatch, scope_store, ['--apply'], capsys)[0] == 0
    assert _cli(monkeypatch, scope_store, [], capsys)[1]['would_change'] == 0


@pytest.mark.parametrize('args', [['--apply','--dry-run'], ['--batch-size','0'], ['--batch-size','1001'], ['--batch-size','oops']])
def test_invalid_cli_returns_2_before_io(monkeypatch, args):
    from scripts import backfill_mail_scope as script
    def forbidden():
        pytest.fail('invalid CLI touched settings')
    monkeypatch.setattr(script, 'get_settings', forbidden)
    assert script.main(args) == 2


def test_missing_collection_is_not_created(monkeypatch, scope_store, capsys):
    scope_store.client.delete_collection(scope_store.collection)
    def forbidden(*a, **kw):
        pytest.fail('missing collection created')
    monkeypatch.setattr(scope_store.client, 'create_collection', forbidden)
    code, report, stderr = _cli(monkeypatch, scope_store, [], capsys)
    assert code == 1 and report['failed_batches'] > 0
    assert 'fixture' not in stderr


def test_deleted_and_broken_tree_are_classified_without_changing_deleted(monkeypatch, scope_store, capsys):
    from datetime import datetime, timezone
    ids = _setup(scope_store)
    with session_scope() as session:
        session.get(Document, 'document').deleted_at = datetime.now(timezone.utc)
        source = session.query(DocumentSource).filter_by(doc_id='mail', source_id='root').one()
        source.parent_source_id = 'missing'
    scope_store.set_document_deleted('document', True)
    code, report, _ = _cli(monkeypatch, scope_store, ['--apply'], capsys)
    assert code == 0 and report['broken_tree'] == 1
    rows = scope_store.client.retrieve(scope_store.collection, list(ids.values()))
    by_doc = {row.payload['doc_id']: row.payload for row in rows}
    assert by_doc['document']['deleted'] is True
    assert by_doc['document']['mail_scope'] == 'document'
    assert by_doc['mail']['mail_scope'] == 'unknown'


def test_index_failure_is_operational_error_without_patching(monkeypatch, scope_store, capsys):
    from scripts import backfill_mail_scope as script
    _setup(scope_store)
    monkeypatch.setattr(script, 'get_settings', lambda: SimpleNamespace(
        knowledge_profile='fixture', database_url='sqlite:///private', search_index_chunks_enabled=True))
    monkeypatch.setattr(script, 'VectorStore', lambda: scope_store)
    def failure():
        raise RuntimeError('SECRET_PROVIDER_DETAIL')
    monkeypatch.setattr(scope_store, 'ensure_mail_scope_indexes', failure)
    monkeypatch.setattr(scope_store, 'patch_mail_scopes', lambda *a: pytest.fail('patch after failed index check'))
    assert script.main(['--apply']) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)['failed_batches'] == 1
    assert 'SECRET_PROVIDER_DETAIL' not in captured.out + captured.err


def test_confirmed_updates_are_counted_by_point_type(monkeypatch, scope_store, capsys):
    _setup(scope_store)
    code, report, _ = _cli(monkeypatch, scope_store, ['--apply'], capsys)
    assert code == 0
    assert report['point_types']['chunk']['updated'] == 2


@pytest.mark.parametrize('status,expected_calls', [(400, 1), (429, 3), (503, 3)])
def test_transport_retries_are_bounded_and_validation_is_not_retried(monkeypatch, status, expected_calls):
    from scripts import backfill_mail_scope as script
    calls = []
    def fail(**kwargs):
        calls.append(1)
        error = RuntimeError('synthetic')
        error.status_code = status
        raise error
    monkeypatch.setattr(script.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError):
        script._call(fail)
    assert len(calls) == expected_calls
