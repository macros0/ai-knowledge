"""Publication failures must leave the previously published document usable."""
from email.message import EmailMessage
from importlib import import_module
import json
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from app.config import Settings
from app.db.models import DocumentGenerationState, OkfConcept, OkfAttachment
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.errors import VectorStoreError
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry
from app.services.staging import StagingStore


DOC_ID = "a123456789abcdef"


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, embedding_provider="fake", embedding_dimensions=8,
        llm_model="test/mail", dedup_enabled=False, dev_detection_enabled=False,
        okf_write_bundles=True, mail_import_enabled=True, parser_supervisor_enabled=False,
    )
    modules = (
        "app.config", "app.services.pipeline", "app.services.staging", "app.services.embedder",
        "app.services.llm_client", "app.services.okf_generator", "app.services.vector_store",
        "app.api.documents", "app.services.export_okf", "app.services.deduplication", "app.services.dev_detector",
    )
    # Import before replacing app.config.get_settings: a lazy module import
    # must not retain a fixture's lambda after monkeypatch restores its caller.
    loaded_modules = [import_module(module) for module in modules]
    for module in loaded_modules:
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    pipeline = Pipeline()
    pipeline.vector_store.client.close()
    pipeline.vector_store.client = QdrantClient(location=":memory:")
    pipeline.okf_generator.generate_chunk = lambda text, *_args, **_kwargs: [
        Concept(id="decision", title="Decision", content=text)
    ]
    source = settings.uploads_dir / f"{DOC_ID}.eml"
    source.parent.mkdir(parents=True, exist_ok=True)

    def write_source(body):
        message = EmailMessage()
        message["Subject"] = "Publication test"
        message.set_content(body)
        message.add_attachment(body.encode(), maintype="application", subtype="octet-stream", filename="data.bin")
        source.write_bytes(message.as_bytes())

    write_source("Original approved decision.")
    registry.create(DOC_ID, "decision.eml", "message/rfc822", source.stat().st_size)
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert registry.get(DOC_ID)["status"] == "done"
    yield pipeline, source, write_source
    pipeline.vector_store.client.close()


def _published_snapshot(pipeline):
    with session_scope() as session:
        state = session.get(DocumentGenerationState, DOC_ID)
        concepts = [(r.slug, r.content) for r in session.query(OkfConcept).filter_by(doc_id=DOC_ID)]
        attachments = [r.saved_path for r in session.query(OkfAttachment).filter_by(doc_id=DOC_ID)]
        active = state.active_generation_id if state else None
    return active, concepts, {
        path: (pipeline.settings.uploads_dir / DOC_ID / path).read_bytes() for path in attachments
    }


def test_regenerate_does_not_delete_published_data_before_worker_starts(pipeline_env, monkeypatch):
    pipeline, _source, _write = pipeline_env
    before = _published_snapshot(pipeline)
    records, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    old_ids = {str(row.id) for row in records}
    monkeypatch.setattr(pipeline, "_start", lambda *_args, **_kwargs: None)
    pipeline.regenerate(DOC_ID)
    assert _published_snapshot(pipeline) == before
    records, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    assert {str(row.id) for row in records} == old_ids


def test_rejected_regeneration_preserves_checkpoint_and_document_status(pipeline_env):
    from threading import BoundedSemaphore
    from app.services.errors import DomainError

    pipeline, _source, _write = pipeline_env
    staging = StagingStore(DOC_ID)
    staging.create(1)
    checkpoint = staging.load()
    assert checkpoint is not None
    before = pipeline.registry.get(DOC_ID)["status"]
    pipeline._pipeline_slots = BoundedSemaphore(0)

    with pytest.raises(DomainError, match="перегружена"):
        pipeline.regenerate(DOC_ID)

    assert staging.load() == checkpoint
    assert pipeline.registry.get(DOC_ID)["status"] == before


def test_parallel_regeneration_resets_checkpoints_only_for_admitted_worker(pipeline_env, monkeypatch):
    from concurrent.futures import Future, ThreadPoolExecutor
    from threading import Barrier, local
    from app.services.errors import ConflictError

    pipeline, _source, _write = pipeline_env
    ready, calls = Barrier(2), local()
    original_check = pipeline._ensure_not_running
    original_remove = StagingStore.remove
    removals = []

    def check(doc_id):
        original_check(doc_id)
        if not getattr(calls, "checked", False):
            calls.checked = True
            ready.wait(timeout=5)

    def remove(staging):
        removals.append(staging.doc_id)
        original_remove(staging)

    def regenerate():
        try:
            pipeline.regenerate(DOC_ID)
            return "admitted"
        except ConflictError:
            return "conflict"

    monkeypatch.setattr(pipeline, "_ensure_not_running", check)
    monkeypatch.setattr(StagingStore, "remove", remove)
    monkeypatch.setattr(pipeline._executor, "submit", lambda *_args: Future())
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: regenerate(), range(2)))
    assert sorted(results) == ["admitted", "conflict"]
    assert removals == [DOC_ID]


