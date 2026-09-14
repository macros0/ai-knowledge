from test_scripts.benchmark_glossary import build_benchmark_rows, summarize_samples


def test_benchmark_workload_has_exact_term_and_alias_counts():
    terms, aliases = build_benchmark_rows(term_count=3, alias_count=12)

    assert len(terms) == 3
    assert len(aliases) == 12
    assert len({row["canonical"] for row in terms}) == 3
    assert len({row["normalized_alias"] for row in aliases}) == 12
    assert {row["term_index"] for row in aliases} == {0, 1, 2}


def test_summarize_samples_reports_avg_p50_and_p95_in_milliseconds():
    summary = summarize_samples([0.001, 0.002, 0.003, 0.004, 0.005])

    assert summary["count"] == 5
    assert summary["avg_ms"] == 3.0
    assert summary["p50_ms"] == 3.0
    assert summary["p95_ms"] == 4.8
