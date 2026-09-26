"""End-to-end source ownership for a selected Outlook-exported EML."""
from __future__ import annotations

from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default
from pathlib import Path

import pytest

from docparser import PARSER_VERSION

from app.config import Settings
from app.db.models import DocumentChunk, DocumentSource, OkfConcept
from app.db.session import session_scope
from app.models.schemas import Concept
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry
from app.services.generation_files import active_bundle_path


DOC_ID = "mailpipe00000001"
NESTED_MSG_FIXTURE = Path(__file__).parents[2] / "doc-parser" / "tests" / "fixtures" / "mail" / "nested-rtf.msg"


def _mail_bytes() -> bytes:
    nested = EmailMessage()
    nested["Subject"] = "Решение"
    nested["From"] = "approver@example.test"
    nested.set_content("Лимит согласования: 12 дней.")

    root = EmailMessage()
    root["Subject"] = "Пересылка решения"
    root["From"] = "sender@example.test"
    root.set_content("См. вложенное решение.")
    root.add_attachment(nested, filename="decision.eml")
    return root.as_bytes()


@pytest.mark.parametrize("body", ["", "Срок оплаты: 18 дней."])
def test_image_only_cid_with_escaped_caption_skips_llm_and_preserves_artifact(tmp_path, monkeypatch, body):
    import io
    from PIL import Image

    settings = Settings(
        _env_file=None, data_dir=tmp_path, embedding_provider="fake", embedding_dimensions=8,
        llm_model="test/mail", dedup_enabled=False, dev_detection_enabled=False,
        okf_write_bundles=True, mail_import_enabled=True,
    )
    for module in ("app.config", "app.services.pipeline", "app.services.staging", "app.services.embedder",
                   "app.services.llm_client", "app.services.okf_generator", "app.services.vector_store"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    payload = buffer.getvalue()
    message = EmailMessage()
    message.set_content(f'<p>{body}</p><img src="cid:figure" alt="Figure [1]">', subtype="html")
    message.add_related(payload, maintype="image", subtype="png", cid="<figure>")
    source = settings.uploads_dir / f"{DOC_ID}.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(message.as_bytes())
    registry.create(DOC_ID, "image.eml", "message/rfc822", source.stat().st_size)
    pipeline = Pipeline()
    calls = []

    def generate(text, *_args, **_kwargs):
        calls.append(text)
        return [Concept(id="body", title="Срок оплаты", content=body)]

    pipeline.okf_generator.generate_chunk = generate
    for method in ("ensure_collection", "delete_document", "delete_orphaned_points"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: None)
    for method in ("index_concepts", "index_chunks"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: set())
    pipeline._process(DOC_ID, source, "image.eml", [], resume=False)
    assert len(calls) == (1 if body else 0)
    document = registry.get(DOC_ID)
    assert document["status"] == "done"
    assert document["problem"] == (None if body else "no_text_layer")
    with session_scope() as session:
        image = session.query(DocumentSource).filter_by(doc_id=DOC_ID, source_id="root/0").one()
        chunks = session.query(DocumentChunk).filter_by(doc_id=DOC_ID).all()
        assert session.query(OkfConcept).filter_by(doc_id=DOC_ID).count() == (1 if body else 0)
    assert (settings.uploads_dir / DOC_ID / image.saved_path).read_bytes() == payload
    assert (active_bundle_path(settings, DOC_ID) / "attachments/source-root-0.png").read_bytes() == payload
    assert any(r"![Figure \[1\]](attachments/source-root-0.png)" in chunk.content for chunk in chunks)


@pytest.mark.parametrize("attachment_kind", ["none", "unsupported", "cid"])
def test_mail_pipeline_keeps_nested_mail_chunks_concepts_and_sources_together(
    tmp_path, monkeypatch, attachment_kind,
):
    unsupported_attachment = attachment_kind == "unsupported"
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        embedding_provider="fake",
        embedding_dimensions=8,
        llm_model="test/mail",
        dedup_enabled=False,
        dev_detection_enabled=False,
        okf_write_bundles=True,
        mail_import_enabled=True,
    )
    for module in (
        "app.config",
        "app.services.pipeline",
        "app.services.staging",
        "app.services.embedder",
        "app.services.llm_client",
        "app.services.okf_generator",
        "app.services.vector_store",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)

    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    source = settings.uploads_dir / f"{DOC_ID}.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    message = BytesParser(policy=default).parsebytes(_mail_bytes())
    unsupported_bytes = b"\x78\x9f\x3e\x22" + b"\0" * 32
    if unsupported_attachment:
        message.add_attachment(unsupported_bytes, maintype="application", subtype="ms-tnef", filename="winmail.dat")
    if attachment_kind == "cid":
        import io
        from PIL import Image

        image_buffer = io.BytesIO()
        Image.new("RGB", (2, 2), "red").save(image_buffer, format="PNG")
        image_bytes = image_buffer.getvalue()
        next(message.iter_parts()).set_content('<p>Mail with <img src="cid:diagram" alt="Diagram"></p>', subtype="html")
        message.add_attachment(image_bytes, maintype="image", subtype="png", cid="<diagram>")
    source.write_bytes(message.as_bytes())
    registry.create(DOC_ID, "forward.eml", "message/rfc822", source.stat().st_size)

    pipeline = Pipeline()
    pipeline.okf_generator.generate_chunk = lambda text, *_args, **_kwargs: [
        Concept(id="mail", title="Фрагмент письма", content=text)
    ]
    for method in ("ensure_collection", "delete_document", "delete_orphaned_points"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: None)
    for method in ("index_concepts", "index_chunks"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: set())

    pipeline._process(DOC_ID, source, "forward.eml", [], resume=False)

    document = registry.get(DOC_ID)
    assert document["parser_version"] == PARSER_VERSION
    warnings = ([{"code": "unsupported_attachment_format", "source_id": "root/1"}]
                if unsupported_attachment else [])
    assert document["parse_warnings"] == warnings
    assert document["status"] == "done"
    assert document["problem"] == ("attachment_partial_result" if unsupported_attachment else None)

    with session_scope() as session:
        source_rows = session.query(DocumentSource).filter_by(doc_id=DOC_ID).order_by(DocumentSource.source_id).all()
        source_ids = [row.source_id for row in source_rows]
        chunk_source_ids = [row.source_id for row in session.query(DocumentChunk).filter_by(doc_id=DOC_ID).order_by(DocumentChunk.chunk_index)]
        concept_source_ids = [row.source_id for row in session.query(OkfConcept).filter_by(doc_id=DOC_ID).order_by(OkfConcept.slug)]
        parser_versions = {row.parser_version for row in session.query(DocumentSource).filter_by(doc_id=DOC_ID)}
        canonical_texts = [row.content for row in session.query(DocumentChunk).filter_by(doc_id=DOC_ID)]

    expected_sources = ["root", "root/0"] if attachment_kind == "none" else ["root", "root/0", "root/1"]
    assert source_ids == expected_sources
    root_source = next(row for row in source_rows if row.source_id == "root")
    assert root_source.saved_path is None  # An inline reference never replaces the original mail.
    if attachment_kind == "cid":
        from app.db.models import OkfAttachment

        image_source = next(row for row in source_rows if row.source_id == "root/1")
        assert image_source.saved_path.endswith("/attachments/source-root-1.png")
        assert image_source.extraction_status == "saved"
        with session_scope() as session:
            image_attachment = session.query(OkfAttachment).filter_by(doc_id=DOC_ID, source_id="root/1").one()
        assert image_attachment.content_type == "image/png"
        assert (settings.uploads_dir / DOC_ID / image_source.saved_path).read_bytes() == image_bytes
        assert (active_bundle_path(settings, DOC_ID) / "attachments/source-root-1.png").read_bytes() == image_bytes
    if unsupported_attachment:
        unsupported = next(row for row in source_rows if row.source_id == "root/1")
        assert unsupported.extraction_status == "unsupported"
        assert unsupported.artifact_kind == "original"
        assert unsupported.warnings == warnings
        assert (settings.uploads_dir / DOC_ID / unsupported.saved_path).read_bytes() == unsupported_bytes
    child_source = next(row for row in source_rows if row.source_id == "root/0")
    assert child_source.saved_path.startswith("generations/")
    assert child_source.saved_path.endswith("/attachments/source-root-0.eml")
    assert (settings.uploads_dir / DOC_ID / child_source.saved_path).read_bytes()
    assert set(chunk_source_ids) == {"root", "root/0"}
    assert set(concept_source_ids) == {"root", "root/0"}
    assert parser_versions == {PARSER_VERSION}
    assert any("Лимит согласования: 12 дней." in text for text in canonical_texts)
    assert all("Вложение:" not in text for text in canonical_texts)
    if attachment_kind == "cid":
        assert any("![Diagram](attachments/source-root-1.png)" in text for text in canonical_texts)
    else:
        assert all("attachments/" not in text for text in canonical_texts)
    import json

    source_manifest = json.loads((active_bundle_path(settings, DOC_ID) / "sources.json").read_text(encoding="utf-8"))
    assert [row["source_id"] for row in source_manifest["sources"]] == expected_sources
    if unsupported_attachment:
        assert source_manifest["sources"][2]["warnings"] == warnings
        assert source_manifest["sources"][2]["extraction_status"] == "unsupported"
    assert all(not (row.get("saved_path") or "").startswith(("C:", "/")) for row in source_manifest["sources"])


