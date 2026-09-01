"""Тесты истории чата (Этап 6): запись/чтение, владение, audit, soft delete, TTL/purge."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db.models import ChatSession
from app.db.session import session_scope
from app.main import create_app
from app.services import audit, chat_history as ch
from app.services.audit import AuditService

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}

SIM_USERS = [
    {"user_id": "sim-user", "username": "demo.user", "email": "u@d.local", "groups": ["KB_Viewer"]},
    {"user_id": "sim-admin", "username": "demo.admin", "email": "a@d.local", "groups": ["KB_Admin"]},
    {"user_id": "sim-security", "username": "demo.security", "email": "s@d.local", "groups": ["KB_Security"]},
]


class _U:
    def __init__(self, user_id, username):
        self.user_id = user_id
        self.username = username


def make_client(tmp_path, monkeypatch, **overrides) -> TestClient:
    defaults: dict = {
        "_env_file": None,
        "data_dir": tmp_path,
        "auth_provider": "simulation",
        "auth_role_groups": ROLE_GROUPS,
        "auth_default_role": "viewer",
        "dedup_enabled": False,
        "auth_sim_users": SIM_USERS,
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


def create_session(user_id, username="demo.user", query="вопрос", answer="ответ"):
    """Прямая запись треда через сервис (без LLM/Qdrant). Возвращает session_id."""
    return ch.store_turn(None, _U(user_id, username), query, answer, sources=[])


class TestStoreTurn:
    def test_store_creates_session_and_messages(self, client):
        sid = create_session("sim-user")
        sessions, total = ch.list_sessions("sim-user")
        assert total == 1
        assert sessions[0]["session_id"] == sid
        assert sessions[0]["message_count"] == 2
        assert sessions[0]["title"] == "вопрос"

        thread = ch.get_thread(sid, "sim-user")
        assert [m["role"] for m in thread["messages"]] == ["user", "assistant"]
        assert thread["messages"][0]["content"] == "вопрос"
        assert thread["messages"][1]["content"] == "ответ"

    def test_store_reuses_own_session(self, client):
        sid = create_session("sim-user", query="первый")
        ch.store_turn(sid, _U("sim-user", "demo.user"), "второй", "ответ2")
        thread = ch.get_thread(sid, "sim-user")
        assert len(thread["messages"]) == 4
        assert thread["title"] == "первый"

    def test_store_ownership_conflict(self, client):
        sid = create_session("sim-user")
        with pytest.raises(ch.ChatOwnershipError):
            ch.store_turn(sid, _U("sim-admin", "demo.admin"), "чужой", "нет")

    def test_store_on_deleted_session_raises(self, client):
        sid = create_session("sim-user")
        ch.soft_delete_session(sid, _U("sim-user", "demo.user"))
        with pytest.raises(ch.ChatSessionDeletedError):
            ch.store_turn(sid, _U("sim-user", "demo.user"), "ещё", "нет")

    def test_check_session_state(self, client):
        sid = create_session("sim-user")
        # активная собственная сессия — не ошибка
        ch.check_session_state(sid, "sim-user")
        # чужая — ChatOwnershipError
        with pytest.raises(ch.ChatOwnershipError):
            ch.check_session_state(sid, "sim-admin")
        # удалённая — ChatSessionDeletedError
        ch.soft_delete_session(sid, _U("sim-user", "demo.user"))
        with pytest.raises(ch.ChatSessionDeletedError):
            ch.check_session_state(sid, "sim-user")
        # несуществующая — не ошибка (store_turn создаст)
        ch.check_session_state("00000000-0000-0000-0000-000000000000", "sim-user")

    def test_session_id_validation(self):
        assert ch.is_valid_session_id("123e4567-e89b-12d3-a456-426614174000")
        assert not ch.is_valid_session_id("not-a-uuid")
        assert not ch.is_valid_session_id("")


class TestOwnHistoryAPI:
    def test_list_empty_default(self, client):
        login(client, "demo.user")
        resp = client.get("/api/chat/history")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"sessions": [], "total": 0, "limit": None, "offset": 0}

    def test_list_and_get_thread(self, client):
        login(client, "demo.user")
        sid = create_session("sim-user")
        resp = client.get("/api/chat/history")
        assert resp.json()["total"] == 1
        assert resp.json()["sessions"][0]["session_id"] == sid

        thread = client.get(f"/api/chat/history/{sid}").json()
        assert thread["session_id"] == sid
        assert len(thread["messages"]) == 2

    def test_soft_delete_hides_from_list(self, client):
        login(client, "demo.user")
        sid = create_session("sim-user")
        resp = client.delete(f"/api/chat/history/{sid}")
        assert resp.status_code == 200, resp.text

        assert client.get("/api/chat/history").json()["total"] == 0
        thread = client.get(f"/api/chat/history/{sid}").json()
        assert thread["deleted_at"] is not None


class TestOwnership:
    def test_foreign_thread_403(self, client):
        login(client, "demo.user")
        sid = create_session("sim-user")
        # другой пользователь (admin) пытается открыть чужой тред через /history
        login(client, "demo.admin")
        resp = client.get(f"/api/chat/history/{sid}")
        assert resp.status_code == 403


class TestAdminHistory:
    def test_list_users_requires_role(self, client):
        login(client, "demo.user")  # viewer — не имеет права
        assert client.get("/api/chat/admin/history/users").status_code == 403

        login(client, "demo.security")
        assert client.get("/api/chat/admin/history/users").status_code == 200

    def test_admin_list_sessions_no_audit(self, client):
        sid = create_session("sim-user")
        login(client, "demo.security")
        resp = client.get("/api/chat/admin/history/sim-user")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1
        assert resp.json()["sessions"][0]["session_id"] == sid
        # только список — audit-записи быть не должно
        assert AuditService().query(action_type=audit.CHAT_HISTORY_VIEW) == []

    def test_admin_get_thread_audits(self, client):
        sid = create_session("sim-user")
        login(client, "demo.security")
        resp = client.get(f"/api/chat/admin/history/sim-user/{sid}")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == sid

        entries = AuditService().query(action_type=audit.CHAT_HISTORY_VIEW)
        assert len(entries) == 1
        assert entries[0]["target_id"] == sid
        assert entries[0]["meta"] == {"owner_user_id": "sim-user"}
        assert entries[0]["username"] == "demo.security"

    def test_admin_get_missing_thread_404_no_audit(self, client):
        login(client, "demo.security")
        resp = client.get("/api/chat/admin/history/sim-user/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404
        assert AuditService().query(action_type=audit.CHAT_HISTORY_VIEW) == []

    def test_admin_thread_of_other_user_404(self, client):
        # Тред принадлежит sim-user, а запрашиваем для другого user_id → 404, без audit.
        sid = create_session("sim-user")
        login(client, "demo.security")
        resp = client.get(f"/api/chat/admin/history/sim-admin/{sid}")
        assert resp.status_code == 404
        assert AuditService().query(action_type=audit.CHAT_HISTORY_VIEW) == []


class TestPurge:
    def _backdate(self, session_id: str, days: int):
        with session_scope() as s:
            sess = s.get(ChatSession, session_id)
            sess.deleted_at = datetime.now(timezone.utc) - timedelta(days=days)

    def test_purge_expired(self, client, monkeypatch):
        monkeypatch.setattr(
            ch, "get_settings",
            lambda: Settings(_env_file=None, chat_history_retention_days=90),
        )
        sid = create_session("sim-user")
        ch.soft_delete_session(sid, _U("sim-user", "demo.user"))
        self._backdate(sid, 100)

        assert ch.purge_expired_sessions() == 1
        assert ch.get_thread(sid, "sim-user") is None

        entries = AuditService().query(action_type=audit.CHAT_HISTORY_AUTO_DELETE)
        assert len(entries) == 1
        assert entries[0]["username"] == "system"
        assert entries[0]["user_id"] == "system"

    def test_purge_skips_within_retention(self, client, monkeypatch):
        monkeypatch.setattr(
            ch, "get_settings",
            lambda: Settings(_env_file=None, chat_history_retention_days=90),
        )
        sid = create_session("sim-user")
        ch.soft_delete_session(sid, _U("sim-user", "demo.user"))
        self._backdate(sid, 45)  # внутри окна

        assert ch.purge_expired_sessions() == 0
        assert ch.get_thread(sid, "sim-user") is not None

    def test_retention_override(self, client, monkeypatch):
        # .env-override: окно 1 день → удалённый 2 дня назад тред вычищается.
        monkeypatch.setattr(
            ch, "get_settings",
            lambda: Settings(_env_file=None, chat_history_retention_days=1),
        )
        sid = create_session("sim-user")
        ch.soft_delete_session(sid, _U("sim-user", "demo.user"))
        self._backdate(sid, 2)

        assert ch.purge_expired_sessions() == 1
        assert ch.get_thread(sid, "sim-user") is None

    def test_purge_audit_failure_does_not_break_batch(self, client, monkeypatch):
        monkeypatch.setattr(
            ch, "get_settings",
            lambda: Settings(_env_file=None, chat_history_retention_days=90),
        )
        sid1 = create_session("sim-user", query="первый")
        sid2 = create_session("sim-user", query="второй")
        for sid in (sid1, sid2):
            ch.soft_delete_session(sid, _U("sim-user", "demo.user"))
            self._backdate(sid, 100)

        real_record = ch.audit.record

        def flaky(user, action_type, target_type, **kw):
            if kw.get("target_id") == sid1:
                raise RuntimeError("audit down")
            real_record(user, action_type, target_type, **kw)

        monkeypatch.setattr(ch.audit, "record", flaky)

        # удаление не должно зависеть от сбоя аудита для одной сессии
        assert ch.purge_expired_sessions() == 2
        assert ch.get_thread(sid1, "sim-user") is None
        assert ch.get_thread(sid2, "sim-user") is None

        entries = AuditService().query(action_type=audit.CHAT_HISTORY_AUTO_DELETE)
        ids = {e["target_id"] for e in entries}
        # sid1 не получил запись (аудит упал), sid2 — получил (цикл продолжился)
        assert sid1 not in ids
        assert sid2 in ids


class TestChatEndpointPersists:
    def test_chat_short_circuit_records_turn(self, client, monkeypatch):
        from app.api import chat as chat_module

        monkeypatch.setattr(chat_module._embedder, "embed", lambda *a, **k: [0.0] * 10)
        monkeypatch.setattr(chat_module._vector_store, "search_composite", lambda **kw: [])

        login(client, "demo.user")
        resp = client.post("/api/chat", json={"query": "тест", "tags": []})
        assert resp.status_code == 200, resp.text
        assert resp.json()["session_id"]

        sessions = client.get("/api/chat/history").json()
        assert sessions["total"] == 1
        thread = client.get(f"/api/chat/history/{resp.json()['session_id']}").json()
        assert [m["role"] for m in thread["messages"]] == ["user", "assistant"]
        assert thread["messages"][0]["content"] == "тест"

    def test_chat_deleted_session_409(self, client):
        login(client, "demo.user")
        sid = create_session("sim-user")
        ch.soft_delete_session(sid, _U("sim-user", "demo.user"))

        resp = client.post("/api/chat", json={"query": "тест", "session_id": sid})
        assert resp.status_code == 409, resp.text

        # ранний отказ — новый тред не создаётся, в удалённую сессию ничего не пишется
        sessions = client.get("/api/chat/history").json()
        assert sessions["total"] == 0
