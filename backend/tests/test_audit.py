"""Тесты журнала ИБ (audit_log): append-only, enum action_type, фильтры."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.audit import (
    ACTION_TYPES,
    DOCUMENT_BULK_DELETE,
    DOCUMENT_DELETE,
    DOCUMENT_RESUME,
    DOCUMENT_UPLOAD,
    AuditService,
    get_audit,
    record,
)
from app.services.registry import DocumentRegistry

EXPECTED_ACTION_TYPES = {
    "document_upload",
    "document_delete",
    "document_bulk_delete",
    "document_regenerate",
    "document_bulk_regenerate",
    "document_resume",
    "document_development_set",
    "document_tags_update",
    "document_bulk_tags_update",
    "job_approve",
    "job_cancel",
    "user_block",
    "user_unblock",
    "development_create",
    "development_update",
    "development_delete",
    "attribute_create",
    "attribute_delete",
    "tag_delete",
    "tag_cleanup",
    "document_restore",
    "document_bulk_restore",
    "document_auto_delete",
    "chat_history_view",
    "chat_history_auto_delete",
    "document_export",
    "locale_create",
    "locale_update",
    "locale_activate",
    "locale_disable",
    "stopwords_import",
    "stopwords_update",
    "stopwords_rollback",
    "tag_translation_update",
    "tag_translation_review",
    "translations_backfill",
    "ui_dictionary_import",
    "ui_dictionary_rollback",
}


class _User:
    user_id = "u-admin"
    username = "demo.admin"


class TestAuditService:
    def test_append_and_query_roundtrip(self):
        svc = AuditService()
        svc.append(
            action_type=DOCUMENT_DELETE,
            user_id="u1",
            username="demo.admin",
            target_type="document",
            target_id="doc1",
            ip_address="127.0.0.1",
            old_value={"filename": "a.docx"},
        )
        entries = svc.query(user_id="u1")
        assert len(entries) == 1
        assert entries[0]["action_type"] == DOCUMENT_DELETE
        assert entries[0]["target_id"] == "doc1"
        assert entries[0]["username"] == "demo.admin"
        assert entries[0]["old_value"] == {"filename": "a.docx"}

    def test_query_filters_by_action_type(self):
        svc = AuditService()
        svc.append(action_type=DOCUMENT_DELETE, user_id="u1", target_id="d1")
        svc.append(action_type=DOCUMENT_BULK_DELETE, user_id="u2", target_id="d2")
        assert len(svc.query(action_type=DOCUMENT_DELETE)) == 1
        assert len(svc.query(user_id="u2")) == 1
        assert len(svc.query(target_id="d1")) == 1

    def test_unknown_action_type_rejected(self):
        with pytest.raises(ValueError):
            AuditService().append(action_type="not_a_real_action")

    def test_append_only_no_update_delete_methods(self):
        assert not hasattr(AuditService, "update")
        assert not hasattr(AuditService, "delete")
        assert not hasattr(AuditService, "remove")

    def test_action_types_enum_frozen(self):
        assert ACTION_TYPES == EXPECTED_ACTION_TYPES


def test_record_uses_user_attributes():
    record(_User(), DOCUMENT_DELETE, "document", target_id="d1")
    entries = get_audit().query(target_id="d1")
    assert entries[0]["username"] == "demo.admin"
    assert entries[0]["user_id"] == "u-admin"


# ---------- Интеграция: хуки аудита в upload/resume (через API) ----------

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}


def make_client(tmp_path: Path, monkeypatch, **overrides) -> TestClient:
    defaults: dict = {
        "_env_file": None,
        "data_dir": tmp_path,
        "auth_provider": "simulation",
        "auth_role_groups": ROLE_GROUPS,
        "auth_default_role": "viewer",
        "dedup_enabled": False,  # дедупликация покрыта отдельно (test_deduplication.py)
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


class TestUploadAuditHook:
    def test_upload_records_document_upload(self, client, monkeypatch, tmp_path):
        from app.api import documents as docs

        dest = tmp_path / "0123456789abcdef.pdf"
        dest.write_bytes(b"%PDF-1.4")

        login(client)
        monkeypatch.setattr(
            docs, "save_upload_stream", lambda *a, **k: ("0123456789abcdef", dest, 123)
        )
        monkeypatch.setattr(docs._pipeline, "ingest", lambda *a, **k: None)

        resp = client.post(
            "/api/documents",
            files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")},
        )
        assert resp.status_code == 200, resp.text

        entries = AuditService().query(action_type=DOCUMENT_UPLOAD)
        assert len(entries) == 1
        assert entries[0]["target_id"] == resp.json()["id"]
        assert entries[0]["new_value"] == {"filename": "a.pdf", "size": 123}


class TestResumeAuditHook:
    def test_resume_records_document_resume_and_transitions_status(self, client, monkeypatch):
        from app.api import documents as docs

        login(client)
        DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 123)
        DocumentRegistry().update("0123456789abcdef", status="paused")

        def fake_resume(doc_id):
            DocumentRegistry().update(doc_id, status="processing")

        monkeypatch.setattr(docs._pipeline, "resume", fake_resume)

        resp = client.post("/api/documents/0123456789abcdef/resume")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "processing"

        entries = AuditService().query(action_type=DOCUMENT_RESUME)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "0123456789abcdef"


SECURITY_USERS = [
    {"user_id": "sim-security", "username": "demo.security", "email": "s@d.local", "groups": ["KB_Security"]},
]


class TestAuditMetaEndpoints:
    def test_action_types_complete(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch, auth_sim_users=SECURITY_USERS)
        login(client, "demo.security")
        resp = client.get("/api/audit/action-types")
        assert resp.status_code == 200, resp.text
        types = resp.json()["action_types"]
        assert types == sorted(types)
        assert "chat_history_view" in types
        assert "chat_history_auto_delete" in types
        assert "document_upload" in types

    def test_users_directory(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch, auth_sim_users=SECURITY_USERS)
        svc = AuditService()
        svc.append(action_type=DOCUMENT_UPLOAD, user_id="u1", username="alice")
        svc.append(action_type=DOCUMENT_DELETE, user_id="u1", username="alice")
        svc.append(action_type="chat_history_view", user_id="u2", username="bob")

        login(client, "demo.security")
        resp = client.get("/api/audit/users")
        assert resp.status_code == 200, resp.text
        users = {u["user_id"]: u for u in resp.json()["users"]}
        assert set(users) == {"u1", "u2"}
        assert users["u1"]["username"] == "alice"
        assert users["u1"]["count"] == 2

    def test_meta_requires_security_role(self, tmp_path, monkeypatch):
        # sim-admin (роль admin) не имеет доступа к метаданным журнала.
        client = make_client(tmp_path, monkeypatch)
        login(client, "demo.admin")
        assert client.get("/api/audit/action-types").status_code == 403
        assert client.get("/api/audit/users").status_code == 403


# ---------- Дополнение 2026-09-01: закрывалки audit-пробелов ----------


class TestAuditGapFixes:
    """detect-development без audit, ip_address в users/jobs, фантомный unblock."""

    def test_detect_development_records_audit(self, client, monkeypatch):
        from app.api import documents as docs
        from app.services.audit import DOCUMENT_DEVELOPMENT_SET
        from app.services.dev_detector import Detection
        from app.services.development_registry import get_development_registry

        dev = get_development_registry().create("12010", "СЭДО")
        login(client)
        DocumentRegistry().create("0123456789abcdef", "a.pdf", "application/pdf", 123)
        monkeypatch.setattr(docs, "_document_head", lambda *a, **k: "текст")
        monkeypatch.setattr(
            docs,
            "detect",
            lambda *a, **k: Detection(
                number="12010", development_id=dev["id"], confidence=0.8, matched=True
            ),
        )

        resp = client.post("/api/documents/0123456789abcdef/detect-development")
        assert resp.status_code == 200, resp.text

        entries = AuditService().query(action_type=DOCUMENT_DEVELOPMENT_SET)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "0123456789abcdef"
        assert entries[0]["old_value"]["development_id"] is None
        assert entries[0]["new_value"]["development_id"] == dev["id"]
        assert entries[0]["meta"] == {"source": "auto"}
        assert entries[0]["ip_address"] is not None

    def test_unblock_without_active_blocks_no_phantom_audit(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch, auth_sim_users=SECURITY_USERS)
        login(client, "demo.security")

        resp = client.post("/api/users/nobody/unblock")
        assert resp.status_code == 404
        assert AuditService().query(action_type="user_unblock") == []

    def test_block_unblock_records_ip(self, tmp_path, monkeypatch):
        client = make_client(tmp_path, monkeypatch, auth_sim_users=SECURITY_USERS)
        login(client, "demo.security")

        assert client.post("/api/users/sim-editor/block", json={"reason": "x"}).status_code == 200
        assert client.post("/api/users/sim-editor/unblock").status_code == 200

        for action in ("user_block", "user_unblock"):
            entries = AuditService().query(action_type=action)
            assert len(entries) == 1
            assert entries[0]["ip_address"] is not None
