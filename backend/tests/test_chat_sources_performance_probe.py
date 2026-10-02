"""The profiler follows real widening and emits no source text or history."""
from copy import deepcopy
import json
from types import SimpleNamespace

from sqlalchemy import func, select

from app.config import Settings
from app.db.models import ChatMessage, ChatSession, Document, DocumentChunk, DocumentSource, OkfConcept
from app.db.session import session_scope
from app.models.schemas import ChatRequest
from app.services.fusion import Hit
from test_scripts.probe_chat_sources_performance import measure, summarize


def test_probe_follows_widening_without_writing_history_or_exporting_source_text(monkeypatch):
    from app import config
    settings = Settings(_env_file=None)
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    hits = []
    with session_scope() as session:
        session.add(Document(id="probe-doc", filename="PRIVATE_FILENAME.docx"))
        session.add(DocumentSource(doc_id="probe-doc", source_id="root", kind="document",
                                   display_name="PRIVATE_FILENAME.docx"))
        for index in range(6):
            text = f"ЭЛН PRIVATE_CONTENT {index}"
            slug = f"part-{index}"
            session.add(OkfConcept(doc_id="probe-doc", slug=slug, title=text, content=text,
                                   source_id="root", chunk_index=index))
            session.add(DocumentChunk(doc_id="probe-doc", chunk_index=index, source_id="root", content=text))
            for kind in ("concept", "chunk"):
                hits.append(Hit(f"{kind}-{index}", 1 / (index + 1), {
                    "point_type": kind, "doc_id": "probe-doc", "slug": slug if kind == "concept" else None,
                    "title": text, "chunk_index": index}))

    def search(**kwargs):
        kwargs["retrieval_status"]["limit_reached"] = len(hits) >= kwargs["top_k"]
        return deepcopy(hits[:kwargs["top_k"]])

    def no_embedding(_query):
        raise AssertionError("BM25 probe used embedding")

    result = measure(ChatRequest(query="ЭЛН", response_mode="documents", search_depth=4,
                                 mode="bm25", use_glossary=False), settings,
                     SimpleNamespace(search_composite=search), SimpleNamespace(embed=no_embedding))

    assert [round_["candidate_depth"] for round_ in result["rounds"]] == [4, 8]
    assert result["counts"]["sources"] == 4
    assert result["counts"]["source_tree_selects"] == 2
    assert result["snapshot_digests"]
    assert sum(query["count"] for query in result["sql_queries"]) == result["counts"]["sql_count"]
    assert result["rounds"][1]["repeated_candidates"] == 4
    assert "PRIVATE" not in json.dumps(result)
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(ChatSession)) == 0
        assert session.scalar(select(func.count()).select_from(ChatMessage)) == 0


def test_short_samples_do_not_claim_p95():
    assert "p95" not in summarize([1, 2, 3, 4, 5])
    assert summarize(list(range(1, 31)))["p95"] == 29


def test_strict_report_preserves_failed_evidence_and_returns_failure(tmp_path):
    from test_scripts.probe_chat_sources_performance import finish_report
    manifest = {"cases": [{"query": "IT3273", "stable_sources": True,
                           "output_equivalent": False,
                           "samples": [{"reference_counts": {"expected": 2, "found": 1}}]}]}
    output = tmp_path / "failed.json"
    assert finish_report(manifest, output, strict=True) == 1
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["acceptance"]["passed"] is False
    assert {failure["criterion"] for failure in saved["acceptance"]["failures"]} == {
        "output_equivalence", "mandatory_sources"}


def test_strict_report_passes_complete_stable_outputs(tmp_path):
    from test_scripts.probe_chat_sources_performance import finish_report
    manifest = {"cases": [{"query": "IT3273", "stable_sources": True,
                           "output_equivalent": True,
                           "samples": [{"reference_counts": {"expected": 2, "found": 2}}]}]}
    assert finish_report(manifest, tmp_path / "passed.json", strict=True) == 0


def test_changed_corpus_preserves_samples_and_fails_even_without_strict(tmp_path):
    from test_scripts.probe_chat_sources_performance import finish_report
    manifest = {"corpus_unchanged": False, "cases": [{"query": "IT3273", "stable_sources": True,
                 "samples": [{"reference_counts": {"expected": 2, "found": 2}}]}]}
    output = tmp_path / "changed.json"
    assert finish_report(manifest, output) == 1
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["cases"][0]["samples"]
    assert saved["acceptance"]["failures"][0]["criterion"] == "corpus_unchanged"
