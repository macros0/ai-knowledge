"""Canonical provenance must be read once per hydration snapshot."""
from sqlalchemy import event, select
from sqlalchemy.dialects import postgresql

from app.db.models import Document, DocumentChunk, DocumentSource, OkfConcept
from app.db.session import get_engine, session_scope
from app.services.fusion import Hit
from app.services.retrieval_hydration import _concept_pair_condition, load_visible_retrieval_hits
from app.services.source_store import fetch_source_paths, fetch_source_trees


def test_hydration_reads_source_tree_once_and_keeps_document_scoped_paths():
    with session_scope() as session:
        for doc_id, filename in (("snapshot-a", "a.docx"), ("snapshot-b", "b.docx")):
            session.add(Document(id=doc_id, filename=filename))
            session.add_all([
                DocumentSource(doc_id=doc_id, source_id="root", kind="document", display_name=filename),
                DocumentSource(doc_id=doc_id, source_id="mail", parent_source_id="root",
                               kind="mail", display_name="letter.eml", metadata_json={"subject": doc_id}),
                DocumentSource(doc_id=doc_id, source_id="attachment", parent_source_id="mail",
                               kind="document", display_name="attachment.docx"),
                OkfConcept(doc_id=doc_id, slug="topic", title="Topic", content="Canonical topic",
                           source_id="attachment", chunk_index=0),
                DocumentChunk(doc_id=doc_id, chunk_index=0, source_id="attachment", content="Canonical chunk"),
            ])
    statements = []

    def observe(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT") and "FROM document_sources" in statement:
            statements.append(statement)

    event.listen(get_engine(), "before_cursor_execute", observe)
    try:
        kept, _ = load_visible_retrieval_hits([
            Hit(doc_id, 1.0, {"point_type": "concept", "doc_id": doc_id, "slug": "topic"})
            for doc_id in ("snapshot-a", "snapshot-b")
        ], mail_mode="only")
    finally:
        event.remove(get_engine(), "before_cursor_execute", observe)

    assert len(kept) == 2
    assert [hit.payload["source_path"][1]["subject"] for hit in kept] == ["snapshot-a", "snapshot-b"]
    assert all(hit.payload["source_id"] == "attachment" for hit in kept)
    assert all(hit.payload["mail_scope"] == "mail" for hit in kept)
    assert len(statements) == 1


def test_explicit_empty_source_snapshot_does_not_reload_or_borrow_stored_rows():
    with session_scope() as session:
        session.add(Document(id="empty-snapshot", filename="stored.docx"))
        session.add(DocumentSource(doc_id="empty-snapshot", source_id="root",
                                   kind="document", display_name="stored.docx"))
    with session_scope() as session:
        assert fetch_source_trees(session, {"empty-snapshot"}, rows=[]) == {"empty-snapshot": []}
        assert fetch_source_paths(session, {("empty-snapshot", "root")}, rows=[]) == {}
        assert fetch_source_paths(session, {("empty-snapshot", "root")}) == {
            ("empty-snapshot", "root"): [{"source_id": "root", "kind": "document", "display_name": "stored.docx"}]}


def test_candidate_queries_group_doc_keys_without_cross_document_matches():
    with session_scope() as session:
        for doc_id in ("pair-a", "pair-b"):
            session.add(Document(id=doc_id, filename=f"{doc_id}.docx"))
            for slug in ("one", "two", "three"):
                session.add(OkfConcept(doc_id=doc_id, slug=slug, title=slug,
                                       content=f"{doc_id}/{slug}"))
    parameter_counts = []

    def observe(_connection, _cursor, statement, parameters, *_args):
        if "FROM okf_concepts" in statement:
            parameter_counts.append(len(parameters))

    event.listen(get_engine(), "before_cursor_execute", observe)
    try:
        kept, _ = load_visible_retrieval_hits([
            Hit(f"{doc_id}/{slug}", 1.0, {"point_type": "concept", "doc_id": doc_id, "slug": slug})
            for doc_id, slug in (("pair-a", "one"), ("pair-a", "two"),
                                 ("pair-b", "one"), ("pair-b", "three"))
        ])
    finally:
        event.remove(get_engine(), "before_cursor_execute", observe)

    assert [hit.payload["content"] for hit in kept] == [
        "pair-a/one", "pair-a/two", "pair-b/one", "pair-b/three"]
    assert parameter_counts and min(parameter_counts) == 8 and max(parameter_counts) == 10
    # PostgreSQL retains the optimization; SQLite uses its shallow tuple-IN.
    query = select(OkfConcept.doc_id).where(_concept_pair_condition([
        ("pair-a", "one"), ("pair-a", "two"), ("pair-b", "one"), ("pair-b", "three")
    ], dialect_name="postgresql"))
    compiled = query.compile(dialect=postgresql.dialect(), compile_kwargs={"render_postcompile": True})
    assert len(compiled.params) == 6


def test_deep_sqlite_hydration_keeps_exact_pairs_across_1200_documents():
    """Widening must not exceed SQLite's OR expression-depth limit."""
    with session_scope() as session:
        for index in range(1200):
            doc_id = f"deep-{index}"
            session.add(Document(id=doc_id, filename=f"{doc_id}.docx"))
            session.add_all([
                OkfConcept(doc_id=doc_id, slug="wanted", title="Wanted", content=f"Canonical {index}"),
                OkfConcept(doc_id=doc_id, slug="other", title="Other", content="Must not leak"),
            ])
    kept, _ = load_visible_retrieval_hits([
        Hit(str(index), 1.0, {"point_type": "concept", "doc_id": f"deep-{index}", "slug": "wanted"})
        for index in range(1200)
    ])
    assert len(kept) == 1200
    assert [hit.payload["content"] for hit in kept] == [f"Canonical {index}" for index in range(1200)]
    assert all(hit.payload["_canonical_verified"] for hit in kept)