def test_index_failure_preserves_old_text_and_attachment_bytes(pipeline_env, monkeypatch):
    pipeline, source, write = pipeline_env
    before = _published_snapshot(pipeline)
    write("New decision pending indexing.")

    def fail(*_args, **_kwargs):
        raise VectorStoreError("Injected unavailable index")

    monkeypatch.setattr(pipeline.vector_store, "index_chunks", fail)
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert pipeline.registry.get(DOC_ID)["status"] != "done"
    assert _published_snapshot(pipeline) == before
    assert StagingStore(DOC_ID).exists()


def test_success_publishes_new_generation_and_uses_distinct_attachment_paths(pipeline_env):
    pipeline, source, write = pipeline_env
    before = _published_snapshot(pipeline)
    assert before[0] is not None
    write("New published decision.")
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    after = _published_snapshot(pipeline)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    assert after[0] and after[0] != before[0]
    assert after[1] != before[1]
    assert set(after[2]).isdisjoint(before[2])
    assert all(f"generations/{after[0]}/attachments/" in name for name in after[2])


def test_checkpoint_read_failure_after_commit_does_not_mark_publication_failed(pipeline_env, monkeypatch):
    pipeline, source, write = pipeline_env
    previous = _published_snapshot(pipeline)[0]
    original = StagingStore.partial_chunks.fget

    def unavailable_after_commit(staging):
        with session_scope() as session:
            active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        if active != previous:
            raise OSError("Checkpoint unavailable after publication")
        return original(staging)

    monkeypatch.setattr(StagingStore, "partial_chunks", property(unavailable_after_commit))
    write("Published despite checkpoint cleanup failure.")
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert _published_snapshot(pipeline)[0] != previous
    assert pipeline.registry.get(DOC_ID)["status"] == "done"


@pytest.mark.parametrize("edit_metadata", [False, True])
def test_ready_generation_resumes_publication_without_parser_or_llm(pipeline_env, monkeypatch, edit_metadata):
    import app.services.pipeline as module
    import app.services.generation_publication as publication

    pipeline, source, write = pipeline_env
    before = _published_snapshot(pipeline)
    write("Prepared decision.")
    original = publication.publish_generation

    def fail(*_args, **_kwargs):
        raise RuntimeError("Crash just before publication")

    monkeypatch.setattr(publication, "publish_generation", fail)
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert _published_snapshot(pipeline) == before
    monkeypatch.setattr(publication, "publish_generation", original)
    if edit_metadata:
        pipeline.registry.update(DOC_ID, tags=["current-tag"], source_locale="de", source_locale_source="manual")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("A ready generation must not be parsed or generated again")

    monkeypatch.setattr(module, "parse_document", forbidden)
    pipeline.okf_generator.generate_chunk = forbidden
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=True)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    assert _published_snapshot(pipeline)[0] != before[0]
    if edit_metadata:
        document = pipeline.registry.get(DOC_ID)
        assert document["source_locale"] == "de"
        assert document["source_locale_source"] == "manual"
        with session_scope() as session:
            assert all("current-tag" in row.tags for row in session.query(OkfConcept).filter_by(doc_id=DOC_ID))
        active = _published_snapshot(pipeline)[0]
        points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
        current = [row for row in points if row.payload.get("generation_id") == active]
        assert current
        assert all(row.payload["source_locale"] == "de" and "current-tag" in row.payload["tags"] for row in current)


def test_attachment_download_resolves_only_published_registered_bytes(pipeline_env):
    from app.api.documents import get_okf_attachment
    from tests.test_artifact_response import _request

    pipeline, _source, _write = pipeline_env
    snapshot = _published_snapshot(pipeline)
    saved_path = next(iter(snapshot[2]))
    response = get_okf_attachment(DOC_ID, Path(saved_path).name)
    assert Path(response.path) == (pipeline.settings.uploads_dir / DOC_ID / saved_path).resolve()
    assert _request(response)[2] == snapshot[2][saved_path]
    assert response._file.closed


def test_export_rebases_generation_source_paths_to_portable_bundle(pipeline_env, tmp_path):
    from app.services.export_okf import export_okf_bundle

    _pipeline, _source, _write = pipeline_env
    destination = tmp_path / "export"
    export_okf_bundle(DOC_ID, destination)
    sources = json.loads((destination / "sources.json").read_text(encoding="utf-8"))["sources"]
    stored = [row["saved_path"] for row in sources if row.get("saved_path")]
    assert stored
    assert all(path.startswith("attachments/") for path in stored)
    assert all((destination / path).is_file() for path in stored)


