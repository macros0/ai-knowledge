from copy import deepcopy
import pytest

from tests.test_processing_scope_change import example


def test_relations_suspend_only_exact_internal_edges_and_restore_unchanged_targets():
    from app.services.processing_scope_relations import reconcile_scope_relations
    base, _ = example()
    base.rows["concepts"][0].update(content="owner", relations=["concept-1", "outside", "Concept 1"])
    base.rows["concepts"][1].update(content="target", relations=[])
    active = deepcopy([base.rows["concepts"][0], base.rows["concepts"][2]])
    pending = reconcile_scope_relations(base.rows["concepts"], active, [])
    assert active[0]["relations"] == ["outside", "Concept 1"]
    assert len(pending) == 1 and pending[0]["target_slug"] == "concept-1"
    restored = [*deepcopy(active), deepcopy(base.rows["concepts"][1])]
    assert reconcile_scope_relations(active, restored, pending) == []
    assert restored[0]["relations"] == ["concept-1", "outside", "Concept 1"]
    changed = deepcopy(base.rows["concepts"][1]); changed["content"] = "changed target"
    altered = [*deepcopy(active), changed]
    assert reconcile_scope_relations(active, altered, pending) == pending
    assert "concept-1" not in altered[0]["relations"]


def test_full_regeneration_cannot_discard_inactive_results(source_environment):
    from app.services.processing_scope_cache import assert_full_regeneration_cache_safe, SourceResultCache
    from app.services.generation_files import generation_paths
    from app.services.errors import ConflictError
    from app.db.session import session_scope
    env = source_environment
    folder = generation_paths(env["settings"], env["doc_id"], env["base_id"]).bundle / "source-cache"
    cache = SourceResultCache(folder, env["doc_id"])
    cache.save_stage(env["nodes"][0], "parse", {"chunks": [{"chunk_index": 0}]})
    selected = deepcopy(env["snapshot"])
    selected["decisions"][0]["effective_included"] = False
    with session_scope() as session:
        with pytest.raises(ConflictError, match="[Cc]ache|кэш"):
            assert_full_regeneration_cache_safe(session, env["settings"], env["doc_id"], selected)
        assert_full_regeneration_cache_safe(session, env["settings"], env["doc_id"], env["snapshot"])


def test_full_regeneration_cannot_first_exclude_existing_results(source_environment):
    from app.services.processing_scope_cache import assert_full_regeneration_cache_safe
    from app.services.errors import ConflictError
    from app.db.session import session_scope
    env=source_environment
    selected=deepcopy(env["snapshot"])
    selected["decisions"][0]["effective_included"]=False
    with session_scope() as session:
        with pytest.raises(ConflictError):
            assert_full_regeneration_cache_safe(session,env["settings"],env["doc_id"],selected)


def test_incremental_changes_use_explicit_scope_endpoint(monkeypatch):
    import app.api.document_processing_scope as api
    monkeypatch.setattr(api,"_apply_change",lambda doc,body,user,modes:modes)
    assert api.apply_scope_changes("document",None,None)=={"reuse_only","incremental_generate"}


def test_preflight_hash_does_not_materialize_heavy_cells_but_detects_changes(source_environment,monkeypatch):
    import json
    from app.db.models import DocumentTable,DocumentTableRow
    from app.db.session import session_scope,get_engine
    from app.services.processing_scope_change import scope_sql_digest
    env=source_environment
    with session_scope() as session:
        session.add(DocumentTable(doc_id=env["doc_id"],generation_id=env["base_id"],table_id="a"*32,
            source_id="root/0",descriptor={},quality={}))
        session.flush()
        session.add(DocumentTableRow(doc_id=env["doc_id"],generation_id=env["base_id"],table_id="a"*32,
            row_number=1,cells={"1":{"raw_value":"HEAVY_CELL_SENTINEL"}}))
    decoded=[]
    def read_json(raw):
        if "HEAVY_CELL_SENTINEL" in raw:decoded.append(True)
        return json.loads(raw)
    monkeypatch.setattr(get_engine().dialect,"_json_deserializer",read_json)
    with session_scope() as session:
        first=scope_sql_digest(session,env["doc_id"])
    assert not decoded,"Preflight must hash serialized cells without building their nested objects"
    from sqlalchemy import update
    with session_scope() as session:
        session.execute(update(DocumentTableRow).values(cells={"1":{"raw_value":"changed"}}))
        assert scope_sql_digest(session,env["doc_id"])!=first


