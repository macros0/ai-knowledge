"""Document edits must serialize with canonical generation publication."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event, get_ident

from sqlalchemy import event
import pytest

from app.db.session import get_engine
from app.services.staging import StagingStore
from app.services.tag_registry import TagRegistry
from tests.test_generation_cleanup import _prepare_second
from tests.test_generation_pipeline import DOC_ID
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def test_tag_edit_reads_document_only_after_publication_lock_is_released(pipeline_env, monkeypatch):
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    TagRegistry().get_or_create_ids(["concurrent-tag"])
    locked, editor_started, editor_read = Event(), Event(), Event()
    observed = {}
    original = pipeline.vector_store.verify_generation_points

    def editor():
        observed["editor_thread"] = get_ident()
        assert locked.wait(5)
        editor_started.set()
        pipeline.registry.update(DOC_ID, tags=["concurrent-tag"])

    def after_read(_connection, _cursor, statement, _parameters, _context, _many):
        if (get_ident() == observed.get("editor_thread") and statement.lstrip().startswith("SELECT")
                and "FROM documents" in statement):
            editor_read.set()

    def verify(*args):
        locked.set()
        assert editor_started.wait(5)
        observed["read_while_publishing"] = editor_read.wait(0.5)
        return original(*args)

    monkeypatch.setattr(pipeline.vector_store, "verify_generation_points", verify)
    engine = get_engine()
    event.listen(engine, "after_cursor_execute", after_read)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(editor)
            pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))
            future.result(timeout=10)
    finally:
        event.remove(engine, "after_cursor_execute", after_read)
    assert editor_read.is_set()
    assert observed["read_while_publishing"] is False
    assert pipeline.registry.get(DOC_ID)["tags"] == ["concurrent-tag"]


def test_tag_sync_keeps_generation_alive_until_payload_update_finishes(pipeline_env, monkeypatch):
    from app.services.document_tag_service import _sync_qdrant_tags
    from app.services.generation_cleanup import cleanup_document_generations

    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    pipeline.registry.update(DOC_ID, tags=["old-tag"])
    triggered, completed = Event(), Event()
    original = pipeline.vector_store.set_document_tags_payload

    def update_while_publishing(*args, **kwargs):
        triggered.set()
        completed.wait(0.5)
        return original(*args, **kwargs)

    def publisher():
        assert triggered.wait(5)
        pipeline.registry.update(DOC_ID, tags=["new-tag"])
        pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))
        cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID)
        completed.set()

    monkeypatch.setattr("app.services.vector_store.VectorStore", lambda: pipeline.vector_store)
    monkeypatch.setattr(pipeline.vector_store, "set_document_tags_payload", update_while_publishing)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publisher)
        synced = _sync_qdrant_tags(DOC_ID)
        future.result(timeout=10)
    assert synced is True
    points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    assert points
    assert all(point.payload["generation_id"] == candidate and "new-tag" in point.payload["tags"]
               for point in points)


def test_delayed_bundle_rewrite_uses_current_canonical_tags(pipeline_env, monkeypatch):
    import yaml
    from app.db.models import OkfConcept
    from app.db.session import session_scope
    from app.services import document_tag_service as tags
    from app.services.generation_files import active_bundle_path

    pipeline, _source, _write = pipeline_env
    monkeypatch.setattr(tags, "get_settings", lambda: pipeline.settings)
    pipeline.registry.update(DOC_ID, tags=["current"])
    with session_scope() as session:
        concepts = session.query(OkfConcept).filter_by(doc_id=DOC_ID).all()
        for concept in concepts:
            concept.tags = ["specific", "current"]
        slugs = [concept.slug for concept in concepts]
    tags._rewrite_bundle_frontmatter(DOC_ID, ["obsolete"], {"older"}, ["obsolete"])
    bundle = active_bundle_path(pipeline.settings, DOC_ID)
    for slug in slugs:
        meta = yaml.safe_load((bundle / f"{slug}.md").read_text(encoding="utf-8").split("---", 2)[1])
        assert meta["global_tags"] == ["current"]
        assert meta["tags"] == ["specific", "current"]


def test_bundle_rewrite_keeps_generation_until_file_replace(pipeline_env, monkeypatch):
    from app.services import document_tag_service as tags
    from app.services.generation_cleanup import cleanup_document_generations

    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    monkeypatch.setattr(tags, "get_settings", lambda: pipeline.settings)
    triggered, completed = Event(), Event()
    original = tags.os.replace
    observed = []

    def replace(source, target):
        if str(source).endswith(".md.tmp"):
            triggered.set()
            observed.append(completed.wait(0.5))
        return original(source, target)

    def publisher():
        assert triggered.wait(5)
        pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))
        cleanup_document_generations(pipeline.settings, pipeline.vector_store, DOC_ID)
        completed.set()

    monkeypatch.setattr(tags.os, "replace", replace)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publisher)
        tags._rewrite_bundle_frontmatter(DOC_ID, [], set(), [])
        future.result(timeout=10)
    assert observed and not any(observed)


@pytest.mark.parametrize("projection", ["locale", "development"])
def test_metadata_sync_cannot_overwrite_new_publication(pipeline_env, monkeypatch, projection):
    from app.services import dev_sync, source_locale_sync
    from app.services.development_registry import get_development_registry

    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    if projection == "locale":
        pipeline.registry.update(DOC_ID, source_locale="en", source_locale_source="manual")
        new_fields = {"source_locale": "ru", "source_locale_source": "manual"}
        module, invoke = source_locale_sync, source_locale_sync.reindex_document_source_locale
        method, argument, payload_key, expected = "set_document_source_locale_payload", "en", "source_locale", "ru"
    else:
        registry = get_development_registry()
        first = registry.create("111", "Old")
        second = registry.create("222", "New")
        pipeline.registry.update(DOC_ID, development_id=first["id"])
        new_fields = {"development_id": second["id"]}
        module, invoke = dev_sync, dev_sync.reindex_document_dev_tags
        method, argument, payload_key, expected = "reindex_document_dev_tags", ["111", "Old"], "dev_tags", ["222", "New"]
    triggered, completed = Event(), Event()
    original = getattr(pipeline.vector_store, method)

    def delayed_write(*args, **kwargs):
        triggered.set()
        completed.wait(0.5)
        return original(*args, **kwargs)

    def publisher():
        assert triggered.wait(5)
        pipeline.registry.update(DOC_ID, **new_fields)
        pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))
        completed.set()

    monkeypatch.setattr(module, "VectorStore", lambda: pipeline.vector_store)
    monkeypatch.setattr(pipeline.vector_store, method, delayed_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(publisher)
        synced = invoke(DOC_ID, argument)
        future.result(timeout=10)
    assert synced is True
    points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    current = [point for point in points if point.payload.get("generation_id") == candidate]
    assert current and all(point.payload[payload_key] == expected for point in current)


def test_delayed_locale_sync_uses_current_value(pipeline_env, monkeypatch):
    from app.services import source_locale_sync

    pipeline, _source, _write = pipeline_env
    pipeline.registry.update(DOC_ID, source_locale="ru", source_locale_source="manual")
    monkeypatch.setattr(source_locale_sync, "VectorStore", lambda: pipeline.vector_store)
    assert source_locale_sync.reindex_document_source_locale(DOC_ID, "en") is True
    points, _ = pipeline.vector_store.client.scroll(pipeline.vector_store.collection, limit=100)
    assert points and all(point.payload["source_locale"] == "ru" for point in points)
