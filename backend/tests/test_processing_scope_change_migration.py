from sqlalchemy import inspect, text
from app.db.session import get_engine, session_scope
from app.db.models import Document


def test_scope_receipt_and_order_schema_is_additive():
    schema = inspect(get_engine())
    assert "document_scope_changes" in schema.get_table_names()
    for table in ("document_chunks", "okf_concepts"):
        columns = {c["name"]: c for c in schema.get_columns(table)}
        assert columns["display_order"]["nullable"] is True
    assert "next_chunk_index" in {c["name"] for c in schema.get_columns("document_generation_states")}


def test_scope_receipts_survive_candidate_cleanup_but_not_document_purge():
    from app.db.models import DocumentScopeChange
    with session_scope() as s:
        s.add(Document(id="1234567890abcdef", filename="test.docx", status="done"))
        s.flush()
        s.add(DocumentScopeChange(id="1" * 32, doc_id="1234567890abcdef", request_id="2" * 32,
            state="published", plan_json={"schema_version": 1}, candidate_generation_id="3" * 32))
    with session_scope() as s:
        assert s.get(DocumentScopeChange, "1" * 32).candidate_generation_id == "3" * 32
    from app.services.registry import DocumentRegistry
    assert DocumentRegistry().delete("1234567890abcdef")
    with session_scope() as s:
        assert s.get(DocumentScopeChange, "1" * 32) is None


def test_scope_receipt_request_is_unique():
    import pytest
    from sqlalchemy.exc import IntegrityError
    from app.db.models import DocumentScopeChange
    with session_scope() as s:
        s.add(Document(id="1234567890abcdef", filename="test.docx", status="done"))
    with pytest.raises(IntegrityError):
        with session_scope() as s:
            for n in (1, 2):
                s.add(DocumentScopeChange(id=str(n) * 32, doc_id="1234567890abcdef", request_id="a" * 32,
                    state="admitted", plan_json={"schema_version": 1}))