def test_scope_metadata_reconcile_skips_unchanged_points(source_environment,monkeypatch):
    from contextlib import nullcontext
    from unittest.mock import Mock
    env=source_environment
    store=env["store"]
    write=Mock(wraps=store.client.set_payload)
    monkeypatch.setattr(store.client,"set_payload",write)
    values={"global_tags":[],"dev_tags":[],"source_locale":None,"concept_tags":{"concept-0":[],"concept-1":[]}}
    store.reconcile_generation_metadata(env["doc_id"],env["base_id"],**values,batch_guard=nullcontext)
    write.assert_not_called()
    store.reconcile_generation_metadata(env["doc_id"],env["base_id"],**{**values,"source_locale":"en"},batch_guard=nullcontext)
    assert write.call_count==1
    points,_=store.client.scroll(store.collection,with_payload=True,limit=10)
    assert all(point.payload["source_locale"]=="en" for point in points)


def test_prepared_writer_hashes_never_replace_publication_file_verification(tmp_path):
    import hashlib
    from app.config import Settings
    from app.services.generation_files import generation_paths
    from app.services.generation_artifacts import artifact_manifest, load_verified_publication, file_digest, GenerationIntegrityError
    from app.services.json_atomic import write_json_atomic
    settings=Settings(_env_file=None,data_dir=tmp_path)
    doc,gid="scopehashdoc001","a"*32
    paths=generation_paths(settings,doc,gid)
    paths.attachments.mkdir(parents=True)
    paths.bundle.mkdir(parents=True)
    target=paths.bundle/"concept.md"
    target.write_bytes(b"original")
    hashes={"bundle/concept.md":hashlib.sha256(b"original").hexdigest()}
    manifest=artifact_manifest(settings,doc,gid,written_hashes=hashes)
    publication=paths.uploads_root/"publication.json"
    write_json_atomic(publication,{"doc_id":doc,"generation_id":gid,"artifacts":manifest})
    expected=file_digest(publication)
    target.write_bytes(b"tampered")
    with pytest.raises(GenerationIntegrityError):load_verified_publication(settings,doc,gid,expected)
    target.unlink()
    with pytest.raises(GenerationIntegrityError):artifact_manifest(settings,doc,gid,written_hashes=hashes)


def test_preview_hashes_canonical_sql_once_inside_document_lock(source_environment,monkeypatch):
    from unittest.mock import Mock
    import app.services.processing_scope_change as change
    import app.services.processing_scope_jobs as jobs
    from app.db.models import DocumentProcessingReview
    from app.db.session import session_scope
    from copy import deepcopy
    env=source_environment
    selected=deepcopy(env["decisions"])
    selected[0]["effective_included"]=False
    with session_scope() as session:
        session.add(DocumentProcessingReview(doc_id=env["doc_id"],id="e"*32,revision=1,state="draft",
            source_file_hash=env["root_hash"],parser_version="test",assessor_version="test",policy_json={},
            inventory_json=env["snapshot"]["inventory"],selection_json=selected,selection_hash="d"*64,
            base_generation_id=env["base_id"]))
    calls=Mock(wraps=change.scope_sql_digest)
    monkeypatch.setattr(change,"scope_sql_digest",calls)
    monkeypatch.setattr(jobs,"scope_sql_digest",calls)
    assert jobs.create_change_preview(env["doc_id"],"e"*32,1,env["base_id"],"editor")["mode"]=="fast_exclude"
    assert calls.call_count==1,"One consistent write-locked snapshot needs only one full SQL hash"


from tests import test_processing_scope_change_end_to_end as scope_fixtures  # noqa: E402

source_environment = scope_fixtures.source_environment


