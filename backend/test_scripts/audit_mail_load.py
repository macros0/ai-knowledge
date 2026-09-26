"""Read-only SQL/Qdrant ownership audit after a completed synthetic load run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen


def audit(report_path: Path) -> dict:
    import psycopg
    from psycopg.rows import dict_row

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["phase"] in {"passed", "failed_gates"}, "Wait for the original load process to finish"
    database = report["database"]
    assert re.fullmatch(r"mail_load_[a-z0-9_]{1,32}", database)
    assert len(report["documents"]) == 100
    points, offset = [], None
    while True:
        body = {"limit": 256, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        request = Request(f"http://127.0.0.1:26333/collections/{database}/points/scroll",
                          data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=15) as response:
            result = json.load(response)["result"]
        points.extend(result["points"])
        offset = result["next_page_offset"]
        if offset is None:
            break
    with psycopg.connect(host="127.0.0.1", port=25432, user="okf", password="acceptance-only",
                         dbname=database, connect_timeout=5, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        docs = conn.execute("SELECT id, filename, status, problem FROM documents").fetchall()
        chunks = conn.execute("SELECT doc_id, chunk_index, source_id FROM document_chunks").fetchall()
        concepts = conn.execute("SELECT doc_id, slug, chunk_index, source_id FROM okf_concepts").fetchall()
        sources = conn.execute("SELECT doc_id, source_id, parser_version FROM document_sources").fetchall()
        states = conn.execute("SELECT doc_id, active_generation_id, candidate_generation_id FROM document_generation_states").fetchall()
    assert len(docs) == 101 and all(row["status"] == "done" and row["problem"] is None for row in docs)
    assert len(points) == len(chunks) + len(concepts)
    assert all(row["parser_version"] == report["parser_version"] for row in sources)
    active = {row["doc_id"]: row["active_generation_id"] for row in states}
    assert set(active) == {row["id"] for row in docs}
    assert all(row["active_generation_id"] and row["candidate_generation_id"] is None for row in states)
    owners = {(row["doc_id"], row["chunk_index"]): row["source_id"] for row in chunks}
    known = {(row["doc_id"], row["source_id"]) for row in sources}
    expected = {(row["doc_id"], "chunk", row["chunk_index"], None) for row in chunks}
    expected.update((row["doc_id"], "concept", row["chunk_index"], row["slug"]) for row in concepts)
    actual = set()
    for point in points:
        payload = point["payload"]
        assert payload["generation_id"] == active[payload["doc_id"]]
        assert payload["source_id"] == owners[payload["doc_id"], payload["chunk_index"]]
        actual.add((payload["doc_id"], payload["point_type"], payload["chunk_index"], payload.get("slug")))
    assert actual == expected and len(actual) == len(points), "Missing, duplicate or foreign source points"
    assert all((row["doc_id"], row["source_id"]) in known for row in chunks + concepts)
    for item in report["documents"]:
        owned = [point for point in points if point["payload"]["doc_id"] == item["doc_id"]]
        assert len(owned) == item["chunks"] + item["concepts"]
    return {"status": "passed", "database": database, "parser_version": report["parser_version"],
            "documents_including_control": len(docs), "sources": len(sources), "chunks": len(chunks),
            "concepts": len(concepts), "points": len(points), "exact_point_identity_match": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Do not overwrite an existing audit")
    result = audit(args.report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result))
