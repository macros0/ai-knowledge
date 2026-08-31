"""Тесты текстового поиска, серверной сортировки и пагинации списка документов.

Поиск (query-параметр search):
  - подстрока по filename / uploaded_by / тегам / разработке (OR);
  - при наличии * или ? — glob (полное совпадение) только для filename;
  - регистронезависимо, включая кириллицу (на SQLite — через переопределённый
    LOWER(), см. app.db.session._on_sqlite_connect).

Сортировка — серверная (sort), пагинация — limit/offset + total (limit=None → все).
"""
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


def seed_dev(number: str, name: str, module: str | None = None) -> int:
    return get_development_registry().create(number, name, module)["id"]


def seed_doc(
    filename: str,
    *,
    doc_id: str,
    tags: list[str] | None = None,
    uploaded_by: str | None = "demo.editor",
    development_id: int | None = None,
) -> None:
    reg = DocumentRegistry()
    reg.create(doc_id, filename, "doc", 10, tags=tags, uploaded_by=uploaded_by)
    if development_id is not None:
        reg.update(doc_id, development_id=development_id)


def filenames(resp) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {d["filename"] for d in resp.json()["documents"]}


def ordered(resp) -> list[str]:
    assert resp.status_code == 200, resp.text
    return [d["filename"] for d in resp.json()["documents"]]


class TestSearchSubstring:
    def test_filename_case_insensitive_cyrillic(self, client):
        seed_doc("Спецификация_СЭДО.docx", doc_id="a" * 16)
        seed_doc("Протокол.docx", doc_id="b" * 16)
        login(client, "demo.editor")
        # Ищем в нижнем регистре — документ сохранён в смешанном.
        assert filenames(client.get("/api/documents?search=сэдо")) == {"Спецификация_СЭДО.docx"}

    def test_tag(self, client):
        seed_doc("doc1.docx", doc_id="a" * 16, tags=["Проактив", "ФС"])
        seed_doc("doc2.docx", doc_id="b" * 16, tags=["Другое"])
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?search=проактив")) == {"doc1.docx"}

    def test_development_number_and_name(self, client):
        dev_id = seed_dev("Пр_10", "Проактив", None)
        seed_doc("in_dev.docx", doc_id="a" * 16, development_id=dev_id)
        seed_doc("other.docx", doc_id="b" * 16)
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?search=пр_10")) == {"in_dev.docx"}
        assert filenames(client.get("/api/documents?search=проактив")) == {"in_dev.docx"}

    def test_uploader(self, client):
        seed_doc("u1.docx", doc_id="a" * 16, uploaded_by="alice.editor")
        seed_doc("u2.docx", doc_id="b" * 16, uploaded_by="bob.editor")
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?search=alice")) == {"u1.docx"}

    def test_or_across_fields(self, client):
        # Имя файла не содержит запроса — документ находится по тегу.
        seed_doc("irrelevant_name.docx", doc_id="a" * 16, tags=["СЭДО"])
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?search=сэдо")) == {"irrelevant_name.docx"}


class TestSearchGlob:
    def test_glob_filename_only_full_match(self, client):
        seed_doc("Спец_001.docx", doc_id="a" * 16)
        seed_doc("другое_Спец.docx", doc_id="b" * 16)
        login(client, "demo.editor")
        # Полное совпадение с начала: только первый документ.
        assert filenames(client.get("/api/documents?search=Спец*")) == {"Спец_001.docx"}

    def test_glob_does_not_apply_to_tag(self, client):
        # glob (*) работает только для filename; тег ищется по литеральной подстроке.
        seed_doc("x.docx", doc_id="a" * 16, tags=["Спец_тег"])
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?search=Спец*")) == set()

    def test_question_mark_glob(self, client):
        seed_doc("Спец_001.docx", doc_id="a" * 16)
        seed_doc("Спец_002.docx", doc_id="b" * 16)
        seed_doc("Спец_00_extra.docx", doc_id="c" * 16)
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?search=Спец_00?.docx")) == {
            "Спец_001.docx",
            "Спец_002.docx",
        }


class TestSearchEscaping:
    def test_percent_literal_not_wildcard(self, client):
        seed_doc("100%_план.docx", doc_id="a" * 16)
        seed_doc("1005_план.docx", doc_id="b" * 16)
        login(client, "demo.editor")
        # % в запросе — литерал, а не LIKE-джокер.
        assert filenames(client.get("/api/documents?search=100%")) == {"100%_план.docx"}


class TestPagination:
    def _seed_five(self):
        for i, name in enumerate(["a", "b", "c", "d", "e"]):
            seed_doc(f"{name}.docx", doc_id=str(i) * 16)

    def test_limit_offset_total(self, client):
        self._seed_five()
        login(client, "demo.editor")
        resp = client.get("/api/documents?sort=name_asc&limit=2&offset=0")
        body = resp.json()
        assert body["total"] == 5
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert ordered(resp) == ["a.docx", "b.docx"]

        resp = client.get("/api/documents?sort=name_asc&limit=2&offset=4")
        assert resp.json()["total"] == 5
        assert ordered(resp) == ["e.docx"]

    def test_limit_absent_returns_all(self, client):
        self._seed_five()
        login(client, "demo.editor")
        resp = client.get("/api/documents")
        body = resp.json()
        assert body["total"] == 5
        assert body["limit"] is None
        assert len(body["documents"]) == 5

    def test_total_respects_search(self, client):
        seed_doc("Спец_1.docx", doc_id="a" * 16)
        seed_doc("Спец_2.docx", doc_id="b" * 16)
        seed_doc("other.docx", doc_id="c" * 16)
        login(client, "demo.editor")
        resp = client.get("/api/documents?search=Спец*&limit=1")
        body = resp.json()
        assert body["total"] == 2
        assert len(body["documents"]) == 1


class TestSort:
    def test_name_asc_desc(self, client):
        for i, name in enumerate(["b", "c", "a"]):
            seed_doc(f"{name}.docx", doc_id=str(i) * 16)
        login(client, "demo.editor")
        assert ordered(client.get("/api/documents?sort=name_asc")) == ["a.docx", "b.docx", "c.docx"]
        assert ordered(client.get("/api/documents?sort=name_desc")) == ["c.docx", "b.docx", "a.docx"]

    def test_null_uploader_last(self, client):
        seed_doc("a.docx", doc_id="a" * 16, uploaded_by="zzz")
        seed_doc("b.docx", doc_id="b" * 16, uploaded_by=None)
        seed_doc("c.docx", doc_id="c" * 16, uploaded_by="aaa")
        login(client, "demo.editor")
        # null/пустое — всегда в конец, независимо от направления.
        assert ordered(client.get("/api/documents?sort=uploader_asc")) == ["c.docx", "a.docx", "b.docx"]
        assert ordered(client.get("/api/documents?sort=uploader_desc")) == ["a.docx", "c.docx", "b.docx"]
