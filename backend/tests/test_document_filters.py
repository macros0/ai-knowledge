"""Тесты фильтров списка документов (status / has_duplicates / development / module / problem).

Все фильтры — дешёвые WHERE-pushdown по хранимым колонкам (см. DocumentRegistry.list):
никакого LSH-поиска и вычисляемой логики по времени на запрос списка нет.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.attribute_registry import get_attribute_registry
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


def seed_module(value: str) -> None:
    get_attribute_registry().add("module", value)


def seed_dev(number: str, name: str, module: str | None = None) -> int:
    return get_development_registry().create(number, name, module)["id"]


def seed_docs() -> None:
    reg = DocumentRegistry()
    reg.create("a" * 16, "done.docx", "doc", 10, uploaded_by="demo.editor")
    reg.update("a" * 16, status="done", has_duplicates=False)
    reg.create("b" * 16, "paused.docx", "doc", 20, uploaded_by="demo.editor")
    reg.update("b" * 16, status="paused", has_duplicates=False)
    reg.create("c" * 16, "failed.docx", "doc", 30, uploaded_by="demo.editor")
    reg.update("c" * 16, status="failed", has_duplicates=False)
    reg.create("d" * 16, "dup.docx", "doc", 40, uploaded_by="demo.editor")
    reg.update("d" * 16, status="done", has_duplicates=True)


def filenames(resp) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {d["filename"] for d in resp.json()["documents"]}


class TestStatusFilter:
    def test_single_status(self, client):
        seed_docs()
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?status=paused")) == {"paused.docx"}

    def test_comma_list(self, client):
        seed_docs()
        login(client, "demo.editor")
        got = filenames(client.get("/api/documents?status=paused,failed"))
        assert got == {"paused.docx", "failed.docx"}


class TestHasDuplicatesFilter:
    def test_true(self, client):
        seed_docs()
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?has_duplicates=true")) == {"dup.docx"}


class TestDevelopmentFilter:
    def test_by_id(self, client):
        dev_id = seed_dev("Пр_10", "Проактив", None)
        reg = DocumentRegistry()
        reg.create("a" * 16, "in_dev.docx", "doc", 10, uploaded_by="demo.editor")
        reg.update("a" * 16, development_id=dev_id)
        reg.create("b" * 16, "no_dev.docx", "doc", 20, uploaded_by="demo.editor")
        login(client, "demo.editor")
        assert filenames(client.get(f"/api/documents?development_id={dev_id}")) == {"in_dev.docx"}

    def test_by_number(self, client):
        dev_id = seed_dev("Пр_10", "Проактив", None)
        reg = DocumentRegistry()
        reg.create("a" * 16, "in_dev.docx", "doc", 10, uploaded_by="demo.editor")
        reg.update("a" * 16, development_id=dev_id)
        reg.create("b" * 16, "no_dev.docx", "doc", 20, uploaded_by="demo.editor")
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?development_number=Пр_10")) == {"in_dev.docx"}


class TestModuleFilter:
    def test_module(self, client):
        seed_module("PY")
        seed_module("OM")
        dev_py = seed_dev("Пр_10", "Проактив", "PY")
        dev_om = seed_dev("Пр_11", "Другое", "OM")
        reg = DocumentRegistry()
        reg.create("a" * 16, "py.docx", "doc", 10, uploaded_by="demo.editor")
        reg.update("a" * 16, development_id=dev_py)
        reg.create("b" * 16, "om.docx", "doc", 20, uploaded_by="demo.editor")
        reg.update("b" * 16, development_id=dev_om)
        reg.create("c" * 16, "no_dev.docx", "doc", 30, uploaded_by="demo.editor")
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?module=PY")) == {"py.docx"}


class TestProblemFilter:
    def test_problem_unites_stopped_duplicates_and_drafts(self, client):
        seed_docs()
        login(client, "demo.editor")
        got = filenames(client.get("/api/documents?problem=true"))
        assert got == {"paused.docx", "failed.docx", "dup.docx", "done.docx"}

    def test_problem_with_uploader(self, client):
        seed_docs()
        login(client, "demo.editor")
        got = filenames(client.get("/api/documents?problem=true&uploader=demo.editor"))
        assert got == {"paused.docx", "failed.docx", "dup.docx", "done.docx"}

    def test_problem_includes_draft_without_development(self, client):
        # Готовый документ без привязанной разработки — «черновик, требует разметки».
        DocumentRegistry().create("e" * 16, "draft.docx", "doc", 10, uploaded_by="demo.editor")
        DocumentRegistry().update("e" * 16, status="done", has_duplicates=False)
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?problem=true")) == {"draft.docx"}

    def test_problem_includes_suggestion_without_development(self, client):
        # «Требует уточнения»: готовый документ с suggestion (кандидат) без привязки.
        reg = DocumentRegistry()
        reg.create("e" * 16, "suggest.docx", "doc", 10, uploaded_by="demo.editor")
        reg.update(
            "e" * 16,
            status="done",
            has_duplicates=False,
            development_suggestion={"number": "12010", "name": "Кандидат"},
        )
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?problem=true")) == {"suggest.docx"}

    def test_problem_excludes_done_with_development(self, client):
        # Готовый + размеченный (есть development) + без дубликатов → не «проблемный».
        dev_id = seed_dev("Пр_10", "Проактив", None)
        reg = DocumentRegistry()
        reg.create("e" * 16, "ok.docx", "doc", 10, uploaded_by="demo.editor")
        reg.update("e" * 16, status="done", has_duplicates=False, development_id=dev_id)
        login(client, "demo.editor")
        assert filenames(client.get("/api/documents?problem=true")) == set()


def test_disabled_mode_filters(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, auth_provider="disabled")
    seed_docs()
    got = filenames(client.get("/api/documents?problem=true"))
    assert got == {"paused.docx", "failed.docx", "dup.docx", "done.docx"}
