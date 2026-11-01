"""Scope changes are admitted by API and allocated only by an owned worker."""
from copy import deepcopy
import shutil
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.models import DocumentGeneration, DocumentGenerationState, DocumentProcessingReview, DocumentScopeChange, WorkItem
from app.db.session import session_scope
from app.services.artifacts import get_blob_store
from app.services.artifacts.repository import attach_blob
import app.services.processing_scope_jobs as jobs
from app.services.processing_scope import selection_hash, snapshot_from_review
from tests.test_processing_scope_change_end_to_end import source_environment as source_environment


@pytest.fixture
def future_scope(source_environment, tmp_path, monkeypatch):
    env = source_environment
    settings = env["settings"]
    previous = env["root_file"]
    settings.data_dir = tmp_path / "worker-a"
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    env["root_file"] = settings.uploads_dir / previous.name
    shutil.copyfile(previous, env["root_file"])
    settings.artifact_root = tmp_path / "durable"
    with env["root_file"].open("rb") as stream:
        ref = get_blob_store(settings).put(stream, object_id=uuid4().hex, media_type="application/octet-stream")
    assert ref.sha256 == env["root_hash"]
    with session_scope() as session:
        attach_blob(session, ref=ref, owner_type="original", owner_id=env["doc_id"], generation_id=None, logical_name="original")
        chosen = deepcopy(env["decisions"])
        chosen[0]["effective_included"] = False
        review = DocumentProcessingReview(doc_id=env["doc_id"], id="e" * 32, revision=1, state="draft",
            source_file_hash=env["root_hash"], parser_version="test", assessor_version="test", policy_json={},
            inventory_json=env["snapshot"]["inventory"], selection_json=chosen, selection_hash="d" * 64,
            base_generation_id=env["base_id"])
        policy = snapshot_from_review(review)
        review.selection_hash = selection_hash(env["root_hash"], review.parser_version, policy["traversal_limits"], chosen)
        session.add(review)
    settings.okf_deployment_schema, settings.app_role = "replicas-v1", "api"
    def forbidden(*_a, **_k):
        pytest.fail("API must not dispatch a local scope worker")
    monkeypatch.setattr(jobs, "_dispatch", forbidden)
    return env


def test_scope_api_admits_work_without_allocating_candidate(future_scope):
    env = future_scope
    preview = jobs.create_change_preview(env["doc_id"], "e" * 32, 1, env["base_id"], "editor")
    assert preview["mode"] == "fast_exclude"
    result = jobs.apply_scope_change(env["doc_id"], preview["plan_id"], "1" * 32, env["base_id"], "editor", {"fast_exclude"})
    assert result["state"] == "queued" and result["candidate_generation_id"] is None
    with session_scope() as session:
        work = session.query(WorkItem).filter_by(kind="scope_change").one()
        assert work.doc_id == env["doc_id"] and work.payload_json["plan_id"] == preview["plan_id"]
        assert work.payload_json["source_file_hash"] == env["root_hash"]
        assert session.get(DocumentGenerationState, env["doc_id"]).candidate_generation_id is None


def accepted_scope(env, expected_mode="fast_exclude"):
    preview = jobs.create_change_preview(env["doc_id"], "e" * 32, 1, env["base_id"], "editor")
    assert preview["mode"] == expected_mode
    jobs.apply_scope_change(env["doc_id"], preview["plan_id"], "1" * 32, env["base_id"], "editor", {expected_mode})
    from app.services.work.store import claim
    with session_scope() as session:
        return claim(session, owner_id="scope-worker", supported={"scope_change": {1}}, lease_seconds=120)


def test_scope_reclaim_allocates_a_new_fenced_generation(future_scope):
    from app.services.work.scope import prepare_scope_attempt
    from app.services.work.store import claim
    from tests.test_work_store import expire
    env = future_scope
    old = accepted_scope(env)
    with session_scope() as session:
        first = prepare_scope_attempt(session, old, env["settings"]).id
    expire(old.work_id)
    with session_scope() as session:
        current = claim(session, owner_id="replacement", supported={"scope_change": {1}}, lease_seconds=120)
        second = prepare_scope_attempt(session, current, env["settings"]).id
    assert first != second
    with session_scope() as session:
        assert session.get(DocumentGeneration, first).phase == "abandoned"
        generation = session.get(DocumentGeneration, second)
        assert (generation.work_id, generation.work_fence) == (current.work_id, current.fence)


