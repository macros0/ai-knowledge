"""Юнит-тесты RRF (Reciprocal Rank Fusion) с весами."""

from app.config import Settings
from app.services.fusion import Hit, reciprocal_rank_fusion
from app.services.vector_store import VectorStore, _demote_review_concepts


class TestRRFBasic:
    def test_single_list_unchanged_order(self):
        hits = [Hit("A", 0.9, {}, 0), Hit("B", 0.8, {}, 1), Hit("C", 0.7, {}, 2)]
        result = reciprocal_rank_fusion([(hits, 1.0)], k=60)
        assert [h.point_id for h in result] == ["A", "B", "C"]
        assert result[0].score > result[1].score > result[2].score

    def test_empty_lists(self):
        result = reciprocal_rank_fusion([], k=60)
        assert result == []

    def test_empty_hit_list_ignored(self):
        result = reciprocal_rank_fusion([([], 1.0), ([Hit("A", 1.0, {}, 0)], 1.0)], k=60)
        assert len(result) == 1
        assert result[0].point_id == "A"


class TestRRFDeduplication:
    def test_duplicate_across_lists_gets_cumulative_score(self):
        list_a = [Hit("A", 0.9, {"x": 1}, 0), Hit("B", 0.8, {}, 1)]
        list_b = [Hit("B", 5.0, {}, 0), Hit("C", 4.0, {}, 1)]
        result = reciprocal_rank_fusion([(list_a, 1.0), (list_b, 1.0)], k=60)
        ids = [h.point_id for h in result]
        assert "A" in ids and "B" in ids and "C" in ids
        assert len(ids) == 3
        assert len(set(ids)) == 3
        # B найден в обоих списках → должен быть выше A и C
        assert ids[0] == "B"

    def test_payload_from_first_occurrence(self):
        list_a = [Hit("A", 1.0, {"src": "dense"}, 0)]
        list_b = [Hit("A", 1.0, {"src": "bm25"}, 0)]
        result = reciprocal_rank_fusion([(list_a, 1.0), (list_b, 1.0)], k=60)
        assert result[0].payload == {"src": "dense"}


class TestRRFWeights:
    def test_weight_influences_ranking(self):
        list_a = [Hit("A", 1.0, {}, 0), Hit("B", 0.9, {}, 1)]
        list_b = [Hit("B", 1.0, {}, 0), Hit("A", 0.9, {}, 1)]
        # Равные веса — A и B на одном ранге в разных списках → scores равны
        result_equal = reciprocal_rank_fusion([(list_a, 1.0), (list_b, 1.0)], k=60)
        # B в list_b rank 0, в list_a rank 1; A наоборот → scores равны
        a_score = next(h.score for h in result_equal if h.point_id == "A")
        b_score = next(h.score for h in result_equal if h.point_id == "B")
        assert abs(a_score - b_score) < 1e-9

        # Разные веса → list_a весит больше → A (rank 0 в list_a) побеждает
        result_weighted = reciprocal_rank_fusion([(list_a, 2.0), (list_b, 1.0)], k=60)
        assert result_weighted[0].point_id == "A"

    def test_graph_expansion_lower_weight(self):
        main = [Hit("A", 0.9, {}, 0)]
        graph = [Hit("B", 0.5, {}, 100)]
        result = reciprocal_rank_fusion([(main, 1.0), (graph, 0.5)], k=60)
        assert result[0].point_id == "A"
        assert result[1].point_id == "B"

    def test_weight_zero_gives_zero_score(self):
        list_a = [Hit("A", 1.0, {}, 0)]
        list_b = [Hit("B", 1.0, {}, 0)]
        result = reciprocal_rank_fusion([(list_a, 1.0), (list_b, 0.0)], k=60)
        assert len(result) == 2
        b_hit = next(h for h in result if h.point_id == "B")
        assert b_hit.score == 0.0
        assert result[0].point_id == "A"


class TestRRFKConstant:
    def test_k_affects_score_distribution(self):
        hits = [Hit("A", 1.0, {}, 0), Hit("B", 0.9, {}, 1)]
        small_k = reciprocal_rank_fusion([(hits, 1.0)], k=1)
        large_k = reciprocal_rank_fusion([(hits, 1.0)], k=1000)
        # Малое k → больший разброс score
        spread_small = small_k[0].score - small_k[1].score
        spread_large = large_k[0].score - large_k[1].score
        assert spread_small > spread_large