def test_failed_attempt_does_not_publish_dedup_or_development_metadata(pipeline_env, monkeypatch):
    from app.db.models import Document
    from app.services.dev_detector import Detection

    pipeline, source, write = pipeline_env
    pipeline.settings.dedup_enabled = True
    pipeline.settings.dev_detection_enabled = True
    monkeypatch.setattr("app.services.pipeline.detect", lambda *_args: Detection(
        confidence=0.6, suggestion={"number": "NEW-123"},
    ))
    with session_scope() as session:
        document = session.get(Document, DOC_ID)
        before = (document.content_hash, document.minhash, document.development_suggestion)
    write("A new unique message for deduplication and development detection.")
    monkeypatch.setattr(pipeline.vector_store, "ensure_collection", lambda: (_ for _ in ()).throw(
        VectorStoreError("Injected unavailable index")
    ))
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    with session_scope() as session:
        document = session.get(Document, DOC_ID)
        assert (document.content_hash, document.minhash, document.development_suggestion) == before


def test_chunk_backfill_reuses_published_generation_point_ids(pipeline_env):
    from qdrant_client.http import models as qm

    pipeline, _source, _write = pipeline_env
    active = _published_snapshot(pipeline)[0]
    pipeline.vector_store.client.delete(
        pipeline.vector_store.collection,
        qm.FilterSelector(filter=qm.Filter(must=[
            qm.FieldCondition(key="point_type", match=qm.MatchValue(value="chunk")),
        ])),
    )
    assert pipeline.vector_store.backfill_chunks(pipeline.embedder) > 0
    points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    chunks = [row for row in points if row.payload.get("point_type") == "chunk"]
    assert chunks and all(row.payload.get("generation_id") == active for row in chunks)
    assert pipeline.vector_store.backfill_chunks(pipeline.embedder) == 0


def test_sparse_backfill_updates_published_generation_points(pipeline_env):
    pipeline, _source, _write = pipeline_env
    assert pipeline.vector_store.backfill_sparse(force=True, include_chunks=True) > 0


def test_tag_and_relation_sync_target_published_generation_points(pipeline_env, monkeypatch):
    from app.services.document_tag_service import _sync_qdrant_tags

    pipeline, _source, _write = pipeline_env
    with session_scope() as session:
        for row in session.query(OkfConcept).filter_by(doc_id=DOC_ID):
            row.tags = ["changed"]
            row.relations = ["new-relation"]
    monkeypatch.setattr("app.services.vector_store.VectorStore", lambda: pipeline.vector_store)
    assert _sync_qdrant_tags(DOC_ID)
    assert pipeline.vector_store.backfill_relations() > 0
    points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    concepts = [row for row in points if row.payload.get("point_type") == "concept"]
    assert concepts
    assert all(row.payload["tags"] == ["changed"] and row.payload["relations"] == ["new-relation"] for row in concepts)


def test_empty_published_generation_is_not_reparsed_during_read(pipeline_env, monkeypatch):
    pipeline, source, _write = pipeline_env
    pipeline.okf_generator.chunk_text = lambda *_args, **_kwargs: []
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Read paths must not rewrite a published generation")

    monkeypatch.setattr("app.services.pipeline.parse_document", forbidden)
    assert pipeline.ensure_chunks(DOC_ID) == []


def test_regenerate_keeps_shared_table_cache_and_bounds_it_for_the_document(pipeline_env):
    import time

    from app.services import field_table

    pipeline, _source, _write = pipeline_env
    shared_entry = pipeline.settings.cache_dir / "table_classify" / "other-document-table.json"
    shared_entry.parent.mkdir(parents=True, exist_ok=True)
    shared_entry.write_text("{}", encoding="utf-8")
    seen = []

    def generate(text, *_args, **_kwargs):
        seen.append(getattr(field_table._fresh_since, "value", None))
        return [Concept(id="decision", title="Decision", content=text)]

    pipeline.okf_generator.generate_chunk = generate
    started = time.time()
    pipeline.regenerate(DOC_ID)
    assert pipeline.wait_for(DOC_ID, timeout=30)["status"] == "done"

    # Общий кэш других документов не стирается (раньше — rmtree всего каталога).
    assert shared_entry.is_file()
    # Генерация этого документа шла с границей свежести = старт перегенерации.
    assert seen and all(value is not None and value >= started for value in seen)


def test_table_cache_bound_survives_resume_of_the_same_attempt(tmp_path):
    from app.services.pipeline import _table_cache_fresh_since

    assert _table_cache_fresh_since(tmp_path, fresh=False, resume=False) is None
    since = _table_cache_fresh_since(tmp_path, fresh=True, resume=False)
    assert since is not None
    # Resume той же попытки перегенерации сохраняет границу.
    assert _table_cache_fresh_since(tmp_path, fresh=False, resume=True) == since
    # Обычная обработка пользуется общим кэшем без границы.
    assert _table_cache_fresh_since(tmp_path, fresh=False, resume=False) is None


def test_pipeline_mail_scopes_use_candidate_tree(pipeline_env):
    pipeline, source, write = pipeline_env
    write('Regenerated mail evidence')
    pipeline._process(DOC_ID, source, 'decision.eml', [], resume=False)
    active = _published_snapshot(pipeline)[0]
    rows, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    rows = [row for row in rows if row.payload['generation_id'] == active]
    assert rows
    assert {row.payload['mail_scope'] for row in rows} == {'mail'}
    assert all(row.payload['mail_scope_version'] == 1 for row in rows)