def test_scope_handler_publishes_and_acknowledges_fenced_change(future_scope, monkeypatch):
    from app.services.work.runtime import build_handlers
    env = future_scope
    work = accepted_scope(env)
    env["settings"].app_role = "worker"
    def forbidden(*_a, **_k):
        pytest.fail("Work heartbeat must replace the legacy scope heartbeat thread")
    monkeypatch.setattr("app.services.update_workers.Thread", forbidden)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        state = session.get(DocumentGenerationState, env["doc_id"])
        assert state.active_generation_id != env["base_id"]
        generation = session.get(DocumentGeneration, state.active_generation_id)
        assert (generation.work_id, generation.work_fence) == (work.work_id, work.fence)
        assert session.get(WorkItem, work.work_id).status == "succeeded"


def test_scope_guard_projects_the_renewed_work_lease(future_scope):
    from app.services.work.scope import prepare_scope_attempt
    from app.services.work.context import work_execution
    from app.services.work.store import renew, _utc
    from app.services.update_workers import scope_write_guard
    env = future_scope
    work = accepted_scope(env)
    with session_scope() as session:
        prepare_scope_attempt(session, work, env["settings"])
        owner = session.get(DocumentScopeChange, work.spec.payload["plan_id"]).owner_token
        renewed = renew(session, work, lease_seconds=180)
    with work_execution(work), scope_write_guard(env["doc_id"], work.spec.payload["plan_id"], owner) as session:
        projected = session.get(DocumentScopeChange, work.spec.payload["plan_id"]).lease_expires_at
        assert _utc(projected) == renewed.lease_until


def test_scope_cannot_execute_a_changed_accepted_plan(future_scope):
    from app.services.work.models import LostOwnership
    from app.services.work.runtime import build_handlers
    env = future_scope
    work = accepted_scope(env)
    with session_scope() as session:
        operation = session.get(DocumentScopeChange, work.spec.payload["plan_id"])
        value = deepcopy(operation.plan_json)
        value["snapshot"]["decisions"][0]["effective_included"] = True
        operation.plan_json = value
    with pytest.raises(LostOwnership):
        build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        state = session.get(DocumentGenerationState, env["doc_id"])
        assert state.active_generation_id == env["base_id"] and state.candidate_generation_id is None


def test_queued_scope_review_is_not_revoked_by_legacy_recovery(future_scope):
    from app.services.processing_scope import recover_processing_reviews
    env = future_scope
    work = accepted_scope(env)
    recover_processing_reviews(env["settings"], force=True)
    with session_scope() as session:
        review = session.get(DocumentProcessingReview, env["doc_id"])
        assert (review.state, review.revision, review.request_id) == ("submitted", 1, work.spec.payload["request_id"])


def test_explicit_cancel_closes_scope_receipt_and_fences_late_worker(future_scope):
    from threading import RLock
    from app.db.models import DocumentUpdateAttempt
    from app.services.pipeline import Pipeline
    from app.services.work.models import LostOwnership
    from app.services.work.runtime import build_handlers
    env = future_scope
    work = accepted_scope(env)
    pipeline = env["pipeline"]
    pipeline._start_lock = RLock()
    with session_scope() as session:
        update_id = session.get(DocumentUpdateAttempt, env["doc_id"]).id
    Pipeline.cancel_update(pipeline, env["doc_id"], expected_update_id=update_id)
    with session_scope() as session:
        operation = session.get(DocumentScopeChange, work.spec.payload["plan_id"])
        assert operation.state == "canceled"
        assert session.get(DocumentGenerationState, env["doc_id"]).active_generation_id == env["base_id"]
    with pytest.raises(LostOwnership):
        build_handlers().resolve(work.spec)(work)


