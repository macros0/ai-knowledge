"""Тесты DB-side фильтра видимости хитов поиска (services/search_filter.py)."""
from app.services import search_filter as sf
from app.services.registry import DocumentRegistry


class _Hit:
    def __init__(self, doc_id):
        self.payload = {"doc_id": doc_id}


class TestDropInvisibleHits:
    def test_build_doc_lookup_batches_document_reads(self, monkeypatch):
        calls = []

        class _Registry:
            def get_visibility_many(self, doc_ids):
                calls.append(set(doc_ids))
                return {"a": {"id": "a"}, "b": None}

            def get_many(self, _doc_ids):
                raise AssertionError("full document lookup should not be used")

        monkeypatch.setattr(sf, "get_registry", lambda: _Registry())

        assert sf.build_doc_lookup([_Hit("a"), _Hit("b")]) == {"a": {"id": "a"}, "b": None}
        assert calls == [{"a", "b"}]

    def test_drops_deleted_and_missing_docs(self):
        reg = DocumentRegistry()
        reg.create("1111111111111111", "a.pdf", "application/pdf", 1)
        reg.create("2222222222222222", "b.pdf", "application/pdf", 1)
        reg.soft_delete("2222222222222222", "u1")
        # 3333... отсутствует в БД вовсе (purge в процессе / сбой финализации).

        hits = [
            _Hit("1111111111111111"),
            _Hit("2222222222222222"),
            _Hit("3333333333333333"),
        ]
        lookup = sf.build_doc_lookup(hits)
        visible = sf.drop_invisible_hits(hits, lookup)

        assert [h.payload["doc_id"] for h in visible] == ["1111111111111111"]
        # lookup различает удалённый и отсутствующий документ.
        assert lookup["2222222222222222"]["deleted_at"] is not None
        assert lookup["3333333333333333"] is None

    def test_empty_hits(self):
        assert sf.build_doc_lookup([]) == {}
        assert sf.drop_invisible_hits([], {}) == []
