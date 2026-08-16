"""Юнит-тесты для OKF-генерации и парсинга (не требуют LLM/Qdrant)."""
import tempfile
from pathlib import Path

from app.models.schemas import Concept
from app.services.okf_generator import _chunk_text, _normalize, _slugify


class TestSlugify:
    def test_cyrillic_title_falls_back_to_concept(self):
        assert _slugify("Настройка моста") == "concept"

    def test_latin_slug(self):
        assert _slugify("Network Config") == "network-config"

    def test_empty(self):
        assert _slugify("") == "concept"


class TestChunkText:
    def test_small_text_single_chunk(self):
        text = "параграф один.\n\nпараграф два."
        chunks = _chunk_text(text, 1000)
        assert len(chunks) == 1

    def test_large_text_split(self):
        paragraphs = "\n\n".join(f"абзац номер {i} " * 30 for i in range(50))
        chunks = _chunk_text(paragraphs, 1000)
        assert len(chunks) > 1
        assert all(len(c) <= 1100 for c in chunks)

    def test_fenced_code_kept_whole(self):
        code = "```python\n" + "\n\n".join(f"def f{i}():\n    pass" for i in range(30)) + "\n```"
        text = "Абзац " * 40 + "\n\n" + code + "\n\n" + "Абзац " * 40
        chunks = _chunk_text(text, 380)
        middle = chunks[1]
        assert middle.startswith("```")
        assert middle.rstrip().endswith("```")
        assert "\n\n" in middle

    def test_code_indentation_preserved(self):
        from app.services.okf_generator import _split_units

        units = _split_units("def f():\n    a = 1\n\n    b = 2")
        assert units == ["def f():\n    a = 1", "    b = 2"]

    def test_table_kept_in_one_chunk(self):
        table = "| a | b |\n|---|---|\n| 1 | 2 |"
        text = table + "\n\n" + "X" * 1000
        chunks = _chunk_text(text, 200)
        assert any(c.startswith("| a | b |") and c.rstrip().endswith("| 1 | 2 |") for c in chunks)


class TestTruncateContent:
    def test_short_text_unchanged(self):
        from app.services.okf_generator import _truncate_content

        assert _truncate_content("короткий текст", 1000) == "короткий текст"

    def test_closed_fence_kept(self):
        from app.services.okf_generator import _truncate_content

        content = "текст\n\n```python\nx = 1\n```\n\nхвост"
        out = _truncate_content(content, 4000)
        assert "x = 1" in out

    def test_open_fence_truncated_before_block(self):
        from app.services.okf_generator import _truncate_content

        content = ("абзац\n" * 80) + "```python\n" + "код " * 1000
        out = _truncate_content(content, 200)
        assert "```" not in out

    def test_no_mid_line_cut_when_not_in_fence(self):
        from app.services.okf_generator import _truncate_content

        content = "короткая строка\n" * 100
        out = _truncate_content(content, 150)
        assert not out.rstrip().endswith("короткая строк")


class TestNormalize:
    def test_valid_concept(self):
        raw = [
            {
                "id": "bridge-setup",
                "title": "Настройка моста",
                "type": "procedure",
                "tags": ["proxmox", "network"],
                "content": "# Bridge\n\nТекст",
                "relations": [],
            }
        ]
        concepts = _normalize(raw)
        assert len(concepts) == 1
        assert concepts[0].type == "procedure"
        assert concepts[0].tags == ["proxmox", "network"]

    def test_invalid_type_falls_back(self):
        raw = [{"title": "X", "type": "garbage", "content": "текст"}]
        assert _normalize(raw)[0].type == "concept"

    def test_empty_content_skipped(self):
        raw = [{"title": "X", "content": "  "}, {"title": "Y", "content": "ok"}]
        assert len(_normalize(raw)) == 1


class TestOkfMarkdown:
    def test_frontmatter_and_body(self):
        from app.services.okf_generator import _build_markdown

        concept = Concept(
            id="bridge",
            title="Настройка моста",
            type="concept",
            tags=["proxmox"],
            content="## VLAN\n\nтаблица",
            relations=["vlan.md"],
        )
        md = _build_markdown(concept, "doc.docx", "abc123")
        assert md.startswith("---")
        assert "title: Настройка моста" in md
        assert "tags:" in md
        assert "source_document:" in md
        assert "## VLAN" in md
        assert md.rstrip().endswith("таблица")

    def test_frontmatter_with_attachments(self):
        from app.services.okf_generator import _build_markdown

        concept = Concept(id="c", title="Концепт", type="concept", tags=[], content="тело")
        md = _build_markdown(
            concept,
            "doc.docx",
            "abc123",
            attachments=[{"name": "embedded.xlsx", "kind": "zip", "caption": "", "saved_path": "attachments/embedded.xlsx"}],
        )
        assert "attachments:" in md
        assert "embedded.xlsx" in md
        assert "attachments/embedded.xlsx" in md

    def test_frontmatter_with_global_tags(self):
        from app.services.okf_generator import _build_markdown

        concept = Concept(id="c", title="Концепт", type="concept", tags=["llm-tag"], content="тело")
        md = _build_markdown(concept, "doc.docx", "abc123", global_tags=["proxmox", "network"])
        assert "global_tags:" in md
        assert "proxmox" in md
        assert "network" in md

    def test_frontmatter_global_tags_default_empty(self):
        from app.services.okf_generator import _build_markdown

        concept = Concept(id="c", title="Концепт", type="concept", tags=[], content="тело")
        md = _build_markdown(concept, "doc.docx", "abc123")
        assert "global_tags: []" in md


