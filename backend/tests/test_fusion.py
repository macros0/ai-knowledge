"""Юнит-тесты RRF (Reciprocal Rank Fusion) с весами."""
from app.services.fusion import Hit, reciprocal_rank_fusion


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
