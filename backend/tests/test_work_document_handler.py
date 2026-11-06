"""Run the existing pipeline under a durable document claim."""
from uuid import uuid4

import pytest

from app.db.models import DocumentGeneration, DocumentGenerationState, WorkItem
from app.db.session import session_scope
from app.services.work.models import WorkSpec
from app.services.work.store import enqueue, claim
from app.services.work.document_handler import run_document_job
from tests.test_generation_pipeline import DOC_ID, pipeline_env as pipeline_env


def durable_original(pipeline_env, tmp_path):
    from app.services.artifacts import get_blob_store
    from app.services.artifacts.repository import attach_blob
    pipeline, source, _ = pipeline_env
    pipeline.settings.artifact_uploads_enabled = True
    pipeline.settings.artifact_root = tmp_path / "durable"
    with source.open("rb") as stream:
        ref = get_blob_store(pipeline.settings).put(stream, object_id=uuid4().hex, media_type="message/rfc822")
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=DOC_ID, generation_id=None, logical_name="original")
    return ref


def admitted(ref, action="regenerate"):
    with session_scope() as session:
        enqueue(session, WorkSpec("document", 1, {"action": action, "doc_id": DOC_ID, "source_file_hash": ref.sha256}, "document-execution", DOC_ID))
        return claim(session, owner_id="worker", supported={"document": {1}}, lease_seconds=120)


def test_document_handler_publishes_owned_attempt_and_acknowledges_atomically(pipeline_env, tmp_path, monkeypatch):
    pipeline, _, _ = pipeline_env
    ref = durable_original(pipeline_env, tmp_path)
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    work = admitted(ref)
    run_document_job(work)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        generation = session.get(DocumentGeneration, active)
        assert (generation.work_id, generation.work_fence) == (work.work_id, work.fence)
        assert session.get(WorkItem, work.work_id).status == "succeeded"
    assert pipeline.registry.get(DOC_ID)["status"] == "done"


def test_default_runtime_dispatches_document_without_http_app(pipeline_env, tmp_path, monkeypatch):
    from app.services.work.runtime import build_handlers
    pipeline, _, _ = pipeline_env
    ref = durable_original(pipeline_env, tmp_path)
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    work = admitted(ref)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.get(WorkItem, work.work_id).status == "succeeded"
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id is not None


def test_successful_work_retires_only_its_checkpoint_durably(pipeline_env, tmp_path, monkeypatch):
    from app.db.models import DocumentStaging
    from app.services.work.runtime import build_handlers
    pipeline, _, _ = pipeline_env
    ref = durable_original(pipeline_env, tmp_path)
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    run_document_job(admitted(ref))
    with session_scope() as session:
        stage = session.get(DocumentStaging, DOC_ID)
        intent = session.query(WorkItem).filter_by(kind="checkpoint_retire").one()
        assert intent.payload_json == {"doc_id": DOC_ID, "generation_id": stage.generation_id}
        maintenance = claim(session, owner_id="maintenance", supported={"checkpoint_retire": {1}}, lease_seconds=120)
    build_handlers().resolve(maintenance.spec)(maintenance)
    with session_scope() as session:
        assert session.get(DocumentStaging, DOC_ID) is None


def test_document_handler_reuses_checkpoint_on_cold_new_attempt(pipeline_env, tmp_path, monkeypatch):
    from app.services.errors import VectorStoreError
    from app.services.staging import StagingStore
    pipeline, source, write = pipeline_env
    write("Accepted checkpoint from a failed indexing attempt.")
    ref = durable_original(pipeline_env, tmp_path)
    original_index = pipeline.vector_store.index_chunks
    def fail(*_args, **_kwargs):
        raise VectorStoreError("index unavailable")
    monkeypatch.setattr(pipeline.vector_store, "index_chunks", fail)
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    old_generation = StagingStore(DOC_ID).generation_id
    assert pipeline.registry.get(DOC_ID)["status"] == "paused"
    work = admitted(ref, "resume")
    pipeline.settings.data_dir = tmp_path / "cold-worker"
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    monkeypatch.setattr(pipeline.vector_store, "index_chunks", original_index)
    monkeypatch.setattr(pipeline.okf_generator, "generate_chunk", lambda *_a, **_k: pytest.fail("Accepted LLM output must not be regenerated"))
    run_document_job(work)
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id != old_generation
        assert session.get(WorkItem, work.work_id).status == "succeeded"
    assert pipeline.registry.get(DOC_ID)["status"] == "done"


