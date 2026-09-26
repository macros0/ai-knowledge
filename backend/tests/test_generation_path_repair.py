"""Path cleanup publishes both search branches and moves verified source offsets."""
from copy import deepcopy

import pytest

from app.db.models import DocumentChunk, DocumentGenerationState, OkfConcept
from app.db.session import session_scope
from app.services.source_evidence import chunk_digest, span_from_offsets
from scripts import fix_attachment_paths as repair
from tests.test_generation_pipeline import DOC_ID, _published_snapshot
from tests.test_generation_pipeline import pipeline_env as pipeline_env


def _legacy_text(pipeline):
    quote = "😀 Проверенный фрагмент документа после устаревшей ссылки на вложение."
    old = "(файл: C:\\legacy\\folder\\file.pdf)\n\n" + quote
    with session_scope() as session:
        chunk = session.query(DocumentChunk).filter_by(doc_id=DOC_ID, source_id="root").first()
        chunk.content = old
        chunk.content_hash = chunk_digest(old)
        chunk.char_count = len(old)
        concept = session.query(OkfConcept).filter_by(doc_id=DOC_ID, chunk_index=chunk.chunk_index).first()
        concept.content = old
        concept.source_spans = [span_from_offsets(old, old.index(quote), len(old)).model_dump()]
        return chunk.chunk_index, concept.slug, quote


def _fix(pipeline, **kwargs):
    return repair.fix_rows(settings=pipeline.settings, generator=pipeline.okf_generator,
                           embedder=pipeline.embedder, vector_store=pipeline.vector_store, **kwargs)


def test_path_repair_reindexes_both_branches_and_translates_verified_offsets(pipeline_env, monkeypatch):
    pipeline, _source, _write = pipeline_env
    index, slug, quote = _legacy_text(pipeline)
    before = _published_snapshot(pipeline)
    inputs = []
    original = pipeline.embedder.embed_texts

    def record(texts):
        inputs.append(list(texts))
        return original(texts)

    monkeypatch.setattr(pipeline.embedder, "embed_texts", record)
    result = _fix(pipeline, dry_run=False)
    assert result["errors"] == []
    assert (DOC_ID, index) in result["chunks"] and (DOC_ID, slug) in result["concepts"]
    assert _published_snapshot(pipeline)[0] != before[0]
    assert len(inputs) == 2
    assert all("C:\\legacy" not in text for batch in inputs for text in batch)
    with session_scope() as session:
        chunk = session.query(DocumentChunk).filter_by(doc_id=DOC_ID, chunk_index=index).one()
        concept = session.query(OkfConcept).filter_by(doc_id=DOC_ID, slug=slug).one()
        assert chunk.content.startswith("(файл: attachments/file.pdf)")
        assert chunk.char_count == len(chunk.content)
        assert chunk.content_hash == chunk_digest(chunk.content)
        span = concept.source_spans[0]
        assert chunk.content[span["start"]:span["end"]] == quote
        assert span["chunk_hash"] == chunk.content_hash
    repaired = _published_snapshot(pipeline)
    second = _fix(pipeline, dry_run=False)
    assert second["chunks"] == second["concepts"] == []
    assert _published_snapshot(pipeline) == repaired


def test_path_repair_failure_keeps_old_sql_files_and_active_generation(pipeline_env, monkeypatch):
    pipeline, _source, _write = pipeline_env
    _legacy_text(pipeline)
    before = _published_snapshot(pipeline)

    def fail(*_args, **_kwargs):
        raise RuntimeError("index failure")

    monkeypatch.setattr(pipeline.vector_store, "index_chunks", fail)
    result = _fix(pipeline, dry_run=False)
    assert result["chunks"] == result["concepts"] == []
    assert result["errors"][0]["doc_id"] == DOC_ID
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert session.get(DocumentGenerationState, DOC_ID).candidate_generation_id is None


def test_path_repair_dry_run_does_not_change_spans_or_generation(pipeline_env):
    pipeline, _source, _write = pipeline_env
    _legacy_text(pipeline)
    before = _published_snapshot(pipeline)
    with session_scope() as session:
        spans = deepcopy([row.source_spans for row in session.query(OkfConcept).filter_by(doc_id=DOC_ID)])
    result = _fix(pipeline, dry_run=True)
    assert result["chunks"] and result["concepts"] and not result["errors"]
    assert _published_snapshot(pipeline) == before
    with session_scope() as session:
        assert [row.source_spans for row in session.query(OkfConcept).filter_by(doc_id=DOC_ID)] == spans


def test_path_rewrite_translates_multiple_edits_and_whole_marker_spans():
    first = "(файл: C:\\legacy\\one.pdf)"
    second = "[Два](file:/old/path/two.pdf)"
    quote = "😀 Цитата после обеих замен."
    text = f"{first}\n{second}\n{quote}"
    ranges = [(0, len(text)), (0, len(first)), (text.index(quote), len(text))]
    rewritten, spans = repair._rewrite_with_spans(text, [span_from_offsets(text, *pos).model_dump() for pos in ranges])
    assert rewritten == f"(файл: attachments/one.pdf)\n[Два](attachments/two.pdf)\n{quote}"
    assert [rewritten[row["start"]:row["end"]] for row in spans] == [rewritten, "(файл: attachments/one.pdf)", quote]
    assert all(row["chunk_hash"] == chunk_digest(rewritten) for row in spans)


def test_path_rewrite_drops_partial_or_stale_spans():
    text = "(файл: C:\\legacy\\one.pdf)\nЦитата"
    partial = span_from_offsets(text, text.index("legacy"), len(text)).model_dump()
    stale = span_from_offsets(text, 0, len(text)).model_dump()
    stale["chunk_hash"] = "0" * 64
    _rewritten, spans = repair._rewrite_with_spans(text, [partial, stale, {}])
    assert spans == []


@pytest.mark.parametrize("start", [True, False, "0", 0.0])
def test_path_rewrite_does_not_promote_invalid_offset_types_to_verified_spans(start):
    text = "(файл: C:\\legacy\\one.pdf)\nЦитата"
    invalid = {"start": start, "end": len(text), "chunk_hash": chunk_digest(text)}
    _rewritten, spans = repair._rewrite_with_spans(text, [invalid])
    assert spans == []