def test_msg_substorage_pipeline_keeps_nested_mail_source_ownership(tmp_path, monkeypatch):
    """MSG substorage must preserve the same source boundary as MIME forwarding."""
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        embedding_provider="fake",
        embedding_dimensions=8,
        llm_model="test/mail",
        dedup_enabled=False,
        dev_detection_enabled=False,
        okf_write_bundles=True,
        mail_import_enabled=True,
    )
    for module in (
        "app.config",
        "app.services.pipeline",
        "app.services.staging",
        "app.services.embedder",
        "app.services.llm_client",
        "app.services.okf_generator",
        "app.services.vector_store",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)

    doc_id = "mailpipe00000002"
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    source = settings.uploads_dir / f"{doc_id}.msg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(NESTED_MSG_FIXTURE.read_bytes())
    registry.create(doc_id, "nested.msg", "application/vnd.ms-outlook", source.stat().st_size)

    pipeline = Pipeline()
    pipeline.okf_generator.generate_chunk = lambda text, *_args, **_kwargs: [
        Concept(id="mail", title="Фрагмент письма", content=text)
    ]
    for method in ("ensure_collection", "delete_document", "delete_orphaned_points"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: None)
    for method in ("index_concepts", "index_chunks"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: set())

    pipeline._process(doc_id, source, "nested.msg", [], resume=False)

    with session_scope() as session:
        sources = session.query(DocumentSource).filter_by(doc_id=doc_id).order_by(DocumentSource.source_id).all()
        chunks = session.query(DocumentChunk).filter_by(doc_id=doc_id).order_by(DocumentChunk.chunk_index).all()
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).order_by(OkfConcept.slug).all()

    assert [(row.source_id, row.parent_source_id, row.kind) for row in sources] == [
        ("root", None, "document"),
        ("root/0", "root", "mail"),
    ]
    assert {row.source_id for row in chunks} == {"root", "root/0"}
    assert {row.source_id for row in concepts} == {"root", "root/0"}
    child_text = "\n".join(row.content for row in chunks if row.source_id == "root/0")
    assert "This is a testmail." in child_text
    assert "Mail in mail." not in child_text





