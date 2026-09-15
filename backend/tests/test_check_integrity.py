from __future__ import annotations

import hashlib

from scripts.check_integrity import manifest_file_issues, strict_issues, strict_runtime_issues


def test_strict_integrity_rejects_unavailable_qdrant_and_unexpected_empty_corpus():
    issues = strict_issues([], {"totals": {"documents": 1, "chunks": 2, "concepts": 3, "qdrant_points": 5}})
    assert "Qdrant is unavailable" in issues
    assert "documents=0 != manifest=1" in issues


def test_strict_integrity_accepts_matching_counts():
    results = [
        {
            "ok": True,
            "qdrant_unavailable": False,
            "db_chunks": 2,
            "db_concepts": 3,
            "qdrant_points": 5,
        }
    ]
    assert strict_issues(results, {"totals": {"documents": 1, "chunks": 2, "concepts": 3, "qdrant_points": 5}}) == []


def test_strict_integrity_reports_unavailable_qdrant_for_nonempty_corpus():
    results = [
        {
            "ok": True,
            "qdrant_unavailable": True,
            "db_chunks": 2,
            "db_concepts": 3,
            "qdrant_points": None,
        }
    ]

    issues = strict_issues(
        results,
        {"totals": {"documents": 1, "chunks": 2, "concepts": 3, "qdrant_points": 5}},
        qdrant_available=False,
    )

    assert "Qdrant is unavailable" in issues


def test_strict_integrity_allows_an_intentionally_empty_corpus_when_qdrant_is_reachable():
    assert strict_issues(
        [], {"totals": {"documents": 0, "chunks": 0, "concepts": 0, "qdrant_points": 0}}, qdrant_available=True
    ) == []


def test_strict_runtime_without_manifest_only_requires_reachable_qdrant():
    assert strict_runtime_issues([], qdrant_available=True) == []
    assert strict_runtime_issues([], qdrant_available=False) == ["Qdrant is unavailable"]


def test_manifest_file_issues_checks_archived_originals_and_attachments(tmp_path):
    original = tmp_path / "uploads" / "doc-1.pdf"
    attachment = tmp_path / "uploads" / "doc-1" / "attachments" / "appendix.docx"
    original.parent.mkdir(parents=True)
    attachment.parent.mkdir(parents=True)
    original.write_bytes(b"original")
    attachment.write_bytes(b"attachment")
    manifest = {
        "files": [
            {"path": "uploads/doc-1.pdf", "sha256": hashlib.sha256(b"original").hexdigest()},
            {
                "path": "uploads/doc-1/attachments/appendix.docx",
                "sha256": hashlib.sha256(b"attachment").hexdigest(),
            },
        ]
    }

    assert manifest_file_issues(manifest, tmp_path) == []

    attachment.write_bytes(b"changed")
    assert manifest_file_issues(manifest, tmp_path) == [
        "checksum mismatch: uploads/doc-1/attachments/appendix.docx"
    ]
