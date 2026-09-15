"""Read-only corpus benchmark for the user's IT3330/IT3273 reference searches.

Capture the nominated historical off/on sources once; later runs reuse that
expectation file instead of blessing the current search output as correct.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import ChatMessage, Document, DocumentChunk, OkfConcept
from app.db.session import session_scope
from app.services.embedder import Embedder
from app.services.glossary.snapshot import load_glossary_snapshot
from app.services.vector_store import VectorStore
from test_scripts.probe_sources import run_case
from test_scripts.stage8_manifest import _runtime_snapshot


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()


def dump(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def capture_reference(path):
    reference = {
        "schema_version": 1,
        "provenance": "User nominated IT3330 and IT3273 searches as a reference; minimum sources are the union of the latest saved off/on searches.",
        "rank_is_not_required": True,
        "cases": [],
    }
    with session_scope() as session:
        for query in ("IT3330", "IT3273"):
            turns = session.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.role == "user",
                    func.lower(func.trim(ChatMessage.content)) == query.lower(),
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(30)
            ).all()
            evidence, expected, modes = [], {}, set()
            for turn in turns:
                answer = session.scalar(
                    select(ChatMessage)
                    .where(
                        ChatMessage.session_id == turn.session_id,
                        ChatMessage.id > turn.id,
                        ChatMessage.role == "assistant",
                    )
                    .order_by(ChatMessage.id)
                    .limit(1)
                )
                if answer is None:
                    continue
                status = (answer.retrieval_metadata or {}).get("expansion_status")
                mode = (
                    "off"
                    if status == "disabled"
                    else "on"
                    if status in {"applied", "limited"}
                    else None
                )
                if mode is None or mode in modes:
                    continue
                modes.add(mode)
                sources = []
                for source in answer.sources or []:
                    slug = (
                        Path(source.get("filepath", "")).stem
                        if source.get("point_type") != "chunk"
                        else None
                    )
                    key = (source.get("doc_id"), slug, source.get("chunk_index"))
                    item = {
                        "doc_id": key[0],
                        "slug": key[1],
                        "chunk_index": key[2],
                        "title": source["title"],
                        "point_type": source.get("point_type"),
                    }
                    expected[key] = item
                    sources.append(item)
                evidence.append(
                    {"glossary": mode, "query_at": str(turn.created_at), "sources": sources}
                )
                if len(modes) == 2:
                    break
            if modes != {"off", "on"}:
                raise RuntimeError(f"No saved off/on reference pair for {query}")
            reference["cases"].append(
                {
                    "query": query,
                    "expected_canonicals": [query],
                    "mandatory_sources": list(expected.values()),
                    "historical_evidence": evidence,
                }
            )
    if path.exists():
        raise FileExistsError("Refusing to overwrite approved reference")
    dump(path, reference)
    return reference


def corpus_state(vs):
    with session_scope() as session:
        docs = session.execute(
            select(Document.id, Document.filename, Document.deleted_at).order_by(Document.id)
        ).all()
        concept_rows = session.execute(
            select(OkfConcept.doc_id, OkfConcept.slug, OkfConcept.content).order_by(
                OkfConcept.doc_id, OkfConcept.slug
            )
        ).all()
        chunk_rows = session.execute(
            select(DocumentChunk.doc_id, DocumentChunk.chunk_index, DocumentChunk.content).order_by(
                DocumentChunk.doc_id, DocumentChunk.chunk_index
            )
        ).all()
    return {
        "documents": len(docs),
        "concepts": len(concept_rows),
        "chunks": len(chunk_rows),
        "relational_sha256": digest(
            [list(map(list, rows)) for rows in (docs, concept_rows, chunk_rows)]
        ),
        "qdrant_points": vs.client.count(collection_name=vs.collection, exact=True).count,
    }


def source_key(source):
    return (source.get("doc_id"), source.get("slug"), source.get("chunk_index"))


def missing_sources(result, expected, stage="final_blocks"):
    actual = {source_key(block["source"]) for block in result[stage]}
    return [item for item in expected if source_key(item) not in actual]


def rank_signature(result):
    return digest(
        [
            {key: row[key] for key in ("title", "point_type", "source")}
            for row in result["final_blocks"]
        ]
    )


def summary(values):
    ordered = sorted(values)
    return {
        "n": len(values),
        "p50": round(statistics.median(values), 3),
        "p95": round(ordered[math.ceil(0.95 * len(ordered)) - 1], 3),
        "min": min(values),
        "max": max(values),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--capture-reference", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=100)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("samples must be positive")
    settings = get_settings()
    identity = _runtime_snapshot(settings)
    if (
        identity["database_name"] != "okf_stage8_test"
        or identity["qdrant_collection"] != "okf_knowledge_stage8_test"
    ):
        raise RuntimeError("This reference must run against the isolated Stage 8 contour")
    args.output.mkdir(parents=True, exist_ok=False)
    reference = (
        capture_reference(args.reference)
        if args.capture_reference
        else json.loads(args.reference.read_text(encoding="utf-8"))
    )
    dump(args.output / "reference.json", reference)
    vs, embedder = VectorStore(), Embedder()
    snapshot = asdict(load_glossary_snapshot())
    corpus_before = corpus_state(vs)
    manifest = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "environment": identity,
        "git_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "reference_sha256": digest(reference),
        "glossary": snapshot,
        "corpus_before": corpus_before,
        "settings": {
            key: getattr(settings, key)
            for key in (
                "search_per_branch_top_k",
                "search_rrf_dense_weight",
                "search_rrf_bm25_weight",
                "search_rrf_k",
                "glossary_sparse_expansion_weight",
                "glossary_max_added_aliases_per_term",
            )
        },
        "method": {
            "samples_per_scenario": args.samples,
            "warmups": 2,
            "order": "seeded shuffled round robin",
            "seed": 32733330,
            "top_k": 50,
            "filters": "none",
            "embedding": "real Embedder on every hybrid request; no benchmark embedding cache",
            "excluded": "HTTP/auth, answer LLM, browser rendering",
            "percentile": "nearest rank: ceil(p*n)-1",
        },
    }
    manifest["code_sha256"] = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT / "backend/app").rglob("*.py"))
    }
    dump(args.output / "manifest.json", manifest)
    variants = []
    for base in reference["cases"]:
        for mode in ("bm25", "hybrid"):
            for api in ("search", "chat"):
                for glossary in ("off", "on"):
                    case = {
                        "id": f"{base['query']}__{mode}__{api}__{glossary}",
                        "query": base["query"],
                        "locale": "ru",
                        "mode": mode,
                        "api": api,
                        "top_k": 50,
                        "expected_canonicals": base["expected_canonicals"],
                        "mandatory_sources": base["mandatory_sources"],
                    }
                    variants.append((case, glossary))
    baseline, measurements = {}, {}
    for case, glossary in variants:
        for _ in range(2):
            result = run_case(
                case, glossary_mode=glossary, settings=settings, vector_store=vs, embedder=embedder
            )
        baseline[case["id"]] = result
        measurements[case["id"]] = {
            "samples": [],
            "ranking_changes": 0,
            "missing_reference_runs": 0,
        }
    dump(args.output / "retrieval.json", list(baseline.values()))
    print("Warmup complete: 16 scenarios; measuring", flush=True)
    rng = random.Random(32733330)
    started = perf_counter()
    for repeat in range(args.samples):
        rng.shuffle(variants)
        for case, glossary in variants:
            result = run_case(
                case, glossary_mode=glossary, settings=settings, vector_store=vs, embedder=embedder
            )
            entry = measurements[case["id"]]
            entry["samples"].append(result["timings_ms"])
            entry["ranking_changes"] += rank_signature(result) != rank_signature(
                baseline[case["id"]]
            )
            entry["missing_reference_runs"] += bool(
                missing_sources(result, case["mandatory_sources"])
            )
        if (repeat + 1) % 10 == 0:
            print(
                f"{repeat + 1}/{args.samples} rounds; elapsed {perf_counter() - started:.1f}s",
                flush=True,
            )
    dump(args.output / "samples.json", measurements)
    report = []
    for name, result in baseline.items():
        entry = measurements[name]
        report.append(
            {
                "id": name,
                "counts": {
                    stage: len(result[stage])
                    for stage in (
                        "raw_candidates",
                        "visible_candidates",
                        "merged_blocks",
                        "final_blocks",
                    )
                },
                "expected": len(result["mandatory_sources"]),
                "missing_sources": missing_sources(result, result["mandatory_sources"]),
                "missing_at_stage": {
                    stage: missing_sources(result, result["mandatory_sources"], stage)
                    for stage in (
                        "raw_candidates",
                        "visible_candidates",
                        "merged_blocks",
                        "final_blocks",
                    )
                },
                "ranking_changes": entry["ranking_changes"],
                "missing_reference_runs": entry["missing_reference_runs"],
                "timing_ms": {
                    metric: summary([sample[metric] for sample in entry["samples"]])
                    for metric in (
                        "prepare_ms",
                        "embed_ms",
                        "qdrant_ms",
                        "postfilter_ms",
                        "total_ms",
                    )
                },
            }
        )
    corpus_after = corpus_state(vs)
    dump(
        args.output / "summary.json",
        {
            "scenarios": report,
            "corpus_unchanged": corpus_before == corpus_after,
            "glossary_unchanged": snapshot == asdict(load_glossary_snapshot()),
            "corpus_after": corpus_after,
        },
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "corpus_unchanged": corpus_before == corpus_after,
                "scenarios": len(report),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