class TestReviewDemotion:
    """Демоция концептов-замечаний (тег review) до fusion: эффективный ранг
    += penalty в каждой ветке — узкое замечание (дословный контекст якоря)
    не должно вытеснять основной контент из топа RRF."""

    @staticmethod
    def _hit(pid: str, rank: int, point_type: str = "concept", tags: list | None = None) -> Hit:
        return Hit(pid, 1.0 - rank * 0.01, {
            "point_type": point_type, "doc_id": "d1", "chunk_index": 1,
            "title": pid, "tags": tags or [], "content": "x",
        }, rank)

    def test_demote_adds_penalty_to_review_concepts_only(self):
        review = self._hit("rev", 0, tags=["review", "comment"])
        main = self._hit("main", 1, tags=["business"])
        chunk = self._hit("ch", 2, point_type="chunk", tags=["review"])  # тег на чанке
        _demote_review_concepts([review, main, chunk], penalty=10)
        assert review.rank == 10
        assert main.rank == 1
        assert chunk.rank == 2  # не концепт — не трогаем

    def test_demote_noop_when_disabled(self):
        review = self._hit("rev", 0, tags=["review"])
        _demote_review_concepts([review], penalty=0)
        _demote_review_concepts([review], penalty=-1)
        assert review.rank == 0

    def _vs(self, tmp_path, monkeypatch, *, penalty: int, per_branch: int) -> tuple[VectorStore, list]:
        """VectorStore с заглушенными ветками (dense/bm25) и выключенным graph
        expansion; возвращает (vs, dense_calls) для проверок per_branch."""
        settings = Settings(
            _env_file=None, data_dir=tmp_path, embedding_dimensions=8,
            search_graph_expansion_enabled=False,
            search_review_concept_rank_penalty=penalty,
            search_per_branch_top_k=per_branch,
        )
        print("DBG settings: dense_enabled=", settings.search_dense_enabled) if False else None
        monkeypatch.setattr("app.services.vector_store.get_settings", lambda: settings)
        vs = VectorStore()
        vs.settings = settings
        calls: list[int] = []

        def fake_dense(vector, query_filter, top_k):
            calls.append(top_k)
            # Реальная группа (диагноз 02.09.2026, dense-ветка): замечание #1,
            # основной концепт #10, между ними шум других документов.
            hits = [self._hit("rev", 0, tags=["review", "comment"])]
            hits += [self._hit(f"noise{i}", i + 1, tags=["x"]) for i in range(8)]
            hits += [self._hit("main", 9, tags=["business"])]
            return hits

        def fake_bm25(sparse_vec, query_filter, top_k):
            return [self._hit("ch", 0, point_type="chunk")]

        monkeypatch.setattr(vs, "search_dense", fake_dense)
        monkeypatch.setattr(vs, "search_bm25", fake_bm25)
        return vs, calls

    def test_penalty_swaps_review_and_main_in_fusion(self, tmp_path, monkeypatch):
        """ИНТЕРАКЦИЯ penalty × per_branch_top_k на реальной группе: замечание
        на dense #1, основной концепт на dense #10. С penalty=10 и запасом
        кандидатов (per_branch=40, оба в пуле) основной концепт обгоняет
        замечание в fusion — а не выталкивается им за границу выборки."""
        vs, calls = self._vs(tmp_path, monkeypatch, penalty=10, per_branch=40)
        fused = vs.search_composite(
            dense_vec=[0.1] * 8, sparse_vec=None, tags=None,
            branches={"dense"}, top_k=40,
        )
        assert calls == [40]  # запас кандидатов реально проброшен в ветку
        order = [h.point_id for h in fused]
        assert order.index("main") < order.index("rev")
        # Замечание не выброшено — просто ниже основного
        assert "rev" in order

    def test_penalty_zero_keeps_review_on_top(self, tmp_path, monkeypatch):
        """penalty=0 (выкл): прежнее поведение — замечание (dense #1) выше
        основного концепта (dense #10)."""
        vs, _ = self._vs(tmp_path, monkeypatch, penalty=0, per_branch=40)
        fused = vs.search_composite(
            dense_vec=[0.1] * 8, sparse_vec=None, tags=None,
            branches={"dense"}, top_k=40,
        )
        order = [h.point_id for h in fused]
        assert order.index("rev") < order.index("main")

    def test_review_only_hits_still_found(self, tmp_path, monkeypatch):
        """Запрос именно про замечания: review-хит — единственный релевантный,
        демоция не мешает его выдаче (опускает, но не удаляет)."""
        settings = Settings(
            _env_file=None, data_dir=tmp_path, embedding_dimensions=8,
            search_graph_expansion_enabled=False,
            search_review_concept_rank_penalty=10,
        )
        monkeypatch.setattr("app.services.vector_store.get_settings", lambda: settings)
        vs = VectorStore()
        vs.settings = settings
        monkeypatch.setattr(vs, "search_dense", lambda v, f, k: [self._hit("rev", 0, tags=["review"])])
        monkeypatch.setattr(vs, "search_bm25", lambda s, f, k: [])
        fused = vs.search_composite(
            dense_vec=[0.1] * 8, sparse_vec=None, tags=None,
            branches={"dense"}, top_k=10,
        )
        assert [h.point_id for h in fused] == ["rev"]
