"""Юнит-тесты для глобального справочника тегов и слияния тегов в концепты."""
import pytest
from scripts.rebuild_tags import collect_tags, read_global_tags
from app.services.pipeline import _merge_tags
from app.services.registry import get_registry
from app.services.tag_registry import TagInUseError, TagRegistry, normalize_tags


class TestNormalizeTags:
    def test_strips_and_filters_empty(self):
        assert normalize_tags(["  proxmox  ", "", "   ", "network"]) == ["proxmox", "network"]

    def test_deduplicates(self):
        assert normalize_tags(["proxmox", "proxmox", "Network", "network"]) == ["proxmox", "Network", "network"]

    def test_none_and_empty(self):
        assert normalize_tags(None) == []
        assert normalize_tags([]) == []


class TestMergeTags:
    def test_merges_user_tags_into_concepts(self):
        assert _merge_tags(["proxmox"], ["network", "  vlan "]) == ["proxmox", "network", "vlan"]

    def test_preserves_base_order_and_dedupes(self):
        assert _merge_tags(["a", "b"], ["b", "c", "a"]) == ["a", "b", "c"]

    def test_empty_extra(self):
        assert _merge_tags(["a"], []) == ["a"]


class TestTagRegistry:
    def test_all_counts_from_documents(self):
        reg = get_registry()
        reg.create("d1", "a.docx", "x", 10, tags=["proxmox", "network"])
        reg.create("d2", "b.docx", "x", 10, tags=["proxmox", "vlan"])
        tr = TagRegistry()
        tr.add(["proxmox", "network", "vlan"])
        by_name = {t["name"]: t["count"] for t in tr.all()}
        assert by_name == {"proxmox": 2, "network": 1, "vlan": 1}

    def test_add_registers_name_without_documents(self):
        tr = TagRegistry()
        tr.add(["solo"])
        items = tr.all()
        assert [(t["name"], t["count"]) for t in items] == [("solo", 0)]

    def test_add_normalizes_and_ignores_empty(self):
        tr = TagRegistry()
        tr.add(["  proxmox  ", "", "   "])
        assert [t["name"] for t in tr.all()] == ["proxmox"]


class TestTagRegistryDelete:
    def test_delete_unused_name(self):
        tr = TagRegistry()
        tr.add(["solo"])
        assert tr.delete("solo") is True
        assert tr.all() == []

    def test_delete_missing_returns_false(self):
        assert TagRegistry().delete("nope") is False

    def test_delete_used_raises(self):
        reg = get_registry()
        reg.create("d1", "a.docx", "x", 10, tags=["proxmox"])
        TagRegistry().add(["proxmox"])
        with pytest.raises(TagInUseError):
            TagRegistry().delete("proxmox")

    def test_delete_unused_only_keeps_used(self):
        reg = get_registry()
        reg.create("d1", "a.docx", "x", 10, tags=["keep"])
        tr = TagRegistry()
        tr.add(["keep", "garbage1", "garbage2"])
        deleted = tr.delete_unused()
        assert sorted(deleted) == ["garbage1", "garbage2"]
        by_name = {t["name"]: t["count"] for t in tr.all()}
        assert by_name == {"keep": 1}

    def test_delete_unused_empty(self):
        assert TagRegistry().delete_unused() == []

    def test_delete_raises_when_tag_became_used_after_listing(self):
        """Гонка «модалка прочитала справочник → документ начал использовать тег
        → клик удалить». delete перечитывает счётчик в момент удаления."""
        tr = TagRegistry()
        tr.add(["race"])
        # Состояние, которое видела модалка до клика «удалить».
        assert [(t["name"], t["count"]) for t in tr.all() if t["name"] == "race"] == [("race", 0)]
        # Между чтением и удалением документ начинает использовать тег.
        get_registry().create("d1", "a.docx", "x", 10, tags=["race"])
        with pytest.raises(TagInUseError):
            tr.delete("race")
        assert [(t["name"], t["count"]) for t in tr.all() if t["name"] == "race"] == [("race", 1)]

    def test_delete_unused_respects_usage_changed_after_listing(self):
        """delete_unused считает used из document_tags на момент выполнения, а не
        доверяет ранее прочитанному списку."""
        tr = TagRegistry()
        tr.add(["g"])
        get_registry().create("d1", "a.docx", "x", 10, tags=["g"])
        assert tr.delete_unused() == []
        assert {t["name"] for t in tr.all()} == {"g"}

    def test_document_tag_outlives_pool_removal(self):
        """Тег используется документом, но отсутствует в пуле (legacy либо удалён
        из пула в окне гонки) — all() всё равно показывает его по document_tags,
        данные не теряются."""
        get_registry().create("d1", "a.docx", "x", 10, tags=["legacy"])
        assert {t["name"]: t["count"] for t in TagRegistry().all()} == {"legacy": 1}


class TestRebuildTags:
    def test_read_global_tags_from_frontmatter(self, tmp_path):
        md = tmp_path / "concept.md"
        md.write_text(
            "---\ntype: concept\ntitle: X\ntags: [llm]\nglobal_tags: [proxmox, network]\n---\n\n# X\n",
            encoding="utf-8",
        )
        assert read_global_tags(md) == ["proxmox", "network"]

    def test_read_global_tags_absent(self, tmp_path):
        md = tmp_path / "concept.md"
        md.write_text("---\ntype: concept\ntitle: X\n---\n\n# X\n", encoding="utf-8")
        assert read_global_tags(md) == []

    def test_collect_tags_counts_across_docs(self, tmp_path):
        d1 = tmp_path / "doc1"
        d2 = tmp_path / "doc2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "a.md").write_text("---\nglobal_tags: [proxmox, network]\n---\n", encoding="utf-8")
        (d1 / "b.md").write_text("---\nglobal_tags: [proxmox]\n---\n", encoding="utf-8")
        (d2 / "c.md").write_text("---\nglobal_tags: [vlan]\n---\n", encoding="utf-8")
        counts = collect_tags(tmp_path)
        assert counts == {"proxmox": 2, "network": 1, "vlan": 1}

    def test_save_bundle_metadata_has_global_tags(self, tmp_path):
        from app.models.schemas import Concept
        from app.services.okf_generator import OKFGenerator, _normalize

        class FakeLLM:
            def chat_json(self, system, user):
                return [
                    {
                        "id": "k1",
                        "title": "Concept One",
                        "type": "concept",
                        "tags": ["llm"],
                        "content": "Тело.",
                        "relations": [],
                    }
                ]

        gen = OKFGenerator(llm=FakeLLM(), bundle_root=tmp_path / "okf")
        concepts = _normalize(FakeLLM().chat_json(None, None))
        okf_docs = gen.save_bundle("doc1", "in.docx", concepts, global_tags=["proxmox"])
        assert okf_docs[0].metadata["global_tags"] == ["proxmox"]
        assert "global_tags:" in okf_docs[0].markdown
