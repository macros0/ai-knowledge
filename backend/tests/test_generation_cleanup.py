"""Cross-store cleanup can retry without removing a published generation."""
import pytest
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

from app.db.models import DocumentGeneration
from app.db.session import session_scope
from app.services.errors import VectorStoreError
from app.services.generation_files import generation_paths
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def _retire_first(pipeline_env):
    pipeline, source, write = pipeline_env
    first = _published_snapshot(pipeline)[0]
    write("Second published version.")
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    return pipeline, first, _published_snapshot(pipeline)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_worker_cleans_after_publication_and_retries_without_changing_done(pipeline_env, monkeypatch, cleanup_fails):
    pipeline, source, write = pipeline_env
    first = _published_snapshot(pipeline)[0]
    original = pipeline.vector_store.delete_generation_points
    if cleanup_fails:
        monkeypatch.setattr(pipeline.vector_store, "delete_generation_points", lambda *_args: (
            _ for _ in ()
        ).throw(VectorStoreError("Temporarily unavailable")))
    write("Second published version.")
    assert pipeline._pipeline_slots.acquire(timeout=1)
    pipeline._run(DOC_ID, source, "decision.eml", [], False)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        assert (session.get(DocumentGeneration, first) is not None) == cleanup_fails
    monkeypatch.setattr(pipeline.vector_store, "delete_generation_points", original)
    pipeline.cleanup_inactive_generations()
    with session_scope() as session:
        assert session.get(DocumentGeneration, first) is None


def test_periodic_cleanup_skips_current_workers_then_retries(pipeline_env):
    from concurrent.futures import Future

    pipeline, first, active = _retire_first(pipeline_env)
    pipeline._threads[DOC_ID] = Future()
    try:
        pipeline.cleanup_inactive_generations()
        with session_scope() as session:
            assert session.get(DocumentGeneration, first) is not None
    finally:
        pipeline._threads.pop(DOC_ID)
    pipeline.cleanup_inactive_generations()
    with session_scope() as session:
        assert session.get(DocumentGeneration, first) is None
    assert _published_snapshot(pipeline) == active


def test_cleanup_loop_retries_failed_pass_and_stops():
    from app.services.generation_cleanup import run_cleanup_loop

    stop = Event()
    attempts = []

    def cleanup():
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("Storage unavailable")
        stop.set()

    run_cleanup_loop(stop, cleanup, interval=0.001)
    assert len(attempts) == 2


def test_cleanup_removes_retired_artifacts_only_and_is_idempotent(pipeline_env):
    from app.services.generation_cleanup import cleanup_document_generations

    pipeline, first, active = _retire_first(pipeline_env)
    assert cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID) == 1
    assert _published_snapshot(pipeline) == active
    paths = generation_paths(pipeline.settings, DOC_ID, first)
    assert not paths.uploads_root.exists() and not paths.bundle.exists()
    with session_scope() as session:
        assert session.get(DocumentGeneration, first) is None
    points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    assert points and all(row.payload.get("generation_id") == active[0] for row in points)
    assert cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID) == 0


def test_concurrent_cleanup_rechecks_retired_row_after_document_lock(pipeline_env, monkeypatch):
    import app.services.generation_cleanup as module
    from sqlalchemy import update

    pipeline, first, active = _retire_first(pipeline_env)
    with session_scope() as session:
        session.execute(update(DocumentGeneration).where(DocumentGeneration.doc_id == DOC_ID).values(
            legacy_cleanup_pending=False,
        ))
    ready = Barrier(2)
    original = module.lock_cleanup_generation

    def synchronized_lock(*args, **kwargs):
        ready.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "lock_cleanup_generation", synchronized_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(module.cleanup_document_generations, pipeline.settings, pipeline.vector_store, DOC_ID)
                   for _ in range(2)]
        assert sorted(future.result(timeout=15) for future in futures) == [0, 1]
    assert _published_snapshot(pipeline) == active
    with session_scope() as session:
        assert session.get(DocumentGeneration, first) is None


def test_qdrant_cleanup_failure_keeps_retry_state_and_files(pipeline_env, monkeypatch):
    from app.services.generation_cleanup import cleanup_document_generations

    pipeline, first, active = _retire_first(pipeline_env)
    original = pipeline.vector_store.delete_generation_points

    def fail(*_args, **_kwargs):
        raise VectorStoreError("Injected unavailable index")

    monkeypatch.setattr(pipeline.vector_store, "delete_generation_points", fail)
    with pytest.raises(VectorStoreError):
        cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID)
    assert _published_snapshot(pipeline) == active
    assert generation_paths(pipeline.settings, DOC_ID, first).uploads_root.exists()
    with session_scope() as session:
        assert session.get(DocumentGeneration, first) is not None
    monkeypatch.setattr(pipeline.vector_store, "delete_generation_points", original)
    assert cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID) == 1


def test_cleanup_retries_after_files_deleted_but_sql_not_committed(pipeline_env, monkeypatch):
    import app.services.generation_cleanup as module

    pipeline, first, active = _retire_first(pipeline_env)
    original = module.cleanup_generation_files

    def fail_after_delete(*args):
        original(*args)
        raise OSError("Injected crash before SQL cleanup commit")

    monkeypatch.setattr(module, "cleanup_generation_files", fail_after_delete)
    with pytest.raises(OSError):
        module.cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID)
    assert _published_snapshot(pipeline) == active
    assert not generation_paths(pipeline.settings, DOC_ID, first).uploads_root.exists()
    with session_scope() as session:
        assert session.get(DocumentGeneration, first) is not None
    monkeypatch.setattr(module, "cleanup_generation_files", original)
    assert module.cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID) == 1


