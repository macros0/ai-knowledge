"""Offline rebuilds preserve published identity, provenance and sparse chunk indices."""
from importlib import import_module

import pytest
from qdrant_client import QdrantClient

from app.config import Settings
from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation
from app.services.registry import DocumentRegistry
from app.services.vector_store import VectorStore, chunk_point_id, concept_point_id


@pytest.fixture
def rebuild_env(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_dimensions=2,
                        qdrant_collection="original", search_index_chunks_enabled=True)
    store = VectorStore.__new__(VectorStore)
    store.settings = settings
    store.client = QdrantClient(location=":memory:")
    store.ensure_collection()

    class Embedder:
        def embed_texts(self, texts):
            return [[1.0, 0.0] for _ in texts]

    def run(script, concepts_only=False):
        module = import_module(f"scripts.{script}")
        monkeypatch.setattr(module, "get_settings", lambda: settings)
        monkeypatch.setattr(module, "VectorStore", lambda: store)
        monkeypatch.setattr(module, "Embedder", Embedder)
        if script == "reindex":
            monkeypatch.setattr("sys.argv", [script, *(["--concepts-only"] if concepts_only else [])])
            module.main()
        else:
            module.build_v2(concepts_only=concepts_only)
        points, _ = store.client.scroll(store.collection, limit=100)
        return points

    yield settings, store, run
    store.client.close()


def _published(doc_id, status="done", legacy=False, deleted=False):
    DocumentRegistry().create(doc_id, "nested.docx", "x", 1, tags=["user-tag"])
    with session_scope() as session:
        generation_id = None
        if not legacy:
            generation_id = begin_generation(session, doc_id).id
            mark_generation_ready(session, doc_id, generation_id)
            publish_generation(session, doc_id, generation_id)
            if status != "done":
                begin_generation(session, doc_id)
        document = session.get(Document, doc_id)
        document.status = status
        document.source_locale = "ru"
        session.add(OkfConcept(doc_id=doc_id, slug="decision", title="Decision",
                               content="Published evidence", tags=["specific", "user-tag"],
                               chunk_index=4, source_id="root/0/0"))
        for index, source_id in [(4, "root/0/0"), (9, "root/1")]:
            session.add(DocumentChunk(doc_id=doc_id, chunk_index=index, source_id=source_id,
                                      section_title=f"Section {index}", content=f"Canonical {index}"))
    if deleted:
        DocumentRegistry().soft_delete(doc_id)
    return generation_id


@pytest.mark.parametrize("script", ["reindex", "rebuild_qdrant_v2"])
@pytest.mark.parametrize("status", ["done", "paused", "failed"])
def test_rebuild_preserves_published_generation_and_provenance(rebuild_env, script, status):
    _settings, _store, run = rebuild_env
    doc_id = "published"
    generation_id = _published(doc_id, status)
    points = run(script)
    assert {str(point.id) for point in points} == {
        concept_point_id(doc_id, "decision", generation_id=generation_id),
        chunk_point_id(doc_id, 4, generation_id=generation_id),
        chunk_point_id(doc_id, 9, generation_id=generation_id),
    }
    assert all(point.payload["generation_id"] == generation_id for point in points)
    assert all(point.payload["source_locale"] == "ru" and "user-tag" in point.payload["tags"] for point in points)
    assert {(p.payload["chunk_index"], p.payload["source_id"]) for p in points} == {
        (4, "root/0/0"), (9, "root/1"),
    }
    from app.services.fusion import Hit
    from app.services.retrieval_hydration import load_visible_retrieval_hits

    visible, _ = load_visible_retrieval_hits([Hit(str(p.id), 1.0, p.payload) for p in points])
    assert len(visible) == 3
    assert {hit.payload["content"] for hit in visible} == {"Published evidence", "Canonical 4", "Canonical 9"}


@pytest.mark.parametrize("script", ["reindex", "rebuild_qdrant_v2"])
def test_rebuild_keeps_legacy_published_rows_but_excludes_trash_and_empty_candidates(rebuild_env, script):
    _settings, _store, run = rebuild_env
    _published("legacy", status="paused", legacy=True)
    _published("trash", deleted=True)
    with session_scope() as session:
        session.add(Document(id="new", filename="new.eml", status="processing"))
        session.flush()
        begin_generation(session, "new")
    points = run(script)
    assert points and {point.payload["doc_id"] for point in points} == {"legacy"}
    assert {str(point.id) for point in points} == {
        concept_point_id("legacy", "decision"), chunk_point_id("legacy", 4), chunk_point_id("legacy", 9),
    }