@pytest.mark.parametrize("action", ["resume", "regenerate"])
def test_future_api_pipeline_actions_enqueue_without_local_executor(pipeline_env, tmp_path, monkeypatch, action):
    pipeline, _, _ = pipeline_env
    ref = durable_original(pipeline_env, tmp_path)
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "api"
    if action == "resume":
        # Seed diagnostics for the fixture's pre-portable active publication.
        pipeline.settings.app_role = "combined"
        from app.services.document_update import capture_published_update
        with session_scope() as session:
            capture_published_update(session, DOC_ID, pipeline.settings)
        pipeline.settings.app_role = "api"
        pipeline.registry.update(DOC_ID, status="paused")
    monkeypatch.setattr(pipeline, "_start", lambda *_a, **_k: pytest.fail("API must enqueue instead of starting local document execution"))
    getattr(pipeline, action)(DOC_ID)
    with session_scope() as session:
        work = session.query(WorkItem).filter_by(kind="document").one()
        assert work.payload_json == {"doc_id": DOC_ID, "action": action, "source_file_hash": ref.sha256}
    assert pipeline.registry.get(DOC_ID)["status"] == "queued"


def test_worker_uses_frozen_admission_scope(pipeline_env, tmp_path, monkeypatch):
    from copy import deepcopy
    from app.services.work.admission import enqueue_existing_document
    from app.services.processing_admission import prepare_upload_scope
    from app.services.processing_scope import automatic_snapshot
    pipeline, path, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    inventory, _ = prepare_upload_scope(path, "decision.eml", pipeline.settings, attachments_dir=tmp_path / "assessment")
    scope = automatic_snapshot(inventory)
    accepted = deepcopy(scope)
    enqueue_existing_document(pipeline.settings, DOC_ID, "regenerate", processing_scope=scope)
    scope["selection_hash"] = "0" * 64
    with session_scope() as session:
        work = claim(session, owner_id="worker", supported={"document": {1}}, lease_seconds=120)
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    run_document_job(work)
    with session_scope() as session:
        generation = session.get(DocumentGeneration, session.get(DocumentGenerationState, DOC_ID).active_generation_id)
        from app.services.work.admission import scope_policy
        from app.services.processing_scope_cache import embedding_fingerprint
        assert scope_policy(generation.processing_scope_json) == accepted
        assert generation.processing_scope_json["vector_space"] == {"kind": "proven", "fingerprint": embedding_fingerprint(pipeline.settings)}


def test_cancel_queued_update_invalidates_worker_and_preserves_publication(pipeline_env, tmp_path):
    from app.services.work.admission import enqueue_existing_document
    from app.services.work.context import document_execution
    from app.services.work.models import LostOwnership
    pipeline, _, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
    enqueue_existing_document(pipeline.settings, DOC_ID, "regenerate")
    with session_scope() as session:
        work = claim(session, owner_id="worker", supported={"document": {1}}, lease_seconds=120)
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "api"
    pipeline.cancel_update(DOC_ID, expected_update_id=pipeline.registry.get(DOC_ID)["update_id"])
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id == active
    with pytest.raises(LostOwnership), document_execution(work):
        pipeline.registry.update(DOC_ID, status="processing")


def test_admission_captures_published_state_before_queuing(pipeline_env, tmp_path):
    from app.db.models import DocumentUpdateAttempt
    from app.services.work.admission import enqueue_existing_document
    pipeline, _, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    before = pipeline.registry.get(DOC_ID)
    enqueue_existing_document(pipeline.settings, DOC_ID, "regenerate")
    with session_scope() as session:
        attempt = session.get(DocumentUpdateAttempt, DOC_ID)
        assert attempt is not None and attempt.previous_fields["okf_concept_count"] == before["okf_concept_count"]


