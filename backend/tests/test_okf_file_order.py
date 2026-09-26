from app.api.documents import _concept_list_sort_key


def test_concepts_follow_document_offsets_then_unanchored_concepts_of_the_same_chunk():
    rows = [
        {"slug": "no-chunk", "chunk_index": None, "source_spans": []},
        {"slug": "later", "chunk_index": 0, "source_spans": [{"start": 90, "end": 100}]},
        {"slug": "unanchored", "chunk_index": 0, "source_spans": []},
        {"slug": "earlier", "chunk_index": 0, "source_spans": [{"start": 12, "end": 18}]},
        {"slug": "next-chunk", "chunk_index": 1, "source_spans": [{"start": 1, "end": 3}]},
    ]

    ordered = sorted(rows, key=lambda row: _concept_list_sort_key(
        row["chunk_index"], row["source_spans"], row["slug"],
    ))

    assert [row["slug"] for row in ordered] == [
        "earlier", "later", "unanchored", "next-chunk", "no-chunk",
    ]


def test_invalid_spans_do_not_claim_a_document_position():
    rows = [
        {"slug": "invalid", "chunk_index": 2, "source_spans": [{"start": "x", "end": 20}]},
        {"slug": "located", "chunk_index": 2, "source_spans": [{"start": 30, "end": 40}]},
    ]

    ordered = sorted(rows, key=lambda row: _concept_list_sort_key(
        row["chunk_index"], row["source_spans"], row["slug"],
    ))

    assert [row["slug"] for row in ordered] == ["located", "invalid"]
