"""The chunk CLI fills missing legacy SQL rows without destroying published files."""
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.generation_store import begin_generation, mark_generation_ready, publish_generation


def _run(monkeypatch, settings, ensure_chunks, *extra):
    from app.services import pipeline as pipeline_module
    from scripts import backfill_chunks

    pipeline = SimpleNamespace(
        settings=settings, ensure_chunks=ensure_chunks,
        registry=SimpleNamespace(get=lambda _id: {"id": "cli-chunks", "filename": "doc.docx"}),
    )
    monkeypatch.setattr(pipeline_module, "Pipeline", lambda: pipeline)
    monkeypatch.setattr("sys.argv", ["backfill_chunks.py", "--doc-id", "cli-chunks", *extra])
    backfill_chunks.main()


def test_empty_flat_chunk_directory_does_not_suppress_sql_backfill(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with session_scope() as session:
        session.add(Document(id="cli-chunks", filename="doc.docx", status="done"))
    (settings.okf_dir / "cli-chunks" / "chunks").mkdir(parents=True)
    settings.uploads_dir.mkdir(parents=True)
    (settings.uploads_dir / "cli-chunks.docx").write_bytes(b"source")
    calls = []

    def ensure(doc_id):
        calls.append(doc_id)
        with session_scope() as session:
            session.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="Recovered"))
        return [{"index": 0}]

    _run(monkeypatch, settings, ensure)
    assert calls == ["cli-chunks"]


def test_force_does_not_delete_published_chunk_files(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with session_scope() as session:
        session.add(Document(id="cli-chunks", filename="doc.docx", status="done"))
        session.add(DocumentChunk(doc_id="cli-chunks", chunk_index=0, content="Published"))
    chunk_file = settings.okf_dir / "cli-chunks" / "chunks" / "chunk_00.md"
    chunk_file.parent.mkdir(parents=True)
    chunk_file.write_text("Published", encoding="utf-8")
    settings.uploads_dir.mkdir(parents=True)
    (settings.uploads_dir / "cli-chunks.docx").write_bytes(b"source")
    with pytest.raises(SystemExit) as failure:
        _run(monkeypatch, settings, lambda _id: [{"index": 0}], "--force")
    assert failure.value.code == 2
    assert chunk_file.read_text(encoding="utf-8") == "Published"


@pytest.mark.parametrize("published", [False, True])
def test_cli_does_not_reparse_existing_sql_or_empty_published_result(tmp_path, monkeypatch, published):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    with session_scope() as session:
        session.add(Document(id="cli-chunks", filename="doc.docx", status="done"))
        session.flush()
        if published:
            generation = begin_generation(session, "cli-chunks")
            mark_generation_ready(session, "cli-chunks", generation.id)
            publish_generation(session, "cli-chunks", generation.id)
        else:
            session.add(DocumentChunk(doc_id="cli-chunks", chunk_index=4, content="Published"))
    settings.uploads_dir.mkdir(parents=True)
    (settings.uploads_dir / "cli-chunks.docx").write_bytes(b"source")
    calls = []
    _run(monkeypatch, settings, lambda _id: calls.append(_id) or [])
    assert calls == []
