"""A04: private transport headers stay out of derived data and application logs."""
import importlib.util
import io
import json
import logging
import struct
from types import SimpleNamespace
from email.message import EmailMessage
from pathlib import Path

import olefile
import pytest

from app.config import Settings
from app.db.models import DocumentChunk, DocumentSource, OkfConcept
from app.db.session import session_scope
from app.services.context_builder import format_context, merge_and_format
from app.services.errors import VectorStoreError
from app.services.fusion import Hit
from app.services.generation_files import active_bundle_path
from app.services.pipeline import Pipeline
from app.services.registry import DocumentRegistry
from app.services.retrieval_hydration import load_visible_retrieval_hits
from tests.test_authz import login, make_client


PRIVATE = ("bcc-root-61@example.test", "bcc-child-72@example.test",
           "transport-root-83", "transport-child-94")
BODY = "PUBLIC_BODY_FACT: Approval takes 18 days."
CANONICAL_BODY = r"PUBLIC\_BODY\_FACT: Approval takes 18 days."


def _eml(*, child=False):
    message = EmailMessage()
    message["From"] = "sender@example.test"
    message["To"] = "recipient@example.test"
    message["Subject"] = "Public decision"
    message["Bcc"] = PRIVATE[1 if child else 0]
    message["Received"] = PRIVATE[3 if child else 2]
    message["X-Private-Transport"] = PRIVATE[3 if child else 2]
    message.set_content(BODY)
    return message


