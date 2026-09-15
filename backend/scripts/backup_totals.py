"""Write strict restore totals for the configured database and Qdrant collection."""

from __future__ import annotations

import json
from pathlib import Path

from check_integrity import check_doc, iter_done_docs

from app.config import get_settings
from app.services.vector_store import VectorStore


def collect_totals() -> dict[str, int]:
    settings = get_settings()
    vector_store = VectorStore()
    results = [
        check_doc(doc_id, chunks, concepts, settings, vector_store=vector_store)
        for doc_id, _filename, chunks, concepts in iter_done_docs()
    ]
    if any(result["qdrant_unavailable"] for result in results):
        raise RuntimeError("Qdrant is unavailable; refusing to create a strict backup manifest")
    return {
        "documents": len(results),
        "chunks": sum(result["db_chunks"] for result in results),
        "concepts": sum(result["db_concepts"] for result in results),
        "qdrant_points": sum(result["qdrant_points"] or 0 for result in results),
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(collect_totals(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
