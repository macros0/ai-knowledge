"""Acceptance must not mistake ranked top-N omission for a lost document."""
from hashlib import sha256
from io import BytesIO
import json

from docx import Document
import httpx
import pytest

from test_scripts import replica_runtime_faults as tool


def corpus(size=42, *, legacy=False):
    sources, receipts = {}, []
    for index in range(size):
        doc = Document()
        marker = f"{index + 1:032x}"
        doc.add_heading(f"Legacy recovery fixture {index}" if legacy else f"Replica {marker}", 0)
        doc.add_paragraph("ТЕСТОВЫЙ концепт. A synthetic source for search preservation.")
        stream = BytesIO(); doc.save(stream)
        doc_id = f"{index + 1:016x}"
        sources[doc_id] = stream.getvalue()
        receipts.append({"id": doc_id, "sha256": sha256(stream.getvalue()).hexdigest()})
    return sources, receipts


def client_for(sources, *, omit=None):
    calls = []
    def handle(request):
        if request.method == "GET":
            doc_id = request.url.path.split("/")[-2]
            return httpx.Response(200, content=sources[doc_id]) if doc_id in sources else httpx.Response(404)
        value = json.loads(request.content); calls.append(value)
        query = value['query']
        if query == "ТЕСТОВЫЙ концепт":
            ids = list(sources)[:30]
        elif query.startswith("Legacy recovery fixture "):
            ids = [f"{int(query.rsplit(' ', 1)[1]) + 1:016x}"]
        else:
            ids = [f"{int(query, 16):016x}"]
        return httpx.Response(200, json={"hits": [{"doc_id": key} for key in ids if key in sources and key != omit]})
    return httpx.Client(base_url="http://acceptance.invalid", transport=httpx.MockTransport(handle)), calls


def test_every_preserved_document_is_searched_beyond_the_shared_candidate_limit():
    sources, docs = corpus()
    with client_for(sources)[0] as client:
        assert len(tool.search_ids(client)) == 30
        tool.verify_corpus_search_download(client, docs)


def test_markerless_legacy_fixture_uses_its_verified_source_heading():
    sources, docs = corpus(2, legacy=True)
    client, calls = client_for(sources)
    with client: tool.verify_corpus_search_download(client, docs)
    assert {value['query'] for value in calls} >= {"Legacy recovery fixture 0", "Legacy recovery fixture 1"}


@pytest.mark.parametrize("fault", ["missing_index", "changed_source", "trash_download", "trash_search"])
def test_corpus_evidence_refuses_actual_missing_or_exposed_documents(fault):
    sources, docs = corpus(2)
    client, _ = client_for(sources, omit=docs[1]['id'] if fault == "missing_index" else None)
    if fault == "changed_source": docs[1]['sha256'] = "0" * 64
    if fault.startswith('trash_'):
        docs[1]['deleted_at'] = '2026-11-02T00:00:00Z'
        if fault == 'trash_download': client, _ = client_for(sources, omit=docs[1]['id'])
    with client, pytest.raises(ValueError): tool.verify_corpus_search_download(client, docs)


def test_trashed_document_remains_unsearchable_and_undownloadable():
    sources, docs = corpus(2)
    docs[1]['deleted_at'] = '2026-11-02T00:00:00Z'
    del sources[docs[1]['id']]
    with client_for(sources)[0] as client: tool.verify_corpus_search_download(client, docs)


def test_unsafe_or_unqueryable_source_never_falls_back_to_a_shared_search():
    for body in (b'not a DOCX',):
        sources = {'1' * 16: body}
        docs = [{'id':'1' * 16,'sha256':sha256(body).hexdigest()}]
        with client_for(sources)[0] as client, pytest.raises(ValueError):
            tool.verify_corpus_search_download(client, docs)
