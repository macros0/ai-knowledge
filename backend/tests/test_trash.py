"""Тесты корзины / soft delete (Этап 4a.2): удаление, восстановление, автоочистка."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.models import Document
from app.db.session import session_scope
from app.main import create_app
from app.services import audit, trash
from app.services.audit import AuditService
from app.services.pipeline import get_pipeline
from app.services.registry import DocumentRegistry

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def make_client(tmp_path, monkeypatch, **overrides) -> TestClient:
    defaults: dict = {
        "_env_file": None,
        "data_dir": tmp_path,
        "auth_provider": "simulation",
        "auth_role_groups": ROLE_GROUPS,
        "auth_default_role": "viewer",
        "dedup_enabled": False,
        "auth_sim_users": [
            {"user_id": "sim-admin", "username": "demo.admin", "email": "a@d.local", "groups": ["KB_Admin"]},
        ],
    }
    defaults.update(overrides)
    settings = Settings(**defaults)
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def login(client, username="demo.admin"):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


def no_qdrant(monkeypatch):
    """Отключает запись payload в Qdrant (set_document_deleted) — тесты без Qdrant."""
    monkeypatch.setattr(
        "app.services.vector_store.VectorStore.set_document_deleted",
        lambda self, doc_id, deleted: None,
    )


class TestSoftDelete:
    def test_delete_moves_to_trash_and_hides_from_list(self, client, monkeypatch):
        login(client)
        no_qdrant(monkeypatch)
        DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 123)

        resp = client.delete("/api/documents/0123456789abcdef")
        assert resp.status_code == 200, resp.text

        active = client.get("/api/documents").json()["documents"]
        assert all(d["id"] != "0123456789abcdef" for d in active)

        trash_resp = client.get("/api/documents/trash").json()
        ids = [d["id"] for d in trash_resp["documents"]]
        assert "0123456789abcdef" in ids
        assert trash_resp["retention_days"] == 14
        item = next(d for d in trash_resp["documents"] if d["id"] == "0123456789abcdef")
        assert item["deleted_by"] == "demo.admin"
        assert item["deleted_at"] is not None
        assert item["days_left"] >= 0

    def test_delete_already_in_trash_409(self, client, monkeypatch):
        login(client)
        no_qdrant(monkeypatch)
        DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 123)
        DocumentRegistry().soft_delete("0123456789abcdef", "demo.admin")

        resp = client.delete("/api/documents/0123456789abcdef")
        assert resp.status_code == 409


class TestRestore:
    def test_restore_single(self, client, monkeypatch):
        login(client)
        no_qdrant(monkeypatch)
        DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 123)
        DocumentRegistry().soft_delete("0123456789abcdef", "demo.admin")

        resp = client.post("/api/documents/0123456789abcdef/restore")
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted_at"] is None

        active = client.get("/api/documents").json()["documents"]
        assert any(d["id"] == "0123456789abcdef" for d in active)

        entries = AuditService().query(action_type=audit.DOCUMENT_RESTORE)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "0123456789abcdef"

    def test_restore_not_in_trash_404(self, client, monkeypatch):
        login(client)
        no_qdrant(monkeypatch)
        DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 123)

        resp = client.post("/api/documents/0123456789abcdef/restore")
        assert resp.status_code == 404

    def test_restore_conflict_409_and_force(self, client, monkeypatch):
        from app.services import trash as trash_mod

        login(client)
        no_qdrant(monkeypatch)
        DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 123)
        DocumentRegistry().soft_delete("0123456789abcdef", "demo.admin")
        monkeypatch.setattr(
            trash_mod,
            "find_active_duplicates_for_document",
            lambda did: {"level2": [{"doc": {"id": "x", "filename": "b.pdf"}}], "level3": []},
        )

        resp = client.post("/api/documents/0123456789abcdef/restore")
        assert resp.status_code == 409
        assert resp.json()["code"] == "duplicate"
        assert resp.json()["duplicates"]["level2"]

        resp2 = client.post("/api/documents/0123456789abcdef/restore?force=true")
        assert resp2.status_code == 200, resp2.text

    def test_bulk_restore(self, client, monkeypatch):
        login(client)
        no_qdrant(monkeypatch)
        reg = DocumentRegistry()
        for did in ("1111111111111111", "2222222222222222"):
            reg.create(did, f"{did}.pdf", "application/pdf", 1)
            reg.soft_delete(did, "demo.admin")

        resp = client.post(
            "/api/documents/bulk-restore",
            json={"doc_ids": ["1111111111111111", "2222222222222222"]},
        )
        assert resp.status_code == 200, resp.text
        assert set(resp.json()["restored"]) == {"1111111111111111", "2222222222222222"}

        entries = AuditService().query(action_type=audit.DOCUMENT_BULK_RESTORE)
        assert len(entries) == 2


class TestPurge:
    def test_purge_expired_removes_and_audits(self, client, monkeypatch):
        login(client)
        reg = DocumentRegistry()
        reg.create("1111111111111111", "x.pdf", "application/pdf", 1)
        reg.soft_delete("1111111111111111", "demo.admin")
        with session_scope() as s:
            doc = s.get(Document, "1111111111111111")
            doc.deleted_at = datetime.now(timezone.utc) - timedelta(days=30)

        removed = []

        def _fake_remove_if_deleted(doc_id):
            removed.append(doc_id)
            return True

        # Патчим общий инстанс, а не класс: purge берёт пайплайн через
        # get_pipeline() (services/pipeline.py), подмена класса до него не дойдёт.
        monkeypatch.setattr(get_pipeline(), "remove_if_deleted", _fake_remove_if_deleted)

        n = trash.purge_expired_documents()
        assert n == 1
        assert removed == ["1111111111111111"]

        entries = AuditService().query(action_type=audit.DOCUMENT_AUTO_DELETE)
        assert len(entries) == 1
        assert entries[0]["username"] == "system"
        assert entries[0]["user_id"] == "system"

    def test_purge_skips_within_retention(self, client, monkeypatch):
        login(client)
        reg = DocumentRegistry()
        reg.create("1111111111111111", "x.pdf", "application/pdf", 1)
        reg.soft_delete("1111111111111111", "demo.admin")  # deleted_at = now → within window

        removed = []

        def _fake_remove_if_deleted(doc_id):
            removed.append(doc_id)
            return True

        # Патчим общий инстанс, а не класс: purge берёт пайплайн через
        # get_pipeline() (services/pipeline.py), подмена класса до него не дойдёт.
        monkeypatch.setattr(get_pipeline(), "remove_if_deleted", _fake_remove_if_deleted)

        assert trash.purge_expired_documents() == 0
        assert removed == []

    def test_purge_skips_doc_restored_during_purge(self, client, monkeypatch):
        """Гонка restore-vs-purge (реальный Pipeline): документ, восстановленный
        между purge_expired() и claim'ом, переживает очистку — файлы/БД/audit целы."""
        login(client)
        reg = DocumentRegistry()
        reg.create("1111111111111111", "x.pdf", "application/pdf", 1)
        reg.soft_delete("1111111111111111", "demo.admin")
        with session_scope() as s:
            doc = s.get(Document, "1111111111111111")
            doc.deleted_at = datetime.now(timezone.utc) - timedelta(days=30)

        monkeypatch.setattr(
            "app.services.vector_store.VectorStore.delete_document",
            lambda self, doc_id: None,
        )
        # Симулируем TOCTOU-окно: purge_expired уже вернул id, но документ восстановили.
        monkeypatch.setattr(
            DocumentRegistry, "purge_expired", lambda self, cutoff: ["1111111111111111"]
        )
        reg.restore("1111111111111111")

        assert trash.purge_expired_documents() == 0
        # Документ жив: строка БД на месте.
        assert reg.get("1111111111111111") is not None
        # Автоудаление не залогировано.
        assert AuditService().query(action_type=audit.DOCUMENT_AUTO_DELETE) == []

    def test_registry_delete_if_deleted_guard(self, client, monkeypatch):
        login(client)
        reg = DocumentRegistry()
        reg.create("1111111111111111", "x.pdf", "application/pdf", 1)

        # Активный документ не удаляется (гонка restore).
        assert reg.delete_if_deleted("1111111111111111") is False
        assert reg.get("1111111111111111") is not None

        # Несуществующий — тоже False.
        assert reg.delete_if_deleted("deadbeefdeadbeef") is False

        # В корзине — удаляется вместе с дочерними строками.
        reg.soft_delete("1111111111111111", "demo.admin")
        assert reg.delete_if_deleted("1111111111111111") is True
        assert reg.get("1111111111111111") is None


