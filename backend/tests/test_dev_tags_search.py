"""Тесты поискового pre-filter по dev_tags (денормализованный номер/название/модуль)."""
from app.services.vector_store import _tag_match_filter


class TestTagMatchFilter:
    def test_or_between_tags_and_dev_tags(self):
        f = _tag_match_filter(["12010"])
        assert f.must is not None
        assert len(f.must) == 1
        inner = f.must[0]
        assert inner.should is not None
        assert len(inner.should) == 2
        keys = {c.key for c in inner.should}
        assert keys == {"tags", "dev_tags"}

    def test_multiple_tags_matchany(self):
        f = _tag_match_filter(["12010", "СЭДО"])
        inner = f.must[0]
        for cond in inner.should:
            assert set(cond.match.any) == {"12010", "СЭДО"}

    def test_build_search_filter_none(self):
        from app.services.vector_store import VectorStore

        assert VectorStore._build_search_filter(None) is None
        assert VectorStore._build_search_filter([]) is None

    def test_build_search_filter_uses_or(self):
        from app.services.vector_store import VectorStore

        f = VectorStore._build_search_filter(["PY"])
        assert f is not None
        assert {c.key for c in f.must[0].should} == {"tags", "dev_tags"}
