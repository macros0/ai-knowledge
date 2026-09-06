"""API-тесты «Поддержки языков» (Этап 7, фаза A): locales CRUD, активация,
импорт стоп-слов (preview→confirm), история/rollback, права и аудит."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.audit import LOCALE_CREATE, STOPWORDS_IMPORT
from app.services.stopwords import ensure_seeded, invalidate

ROLE_GROUPS = {
    "KB_Viewer": "viewer",
    "KB_Editor": "editor",
    "KB_Admin": "admin",
    "KB_Security": "security",
}

ADMIN_USER = {
    "user_id": "sim-admin",
    "username": "demo.admin",
    "email": "a@d.local",
    "groups": ["KB_Admin"],
}

VIEWER_USER = {
    "user_id": "sim-viewer",
    "username": "demo.viewer",
    "email": "v@d.local",
    "groups": ["KB_Viewer"],
}


def make_client(tmp_path, monkeypatch, **overrides) -> TestClient:
    defaults = {
        "_env_file": None,
        "data_dir": tmp_path,
        "auth_provider": "simulation",
        "auth_role_groups": ROLE_GROUPS,
        "auth_default_role": "viewer",
        "auth_sim_users": [ADMIN_USER, VIEWER_USER],
    }
    defaults.update(overrides)
    settings = Settings(**defaults)
    for module in ("app.config", "app.main", "app.auth.api"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _seed():
    ensure_seeded()
    yield
    invalidate()


@pytest.fixture
def client(tmp_path, monkeypatch):
    return make_client(tmp_path, monkeypatch)


def login(client, username="demo.admin"):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


class TestActiveLocales:
    def test_returns_seeded_active(self, client):
        login(client)
        resp = client.get("/api/locales")
        assert resp.status_code == 200, resp.text
        codes = {l["code"] for l in resp.json()["locales"]}
        assert {"ru", "en"} <= codes

    def test_etag_304(self, client):
        login(client)
        r1 = client.get("/api/locales")
        etag = r1.headers["etag"]
        r2 = client.get("/api/locales", headers={"if-none-match": etag})
        assert r2.status_code == 304


class TestLocalesCrud:
    def test_admin_list_has_counts(self, client):
        login(client)
        resp = client.get("/api/admin/locales")
        assert resp.status_code == 200, resp.text
        by_code = {l["code"]: l for l in resp.json()["locales"]}
        assert by_code["ru"]["status"] == "active"
        assert by_code["ru"]["stopwords_bm25_count"] > 0
        assert by_code["en"]["stopwords_bm25_count"] > 0

    def test_create_locale_draft(self, client):
        login(client)
        resp = client.post("/api/admin/locales", json={"code": "de", "name": "Deutsch"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "draft"

    def test_create_duplicate_422(self, client):
        login(client)
        assert client.post("/api/admin/locales", json={"code": "ru", "name": "X"}).status_code == 422

    def test_activate_unshipped_422(self, client):
        login(client)
        client.post("/api/admin/locales", json={"code": "de", "name": "Deutsch"})
        resp = client.post("/api/admin/locales/de/activate")
        assert resp.status_code == 422, resp.text  # нет в статическом манифесте

    def test_disable_ru_forbidden(self, client):
        login(client)
        assert client.post("/api/admin/locales/ru/disable").status_code == 422

    def test_disable_en(self, client):
        login(client)
        resp = client.post("/api/admin/locales/en/disable")
        assert resp.status_code == 200
        assert resp.json()["status"] == "disabled"

    def test_create_records_audit(self, client):
        login(client)
        client.post("/api/admin/locales", json={"code": "nl", "name": "Nederlands"})
        from app.services.audit import AuditService

        entries = AuditService().query(action_type=LOCALE_CREATE)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "nl"

    def test_admin_requires_role(self, client):
        login(client, "demo.viewer")
        assert client.get("/api/admin/locales").status_code == 403
        assert client.post("/api/admin/locales", json={"code": "de", "name": "D"}).status_code == 403


class TestUiDictionaryAdmin:
    def test_get_active_empty_returns_null_data(self, client):
        login(client)
        resp = client.get("/api/admin/locales/en/ui-dictionary")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["locale"] == "en"
        assert body["version"] is None and body["data"] is None

    def test_get_active_after_import(self, client):
        login(client)
        resp = client.post(
            "/api/admin/locales/en/ui-dictionary/import",
            json={"data": {"nav.documents": "Papers"}, "confirm": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["applied"] is True

        body = client.get("/api/admin/locales/en/ui-dictionary").json()
        assert body["version"] == 1
        assert body["data"] == {"nav.documents": "Papers"}

    def test_get_active_unknown_locale_404(self, client):
        login(client)
        assert client.get("/api/admin/locales/zz/ui-dictionary").status_code == 404


class TestTranslationPending:
    def test_pending_endpoint(self, client):
        login(client)
        resp = client.get("/api/admin/locales/en/stopwords?kind=bm25")
        assert resp.status_code == 200
        # count_pending живёт под /tags/translations/pending (admin).
        pend = client.get(
            "/api/tags/translations/pending", params={"locale": "en", "entities": "tags"}
        )
        assert pend.status_code == 200, pend.text
        assert "tags" in pend.json()["pending"]

    def test_pending_requires_admin(self, client):
        login(client, "demo.viewer")
        assert client.get(
            "/api/tags/translations/pending", params={"locale": "en"}
        ).status_code == 403


class TestStopwordsImport:
    def test_preview_then_confirm(self, client):
        login(client)
        preview = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["abctest", "defgh"], "confirm": False},
        )
        assert preview.status_code == 200, preview.text
        body = preview.json()
        assert body["applied"] is False
        assert set(body["added"]) == {"abctest", "defgh"}

        applied = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["abctest", "defgh"], "confirm": True},
        )
        assert applied.status_code == 200
        assert applied.json()["applied"] is True

        words = client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]
        assert {"abctest", "defgh"} <= {w["word"] for w in words}

    def test_replace_removes_absent(self, client):
        login(client)
        client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["keepme", "dropme"], "confirm": True},
        )
        resp = client.post(
            "/api/admin/locales/ru/stopwords/import?mode=replace&kind=bm25",
            json={"words": ["keepme"], "confirm": True},
        )
        # replace — полная замена набора: удаляется всё, чего нет во входящем
        # (включая сид-слова), поэтому проверяем членство, а не точный список.
        assert "dropme" in resp.json()["removed"]
        words = {w["word"] for w in client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]}
        assert "keepme" in words
        assert "dropme" not in words

    def test_import_records_audit(self, client):
        login(client)
        client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["auditword"], "confirm": True},
        )
        from app.services.audit import AuditService

        entries = AuditService().query(action_type=STOPWORDS_IMPORT)
        assert len(entries) == 1
        assert entries[0]["target_id"] == "ru"
        assert entries[0]["new_value"]["kind"] == "bm25"

    def test_history_and_rollback(self, client):
        login(client)
        client.post(
            "/api/admin/locales/ru/stopwords/import?mode=merge&kind=bm25",
            json={"words": ["v1"], "confirm": True},
        )
        client.post(
            "/api/admin/locales/ru/stopwords/import?mode=replace&kind=bm25",
            json={"words": ["v2"], "confirm": True},
        )
        history = client.get("/api/admin/locales/ru/stopwords/history").json()["entries"]
        assert len(history) == 2
        # history[0] — самый свежий (replace на {v2}), его old_value = сид ∪ {v1}.
        latest_entry_id = history[0]["id"]
        assert "v2" in history[0]["words"]

        resp = client.post(
            "/api/admin/locales/ru/stopwords/rollback", json={"entry_id": latest_entry_id}
        )
        assert resp.status_code == 200, resp.text
        words = {w["word"] for w in client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]}
        assert "v1" in words
        assert "v2" not in words

    def test_add_and_delete_word(self, client):
        login(client)
        assert client.post(
            "/api/admin/locales/ru/stopwords", json={"word": "singleword", "kind": "bm25"}
        ).status_code == 200
        words = {w["word"] for w in client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]}
        assert "singleword" in words
        assert client.delete("/api/admin/locales/ru/stopwords/singleword?kind=bm25").status_code == 200
        words = {w["word"] for w in client.get("/api/admin/locales/ru/stopwords?kind=bm25").json()["words"]}
        assert "singleword" not in words