class TestRegistry:
    def test_list_trash_excludes_active_and_filters(self):
        reg = DocumentRegistry()
        reg.create("1111111111111111", "a.pdf", "application/pdf", 1, uploaded_by="u1")
        reg.create("2222222222222222", "b.pdf", "application/pdf", 1, uploaded_by="u1")
        reg.create("3333333333333333", "c.pdf", "application/pdf", 1, uploaded_by="u2")
        reg.soft_delete("1111111111111111", "u1")
        reg.soft_delete("3333333333333333", "u2")

        docs, total = reg.list_trash()
        assert total == 2
        assert {d["id"] for d in docs} == {"1111111111111111", "3333333333333333"}

        mine, total_mine = reg.list_trash(uploaded_by="u1")
        assert total_mine == 1
        assert mine[0]["id"] == "1111111111111111"

    def test_distinct_uploaders_excludes_deleted(self):
        reg = DocumentRegistry()
        reg.create("1111111111111111", "a.pdf", "application/pdf", 1, uploaded_by="u1")
        reg.create("2222222222222222", "b.pdf", "application/pdf", 1, uploaded_by="u2")
        reg.soft_delete("2222222222222222", "u2")
        assert reg.distinct_uploaders() == ["u1"]


class TestBulkRestoreDedup:
    """bulk_restore не должен обходить дедуп-проверку одиночного restore (Этап 4a.2)."""

    def test_bulk_restore_conflict_skips_doc(self, client, monkeypatch):
        from app.services import trash as trash_mod

        login(client)
        no_qdrant(monkeypatch)
        reg = DocumentRegistry()
        for did in ("1111111111111111", "2222222222222222"):
            reg.create(did, f"{did}.pdf", "application/pdf", 1)
            reg.soft_delete(did, "demo.admin")
        monkeypatch.setattr(
            trash_mod,
            "find_active_duplicates_for_document",
            lambda did: (
                {"level2": [{"doc": {"id": "x", "filename": "twin.pdf"}}], "level3": []}
                if did == "1111111111111111"
                else {"level2": [], "level3": []}
            ),
        )

        resp = client.post(
            "/api/documents/bulk-restore",
            json={"doc_ids": ["1111111111111111", "2222222222222222"]},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["restored"] == ["2222222222222222"]
        assert data["conflicts"][0]["doc_id"] == "1111111111111111"
        assert data["conflicts"][0]["duplicates"]["level2"]
        # Конфликтный документ остался в корзине.
        assert reg.get("1111111111111111")["deleted_at"] is not None
        assert reg.get("2222222222222222")["deleted_at"] is None


class TestUploadWithDedup:
    """Level-1 дедуп на API-пути: активный близнец блокирует (409), близнец в
    корзине — нет (осознанное решение 2026-09-01) + информационное поле."""

    def _upload(self, tmp_path, monkeypatch, *, twin_in_trash):
        from app.api import documents as docs
        from app.services.deduplication import sha256_bytes

        client = make_client(tmp_path, monkeypatch, dedup_enabled=True)
        login(client)
        reg = DocumentRegistry()
        reg.create("1111111111111111", "old.pdf", "application/pdf", 10)
        reg.update("1111111111111111", file_hash=sha256_bytes(b"%PDF-1.4"))
        if twin_in_trash:
            reg.soft_delete("1111111111111111", "demo.admin")

        dest = tmp_path / "0123456789abcdef.pdf"
        dest.write_bytes(b"%PDF-1.4")
        monkeypatch.setattr(
            docs, "save_upload_stream", lambda *a, **k: ("0123456789abcdef", dest, 8)
        )
        monkeypatch.setattr(docs.get_pipeline(), "ingest", lambda *a, **k: None)
        return client, dest

    def test_upload_active_twin_409_and_cleanup(self, tmp_path, monkeypatch):
        client, dest = self._upload(tmp_path, monkeypatch, twin_in_trash=False)

        resp = client.post(
            "/api/documents", files={"file": ("new.pdf", b"%PDF-1.4", "application/pdf")}
        )
        assert resp.status_code == 409, resp.text
        data = resp.json()
        assert data["code"] == "duplicate"
        assert data["duplicate"]["id"] == "1111111111111111"
        # Файл-заготовка удалена (не остаётся мусора).
        assert not dest.exists()

    def test_upload_trash_twin_allowed_with_info(self, tmp_path, monkeypatch):
        client, dest = self._upload(tmp_path, monkeypatch, twin_in_trash=True)

        resp = client.post(
            "/api/documents", files={"file": ("new.pdf", b"%PDF-1.4", "application/pdf")}
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == "0123456789abcdef"
        assert data["duplicate_in_trash"]["id"] == "1111111111111111"
        assert data["duplicate_in_trash"]["filename"] == "old.pdf"

        # Информация о близнеце — и в audit-записи document_upload.
        entries = AuditService().query(action_type=audit.DOCUMENT_UPLOAD)
        assert entries[0]["new_value"]["duplicate_in_trash"]["id"] == "1111111111111111"
