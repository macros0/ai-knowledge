from copy import deepcopy
from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.processing_scope_order import assign_scope_display_order, reserve_chunk_indices


def test_insert_middle_source_preserves_existing_ids():
    sources = [{"source_id": "root", "parent_source_id": None},
               {"source_id": "root/a", "parent_source_id": "root", "ordinal": 0},
               {"source_id": "root/new", "parent_source_id": "root", "ordinal": 1},
               {"source_id": "root/b", "parent_source_id": "root", "ordinal": 2}]
    chunks = [{"source_id": "root/b", "chunk_index": 20},
              {"source_id": "root/new", "chunk_index": 99},
              {"source_id": "root/a", "chunk_index": 10}]
    concepts = [{"source_id": c["source_id"], "chunk_index": c["chunk_index"], "slug": str(c["chunk_index"])} for c in chunks]
    before = deepcopy(chunks)
    assign_scope_display_order(sources, chunks, concepts)
    assert [c["chunk_index"] for c in sorted(chunks, key=lambda c: c["display_order"])] == [10, 99, 20]
    assert [{k: v for k, v in c.items() if k != "display_order"} for c in chunks] == before
    assert [c["slug"] for c in sorted(concepts, key=lambda c: c["display_order"])] == ["10", "99", "20"]


def test_reserved_indices_not_reused_after_cancel():
    doc_id = "1234567890abcdef"
    with session_scope() as s:
        s.add(Document(id=doc_id, filename="test.docx", status="done"))
        s.flush()
        s.add(DocumentChunk(doc_id=doc_id, chunk_index=29, content="existing"))
    with session_scope() as s:
        assert list(reserve_chunk_indices(s, doc_id, 3)) == [30, 31, 32]
    # Canceling publication cannot reset an already committed reservation.
    with session_scope() as s:
        assert list(reserve_chunk_indices(s, doc_id, 2)) == [33, 34]


def test_equal_names_and_nested_sibling_order():
    sources = [{"source_id": "root", "parent_source_id": None},
               {"source_id": "root/b", "parent_source_id": "root", "ordinal": 1},
               {"source_id": "root/a", "parent_source_id": "root", "ordinal": 0},
               {"source_id": "root/a/x", "parent_source_id": "root/a", "ordinal": 0}]
    chunks = [{"source_id": s["source_id"], "chunk_index": i} for i, s in enumerate(sources)]
    assign_scope_display_order(sources, chunks, [])
    assert [c["source_id"] for c in sorted(chunks, key=lambda c: c["display_order"])] == ["root", "root/a", "root/a/x", "root/b"]


def test_stores_and_fulltext_preserve_display_order():
    from app.db.models import DocumentSource, OkfConcept
    from app.models.schemas import OkfDocument
    from app.services.chunk_store import replace_chunks
    from app.services.concept_store import replace_concepts
    from app.api.documents import _fulltext_from_db, _okf_files_from_db
    doc_id = "1234567890abcdef"
    with session_scope() as s:
        s.add(Document(id=doc_id, filename="order.docx", status="done"))
        s.flush()
        s.add(DocumentSource(doc_id=doc_id, source_id="root"))
        s.flush()
        replace_chunks(s, doc_id, [{"source_id": "root", "chunk_index": 30, "display_order": 0, "content": "first"},
                                  {"source_id": "root", "chunk_index": 10, "display_order": 1, "content": "second"}])
        replace_concepts(s, doc_id, [OkfDocument(filepath="first.md", content="first", markdown="first", metadata={"source_id": "root", "chunk_index": 30, "display_order": 0}),
                                   OkfDocument(filepath="second.md", content="second", markdown="second", metadata={"source_id": "root", "chunk_index": 10, "display_order": 1})])
    with session_scope() as s:
        assert s.query(DocumentChunk).filter_by(doc_id=doc_id, chunk_index=30).one().display_order == 0
        assert s.query(OkfConcept).filter_by(doc_id=doc_id, slug="first").one().display_order == 0
    assert _fulltext_from_db(doc_id) == ["first", "second"]
    assert [item.filename for item in _okf_files_from_db(doc_id)] == ["first.md", "second.md"]
    from app.services.source_location import get_document_text_chunks
    from app.services.pipeline import _chunks_meta_from_db
    assert [item.chunk_index for item in get_document_text_chunks(doc_id)] == [30, 10]
    assert [item["index"] for item in _chunks_meta_from_db(doc_id)] == [30, 10]
