"""M10 acceptance on synthetic DOCX -> pinned MSG -> XLSX, real configured LLM.

Uses only the isolated acceptance services on ports 25432/26333. Creates a new
database/collection, never overwrites an existing run. No user uploads are read.
Embeddings are fake/8D; search qualification is explicitly BM25 only.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def make_fixture(destination: Path) -> tuple[bytes, bytes, bytes]:
    import olefile

    module_spec = importlib.util.spec_from_file_location("mail_chain_fixtures", ROOT / "doc-parser/tests/fixtures.py")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    msg = (ROOT / "doc-parser/tests/fixtures/mail/synthetic-unicode-attachment.msg").read_bytes()
    assert hashlib.sha256(msg).hexdigest() == "2fa5cdebfe6526c943c788e2613c02e24fc855fae072a4fcfd50ed8dbc044229"
    with olefile.OleFileIO(io.BytesIO(msg)) as ole:
        xlsx = ole.openstream("__attach_version1.0_#00000000/__substg1.0_37010102").read()
    module.make_docx_with_embedded_xlsx(destination, msg, prog_id="Outlook.File.msg.15", filename="approval.msg")
    return destination.read_bytes(), msg, xlsx


def run(output: Path, name: str) -> None:
    if not re.fullmatch(r"mail_chain_[a-z0-9_]{1,32}", name):
        raise ValueError("Use a unique mail_chain_<run> database/collection name")
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    if report_path.exists():
        raise ValueError("Run already exists: inspect its report; never overwrite evidence")
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    from dotenv import dotenv_values
    from app import config

    env = dotenv_values(ROOT / ".env")
    provider = {key: env[name] for key, name in (
        ("llm_model", "LLM_MODEL"), ("llm_chat_model", "LLM_CHAT_MODEL"),
        ("llm_base_url", "LLM_BASE_URL"), ("llm_api_key", "LLM_API_KEY"),
    ) if env.get(name)}
    del env
    settings = config.Settings(
        _env_file=None, data_dir=output / "data",
        database_url=f"postgresql+psycopg://okf:acceptance-only@127.0.0.1:25432/{name}?connect_timeout=5",
        qdrant_url="http://127.0.0.1:26333", qdrant_collection=name,
        embedding_provider="fake", embedding_dimensions=8,
        auth_provider="simulation", auth_default_role="viewer", auth_role_groups={"KB_Editor": "editor"},
        auth_sim_users=[{"user_id": "synthetic-editor", "username": "synthetic.editor",
                         "email": "editor@example.test", "groups": ["KB_Editor"]}],
        mail_import_enabled=True, parser_supervisor_enabled=True,
        dedup_enabled=True, dev_detection_enabled=False, translation_provider="off",
        search_graph_expansion_enabled=False, glossary_query_expansion_enabled=False, **provider,
    )
    del provider
    config.get_settings = lambda: settings

    import psycopg
    from psycopg import sql
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.main import create_app
    from app.db.models import DocumentChunk, OkfConcept
    from app.db.session import session_scope
    from app.services.pipeline import get_pipeline
    from docparser import PARSER_VERSION

    report = {"phase": "started", "pid": os.getpid(), "database": name,
              "parser_version": PARSER_VERSION, "embedding": "fake/8D", "search": "BM25",
              "transport": "ASGI TestClient", "auth": "simulation", "checks": {}}

    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def require(response):
        assert response.status_code == 200, (response.status_code, response.text[:600])
        return response

    save()
    try:
        with psycopg.connect("host=127.0.0.1 port=25432 user=okf password=acceptance-only dbname=postgres connect_timeout=5", autocommit=True) as conn:
            assert not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone(), "Database already exists"
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        cfg = Config(str(ROOT / "backend/alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
        command.upgrade(cfg, "head")
        docx, msg, xlsx = make_fixture(output / "synthetic-chain.docx")
        expected = {"root": docx, "root/0": msg, "root/0/0": xlsx}
        report["input_hashes"] = {key: hashlib.sha256(value).hexdigest() for key, value in expected.items()}
        with TestClient(create_app()) as client:
            require(client.post("/api/auth/simulate", json={"username": "synthetic.editor"}))
            document = require(client.post("/api/documents", files={"file": ("synthetic-chain.docx", docx)})).json()
            doc_id = report["doc_id"] = document["id"]
            save()
            deadline = time.monotonic() + 900
            while time.monotonic() < deadline:
                document = require(client.get(f"/api/documents/{doc_id}")).json()
                if document["status"] in {"done", "failed", "paused", "error"}:
                    future = get_pipeline()._threads.get(doc_id)
                    if future:
                        future.result(timeout=60)
                    break
                time.sleep(0.3)
            assert document["status"] == "done", {key: document.get(key) for key in ("status", "problem", "error")}
            assert document.get("problem") is None, document.get("problem")
            tree = require(client.get(f"/api/documents/{doc_id}/sources")).json()["sources"]
            report["tree"] = tree
            assert {(node["source_id"], node["parent_source_id"]) for node in tree} == {
                ("root", None), ("root/0", "root"), ("root/0/0", "root/0"),
            }
            assert all(node["parser_version"] == PARSER_VERSION and not node["warnings"] for node in tree)
            for node in tree:
                response = require(client.get(f"/api/documents/{doc_id}/sources/download", params={"source_id": node["source_id"]}))
                assert response.content == expected[node["source_id"]]
            with session_scope() as session:
                chunks = session.scalars(select(DocumentChunk).where(DocumentChunk.doc_id == doc_id)).all()
                concepts = session.scalars(select(OkfConcept).where(OkfConcept.doc_id == doc_id)).all()
                assert {row.source_id for row in chunks} == set(expected)
                by_index = {row.chunk_index: row for row in chunks}
                assert all(row.source_id == by_index[row.chunk_index].source_id for row in concepts)
                spreadsheet = [row for row in concepts if row.source_id == "root/0/0"]
                assert spreadsheet and any("3509" in row.content and "12" in row.content for row in spreadsheet)
                report["spreadsheet_locations"] = {}
                for concept in spreadsheet:
                    location = require(client.get(f"/api/documents/{doc_id}/concepts/{concept.slug}/source-location")).json()
                    assert location["source_id"] == "root/0/0"
                    assert location["status"] == "exact" and location["spans"]
                    text = by_index[concept.chunk_index].content
                    assert all(text[span["start"]:span["end"]][:240] == span["quote"] for span in location["spans"])
                    report["spreadsheet_locations"][concept.slug] = location
            report["checks"].update(source_chain=True, original_downloads=True, canonical_spans=True)
            save()
            query = {"query": "Какой срок указан в таблице для кода 3509?", "dense": False, "bm25": True, "use_glossary": False}
            found = require(client.post("/api/search", json=query)).json()
            assert any(hit["source_id"] == "root/0/0" and "12" in hit["snippet"] for hit in found["hits"]), found
            report["search_result"] = found
            answer = require(client.post("/api/chat", json=query)).json()
            report["answer"] = answer
            save()
            assert re.search(r"\b12\b|двенадцат", answer["answer"], re.IGNORECASE), answer["answer"]
            citations = {int(value) for value in re.findall(r"\[(\d+)\]", answer["answer"])}
            assert citations and all(1 <= index <= len(answer["sources"]) for index in citations)
            assert any(answer["sources"][index - 1]["source_id"] == "root/0/0" for index in citations)
            assert all(source["doc_id"] == doc_id for source in answer["sources"])
            export = require(client.post(f"/api/documents/{doc_id}/export-okf"))
            with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
                manifest = json.loads(archive.read("sources.json"))["sources"]
                assert {node["source_id"] for node in manifest} == set(expected)
                for node in manifest:
                    if node["source_id"] != "root":
                        assert archive.read(node["saved_path"]) == expected[node["source_id"]]
            report["checks"].update(search_value=True, cited_spreadsheet_answer=True, export_chain=True)
        report["phase"] = "passed"
    except BaseException as exc:
        report["phase"] = "failed"
        report["failure_type"] = type(exc).__name__
        raise
    finally:
        save()
    print(json.dumps({"phase": report["phase"], "checks": report["checks"], "report": str(report_path)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    arguments = parser.parse_args()
    run(arguments.output.resolve(), arguments.name)