class TestGenerateChunk:
    def _make_gen(self, tmp_path, llm):
        from app.services.okf_generator import OKFGenerator

        return OKFGenerator(llm=llm, bundle_root=tmp_path / "okf")

    class FakeLLM:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user):
            self.calls.append(user)
            return [
                {
                    "id": "k1",
                    "title": "Concept One",
                    "type": "concept",
                    "tags": ["a"],
                    "content": "Тело концепта.",
                    "relations": [],
                }
            ]

    def test_single_chunk_uses_full_document_prompt(self, tmp_path):
        llm = self.FakeLLM()
        gen = self._make_gen(tmp_path, llm)
        concepts = gen.generate_chunk("текст", "doc.docx", 1, 1)
        assert len(concepts) == 1
        assert "Текст документа" in llm.calls[0]

    def test_multi_chunk_uses_chunk_prompt(self, tmp_path):
        llm = self.FakeLLM()
        gen = self._make_gen(tmp_path, llm)
        concepts = gen.generate_chunk("текст", "doc.docx", 2, 5)
        assert len(concepts) == 1
        assert "Фрагмент 2 из 5" in llm.calls[0]

    def test_chunk_text_respects_limit(self, tmp_path):
        from app.config import Settings

        gen = self._make_gen(tmp_path, self.FakeLLM())
        gen.settings = Settings(okf_max_chunk_chars=100)
        text = "\n\n".join("абзац " * 40 for _ in range(20))
        chunks = gen.chunk_text(text)
        assert len(chunks) > 1

    def test_generate_combines_all_chunks(self, tmp_path):
        class CountingLLM:
            def __init__(self):
                self.n = 0

            def chat_json(self, system, user):
                self.n += 1
                return [
                    {
                        "id": f"k{self.n}",
                        "title": f"Concept {self.n}",
                        "type": "concept",
                        "tags": [],
                        "content": f"тело {self.n}",
                        "relations": [],
                    }
                ]

        from app.config import Settings

        llm = CountingLLM()
        gen = self._make_gen(tmp_path, llm)
        gen.settings = Settings(okf_max_chunk_chars=100)
        text = "\n\n".join("абзац " * 40 for _ in range(20))
        concepts = gen.generate(text, "doc.docx")
        assert len(concepts) == llm.n > 1

    def test_save_bundle_with_preallocated_slugs(self, tmp_path):
        gen = self._make_gen(tmp_path, self.FakeLLM())
        concepts = [_normalize([{"id": "a", "title": "Bridge", "type": "concept", "tags": [], "content": "1", "relations": []}])[0]]
        okf_docs = gen.save_bundle("doc1", "in.docx", concepts, slugs=["my-slug"])
        assert Path(okf_docs[0].filepath).name == "my-slug.md"


class TestBundleRoundtrip:
    def test_save_and_read(self, tmp_path: Path):
        from app.services.okf_generator import OKFGenerator

        class FakeLLM:
            def __init__(self, raw):
                self.raw = raw

            def chat_json(self, system, user):
                return self.raw

        raw = [
            {
                "id": "k1",
                "title": "Concept One",
                "type": "concept",
                "tags": ["a", "b"],
                "content": "Тело концепта.",
                "relations": [],
            }
        ]
        gen = OKFGenerator(llm=FakeLLM(raw), bundle_root=tmp_path / "okf")
        okf_docs = gen.save_bundle(
            "doc1",
            "in.docx",
            _normalize(raw),
            attachments=[{"name": "a.xlsx", "kind": "zip", "caption": "", "saved_path": "attachments/a.xlsx"}],
        )
        assert len(okf_docs) == 1
        assert Path(okf_docs[0].filepath).exists()
        assert "Concept One" in okf_docs[0].markdown
        assert okf_docs[0].metadata["attachments"][0]["name"] == "a.xlsx"
