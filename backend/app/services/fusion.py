"""Reciprocal Rank Fusion (RRF) — слияние ranked-списков из разных веток поиска.

RRFscore(d) = sum over ranked-lists m:  w_m / (k + rank_m(d))

где:
  - d — документ (point_id),
  - m — каждая ветка поиска (dense, bm25, graph expansion),
  - rank_m(d) — позиция d в ranked-list ветки m (0-based),
  - w_m — вес ветки (настраивается в config.py),
  - k — константа RRF (стандарт TREC = 60).

Документ, найденный несколькими ветками, получает кумулятивный буст.
Дедупликация по point_id — структурная (dict-аккумулятор).
"""
from dataclasses import dataclass


@dataclass
class Hit:
    """Результат поиска из одной ветки."""

    point_id: str
    score: float
    payload: dict
    rank: int = 0


def reciprocal_rank_fusion(
    ranked_lists: list[tuple[list[Hit], float]],
    k: int = 60,
) -> list[Hit]:
    """Сливает несколько ranked-listов в один через RRF с весами.

    Args:
        ranked_lists: список кортежей (hits, weight), где hits — список Hit
            (уже отсортированный по убыванию релевантности), weight — вес ветки.
        k: константа RRF (default 60, стандарт TREC).

    Returns:
        Список Hit, отсортированный по убыванию fused score. payload берётся из
        первого списка, где hit встретился (у дубликатов payload одинаковый).
        Пустые входные списки игнорируются.
    """
    fused_scores: dict[str, float] = {}
    payload_map: dict[str, dict] = {}

    for hits, weight in ranked_lists:
        if not hits:
            continue
        for hit in hits:
            score = weight / (k + hit.rank)
            fused_scores[hit.point_id] = fused_scores.get(hit.point_id, 0.0) + score
            if hit.point_id not in payload_map:
                payload_map[hit.point_id] = hit.payload

    ordered = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)
    return [
        Hit(point_id=pid, score=score, payload=payload_map[pid])
        for pid, score in ordered
    ]
