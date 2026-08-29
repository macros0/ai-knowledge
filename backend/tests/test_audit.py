"""Тесты журнала ИБ (audit_log): append-only, enum action_type, фильтры."""
import pytest

from app.services.audit import (
    ACTION_TYPES,
    DOCUMENT_BULK_DELETE,
    DOCUMENT_DELETE,
    AuditService,
    get_audit,
    record,
)

EXPECTED_ACTION_TYPES = {
    "document_delete",
    "document_bulk_delete",
    "document_regenerate",
    "document_bulk_regenerate",
    "job_approve",
    "job_cancel",
    "user_block",
    "user_unblock",
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
