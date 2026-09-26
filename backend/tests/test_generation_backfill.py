"""Read-time backfill cannot replace a published or concurrently prepared version."""
import pytest
from docparser import Block

from app.db.models import DocumentChunk, DocumentGeneration, DocumentGenerationState
from app.db.session import session_scope
from app.services.staging import StagingStore
from tests.test_generation_cleanup import _prepare_second
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def test_backfill_refuses_to_reparse_an_already_published_generation(pipeline_env, monkeypatch):
    pipeline, _source, _write = pipeline_env

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Published data must not be reparsed during a read")

    monkeypatch.setattr("app.services.pipeline.parse_document", forbidden)
    pipeline._backfill_chunks(DOC_ID)


def test_publication_during_legacy_backfill_discards_text_and_private_files(pipeline_env, monkeypatch):
    pipeline, candidate = _prepare_second(pipeline_env, monkeypatch)
    with session_scope() as session:
        state = session.get(DocumentGenerationState, DOC_ID)
        session.get(DocumentGeneration, state.active_generation_id).phase = "retired"
        state.active_generation_id = None
        state.candidate_generation_id = None
        session.get(DocumentGeneration, candidate).base_generation_id = None
        session.query(DocumentChunk).filter_by(doc_id=DOC_ID).delete()
    expected = {}
    temporary = []

    def parse(_path, _filename, *, attachments_dir, **_kwargs):
        attachments_dir.mkdir(parents=True, exist_ok=True)
        extra = attachments_dir / "temporary-only.bin"
        extra.write_bytes(b"unpublished")
        temporary.append(extra)
        with session_scope() as session:
            session.get(DocumentGenerationState, DOC_ID).candidate_generation_id = candidate
        pipeline._publish_prepared_generation(DOC_ID, candidate, StagingStore(DOC_ID))
        expected["snapshot"] = _published_snapshot(pipeline)
        with session_scope() as session:
            expected["chunks"] = [row.content for row in session.query(DocumentChunk).filter_by(doc_id=DOC_ID)]
        return [Block("paragraph", "Stale read-time backfill")]

    monkeypatch.setattr("app.services.pipeline.parse_document", parse)
    pipeline._backfill_chunks(DOC_ID)
    assert _published_snapshot(pipeline) == expected["snapshot"]
    with session_scope() as session:
        assert [row.content for row in session.query(DocumentChunk).filter_by(doc_id=DOC_ID)] == expected["chunks"]
    assert temporary and all(not path.exists() for path in temporary)


def test_legacy_backfill_does_not_overwrite_existing_attachment(pipeline_env, monkeypatch):
    pipeline, source, _write = pipeline_env
    legacy = "legacybackfill01"
    (pipeline.settings.uploads_dir / f"{legacy}.eml").write_bytes(source.read_bytes())
    pipeline.registry.create(legacy, "legacy.eml", "message/rfc822", source.stat().st_size)
    existing = pipeline.settings.uploads_dir / legacy / "attachments" / "old.bin"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"original")

    def parse(_path, _filename, *, attachments_dir, **_kwargs):
        (attachments_dir / "old.bin").write_bytes(b"changed")
        return [Block("paragraph", "Changed text")]

    monkeypatch.setattr("app.services.pipeline.parse_document", parse)
    with pytest.raises(ValueError):
        pipeline._backfill_chunks(legacy)
    assert existing.read_bytes() == b"original"
    with session_scope() as session:
        assert session.query(DocumentChunk).filter_by(doc_id=legacy).count() == 0


@pytest.mark.parametrize("change", ["candidate", "trash", "purge", "chunks"])
def test_backfill_discards_parse_when_document_changes(pipeline_env, monkeypatch, change):
    from app.services.generation_store import begin_generation

    pipeline, source, _write = pipeline_env
    legacy = "legacyrace01"
    (pipeline.settings.uploads_dir / f"{legacy}.eml").write_bytes(source.read_bytes())
    pipeline.registry.create(legacy, "legacy.eml", "message/rfc822", source.stat().st_size)
    temporary = []

    def parse(_path, _filename, *, attachments_dir, **_kwargs):
        artifact = attachments_dir / "discard.bin"
        artifact.write_bytes(b"unpublished")
        temporary.append(artifact)
        if change == "trash":
            pipeline.registry.soft_delete(legacy)
        elif change == "purge":
            pipeline.registry.delete(legacy)
        else:
            with session_scope() as session:
                if change == "candidate":
                    begin_generation(session, legacy)
                else:
                    session.add(DocumentChunk(doc_id=legacy, chunk_index=0, content="Winner"))
        return [Block("paragraph", "Stale backfill")]

    monkeypatch.setattr("app.services.pipeline.parse_document", parse)
    pipeline._backfill_chunks(legacy)
    with session_scope() as session:
        rows = session.query(DocumentChunk).filter_by(doc_id=legacy).all()
        assert [row.content for row in rows] == (["Winner"] if change == "chunks" else [])
    assert not (pipeline.settings.uploads_dir / legacy / "attachments" / "discard.bin").exists()
    assert temporary and all(not artifact.exists() for artifact in temporary)


def test_legacy_backfill_retry_after_sql_failure_preserves_source_paths(pipeline_env, monkeypatch):
    from app.db.models import DocumentSource

    pipeline, source, _write = pipeline_env
    legacy = "legacyretry01"
    (pipeline.settings.uploads_dir / f"{legacy}.eml").write_bytes(source.read_bytes())
    pipeline.registry.create(legacy, "legacy.eml", "message/rfc822", source.stat().st_size)

    def failed_write(*_args):
        raise RuntimeError("SQL write failed")

    with monkeypatch.context() as patch:
        patch.setattr("app.services.pipeline.replace_chunks", failed_write)
        with pytest.raises(RuntimeError, match="SQL write failed"):
            pipeline._backfill_chunks(legacy)
    with session_scope() as session:
        assert session.query(DocumentSource).filter_by(doc_id=legacy).count() == 0
        assert session.query(DocumentChunk).filter_by(doc_id=legacy).count() == 0
    root = pipeline.settings.uploads_dir / legacy
    before = {path: path.read_bytes() for path in (root / "attachments").rglob("*") if path.is_file()}
    assert before
    pipeline._backfill_chunks(legacy)
    assert all(path.read_bytes() == data for path, data in before.items())
    with session_scope() as session:
        sources = session.query(DocumentSource).filter_by(doc_id=legacy).all()
        assert {row.source_id for row in sources} == {"root", "root/0"}
        attachment = next(row for row in sources if row.source_id == "root/0")
        assert attachment.saved_path.startswith("attachments/")
        assert (root / attachment.saved_path).is_file()
        chunks = session.query(DocumentChunk).filter_by(doc_id=legacy).all()
        assert chunks and {row.source_id for row in chunks} <= {row.source_id for row in sources}
    assert not list(pipeline.settings.staging_dir.glob("chunk-backfill-*"))
