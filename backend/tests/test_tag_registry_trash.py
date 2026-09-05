"""TagRegistry: учёт только активных документов (корзинные доки не считаются).

Регрессия бага 06.09.2026: тег «ааа», оставшийся только на доке в корзине, нельзя
было удалить — счётчики/guard'ы TagRegistry считали document_tags без учёта
deleted_at (soft delete, Этап 4a.2). Сценарии: trash-only / active / mixed /
restore / purge.
"""
import pytest

from app.services.registry import get_registry
from app.services.tag_registry import TagInUseError, TagRegistry


def _count(name: str) -> int | None:
    return {t["name"]: t["count"] for t in TagRegistry().all()}.get(name)


class TestTrashOnly:
    def test_count_is_zero(self):
        TagRegistry().add(["ааа"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("aaaaaaaaaaaaaaaa", "demo.editor")

        assert _count("ааа") == 0

    def test_delete_allowed(self):
        TagRegistry().add(["ааа"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("aaaaaaaaaaaaaaaa", "demo.editor")

        assert TagRegistry().delete("ааа") is True
        assert "ааа" not in {t["name"] for t in TagRegistry().all()}

    def test_delete_leaves_document_tags_untouched(self):
        """Удаление из пула не трогает связи: после restore тег снова честно used."""
        TagRegistry().add(["ааа"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("aaaaaaaaaaaaaaaa", "demo.editor")

        assert TagRegistry().delete("ааа") is True
        reg.restore("aaaaaaaaaaaaaaaa")
        assert _count("ааа") == 1

    def test_cleanup_removes(self):
        TagRegistry().add(["ааа", "garbage"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("aaaaaaaaaaaaaaaa", "demo.editor")

        deleted = TagRegistry().delete_unused()
        assert "ааа" in deleted
        assert "garbage" in deleted


class TestActive:
    def test_count_is_one(self):
        TagRegistry().add(["ааа"])
        get_registry().create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])

        assert _count("ааа") == 1

    def test_delete_blocked(self):
        TagRegistry().add(["ааа"])
        get_registry().create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])

        with pytest.raises(TagInUseError):
            TagRegistry().delete("ааа")

    def test_cleanup_keeps(self):
        TagRegistry().add(["ааа", "garbage"])
        get_registry().create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])

        deleted = TagRegistry().delete_unused()
        assert "ааа" not in deleted
        assert "garbage" in deleted


class TestMixed:
    def test_count_is_active_only(self):
        TagRegistry().add(["ааа"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("bbbbbbbbbbbbbbbb", "demo.editor")

        assert _count("ааа") == 1

    def test_delete_blocked_while_any_active(self):
        TagRegistry().add(["ааа"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("bbbbbbbbbbbbbbbb", "demo.editor")

        with pytest.raises(TagInUseError):
            TagRegistry().delete("ааа")

    def test_soft_delete_of_last_active_zeroes_count(self):
        TagRegistry().add(["ааа"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("aaaaaaaaaaaaaaaa", "demo.editor")

        assert _count("ааа") == 1  # bbb ещё активен
        reg.soft_delete("bbbbbbbbbbbbbbbb", "demo.editor")
        assert _count("ааа") == 0  # оба в корзине — больше не используется


class TestRestore:
    def test_count_returns_after_restore(self):
        TagRegistry().add(["ааа"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("aaaaaaaaaaaaaaaa", "demo.editor")
        assert _count("ааа") == 0

        assert reg.restore("aaaaaaaaaaaaaaaa") is True
        assert _count("ааа") == 1


class TestPurge:
    def test_purge_does_not_change_active_usage(self):
        TagRegistry().add(["общий"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["общий"])
        reg.create("bbbbbbbbbbbbbbbb", "b.docx", "x", 10, tags=["общий"])
        reg.soft_delete("aaaaaaaaaaaaaaaa", "demo.editor")

        assert reg.delete_if_deleted("aaaaaaaaaaaaaaaa") is True
        assert _count("общий") == 1  # активный bbb остался единственным держателем

    def test_purge_of_only_holder_frees_tag(self):
        TagRegistry().add(["ааа"])
        reg = get_registry()
        reg.create("aaaaaaaaaaaaaaaa", "a.docx", "x", 10, tags=["ааа"])
        reg.soft_delete("aaaaaaaaaaaaaaaa", "demo.editor")

        assert reg.delete_if_deleted("aaaaaaaaaaaaaaaa") is True
        assert _count("ааа") == 0
        assert TagRegistry().delete("ааа") is True