def test_pipeline_reports_embedded_parse_warning_without_losing_searchable_sibling(tmp_path, monkeypatch):
    from docparser.blocks import Block

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        embedding_provider="fake",
        embedding_dimensions=8,
        llm_model="test/mail",
        dedup_enabled=False,
        dev_detection_enabled=False,
        okf_write_bundles=True,
        parser_supervisor_enabled=False,
    )
    for module in (
        "app.config",
        "app.services.pipeline",
        "app.services.staging",
        "app.services.embedder",
        "app.services.llm_client",
        "app.services.okf_generator",
        "app.services.vector_store",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)

    def parse_with_partial_warning(_path, _filename, *, context, **_kwargs):
        child = context.add_child("root", "broken.eml", "mail")
        context.warn(child, "mail_parse_failed")
        return [Block("paragraph", "Доступный срок согласования: 12 дней.")]

    monkeypatch.setattr("app.services.pipeline.parse_document", parse_with_partial_warning)
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    doc_id = "mailpipe00000003"
    source = settings.uploads_dir / f"{doc_id}.eml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"From: sender@example.test\n\nplaceholder")
    registry.create(doc_id, "partial.eml", "message/rfc822", source.stat().st_size)

    pipeline = Pipeline()
    pipeline.okf_generator.generate_chunk = lambda text, *_args, **_kwargs: [
        Concept(id="mail", title="Фрагмент", content=text)
    ]
    for method in ("ensure_collection", "delete_document", "delete_orphaned_points"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: None)
    for method in ("index_concepts", "index_chunks"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: set())

    pipeline._process(doc_id, source, "partial.eml", [], resume=False)

    document = registry.get(doc_id)
    assert document["status"] == "done"
    assert document["problem"] == "attachment_partial_result"
    assert document["parse_warnings"] == [{"code": "mail_parse_failed", "source_id": "root/0"}]

    with session_scope() as session:
        source = session.query(DocumentSource).filter_by(doc_id=doc_id, source_id="root/0").one()
    assert source.warnings == [{"code": "mail_parse_failed", "source_id": "root/0"}]


