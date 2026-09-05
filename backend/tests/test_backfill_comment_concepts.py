# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Тесты backfill концептов из комментариев рецензентов
(scripts/backfill_comment_concepts.py).

Фейковый Qdrant: scroll/upsert/delete — записывают вызовы. SQLite per-test
из conftest — для registry/okf_concepts. Проверяются все слои записи:
.md-бандл, okf_concepts (БД), Qdrant (upsert новых + удаление орфанов),
registry-счётчик; плюс идемпотентность повторного запуска.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

from app.config import Settings
from app.services.concept_store import fetch_contents
from app.services.registry import get_registry

_SPEC = importlib.util.spec_from_file_location(
    "backfill_comment_concepts",
    Path(__file__).resolve().parents[1] / "scripts" / "backfill_comment_concepts.py",
)
backfill = importlib.util.module_from_spec(_SPEC)
sys.modules["backfill_comment_concepts"] = backfill
_SPEC.loader.exec_module(backfill)


class _Rec:
    def __init__(self, id, payload=None):
        self.id = id
        self.payload = payload or {}


class FakeQdrant:
    def __init__(self, records):
        self.records = records
        self.upserted: list = []
        self.deleted: list = []

    def scroll(self, *, collection_name, limit, with_payload, with_vectors, scroll_filter=None, offset=None):
        # фильтр по doc_id (как реальный scroll_filter)
        doc_id = None
        try:
            cond = scroll_filter.must[0]
            doc_id = cond.match.value
        except Exception:
            pass
        recs = [r for r in self.records if doc_id is None or r.payload.get("doc_id") == doc_id]
        return recs, None

    def upsert(self, *, collection_name, points):
        self.upserted.extend(points)
        for p in points:
            self.records = [r for r in self.records if r.id != p.id] + [_Rec(p.id, p.payload)]

    def delete(self, *, collection_name, points_selector):
        ids = [str(x) for x in points_selector.points]
        self.deleted.extend(ids)
        self.records = [r for r in self.records if str(r.id) not in ids]


class FakeEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def embed_texts(self, texts):
        self.calls.append(list(texts))
        return [[0.0] * 8 for _ in texts]


DOC_ID = "aaaaaaaaaaaaaaaa"
QUESTION = "Какой ТН считать самым свежим?"
ANSWER = "Наибольший табельный является самым свежим."


