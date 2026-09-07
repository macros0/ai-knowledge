"""Тесты backfill_db_store и check_integrity (Этап 2b, Фаза 2)."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings
from app.db.models import Document, DocumentChunk, OkfAttachment, OkfConcept
from app.db.session import session_scope


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path)


def _make_done_doc(doc_id: str, filename: str = "test.doc", **kw) -> None:
    with session_scope() as s:
        s.add(
            Document(id=doc_id, filename=filename, content_type="doc", size=10, status="done", **kw)
        )


class TestBackfillChunks:
    def test_backfill_chunks_from_bundle(self, tmp_path):
        from scripts.backfill_db_store import backfill_chunks

        settings = _settings(tmp_path)
        doc_id = "chunkdoc00000001"
        _make_done_doc(doc_id)

        bundle = settings.okf_dir / doc_id / "chunks"
        bundle.mkdir(parents=True)
        text = "# Раздел\nтекст чанка"
        (bundle / "chunk_00.md").write_text(text, encoding="utf-8")
        (bundle / "chunk_01.md").write_text("второй", encoding="utf-8")

        n = backfill_chunks(doc_id, "test.doc", settings)
        assert n == 2

        with session_scope() as s:
            rows = (
                s.query(DocumentChunk)
                .filter(DocumentChunk.doc_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
                .all()
            )
        assert len(rows) == 2
        assert rows[0].content == text
        assert rows[0].section_title == "Раздел"
        assert rows[0].content_hash == hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert rows[0].char_count == len(text)

        # Идемпотентность: уже заполнено -> 0 (скип).
        assert backfill_chunks(doc_id, "test.doc", settings) == 0


class TestBackfillAttachments:
    def test_backfill_attachments_normalizes_and_copies(self, tmp_path):
        from scripts.backfill_db_store import backfill_attachments

        settings = _settings(tmp_path)
        doc_id = "attdoc000000001"
        _make_done_doc(doc_id)

        bundle_dir = settings.okf_dir / doc_id
        (bundle_dir / "attachments").mkdir(parents=True)
        payload = b"\x89PNG\r\n\x1a\nfakepng"
        (bundle_dir / "attachments" / "diagram.png").write_bytes(payload)
        (bundle_dir / "c.md").write_text(
            "---\ntitle: C\ntype: concept\n"
            "attachments:\n- name: diagram.png\n  kind: image\n  caption: ''\n"
            "  saved_path: diagram.png\n---\n\n# C\n\nbody\n",
            encoding="utf-8",
        )

        n = backfill_attachments(doc_id, settings)
        assert n == 1

        with session_scope() as s:
            rows = s.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).all()
        assert len(rows) == 1
        a = rows[0]
        assert a.saved_path == "attachments/diagram.png"
        assert a.kind == "image"
        assert a.sha256 == hashlib.sha256(payload).hexdigest()
        assert a.size == len(payload)

        dst = settings.uploads_dir / doc_id / "attachments" / "diagram.png"
        assert dst.is_file()
        assert dst.read_bytes() == payload

        assert backfill_attachments(doc_id, settings) == 0


    def test_windows_saved_path_normalized(self, tmp_path):
        """Легаси-бандл с windows-путём: в БД не должен попасть путь целиком.

        Path().name берёт флейвор текущей ОС, поэтому на Linux/macOS такой
        saved_path не резался вовсе и оседал в БД (инцидент 2026-09-07).
        """
        from scripts.backfill_db_store import backfill_attachments

        settings = _settings(tmp_path)
        doc_id = "attdoc000000002"
        _make_done_doc(doc_id)

        bundle_dir = settings.okf_dir / doc_id
        (bundle_dir / "attachments").mkdir(parents=True)
        (bundle_dir / "attachments" / "embedded-0.pdf").write_bytes(b"%PDF-1.4 fake")
        win = "C:\\Users\\alexey\\ai-workspace\\data\\uploads\\doc1\\attachments\\embedded-0.pdf"
        (bundle_dir / "c.md").write_text(
            "---\ntitle: C\ntype: concept\n"
            "attachments:\n- name: oleObject2.bin\n  kind: pdf\n  caption: ''\n"
            f"  saved_path: '{win}'\n---\n\n# C\n\nbody\n",
            encoding="utf-8",
        )

        assert backfill_attachments(doc_id, settings) == 1

        with session_scope() as s:
            a = s.query(OkfAttachment).filter(OkfAttachment.doc_id == doc_id).one()
        assert a.saved_path == "attachments/embedded-0.pdf"
        assert "C:" not in a.saved_path


class TestBackfillGeneratedAt:
    def test_backfill_generated_at_from_frontmatter(self, tmp_path):
        from scripts.backfill_db_store import backfill_generated_at

        settings = _settings(tmp_path)
        doc_id = "gendoc000000001"
        _make_done_doc(doc_id)

        bundle_dir = settings.okf_dir / doc_id
        bundle_dir.mkdir(parents=True)
        (bundle_dir / "a.md").write_text(
            "---\ntitle: A\ntype: concept\ncreated_at: '2025-11-05'\n---\n\n# A\n\nbody\n",
            encoding="utf-8",
        )
        with session_scope() as s:
            s.add(OkfConcept(doc_id=doc_id, slug="a", title="A", content="body"))

        updated = backfill_generated_at(doc_id, settings)
        assert updated == 1

        with session_scope() as s:
            c = s.query(OkfConcept).filter(OkfConcept.doc_id == doc_id).one()
            # SQLite не хранит tzinfo при round-trip (на Postgres TIMESTAMPTZ сохраняет),
            # поэтому сверяем компоненты даты, а не объект с tz.
            assert c.generated_at is not None
            assert (c.generated_at.year, c.generated_at.month, c.generated_at.day) == (2025, 11, 5)

        # generated_at уже проставлен -> 0 без --force.
        assert backfill_generated_at(doc_id, settings) == 0


class TestCheckIntegrity:
    def test_db_checks_ok(self, tmp_path):
        from scripts.check_integrity import check_doc

        settings = _settings(tmp_path)
        doc_id = "intdoc000000001"
        _make_done_doc(doc_id, total_chunks=1, okf_concept_count=1)
        with session_scope() as s:
            s.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="x", char_count=1))
            s.add(OkfConcept(doc_id=doc_id, slug="a", title="A", content="y"))

        att = settings.uploads_dir / doc_id / "attachments"
        att.mkdir(parents=True)
        (att / "d.png").write_bytes(b"zzz")
        with session_scope() as s:
            s.add(OkfAttachment(doc_id=doc_id, name="d.png", saved_path="attachments/d.png"))

        r = check_doc(doc_id, 1, 1, settings, vector_store=None)
        assert r["ok"], r["issues"]

    def test_detects_orphan_file_and_count_mismatch(self, tmp_path):
        from scripts.check_integrity import check_doc

        settings = _settings(tmp_path)
        doc_id = "intdoc000000002"
        # total_chunks=2, но в БД 1 чанк -> расхождение.
        _make_done_doc(doc_id, total_chunks=2, okf_concept_count=0)
        with session_scope() as s:
            s.add(DocumentChunk(doc_id=doc_id, chunk_index=0, content="x", char_count=1))

        att = settings.uploads_dir / doc_id / "attachments"
        att.mkdir(parents=True)
        (att / "orphan.png").write_bytes(b"ooo")

        r = check_doc(doc_id, 2, 0, settings, vector_store=None)
        assert not r["ok"]
        joined = "\n".join(r["issues"])
        assert "total_chunks" in joined
        assert "orphan.png" in joined