@pytest.mark.parametrize("with_parent_text", [True, False])
def test_disabled_embedded_mail_keeps_parent_searchable_and_original_downloadable(
    tmp_path, monkeypatch, with_parent_text,
):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        embedding_provider="fake",
        embedding_dimensions=8,
        llm_model="test/mail",
        dedup_enabled=False,
        dev_detection_enabled=False,
        okf_write_bundles=True,
        parser_supervisor_enabled=False,
        mail_import_enabled=False,
    )
    for module in (
        "app.config",
        "app.services.pipeline",
        "app.services.staging",
        "app.services.embedder",
        "app.services.llm_client",
        "app.services.okf_generator",
        "app.services.vector_store",
    ):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)

    doc_id = "mailpipe00000004"
    mail_bytes = b"From: sender@example.test\nSubject: Decision\n\nPRIVATE MAIL BODY"
    pdf = PdfWriter()
    page = pdf.add_blank_page(width=612, height=792)
    if with_parent_text:
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({
                NameObject("/F1"): DictionaryObject({
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }),
            }),
        })
        text_stream = DecodedStreamObject()
        text_stream.set_data(b"BT /F1 12 Tf 72 720 Td (Searchable parent document text) Tj ET")
        page[NameObject("/Contents")] = pdf._add_object(text_stream)
    pdf.add_attachment("decision.eml", mail_bytes)

    source = settings.uploads_dir / f"{doc_id}.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    with source.open("wb") as output:
        pdf.write(output)
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    registry.create(doc_id, "container.pdf", "application/pdf", source.stat().st_size)

    pipeline = Pipeline()
    pipeline.okf_generator.generate_chunk = lambda text, *_args, **_kwargs: [
        Concept(id="parent", title="Parent", content=text)
    ]
    for method in ("ensure_collection", "delete_document", "delete_orphaned_points"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: None)
    for method in ("index_concepts", "index_chunks"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: set())

    pipeline._process(doc_id, source, "container.pdf", [], resume=False)

    document = registry.get(doc_id)
    assert document["status"] == "done"
    assert document["problem"] == ("attachment_partial_result" if with_parent_text else "no_text_layer")
    assert document["parser_version"].endswith("-mail-disabled")
    assert document["parse_warnings"] == [{"code": "mail_import_disabled", "source_id": "root/0"}]
    with session_scope() as session:
        sources = session.query(DocumentSource).filter_by(doc_id=doc_id).order_by(DocumentSource.source_id).all()
        chunks = session.query(DocumentChunk).filter_by(doc_id=doc_id).all()
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).all()

    assert [row.source_id for row in sources] == ["root", "root/0"]
    assert sources[1].extraction_status == "skipped_disabled"
    assert (settings.uploads_dir / doc_id / sources[1].saved_path).read_bytes() == mail_bytes
    if with_parent_text:
        assert chunks and {row.source_id for row in chunks} == {"root"}
        assert concepts and {row.source_id for row in concepts} == {"root"}
        assert any("Searchable parent document text" in row.content for row in chunks)
    else:
        assert concepts == []
        assert {row.source_id for row in chunks} <= {"root"}
    assert all("PRIVATE MAIL BODY" not in row.content for row in chunks)
    assert all("импорт писем отключён" not in row.content for row in chunks)


def test_chunk_backfill_does_not_index_disabled_mail_marker(tmp_path, monkeypatch):
    from docparser.blocks import Block

    settings = Settings(
        _env_file=None,
        data_dir=tmp_path,
        embedding_provider="fake",
        embedding_dimensions=8,
        llm_model="test/mail",
        mail_import_enabled=False,
    )
    for module in ("app.config", "app.services.pipeline", "app.services.vector_store"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)

    doc_id = "mailpipe00000005"
    source = settings.uploads_dir / f"{doc_id}.pdf"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"placeholder")
    registry = DocumentRegistry()
    registry.create(doc_id, "container.pdf", "application/pdf", source.stat().st_size)
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)

    def parse_disabled(_path, _filename, *, context, **_kwargs):
        source_id = context.add_child("root", "decision.eml", "mail")
        context.warn(source_id, "mail_import_disabled")
        return [Block(
            "attachment", "Вложение: decision.eml — импорт писем отключён",
            meta={"source_id": source_id, "extraction_status": "skipped_disabled"},
        )]

    monkeypatch.setattr("app.services.pipeline.parse_document", parse_disabled)
    Pipeline()._backfill_chunks(doc_id)

    with session_scope() as session:
        chunks = session.query(DocumentChunk).filter_by(doc_id=doc_id).all()
        sources = session.query(DocumentSource).filter_by(doc_id=doc_id).all()
    assert chunks == []
    assert {row.source_id for row in sources} == {"root", "root/0"}
