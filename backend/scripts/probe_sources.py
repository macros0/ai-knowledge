# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Baseline/after probe для приёмки поисковых правок.

Прогоняет полный путь чата (без LLM) по фиксированному набору запросов на
реальном корпусе и печатает итоговые блоки-источники. Используется для
сравнения до/после правок matched_terms (стемминг словоформ) и других
изменений ранжирования/фильтрации.

Критерий ложного срабатывания фиксировать ДО прогона:
  (а) источник baseline пропал из выдачи ЦЕЛИКОМ. Смещение позиции или
      выход из топ-5 срабатыванием НЕ считается: блок может законно
      сместиться, если фильтр раньше ошибочно его вырезал/пропускал
      (пример 02.09.2026: LK_STAT #5 -> #6, когда стемминг вернул в выдачу
      лексически связанный блок с более высоким fused score);
  (б) появился источник, сматченный ТОЛЬКО стеммом, чей заголовок не
      относится к теме запроса.

Запуск (из backend/, стек поднят): python scripts/probe_sources.py [--baseline]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.chunk_store import enrich_chunk_hits
from app.services.concept_store import enrich_concept_hits
from app.services.context_builder import (
    drop_partial_title_matches,
    drop_unmatched_blocks,
    matched_terms,
    merge_and_format,
)
from app.services.embedder import Embedder
from app.services.search_filter import build_doc_lookup, drop_invisible_hits
from app.services.sparse import to_sparse_vector
from app.services.vector_store import VectorStore

PROBES = [
    {"q": "Выбор табельного номера", "tags": ["СФР ПР"]},
    {"q": "Выбор табельного номера", "tags": None},
    {"q": "Коды условий расчёта пособий", "tags": None},
    {"q": "Подписанты в 2-НДФЛ", "tags": None},
    {"q": "Какие интеграции с ЛК есть", "tags": None},
]


def run() -> dict:
    settings = get_settings()
    vs = VectorStore()
    emb = Embedder()
    out: dict = {}
    for probe in PROBES:
        q = probe["q"]
        vec = emb.embed(q)
        sv = to_sparse_vector(q)
        hits = vs.search_composite(
            dense_vec=vec, sparse_vec=sv, tags=probe["tags"],
            branches={"dense", "bm25"}, top_k=settings.search_per_branch_top_k,
        )
        doc_lookup = build_doc_lookup(hits)
        hits = drop_invisible_hits(hits, doc_lookup)
        enrich_concept_hits(hits)
        enrich_chunk_hits(hits)
        filename_lookup = {did: (d or {}).get("filename", "") for did, d in doc_lookup.items()}
        merged = merge_and_format(hits, settings, filename_lookup=filename_lookup)
        merged = merged[:10]
        merged = drop_unmatched_blocks(merged, q)
        merged = drop_partial_title_matches(merged, q)
        out[q if not probe["tags"] else f"{q} [tags={probe['tags'][0]}]"] = [
            {
                "title": m["title"],
                "doc": m["doc_id"][:8],
                "tags": m["tags"],
                "matched": matched_terms(m, q),
            }
            for m in merged
        ]
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true", help="Сохранить baseline в probe-baseline.json")
    args = parser.parse_args()

    result = run()
    here = Path(__file__).resolve().parent
    if args.baseline:
        (here / "probe-baseline.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print("baseline saved:", len(result), "probes")
    base_path = here / "probe-baseline.json"
    baseline = json.loads(base_path.read_text(encoding="utf-8")) if base_path.exists() else None

    for key, blocks in result.items():
        print(f"\n=== {key}: {len(blocks)} источников ===")
        base = (baseline or {}).get(key)
        for i, b in enumerate(blocks, 1):
            mark = ""
            if base:
                if b["title"] not in {x["title"] for x in base}:
                    # новые блоки — проверить вручную по критерию (б):
                    # относится ли заголовок к теме запроса
                    mark = "  <<< NEW (не в baseline — проверить критерий (б))"
            print(f"  {i}. {b['title'][:70]} | doc {b['doc']} | mt={','.join(b['matched'])}{mark}")

    if baseline:
        print("\n=== Сравнение с baseline ===")
        for key, blocks in result.items():
            base = baseline.get(key, [])
            base_titles = {x["title"] for x in base}
            now_titles = {x["title"] for x in blocks}
            lost = base_titles - now_titles
            if lost:
                print(f"  !! {key}: источники baseline пропали из выдачи ЦЕЛИКОМ: {lost}")
            else:
                print(f"  ok {key}: все источники baseline в выдаче ({len(blocks)} источников)")


if __name__ == "__main__":
    main()
