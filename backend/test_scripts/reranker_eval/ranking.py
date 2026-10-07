"""Stable permutation of a prefix without editing evidence."""
import math


def reorder_prefix(blocks: list[dict], scores: list[float], *, top_n: int) -> list[dict]:
    if type(top_n) is not int or top_n < 1:
        raise ValueError('invalid prefix size')
    count = min(top_n, len(blocks))
    if len(scores) != count or any(
        type(score) not in (float, int) or not math.isfinite(score) for score in scores
    ):
        raise ValueError('incomplete or invalid scores')
    positions = sorted(range(count), key=lambda i: (-scores[i], i))
    return [blocks[i] for i in positions] + blocks[count:]
