"""Тесты GET /developments/{id}/documents (поиск/сортировка/пагинация, граница development_id)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.development_registry import get_development_registry
from app.services.registry import DocumentRegistry

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
        "auth_sim_users": [
            {"user_id": "sim-user", "username": "demo.user", "email": "u@d.local", "groups": ["KB_Viewer"]},
            {"user_id": "sim-editor", "username": "demo.editor", "email": "e@d.local", "groups": ["KB_Editor"]},
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


def login(client, username):
    resp = client.post("/api/auth/simulate", json={"username": username})
    assert resp.status_code == 200, resp.text


def seed_dev(number: str, name: str) -> int:
    return get_development_registry().create(number, name, None)["id"]


def seed_doc(filename, *, doc_id, tags=None, uploaded_by="demo.editor", development_id=None):
    reg = DocumentRegistry()
    reg.create(doc_id, filename, "doc", 10, tags=tags, uploaded_by=uploaded_by)
    if development_id is not None:
        reg.update(doc_id, development_id=development_id)


def ordered(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [d["filename"] for d in resp.json()["documents"]]


class TestDevelopmentDocuments:
    def test_search_within_development(self, client):
        dev = seed_dev("Пр_10", "Проактив")
        seed_doc("Спец_001.docx", doc_id="a" * 16, development_id=dev)
        seed_doc("other.docx", doc_id="b" * 16, development_id=dev)
        login(client, "demo.editor")
        assert ordered(client.get(f"/api/developments/{dev}/documents?search=спец")) == ["Спец_001.docx"]

    def test_search_does_not_leak_across_development_boundary(self, client):
        # Два документа из РАЗНЫХ разработок совпадают по uploaded_by и общему тегу.
        # Поисковый термин совпадает с документом из другой разработки — граница
        # development_id должна отфильтровать его через AND (а не OR).
        dev_a = seed_dev("Пр_10", "Проактив")
        dev_b = seed_dev("Пр_11", "Другое")
        seed_doc("a_spec.docx", doc_id="a" * 16, tags=["общий"], uploaded_by="alice.editor", development_id=dev_a)
        seed_doc("b_spec.docx", doc_id="b" * 16, tags=["общий"], uploaded_by="alice.editor", development_id=dev_b)
        login(client, "demo.editor")

        # По общему тегу — виден только документ разработки A.
        assert ordered(client.get(f"/api/developments/{dev_a}/documents?search=общий")) == ["a_spec.docx"]
        assert ordered(client.get(f"/api/developments/{dev_b}/documents?search=общий")) == ["b_spec.docx"]

        # По общему загрузчику — аналогично.
        assert ordered(client.get(f"/api/developments/{dev_a}/documents?search=alice")) == ["a_spec.docx"]
        assert ordered(client.get(f"/api/developments/{dev_b}/documents?search=alice")) == ["b_spec.docx"]

    def test_pagination(self, client):
        dev = seed_dev("Пр_10", "Проактив")
        for i, name in enumerate(["a", "b", "c", "d", "e"]):
            seed_doc(f"{name}.docx", doc_id=str(i) * 16, development_id=dev)
        login(client, "demo.editor")

        resp = client.get(f"/api/developments/{dev}/documents?sort=name_asc&limit=2&offset=0")
        body = resp.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert ordered(resp) == ["a.docx", "b.docx"]

        resp = client.get(f"/api/developments/{dev}/documents?sort=name_asc&limit=2&offset=4")
        assert resp.json()["total"] == 5
        assert ordered(resp) == ["e.docx"]

    def test_limit_absent_returns_all(self, client):
        dev = seed_dev("Пр_10", "Проактив")
        for i, name in enumerate(["a", "b", "c"]):
            seed_doc(f"{name}.docx", doc_id=str(i) * 16, development_id=dev)
        login(client, "demo.editor")
        body = client.get(f"/api/developments/{dev}/documents").json()
        assert body["total"] == 3
        assert body["limit"] is None
        assert len(body["documents"]) == 3

    def test_sort_name_desc(self, client):
        dev = seed_dev("Пр_10", "Проактив")
        for i, name in enumerate(["b", "c", "a"]):
            seed_doc(f"{name}.docx", doc_id=str(i) * 16, development_id=dev)
        login(client, "demo.editor")
        assert ordered(client.get(f"/api/developments/{dev}/documents?sort=name_asc")) == ["a.docx", "b.docx", "c.docx"]
        assert ordered(client.get(f"/api/developments/{dev}/documents?sort=name_desc")) == ["c.docx", "b.docx", "a.docx"]

    def test_missing_development_404(self, client):
        login(client, "demo.editor")
        assert client.get("/api/developments/99999/documents").status_code == 404
