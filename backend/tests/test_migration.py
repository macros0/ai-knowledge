"""Тесты concept_store (slim-payload) и скрипта миграции JSON->БД."""
import json
from pathlib import Path

from app.db.models import Document, OkfConcept
from app.db.session import session_scope
from app.models.schemas import OkfDocument
from app.services.concept_store import fetch_contents, replace_concepts
from app.services.tag_registry import TagRegistry


class TestConceptStore:
    def test_replace_and_fetch(self):
        with session_scope() as s:
            replace_concepts(s, "doc1", [
                OkfDocument(filepath="/x/auth-flow.md", metadata={"title": "Auth Flow", "type": "concept", "tags": ["t1"], "relations": ["other"], "chunk_index": 0}, content="полный текст", markdown="")
            ])
        assert fetch_contents([("doc1", "auth-flow")]) == {("doc1", "auth-flow"): "полный текст"}
        assert fetch_contents([("doc1", "missing")]) == {}

    def test_replace_clears_previous(self):
        with session_scope() as s:
            replace_concepts(s, "doc1", [
                OkfDocument(filepath="/x/a.md", metadata={}, content="one", markdown=""),
                OkfDocument(filepath="/x/b.md", metadata={}, content="two", markdown=""),
            ])
        with session_scope() as s:
            replace_concepts(s, "doc1", [
                OkfDocument(filepath="/x/c.md", metadata={}, content="three", markdown=""),
            ])
        with session_scope() as s:
            slugs = {r for (r,) in s.query(OkfConcept.slug).filter(OkfConcept.doc_id == "doc1").all()}
        assert slugs == {"c"}


class TestMigrationScript:
    def _make_data(self, tmp_path: Path) -> Path:
        data = tmp_path / "data"
        (data / "okf_bundles" / "doc1").mkdir(parents=True)
        (data / "staging" / "doc1").mkdir(parents=True)

        (data / "documents.json").write_text(
            json.dumps({"doc1": {"id": "doc1", "filename": "a.docx", "content_type": "doc", "size": 10,
                                  "status": "done", "tags": ["proxmox"]}}),
            encoding="utf-8",
        )
        (data / "tags.json").write_text(json.dumps({"proxmox": 1, "extra": 1}), encoding="utf-8")
        (data / "okf_bundles" / "doc1" / "concept.md").write_text(
            "---\ntitle: Concept\ntype: concept\ntags: [llm]\nrelations: []\nchunk_index: 0\n---\n\n# Concept\n\nтело концепта\n",
            encoding="utf-8",
        )
        (data / "staging" / "doc1" / "manifest.json").write_text(
            json.dumps({"task_id": "doc1", "status": "in_progress", "total_chunks": 2,
                        "processed_chunks": [0], "used_slugs": [], "global_tags": ["proxmox"],
                        "chunks_data": {}}),
            encoding="utf-8",
        )
        return data

    def test_migrate_documents_concepts_staging_tags(self, tmp_path):
        from scripts.migrate_json_to_db import (
            _migrate_attachments,
            _migrate_concepts,
            _migrate_documents,
            _migrate_staging,
            _migrate_tags,
        )

        data = self._make_data(tmp_path)
        assert _migrate_documents(data) == 1
        assert _migrate_concepts(data) == 1
        assert _migrate_staging(data) == 1
        # «proxmox» уже создан _migrate_documents (через get_or_create_ids) —
        # _migrate_tags доносит только «extra».
        assert _migrate_tags(data) == 1
        assert _migrate_attachments(data) == 0

        with session_scope() as s:
            assert s.query(Document.id).count() == 1
            assert s.query(OkfConcept.slug).filter(OkfConcept.doc_id == "doc1").scalar() == "concept"
        assert [(t["name"], t["count"]) for t in TagRegistry().all()] == [
            ("extra", 0),
            ("proxmox", 1),
        ]

    def test_migrate_idempotent_skips_existing(self, tmp_path):
        from scripts.migrate_json_to_db import _migrate_documents

        data = self._make_data(tmp_path)
        assert _migrate_documents(data) == 1
        assert _migrate_documents(data) == 0