def test_scope_cold_reclaim_reuses_accepted_llm_output(future_scope, tmp_path, monkeypatch):
    from app.db.models import DocumentChunk, OkfConcept
    from app.models.schemas import Concept
    from app.services.errors import VectorStoreError
    from app.services.processing_scope_cache import SourceResultCache
    from app.services.work.handlers import TransientWorkError
    from app.services.work.runtime import build_handlers
    from app.services.work.store import claim
    from tests.test_work_store import expire
    import app.services.generation_publication as publication
    env = future_scope
    doc_id, settings = env["doc_id"], env["settings"]
    with session_scope() as session:
        generation = session.get(DocumentGeneration, env["base_id"])
        snapshot = deepcopy(generation.processing_scope_json)
        snapshot["decisions"][0]["effective_included"] = False
        generation.processing_scope_json = snapshot
        session.query(DocumentChunk).filter_by(doc_id=doc_id, source_id="root/0").delete()
        session.query(OkfConcept).filter_by(doc_id=doc_id, source_id="root/0").delete()
        review = session.get(DocumentProcessingReview, doc_id)
        review.selection_json = deepcopy(env["decisions"])
        policy = snapshot_from_review(review)
        review.selection_hash = selection_hash(env["root_hash"], review.parser_version, policy["traversal_limits"], review.selection_json)
    # Seed the retained source parse cache, rather than writing a published generation.
    cache = SourceResultCache(settings.uploads_dir / doc_id / "scope-stage-cache", doc_id)
    cache.save_stage(env["nodes"][0], "parse", {"chunks": [{"chunk_index": 0, "source_id": "root/0", "content": "cached input", "local_order": 0}],
        "source": {"source_id": "root/0", "parent_source_id": "root", "ordinal": 0}})
    calls = []
    def generate(content, *_a, **_k):
        calls.append(content)
        return [Concept(id="accepted", title="Accepted", content="accepted scope output")]
    env["pipeline"].okf_generator.generate_chunk = generate
    env["pipeline"].embedder = SimpleNamespace(embed_texts=lambda values: [[1., 0.] for _ in values])
    def forbid_parse(*_a, **_k):
        pytest.fail("Accepted parse cache must survive worker loss")
    monkeypatch.setattr("app.services.parser_supervisor.parse_document_supervised", forbid_parse)
    old = accepted_scope(env, "incremental_generate")
    real_publish = publication.publish_prepared_document
    def unavailable(*_a, **_k):
        raise VectorStoreError(cause=ConnectionError("offline before publication"))
    monkeypatch.setattr(publication, "publish_prepared_document", unavailable)
    with pytest.raises(TransientWorkError):
        build_handlers().resolve(old.spec)(old)
    assert calls == ["cached input"]
    with session_scope() as session:
        old_generation = session.get(DocumentGenerationState, doc_id).candidate_generation_id
    expire(old.work_id)
    with session_scope() as session:
        current = claim(session, owner_id="cold-worker", supported={"scope_change": {1}}, lease_seconds=120)
    warm = settings.data_dir.resolve()
    assert warm == (tmp_path / "worker-a").resolve() and warm.is_relative_to(tmp_path.resolve())
    assert not settings.artifact_root.resolve().is_relative_to(warm)
    shutil.rmtree(warm)
    settings.data_dir = tmp_path / "worker-b"
    monkeypatch.setattr(publication, "publish_prepared_document", real_publish)
    def forbid_model(*_a, **_k):
        pytest.fail("Accepted generation/embedding must not be repeated")
    env["pipeline"].okf_generator.generate_chunk = forbid_model
    env["pipeline"].embedder.embed_texts = forbid_model
    build_handlers().resolve(current.spec)(current)
    with session_scope() as session:
        state = session.get(DocumentGenerationState, doc_id)
        assert state.active_generation_id != old_generation
        assert session.get(DocumentGeneration, state.active_generation_id).work_fence == current.fence
        assert session.get(WorkItem, current.work_id).status == "succeeded"


@pytest.mark.parametrize("later_review_cancelled", [False, True])
def test_published_scope_remains_admissible_for_document_regeneration(future_scope, later_review_cancelled):
    from app.services.work.admission import enqueue_existing_document
    from app.services.work.runtime import build_handlers
    env = future_scope
    work = accepted_scope(env)
    build_handlers().resolve(work.spec)(work)
    if later_review_cancelled:
        with session_scope() as session:
            review = session.get(DocumentProcessingReview, env["doc_id"])
            review.id, review.state, review.revision = "f" * 32, "canceled", 2
    new_id = enqueue_existing_document(env["settings"], env["doc_id"], "regenerate")
    with session_scope() as session:
        from app.db.models import DocumentWorkInput
        frozen = session.get(DocumentWorkInput, new_id)
        assert frozen.processing_scope_json["review_id"] == "e" * 32
        assert not frozen.processing_scope_json["decisions"][0]["effective_included"]
        assert session.get(WorkItem, new_id).status == "queued"


def test_historical_scope_proof_rejects_tampered_generation_policy(future_scope):
    from app.services.processing_scope import ProcessingScopeConflict
    from app.services.work.admission import enqueue_existing_document
    from app.services.work.runtime import build_handlers
    env = future_scope
    work = accepted_scope(env)
    build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        review = session.get(DocumentProcessingReview, env["doc_id"])
        review.id, review.state = "f" * 32, "canceled"
        state = session.get(DocumentGenerationState, env["doc_id"])
        generation = session.get(DocumentGeneration, state.active_generation_id)
        policy = deepcopy(generation.processing_scope_json)
        policy["decisions"][0]["effective_included"] = True
        policy["selection_hash"] = selection_hash(policy["source_file_hash"], policy["parser_version"],
            policy["traversal_limits"], policy["decisions"])
        generation.processing_scope_json = policy
    with pytest.raises(ProcessingScopeConflict):
        enqueue_existing_document(env["settings"], env["doc_id"], "regenerate")
    with session_scope() as session:
        assert session.query(WorkItem).filter_by(kind="document").count() == 0


