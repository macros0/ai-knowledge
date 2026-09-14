from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_scripts import stage8_manifest


def test_runtime_snapshot_identifies_contour_without_database_credentials():
    settings = SimpleNamespace(
        knowledge_profile="Измерительная база Stage 8",
        database_url="postgresql+psycopg://user:secret@127.0.0.1:5432/okf_stage8_test",
        database_url_dev=None,
        data_dir=Path("./tests/tmp/stage8-data"),
        qdrant_collection="okf_knowledge_stage8_test",
    )

    snapshot = stage8_manifest._runtime_snapshot(settings)

    assert snapshot == {
        "knowledge_profile": "Измерительная база Stage 8",
        "database_name": "okf_stage8_test",
        "data_dir": str(Path("tests/tmp/stage8-data")),
        "qdrant_collection": "okf_knowledge_stage8_test",
    }
    assert "secret" not in str(snapshot)


def test_label_snapshot_includes_document_and_source_expectations_and_approval():
    payload = {
        "labels": {"approved": True, "reviewer": "subject-expert"},
        "cases": [
            {
                "id": "case-1",
                "expected_canonicals": ["IT0003"],
                "forbidden_canonicals": [],
                "relevance": {"doc-1||0": "relevant"},
                "expected_documents": ["doc-1"],
                "mandatory_sources": [{"doc_id": "doc-1", "slug": None, "chunk_index": 0}],
            }
        ],
    }

    labels, approved = stage8_manifest._label_snapshot(payload)

    assert approved is True
    assert labels["case-1"]["expected_documents"] == ["doc-1"]
    assert labels["case-1"]["mandatory_sources"][0]["doc_id"] == "doc-1"

    changed = {**payload, "cases": [{**payload["cases"][0], "expected_documents": ["doc-2"]}]}
    changed_labels, _ = stage8_manifest._label_snapshot(changed)
    assert stage8_manifest._digest(labels) != stage8_manifest._digest(changed_labels)
