"""Presentation order never changes the canonical identity of existing content."""
from collections import defaultdict

from sqlalchemy import func, select
from app.db.models import DocumentChunk, DocumentGenerationState
from app.services.generation_store import lock_document_write


def reserve_chunk_indices(session, doc_id, count):
    """Commit this reservation before publish; cancellation must not rewind it."""
    if type(count) is not int or count < 0:
        raise ValueError("Invalid chunk reservation")
    if not lock_document_write(session, doc_id, allow_deleted=False):
        raise ValueError("Document unavailable")
    state = session.get(DocumentGenerationState, doc_id)
    if state is None:
        state = DocumentGenerationState(doc_id=doc_id, next_chunk_index=0)
        session.add(state)
    maximum = session.scalar(select(func.max(DocumentChunk.chunk_index)).where(DocumentChunk.doc_id == doc_id))
    start = max(state.next_chunk_index or 0, (maximum + 1) if maximum is not None else 0)
    state.next_chunk_index = start + count
    session.flush()
    return range(start, start + count)


def assign_scope_display_order(sources, chunks, concepts):
    by_id = {s["source_id"]: s for s in sources}
    if len(by_id) != len(sources) or "root" not in by_id:
        raise ValueError("Invalid source ordering tree")
    children = defaultdict(list)
    for source in sources:
        sid, parent = source["source_id"], source.get("parent_source_id")
        if sid != "root":
            if parent not in by_id:
                raise ValueError("Missing source ancestor")
            children[parent].append(source)
    order, visiting = {}, set()
    def visit(sid):
        if sid in visiting:
            raise ValueError("Source cycle")
        visiting.add(sid)
        order[sid] = len(order)
        for child in sorted(children[sid], key=lambda s: (s.get("ordinal", 0),
                (s.get("metadata") or {}).get("source_key", s["source_id"]))):
            visit(child["source_id"])
    visit("root")
    if len(order) != len(sources):
        raise ValueError("Disconnected source tree")
    sorted_chunks = sorted(chunks, key=lambda r: (order[r["source_id"]], r.get("local_order", r["chunk_index"])))
    by_chunk = {}
    for position, chunk in enumerate(sorted_chunks):
        chunk["display_order"] = position
        by_chunk[chunk["chunk_index"]] = position
    indexed = list(enumerate(concepts))
    indexed.sort(key=lambda pair: (order[pair[1]["source_id"]],
        by_chunk.get(pair[1].get("chunk_index"), -1),
        pair[1].get("local_order", pair[1].get("display_order") if pair[1].get("display_order") is not None else pair[0])))
    for position, (_, concept) in enumerate(indexed):
        concept["display_order"] = position