def _make_thread_docx(path: Path) -> Path:
    """DOCX с тредом «вопрос → ответ» (comments.xml + commentsExtended.xml)."""
    from docx import Document
    from lxml import etree

    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
    W15 = "http://schemas.microsoft.com/office/word/2012/wordml"
    PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
    CT = "http://schemas.openxmlformats.org/package/2006/content-types"

    doc = Document()
    doc.add_paragraph("Алгоритм выбора табельного номера.")
    doc.save(str(path))

    import zipfile

    with zipfile.ZipFile(str(path)) as zf:
        entries = {n: zf.read(n) for n in zf.namelist()}

    docxml = etree.fromstring(entries["word/document.xml"])
    body = docxml.find(f"{{{W}}}body")
    target = next(p for p in body.iter(f"{{{W}}}p") if p.findall(f".//{{{W}}}t"))

    for cid in ("137", "138"):
        cstart = etree.SubElement(target, f"{{{W}}}commentRangeStart")
        cstart.set(f"{{{W}}}id", cid)
        ref = etree.SubElement(etree.SubElement(target, f"{{{W}}}r"), f"{{{W}}}commentReference")
        ref.set(f"{{{W}}}id", cid)
        cend = etree.SubElement(target, f"{{{W}}}commentRangeEnd")
        cend.set(f"{{{W}}}id", cid)
        target.remove(cstart)
        target.insert(0, cstart)
        target.remove(cend)
        target.append(cend)
    entries["word/document.xml"] = etree.tostring(docxml, xml_declaration=True, encoding="UTF-8", standalone=True)

    croot = etree.Element(f"{{{W}}}comments")
    for cid, author, text, pid in (
        ("137", "Волкова Рецензент", QUESTION, "AAAA0001"),
        ("138", "Сагитов Автор", ANSWER, "BBBB0002"),
    ):
        c = etree.SubElement(croot, f"{{{W}}}comment")
        c.set(f"{{{W}}}id", cid)
        c.set(f"{{{W}}}author", author)
        c.set(f"{{{W}}}date", "2026-06-23T10:00:00Z")
        p = etree.SubElement(c, f"{{{W}}}p")
        p.set(f"{{{W14}}}paraId", pid)
        etree.SubElement(etree.SubElement(p, f"{{{W}}}r"), f"{{{W}}}t").text = text
    entries["word/comments.xml"] = etree.tostring(croot, xml_declaration=True, encoding="UTF-8", standalone=True)

    eroot = etree.Element(f"{{{W15}}}commentsEx")
    for pid, parent in (("AAAA0001", None), ("BBBB0002", "AAAA0001")):
        ce = etree.SubElement(eroot, f"{{{W15}}}commentEx")
        ce.set(f"{{{W15}}}paraId", pid)
        if parent:
            ce.set(f"{{{W15}}}paraIdParent", parent)
        ce.set(f"{{{W15}}}done", "1")
    entries["word/commentsExtended.xml"] = etree.tostring(eroot, xml_declaration=True, encoding="UTF-8", standalone=True)

    rels = etree.fromstring(entries["word/_rels/document.xml.rels"])
    for rid, rtype, target_part in (
        ("rIdC1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments", "comments.xml"),
        ("rIdC2", "http://schemas.microsoft.com/office/2011/relationships/commentsExtended", "commentsExtended.xml"),
    ):
        rel = etree.SubElement(rels, f"{{{PKG}}}Relationship")
        rel.set("Id", rid)
        rel.set("Type", rtype)
        rel.set("Target", target_part)
    entries["word/_rels/document.xml.rels"] = etree.tostring(rels, xml_declaration=True, encoding="UTF-8", standalone=True)

    ct = etree.fromstring(entries["[Content_Types].xml"])
    for part, ctype in (
        ("/word/comments.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"),
        ("/word/commentsExtended.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"),
    ):
        ov = etree.SubElement(ct, f"{{{CT}}}Override")
        ov.set("PartName", part)
        ov.set("ContentType", ctype)
    entries["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)

    with zipfile.ZipFile(str(path), "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _md_concept(title: str, tags: list[str], body: str) -> str:
    import yaml

    meta = {
        "type": "concept",
        "title": title,
        "tags": tags,
        "global_tags": [],
        "source_document": {"filename": "doc.docx", "doc_id": DOC_ID},
        "relations": [],
        "attachments": [],
        "chunk_index": 0,
    }
    fm = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)
    return f"---\n{fm}---\n\n# {title}\n\n{body}\n"


def _setup_doc(tmp_path: Path) -> tuple[Settings, Path]:
    """Бандл с обычным концептом + старым LLM-концептом-комментарием;
    чанк со старым форматом комментария; исходник с тредом; registry-строка."""
    settings = Settings(_env_file=None, data_dir=tmp_path, embedding_dimensions=8)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    bundle = settings.okf_dir / DOC_ID
    chunks = bundle / "chunks"
    chunks.mkdir(parents=True)
    (bundle / "obychnoy-koncept.md").write_text(
        _md_concept("Обычный концепт", ["business"], "Содержимое обычного концепта."), encoding="utf-8"
    )
    (bundle / "kommentariy-retsenzenta-llm.md").write_text(
        _md_concept("Комментарий рецензента (старый LLM)", ["review", "comment"], "Старый неполный комментарий."),
        encoding="utf-8",
    )
    (chunks / "chunk_00.md").write_text(
        "Текст чанка.\n\n"
        f"> **Комментарий рецензента (Волкова Рецензент):** {QUESTION}\n\n"
        f"> **Комментарий рецензента (Сагитов Автор):** {ANSWER}\n",
        encoding="utf-8",
    )
    _make_thread_docx(settings.uploads_dir / f"{DOC_ID}.docx")
    get_registry().create(DOC_ID, "doc.docx", "application/vnd.openxmlformats.openxmlformats-officedocument.wordprocessingml.document", 1)
    get_registry().update(DOC_ID, status="done", okf_concept_count=2)
    return settings, bundle


def _fake_services(settings, monkeypatch):
    from app.services.vector_store import VectorStore

    monkeypatch.setattr("app.services.vector_store.get_settings", lambda: settings)
    vs = VectorStore()
    fake_q = FakeQdrant([])
    monkeypatch.setattr(vs, "client", fake_q)
    monkeypatch.setattr(vs, "ensure_collection", lambda: None)
    return FakeEmbedder(), vs, fake_q


class TestProcessDoc:
    def test_full_flow(self, tmp_path, monkeypatch):
        settings, bundle = _setup_doc(tmp_path)
        embedder, vs, fake_q = _fake_services(settings, monkeypatch)
        from app.services.okf_generator import OKFGenerator

        result = backfill.process_doc(
            DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, vs
        )
        assert result["skipped"] == [] and result["error"] is None
        assert result["threads"] == 1
        assert result["removed"] == 1  # старый LLM-концепт-комментарий

        # бандл: обычный концепт сохранён, старый комментарий удалён, тред добавлен
        names = sorted(p.stem for p in bundle.glob("*.md"))
        assert "obychnoy-koncept" in names
        assert "kommentariy-retsenzenta-llm" not in names
        thread_md = [p for p in bundle.glob("*.md") if p.stem.startswith("zamechanie-retsenzenta")]
        assert len(thread_md) == 1
        text = thread_md[0].read_text(encoding="utf-8")
        assert QUESTION in text and ANSWER in text
        assert "chunk_index: 0" in text  # якорь вопроса нашёл чанк

        # БД: концепты заменены полным списком (2 шт.), полный текст треда
        contents = fetch_contents([(DOC_ID, "obychnoy-koncept"), (DOC_ID, thread_md[0].stem)])
        assert "Содержимое обычного концепта." in contents[(DOC_ID, "obychnoy-koncept")]
        assert ANSWER in contents[(DOC_ID, thread_md[0].stem)]

        # Qdrant: апсертнут только новый концепт
        assert len(fake_q.upserted) == 1
        payload = fake_q.upserted[0].payload
        assert payload["point_type"] == "concept"
        assert "review" in payload["tags"]
        assert payload["chunk_index"] == 0
        # registry-счётчик
        assert get_registry().get(DOC_ID)["okf_concept_count"] == 2

    def test_idempotent_second_run(self, tmp_path, monkeypatch):
        settings, bundle = _setup_doc(tmp_path)
        embedder, vs, fake_q = _fake_services(settings, monkeypatch)
        from app.services.okf_generator import OKFGenerator

        gen = OKFGenerator()
        r1 = backfill.process_doc(DOC_ID, "doc.docx", settings, gen, embedder, vs)
        assert r1["threads"] == 1
        files_after_first = sorted(p.name for p in bundle.glob("*.md"))
        upserts_first = len(fake_q.upserted)

        # у старого LLM-концепта была точка — после первого прогона осиротела и удалена
        r2 = backfill.process_doc(DOC_ID, "doc.docx", settings, gen, embedder, vs)
        assert r2["error"] is None
        assert r2["threads"] == 1
        # фильтр 'review' убирает и треды прошлого прогона, пересоздавая их
        # детерминированно — итоговый НАБОР стабилен (идемпотентность по результату)
        files_after_second = sorted(p.name for p in bundle.glob("*.md"))
        assert files_after_first == files_after_second  # тот же набор файлов
        assert len(fake_q.upserted) == upserts_first + 1  # тот же концепт переапсертнут
        assert get_registry().get(DOC_ID)["okf_concept_count"] == 2

    def test_dry_run_no_writes(self, tmp_path, monkeypatch):
        settings, bundle = _setup_doc(tmp_path)
        embedder, vs, fake_q = _fake_services(settings, monkeypatch)
        from app.services.okf_generator import OKFGenerator

        before = sorted(p.name for p in bundle.glob("*.md"))
        result = backfill.process_doc(
            DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, vs, dry_run=True
        )
        assert result["threads"] == 1
        assert sorted(p.name for p in bundle.glob("*.md")) == before
        assert fake_q.upserted == []
        assert get_registry().get(DOC_ID)["okf_concept_count"] == 2  # не менялся

    def test_no_chunks_skipped(self, tmp_path, monkeypatch):
        """Paused/недофинализированный документ (бандл без чанков) — не трогаем."""
        settings, bundle = _setup_doc(tmp_path)
        import shutil

        shutil.rmtree(bundle / "chunks")
        embedder, vs, fake_q = _fake_services(settings, monkeypatch)
        from app.services.okf_generator import OKFGenerator

        result = backfill.process_doc(DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, vs)
        assert result["skipped"] == ["no_chunks"]
        assert fake_q.upserted == []

    def test_old_comment_point_orphaned(self, tmp_path, monkeypatch):
        """Точка старого LLM-концепта-комментария удаляется как осиротевшая;
        chunk-точки не трогаются."""
        settings, bundle = _setup_doc(tmp_path)
        old_md = bundle / "kommentariy-retsenzenta-llm.md"
        from app.services.vector_store import chunk_point_id, concept_point_id

        old_pid = concept_point_id(DOC_ID, old_md.stem)
        chunk_pid = chunk_point_id(DOC_ID, 0)
        embedder, vs, fake_q = _fake_services(
            settings,
            monkeypatch,
        )
        fake_q.records = [
            _Rec(old_pid, {"doc_id": DOC_ID, "point_type": "concept"}),
            _Rec(chunk_pid, {"doc_id": DOC_ID, "point_type": "chunk"}),
        ]
        from app.services.okf_generator import OKFGenerator

        backfill.process_doc(DOC_ID, "doc.docx", settings, OKFGenerator(), embedder, vs)
        assert old_pid in fake_q.deleted
        assert chunk_pid not in fake_q.deleted
