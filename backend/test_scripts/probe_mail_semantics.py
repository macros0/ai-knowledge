"""Synthetic S03/S04/S06/S07 acceptance with real configured LLM, isolated storage.

Produces answers and exact chat context for human review. Structural checks are
automatic; passing them alone does not certify semantic correctness or dense RAG.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from email.message import EmailMessage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


def mail(subject, body, sender, date="Fri, 25 Sep 2026 10:30:00 +0300"):
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["Date"] = date
    message.set_content(body)
    return message


def cases():
    from openpyxl import Workbook

    proposal = mail("Кварц-41: согласование", "Согласовано.\n\n> Борис: Предлагаю для проекта Кварц-41 установить срок проверки 18 дней.", "Анна <anna@example.test>")
    decisions = mail("Орбита-42: два решения", "Пересылаю два письма по проекту Орбита-42.", "Пересылка <relay@example.test>")
    decisions.add_attachment(mail("Орбита-42: решение", "Для проекта Орбита-42 продление лицензии согласовано.", "Анна <anna@example.test>", "Thu, 24 Sep 2026 09:00:00 +0300"), filename="approved.eml")
    decisions.add_attachment(mail("Орбита-42: решение", "Для проекта Орбита-42 продление лицензии не согласовано.", "Борис <boris@example.test>", "Fri, 25 Sep 2026 11:00:00 +0300"), filename="declined.eml")
    inline = mail("Лира-43: переписка", "Пересылаю переписку.\n\n> Павел: Для Лира-43 срок проверки 20 дней?\n\nТребуется 22 дня.\n\n> Марина: Для Лира-43 нужен бумажный акт?\n\nДостаточно электронного акта.", "Пересылка <relay@example.test>")
    forwarded = mail("Вега-44: таблица", "Пересылаю полученную таблицу по проекту Вега-44.", "Пётр <petr@example.test>")
    workbook = Workbook()
    workbook.properties.creator = None
    workbook.active.append(["Проект", "Срок проверки"])
    workbook.active.append(["Вега-44", "14 дней"])
    output = io.BytesIO()
    workbook.save(output)
    forwarded.add_attachment(output.getvalue(), maintype="application", subtype="octet-stream", filename="vega.xlsx")
    return [
        ("s03", proposal, "Что согласовано по проекту Кварц-41 и какой срок проверки?", "Связать Согласовано с предложением о сроке 18 дней, не потерять предмет."),
        ("s04", decisions, "Какие решения о продлении лицензии Орбита-42 содержат письма, кто и когда их отправил? Отменено ли первое согласование?", "Оба противоположных решения, Анна 24 сентября и Борис 25 сентября; автоматическую отмену не утверждать."),
        ("s06", inline, "Какой срок и какой акт требуются для Лира-43? Кто автор уточнений между цитатами?", "22 дня, электронный акт; автор не указан, не приписать relay, Павлу или Марине."),
        ("s07", forwarded, "Какой срок проверки указан в таблице Вега-44 и кто автор этой таблицы?", "14 дней; автор таблицы неизвестен, Пётр только переслал письмо."),
    ]


def run(output: Path, name: str, baseline: Path | None = None, chat_model: str | None = None,
        generation_model: str | None = None):
    if not re.fullmatch(r"mail_semantics_[a-z0-9_]{1,24}", name):
        raise ValueError("Use a unique mail_semantics_<run> database name")
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.json"
    if report_path.exists():
        raise ValueError("Existing evidence must not be overwritten")
    previous = json.loads(baseline.read_text(encoding="utf-8")) if baseline else None
    if previous and (previous["database"] != name or previous["phase"] != "awaiting_semantic_review"):
        raise ValueError("Recheck requires a completed baseline from this isolated database")
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    from dotenv import dotenv_values
    from app import config

    env = dotenv_values(ROOT / ".env")
    provider = {key: env[source] for key, source in (
        ("llm_model", "LLM_MODEL"), ("llm_chat_model", "LLM_CHAT_MODEL"),
        ("llm_base_url", "LLM_BASE_URL"), ("llm_api_key", "LLM_API_KEY"),
    ) if env.get(source)}
    if chat_model:
        provider["llm_chat_model"] = chat_model
    if generation_model:
        provider["llm_model"] = generation_model
    settings = config.Settings(
        _env_file=None, data_dir=(baseline.parent if baseline else output) / "data",
        database_url=f"postgresql+psycopg://okf:acceptance-only@127.0.0.1:25432/{name}?connect_timeout=5",
        qdrant_url="http://127.0.0.1:26333", qdrant_collection=name,
        embedding_provider="fake", embedding_dimensions=8, mail_import_enabled=True,
        parser_supervisor_enabled=True, dev_detection_enabled=False, dedup_enabled=False,
        search_graph_expansion_enabled=False, glossary_query_expansion_enabled=False,
        translation_provider="off", auth_provider="simulation", auth_default_role="viewer",
        auth_role_groups={"KB_Editor": "editor"},
        auth_sim_users=[{"user_id": "synthetic-editor", "username": "synthetic.editor",
                         "email": "editor@example.test", "groups": ["KB_Editor"]}], **provider,
    )
    del env, provider
    config.get_settings = lambda: settings
    import psycopg
    from psycopg import sql
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    from app.main import create_app
    from app.db.models import DocumentChunk, DocumentSource, OkfConcept
    from app.db.session import session_scope
    from app.services.pipeline import get_pipeline
    from app.api import chat as chat_api
    from docparser import PARSER_VERSION

    report = {"phase": "started", "pid": os.getpid(), "database": name,
              "parser_version": PARSER_VERSION, "embedding": "fake/8D", "search": "BM25",
              "chat_model": settings.llm_chat_model or settings.llm_model,
              "generation_model": settings.llm_model,
              "auth": "simulation", "cases": {}}

    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def require(response):
        assert response.status_code == 200, (response.status_code, response.text[:400])
        return response.json()

    save()
    try:
        if not previous:
            with psycopg.connect("host=127.0.0.1 port=25432 user=okf password=acceptance-only dbname=postgres connect_timeout=5", autocommit=True) as connection:
                assert not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone()
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
            cfg = Config(str(ROOT / "backend/alembic.ini"))
            cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
            command.upgrade(cfg, "head")
        else:
            report["baseline"] = str(baseline)
        with TestClient(create_app()) as client:
            require(client.post("/api/auth/simulate", json={"username": "synthetic.editor"}))
            llm = chat_api._get_llm()
            actual_chat = llm.chat
            prompts = []

            def record_chat(system, user, **kwargs):
                prompts.append({"system": system, "user": user})
                return actual_chat(system, user, **kwargs)

            llm.chat = record_chat
            for key, message, query, expected in cases():
                raw = message.as_bytes()
                filename = getattr(message, "filename", f"{key}.eml")
                (output / filename).write_bytes(raw)
                tag = f"semantic-{key}"
                if previous:
                    doc_id = previous["cases"][key]["doc_id"]
                else:
                    doc = require(client.post("/api/documents", data={"tags": [tag]}, files={"file": (filename, raw)}))
                    doc_id = doc["id"]
                item = report["cases"][key] = {"doc_id": doc_id, "query": query, "acceptance": expected}
                save()
                deadline = time.monotonic() + 600
                while time.monotonic() < deadline:
                    doc = require(client.get(f"/api/documents/{doc_id}"))
                    if doc["status"] in {"done", "failed", "paused", "error"}:
                        future = get_pipeline()._threads.get(doc_id)
                        if future:
                            future.result(timeout=60)
                        break
                    time.sleep(0.3)
                item["document"] = {field: doc.get(field) for field in ("status", "problem", "error_code", "okf_concept_count")}
                assert doc["status"] == "done", item["document"]
                with session_scope() as session:
                    item["sources"] = [{"id": row.source_id, "parent": row.parent_source_id, "metadata": row.metadata_json}
                                       for row in session.scalars(select(DocumentSource).where(DocumentSource.doc_id == doc_id))]
                    item["chunks"] = [{"index": row.chunk_index, "source_id": row.source_id, "content": row.content}
                                      for row in session.scalars(select(DocumentChunk).where(DocumentChunk.doc_id == doc_id))]
                    item["concepts"] = [{"slug": row.slug, "source_id": row.source_id, "content": row.content}
                                        for row in session.scalars(select(OkfConcept).where(OkfConcept.doc_id == doc_id))]
                request = {"query": query, "tags": [tag], "dense": False, "bm25": True, "use_glossary": False, "top_k": 12}
                item["search"] = require(client.post("/api/search", json=request))
                before = len(prompts)
                item["chat"] = require(client.post("/api/chat", json=request))
                item["prompts"] = prompts[before:]
                sources = item["chat"]["sources"]
                citations = {int(index) for index in re.findall(r"\[(\d+)\]", item["chat"]["answer"])}
                item["structural_checks"] = {
                    "sources_same_document": bool(sources) and all(source["doc_id"] == doc_id for source in sources),
                    "citations_in_range": bool(citations) and all(1 <= index <= len(sources) for index in citations),
                    "real_chat_called": len(prompts) > before,
                }
                save()
                print(json.dumps({"case": key, "checks": item["structural_checks"]}), flush=True)
        report["phase"] = "awaiting_semantic_review"
    except BaseException as exc:
        report["phase"] = "failed"
        report["failure_type"] = type(exc).__name__
        raise
    finally:
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--baseline", type=Path, help="Re-query existing synthetic documents without regeneration")
    parser.add_argument("--chat-model", help="Isolated test override; does not modify application configuration")
    args = parser.parse_args()
    run(args.output.resolve(), args.name, args.baseline.resolve() if args.baseline else None, args.chat_model)
