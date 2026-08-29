"""Тесты атомарной записи JSON-состояния.

Ключевое свойство: обрыв посреди записи не оставляет усечённый файл —
читатель видит либо прежнюю версию, либо новую.
"""
import json
import tempfile
from pathlib import Path

import pytest

from app.config import Settings
from app.services.jsonio import write_json_atomic
from app.services.registry import DocumentRegistry
from app.services.staging import StagingStore
from app.services.tag_registry import TagRegistry


def _explode_after_partial(monkeypatch):
    """Подменяет json.dump на «записал половину и умер»."""

    def exploding_dump(obj, fp, **kwargs):
        fp.write('{"partial')
        raise RuntimeError("обрыв процесса")

    monkeypatch.setattr("app.services.jsonio.json.dump", exploding_dump)


class TestWriteJsonAtomic:
    def test_writes_readable_json(self, tmp_path: Path):
        target = tmp_path / "state.json"
        write_json_atomic(target, {"a": 1, "б": "кириллица"})
        assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "б": "кириллица"}

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "state.json"
        write_json_atomic(target, [1, 2])
        assert json.loads(target.read_text(encoding="utf-8")) == [1, 2]

    def test_does_not_escape_non_ascii(self, tmp_path: Path):
        target = tmp_path / "state.json"
        write_json_atomic(target, {"tag": "Отпуск"})
        assert "Отпуск" in target.read_text(encoding="utf-8")

    def test_previous_content_survives_failed_write(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "state.json"
        write_json_atomic(target, {"version": 1})

        _explode_after_partial(monkeypatch)
        with pytest.raises(RuntimeError):
            write_json_atomic(target, {"version": 2})

        assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}

    def test_no_temp_files_left_behind(self, tmp_path: Path, monkeypatch):
        target = tmp_path / "state.json"
        write_json_atomic(target, {"version": 1})

        _explode_after_partial(monkeypatch)
        with pytest.raises(RuntimeError):
            write_json_atomic(target, {"version": 2})

        assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]

    def test_replaces_existing_file_in_place(self, tmp_path: Path):
        target = tmp_path / "state.json"
        write_json_atomic(target, {"version": 1})
        write_json_atomic(target, {"version": 2})
        assert json.loads(target.read_text(encoding="utf-8")) == {"version": 2}
        assert sorted(p.name for p in tmp_path.iterdir()) == ["state.json"]

    def test_temp_file_lands_next_to_target(self, tmp_path: Path, monkeypatch):
        """os.replace атомарен только в пределах ФС — временный файл обязан
        лежать в каталоге назначения, не в /tmp."""
        target = tmp_path / "deep" / "state.json"
        seen: list[Path] = []
        real = tempfile.mkstemp

        def spy(*args, **kwargs):
            fd, name = real(*args, **kwargs)
            seen.append(Path(name))
            return fd, name

        monkeypatch.setattr("app.services.jsonio.tempfile.mkstemp", spy)
        write_json_atomic(target, {"a": 1})
        write_json_atomic(target, {"a": 2})

        assert [p.parent for p in seen] == [target.parent, target.parent]
        assert len({p.name for p in seen}) == 2, "имя временного файла обязано быть уникальным"


class TestStateFilesAreAtomic:
    """Три хранилища состояния пишут через общий хелпер, а не write_text напрямую."""

    def test_staging_manifest_survives_crash(self, tmp_path: Path, monkeypatch):
        staging = StagingStore("a1b2c3d4e5f60718", staging_root=tmp_path / "st")
        staging.create(total_chunks=3)
        assert staging.load()["total_chunks"] == 3

        _explode_after_partial(monkeypatch)
        with pytest.raises(RuntimeError):
            staging.create(total_chunks=99)

        monkeypatch.undo()
        # точка восстановления цела, а не потеряна из-за усечённого JSON
        assert staging.load()["total_chunks"] == 3

    def test_registry_survives_crash(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.services.registry.get_settings", lambda: Settings(data_dir=tmp_path))
        reg = DocumentRegistry()
        reg.create("a1b2c3d4e5f60718", "in.docx", "application/octet-stream", 10)

        _explode_after_partial(monkeypatch)
        with pytest.raises(RuntimeError):
            reg.update("a1b2c3d4e5f60718", status="processing")

        monkeypatch.undo()
        monkeypatch.setattr("app.services.registry.get_settings", lambda: Settings(data_dir=tmp_path))
        assert DocumentRegistry().get("a1b2c3d4e5f60718")["filename"] == "in.docx"

    def test_tag_registry_survives_crash(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr("app.services.tag_registry.get_settings", lambda: Settings(data_dir=tmp_path))
        TagRegistry().add(["proxmox"])

        _explode_after_partial(monkeypatch)
        with pytest.raises(RuntimeError):
            TagRegistry().add(["network"])

        monkeypatch.undo()
        monkeypatch.setattr("app.services.tag_registry.get_settings", lambda: Settings(data_dir=tmp_path))
        assert [t["name"] for t in TagRegistry().all()] == ["proxmox"]
