"""Trusted repairs retain the active corpus until fenced publication succeeds."""
import shutil

import pytest

from app.db.models import DocumentGeneration, DocumentGenerationState, OkfConcept
from app.db.session import session_scope, current_session
from app.services.work.models import LostOwnership
from app.services.work.runtime import build_handlers
from app.services.work.store import claim
from tests.test_generation_pipeline import DOC_ID, pipeline_env as pipeline_env
from tests.test_work_document_handler import durable_original
from tests.test_work_store import expire


@pytest.fixture
def repair_environment(pipeline_env, tmp_path, monkeypatch):
    pipeline, source, _ = pipeline_env
    pipeline.settings.data_dir = tmp_path / "worker-a"
    durable_original(pipeline_env, tmp_path)
    pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        concept = session.query(OkfConcept).filter_by(doc_id=DOC_ID).first()
        concept.content += "\n(файл: C:/private/attachments/data.bin)"
    pipeline.settings.okf_deployment_schema, pipeline.settings.app_role = "replicas-v1", "api"
    monkeypatch.setattr("app.services.pipeline.get_pipeline", lambda: pipeline)
    return pipeline


def admit_repair(pipeline):
    from app.services.work.repair import admit_canonical_repair
    return admit_canonical_repair(pipeline.settings, DOC_ID, "attachment_paths")


def take():
    with session_scope() as session:
        return claim(session, owner_id="repair-worker", supported={"canonical_repair": {1}}, lease_seconds=120)


def test_repair_admission_freezes_base_without_creating_a_candidate(repair_environment):
    pipeline = repair_environment
    receipt = admit_repair(pipeline)
    with session_scope() as session:
        state = session.get(DocumentGenerationState, DOC_ID)
        assert state.candidate_generation_id is None
        base = state.active_generation_id
    work = take()
    assert work.work_id == receipt["work_id"]
    assert work.spec.payload["base_generation_id"] == base


def test_repair_rejects_payload_selected_callable(repair_environment):
    from app.services.work.repair import admit_canonical_repair
    with pytest.raises(ValueError):
        admit_canonical_repair(repair_environment.settings, DOC_ID, "os.system")


def test_repair_embeds_outside_sql_and_publishes_owned_generation(repair_environment):
    pipeline = repair_environment
    admit_repair(pipeline)
    work = take()
    embed = pipeline.embedder.embed_texts
    def checked(texts):
        assert current_session() is None, "A provider call must not hold SQL ownership locks"
        return embed(texts)
    pipeline.embedder.embed_texts = checked
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        from app.db.models import WorkItem
        state = session.get(DocumentGenerationState, DOC_ID)
        generation = session.get(DocumentGeneration, state.active_generation_id)
        assert generation.work_id == work.work_id and generation.work_fence == work.fence
        assert session.get(WorkItem, work.work_id).status == "succeeded"
        assert all("C:/private/" not in row.content for row in session.query(OkfConcept).filter_by(doc_id=DOC_ID))


def test_repair_reclaim_reuses_accepted_embeddings_after_warm_loss(repair_environment, tmp_path, monkeypatch):
    from app.services.errors import VectorStoreError
    from app.services.work.handlers import TransientWorkError
    import app.services.canonical_repair as repairs
    pipeline = repair_environment
    admit_repair(pipeline)
    old = take()
    publish = repairs.publish_prepared_document
    def offline(*_a, **_k):
        raise VectorStoreError(cause=ConnectionError("publication unavailable"))
    monkeypatch.setattr(repairs, "publish_prepared_document", offline)
    with pytest.raises(TransientWorkError):
        build_handlers().resolve(old.spec)(old)
    with session_scope() as session:
        old_generation = session.get(DocumentGenerationState, DOC_ID).candidate_generation_id
        frozen = session.get(DocumentGeneration, old_generation).id
    expire(old.work_id)
    current = take()
    warm = pipeline.settings.data_dir.resolve()
    assert warm == (tmp_path / "worker-a").resolve() and warm.is_relative_to(tmp_path.resolve())
    assert not pipeline.settings.artifact_root.resolve().is_relative_to(warm)
    shutil.rmtree(warm)
    pipeline.settings.data_dir = tmp_path / "cold-repair-worker"
    pipeline.embedder.embed_texts = lambda *_a, **_k: pytest.fail("Accepted repair embeddings must be reused")
    monkeypatch.setattr(repairs, "publish_prepared_document", publish)
    build_handlers().resolve(current.spec)(current)
    with pytest.raises(LostOwnership):
        build_handlers().resolve(old.spec)(old)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        assert active != frozen
        assert session.get(DocumentGeneration, active).work_fence == current.fence


def test_changed_repair_input_fails_its_receipt_instead_of_reclaiming_forever(repair_environment):
    from threading import Event
    from app.db.models import WorkItem
    from app.services.work.worker import Worker
    pipeline = repair_environment
    receipt = admit_repair(pipeline)
    with session_scope() as session:
        session.query(OkfConcept).filter_by(doc_id=DOC_ID).first().content += "\nA newer user edit."
    runner = Worker(pipeline.settings, registry=build_handlers(), owner_id="stale-input-worker")
    assert runner.run_once(Event())
    with session_scope() as session:
        assert session.get(WorkItem, receipt["work_id"]).status == "failed"
        assert session.query(OkfConcept).filter_by(doc_id=DOC_ID).first().content.endswith("A newer user edit.")