@pytest.mark.parametrize("started", [False, True])
def test_trashed_scope_update_resumes_its_accepted_policy(future_scope, started):
    from app.services.pipeline import Pipeline
    from app.services.registry import DocumentRegistry
    from app.services.processing_scope import recover_processing_reviews
    from app.services.work.runtime import build_handlers
    from app.services.work.scope import prepare_scope_attempt
    from app.services.work.store import claim
    env = future_scope
    old = accepted_scope(env)
    with session_scope() as session:
        accepted_policy = deepcopy(session.get(DocumentScopeChange, old.spec.payload["plan_id"]).plan_json["snapshot"])
    previous = None
    if started:
        with session_scope() as session:
            previous = prepare_scope_attempt(session, old, env["settings"]).id
    registry = DocumentRegistry()
    registry.soft_delete(env["doc_id"], deleted_by="editor")
    registry.restore(env["doc_id"])
    recover_processing_reviews(env["settings"], force=True)
    env["pipeline"].registry = registry
    Pipeline.resume(env["pipeline"], env["doc_id"])
    with session_scope() as session:
        new = session.query(WorkItem).filter(WorkItem.kind == "scope_change", WorkItem.id != old.work_id).one()
        assert new.payload_json == old.spec.payload
        assert session.query(WorkItem).filter_by(kind="document").count() == 0
        current = claim(session, owner_id="resumed-scope", supported={"scope_change": {1}}, lease_seconds=120)
    build_handlers().resolve(current.spec)(current)
    with session_scope() as session:
        state = session.get(DocumentGenerationState, env["doc_id"])
        generation = session.get(DocumentGeneration, state.active_generation_id)
        assert generation.processing_scope_json["selection_hash"] == accepted_policy["selection_hash"]
        assert generation.processing_scope_json["decisions"] == accepted_policy["decisions"]
        assert generation.work_id == current.work_id and state.active_generation_id != previous
        assert session.get(WorkItem, current.work_id).status == "succeeded"


@pytest.mark.parametrize("before_candidate", [True, False])
def test_scope_exhausted_transient_failure_remains_resumable(future_scope, monkeypatch, before_candidate):
    from threading import Event
    from app.db.models import Document, DocumentUpdateAttempt
    from app.services.artifacts.contract import BlobUnavailable
    from app.services.work.handlers import TransientWorkError
    from app.services.work.runtime import build_handlers
    from app.services.work.worker import Worker
    from app.services.work.scope import resume_scope_change
    from tests.test_work_store import expire
    env = future_scope
    old = accepted_scope(env)
    expire(old.work_id)
    env["settings"].work_max_attempts = 1
    def unavailable(*_a, **_k):
        raise BlobUnavailable("temporarily unavailable") if before_candidate else TransientWorkError("model_unavailable")
    if before_candidate:
        monkeypatch.setattr("app.services.artifacts.original.original_path", unavailable)
    else:
        monkeypatch.setattr("app.services.processing_scope_worker.execute_scope_change", unavailable)
    runner = Worker(env["settings"], registry=build_handlers(), owner_id="retry-worker")
    assert runner.run_once(Event())
    with session_scope() as session:
        assert session.get(WorkItem, old.work_id).status == "failed"
        assert session.get(Document, env["doc_id"]).status == "paused"
        operation = session.get(DocumentScopeChange, old.spec.payload["plan_id"])
        assert operation.state in {"queued", "processing"}
        assert not session.get(DocumentUpdateAttempt, env["doc_id"]).cancel_requested
        assert session.get(DocumentProcessingReview, env["doc_id"]).state == "submitted"
    resumed_id = resume_scope_change(env["settings"], env["doc_id"])
    with session_scope() as session:
        resumed = session.get(WorkItem, resumed_id)
        assert resumed.kind == "scope_change" and resumed.payload_json == old.spec.payload


def test_scope_permanent_failure_before_candidate_restores_publication(future_scope, monkeypatch):
    from app.db.models import Document
    from app.services.generation_artifacts import GenerationIntegrityError
    from app.services.work.runtime import build_handlers
    env = future_scope
    work = accepted_scope(env)
    def corrupt(*_a, **_k):
        raise GenerationIntegrityError()
    monkeypatch.setattr("app.services.artifacts.original.original_path", corrupt)
    with pytest.raises(GenerationIntegrityError):
        build_handlers().resolve(work.spec)(work)
    with session_scope() as session:
        assert session.get(DocumentScopeChange, work.spec.payload["plan_id"]).state == "failed"
        assert session.get(Document, env["doc_id"]).status == "done"
        assert session.get(DocumentGenerationState, env["doc_id"]).active_generation_id == env["base_id"]