def test_delete_restore_of_queued_work_restores_a_resumable_state(pipeline_env, tmp_path):
    from app.services.work.admission import enqueue_existing_document
    pipeline, _, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    enqueue_existing_document(pipeline.settings, DOC_ID, "regenerate")
    pipeline.registry.soft_delete(DOC_ID)
    pipeline.registry.restore(DOC_ID)
    assert pipeline.registry.get(DOC_ID)["status"] == "paused"
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="document").one().cancel_requested


def test_soft_delete_retains_accepted_update_checkpoint_for_restore(pipeline_env, tmp_path, monkeypatch):
    from app.services.errors import VectorStoreError
    from app.services.staging import StagingStore
    from app.services.work.handlers import TransientWorkError
    pipeline, _, _ = pipeline_env
    work = admitted(durable_original(pipeline_env, tmp_path))
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    def fail(*_a, **_k):
        raise VectorStoreError("injected indexing failure")
    monkeypatch.setattr(pipeline.vector_store, "index_chunks", fail)
    with pytest.raises(TransientWorkError, match="storage_unavailable"):
        run_document_job(work)
    stage = StagingStore(DOC_ID)
    before = stage.load()
    assert before and stage.processed_chunks
    pipeline.registry.soft_delete(DOC_ID)
    pipeline.registry.restore(DOC_ID)
    assert pipeline.registry.get(DOC_ID)["status"] == "paused"
    assert stage.load() == before


def test_process_receipt_replay_does_not_requeue_completed_document(pipeline_env, tmp_path):
    from app.services.work.store import finish
    pipeline, source, _ = pipeline_env
    durable_original(pipeline_env, tmp_path)
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "api"
    pipeline.ingest(DOC_ID, source, "decision.eml")
    with session_scope() as session:
        work = claim(session, owner_id="worker", supported={"document": {1}}, lease_seconds=120)
        finish(session, work, outcome="succeeded")
    pipeline.registry.update(DOC_ID, status="done")
    pipeline.ingest(DOC_ID, source, "decision.eml")
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="document").count() == 1


def test_distributed_startup_does_not_reset_foreign_document_work(pipeline_env):
    from app.services.work.context import document_execution
    from app.services.work.document_handler import prepare_document_attempt
    from tests.test_work_document_publication import document_claim
    pipeline, _, _ = pipeline_env
    work = document_claim()
    with document_execution(work), session_scope() as session:
        generation_id = prepare_document_attempt(session, work, pipeline.settings).id
        from app.db.models import Document
        session.get(Document, DOC_ID).status = "processing"
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "api"
    pipeline.registry.reset_stale_statuses()
    assert pipeline.registry.get(DOC_ID)["status"] == "processing"
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id == generation_id


