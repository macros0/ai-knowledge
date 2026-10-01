import pytest

from app.services.chat_answer_modes import EvidenceTooLarge, select_batches
from app.services.context_builder import format_context


def test_reduction_groups_respect_token_budget():
    from app.api.chat import _fact_groups

    class Counter:
        def fits(self, _system, user, *, output_tokens):
            assert output_tokens == 100
            return len(user) <= 150

    facts = [
        {"source": i, "quote": f"Q{i}", "text": "X" * 40}
        for i in range(1, 5)
    ]
    groups = _fact_groups(facts, Counter(), 100)
    assert [fact["source"] for group in groups for fact in group] == [1, 2, 3, 4]
    assert len(groups) > 1


def test_final_source_validation_rejects_deleted_document():
    from datetime import datetime, timezone

    from app.api.chat import _validate_final_source_state
    from app.api.errors import ApiError
    from app.db.models import Document
    from app.db.session import session_scope

    doc_id = "1234567890abcdef"
    with session_scope() as session:
        session.add(Document(id=doc_id, filename="source.txt"))
    _validate_final_source_state([{"doc_id": doc_id, "generation_id": None}])
    with session_scope() as session:
        session.get(Document, doc_id).deleted_at = datetime.now(timezone.utc)
    with pytest.raises(ApiError) as exc:
        _validate_final_source_state([{"doc_id": doc_id, "generation_id": None}])
    assert exc.value.code == "chat_sources_changed"


def _block(number, size):
    return {
        "_source_index": number,
        "doc_id": str(number),
        "title": f"Документ {number}",
        "content": "x" * size,
        "filepath": f"{number}.md",
        "tags": [],
        "point_type": "concept",
    }


def test_fast_uses_one_batch_and_keeps_global_source_numbers():
    blocks = [_block(1, 200), _block(2, 200), _block(3, 200)]
    batches = select_batches(blocks, mode="fast", max_context_chars=550, fits=lambda text: True)
    assert len(batches) == 1
    assert [item["_source_index"] for item in batches[0]] == [1]
    assert '<context_block id="1">' in format_context(batches[0])


def test_full_covers_all_blocks_in_order():
    blocks = [_block(1, 200), _block(2, 200), _block(3, 200)]
    batches = select_batches(blocks, mode="full", max_context_chars=550, fits=lambda text: True)
    assert [item["_source_index"] for batch in batches for item in batch] == [1, 2, 3]
    assert '<context_block id="3">' in format_context(batches[-1])


def test_full_rejects_oversize_indivisible_block():
    with pytest.raises(EvidenceTooLarge):
        select_batches([_block(1, 1000)], mode="full", max_context_chars=550, fits=lambda text: True)


def test_fast_skips_oversize_and_uses_next():
    batches = select_batches([_block(1, 1000), _block(2, 100)], mode="fast", max_context_chars=550, fits=lambda text: True)
    assert [[item["_source_index"] for item in batch] for batch in batches] == [[2]]


def test_full_splits_table_only_between_complete_rows():
    table = _block(1, 0)
    table["content"] = "| Код | Значение |\n| --- | --- |\n" + "".join(
        f"| {number:03d} | описание |\n" for number in range(20)
    )
    batches = select_batches([table], mode="full", max_context_chars=480, fits=lambda text: True)
    assert len(batches) > 1
    assert all("| Код | Значение |" in batch[0]["content"] for batch in batches)
    rows = [line for batch in batches for line in batch[0]["content"].splitlines() if "описание" in line]
    assert rows == [f"| {number:03d} | описание |" for number in range(20)]


def test_fast_keeps_first_segment_before_later_oversized_row():
    table = _block(1, 0)
    table["content"] = "| Код | Значение |\n| --- | --- |\n| 001 | есть |\n" + "x" * 2000
    calls = []
    def fits(context):
        calls.append(context)
        return True
    batches = select_batches([table], mode="fast", max_context_chars=480, fits=fits)
    assert len(batches) == 1
    assert "| 001 | есть |" in batches[0][0]["content"]
    assert "x" * 100 not in batches[0][0]["content"]
    assert len(calls) < 10


def test_fast_skips_oversized_first_row_and_uses_later_row():
    table = _block(1, 0)
    table["content"] = "| Код | Значение |\n| --- | --- |\n" + "x" * 2000 + "\n| 002 | есть |\n"
    batches = select_batches([table], mode="fast", max_context_chars=480, fits=lambda text: True)
    assert len(batches) == 1
    assert "| 002 | есть |" in batches[0][0]["content"]
    assert "x" * 100 not in batches[0][0]["content"]