@pytest.mark.parametrize("trashed", [False, True])
def test_terminal_repair_candidate_is_abandoned_by_maintenance(repair_environment, monkeypatch, trashed):
    from app.services.errors import VectorStoreError
    from app.services.work.handlers import TransientWorkError
    from app.services.work.models import WorkSpec
    from app.services.work.store import cancel, enqueue
    import app.services.canonical_repair as repairs
    pipeline = repair_environment
    admit_repair(pipeline)
    old = take()
    def offline(*_a, **_k):
        raise VectorStoreError(cause=ConnectionError("publication unavailable"))
    monkeypatch.setattr(repairs, "publish_prepared_document", offline)
    with pytest.raises(TransientWorkError):
        build_handlers().resolve(old.spec)(old)
    with session_scope() as session:
        candidate = session.get(DocumentGenerationState, DOC_ID).candidate_generation_id
        assert candidate
        cancel(session, old.work_id)
    if trashed:
        pipeline.registry.soft_delete(DOC_ID)
    expire(old.work_id)
    with session_scope() as session:
        enqueue(session, WorkSpec("repair_reconcile", 1, {}, "repair-reconcile"))
        current = claim(session, owner_id="repair-maintenance", supported={"repair_reconcile": {1}}, lease_seconds=120)
    build_handlers().resolve(current.spec)(current)
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id is None
        assert session.get(DocumentGeneration, candidate).phase == "abandoned"


def test_path_repair_cli_only_admits_future_work(repair_environment, monkeypatch):
    from scripts import fix_attachment_paths
    pipeline = repair_environment
    def forbidden(*_a, **_k):
        pytest.fail("The future CLI must enqueue, not construct providers or execute a repair")
    monkeypatch.setattr(fix_attachment_paths, "repair_published_document", forbidden)
    monkeypatch.setattr(fix_attachment_paths, "Embedder", forbidden)
    result = fix_attachment_paths.fix_rows(False, doc_id=DOC_ID, settings=pipeline.settings)
    assert result["enqueued"][0]["doc_id"] == DOC_ID


def test_comment_repair_cli_admits_without_reading_a_local_original(repair_environment, monkeypatch):
    from scripts import backfill_comment_concepts
    pipeline = repair_environment
    def forbidden(*_a, **_k):
        pytest.fail("The future CLI must not parse the original")
    monkeypatch.setattr(backfill_comment_concepts, "_read_comment_concepts", forbidden)
    result = backfill_comment_concepts.process_doc(DOC_ID, "decision.docx", pipeline.settings, None, None, None)
    assert result["status"] == "queued" and result["work_id"]


def test_owned_comment_repair_restores_real_docx_threads(repair_environment, tmp_path):
    from uuid import uuid4
    from app.services.artifacts import get_blob_store
    from app.services.work.repair import admit_canonical_repair
    from tests.test_backfill_comment_concepts import _make_thread_docx
    pipeline = repair_environment
    settings = pipeline.settings
    source = _make_thread_docx(tmp_path / "comments.docx")
    with source.open("rb") as stream:
        ref = get_blob_store(settings).put(stream, object_id=uuid4().hex, media_type="application/octet-stream")
    settings.okf_deployment_schema, settings.app_role = None, "combined"
    doc_id = "b" * 16
    pipeline.registry.create(doc_id, "comments.docx", "application/octet-stream", source.stat().st_size, artifact_ref=ref)
    pipeline._process(doc_id, source, "comments.docx", [], resume=False)
    assert pipeline.registry.get(doc_id)["status"] == "done"
    with session_scope() as session:
        reviews = [row for row in session.query(OkfConcept).filter_by(doc_id=doc_id) if "review" in (row.tags or [])]
        for row in reviews:
            session.delete(row)
    settings.okf_deployment_schema, settings.app_role, settings.parser_supervisor_enabled = "replicas-v1", "worker", True
    admit_canonical_repair(settings, doc_id, "comment_threads")
    work = take()
    pipeline.okf_generator.generate_chunk = lambda *_a, **_k: pytest.fail("Comment repair must not regenerate with an LLM")
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, doc_id).active_generation_id
        assert session.get(DocumentGeneration, active).work_id == work.work_id
        assert any("review" in (row.tags or []) for row in session.query(OkfConcept).filter_by(doc_id=doc_id))


def test_future_legacy_read_cannot_launch_a_parser_or_change_canonical_rows(repair_environment, monkeypatch):
    from app.services.errors import DomainError
    from app.db.models import Document, DocumentChunk
    pipeline = repair_environment
    legacy_id = "c" * 16
    with session_scope() as session:
        session.add(Document(id=legacy_id, filename="legacy.pdf", content_type="application/pdf", size=1, status="done"))
    monkeypatch.setattr("app.services.pipeline.original_path", lambda *_a, **_k: pytest.fail("Viewer started a legacy parse"))
    with pytest.raises(DomainError) as error:
        pipeline._backfill_chunks(legacy_id)
    assert error.value.code == "legacy_corpus_adoption_required"
    with session_scope() as session:
        assert session.query(DocumentChunk).filter_by(doc_id=legacy_id).count() == 0
