"""Lossless generic chunk projection; immutable version and physical identity."""

import unicodedata
import pytest
from app.services.chunk_index_projection import (
    CURRENT_CHUNK_PROJECTION_VERSION,
    build_chunk_index_projection,
    chunk_fragment_point_id,
    iter_chunk_index_fragments,
)
from app.services.vector_store import chunk_point_id


def assert_coverage(title, content):
    projection = build_chunk_index_projection(title, content, version=1)
    parts = list(projection.fragments())
    assert parts
    for part in parts:
        assert len(part.text.encode("utf-8")) <= 2000
        assert len(unicodedata.normalize("NFKC", part.text).encode("utf-8")) <= 2000
    if not projection.legacy:
        for field, value in [("title", title), ("body", projection.body)]:
            field_parts = [part for part in parts if part.field == field]
            assert "".join(part.covered_text for part in field_parts) == value
            cursor = 0
            for part in field_parts:
                assert part.start == cursor
                assert part.covered_text == value[part.start : part.end]
                cursor = part.end
            assert cursor == len(value)
    return projection, parts


@pytest.mark.parametrize("version", [None, 0, 2, True, "1", -1])
def test_unknown_versions_do_not_downgrade(version):
    with pytest.raises(ValueError):
        build_chunk_index_projection("title", "body", version=version)


def test_current_version_and_short_legacy_identity():
    assert CURRENT_CHUNK_PROJECTION_VERSION == 1
    projection, parts = assert_coverage("Section", "0 False")
    assert projection.legacy and len(parts) == 1 and parts[0].text == "Section\n0 False"
    assert parts[0].descriptor is None
    assert chunk_fragment_point_id("doc", 3, "gen", "source", parts[0]) == chunk_point_id(
        "doc", 3, generation_id="gen"
    )


@pytest.mark.parametrize(
    "body",
    [
        "|A||B||\n|---|---|---|---|\n|0||False||\n|0||False||\n|last||x|filled|\n",
        "```\n|A|||\n|---|---|---|\n|0|||\n```\n",
        "|A|||\n|---|---|---|\n|0||\n",
    ],
)
def test_internal_blanks_final_value_fences_and_ragged_stay(body):
    projection, _ = assert_coverage("", body)
    assert projection.body == body


def test_compaction_removes_only_fully_empty_tail_and_retains_duplicates():
    projection, parts = assert_coverage(
        "", "|A||B||\n|---|---|---|---|\n|0||False||\n|0||False||\n"
    )
    assert projection.body == "|A||B|\n|---|---|---|\n|0||False|\n|0||False|\n"
    assert not projection.legacy
    assert parts[0].descriptor["source_hash"] == projection.source_hash


@pytest.mark.parametrize(
    "title,body",
    [
        ("Длинная шапка 👩‍💻 " * 200, "|" + ("Ё𝄞" * 6000) + "|\n"),
        ("title", "ﷺ" * 1500),
        ("", "same physical line\n" * 1000),
        ("title", "line 0 False\n" * 1000),
    ],
    ids=["long-title-row", "nfkc-expansion", "duplicates", "line-boundary"],
)
def test_long_title_and_body_and_normalization_expansion_are_complete(title, body):
    assert_coverage(title, body)


def test_parts_beyond_old_index_cap_are_represented():
    _, parts = assert_coverage("title", "x" * 10000 + " final unique value")
    assert "final unique value" in parts[-1].covered_text
    assert parts[-1].end > 8000


def test_point_identity_separates_repeated_parts_scope_title_and_generation():
    _, parts = assert_coverage("title", "repeated row\n" * 1500)
    ids = [chunk_fragment_point_id("doc", 4, "gen", "source", part) for part in parts]
    assert len(set(ids)) == len(parts)
    first = parts[0]
    assert ids[0] == chunk_fragment_point_id("doc", 4, "gen", "source", first)
    for doc, idx, gen, source in [
        ("other", 4, "gen", "source"),
        ("doc", 5, "gen", "source"),
        ("doc", 4, "other", "source"),
        ("doc", 4, "gen", "other"),
    ]:
        assert ids[0] != chunk_fragment_point_id(doc, idx, gen, source, first)
    changed = next(iter_chunk_index_fragments("changed", "repeated row\n" * 1500, version=1))
    assert ids[0] != chunk_fragment_point_id("doc", 4, "gen", "source", changed)


def test_line_boundaries_and_explicit_long_line_continuation():
    _, parts = assert_coverage("", "line\n" * 3000)
    assert all(part.covered_text.endswith("\n") for part in parts[:-1])
    _, parts = assert_coverage("", "a" * 9000)
    assert all(part.continuation for part in parts)


def test_projector_is_lazy_and_has_no_provider_or_database_dependencies():
    parts = iter_chunk_index_fragments("title", "a" * 200000, version=1)
    assert iter(parts) is parts
    assert next(parts).ordinal == 0


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_header_hints_ignore_nonclosing_fence_suffix(newline):
    table = "| fake | value |\n| --- | --- |\n" + "| sample | 0 False |\n" * 200
    body = ("```text\n```still code\n" + table + "```\n").replace("\n", newline)
    projection, parts = assert_coverage("", body)
    assert projection.body == body
    assert projection.headers == ()
    assert all("Table header: " not in part.text for part in parts)


@pytest.mark.parametrize("indent", ["    ", "\t"], ids=["spaces", "tab"])
def test_header_hints_ignore_indented_code(indent):
    table = "| fake | value |\n| --- | --- |\n" + "| sample | 0 False |\n" * 200
    body = "".join(indent + line for line in table.splitlines(keepends=True))
    projection, parts = assert_coverage("", body)
    assert projection.body == body
    assert projection.headers == ()
    assert all("Table header: " not in part.text for part in parts)


@pytest.mark.parametrize("tail", ["| ragged |\n", "unseparated continuation\n"], ids=["ragged", "multiline"])
def test_ambiguous_table_does_not_supply_header_hints(tail):
    body = "| header | value |\n| --- | --- |\n" + "| row | 0 False |\n" * 200 + tail
    projection, parts = assert_coverage("", body)
    assert projection.body == body
    assert projection.headers == ()
    assert all("Table header: " not in part.text for part in parts)


def test_real_table_after_fenced_code_keeps_its_own_header():
    code = "```text\n| fake | value |\n| --- | --- |\n| code | 0 |\n```\n\n"
    table = "| actual | value |\n| --- | --- |\n" + "| row | False |\n" * 200
    projection, parts = assert_coverage("", code + table)
    assert len(projection.headers) == 1
    assert projection.headers[0][0] == len(code)
    hints = [part.text for part in parts if "Table header: " in part.text]
    assert hints and all("Table header: | actual | value |" in text for text in hints)