def _root_bytes(format_name, partial, *, malformed=False):
    child = _eml(child=True)
    unsupported = b"\x78\x9f\x3e\x22" + bytes(32)
    if format_name == "eml":
        message = _eml()
        message.add_attachment(child, filename="decision.eml")
        if malformed:
            message.add_attachment(_malformed_msg(), maintype="application", subtype="vnd.ms-outlook", filename="broken.msg")
        if partial:
            message.add_attachment(unsupported, maintype="application", subtype="ms-tnef", filename="winmail.dat")
        return message.as_bytes()
    spec = importlib.util.spec_from_file_location(
        "privacy_msg_fixture", Path(__file__).parents[2] / "doc-parser/tests/mail_fixtures.py",
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    attachments = {"decision.eml": child.as_bytes()}
    if malformed:
        attachments["broken.msg"] = _malformed_msg()
    if partial:
        attachments["winmail.dat"] = unsupported
    payload = fixture.unicode_msg(subject="Public decision", body=BODY, attachments=attachments)
    with olefile.OleFileIO(io.BytesIO(payload)) as compound:
        streams = {"/".join(path): compound.openstream(path).read() for path in compound.listdir()}
    headers = f"From: sender@example.test\r\nBcc: {PRIVATE[0]}\r\nReceived: {PRIVATE[2]}\r\n"
    streams["__substg1.0_007D001F"] = (headers + "\0").encode("utf-16-le")
    recipient = "__recip_version1.0_#00000000/"
    streams[recipient + "__properties_version1.0"] = bytes(8) + struct.pack("<IIQ", 0x0C150003, 6, 3)
    streams[recipient + "__substg1.0_39FE001F"] = (PRIVATE[0] + "\0").encode("utf-16-le")
    streams["__properties_version1.0"] = struct.pack("<8sIIII8s", b"", 1, len(attachments), 1, len(attachments), b"")
    return fixture.compound_bytes(streams)


def _malformed_msg():
    spec = importlib.util.spec_from_file_location(
        "privacy_broken_msg_fixture", Path(__file__).parents[2] / "doc-parser/tests/mail_fixtures.py",
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    headers = f"Bcc: {PRIVATE[0]}\r\nReceived: {PRIVATE[2]}\r\n"
    # A real CFB identified as MSG, with a malformed fixed MAPI record tail.
    return fixture.compound_bytes({"__properties_version1.0": bytes(33),
                                   "__substg1.0_007D001F": (headers + "\0").encode("utf-16-le")})


@pytest.mark.parametrize("format_name", ["eml", "msg"])
@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_private_headers_excluded_from_llm_index_context_and_logs(tmp_path, monkeypatch, caplog, capfd, format_name, partial, resume):
    settings = Settings(
        _env_file=None, data_dir=tmp_path, embedding_provider="fake", embedding_dimensions=8,
        llm_model="test/mail", dedup_enabled=False, dev_detection_enabled=False,
        okf_write_bundles=True, mail_import_enabled=True,
    )
    for module in ("app.config", "app.services.pipeline", "app.services.staging", "app.services.embedder",
                   "app.services.llm_client", "app.services.okf_generator", "app.services.vector_store",
                   "app.services.field_table"):
        monkeypatch.setattr(f"{module}.get_settings", lambda: settings)
    caplog.set_level(logging.INFO)
    registry = DocumentRegistry()
    monkeypatch.setattr("app.services.pipeline.get_registry", lambda: registry)
    pipeline = Pipeline()
    llm_inputs, points = [], []

    def complete(system, user, **_kwargs):
        llm_inputs.append(system + "\n" + user)
        return json.dumps([{"id": f"public-{len(llm_inputs)}", "title": "Public decision",
                            "content": BODY, "source_quotes": [BODY]}]), "stop"

    def upsert(batch, **_kwargs):
        points.extend(batch)
        return {"failed_batches": 0}

    def verify(doc_id, generation_id, expected_ids):
        assert set(expected_ids) == {str(point.id) for point in points}
        assert all(point.payload["doc_id"] == doc_id and point.payload["generation_id"] == generation_id
                   for point in points)

    # Capture the actual provider boundary after prompt construction and JSON
    # handling; no external model sees these synthetic headers.
    monkeypatch.setattr(pipeline.okf_generator.llm, "_complete_once", complete)
    monkeypatch.setattr(pipeline.vector_store, "_upsert_batches", upsert)
    monkeypatch.setattr(pipeline.vector_store, "verify_generation_points", verify)
    for method in ("ensure_collection", "delete_document", "delete_orphaned_points", "update_generation_metadata"):
        monkeypatch.setattr(pipeline.vector_store, method, lambda *_args, **_kwargs: None)
    doc_id = "privacy000000001"
    source = settings.uploads_dir / f"{doc_id}.{format_name}"
    source.parent.mkdir(parents=True, exist_ok=True)
    original = _root_bytes(format_name, partial)
    if format_name == "msg":
        from docparser.embedded import AttachmentBudget
        from docparser.msg_parser import _read_scoped_message

        with olefile.OleFileIO(io.BytesIO(original)) as compound:
            recipients = _read_scoped_message(compound, (), budget=AttachmentBudget())["recipients"]
        assert len(recipients) == 1
        recipient = next(iter(recipients.values()))
        assert recipient["RecipientType"] == 3 and recipient["SmtpAddress"] == PRIVATE[0]
    source.write_bytes(original)
    registry.create(doc_id, source.name, "application/octet-stream", len(original))
    if resume:
        finalize = pipeline._finalize

        def unavailable(*_args, **_kwargs):
            raise VectorStoreError("Synthetic publication unavailable")

        monkeypatch.setattr(pipeline, "_finalize", unavailable)
        pipeline._process(doc_id, source, source.name, [], resume=False)
        assert registry.get(doc_id)["status"] == "paused"
        monkeypatch.setattr(pipeline, "_finalize", finalize)
    pipeline._process(doc_id, source, source.name, [], resume=resume)
    document = registry.get(doc_id)
    assert document["status"] == "done"
    assert document["problem"] is None
    assert len(llm_inputs) == 2 and all(CANONICAL_BODY in text for text in llm_inputs)
    assert {point.payload["point_type"] for point in points} == {"concept", "chunk"}
    with session_scope() as session:
        sources = session.query(DocumentSource).filter_by(doc_id=doc_id).all()
        chunks = session.query(DocumentChunk).filter_by(doc_id=doc_id).all()
        concepts = session.query(OkfConcept).filter_by(doc_id=doc_id).all()
        derived = json.dumps({
            "sources": [row.metadata_json for row in sources],
            "chunks": [row.content for row in chunks], "concepts": [row.content for row in concepts],
        })
    hits, lookup = load_visible_retrieval_hits([Hit(str(point.id), 1, point.payload) for point in points])
    context = format_context(merge_and_format(hits, settings, filename_lookup={
        key: value["filename"] for key, value in lookup.items()
    }))
    assert CANONICAL_BODY in context and "sender@example.test" in context
    # Exercise the registered HTTP chat route, its prompt construction, error
    # response and persisted history. Only the external provider/search I/O is
    # replaced; SQL hydration and all source-path filtering remain real.
    from app.api import chat as chat_api
    from app.db.models import ChatMessage
    from app.services.llm_client import LLMClient

    chat_inputs = []
    interactive = LLMClient(interactive=True)
    fail_provider = False

    def chat_completion(system, user, **_kwargs):
        chat_inputs.append(system + "\n" + user)
        if fail_provider:
            raise ConnectionError("Synthetic provider connection unavailable")
        return "Approval takes 18 days [1].", "stop"

    monkeypatch.setattr(interactive, "_complete_once", chat_completion)
    monkeypatch.setattr(chat_api, "_get_llm", lambda: interactive)
    monkeypatch.setattr(chat_api, "get_settings", lambda: settings)
    monkeypatch.setattr(chat_api, "_get_vector_store", lambda: SimpleNamespace(
        search_composite=lambda **_kwargs: [Hit(str(point.id), 1, point.payload) for point in points],
    ))
    client = make_client(tmp_path, monkeypatch)
    try:
        login(client, "demo.user")
        request = {"query": "Approval days", "dense": False, "bm25": True, "use_glossary": False}
        response = client.post("/api/chat", json=request)
        assert response.status_code == 200, response.text
        assert response.json()["answer"] == "Approval takes 18 days [1]."
        assert response.json()["sources"]
        fail_provider = True
        error_response = client.post("/api/chat", json=request)
        assert error_response.status_code == 503, error_response.text
        assert error_response.json()["code"] == "dependency_unavailable"
        assert chat_inputs and all(CANONICAL_BODY in text for text in chat_inputs)
        with session_scope() as session:
            history = json.dumps([{"role": row.role, "content": row.content,
                                   "sources": row.sources} for row in session.query(ChatMessage).all()])
        assert "18 days" in history
    finally:
        client.close()
    bundle = active_bundle_path(settings, doc_id)
    generated = "\n".join(path.read_text(encoding="utf-8") for path in bundle.rglob("*")
                          if path.is_file() and path.suffix in {".md", ".json"})
    worker_output = capfd.readouterr()
    outputs = "\n".join([*llm_inputs, *chat_inputs, response.text, error_response.text, history,
                         derived, context, generated, worker_output.out, worker_output.err,
                         json.dumps([point.model_dump(mode="json") for point in points]), caplog.text])
    assert "PUBLIC_BODY_FACT" not in caplog.text
    assert any(record.name == "app.services.pipeline" for record in caplog.records)
    for private in PRIVATE:
        assert private not in outputs
    assert source.read_bytes() == original  # Raw originals intentionally retain their headers.
    child = next(row for row in sources if row.source_id == "root/0")
    assert PRIVATE[1].encode() in (settings.uploads_dir / doc_id / child.saved_path).read_bytes()


@pytest.mark.parametrize("format_name", ["eml", "msg"])
def test_malformed_child_private_headers_do_not_reach_worker_output(tmp_path, capfd, format_name):
    from app.services.parser_supervisor import parse_document_supervised

    source = tmp_path / f"private.{format_name}"
    original = _root_bytes(format_name, False, malformed=True)
    source.write_bytes(original)
    result = parse_document_supervised(source, source.name, attachments_dir=tmp_path / "children",
                                       timeout_seconds=20, max_memory_mb=1024)
    assert any(warning["code"] == "mail_parse_failed" for warning in result.warnings)
    assert sum(CANONICAL_BODY in block.text for block in result.blocks) == 2  # Root and intact sibling.
    captured = capfd.readouterr()
    outputs = json.dumps({"sources": [node.__dict__ for node in result.sources],
                          "blocks": [block.__dict__ for block in result.blocks],
                          "warnings": result.warnings}) + captured.out + captured.err
    for private in PRIVATE:
        assert private not in outputs
    assert source.read_bytes() == original


@pytest.mark.parametrize("failure", ["malformed", "deadline"])
def test_worker_failure_does_not_echo_private_headers(tmp_path, capfd, failure):
    import multiprocessing
    from app.services.parser_supervisor import ParserTimeoutError, ParserWorkerError, parse_document_supervised

    before = {child.pid for child in multiprocessing.active_children()}
    source = tmp_path / ("private.msg" if failure == "malformed" else "private.eml")
    original = _malformed_msg() if failure == "malformed" else _eml().as_bytes()
    source.write_bytes(original)
    artifacts = tmp_path / "children"
    expected = ParserWorkerError if failure == "malformed" else ParserTimeoutError
    with pytest.raises(expected) as caught:
        parse_document_supervised(source, source.name, attachments_dir=artifacts,
                                  timeout_seconds=20 if failure == "malformed" else 0.01, max_memory_mb=1024)
    if failure == "malformed":
        assert str(caught.value) == "parser worker failed (ValueError)"
    captured = capfd.readouterr()
    for private in PRIVATE:
        assert private not in str(caught.value) + captured.out + captured.err
    assert not artifacts.exists()
    assert source.read_bytes() == original
    assert {child.pid for child in multiprocessing.active_children()} <= before
