"""Seed an isolated disposable stack with synthetic searchable chunks only."""

import argparse
from hashlib import sha256

from app.db.models import Document, DocumentChunk
from app.db.session import session_scope
from app.services.vector_store import VectorStore


def seed(count: int) -> None:
    if count not in (1, 100):
        raise ValueError("Only the documented synthetic corpus sizes are supported")
    store = VectorStore()
    store.ensure_collection()
    dimensions = store.settings.embedding_dimensions
    vector = [0.0] * dimensions
    vector[0] = 1.0
    for i in range(count):
        doc_id = f"diagperf{i:08x}"
        text = f"Synthetic diagnostic restore drill document {i:03d}. " * 8
        with session_scope() as db:
            if db.get(Document, doc_id) is None:
                db.add(Document(id=doc_id, filename=f"synthetic-{i:03d}.txt", status="done",
                                total_chunks=1, processed_chunks=1))
                db.add(DocumentChunk(doc_id=doc_id, chunk_index=0,
                                     section_title="Synthetic diagnostic restore drill",
                                     content=text, content_hash=sha256(text.encode()).hexdigest(),
                                     char_count=len(text)))
        store.index_chunks(doc_id, f"synthetic-{i:03d}.txt", [text], [], [vector],
                           section_titles=["Synthetic diagnostic restore drill"])
    print(f"seeded={count} synthetic_docs_only=true", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, choices=(1, 100), required=True)
    seed(parser.parse_args().count)
