"""Fixed-corpus HTTP load acceptance; real PG/Qdrant, deterministic LLM/embedder.

All application imports stay in main so Windows parser spawn measures its own
startup rather than importing a test server, dotenv or LLM client again.
"""
import argparse
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import statistics
import re
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]


def stats(values):
    ordered = sorted(values)
    assert len(values) >= 2
    return {"count": len(values), "p50": statistics.median(ordered),
            "p95": statistics.quantiles(ordered, n=100, method="inclusive")[94],
            "max": max(ordered)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Directory containing manifest.json and corpus/")
    parser.add_argument("--output", type=Path, required=True, help="Fresh output directory; previous results are preserved")
    parser.add_argument("--name", required=True, help="Fresh mail_load_<run> database and collection")
    args = parser.parse_args()
    if not re.fullmatch(r"mail_load_[a-z0-9_]{1,32}", args.name):
        raise ValueError("Use a unique mail_load_<run> name")
    task = args.output.resolve()
    inputs = args.input.resolve()
    if (task / "load-report.json").exists():
        raise ValueError("Inspect the existing run; do not overwrite evidence")
    task.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / "backend"))
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    import psycopg
    from app import config
    settings = config.Settings(
        _env_file=None, data_dir=task / "data",
        database_url=f"postgresql+psycopg://okf:acceptance-only@127.0.0.1:25432/{args.name}?connect_timeout=5",
        qdrant_url="http://127.0.0.1:26333", qdrant_collection=args.name,
        auth_provider="simulation", auth_role_groups={"KB_Editor": "editor"},
        auth_sim_users=[{"user_id": "load-editor", "username": "load.editor", "groups": ["KB_Editor"]}],
        embedding_provider="fake", embedding_dimensions=8, llm_model="test/mail",
        mail_import_enabled=True, dedup_enabled=True, dev_detection_enabled=False,
        search_graph_expansion_enabled=False, glossary_query_expansion_enabled=False,
        translation_provider="off", search_rate_limit_per_minute=10_000,
        trash_purge_enabled=False, chat_history_purge_enabled=False,
    )
    config.get_settings = lambda: settings
    manifest = json.loads((inputs / "manifest.json").read_text(encoding="utf-8"))
    for item in manifest["entries"] + manifest["controls"]:
        path = inputs / "corpus" / item["name"]
        assert path.stat().st_size == item["size"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
    with psycopg.connect("host=127.0.0.1 port=25432 user=okf password=acceptance-only dbname=postgres connect_timeout=5", autocommit=True) as conn:
        assert not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (args.name,)).fetchone(), "Inspect existing test run, never overwrite it"
        conn.execute(psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(args.name)))
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(ROOT / "backend/alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
    command.upgrade(cfg, "head")
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.db.models import DocumentChunk, DocumentSource, OkfConcept
    from app.db.session import session_scope
    from app.models.schemas import Concept
    from app.services import parser_supervisor as supervisor
    from app.services.llm_client import LLMClient
    from app.services.okf_generator import OKFGenerator
    from app.services.source_evidence import resolve_source_spans
    from app.services.pipeline import get_pipeline
    from app.main import create_app
    import app.api.documents as documents_api
    import app.services.pipeline as pipeline_module
    from docparser import PARSER_VERSION

    def forbidden(*args, **kwargs):
        raise AssertionError("The load run must never contact an LLM provider")
    LLMClient._complete_once = forbidden
    def deterministic(self, chunk, *args, **kwargs):
        slug = "load-" + hashlib.sha256(chunk.encode()).hexdigest()[:12]
        return [Concept(id=slug, title=slug, content=chunk, source_spans=resolve_source_spans(chunk, chunk))]
    OKFGenerator.generate_chunk = deterministic
    observations = []
    memory = []
    actual_rss = supervisor._windows_rss_bytes
    def measured_rss(pid):
        value = actual_rss(pid)
        if value is not None:
            memory.append(value)
        return value
    supervisor._windows_rss_bytes = measured_rss
    actual_parse = supervisor.parse_document_supervised
    def measured_parse(stage):
        def run(path, filename, **kwargs):
            started = time.perf_counter()
            result = actual_parse(path, filename, **kwargs)
            observations.append({"filename": filename, "stage": stage, "seconds": time.perf_counter() - started,
                                 "sources": len(result.sources), "warnings": len(result.warnings)})
            return result
        return run
    documents_api.parse_document_supervised = measured_parse("preview")
    pipeline_module.parse_document_supervised = measured_parse("pipeline")

    report = {"phase": "running", "pid": os.getpid(), "input_bytes": manifest["input_bytes"],
              "manifest_sha256": hashlib.sha256((inputs / "manifest.json").read_bytes()).hexdigest(),
              "parser_version": PARSER_VERSION, "database": args.name, "inputs": str(inputs),
              "scope": "Windows, ASGI HTTP, PostgreSQL17, Qdrant1.19, fake LLM and embeddings",
              "documents": [], "http_errors": [], "checks": {}}
    report_path = task / "load-report.json"
    def save():
        report["parse_observations"] = observations
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    original_hook = sys.excepthook
    def record_failure(kind, value, traceback):
        if report["phase"] == "running":
            report["phase"] = "failed"
        report["failure_type"] = kind.__name__
        save()
        original_hook(kind, value, traceback)
    sys.excepthook = record_failure
    save()
    def require(response, status=200):
        if response.status_code != status:
            report["http_errors"].append({"status": response.status_code, "expected": status, "body": response.text[:300]})
            save()
            raise AssertionError(report["http_errors"][-1])
        return response
    def wait(doc_id):
        # Wait on the actual pipeline future, not a guessed delay or status file.
        result = get_pipeline().wait_for(doc_id, timeout=150)
        assert result["status"] == "done", {"doc_id": doc_id, "status": result["status"], "error": result.get("error")}
        task = get_pipeline()._threads.get(doc_id)
        if task:
            task.result(timeout=10)
    def upload(client, name, tag):
        path = inputs / "corpus" / name
        with path.open("rb") as handle:
            response = require(client.post("/api/documents", data={"tags": tag, "allow_similar": "true"}, files={"file": (name, handle)}))
        doc_id = response.json()["id"]
        wait(doc_id)
        return doc_id

    # Same control hashes with the mail flag off/on isolates non-mail parser
    # overhead in this revision. This is NOT a historical-binary comparison.
    control_results = {False: {".docx": [], ".pdf": []}, True: {".docx": [], ".pdf": []}}
    for enabled in (False, True):
        for repeat in range(2):
            for item in manifest["controls"]:
                path = inputs / "corpus" / item["name"]
                started = time.perf_counter()
                actual_parse(path, path.name, attachments_dir=task / "control-output" / f"{enabled}-{repeat}-{path.stem}-{path.suffix[1:]}",
                             timeout_seconds=120, max_memory_mb=1024, max_concurrent=2, mail_enabled=enabled)
                control_results[enabled][path.suffix].append(time.perf_counter() - started)
    report["controls"] = {str(flag): {ext: stats(values) for ext, values in groups.items()} for flag, groups in control_results.items()}
    save()

    with TestClient(create_app()) as client:
        require(client.post("/api/auth/simulate", json={"username": "load.editor"}))
        control_id = upload(client, "control-000.docx", "load-control")
        query = {"query": "certificate verification", "tags": ["load-control"], "dense": False, "bm25": True, "use_glossary": False}
        def search():
            started = time.perf_counter()
            response = require(client.post("/api/search", json=query)).json()
            elapsed = time.perf_counter() - started
            assert response["hits"] and all(control_id in hit["filepath"].replace("\\", "/").split("/") for hit in response["hits"])
            return elapsed
        for _ in range(10):
            search()
        before = []
        for _ in range(100):
            before.append(search())
            time.sleep(0.2)
        report["search_before"] = stats(before)
        during, errors = [], []
        stop = threading.Event()
        def search_loop():
            try:
                while not stop.is_set():
                    during.append(search())
                    stop.wait(0.2)
            except BaseException as exc:
                errors.append(type(exc).__name__)
        thread = threading.Thread(target=search_loop, name="acceptance-search")
        thread.start()
        began = time.perf_counter()
        try:
            for index, item in enumerate(manifest["entries"]):
                doc_id = upload(client, item["name"], "load-mail")
                with session_scope() as session:
                    sources = session.scalars(select(DocumentSource).where(DocumentSource.doc_id == doc_id)).all()
                    chunks = session.scalars(select(DocumentChunk).where(DocumentChunk.doc_id == doc_id)).all()
                    concepts = session.scalars(select(OkfConcept).where(OkfConcept.doc_id == doc_id)).all()
                    known = {row.source_id for row in sources}
                    assert len(sources) == item["expected_sources"]
                    assert chunks and concepts
                    assert all(row.source_id in known for row in chunks + concepts)
                    assert any(item["required_fact"] in row.content for row in chunks)
                    assert any(item["required_fact"] in row.content for row in concepts)
                    assert all(row.source_id == next(chunk.source_id for chunk in chunks if chunk.chunk_index == row.chunk_index) for row in concepts)
                if item["name"] in manifest["repeat_uploads"]:
                    with (inputs / "corpus" / item["name"]).open("rb") as handle:
                        repeated = require(client.post("/api/documents", data={"allow_similar": "true"}, files={"file": (item["name"], handle)}), 409)
                    assert repeated.json()["code"] == "duplicate"
                report["documents"].append({"name": item["name"], "doc_id": doc_id, "sources": len(sources), "chunks": len(chunks), "concepts": len(concepts)})
                save()
                if (index + 1) % 10 == 0:
                    print(json.dumps({"completed": index + 1, "total": 100, "search_samples": len(during)}), flush=True)
        finally:
            stop.set()
            thread.join(timeout=10)
            assert not thread.is_alive()
            report["elapsed_load_seconds"] = time.perf_counter() - began
            report["search_errors"] = errors
            if len(during) > 1:
                report["search_during"] = stats(during)
            report["sampled_worker_rss_max_mib"] = max(memory, default=0) / 1024**2
            report["rss_samples"] = len(memory)
            save()
        assert not errors
        assert not multiprocessing.active_children(), "orphan multiprocessing workers remain"
        semaphore = supervisor._slots_by_limit[settings.parser_max_concurrent]
        acquired = 0
        try:
            while acquired < settings.parser_max_concurrent and semaphore.acquire(blocking=False):
                acquired += 1
            assert acquired == settings.parser_max_concurrent, "parser slots lost"
        finally:
            for _ in range(acquired):
                semaphore.release()
        ordinary = [item["seconds"] for item in observations if item["filename"] in {row["name"] for row in manifest["entries"] if row["category"] == "ordinary_mail"}]
        report["ordinary_mail_parse"] = stats(ordinary)
        report["search_ratio"] = report["search_during"]["p95"] / report["search_before"]["p95"]
        report["control_ratios"] = {ext: report["controls"]["True"][ext]["p95"] / report["controls"]["False"][ext]["p95"] for ext in (".docx", ".pdf")}
        checks = report["checks"] = {
            "100_inputs": len(report["documents"]) == 100,
            "ordinary_mail_p95_le_5s": report["ordinary_mail_parse"]["p95"] <= 5,
            "observed_rss_le_1024mib": bool(memory) and report["sampled_worker_rss_max_mib"] <= 1024,
            "all_parser_calls_le_120s": all(item["seconds"] <= 120 for item in observations),
            "docx_pdf_p95_regression_le_20_percent": all(value <= 1.2 for value in report["control_ratios"].values()),
            "search_p95_regression_le_20_percent": report["search_ratio"] <= 1.2,
            "no_http_errors": not report["http_errors"], "no_orphan_workers": True, "no_lost_parser_slots": True,
        }
        report["phase"] = "passed" if all(checks.values()) else "failed_gates"
        save()
        print(json.dumps({"phase": report["phase"], "checks": checks, "search_ratio": report["search_ratio"],
                          "ordinary_mail_p95": report["ordinary_mail_parse"]["p95"], "report": str(report_path)}), flush=True)
        assert all(checks.values()), "Do not waive failed gates; inspect the report"


if __name__ == "__main__":
    main()
