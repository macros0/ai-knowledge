from email.message import EmailMessage
import io
import json

from openpyxl import Workbook
from sqlalchemy import select

from tests.test_generation_pipeline import pipeline_env as shared_pipeline_env, DOC_ID  # noqa: F401

pipeline_env = shared_pipeline_env
from app.db.models import DocumentGeneration, DocumentGenerationState, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.processing_scope import automatic_snapshot
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_policy import AssessmentLimits, TraversalLimits


def test_scoped_generation_excludes_raw_and_programmatic_content(pipeline_env):
    pipeline, path, _ = pipeline_env
    book = Workbook()
    for _ in range(700):
        book.active.append(["EXCLUDED_SCOPE_CANARY"])
    data = io.BytesIO()
    book.save(data)
    message = EmailMessage()
    message["Subject"] = "Decision"
    message.set_content("ROOT_SCOPE_EVIDENCE")
    message.add_attachment(
        data.getvalue(), maintype="application", subtype="octet-stream", filename="large.xlsx"
    )
    path.write_bytes(message.as_bytes())
    inventory = inspect_attachment_inventory(
        path, "decision.eml", limits=AssessmentLimits(), traversal=TraversalLimits()
    )
    snapshot = automatic_snapshot(inventory)
    pipeline._process(DOC_ID, path, "decision.eml", [], resume=False, processing_scope=snapshot)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        state = session.get(DocumentGenerationState, DOC_ID)
        generation = session.get(DocumentGeneration, state.active_generation_id)
        from app.services.processing_scope_cache import embedding_fingerprint
        assert generation.processing_scope_json == {**snapshot,"vector_space":{
            "kind":"proven","fingerprint":embedding_fingerprint(pipeline.settings)}}
        assert all(
            "EXCLUDED_SCOPE_CANARY" not in row.content
            for row in session.scalars(select(DocumentChunk))
        )
        assert all(
            "EXCLUDED_SCOPE_CANARY" not in row.content
            for row in session.scalars(select(OkfConcept))
        )
        active_id = state.active_generation_id
    publication = (
        pipeline.settings.uploads_dir / DOC_ID / "generations" / active_id / "publication.json"
    )
    assert (
        json.loads(publication.read_text(encoding="utf8"))["processing_scope"]["selection_hash"]
        == snapshot["selection_hash"]
    )


def test_publication_scope_guard_rejects_injected_excluded_source():
    from app.services.processing_scope import assert_prepared_scope, ProcessingScopeConflict
    import pytest

    snapshot = {"decisions": [{"source_key": "a" * 64, "effective_included": False}]}
    sources = [{"source_id": "root/0", "metadata": {"source_key": "a" * 64}}]
    with pytest.raises(ProcessingScopeConflict):
        assert_prepared_scope(snapshot, sources, [{"source_id": "root/0"}], [])


def test_retained_original_hash_mismatch_fails_without_publishing_file(tmp_path):
    import hashlib
    import pytest
    from app.services.processing_scope_originals import _copy_original
    from app.services.generation_files import GenerationStorageError

    original = tmp_path / "review.xlsx"
    original.write_bytes(b"changed original")
    destination = tmp_path / "candidate" / "original.xlsx"
    with pytest.raises(GenerationStorageError, match="changed"):
        _copy_original(original, destination, hashlib.sha256(b"expected original").hexdigest())
    assert not destination.exists()
    assert not list(destination.parent.glob(".original-*"))


def test_retained_originals_never_write_active_generation(pipeline_env):
    import pytest
    from app.services.processing_scope_originals import retain_scope_originals
    from app.services.generation_store import GenerationConflict
    from docparser.attachment_policy import AttachmentInventory

    pipeline, _, _ = pipeline_env
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
    snapshot = automatic_snapshot(AttachmentInventory(source_file_hash="a" * 64))
    snapshot["review_id"] = "b" * 32
    with pytest.raises(GenerationConflict):
        retain_scope_originals(pipeline.settings, DOC_ID, active, snapshot, [], [])


def test_retained_originals_support_relative_data_directory(pipeline_env, monkeypatch):
    import hashlib
    from pathlib import Path
    from app.services.processing_scope_originals import retain_scope_originals
    from app.services.generation_store import begin_generation
    from app.services.generation_files import processing_review_paths
    from docparser.attachment_policy import AttachmentInventory, AttachmentNode

    pipeline, _, _ = pipeline_env
    monkeypatch.chdir(pipeline.settings.data_dir)
    pipeline.settings.data_dir = Path(".")
    payload = b"excluded original bytes"
    key = "a" * 64
    inventory = AttachmentInventory(source_file_hash="b" * 64, nodes=[
        AttachmentNode(key, "root", "one", "one.xlsx", "xlsx", assessment="heavy",
                       saved_path="one.xlsx", content_fingerprint=hashlib.sha256(payload).hexdigest())
    ])
    snapshot = automatic_snapshot(inventory)
    snapshot["review_id"] = "c" * 32
    root = processing_review_paths(pipeline.settings, DOC_ID, snapshot["review_id"]) / "attachments"
    root.mkdir(parents=True)
    (root / "one.xlsx").write_bytes(payload)
    with session_scope() as session:
        generation_id = begin_generation(session, DOC_ID).id
    sources, attachments = [], []
    retain_scope_originals(pipeline.settings, DOC_ID, generation_id, snapshot, sources, attachments)
    assert (pipeline.settings.uploads_dir / DOC_ID / sources[0]["saved_path"]).read_bytes() == payload


def test_confirm_replay_admits_one_durable_generation(pipeline_env, monkeypatch):
    from concurrent.futures import Future
    import hashlib
    import uuid
    import app.services.processing_admission as admission
    from app.services.processing_scope import create_review
    from app.db.models import DocumentProcessingReview
    from docparser.attachment_policy import AttachmentInventory

    pipeline, path, _ = pipeline_env
    submissions = []

    class HoldingExecutor:
        def submit(self, *args, **kwargs):
            submissions.append(args)
            return Future()

    pipeline._executor = HoldingExecutor()
    monkeypatch.setattr(admission, "get_pipeline", lambda: pipeline)
    monkeypatch.setattr(admission, "get_settings", lambda: pipeline.settings)
    monkeypatch.setattr(admission, "get_registry", lambda: pipeline.registry)
    with session_scope() as session:
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        row = create_review(
            session,
            DOC_ID,
            AttachmentInventory(source_file_hash=hashlib.sha256(path.read_bytes()).hexdigest()),
            base_generation_id=active,
            actor="editor",
        )
        review_id, revision = row.id, row.revision
    request_id = uuid.uuid4().hex
    first = admission.confirm_processing_scope(
        DOC_ID, review_id, revision, request_id, actor="editor"
    )
    second = admission.confirm_processing_scope(
        DOC_ID, review_id, revision, request_id, actor="editor"
    )
    assert first.status == "accepted" and second.status == "already_accepted"
    assert len(submissions) == 1
    with session_scope() as session:
        candidate = session.get(DocumentGenerationState, DOC_ID).candidate_generation_id
        assert session.get(DocumentProcessingReview, DOC_ID).generation_id == candidate
        assert session.get(DocumentGeneration, candidate).scope_request_id == request_id
    pipeline._threads.clear()
