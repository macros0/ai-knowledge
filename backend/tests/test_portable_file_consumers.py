"""Consumers must read approved bytes on a worker with no generation cache."""
import shutil

import pytest

from app.db.models import Document
from app.db.session import session_scope
from app.services.artifacts import get_blob_store
from app.services.artifacts.repository import attach_blob
from app.services.generation_publication import publish_prepared_document
from tests.test_generation_pipeline import DOC_ID, pipeline_env as pipeline_env
from tests.test_generation_cleanup import _prepare_second
from tests.test_portable_staging import portable as portable


@pytest.fixture
def cold_published(pipeline_env, monkeypatch, tmp_path):
    pipeline, source, _ = pipeline_env
    settings = pipeline.settings
    settings.artifact_uploads_enabled = True
    settings.artifact_root = tmp_path / "durable"
    pipeline, generation = _prepare_second(pipeline_env, monkeypatch)
    assert publish_prepared_document(settings, pipeline.vector_store, DOC_ID, generation)
    with source.open("rb") as stream:
        from uuid import uuid4
        ref = get_blob_store(settings).put(stream, object_id=uuid4().hex, media_type="message/rfc822")
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=DOC_ID, generation_id=None, logical_name="original")
        session.get(Document, DOC_ID).file_hash = ref.sha256
    for path in (settings.uploads_dir / DOC_ID, settings.okf_dir / DOC_ID):
        shutil.rmtree(path)
    source.unlink()
    settings.data_dir = tmp_path / "worker-b"
    return pipeline, generation, ref


def test_okf_export_restores_registered_attachments(cold_published, tmp_path):
    from app.services.export_okf import export_okf_bundle
    files = export_okf_bundle(DOC_ID, tmp_path / "export")
    attachments = [name for name in files if name.startswith("attachments/")]
    assert attachments, "cold export must include registered binary attachments"
    assert all((tmp_path / "export" / name).stat().st_size for name in attachments)


def test_canonical_repair_restores_approved_sidecars_and_attachments(cold_published):
    from tests.test_canonical_repair import _repair
    pipeline, previous, _ = cold_published
    result = _repair(pipeline)
    assert result["changed"] and result["generation_id"] != previous


def test_scope_base_reads_bound_root_on_cold_worker(cold_published):
    from app.services.processing_scope_change import load_scope_base
    _, generation, ref = cold_published
    base = load_scope_base(DOC_ID)
    assert base.generation_id == generation
    assert base.current_file_hash == ref.sha256


def test_missing_registered_bytes_block_okf_export(cold_published, tmp_path):
    from app.services.export_okf import export_okf_bundle
    from app.services.generation_artifacts import GenerationIntegrityError
    from app.services.artifacts.repository import resolve_blob
    pipeline, generation, _ = cold_published
    with session_scope() as session:
        ref = resolve_blob(session, owner_type="generation", owner_id=DOC_ID,
                           generation_id=generation, logical_name="publication.json")
    (pipeline.settings.artifact_root / ref.key).unlink()
    with pytest.raises(GenerationIntegrityError):
        export_okf_bundle(DOC_ID, tmp_path / "export")


@pytest.mark.parametrize("field,value", [("phase", "retired"), ("publication_version", 1), ("publication_hash", "0" * 64)])
def test_transaction_hydration_rejects_stale_metadata_before_provider(cold_published, monkeypatch, field, value):
    from app.db.models import DocumentGeneration
    from app.services.artifacts.publication import hydrate_publication
    from app.services.generation_artifacts import GenerationIntegrityError
    pipeline, generation, _ = cold_published
    with session_scope() as session:
        setattr(session.get(DocumentGeneration, generation), field, value)
    monkeypatch.setattr("app.services.artifacts.get_blob_store", lambda *_a: pytest.fail("provider accessed before owner check"))
    with session_scope(), pytest.raises(GenerationIntegrityError):
        hydrate_publication(pipeline.settings, DOC_ID, generation)