@pytest.mark.parametrize("write_bundles",[False,True])
def test_scope_candidate_honors_optional_markdown_materialization(tmp_path,write_bundles,monkeypatch):
    from types import SimpleNamespace
    from app.services.canonical_repair import _write_bundle
    from app.services.okf_generator import OKFGenerator
    base,_=example()
    proposal=deepcopy(base.rows)
    for row in proposal["concepts"]:
        row.update(title=row["slug"],type="concept",content="preserved body")
    proposal.update(attachments=[],sources=[],global_tags=["current"])
    paths=SimpleNamespace(bundle=tmp_path/"bundle")
    hashes={}
    if not write_bundles:
        def reject_render(*args,**kwargs):raise AssertionError("SQL publication must not render an unused YAML export")
        monkeypatch.setattr("app.services.okf_generator._build_markdown",reject_render)
    docs=_write_bundle(OKFGenerator(),base.doc_id,"source.docx",paths,proposal,
        write_bundles=write_bundles,written_hashes=hashes)
    assert len(docs)==len(proposal["concepts"])
    assert [d.content for d in docs]==[r["content"] for r in proposal["concepts"]]
    assert [d.metadata["source_id"] for d in docs]==[r["source_id"] for r in proposal["concepts"]]
    assert all(r["content_hash"] for r in proposal["chunks"])
    files=list(paths.bundle.rglob("*.md"))
    assert bool(files)==write_bundles
    assert bool(hashes)==write_bundles
    assert (paths.bundle/"_files.json").exists()==write_bundles


def test_scope_signature_reuses_only_exact_bytes_and_algorithm_parameters(monkeypatch):
    from app.services.processing_scope_signatures import prepare_scope_signature
    from app.config import Settings
    import app.services.deduplication as dedup
    from unittest.mock import Mock
    settings=Settings(_env_file=None)
    build=Mock(wraps=dedup.prepare_document_signature)
    monkeypatch.setattr(dedup,"prepare_document_signature",build)
    text="Текст данных. Проверка неизменных результатов."
    first,cache=prepare_scope_signature(text,None,settings)
    second,cache=prepare_scope_signature(text,cache,settings)
    assert first==second and build.call_count==1
    _,cache=prepare_scope_signature(text+" ",cache,settings)
    assert build.call_count==2,"Even whitespace bytes are part of cache identity"
    changed=settings.model_copy(update={"dedup_shingle_n":settings.dedup_shingle_n+1})
    prepare_scope_signature(text,cache,changed)
    assert build.call_count==3
    for index in range(12):
        _,cache=prepare_scope_signature(text+str(index),cache,settings)
    assert len(cache["entries"])==8


def test_scope_signature_recomputes_a_corrupt_cached_result(monkeypatch):
    from app.services.processing_scope_signatures import prepare_scope_signature
    from app.config import Settings
    settings=Settings(_env_file=None)
    text="Слова текста для проверки подписи документа"
    expected,cache=prepare_scope_signature(text,None,settings)
    cache["entries"][0]["signature"]["minhash"]=[-1]
    actual,_=prepare_scope_signature(text,cache,settings)
    assert actual==expected


def test_cached_derived_restore_batches_sql_and_keeps_conflict_checks(source_environment):
    from app.services.processing_scope_worker import _insert_scope_records
    from app.services.generation_artifacts import GenerationIntegrityError
    from app.db.models import DocumentTable,DocumentTableRow
    from app.db.session import session_scope,get_engine
    from sqlalchemy import event,select,func
    env=source_environment
    table="a"*32
    with session_scope() as s:
        s.add(DocumentTable(doc_id=env["doc_id"],generation_id=env["base_id"],table_id=table,
            source_id="root/0",descriptor={},quality={}))
    rows=[dict(doc_id=env["doc_id"],generation_id="old",table_id=table,row_number=i+1,
        cells={"1":{"raw_value":str(i)}},hidden=False) for i in range(1000)]
    calls=[]
    def record(connection,cursor,statement,parameters,context,executemany):
        if statement.startswith("INSERT INTO document_table_rows"):
            calls.append(len(parameters) if executemany else 1)
    engine=get_engine()
    event.listen(engine,"before_cursor_execute",record)
    try:
        with session_scope() as s:
            _insert_scope_records(s,DocumentTableRow,env["doc_id"],env["base_id"],rows)
        assert len(calls)==4 and max(calls)<=256
        with session_scope() as s:
            _insert_scope_records(s,DocumentTableRow,env["doc_id"],env["base_id"],rows)
            assert s.scalar(select(func.count()).select_from(DocumentTableRow))==1000
        conflicting=deepcopy(rows[:1]);conflicting[0]["hidden"]=True
        with session_scope() as s,pytest.raises(GenerationIntegrityError):
            _insert_scope_records(s,DocumentTableRow,env["doc_id"],env["base_id"],conflicting)
    finally:event.remove(engine,"before_cursor_execute",record)
