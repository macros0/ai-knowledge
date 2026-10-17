"""Private read-only fingerprints distinguish empty inputs from published outputs."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine,text


def api():
    path=Path(__file__).parents[1]/'test_scripts/table_acceptance_storage.py'
    assert path.exists(), 'private resource snapshot adapter is missing'
    spec=importlib.util.spec_from_file_location('r2_storage',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def engine(tmp_path):
    result=create_engine('sqlite:///'+str(tmp_path/'private.db'))
    with result.begin() as connection:
        connection.execute(text('CREATE TABLE document_generation_states (doc_id TEXT PRIMARY KEY, active_generation_id TEXT)'))
        connection.execute(text('CREATE TABLE cells (id INTEGER PRIMARY KEY, value TEXT)'))
    return result


def test_empty_active_generation_is_known_only_for_resource_inputs(tmp_path):
    m=api();db=engine(tmp_path)
    before=m.database_state(db,private_root=tmp_path,require_active=False)
    assert before['active_generation_count']==0 and len(before['active_generations_sha256'])==64
    assert m.database_state(db,private_root=tmp_path,require_active=False)==before
    with pytest.raises(m.StorageUnknown):m.database_state(db,private_root=tmp_path,require_active=True)
    with db.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM document_generation_states')).scalar()==0
    db.dispose()


def test_output_hash_changes_without_rewriting_frozen_input_snapshot(tmp_path):
    m=api();db=engine(tmp_path)
    before=m.database_state(db,private_root=tmp_path,require_active=False)
    with db.begin() as connection:
        connection.execute(text('INSERT INTO document_generation_states VALUES (:doc,:generation)'),{'doc':'a'*16,'generation':'b'*32})
    after=m.database_state(db,private_root=tmp_path,require_active=True)
    assert after['active_generation_count']==1
    assert after['database_snapshot_sha256']!=before['database_snapshot_sha256']
    assert after['active_generations_sha256']!=before['active_generations_sha256']
    assert before['active_generation_count']==0
    db.dispose()


def test_sqlite_outside_private_root_is_rejected_before_reading(tmp_path):
    m=api();db=engine(tmp_path)
    with pytest.raises(m.StorageUnknown):m.database_state(db,private_root=tmp_path/'another-root',require_active=False)
    db.dispose()


def test_primary_postgres_identity_is_rejected_before_connecting():
    from sqlalchemy.engine import make_url
    calls=[]
    fake=SimpleNamespace(url=make_url('postgresql://reader@127.0.0.1:5432/production'),connect=lambda:calls.append('network'))
    m=api()
    with pytest.raises(m.StorageUnknown):m.database_state(fake,require_active=False)
    assert not calls


def test_foreign_qdrant_collection_is_rejected_before_network():
    calls=[];m=api()
    fake=SimpleNamespace(get_collection=lambda name:calls.append('network'))
    with pytest.raises(m.StorageUnknown):m.index_state(fake,'okf_knowledge_base')
    assert not calls


def test_empty_private_index_has_known_hash_and_zero_points():
    m=api()
    config=SimpleNamespace(model_dump=lambda **kwargs:{'vectors':{'size':8}})
    client=SimpleNamespace(get_collection=lambda name:SimpleNamespace(config=config),
                           scroll=lambda **kwargs:([],None))
    result=m.index_state(client,'r2_evaluation_synthetic')
    assert result['point_count']==0 and len(result['index_snapshot_sha256'])==64


def test_cache_manifest_changes_on_content_even_if_name_is_unchanged(tmp_path):
    m=api();folder=tmp_path/'cache';folder.mkdir();file=folder/'one.json';file.write_text('{"mode":"per_row"}')
    before=m.files_digest(folder)
    file.write_text('{"mode":"whole"}')
    assert m.files_digest(folder)!=before


def test_absent_cache_is_unknown_not_certified_empty(tmp_path):
    m=api()
    with pytest.raises(m.StorageUnknown):m.files_digest(tmp_path/'absent')


def test_snapshots_are_explicit_and_hash_all_tables_and_typed_values(tmp_path):
    m=api();db=engine(tmp_path);cache=tmp_path/'cache';cache.mkdir()
    config=SimpleNamespace(model_dump=lambda **kwargs:{'vectors':{'size':8}})
    client=SimpleNamespace(get_collection=lambda name:SimpleNamespace(config=config),scroll=lambda **kwargs:([],None))
    before=m.capture_resource_snapshot(db,client,'r2_evaluation_synthetic',cache,
                                      table_structure_enabled=False,kind='resource-inputs',private_root=tmp_path)
    assert before['snapshot_kind']=='resource-inputs' and before['active_generation_count']==0
    with db.begin() as connection:
        connection.execute(text('CREATE TABLE typed (id INTEGER PRIMARY KEY, zero INTEGER, flag BOOLEAN, derived INTEGER GENERATED ALWAYS AS (zero + 1) STORED)'))
        connection.execute(text('INSERT INTO typed (id,zero,flag) VALUES (1,0,false)'))
        connection.execute(text('INSERT INTO document_generation_states VALUES (:doc,:generation)'),{'doc':'a'*16,'generation':'b'*32})
    after=m.capture_resource_snapshot(db,client,'r2_evaluation_synthetic',cache,
                                     table_structure_enabled=False,kind='resource-outputs',private_root=tmp_path)
    assert after['snapshot_kind']=='resource-outputs' and after['active_generation_count']==1
    assert before['schema_sha256']!=after['schema_sha256']
    assert before['database_snapshot_sha256']!=after['database_snapshot_sha256']
    with pytest.raises(m.StorageUnknown):
        m.capture_resource_snapshot(db,client,'r2_evaluation_synthetic',cache,
                                    table_structure_enabled=False,kind='resource-inputs',private_root=tmp_path)
    db.dispose()