def test_cold_xlsx_resume_preserves_rows_and_accepted_concepts(pipeline_env, tmp_path, monkeypatch):
    from openpyxl import Workbook
    from app.db.models import DocumentTableRow, OkfConcept
    from app.services.artifacts import get_blob_store
    from app.services.errors import VectorStoreError
    from app.services.staging import StagingStore
    from app.services.work.admission import enqueue_existing_document
    pipeline, _, _ = pipeline_env
    settings = pipeline.settings
    settings.artifact_uploads_enabled, settings.okf_table_llm_classify = True, False
    settings.artifact_root = tmp_path / "xlsx-blobs"
    source = tmp_path / "table.xlsx"
    book = Workbook()
    book.active.append(["Code", "Value"])
    book.active.append(["A", 7])
    book.active.append(["B", 0])
    book.save(source)
    with source.open("rb") as stream:
        ref = get_blob_store(settings).put(stream, object_id=uuid4().hex, media_type="application/octet-stream")
    doc_id = "b" * 16
    pipeline.registry.create(doc_id, "table.xlsx", "application/octet-stream", source.stat().st_size, artifact_ref=ref)
    original_index = pipeline.vector_store.index_chunks
    def fail(*_a, **_k):
        raise VectorStoreError("injected indexing failure")
    monkeypatch.setattr(pipeline.vector_store, "index_chunks", fail)
    pipeline._process(doc_id, source, "table.xlsx", [], resume=False)
    assert pipeline.registry.get(doc_id)["status"] == "paused"
    stage = StagingStore(doc_id)
    source_generation = stage.generation_id
    accepted = [(concept.id, concept.content) for concept in stage.concepts()]
    with session_scope() as session:
        before = [(row.row_number, row.cells) for row in session.query(DocumentTableRow).filter_by(doc_id=doc_id, generation_id=source_generation).order_by(DocumentTableRow.row_number)]
    enqueue_existing_document(settings, doc_id, "resume")
    with session_scope() as session:
        work = claim(session, owner_id="cold-worker", supported={"document": {1}}, lease_seconds=120)
    settings.data_dir = tmp_path / "cold-xlsx-worker"
    monkeypatch.setattr(pipeline.vector_store, "index_chunks", original_index)
    monkeypatch.setattr(pipeline.okf_generator, "generate_chunk", lambda *_a, **_k: pytest.fail("Accepted table result must be reused"))
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    run_document_job(work)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, doc_id).active_generation_id
        after = [(row.row_number, row.cells) for row in session.query(DocumentTableRow).filter_by(doc_id=doc_id, generation_id=active).order_by(DocumentTableRow.row_number)]
        concepts = list(session.query(OkfConcept).filter_by(doc_id=doc_id))
        assert active != source_generation and before == after and before
        assert sorted(row.content for row in concepts) == sorted(content for _, content in accepted)
        assert all(ref["generation_id"] == active for row in concepts for ref in row.table_refs)
    assert [(concept.id, concept.content) for concept in StagingStore(doc_id).concepts()] == accepted


def test_unavailable_original_requeues_same_work_and_preserves_old_publication(pipeline_env, tmp_path, monkeypatch):
    from datetime import timedelta
    from threading import Event
    from app.db.models import OkfConcept
    from app.services.artifacts import original as originals
    from app.services.artifacts.contract import BlobUnavailable
    from app.services.work.handlers import HandlerRegistry
    from app.services.work.store import _now
    from app.services.work.worker import Worker

    pipeline, _, _ = pipeline_env
    ref = durable_original(pipeline_env, tmp_path)
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    registry = HandlerRegistry()
    registry.register("document", 1, run_document_job)
    pipeline.settings.work_retry_backoff_seconds = (1, 1, 1)
    runner = Worker(pipeline.settings, registry=registry, owner_id="storage-retry-worker")
    with session_scope() as session:
        old_active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        old_concepts = [(v.slug, v.content) for v in session.query(OkfConcept).filter_by(doc_id=DOC_ID).order_by(OkfConcept.slug)]
        work_id = enqueue(session, WorkSpec("document", 1, {"action": "regenerate", "doc_id": DOC_ID,
            "source_file_hash": ref.sha256}, "unavailable-original", DOC_ID))
    original_path = originals.original_path
    calls = []
    def temporary_failure(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise BlobUnavailable("S3 returned an opaque temporary server failure")
        return original_path(*args, **kwargs)
    monkeypatch.setattr(originals, "original_path", temporary_failure)

    assert runner.run_once(Event())
    with session_scope() as session:
        work = session.get(WorkItem, work_id)
        assert (work.status, work.attempts, work.fence, work.owner_id) == ("queued", 1, 1, None)
        assert work.error_code == "storage_unavailable"
        assert session.get(DocumentGenerationState, DOC_ID).active_generation_id == old_active
        assert [(v.slug, v.content) for v in session.query(OkfConcept).filter_by(doc_id=DOC_ID).order_by(OkfConcept.slug)] == old_concepts
        # Advance retry eligibility only in this isolated SQLite test database.
        work.available_at = _now(session) - timedelta(seconds=1)
    assert pipeline.registry.get(DOC_ID)["error_code"] == "processing_unavailable"

    assert runner.run_once(Event())
    with session_scope() as session:
        work = session.get(WorkItem, work_id)
        assert (work.status, work.attempts, work.fence, work.owner_id) == ("succeeded", 2, 2, None)
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        assert active != old_active
        assert session.get(DocumentGeneration, active).work_id == work_id
        assert session.get(DocumentGeneration, old_active).phase == "retired"
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
