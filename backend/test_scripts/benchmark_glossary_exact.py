"""Isolated LocalQdrant/SQLite query-time acceptance and performance harness.

Run from backend: python -m test_scripts.benchmark_glossary_exact --output <local.json>.
This deliberately measures a deterministic local retrieval pipeline, not a remote
Qdrant server, PostgreSQL, HTTP, LLM, or embedding latency. No existing DB is used.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

from qdrant_client import QdrantClient, models as qm
from sqlalchemy import event

from app.config import Settings
from app.db.models import Document, DocumentChunk, OkfConcept
from app.db.session import configure_for_tests, get_engine, init_db, session_scope
from app.services.context_builder import merge_and_format
from app.services.fusion import Hit, reciprocal_rank_fusion
from app.services.glossary.expansion import prepare_query
from app.services.glossary.matching import exact_excerpt
from app.services.glossary.query_sparse import build_query_sparse
from app.services.glossary.registry import GlossaryRegistry
from app.services.glossary.rule_registry import GlossaryRuleRegistry
from app.services import retrieval_hydration
from app.services.sparse import to_sparse_vector


WRITE_METHODS = (
    "upsert", "update_vectors", "delete_vectors", "set_payload", "overwrite_payload",
    "delete_payload", "clear_payload", "delete", "create_collection",
    "recreate_collection", "delete_collection", "update_collection",
    "batch_update_points", "upload_points", "upload_collection",
)


@contextmanager
def forbid_index_writes():
    """Install only AFTER fixture indexing; catch writes via any QdrantClient."""
    from contextlib import ExitStack

    with ExitStack() as stack:
        spies = {
            name: stack.enter_context(patch.object(
                QdrantClient, name,
                side_effect=AssertionError(f"query-time Qdrant write: {name}"),
            ))
            for name in WRITE_METHODS
        }
        yield spies
        for spy in spies.values():
            spy.assert_not_called()


@dataclass
class Corpus:
    client: QdrantClient
    records: dict[str, str]
    settings: Settings
    collection: str = "glossary_exact_acceptance"

    @classmethod
    def build(cls, records: dict[str, str]):
        client = QdrantClient(":memory:")
        corpus = cls(client, records, Settings(_env_file=None, auth_provider="disabled"))
        client.create_collection(
            corpus.collection,
            # LocalQdrant's COSINE query normalizes its stored float32 array
            # in-place; DOT makes the byte-exact immutability check meaningful.
            vectors_config={"dense": qm.VectorParams(size=2, distance=qm.Distance.DOT)},
            sparse_vectors_config={"sparse": qm.SparseVectorParams()},
        )
        points = []
        with session_scope() as session:
            for index, (doc_id, content) in enumerate(records.items()):
                title = f"Record {index}"
                session.add(Document(id=doc_id, filename=f"{doc_id}.docx"))
                session.add(OkfConcept(
                    doc_id=doc_id, slug="concept", title=title, content=content,
                    chunk_index=0,
                ))
                session.add(DocumentChunk(
                    doc_id=doc_id, chunk_index=0, section_title=title,
                    content=content, char_count=len(content),
                ))
                for offset, kind in enumerate(("concept", "chunk")):
                    payload = dict(point_type=kind, doc_id=doc_id, chunk_index=0,
                                   title=title, tags=[], source_locale="en")
                    if kind == "concept":
                        payload["slug"] = "concept"
                    points.append(qm.PointStruct(
                        id=index * 2 + offset,
                        vector={
                            "dense": [1.0, (index + 1) / (len(records) + 1)],
                            "sparse": to_sparse_vector(f"{title}\n{content}", stopwords=frozenset()),
                        },
                        payload=payload,
                    ))
        client.upsert(corpus.collection, points=points, wait=True)
        return corpus

    def fingerprint(self):
        points, cursor = self.client.scroll(
            self.collection, limit=len(self.records) * 2 + 1,
            with_vectors=True, with_payload=True,
        )
        assert cursor is None
        data = [point.model_dump(mode="json") for point in sorted(points, key=lambda p: p.id)]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def candidates(self, plan, *, dense=True):
        """Real local dense/sparse query calls, followed by production RRF."""
        branches = []
        vector = build_query_sparse(plan, stopwords=frozenset(), settings=self.settings)
        for using, query, weight in (
            ("dense", [1.0, 0.0], 1.0), ("sparse", vector, 1.5),
        ):
            if using == "dense" and not dense:
                continue
            points = self.client.query_points(
                self.collection, query=query, using=using,
                limit=len(self.records) * 2, with_payload=True,
            ).points
            branches.append(([
                Hit(str(point.id), point.score, deepcopy(point.payload), rank)
                for rank, point in enumerate(points)
            ], weight))
        return reciprocal_rank_fusion(branches)

    def search(self, query, *, enabled=True, dense=True):
        plan = prepare_query(query, ui_locale="en", enabled=enabled, settings=self.settings)
        groups = plan.strict_groups or plan.match_groups
        hits, lookup = retrieval_hydration.load_visible_retrieval_hits(
            self.candidates(plan, dense=dense), max_concept_chars=300,
            max_chunk_chars=300, exact_groups=groups,
        )
        blocks = merge_and_format(
            hits, self.settings, exact_groups=groups,
            filename_lookup={key: value["filename"] for key, value in lookup.items() if value},
        )
        excerpts = [exact_excerpt(block["content"], 300, groups) for block in blocks]
        return plan, hits, blocks, excerpts


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.5))]


def measure(corpus, query, enabled):
    """Instrument actual hydrated text BEFORE filtering, plus all SQL SELECTs."""
    counts = {"sql_selects": 0, "hydration_bytes": 0}
    stage = {}
    original = retrieval_hydration._enrich_retrieval_hits_in_session

    def sql(_conn, _cursor, statement, _parameters, _context, _many):
        counts["sql_selects"] += int(statement.lstrip().upper().startswith("SELECT"))

    def hydrated(hits, session, **kwargs):
        result = original(hits, session, **kwargs)
        counts["hydration_bytes"] = sum(
            len(hit.payload.get("content", "").encode("utf-8")) for hit in hits
        )
        return result

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", sql)
    try:
        with patch.object(retrieval_hydration, "_enrich_retrieval_hits_in_session", hydrated):
            started = previous = perf_counter()
            plan = prepare_query(query, ui_locale="en", enabled=enabled, settings=corpus.settings)
            now = perf_counter()
            stage["planning_ms"] = (now - previous) * 1000
            previous = now
            hits = corpus.candidates(plan)
            now = perf_counter()
            stage["local_candidates_ms"] = (now - previous) * 1000
            previous = now
            groups = plan.strict_groups or plan.match_groups
            hits, lookup = retrieval_hydration.load_visible_retrieval_hits(
                hits, max_concept_chars=300, max_chunk_chars=300, exact_groups=groups,
            )
            now = perf_counter()
            stage["hydration_filter_ms"] = (now - previous) * 1000
            previous = now
            blocks = merge_and_format(
                hits, corpus.settings, exact_groups=groups,
                filename_lookup={key: value["filename"] for key, value in lookup.items() if value},
            )
            for block in blocks[:10]:
                exact_excerpt(block["content"], 300, groups)
            finished = perf_counter()
            stage["merge_excerpt_ms"] = (finished - previous) * 1000
    finally:
        event.remove(engine, "before_cursor_execute", sql)
    return dict(total_ms=(finished - started) * 1000, **stage, **counts,
                strict_groups=len(groups), status=plan.status, result_blocks=len(blocks))


def summarize(rows):
    result = {"samples": len(rows)}
    for key in rows[0]:
        if key.endswith("_ms"):
            result[key] = {
                "p50": round(median(row[key] for row in rows), 3),
                "p95": round(percentile([row[key] for row in rows], 0.95), 3),
            }
        else:
            result[key] = sorted(set(row[key] for row in rows))
    return result


def run_benchmark(*, repeats=30):
    # 40 concept + 40 chunk candidates, late exact text at ~4 KB in every match.
    records = {
        f"bench-{index:03d}": ("Neutral document material. " * 160)
        + (f"IT{index % 5 + 3:04d}" if index < 30 else f"IT{index % 5 + 3:04d}7")
        for index in range(40)
    }
    corpus = Corpus.build(records)
    try:
        before = corpus.fingerprint()
        with forbid_index_writes():
            terms = GlossaryRegistry()
            for number in range(3, 8):
                terms.create(None, "sap_infotype", f"Infotype number {number}", infotype_number=f"{number:04d}")
            prefixes = ["IT"] + [f"Prefix{index:02d}" for index in range(49)]
            GlossaryRuleRegistry().create(
                name="Maximum prefixes", number_from=0, number_to=9999, prefixes=prefixes,
            )
            result = dict(
                scope="Isolated real QdrantClient(':memory:') + SQLite; no HTTP/PostgreSQL/embedding/LLM",
                baseline="Same prepared corpus and glossary, expansion disabled (bounded 300-char hydration)",
                after="Expansion enabled, full canonical hydration and exact filtering",
                corpus_documents=40, indexed_points=80, prefixes=50,
                hydration_bytes_definition="UTF-8 bytes of canonical texts attached before exact filtering; excludes metadata and wire overhead",
                sql_definition="SELECT statements in the complete timed query; warmed glossary snapshot",
                target_p95_growth_percent=20, cases={},
            )
            for count in (1, 5):
                query = " ".join(f"IT{number:04d}" for number in range(3, 3 + count))
                for _ in range(3):
                    measure(corpus, query, False)
                    measure(corpus, query, True)
                samples = {False: [], True: []}
                # Alternate order to reduce drift bias.
                for iteration in range(repeats):
                    for enabled in ((False, True) if iteration % 2 == 0 else (True, False)):
                        samples[enabled].append(measure(corpus, query, enabled))
                baseline, after = summarize(samples[False]), summarize(samples[True])
                growth = (after["total_ms"]["p95"] / baseline["total_ms"]["p95"] - 1) * 100
                result["cases"][str(count)] = dict(
                    query=query, baseline=baseline, after=after,
                    p95_growth_percent=round(growth, 2), target_passed=growth <= 20,
                )
            result["index_unchanged"] = before == corpus.fingerprint()
            result["index_sha256"] = before
            assert result["index_unchanged"]
            return result
    finally:
        corpus.client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=30)
    args = parser.parse_args()
    if args.repeats < 10:
        parser.error("at least 10 measured repetitions are required")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="glossary-benchmark-", dir=args.output.parent) as directory:
        configure_for_tests(f"sqlite:///{(Path(directory) / 'isolated.db').as_posix()}")
        init_db()
        try:
            result = run_benchmark(repeats=args.repeats)
        finally:
            get_engine().dispose()
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
