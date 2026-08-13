"""Юнит-тесты для глобального справочника тегов и слияния тегов в концепты."""
import threading

from scripts.rebuild_tags import collect_tags, read_global_tags
from app.services.pipeline import _merge_tags
from app.services.tag_registry import TagRegistry, normalize_tags


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
    def test_add_and_all(self, tmp_path):
        reg = TagRegistry()
        reg.path = tmp_path / "tags.json"
        reg._tags = {}
        reg.add(["proxmox", "network"])
        reg.add(["proxmox", "vlan"])
        tags = reg.all()
        by_name = {t["name"]: t["count"] for t in tags}
        assert by_name == {"proxmox": 2, "network": 1, "vlan": 1}
        assert reg.path.exists()

    def test_persists_to_disk(self, tmp_path):
        reg = TagRegistry()
        reg.path = tmp_path / "tags.json"
        reg._tags = {}
        reg.add(["devops"])
        reg2 = TagRegistry()
        reg2.path = tmp_path / "tags.json"
        reg2._load()
        assert "devops" in reg2._tags

    def test_concurrent_add_is_thread_safe(self, tmp_path):
        reg = TagRegistry()
        reg.path = tmp_path / "tags.json"
        reg._tags = {}

        def worker():
            for _ in range(50):
                reg.add(["proxmox"])

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert reg._tags == {"proxmox": 200}


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