def test_review_confirmation_restores_bound_original_on_cold_api(cold_published, monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4
    from app.services.processing_scope import create_review
    from docparser.attachment_policy import AttachmentInventory
    import app.services.processing_admission as admission
    pipeline, generation, ref = cold_published
    started = []
    monkeypatch.setattr(admission, "get_settings", lambda: pipeline.settings)
    monkeypatch.setattr(admission, "get_registry", lambda: pipeline.registry)
    monkeypatch.setattr(admission, "get_pipeline", lambda: SimpleNamespace(regenerate=lambda *a, **_k: started.append(a[0])))
    with session_scope() as session:
        review = create_review(session, DOC_ID, AttachmentInventory(source_file_hash=ref.sha256),
                               base_generation_id=generation, actor="editor")
        review_id, revision = review.id, review.revision
    result = admission.confirm_processing_scope(DOC_ID, review_id, revision, uuid4().hex, actor="editor")
    assert result.status == "accepted" and started == [DOC_ID]


def test_update_capture_restores_cold_active_publication(cold_published):
    from app.db.models import DocumentUpdateAttempt
    from app.services.document_update import capture_published_update
    pipeline, _, _ = cold_published
    with session_scope() as session:
        session.query(DocumentUpdateAttempt).filter_by(doc_id=DOC_ID).delete()
        session.get(Document, DOC_ID).status = "paused"
        assert capture_published_update(session, DOC_ID, pipeline.settings)
        assert session.get(DocumentUpdateAttempt, DOC_ID).previous_fields["total_chunks"] == 1


def test_staging_chunk_api_restores_committed_text_after_cache_loss(portable):
    from app.api.documents import get_chunk
    from tests.test_portable_staging import DOC, prepare
    stage, _ = portable
    prepare(stage)
    shutil.rmtree(stage.dir)
    response = get_chunk(DOC, 0)
    assert response.body.decode("utf8") == "Исходный текст таблицы\n"


def test_table_import_restores_approved_jsonl_on_cold_worker(pipeline_env, tmp_path):
    import hashlib
    from openpyxl import Workbook
    from app.db.models import DocumentGenerationState, DocumentTableRow
    from app.services.generation_files import generation_paths
    from app.services.generation_store import begin_generation
    from app.services.table_store import import_generation_tables
    pipeline, _, _ = pipeline_env
    settings = pipeline.settings
    settings.artifact_uploads_enabled = True
    settings.artifact_root = tmp_path / "durable"
    settings.table_structure_enabled = True
    settings.okf_table_llm_classify = False
    settings.llm_chunk_retry_attempts = 1
    source = settings.uploads_dir / (DOC_ID + ".xlsx")
    workbook = Workbook()
    sheet = workbook.active
    for row in (("Code", "Value"), ("first", 7), ("second", 9)):
        sheet.append(row)
    workbook.save(source)
    with session_scope() as session:
        document = session.get(Document, DOC_ID)
        document.filename = "table.xlsx"
        document.file_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    pipeline._process(DOC_ID, source, "table.xlsx", [], resume=False)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        generation = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        assert session.query(DocumentTableRow).filter_by(generation_id=generation).count() == 3
        target = begin_generation(session, DOC_ID).id
    old = generation_paths(settings, DOC_ID, generation)
    shutil.rmtree(old.bundle)
    shutil.rmtree(old.uploads_root)
    settings.data_dir = tmp_path / "worker-b"
    import_generation_tables(DOC_ID, target, old.bundle)
    with session_scope() as session:
        rows = session.query(DocumentTableRow).filter_by(generation_id=target).order_by(DocumentTableRow.row_number).all()
        assert [row.cells["2"]["raw_value"] for row in rows] == ["Value", 7, 9]


def test_portable_document_does_not_downgrade_when_upload_preview_is_disabled(pipeline_env, tmp_path):
    from app.db.models import DocumentGeneration, DocumentGenerationState
    pipeline, source, write = pipeline_env
    pipeline.settings.artifact_uploads_enabled = True
    pipeline.settings.artifact_root = tmp_path / "durable"
    write("First portable publication")
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    pipeline.settings.artifact_uploads_enabled = False
    write("Portable data must remain portable")
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        assert session.get(DocumentGeneration, active).publication_version == 2
