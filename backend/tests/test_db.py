"""Тесты слоя БД: репозитории документов/тегов (изолированная SQLite из conftest)."""
from app.db.models import Document, OkfConcept
from app.db.session import session_scope
from app.services.registry import DocumentRegistry


class TestDocumentRegistry:
    def test_create_get_list_delete(self):
        reg = DocumentRegistry()
        reg.create("doc1", "a.docx", "doc", 10, tags=["proxmox"], uploaded_by="demo.user")
        reg.create("doc2", "b.pdf", "pdf", 20)

        assert reg.get("doc1")["tags"] == ["proxmox"]
        assert reg.get("doc1")["status"] == "uploaded"
        assert {d["id"] for d in reg.list()} == {"doc1", "doc2"}

        reg.update("doc1", status="done", okf_concept_count=3)
        assert reg.get("doc1")["status"] == "done"
        assert reg.get("doc1")["okf_concept_count"] == 3

        assert reg.delete("doc1") is True
        assert reg.get("doc1") is None
        assert reg.delete("nope") is False

    def test_update_tags_replaces(self):
        reg = DocumentRegistry()
        reg.create("doc1", "a.docx", "doc", 10, tags=["a", "b"])
        reg.update("doc1", tags=["c", "d"])
        assert sorted(reg.get("doc1")["tags"]) == ["c", "d"]

    def test_uploaded_by_persisted(self):
        reg = DocumentRegistry()
        reg.create("doc1", "a.docx", "doc", 10, uploaded_by="demo.editor")
        with session_scope() as s:
            assert s.get(Document, "doc1").uploaded_by == "demo.editor"

    def test_delete_cascades_concepts(self):
        reg = DocumentRegistry()
        reg.create("doc1", "a.docx", "doc", 10)
        with session_scope() as s:
            s.add(OkfConcept(doc_id="doc1", slug="auth-flow", title="T", content="body"))
        with session_scope() as s:
            assert s.query(OkfConcept).filter(OkfConcept.doc_id == "doc1").count() == 1
        reg.delete("doc1")
        with session_scope() as s:
            assert s.query(OkfConcept).filter(OkfConcept.doc_id == "doc1").count() == 0

    def test_reset_stale_statuses(self):
        reg = DocumentRegistry()
        reg.create("doc1", "a.docx", "doc", 10)
        reg.update("doc1", status="processing")
        reg.reset_stale_statuses()
        assert reg.get("doc1")["status"] == "paused"


def test_session_scope_initializes_engine_without_deadlock(monkeypatch, tmp_path):
    """Регрессия: первый session_scope без предварительного init_db не дедлочит.

    get_session_factory() вызывает get_engine() внутри своей критической секции —
    нереентерабельный Lock здесь давал бы deadlock (движок создаётся только при
    первом обращении). Проверяем путь, когда _engine ещё не инициализирован.
    """
    import app.db.session as ds
    from app.config import Settings as _Settings
    from sqlalchemy import text

    monkeypatch.setattr(
        "app.config.get_settings",
        lambda: _Settings(
            _env_file=None,
            data_dir=tmp_path,
            database_url=f"sqlite:///{(tmp_path / 'session-scope.db').as_posix()}",
        ),
    )
    ds._engine = None
    ds._session_factory = None

    from app.db.session import session_scope

    with session_scope() as s:
        assert s.execute(text("SELECT 1")).fetchone() == (1,)