def test_v2_rebuild_does_not_redirect_or_delete_current_collection(rebuild_env):
    settings, store, run = rebuild_env
    store.index_chunks("original-only", "x", ["old index"], [], [[1.0, 0.0]])
    original = settings.qdrant_collection
    _published("new-target")
    run("rebuild_qdrant_v2")
    assert settings.qdrant_collection == original
    assert store.client.count(original).count == 1


def test_v2_rebuild_refuses_to_delete_its_active_target(rebuild_env):
    settings, store, run = rebuild_env
    settings.qdrant_collection = "okf_knowledge_base_v2"
    store.ensure_collection()
    store.index_chunks("keep", "x", ["active index"], [], [[1.0, 0.0]])
    with pytest.raises(ValueError, match="active"):
        run("rebuild_qdrant_v2")
    assert store.client.count(settings.qdrant_collection).count == 1


def test_parity_ignores_non_active_generations(rebuild_env):
    _settings, store, run = rebuild_env
    _published("parity", status="paused")
    run("reindex")
    store.index_chunks("parity", "x", ["not published"], [], [[1.0, 0.0]], generation_id="a" * 32)
    run("rebuild_qdrant_v2")
    import_module("scripts.rebuild_qdrant_v2").parity()


def test_parity_detects_broken_source_identity_with_equal_point_counts(rebuild_env):
    _settings, store, run = rebuild_env
    generation_id = _published("parity")
    run("reindex")
    run("rebuild_qdrant_v2")
    point_id = concept_point_id("parity", "decision", generation_id=generation_id)
    store.client.set_payload(store.collection, {"source_id": "root/wrong"}, [point_id])
    with pytest.raises(SystemExit) as caught:
        import_module("scripts.rebuild_qdrant_v2").parity()
    assert caught.value.code == 1


@pytest.mark.parametrize("script", ["reindex", "rebuild_qdrant_v2"])
def test_concepts_only_rebuild_preserves_generation(rebuild_env, script):
    _settings, _store, run = rebuild_env
    generation_id = _published("concepts-only", status="failed")
    points = run(script, concepts_only=True)
    assert [str(point.id) for point in points] == [concept_point_id("concepts-only", "decision", generation_id=generation_id)]


def test_integrity_checks_only_active_artifacts_and_points(rebuild_env):
    from app.services.generation_files import generation_paths
    from scripts.check_integrity import check_doc, iter_done_docs

    settings, store, run = rebuild_env
    generation_id = _published("integrity", status="failed")
    run("reindex")
    store.index_chunks("integrity", "x", ["old"], [], [[1.0, 0.0]], generation_id="c" * 32)
    paths = generation_paths(settings, "integrity", generation_id)
    (paths.bundle / "chunks").mkdir(parents=True)
    for index in (4, 9):
        (paths.bundle / "chunks" / f"chunk_{index:02d}.md").write_text(f"Canonical {index}", encoding="utf-8")
    retired = settings.uploads_dir / "integrity" / "attachments"
    retired.mkdir(parents=True)
    (retired / "old.bin").write_bytes(b"old")
    result = check_doc("integrity", 2, 1, settings, store, verify_content=True)
    assert result["ok"], result["issues"]
    assert result["qdrant_points"] == 3
    assert any(row[0] == "integrity" for row in iter_done_docs())


def test_integrity_reports_active_bundle_drift_and_nested_orphan(rebuild_env):
    from app.services.generation_files import generation_paths
    from scripts.check_integrity import check_doc

    settings, _store, _run = rebuild_env
    generation_id = _published("integrity")
    paths = generation_paths(settings, "integrity", generation_id)
    (paths.bundle / "chunks").mkdir(parents=True)
    (paths.bundle / "chunks" / "chunk_04.md").write_text("Wrong", encoding="utf-8")
    (paths.attachments / "nested").mkdir(parents=True)
    (paths.attachments / "nested" / "orphan.bin").write_bytes(b"orphan")
    result = check_doc("integrity", 2, 1, settings, verify_content=True)
    assert any("chunk_04" in issue for issue in result["issues"])
    assert any("orphan.bin" in issue for issue in result["issues"])


def test_reindex_mail_scopes_use_locked_canonical_tree(rebuild_env):
    from app.services.source_store import replace_sources
    _settings, _store, run = rebuild_env
    _published('doc')
    with session_scope() as session:
        replace_sources(session, 'doc', [
            dict(source_id='root', parent_source_id=None),
            dict(source_id='root/0', parent_source_id='root', kind='mail'),
            dict(source_id='root/0/0', parent_source_id='root/0'),
            dict(source_id='root/1', parent_source_id='root'),
        ])
    points = run('reindex')
    assert {p.payload['mail_scope'] for p in points if p.payload['chunk_index'] == 4} == {'mail'}
    assert {p.payload['mail_scope'] for p in points if p.payload['chunk_index'] == 9} == {'document'}
