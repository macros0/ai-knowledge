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

    def test_comment_thread_rendered(self):
        """Тред: контекст → вопрос (автор, дата) → ответы → статус; каждая
        строка — внутри блок-цитаты (формат распознаёт backend-экстрактор)."""
        block = Block(
            "comment",
            "Какой ТН считать свежим?",
            meta={
                "author": "Волкова",
                "thread": [
                    {"author": "Волкова", "date": "2026-08-12T10:00:00Z", "text": "Какой ТН считать свежим?"},
                    {"author": "Сагитов", "date": "2026-08-13T10:00:00Z", "text": "Наибольший табельный."},
                ],
                "context": "Алгоритм выбора табельного",
                "resolved": True,
            },
        )
        md = blocks_to_markdown([block])
        lines = [line for line in md.split("\n") if line.startswith(">")]
        assert lines == [
            "> **Контекст:** Алгоритм выбора табельного",
            "> **Комментарий рецензента (Волкова, 2026-08-12):** Какой ТН считать свежим?",
            "> **Ответ (Сагитов):** Наибольший табельный.",
            "> **Статус:** замечание закрыто",
        ]

    def test_comment_thread_without_context_and_status(self):
        block = Block(
            "comment",
            "Вопрос?",
            meta={
                "author": "Рецензент",
                "thread": [{"author": "Рецензент", "date": "", "text": "Вопрос?"}],
            },
        )
        md = blocks_to_markdown([block])
        assert md == "> **Комментарий рецензента (Рецензент):** Вопрос?"

    def test_comment_thread_multiline_text_quoted(self):
        """Многострочный текст записи не разрывает блок-цитату."""
        block = Block(
            "comment",
            "Строка 1\nСтрока 2",
            meta={"author": "Рецензент", "thread": [{"author": "Рецензент", "date": "", "text": "Строка 1\nСтрока 2"}]},
        )
        md = blocks_to_markdown([block])
        assert "> **Комментарий рецензента (Рецензент):** Строка 1" in md
        assert "> Строка 2" in md

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
