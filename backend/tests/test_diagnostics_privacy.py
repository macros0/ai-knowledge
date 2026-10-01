from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest

from app.config import Settings
from app.services import llm_client


def test_production_never_writes_llm_raw_dump(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "spool")
    # Even a caller bypassing settings validation cannot enable production dumps.
    settings = settings.model_copy(update={"environment": "production", "llm_raw_debug_enabled": True})
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    llm_client._dump_debug_response("CANARY_DOCUMENT_AND_PASSWORD", "0123456789abcdef", 1)
    assert not list(settings.data_dir.rglob("*.txt"))


def test_development_raw_dump_disabled_by_default_and_legacy_preserved(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "spool")
    legacy = settings.data_dir / "debug" / "existing.txt"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("EXISTING_OWNER_DATA")
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    llm_client._dump_debug_response("CANARY_NEW_RAW", "0123456789abcdef", 1)
    assert [path for path in settings.data_dir.rglob("*.txt")] == [legacy]
    assert legacy.read_text() == "EXISTING_OWNER_DATA"


def test_llm_parse_exception_has_no_response_prefix(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "spool")
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    monkeypatch.setattr(llm_client, "repair_json", lambda *args, **kwargs: None)
    with pytest.raises(ValueError) as error:
        llm_client._parse_json("CANARY_PRIVATE_LLM_REPLY", doc_id="0123456789abcdef", chunk_idx=2)
    assert "CANARY" not in str(error.value)
    assert "Дамп сохранён" not in str(error.value)
    assert not list(settings.data_dir.rglob("*.txt"))


def test_development_raw_dump_enforces_quota_and_ttl(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "spool",
                        llm_raw_debug_enabled=True)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    root = settings.data_dir / "dev-llm-debug"
    root.mkdir(parents=True)
    old = root / f"llm_raw_{uuid4().hex}.txt"
    old.write_bytes(b"old")
    stamp = (datetime.now(timezone.utc) - timedelta(hours=25)).timestamp()
    os.utime(old, (stamp, stamp))
    llm_client._dump_debug_response("synthetic", "../CANARY_PATH", 1)
    assert not old.exists()
    files = list(root.glob("*.txt"))
    assert len(files) == 1
    assert files[0].read_text() == "synthetic"
    assert "CANARY_PATH" not in files[0].name
    llm_client._dump_debug_response("x" * (21 * 1048576), "0123456789abcdef", 1)
    assert sum(path.stat().st_size for path in root.iterdir() if path.is_file()) <= 20 * 1048576
    assert len(list(root.glob("*.txt"))) == 1


def test_development_raw_dump_does_not_follow_symlink(monkeypatch, tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", diagnostics_dir=tmp_path / "spool",
                        llm_raw_debug_enabled=True)
    monkeypatch.setattr(llm_client, "get_settings", lambda: settings)
    settings.data_dir.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    link = settings.data_dir / "dev-llm-debug"
    try:
        link.symlink_to(external, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(external)],
                       check=True, capture_output=True)
    llm_client._dump_debug_response("CANARY", "0123456789abcdef", 1)
    assert not list(external.iterdir())
