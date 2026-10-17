"""Real parser, publication, SQL and dense/BM25 paths; no external model/service."""

import importlib.util
import io
import json
from pathlib import Path
import struct
import zipfile
import zlib
import shutil
import hashlib

import py7zr
import pytest
from sqlalchemy import select

from app.db.models import DocumentChunk, DocumentSource, DocumentTableRow, OkfConcept, DocumentGenerationState
from app.db.session import session_scope
from app.services.processing_scope import create_review, snapshot_from_review
from docparser.attachment_inventory import inspect_attachment_inventory
from docparser.attachment_policy import AssessmentLimits, TraversalLimits
from tests.test_generation_pipeline import DOC_ID, pipeline_env as shared_pipeline_env

pipeline_env = shared_pipeline_env


def stored_rar(members):
    def header(kind, flags, body):
        data = struct.pack("<BHH", kind, flags, len(body) + 7) + body
        return struct.pack("<H", zlib.crc32(data) & 0xFFFF) + data

    result = b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\0" * 6)
    for name, data in members:
        encoded = name.encode("ascii")
        body = (
            struct.pack(
                "<IIBIIBBHI",
                len(data),
                len(data),
                3,
                zlib.crc32(data),
                0,
                20,
                0x30,
                len(encoded),
                0x20,
            )
            + encoded
        )
        result += header(0x74, 0x8000, body) + data
    return result + header(0x7B, 0, b"")


@pytest.mark.parametrize("kind", ["zip", "7z", "rar"])
def test_nested_archive_exclusions_survive_canonical_publication_and_search(
    pipeline_env, tmp_path, kind
):
    spec = importlib.util.spec_from_file_location(
        "scope_synthetic_inputs",
        Path(__file__).parents[2] / "doc-parser/tests/attachment_fixtures.py",
    )
    synthetic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synthetic)
    members = [
        ("heavy.xlsx", synthetic.spreadsheet(rows=700, text="EXCLUDED_UNIQUE_CANARY")),
        ("light.xlsx", synthetic.spreadsheet(rows=2, text="INCLUDED_UNIQUE_EVIDENCE")),
    ]
    buffer = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in members:
                archive.writestr(name, data)
    elif kind == "7z":
        with py7zr.SevenZipFile(buffer, "w") as archive:
            for name, data in members:
                archive.writestr(data, name)
    else:
        buffer.write(stored_rar(members))
    pipeline, path, _ = pipeline_env
    pipeline.settings.okf_table_llm_classify = False
    synthetic.office_container(
        path, [("approval.eml", synthetic.mail_bytes([("files." + kind, buffer.getvalue())]))]
    )
    spool = tmp_path / "review-spool"
    inventory = inspect_attachment_inventory(
        path, "decision.docx", limits=AssessmentLimits(), traversal=TraversalLimits(), attachments_dir=spool
    )
    assert [n.kind for n in inventory.nodes] == ["mail", "archive", "xlsx", "xlsx"]
    from app.services.generation_files import processing_review_paths

    with session_scope() as session:
        from app.services.generation_store import begin_generation
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        review = create_review(session, DOC_ID, inventory, base_generation_id=active, actor="editor")
        snapshot = snapshot_from_review(review)
        review_root = processing_review_paths(pipeline.settings, DOC_ID, review.id)
        generation = begin_generation(session, DOC_ID)
        generation.processing_scope_json = snapshot
        generation_id = generation.id
        review.generation_id = generation_id
        review.state = "submitted"
    shutil.copytree(spool, review_root / "attachments")
    assert [d["effective_included"] for d in snapshot["decisions"]] == [True, True, False, True]
    pipeline._process(DOC_ID, path, "decision.docx", [], resume=False, processing_scope=snapshot,
                      admitted_generation_id=generation_id)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        contents = [
            r.content
            for model in (DocumentChunk, OkfConcept)
            for r in session.scalars(select(model).where(model.doc_id == DOC_ID))
        ]
        cells = [
            r.cells
            for r in session.scalars(
                select(DocumentTableRow).where(DocumentTableRow.doc_id == DOC_ID)
            )
        ]
        assert "EXCLUDED_UNIQUE_CANARY" not in json.dumps([contents, cells])
        assert "INCLUDED_UNIQUE_EVIDENCE" in json.dumps(contents)
        excluded = [
            r
            for r in session.scalars(select(DocumentSource).where(DocumentSource.doc_id == DOC_ID))
            if (r.metadata_json or {}).get("processing_included") is False
        ]
        assert len(excluded) == 1
        assert excluded[0].saved_path is not None, "already extracted review original must survive publish"
        assert excluded[0].artifact_kind == "original"
        saved_path = excluded[0].saved_path
        assert (pipeline.settings.uploads_dir / DOC_ID / saved_path).read_bytes() == members[0][1]
        active = session.get(DocumentGenerationState, DOC_ID).active_generation_id
        # Replacement review and its cleanup cannot own the published original.
        replacement = create_review(session, DOC_ID, inventory, base_generation_id=active, actor="editor")
        replacement.state = "canceled"
    shutil.rmtree(review_root)
    with session_scope() as session:
        from app.db.models import Document
        from app.api.documents import _source_download_path
        source = session.scalar(select(DocumentSource).where(DocumentSource.doc_id == DOC_ID,
                                                              DocumentSource.saved_path == saved_path))
        target, name = _source_download_path(session, session.get(Document, DOC_ID), source)
        assert name == "heavy.xlsx"
        assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(members[0][1]).digest()
    # A later generation reuses the selected snapshot after its original review
    # was cleaned up; the published original is now the durable source.
    pipeline._process(DOC_ID, path, "decision.docx", [], resume=False, processing_scope=snapshot)
    assert pipeline.registry.get(DOC_ID)["status"] == "done"
    with session_scope() as session:
        retained = next(r for r in session.scalars(select(DocumentSource).where(DocumentSource.doc_id == DOC_ID))
                        if (r.metadata_json or {}).get("source_key") == excluded[0].metadata_json["source_key"])
        assert retained.saved_path != saved_path
        assert (pipeline.settings.uploads_dir / DOC_ID / retained.saved_path).read_bytes() == members[0][1]
    from app.services.context_builder import merge_and_format
    from app.services.sparse import to_sparse_vector

    query = "EXCLUDED_UNIQUE_CANARY"
    for branches in ({"dense"}, {"bm25"}, {"dense", "bm25"}):
        hits = pipeline.vector_store.search_composite(
            dense_vec=pipeline.embedder.embed(query),
            sparse_vec=to_sparse_vector(query),
            tags=None,
            branches=branches,
            top_k=100,
            doc_ids=[DOC_ID],
        )
        blocks = merge_and_format(hits, settings=pipeline.settings, query=query)
        assert all(query not in block["content"] for block in blocks)
