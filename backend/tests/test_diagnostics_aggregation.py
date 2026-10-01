"""Bounded numerical aggregates retain totals without raw identifiers."""
from uuid import uuid4


def test_thousand_success_calls_produce_exact_fixed_histogram():
    from app.services.diagnostics.aggregation import AggregateBuffer, AggregateKey
    session_id = str(uuid4())
    buffer = AggregateBuffer(max_keys=256)
    key = AggregateKey(session_id, "backend", "/api/search", "search", "qdrant", "success")
    for _ in range(1000):
        buffer.observe(key, 10000, "success")
    result = buffer.flush(session_id, 5.0)
    assert len(result) == 1
    counts = result[0].counts
    assert counts["count"] == 1000
    assert counts["duration_sum_us"] == 10000000
    assert counts["duration_max_us"] == 10000
    assert counts["le_10ms"] == 1000
    assert sum(counts[k] for k in ("le_50ms", "le_250ms", "le_1000ms", "le_5000ms", "le_30000ms", "gt_30000ms")) == 0


def test_overflow_does_not_expand_key_set():
    from app.services.diagnostics.aggregation import AggregateBuffer, AggregateKey
    session_id = str(uuid4())
    buffer = AggregateBuffer(max_keys=2)
    for stage in ("search", "chat", "generate"):
        buffer.observe(AggregateKey(session_id, "backend", "/unknown", stage, None, "success"),
                       1000, "success")
    result = buffer.flush(session_id, 5.0)
    assert len(result) == 3  # Two named keys plus one bounded overflow bucket.
    assert sum(item.counts["count"] for item in result) == 3
    assert buffer.aggregate_overflow == 1


def test_session_switch_keeps_overflow_counts_separate():
    from app.services.diagnostics.aggregation import AggregateBuffer, AggregateKey
    first, second = str(uuid4()), str(uuid4())
    buffer = AggregateBuffer(max_keys=1)
    for session_id in (first, second):
        for stage in ("search", "chat"):
            buffer.observe(AggregateKey(session_id, "backend", "/unknown", stage, None, "success"),
                           1000, "success")
    one = buffer.flush(first, 5.0)
    two = buffer.flush(second, 5.0)
    assert sum(item.counts["count"] for item in one) == 2
    assert sum(item.counts["count"] for item in two) == 2
    assert all(item.key.session_id == second for item in two)
