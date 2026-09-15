"""Create one deterministic nonempty corpus fixture for backup/restore CI."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.db.models import Document, DocumentChunk, OkfAttachment, OkfConcept
from app.db.session import session_scope
from app.models.schemas import OkfDocument
from app.services.vector_store import VectorStore

DOC_ID = "cibackupfixture"
ORIGINAL = b"OKF backup fixture original\n"
ATTACHMENT = b"OKF backup fixture attachment\n"
CHUNK = "Backup fixture chunk."
CONCEPT = "Backup fixture concept."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-ci-fixture", action="store_true")
    args = parser.parse_args()
    if not args.confirm_ci_fixture:
        parser.error("refusing to write fixture without --confirm-ci-fixture")

    settings = get_settings()
    original = settings.uploads_dir / f"{DOC_ID}.txt"
    attachment = settings.uploads_dir / DOC_ID / "attachments" / "fixture.txt"
    if original.exists() or attachment.exists():
        raise RuntimeError("backup fixture files already exist")

    original.parent.mkdir(parents=True, exist_ok=True)
    attachment.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(ORIGINAL)
    attachment.write_bytes(ATTACHMENT)
    with session_scope() as session:
        if session.get(Document, DOC_ID) is not None:
            raise RuntimeError("backup fixture document already exists")
        session.add(
            Document(
                id=DOC_ID,
                filename="fixture.txt",
                content_type="text/plain",
                size=len(ORIGINAL),
                status="done",
                total_chunks=1,
                processed_chunks=1,
                okf_concept_count=1,
            )
        )
        session.add(
            DocumentChunk(
                doc_id=DOC_ID,
                chunk_index=0,
                section_title="Fixture",
                content=CHUNK,
                content_hash=hashlib.sha256(CHUNK.encode()).hexdigest(),
                char_count=len(CHUNK),
            )
        )
        session.add(
            OkfConcept(
                doc_id=DOC_ID,
                slug="fixture-concept",
                title="Fixture concept",
                content=CONCEPT,
                tags=["fixture"],
                relations=[],
                chunk_index=0,
            )
        )
        session.add(
            OkfAttachment(
                doc_id=DOC_ID,
                name="fixture.txt",
                kind="other",
                saved_path="attachments/fixture.txt",
                content_type="text/plain",
                size=len(ATTACHMENT),
                sha256=hashlib.sha256(ATTACHMENT).hexdigest(),
                is_processable=False,
                extraction_status="saved",
            )
        )

    store = VectorStore()
    store.ensure_collection()
    vector = [0.0] * settings.embedding_dimensions
    store.index_chunks(DOC_ID, "fixture.txt", [CHUNK], [], [vector], ["Fixture"])
    store.index_concepts(
        DOC_ID,
        [
            OkfDocument(
                filepath="fixture-concept.md",
                metadata={
                    "title": "Fixture concept",
                    "type": "concept",
                    "tags": ["fixture"],
                    "relations": [],
                    "chunk_index": 0,
                },
                content=CONCEPT,
                markdown=CONCEPT,
            )
        ],
        [vector],
    )
    print(DOC_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
