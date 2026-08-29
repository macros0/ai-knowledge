"""Тесты загрузки реестра из documents.json неверной формы.

try/except в _load накрывал только json.loads, но не форму результата.
Валидный JSON со значениями-не-словарями проходил разбор и ронял конструктор,
а _registry = get_registry() стоит на уровне модуля в api/documents.py —
то есть backend не стартовал вообще и отдавал трейсбек вместо объяснения.
"""
import json
from pathlib import Path

import pytest

from app.config import Settings
from app.services.registry import DocumentRegistry


def _registry(tmp_path: Path, monkeypatch, payload) -> DocumentRegistry:
    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr("app.services.registry.get_settings", lambda: settings)
    if payload is not None:
        (tmp_path / "documents.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
    return DocumentRegistry()


class TestMalformedRegistryDoesNotCrash:
    @pytest.mark.parametrize(
        "payload",
        [
            {"abc": "done"},           # значения-строки — правдоподобная ручная правка
            {"abc": 1},                # значение-число
            {"abc": None},             # значение-null
            {"abc": ["done"]},         # значение-список
            [{"id": "abc"}],           # список на верхнем уровне
            "просто строка",           # скаляр на верхнем уровне
            42,
        ],
    )
    def test_starts_with_empty_registry(self, tmp_path: Path, monkeypatch, payload):
        reg = _registry(tmp_path, monkeypatch, payload)
        assert reg.list() == []

    def test_keeps_valid_entries_and_drops_broken(self, tmp_path: Path, monkeypatch):
        """Частичное восстановление не должно стирать уцелевшие записи."""
        reg = _registry(tmp_path, monkeypatch, {
            "a1b2c3d4e5f60718": {"id": "a1b2c3d4e5f60718", "filename": "ok.docx", "status": "paused"},
            "битая": "done",
        })
        docs = reg.list()
        assert [d["filename"] for d in docs] == ["ok.docx"]
        assert reg.get("битая") is None

    def test_entry_without_id_does_not_crash(self, tmp_path: Path, monkeypatch):
        """_reconcile_okf_counts брал doc["id"] напрямую — KeyError при status=done."""
        reg = _registry(tmp_path, monkeypatch, {
            "a1b2c3d4e5f60718": {"status": "done", "filename": "ok.docx"},
        })
        assert len(reg.list()) == 1

    def test_broken_json_still_handled(self, tmp_path: Path, monkeypatch):
        settings = Settings(data_dir=tmp_path)
        monkeypatch.setattr("app.services.registry.get_settings", lambda: settings)
        (tmp_path / "documents.json").write_text('{"abc": {"id"', encoding="utf-8")
        assert DocumentRegistry().list() == []

    def test_missing_file_is_fine(self, tmp_path: Path, monkeypatch):
        assert _registry(tmp_path, monkeypatch, None).list() == []

    def test_warns_about_dropped_entries(self, tmp_path: Path, monkeypatch, caplog):
        with caplog.at_level("WARNING", logger="app.services.registry"):
            _registry(tmp_path, monkeypatch, {"битая": "done", "тоже": 1})
        assert "неверной формы" in caplog.text
        assert "битая" in caplog.text and "тоже" in caplog.text

    def test_warns_about_wrong_toplevel_type(self, tmp_path: Path, monkeypatch, caplog):
        with caplog.at_level("WARNING", logger="app.services.registry"):
            _registry(tmp_path, monkeypatch, ["a"])
        assert "получен list" in caplog.text


def test_api_starts_with_malformed_registry(tmp_path: Path, monkeypatch):
    """Тот самый сценарий: сервис обязан подняться и отвечать."""
    from fastapi.testclient import TestClient

    settings = Settings(data_dir=tmp_path)
    (tmp_path / "documents.json").write_text('{"abc": "done"}', encoding="utf-8")
    for mod in ("app.config", "app.services.registry", "app.api.documents", "app.main"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda: settings, raising=False)

    class FakeVectorStore:
        def __getattr__(self, name):
            return lambda *a, **k: 0

    monkeypatch.setattr("app.main.VectorStore", FakeVectorStore)

    import importlib

    import app.api.documents as documents
    import app.main as main

    importlib.reload(documents)  # _registry = get_registry() на уровне модуля
    with TestClient(main.create_app()) as client:
        resp = client.get("/api/documents")
    assert resp.status_code == 200
    assert resp.json()["documents"] == []
