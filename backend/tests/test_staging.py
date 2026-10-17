"""Юнит-тесты StagingStore: инкрементальная запись чанков, manifest в БД, resume.

Manifest хранится в document_staging (БД), сырые файлы чанков — в FS под
staging_root. Проверки читают manifest через store.load() (а не manifest.json).
"""
from app.models.schemas import Concept
from app.services.staging import StagingStore


def _concept(title: str, cid: str) -> Concept:
    return Concept(id=cid, title=title, type="concept", tags=[], content=f"тело {title}", relations=[])


class TestStagingAppend:
    def test_replace_chunk_reuses_own_slugs_and_preserves_other_chunk(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2)
        store.append_chunk(0, [_concept("Bridge", "a")], degradation=[{"event": "llm_salvage"}])
        store.append_chunk(1, [_concept("Bridge", "b")], provenance={"model_id": "original"})
        untouched = store.load()["chunks_data"]["1"]
        store.append_chunk(0, [_concept("Bridge", "fixed")])
        assert store.slugs() == ["bridge", "bridge-1"]
        assert store.load()["used_slugs"] == ["bridge-1", "bridge"]
        assert store.load()["chunks_data"]["1"] == untouched
        assert "degradation" not in store.load()["chunks_data"]["0"]

    def test_append_roundtrip(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2, global_tags=["proxmox"])
        slugs = store.append_chunk(0, [_concept("Auth Flow", "a"), _concept("Auth Flow", "b")])
        assert slugs == ["auth-flow", "auth-flow-1"]
        assert store.has_chunk(0)
        assert not store.has_chunk(1)
        assert store.processed_chunks == [0]

        concepts = store.concepts()
        assert [c.title for c in concepts] == ["Auth Flow", "Auth Flow"]

        manifest = store.load()
        assert manifest["total_chunks"] == 2
        assert manifest["global_tags"] == ["proxmox"]
        assert manifest["processed_chunks"] == [0]
        assert manifest["chunks_data"]["0"]["concepts_count"] == 2

    def test_append_multiple_chunks_ordered(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(3)
        store.append_chunk(1, [_concept("Middle", "m")])
        store.append_chunk(0, [_concept("First", "f")])
        store.append_chunk(2, [_concept("Last", "l")])
        assert store.processed_chunks == [0, 1, 2]
        assert [c.title for c in store.concepts()] == ["First", "Middle", "Last"]
        assert store.slugs() == ["first", "middle", "last"]

    def test_slug_dedup_across_chunks(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2)
        store.append_chunk(0, [_concept("Bridge", "b")])
        store.append_chunk(1, [_concept("Bridge", "b2"), _concept("Bridge", "b3")])
        assert store.slugs() == ["bridge", "bridge-1", "bridge-2"]


class TestStagingResume:
    def test_restart_skips_processed_chunks(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2)
        store.append_chunk(0, [_concept("Done", "d")])

        resumed = StagingStore("doc1", staging_root=tmp_path / "staging")
        assert resumed.exists()
        assert resumed.has_chunk(0)
        assert not resumed.has_chunk(1)
        resumed.append_chunk(1, [_concept("Next", "n")])
        assert [c.title for c in resumed.concepts()] == ["Done", "Next"]
        assert resumed.slugs() == ["done", "next"]

    def test_remove(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(1)
        store.append_chunk(0, [_concept("X", "x")])
        store.remove()
        assert not store.dir.exists()
        assert not store.exists()


class TestStagingChunkText:
    def test_save_and_read_chunk_text(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2)
        store.save_chunk_text(0, "первый чанк")
        store.save_chunk_text(1, "второй чанк")
        assert (store.dir / "chunk_00.md").read_text(encoding="utf-8") == "первый чанк"
        assert (store.dir / "chunk_01.md").read_text(encoding="utf-8") == "второй чанк"
        assert store.has_chunk(0) is False, "текст чанка не должен влиять на processed_chunks"

    def test_save_chunk_text_idempotent(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(1)
        store.save_chunk_text(0, "версия 1")
        store.save_chunk_text(0, "версия 2")
        assert (store.dir / "chunk_00.md").read_text(encoding="utf-8") == "версия 2"


class TestStagingEmpty:
    def test_no_manifest(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        assert not store.exists()
        assert store.concepts() == []
        assert store.processed_chunks == []
        assert not store.has_chunk(0)



def test_prepare_chunk_metadata_checkpoints_bounded_batches_and_keeps_resume(tmp_path):
    import pytest
    store = StagingStore('doc1', staging_root=tmp_path / 'staging')
    store.create(600, generation_id='generation')
    store.set_chunk_source(0, 'root')
    store.append_chunk(0, [_concept('Done', 'old')], provenance={'model_id': 'original'})
    before = store.load()['chunks_data']['0']
    def entries():
        for index in range(300):
            yield {'index': index, 'source_id': 'root/1', 'table_refs': [{'row_number': index + 1}], 'table_title': 'Table'}
        raise RuntimeError('interrupted preparation')
    with pytest.raises(RuntimeError, match='interrupted preparation'):
        store.set_chunks_metadata(entries())
    resumed = StagingStore('doc1', staging_root=store.dir)
    state = resumed.load()
    assert len(state['chunks_data']) == 256
    assert state['chunks_data']['255']['table_refs'] == [{'row_number': 256}]
    assert state['chunks_data']['0']['provenance'] == before['provenance']
    assert resumed.has_chunk(0)
    resumed.set_chunks_metadata([{'index': i, 'source_id': 'root/1'} for i in range(256, 600)])
    assert len(resumed.load()['chunks_data']) == 600
    assert resumed.load()['generation_id'] == 'generation'


def test_chunk_checkpoint_does_not_load_unrelated_table_metadata(tmp_path, monkeypatch):
    store = StagingStore('doc1', staging_root=tmp_path / 'staging')
    store.create(2, global_tags=['kept'])
    store.set_chunk_source(0, 'root/1')
    store.set_chunk_table_refs(0, [{'row_number': 7}], title='Table')
    store.set_chunk_source(1, 'root/2')
    store.set_chunk_table_refs(1, [{'large': 'x' * 100000}], title='Other')
    unrelated = store.load()['chunks_data']['1']
    original_load = store.load
    def forbid_full_manifest():
        raise AssertionError('hot checkpoint path loaded the entire table-ref map')
    monkeypatch.setattr(store, 'load', forbid_full_manifest)
    store.append_chunk(0, [_concept('Entry', 'new')], provenance={'model_id': 'test'})
    assert store.has_chunk(0)
    assert not store.has_chunk(1)
    state = original_load()
    assert state['chunks_data']['1'] == unrelated
    assert state['chunks_data']['0']['table_refs'] == [{'row_number': 7}]
    assert state['chunks_data']['0']['source_id'] == 'root/1'
    assert state['chunks_data']['0']['global_tags'] == ['kept']



def test_large_table_refs_are_portable_hashed_files_not_repeated_sql_payload(tmp_path):
    import json
    from app.db.models import DocumentStaging
    from app.db.session import session_scope
    store = StagingStore('doc1', staging_root=tmp_path / 'staging')
    store.create(2)
    refs = [{'row_number': 7, 'columns': list(range(1000)), 'description': 'x' * 100000}]
    store.set_chunk_table_refs(0, refs, title='Table')
    with session_scope() as session:
        raw = session.get(DocumentStaging, 'doc1').chunks_data
    assert len(json.dumps(raw)) < 2000
    assert 'table_refs' not in raw['0']
    assert (store.dir / raw['0']['table_refs_file']).is_file()
    assert store.load()['chunks_data']['0']['table_refs'] == refs
    store.append_chunk(0, [_concept('Entry', 'new')])
    assert store.load()['chunks_data']['0']['table_refs'] == refs


def test_failed_reference_update_keeps_old_hashed_payload(tmp_path):
    import pytest
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app.db.models import DocumentStaging
    store = StagingStore('doc1', staging_root=tmp_path / 'staging')
    store.create(1)
    store.set_chunk_table_refs(0, [{'row_number': 7}], title='Table')
    def fail_commit(session, *_args):
        if any(isinstance(row, DocumentStaging) for row in session.dirty):
            raise OSError('database write interrupted')
    event.listen(Session, 'before_flush', fail_commit)
    try:
        with pytest.raises(OSError, match='database write interrupted'):
            store.set_chunk_table_refs(0, [{'row_number': 8}], title='Updated')
    finally:
        event.remove(Session, 'before_flush', fail_commit)
    assert store.load()['chunks_data']['0']['table_refs'] == [{'row_number': 7}]
    assert store.load()['chunks_data']['0']['table_title'] == 'Table'


def test_tampered_reference_file_cannot_silently_change_checkpoint(tmp_path):
    import pytest
    from app.db.models import DocumentStaging
    from app.db.session import session_scope
    store = StagingStore('doc1', staging_root=tmp_path / 'staging')
    store.create(1)
    store.set_chunk_table_refs(0, [{'row_number': 7}], title='Table')
    with session_scope() as session:
        info = session.get(DocumentStaging, 'doc1').chunks_data['0']
    assert 'table_refs_file' in info
    (store.dir / info['table_refs_file']).write_text('[{"row_number": 8}]', encoding='utf8')
    with pytest.raises(ValueError, match='checksum'):
        store.load()



def test_legacy_inline_refs_remain_readable_and_missing_new_refs_fail(tmp_path):
    import pytest
    from app.db.models import DocumentStaging
    from app.db.session import session_scope
    store = StagingStore('doc1', staging_root=tmp_path / 'staging')
    store.create(1)
    with session_scope() as session:
        session.get(DocumentStaging, 'doc1').chunks_data = {'0': {'source_id': 'root', 'table_refs': [{'row_number': 7}], 'table_title': 'Old'}}
    assert store.load()['chunks_data']['0']['table_refs'] == [{'row_number': 7}]
    store.append_chunk(0, [_concept('Entry', 'new')])
    with session_scope() as session:
        filename = session.get(DocumentStaging, 'doc1').chunks_data['0']['table_refs_file']
    assert 'table_refs_file' not in store.load()['chunks_data']['0']
    (store.dir / filename).unlink()
    with pytest.raises(FileNotFoundError):
        store.load()


def test_staging_reference_filename_cannot_escape_owned_directory(tmp_path):
    import pytest
    from app.db.models import DocumentStaging
    from app.db.session import session_scope
    store = StagingStore('doc1', staging_root=tmp_path / 'staging')
    store.create(1)
    with session_scope() as session:
        session.get(DocumentStaging, 'doc1').chunks_data = {'0': {'table_refs_file': '../outside.json'}}
    with pytest.raises(ValueError, match='filename'):
        store.load()
