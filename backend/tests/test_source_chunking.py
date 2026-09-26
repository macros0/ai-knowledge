"""Разбиение извлечённого текста без смешения разных источников."""
from docparser.blocks import Block

from app.services.source_chunking import chunk_blocks_by_source


class _Generator:
    def chunk_text(self, text: str) -> list[str]:
        return [text]


def test_chunking_keeps_source_boundaries_when_root_resumes_after_attachment():
    blocks = [
        Block("paragraph", "Текст корневого письма.", meta={"source_id": "root"}),
        Block("paragraph", "Таблица из вложения.", meta={"source_id": "root/0"}),
        Block("paragraph", "Продолжение корневого письма.", meta={"source_id": "root"}),
    ]

    chunks = chunk_blocks_by_source(blocks, _Generator())

    assert [(chunk["source_id"], chunk["content"]) for chunk in chunks] == [
        ("root", "Текст корневого письма."),
        ("root/0", "Таблица из вложения."),
        ("root", "Продолжение корневого письма."),
    ]


def test_attachment_coverage_uses_source_boundaries_even_when_flat_chunk_count_matches():
    from types import SimpleNamespace

    from docparser import markdown_attachment_spans
    from docparser.blocks import Block
    from app.services import source_chunking
    from app.services.okf_generator import OKFGenerator

    generator = OKFGenerator(llm=SimpleNamespace())
    generator.settings = SimpleNamespace(okf_max_chunk_chars=1000)
    blocks = [
        Block("paragraph", "a" * 200, meta={"source_id": "root"}),
        Block("paragraph", "b" * 200, meta={"source_id": "root/0", "from_attachment": True}),
        Block("paragraph", "c" * 600, meta={"source_id": "root/0", "from_attachment": True}),
        Block("paragraph", "d" * 600, meta={"source_id": "root"}),
    ]
    chunks = source_chunking.chunk_blocks_by_source(blocks, generator)
    markdown, spans = markdown_attachment_spans(blocks)
    old_shares = generator.attachment_shares(markdown, spans)
    assert len(chunks) == len(old_shares) == 3
    assert old_shares[0] > 0  # Equal counts hid a root/attachment layout mismatch.
    assert source_chunking.attachment_shares_by_source(blocks, generator) == [0.0, 1.0, 0.0]


def test_generated_attachment_marker_is_metadata_not_canonical_knowledge():
    from docparser.embedded import Attachment, marker_block
    from app.services.source_chunking import attachment_shares_by_source

    attachment = Attachment("approval.msg", "", "", b"")
    attachment.source_id = "root/0"
    marker = marker_block(attachment, parsed=True)
    body = "Для заявки 3509 нужен сертификат Keycloak."
    blocks = [marker, Block("paragraph", body, meta={"source_id": "root/0"})]

    assert chunk_blocks_by_source(blocks, _Generator()) == [
        {"source_id": "root/0", "content": body},
    ]
    assert attachment_shares_by_source(blocks, _Generator()) == [1.0]
    # The parser result remains available to source manifests and downloads.
    assert blocks[0] is marker
    assert marker.meta["name"] == "approval.msg"


def test_unsupported_marker_creates_no_knowledge_but_literal_document_text_survives():
    from docparser.embedded import Attachment, marker_block

    marker = marker_block(Attachment("opaque.bin", "", "", b""), extraction_status="unsupported")
    assert chunk_blocks_by_source([marker], _Generator()) == []
    literal = "Вложение: approval.msg (other) — указать это имя в заявке."
    blocks = [Block("paragraph", literal), Block("attachment", "Legacy extracted content")]
    assert literal in chunk_blocks_by_source(blocks, _Generator())[0]["content"]
    assert "Legacy extracted content" in chunk_blocks_by_source(blocks, _Generator())[0]["content"]