def test_scope_cold_reclaim_preserves_supervised_xlsx_tables(future_scope, tmp_path, monkeypatch):
    import hashlib
    from openpyxl import Workbook
    from docparser.source_model import parser_version_for_mail_mode
    from app.db.models import DocumentTableRow, OkfConcept
    from app.services.generation_files import processing_review_paths
    from app.services.errors import VectorStoreError
    from app.services.work.handlers import TransientWorkError
    from app.services.work.runtime import build_handlers
    from app.services.work.store import claim
    from tests.test_work_store import expire
    import app.services.generation_publication as publication
    env = future_scope
    settings, doc_id = env["settings"], env["doc_id"]
    folder = processing_review_paths(settings, doc_id, "e" * 32) / "attachments"
    folder.mkdir(parents=True)
    original = folder / "new.xlsx"
    book = Workbook()
    book.active.append(["Code", "Meaning"])
    book.active.append(["COLD_ONLY", "retained row"])
    book.save(original)
    node = {"source_key": "3" * 64, "parent_key": "root", "locator": "part:new", "display_name": "new.xlsx", "kind": "xlsx",
            "saved_path": "new.xlsx", "content_fingerprint": hashlib.sha256(original.read_bytes()).hexdigest(),
            "supported": True, "children_complete": True}
    with session_scope() as session:
        review = session.get(DocumentProcessingReview, doc_id)
        inventory = deepcopy(review.inventory_json)
        inventory["nodes"].append(node)
        review.inventory_json = inventory
        review.selection_json = [*env["decisions"], {"source_key": node["source_key"], "effective_included": True, "reason": "small"}]
        review.parser_version = parser_version_for_mail_mode(settings.mail_import_enabled) + "-scope-v1"
        snapshot = snapshot_from_review(review)
        review.selection_hash = selection_hash(env["root_hash"], review.parser_version, snapshot["traversal_limits"], review.selection_json)
    def forbid_llm(*_a, **_k):
        pytest.fail("A structural table must not invoke the LLM")
    env["pipeline"].okf_generator.generate_chunk = forbid_llm
    env["pipeline"].embedder = SimpleNamespace(embed_texts=lambda values: [[1., 0.] for _ in values])
    old = accepted_scope(env, "incremental_generate")
    real_publish = publication.publish_prepared_document
    def unavailable(*_a, **_k):
        raise VectorStoreError(cause=ConnectionError("before publication"))
    monkeypatch.setattr(publication, "publish_prepared_document", unavailable)
    with pytest.raises(TransientWorkError):
        build_handlers().resolve(old.spec)(old)
    with session_scope() as session:
        old_generation = session.get(DocumentGenerationState, doc_id).candidate_generation_id
        before = [(r.table_id, r.row_number, r.cells) for r in session.query(DocumentTableRow).filter_by(
            doc_id=doc_id, generation_id=old_generation).order_by(DocumentTableRow.table_id, DocumentTableRow.row_number)]
    assert before
    expire(old.work_id)
    with session_scope() as session:
        current = claim(session, owner_id="cold-table-worker", supported={"scope_change": {1}}, lease_seconds=120)
    warm = settings.data_dir.resolve()
    assert warm == (tmp_path / "worker-a").resolve() and warm.is_relative_to(tmp_path.resolve())
    assert not settings.artifact_root.resolve().is_relative_to(warm)
    shutil.rmtree(warm)
    settings.data_dir = tmp_path / "cold-table-worker"
    monkeypatch.setattr(publication, "publish_prepared_document", real_publish)
    monkeypatch.setattr("app.services.parser_supervisor.parse_document_supervised", forbid_llm)
    env["pipeline"].embedder.embed_texts = forbid_llm
    build_handlers().resolve(current.spec)(current)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, doc_id).active_generation_id
        after = [(r.table_id, r.row_number, r.cells) for r in session.query(DocumentTableRow).filter_by(
            doc_id=doc_id, generation_id=active).order_by(DocumentTableRow.table_id, DocumentTableRow.row_number)]
        assert active != old_generation and before == after
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).all()
        refs = [ref for concept in concepts for ref in concept.table_refs or []]
        assert refs and all(ref["generation_id"] == active for ref in refs)
        assert session.get(WorkItem, current.work_id).status == "succeeded"
