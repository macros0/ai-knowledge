"""Юнит-тесты StagingStore: инкрементальная запись чанков, manifest в БД, resume.

Manifest хранится в document_staging (БД), сырые файлы чанков — в FS под
staging_root. Проверки читают manifest через store.load() (а не manifest.json).
"""
from app.models.schemas import Concept
from app.services.staging import StagingStore


def _concept(title: str, cid: str) -> Concept:
    return Concept(id=cid, title=title, type="concept", tags=[], content=f"тело {title}", relations=[])


class TestStagingAppend:
    def test_append_roundtrip(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2, global_tags=["proxmox"])
        slugs = store.append_chunk(0, [_concept("Auth Flow", "a"), _concept("Auth Flow", "b")])
        assert slugs == ["auth-flow", "auth-flow-1"]
        assert store.has_chunk(0)
        assert not store.has_chunk(1)
        assert store.processed_chunks == [0]

        concepts = store.concepts()
        assert [c.title for c in concepts] == ["Auth Flow", "Auth Flow"]

        manifest = store.load()
        assert manifest["total_chunks"] == 2
        assert manifest["global_tags"] == ["proxmox"]
        assert manifest["processed_chunks"] == [0]
        assert manifest["chunks_data"]["0"]["concepts_count"] == 2

    def test_append_multiple_chunks_ordered(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(3)
        store.append_chunk(1, [_concept("Middle", "m")])
        store.append_chunk(0, [_concept("First", "f")])
        store.append_chunk(2, [_concept("Last", "l")])
        assert store.processed_chunks == [0, 1, 2]
        assert [c.title for c in store.concepts()] == ["First", "Middle", "Last"]
        assert store.slugs() == ["first", "middle", "last"]

    def test_slug_dedup_across_chunks(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2)
        store.append_chunk(0, [_concept("Bridge", "b")])
        store.append_chunk(1, [_concept("Bridge", "b2"), _concept("Bridge", "b3")])
        assert store.slugs() == ["bridge", "bridge-1", "bridge-2"]


class TestStagingResume:
    def test_restart_skips_processed_chunks(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2)
        store.append_chunk(0, [_concept("Done", "d")])

        resumed = StagingStore("doc1", staging_root=tmp_path / "staging")
        assert resumed.exists()
        assert resumed.has_chunk(0)
        assert not resumed.has_chunk(1)
        resumed.append_chunk(1, [_concept("Next", "n")])
        assert [c.title for c in resumed.concepts()] == ["Done", "Next"]
        assert resumed.slugs() == ["done", "next"]

    def test_remove(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(1)
        store.append_chunk(0, [_concept("X", "x")])
        store.remove()
        assert not store.dir.exists()
        assert not store.exists()


class TestStagingChunkText:
    def test_save_and_read_chunk_text(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(2)
        store.save_chunk_text(0, "первый чанк")
        store.save_chunk_text(1, "второй чанк")
        assert (store.dir / "chunk_00.md").read_text(encoding="utf-8") == "первый чанк"
        assert (store.dir / "chunk_01.md").read_text(encoding="utf-8") == "второй чанк"
        assert store.has_chunk(0) is False, "текст чанка не должен влиять на processed_chunks"

    def test_save_chunk_text_idempotent(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        store.create(1)
        store.save_chunk_text(0, "версия 1")
        store.save_chunk_text(0, "версия 2")
        assert (store.dir / "chunk_00.md").read_text(encoding="utf-8") == "версия 2"


class TestStagingEmpty:
    def test_no_manifest(self, tmp_path):
        store = StagingStore("doc1", staging_root=tmp_path / "staging")
        assert not store.exists()
        assert store.concepts() == []
        assert store.processed_chunks == []
        assert not store.has_chunk(0)
