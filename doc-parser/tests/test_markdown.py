"""Тесты преобразования блоков в Markdown."""
from pathlib import Path

from docparser import Block, blocks_to_markdown


class TestBlocksToMarkdown:
    def test_heading_paragraph_table(self):
        blocks = [
            Block("heading", "Глава", level=2),
            Block("paragraph", "Абзац текста"),
            Block("table", "| A | B |\n|---|---|"),
        ]
        md = blocks_to_markdown(blocks)
        assert "## Глава" in md
        assert "Абзац текста" in md
        assert "| A | B |" in md

    def test_heading_level_clamped(self):
        md = blocks_to_markdown([Block("heading", "x", level=9), Block("heading", "y", level=0)])
        assert md.startswith("###### x")
        assert "y" in md
        assert "#######" not in md

    def test_comment_with_author(self):
        md = blocks_to_markdown([Block("comment", "Замечание", meta={"author": "Рецензент"})])
        assert "**Комментарий рецензента (Рецензент):**" in md
        assert "Замечание" in md

    def test_comment_without_author(self):
        md = blocks_to_markdown([Block("comment", "Замечание")])
        assert "**Комментарий рецензента:**" in md

    def test_attachment_marker(self):
        blocks = [Block("attachment", "Вложение: a.xlsx (zip)", meta={"saved_path": "out/a.xlsx"})]
        md = blocks_to_markdown(blocks)
        assert "Вложение: a.xlsx (zip)" in md
        assert "out/a.xlsx" in md

    def test_image_link(self):
        blocks = [
            Block(
                "image",
                "",
                meta={"kind": "image", "name": "image-0.png", "caption": "Схема", "saved_path": "att/image-0.png"},
            )
        ]
        md = blocks_to_markdown(blocks)
        assert "![Схема](attachments/image-0.png)" in md

    def test_image_without_saved_path(self):
        md = blocks_to_markdown([Block("image", "", meta={"kind": "image", "caption": "Схема"})])
        assert "(изображение: Схема)" in md

    def test_code_block_fenced(self):
        md = blocks_to_markdown([Block("code", "def f():\n    pass")])
        assert "```" in md
        assert "def f():" in md
        assert "    pass" in md

    def test_empty(self):
        assert blocks_to_markdown([]) == ""
