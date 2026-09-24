"""Тесты PromptStore: каскад override → canonical → дефолт, горячая перезагрузка, защита от битых файлов."""
import os
import time

import pytest

from app.prompts.store import PROMPT_DEFAULTS, PromptStore


@pytest.fixture
def dirs(tmp_path):
    override = tmp_path / "override"
    canonical = tmp_path / "canonical"
    override.mkdir()
    canonical.mkdir()
    return override, canonical


def _store(dirs):
    override, canonical = dirs
    return PromptStore(override_dir=override, canonical_dir=canonical)


def _bump(path):
    """Гарантированно меняет mtime (обход низкой гранулярности ФС)."""
    future = time.time() + 2
    os.utime(path, (future, future))


class TestCascade:
    def test_default_when_no_files(self, dirs):
        assert _store(dirs).get("chat_system") == PROMPT_DEFAULTS["chat_system"]

    def test_canonical_file_used(self, dirs):
        _, canonical = dirs
        (canonical / "chat_system.md").write_text("канонический", encoding="utf-8")
        assert _store(dirs).get("chat_system") == "канонический"

    def test_override_wins_over_canonical(self, dirs):
        override, canonical = dirs
        (override / "chat_system.md").write_text("оверрайд", encoding="utf-8")
        (canonical / "chat_system.md").write_text("канонический", encoding="utf-8")
        assert _store(dirs).get("chat_system") == "оверрайд"

    def test_unknown_key_raises(self, dirs):
        with pytest.raises(KeyError):
            _store(dirs).get("nope")


class TestHotReload:
    def test_reloads_on_mtime_change(self, dirs):
        override, _ = dirs
        path = override / "chat_system.md"
        path.write_text("v1", encoding="utf-8")
        store = _store(dirs)
        assert store.get("chat_system") == "v1"
        path.write_text("v2", encoding="utf-8")
        _bump(path)
        assert store.get("chat_system") == "v2"

    def test_cached_when_mtime_unchanged(self, dirs):
        override, _ = dirs
        path = override / "chat_system.md"
        path.write_text("v1", encoding="utf-8")
        store = _store(dirs)
        assert store.get("chat_system") == "v1"
        path.write_text("v2", encoding="utf-8")
        _bump(path)
        store.get("chat_system")  # перезагрузка
        assert store.get("chat_system") == "v2"

    def test_override_deleted_falls_back(self, dirs):
        override, canonical = dirs
        (override / "chat_system.md").write_text("оверрайд", encoding="utf-8")
        (canonical / "chat_system.md").write_text("канонический", encoding="utf-8")
        store = _store(dirs)
        assert store.get("chat_system") == "оверрайд"
        (override / "chat_system.md").unlink()
        assert store.get("chat_system") == "канонический"


class TestBrokenFiles:
    def test_empty_override_keeps_last_valid(self, dirs):
        override, _ = dirs
        path = override / "chat_system.md"
        path.write_text("валидно", encoding="utf-8")
        store = _store(dirs)
        assert store.get("chat_system") == "валидно"
        _bump(path)
        path.write_text("", encoding="utf-8")
        assert store.get("chat_system") == "валидно"

    def test_empty_override_without_cache_falls_back(self, dirs):
        override, canonical = dirs
        (override / "chat_system.md").write_text("", encoding="utf-8")
        (canonical / "chat_system.md").write_text("канонический", encoding="utf-8")
        assert _store(dirs).get("chat_system") == "канонический"

    def test_invalid_utf8_falls_back_to_default(self, dirs):
        override, canonical = dirs
        (override / "chat_system.md").write_bytes(b"\xff\xfe broken")
        (canonical / "chat_system.md").write_text("канонический", encoding="utf-8")
        assert _store(dirs).get("chat_system") == "канонический"

    def test_both_broken_falls_back_to_default(self, dirs):
        override, canonical = dirs
        (override / "chat_system.md").write_bytes(b"\xff\xfe broken")
        (canonical / "chat_system.md").write_bytes(b"\xff\xfe broken")
        assert _store(dirs).get("chat_system") == PROMPT_DEFAULTS["chat_system"]


class TestFormatting:
    def test_format_chat_user_default(self, dirs):
        out = _store(dirs).format("chat_user", context="КТ", query="ВПР")
        assert "КТ" in out and "ВПР" in out

    def test_missing_required_placeholder_falls_back_to_default(self, dirs):
        override, _ = dirs
        (override / "chat_user.md").write_text("нет плейсхолдера {query}", encoding="utf-8")
        out = _store(dirs).format("chat_user", context="КТ", query="ВПР")
        assert "ВПР" in out  # дефолт, а не файл без {query}
        assert "нет плейсхолдера" not in out

    def test_unknown_placeholder_survives_without_crash(self, dirs):
        override, _ = dirs
        (override / "chat_user.md").write_text(
            "Контекст: {context} Опечатка: {контекст} Вопрос: {query}", encoding="utf-8"
        )
        out = _store(dirs).format("chat_user", context="КТ", query="ВПР")
        assert "КТ" in out and "ВПР" in out
        assert "{контекст}" in out  # неизвестный токен сохраняется дословно

    def test_format_okf_chunk_requires_all(self, dirs):
        override, _ = dirs
        (override / "okf_chunk.md").write_text(
            "Док: {filename} #{index}/{total}\n{content}", encoding="utf-8"
        )
        out = _store(dirs).format(
            "okf_chunk", filename="f.docx", index=1, total=3, content="тело"
        )
        assert "f.docx" in out and "#1/3" in out and "тело" in out


class TestEnsure:
    def test_seeds_canonical_files(self, dirs, tmp_path):
        override, canonical = dirs
        store = _store(dirs)
        store.ensure()
        assert override.is_dir()
        for key, default in PROMPT_DEFAULTS.items():
            path = canonical / f"{key}.md"
            assert path.exists(), f"{key}.md не создан"
            assert path.read_text(encoding="utf-8").strip() == default.strip()

    def test_ensure_preserves_existing_canonical(self, dirs):
        _, canonical = dirs
        (canonical / "chat_system.md").write_text("кастомный", encoding="utf-8")
        store = _store(dirs)
        store.ensure()
        assert (canonical / "chat_system.md").read_text(encoding="utf-8").strip() == "кастомный"


class TestDefaultsSync:
    """Код-дефолты prompts/okf.py обязаны совпадать с каноническими файлами
    backend/prompts/*.md (иначе fallback тихо вернёт устаревший промпт —
    инцидент «красный тест okf_chunk», 06.09.2026)."""

    def test_defaults_match_canonical_files(self):
        from pathlib import Path

        canonical_dir = Path(__file__).resolve().parents[1] / "prompts"
        for key, default in PROMPT_DEFAULTS.items():
            path = canonical_dir / f"{key}.md"
            assert path.exists(), f"{key}.md отсутствует"
            file_text = path.read_text(encoding="utf-8").strip()
            assert file_text == default.strip(), (
                f"prompt '{key}': дефолт в prompts/okf.py разошёлся с {key}.md — "
                "перенесите правку файла в код-дефолт"
            )

    def test_okf_prompt_requires_exact_source_quotes_when_supported(self):
        prompt = PROMPT_DEFAULTS["okf_system"]

        assert "MUST include at least one item in `source_quotes`" in prompt
        assert "Optionally add `source_quotes`" not in prompt