def test_legacy_cleanup_preserves_original_upload_and_generation_tree(pipeline_env):
    from app.models.schemas import OkfDocument
    from app.services.generation_cleanup import cleanup_document_generations

    pipeline, source, _write = pipeline_env
    active = _published_snapshot(pipeline)
    original = source.read_bytes()
    legacy_attachment = pipeline.settings.uploads_dir / DOC_ID / "attachments" / "old.bin"
    legacy_attachment.parent.mkdir(parents=True, exist_ok=True)
    legacy_attachment.write_bytes(b"old")
    legacy_bundle = pipeline.settings.okf_dir / DOC_ID / "old.md"
    legacy_bundle.write_text("old")
    unrelated = pipeline.settings.okf_dir / DOC_ID / "keep.txt"
    unrelated.write_text("unrelated")
    pipeline.vector_store.index_concepts(DOC_ID, [OkfDocument(
        filepath=f"{DOC_ID}/old.md", content="Legacy", markdown="", metadata={"title": "Legacy"},
    )], [[1.0] * 8])
    assert cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID) == 0
    assert not legacy_attachment.exists() and not legacy_bundle.exists()
    assert unrelated.read_text() == "unrelated"
    assert source.read_bytes() == original
    assert _published_snapshot(pipeline) == active
    points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    assert all(row.payload.get("generation_id") == active[0] for row in points)


def _prepare_second(pipeline_env, monkeypatch):
    import app.services.generation_publication as module
    from app.db.models import DocumentGenerationState

    pipeline, source, write = pipeline_env
    original = module.publish_generation
    write("Second ready version.")
    with monkeypatch.context() as context:
        context.setattr(module, "publish_generation", lambda *_args: (_ for _ in ()).throw(RuntimeError("Before commit")))
        pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert module.publish_generation is original
    with session_scope() as session:
        candidate = session.get(DocumentGenerationState, DOC_ID).candidate_generation_id
    return pipeline, candidate


def test_export_keeps_old_bytes_while_publication_and_cleanup_race(pipeline_env, monkeypatch, tmp_path):
    import app.services.export_okf as module
    from app.services.generation_cleanup import cleanup_document_generations
    from app.services.staging import StagingStore

    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    previous = _published_snapshot(pipeline)
    triggered, completed = Event(), Event()
    original_copy = module.shutil.copy2

    def copy_while_publishing(*args, **kwargs):
        triggered.set()
        completed.wait(0.5)
        return original_copy(*args, **kwargs)

    def publisher():
        assert triggered.wait(5)
        pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))
        cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID)
        completed.set()

    monkeypatch.setattr(module.shutil, "copy2", copy_while_publishing)
    destination = tmp_path / "concurrent-export"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publisher)
        module.export_okf_bundle(DOC_ID, destination)
        future.result(timeout=10)
    assert completed.is_set()
    assert sorted(path.read_bytes() for path in (destination / "attachments").iterdir()) == sorted(previous[2].values())


def test_source_location_reads_concept_and_chunk_from_same_publication(pipeline_env, monkeypatch):
    from sqlalchemy import event
    from app.db.session import get_engine
    from app.services.source_location import get_source_location
    from app.services.staging import StagingStore

    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    previous = _published_snapshot(pipeline)
    slug = previous[1][0][0]
    expected = get_source_location(DOC_ID, slug).model_dump()
    assert expected["spans"]
    triggered, completed = Event(), Event()

    def after_concept_read(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().startswith("SELECT okf_concepts.chunk_index"):
            triggered.set()
            completed.wait(1)

    def publisher():
        assert triggered.wait(5)
        pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))
        completed.set()

    engine = get_engine()
    event.listen(engine, "after_cursor_execute", after_concept_read)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(publisher)
            actual = get_source_location(DOC_ID, slug).model_dump()
            future.result(timeout=10)
    finally:
        event.remove(engine, "after_cursor_execute", after_concept_read)
    assert completed.is_set()
    assert actual == expected


def test_download_opened_before_publication_survives_cleanup(pipeline_env, monkeypatch):
    from app.api.documents import get_okf_attachment
    from app.services.generation_cleanup import cleanup_document_generations
    from app.services.staging import StagingStore
    from pathlib import Path

    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    previous = _published_snapshot(pipeline)
    saved, expected = next(iter(previous[2].items()))
    response = get_okf_attachment(DOC_ID, Path(saved).name)
    pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))
    try:
        cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID)
    except PermissionError:
        pass  # Windows holds the open file until the response finishes.
    received = []

    async def send(message):
        if message["type"] == "http.response.body":
            received.append(message.get("body", b""))

    async def receive():
        return {"type": "http.disconnect"}

    asyncio.run(response({"type": "http", "method": "GET", "headers": [], "asgi": {"spec_version": "2.4"}}, receive, send))
    assert b"".join(received) == expected
    cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID)
