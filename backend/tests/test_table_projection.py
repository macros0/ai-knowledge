"""LLM table projection must preserve data and canonical checkpoint text."""
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services import field_table
from app.services.okf_generator import OKFGenerator


def prompt_for(text):
    generator = OKFGenerator(llm=SimpleNamespace())
    return generator._build_prompt(text, "source.xlsx", 1, 1)


def test_generation_prompt_removes_excel_empty_tail_without_changing_checkpoint(tmp_path):
    width = 16_384
    text = "\n".join([
        "| Name | Value " + "| " * (width - 2) + "|",
        "|" + "---|" * width,
        "| Alpha | 0 " + "| " * (width - 2) + "|",
        "| Beta | False " + "| " * (width - 2) + "|",
    ])
    checkpoint = tmp_path / "chunk_118.md"
    checkpoint.write_text(text, encoding="utf-8")
    before = checkpoint.read_bytes()
    prompt = prompt_for(text)
    assert len(prompt) < 10_000
    assert "| Alpha | 0 |" in prompt
    assert "| Beta | False |" in prompt
    assert checkpoint.read_bytes() == before


def test_classifier_compacts_empty_tail_and_validates_transmitted_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(field_table, "get_settings", lambda: Settings(_env_file=None, data_dir=tmp_path))
    header = ["Name", "Value"] + [""] * 200
    rows = ["| Alpha | value " + "| " * 200 + "|"]
    seen = []

    def classify(system, user, **kwargs):
        seen.append(user)
        return {"concept_per_row": True, "title_col": 2, "concept_type": "reference", "extraction_mode": "per_row"}

    with pytest.raises(ValueError, match="outside"):
        field_table._llm_classify_table(header, rows, SimpleNamespace(chat_json=classify), "test", 0)
    assert len(seen[0]) < 1000
    assert header == ["Name", "Value"] + [""] * 200


def test_classifier_uses_last_row_to_determine_width():
    header = ["Name", "", ""]
    rows = ["| Alpha | | |"] * 5 + ["| Beta | | Late value |"]
    user = field_table._classification_user(header, rows)
    assert "['Name', '', '']" in user
    assert "| --- | --- | --- |" in user
    assert "Late value" not in user


@pytest.mark.parametrize("cell", [r"A\|B", "`A|B`", "``A`|B``", "line<br>next", "0", "False"])
def test_projection_preserves_cell_syntax_and_interior_empty_column(cell):
    text = f"| Name | | Value | |\n|---|---|---|---|\n| {cell} | | end | |"
    prompt = prompt_for(text)
    assert f"| {cell} | | end |" in prompt
    assert "|---|---|---|---|" not in prompt


@pytest.mark.parametrize("text", [
    "| Name | Value | |\n|---|---|---|\n| Alpha | too | many | cells |",
    "| Name | Value | |\n|---|---|---|\n| Alpha | unfinished\ncontinuation | |",
    "```markdown\n| Name | Value | |\n|---|---|---|\n| Alpha | 0 | |\n```",
    "~~~\n| Name | Value | |\n|---|---|---|\n| Alpha | 0 | |\n~~~",
])
def test_ambiguous_or_fenced_tables_are_sent_unchanged(text):
    assert text in prompt_for(text)


def test_nonempty_header_preserves_empty_body_column():
    text = "| Name | Meaning | |\n|---|---|---|\n| Alpha | | |"
    prompt = prompt_for(text)
    assert "| Name | Meaning |" in prompt
    assert "| Alpha | |" in prompt


def test_table_projection_preserves_other_text_and_multiple_tables():
    text = "Before\n\n| A | |\n|---|---|\n| One | |\n\nBetween\n\n| B | |\n|---|---|\n| Two | |\n\nAfter"
    prompt = prompt_for(text)
    assert "Before\n\n| A |\n|---|\n| One |\n\nBetween" in prompt
    assert "| B |\n|---|\n| Two |\n\nAfter" in prompt


def test_generation_retains_a_column_populated_only_in_last_row():
    text = "| Name | | | |\n|---|---|---|---|\n| Alpha | | | |\n| Beta | | late | |"
    prompt = prompt_for(text)
    assert "| Name | | |\n|---|---|---|" in prompt
    assert "| Beta | | late |" in prompt


def test_generation_evidence_uses_canonical_chunk_offsets(monkeypatch):
    from app.services import okf_generator
    from app.services.source_evidence import chunk_digest

    source = "| Name | Value | |\n|---|---|---|\n| Alpha | Unique retained source evidence | |"
    seen = []

    def answer(system, user, **kwargs):
        seen.append(user)
        return [{"id": "alpha", "title": "Alpha", "type": "reference", "content": "Summary", "source_quotes": ["Unique retained source evidence"]}]

    monkeypatch.setattr(okf_generator, "extract_table_concepts", lambda text, **kw: ([], text))
    generator = OKFGenerator(llm=SimpleNamespace(chat_json=answer))
    concepts = generator.generate_chunk(source, "source.xlsx", 1, 1)
    assert "|---|---|---|" not in seen[0]
    span = concepts[0].source_spans[0]
    assert source[span.start:span.end] == "Unique retained source evidence"
    assert span.chunk_hash == chunk_digest(source)


def test_classifier_cache_tracks_width_changes_beyond_preview(monkeypatch):
    monkeypatch.setattr(field_table, "_get_prompt_hash", lambda: "fixed")
    rows = ["| Alpha | | |"] * 6
    original = field_table._build_cache_key(["Name", "", ""], rows)
    rows[-1] = "| Alpha | | Late value |"
    assert field_table._build_cache_key(["Name", "", ""], rows) != original


def test_projection_preserves_crlf_and_alignment():
    text = "  | Name | Value | |\r\n  |:---|---:|---|\r\n  | Alpha | False | |\r\n"
    assert "  | Name | Value |\r\n  |:---|---:|\r\n  | Alpha | False |\r\n" in prompt_for(text)


@pytest.mark.parametrize("mode", ["per_row", "whole"])
def test_programmatic_concepts_drop_empty_tail_but_keep_original_source_spans(mode, monkeypatch):
    from app.services.source_evidence import chunk_digest

    width = 300
    text = "\n".join([
        "| Name | Value " + "| " * (width - 2) + "|",
        "|" + "---|" * width,
        "| Alpha | kept " + "| " * (width - 2) + "|",
        "| Beta | retained " + "| " * (width - 2) + "|",
        "| Gamma | third " + "| " * (width - 2) + "|",
    ])
    monkeypatch.setattr(field_table, "_min_rows", lambda: 1)
    block = field_table.detect_tables(text)[0]
    cls = field_table.TableClassification(concept_per_row=True, title_col=0, extraction_mode=mode)
    concepts = field_table.build_row_concepts(block, cls, None, lines=text.split("\n"))
    assert all(len(c.content) < 1000 for c in concepts)
    assert all("Колонка 299" not in c.content for c in concepts)
    assert all(span.chunk_hash == chunk_digest(text) for c in concepts for span in c.source_spans)
    assert "retained" in "\n".join(c.content for c in concepts)
    assert len(block.header) == width
