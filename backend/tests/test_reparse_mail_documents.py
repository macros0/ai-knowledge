# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT
"""Unit tests for the bounded mail reparse selector and its queue handoff."""
from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path


_SPEC = importlib.util.spec_from_file_location(
    "reparse_mail_documents",
    Path(__file__).resolve().parents[1] / "scripts" / "reparse_mail_documents.py",
)
reparse = importlib.util.module_from_spec(_SPEC)
sys.modules["reparse_mail_documents"] = reparse
_SPEC.loader.exec_module(reparse)


class _Pipeline:
    def __init__(self):
        self.regenerated: list[str] = []
        self.waited: list[tuple[str, float]] = []

    def regenerate(self, doc_id: str) -> None:
        self.regenerated.append(doc_id)

    def wait_for(self, doc_id: str, timeout: float) -> dict:
        self.waited.append((doc_id, timeout))
        return {"status": "done"}


def _doc(doc_id: str, filename: str) -> dict:
    return {"id": doc_id, "filename": filename, "status": "done"}


def test_scan_recognizes_direct_mail_and_reports_ole_as_candidate(tmp_path: Path):
    (tmp_path / "a.eml").write_bytes(b"From: sender@example.test\n\nBody")
    with zipfile.ZipFile(tmp_path / "b.docx", "w") as archive:
        archive.writestr("word/embeddings/oleObject1.bin", reparse._CFB_SIGNATURE + b"not-decoded")
    (tmp_path / "c.pdf").write_bytes(b"not a mail")

    found = reparse.scan_documents(
        [_doc("c", "c.pdf"), _doc("b", "b.docx"), _doc("a", "a.eml")], tmp_path
    )

    assert [(item["doc_id"], item["confidence"]) for item in found] == [
        ("a", "confirmed_standalone_mail"),
        ("b", "embedded_ole_candidate"),
    ]
    assert all("body" not in item for item in found)


def test_apply_uses_pipeline_only_for_allowed_candidates_and_dry_run_is_inert():
    candidates = [
        {"doc_id": "direct", "confidence": "confirmed_standalone_mail"},
        {"doc_id": "ole", "confidence": "embedded_ole_candidate"},
    ]
    pipeline = _Pipeline()

    dry = reparse.regenerate_candidates(
        candidates, pipeline=pipeline, apply=False, include_embedded_candidates=False, timeout=10
    )
    assert [row["action"] for row in dry] == ["dry_run", "skipped_embedded_candidate"]
    assert pipeline.regenerated == []

    applied = reparse.regenerate_candidates(
        candidates, pipeline=pipeline, apply=True, include_embedded_candidates=False, timeout=12
    )
    assert [row["action"] for row in applied] == ["regenerated", "skipped_embedded_candidate"]
    assert pipeline.regenerated == ["direct"]
    assert pipeline.waited == [("direct", 12)]


def test_embedded_candidate_requires_explicit_apply_opt_in():
    pipeline = _Pipeline()
    results = reparse.regenerate_candidates(
        [{"doc_id": "ole", "confidence": "embedded_ole_candidate"}],
        pipeline=pipeline,
        apply=True,
        include_embedded_candidates=True,
        timeout=3,
    )
    assert results[0]["action"] == "regenerated"
    assert pipeline.regenerated == ["ole"]
