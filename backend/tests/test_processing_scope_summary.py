from sqlalchemy import event
from app.db.models import Document, DocumentGenerationState
from app.db.session import session_scope, get_engine
from app.services.generation_store import begin_generation
from app.services.processing_scope import automatic_snapshot, scope_summary
from app.services.registry import DocumentRegistry
from docparser.attachment_policy import AttachmentInventory
import pytest


@pytest.mark.parametrize("count", [1, 100])
def test_document_metadata_does_not_load_whole_inventory(count):
    doc_id = "1234567890abcdef"
    snapshot = automatic_snapshot(AttachmentInventory(source_file_hash="a" * 64))
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="test.docx", status="done"))
        session.flush()
        gen = begin_generation(session, doc_id)
        gen.phase = "active"
        gen.processing_scope_json = snapshot
        # Summary is an intentional read model; older missing summaries remain unknown.
        gen.processing_scope_summary_json = scope_summary(snapshot, gen.id)
        state = session.get(DocumentGenerationState, doc_id)
        state.active_generation_id = gen.id
        state.candidate_generation_id = None
        for index in range(1, count):
            session.add(Document(id=f"{index:016x}", filename="legacy.docx", status="done"))
    statements = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        assert DocumentRegistry().get(doc_id)["processing_scope"]["mode"] == "all"
        from app.services.processing_scope import load_processing_scope_summaries

        statements.clear()
        with session_scope() as session:
            summaries = load_processing_scope_summaries(
                session, [doc_id] + [f"{i:016x}" for i in range(1, count)]
            )
        assert len(summaries) == count
        assert len(statements) == 3
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert not any(
        "document_generations.processing_scope_json" in statement for statement in statements
    )
    assert not any(
        "document_processing_reviews.inventory_json" in statement for statement in statements
    )
def test_scope_availability_distinguishes_root_unknown_and_exclusions():
    from app.services.processing_scope import scope_availability
    assert scope_availability(None, None, []) == "unknown"
    assert scope_availability(None, None, [{"source_id": "root"}]) == "none"
    assert scope_availability({"excluded": 1}, None, []) == "available"
    assert scope_availability({"total": 0, "count_complete": True}, None, []) == "none"
    assert scope_availability(None, None, [{"source_id": "root"}, {"source_id": "root/0", "display_name": "book.xlsx"}]) == "available"
