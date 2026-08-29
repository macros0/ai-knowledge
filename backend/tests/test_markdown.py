"""Тесты общего разбора markdown.

Фронтматтер разбирался тремя разными реализациями (api/documents,
vector_store, bundle), extract_section_title жил в pipeline и импортировался
в vector_store внутри функции ради обхода цикла импортов.
"""
from pathlib import Path

from app.services.markdown import (
    extract_section_title,
    parse_frontmatter,
    read_frontmatter,
    strip_heading,
)


class TestParseFrontmatter:
    def test_reads_meta_and_body(self):
        meta, body = parse_frontmatter("---\ntitle: Концепт\ntags: [a, b]\n---\n\nтело")
        assert meta == {"title": "Концепт", "tags": ["a", "b"]}
        assert "тело" in body

    def test_strips_bom(self):
        meta, _ = parse_frontmatter("﻿---\ntitle: X\n---\n\nтело")
        assert meta == {"title": "X"}

    def test_no_frontmatter_returns_whole_text(self):
        meta, body = parse_frontmatter("просто текст")
        assert meta == {}
        assert body == "просто текст"

    def test_broken_yaml_is_not_an_error(self):
        meta, _ = parse_frontmatter("---\n: : :\n---\nтело")
        assert meta == {}

    def test_scalar_frontmatter_is_not_a_dict(self):
        meta, _ = parse_frontmatter("---\nпросто строка\n---\nтело")
        assert meta == {}


class TestReadFrontmatter:
    def test_reads_from_file(self, tmp_path: Path):
        f = tmp_path / "c.md"
        f.write_text("---\ntitle: T\nchunk_index: 3\n---\n\n# T\n\nтело", encoding="utf-8")
        assert read_frontmatter(f) == {"title": "T", "chunk_index": 3}

    def test_missing_file_returns_empty(self, tmp_path: Path):
        assert read_frontmatter(tmp_path / "нет.md") == {}


class TestStripHeading:
    def test_removes_leading_heading(self):
        assert strip_heading("\n# Заголовок\n\nтело") == "тело"

    def test_keeps_body_without_heading(self):
        assert strip_heading("тело\n\nещё") == "тело\n\nещё"


class TestExtractSectionTitle:
    def test_takes_last_heading(self):
        assert extract_section_title("# Первый\n\nтекст\n\n## Второй\n\nтекст") == "Второй"

    def test_no_heading_returns_empty(self):
        assert extract_section_title("просто текст") == ""

    def test_ignores_headings_past_50_lines(self):
        text = "\n".join(["строка"] * 60 + ["# Поздний"])
        assert extract_section_title(text) == ""


def test_all_readers_agree_on_the_same_file(tmp_path: Path):
    """Три прежние реализации разбирали фронтматтер по-разному; теперь
    bundle и read_frontmatter обязаны давать одинаковые метаданные."""
    from app.services.bundle import parse_okf_file

    f = tmp_path / "c.md"
    f.write_text("---\ntitle: T\ntags: [x]\n---\n\n# T\n\nтело концепта", encoding="utf-8")
    meta_bundle, body = parse_okf_file(f)
    assert meta_bundle == read_frontmatter(f)
    assert body == "тело концепта"
