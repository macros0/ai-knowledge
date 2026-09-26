"""An attempt owns its private files until publication, including failed resume."""
from pathlib import Path

import pytest

from app.services.errors import DomainError
from app.services.staging import StagingStore
from tests.test_generation_pipeline import DOC_ID, _published_snapshot  # noqa: F401
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def _partial_failure_worker(send, path, filename, destination, max_memory):
    from app.services.parser_supervisor import _send_worker_message

    (Path(destination) / "partial.bin").write_bytes(b"synthetic worker artifact")
    _send_worker_message(send, {"ok": False, "code": "parse_failed", "error": "synthetic worker failure"})


@pytest.mark.parametrize("stage", ["parse", "checkpoint"])
def test_failed_pipeline_attempt_removes_private_files_and_preserves_publication(pipeline_env, monkeypatch, stage):
    from app.services import pipeline as pipeline_module

    pipeline, source, _write = pipeline_env
    before = _published_snapshot(pipeline)
    original = pipeline_module.parse_document

    def parse(*args, **kwargs):
        destination = Path(kwargs["attachments_dir"])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "partial.bin").write_bytes(b"synthetic partial artifact")
        if stage == "parse":
            raise ValueError("synthetic parser failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline_module, "parse_document", parse)
    if stage == "checkpoint":
        staging = StagingStore(DOC_ID)
        staging.create(1, parser_version="incompatible-parser")
        staging.save_chunk_text(0, "Old incompatible chunk")
    with pytest.raises((ValueError, DomainError)):
        pipeline._process(DOC_ID, source, "decision.eml", [], resume=stage == "checkpoint")
    assert not list((pipeline.settings.uploads_dir / DOC_ID).rglob(".attachments-attempt-*"))
    assert _published_snapshot(pipeline) == before


def test_spawned_parser_failure_leaves_no_attempts_after_repeated_pipeline_calls(pipeline_env, monkeypatch):
    from app.services import pipeline as pipeline_module
    from app.services.parser_supervisor import ParserWorkerError, parse_document_supervised

    pipeline, source, _write = pipeline_env
    pipeline.settings.parser_supervisor_enabled = True
    before = _published_snapshot(pipeline)

    def supervised(*args, **kwargs):
        return parse_document_supervised(*args, **kwargs, _worker_target=_partial_failure_worker)

    monkeypatch.setattr(pipeline_module, "parse_document_supervised", supervised)
    for _ in range(2):
        with pytest.raises(ParserWorkerError):
            pipeline._process(DOC_ID, source, "decision.eml", [], resume=False)
        assert not list((pipeline.settings.uploads_dir / DOC_ID).rglob(".attachments-attempt-*"))
        assert _published_snapshot(pipeline) == before
