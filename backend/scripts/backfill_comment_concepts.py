# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Бэкфилл концептов из комментариев рецензентов для уже загруженных документов.

До включения программной экстракции (okf_comment_concepts_enabled) комментарии
попадали в концепты нестабильно: правило промпта требовало «встраивать в
соответствующий концепт», но по факту из 19 комментариев целевого документа
концептами стали только 4, а решения (например, «наибольший табельный — самый
свежий») терялись. Скрипт детерминированно создаёт концепты из тредов
«вопрос → ответы» для существующих документов БЕЗ вызова LLM.

Для каждого .docx-документа (status=done, не в корзине, есть бандл и чанки):
  1. re-parse uploads/{doc_id}.docx парсером с поддержкой тредов;
  2. chunk_index: substring-поиск raw-текста вопроса по chunks/*.md бандла —
     якорь инвариантен к формату рендера (старые чанки содержат текст
     комментария дословно после жирного префикса);
  3. старые концепты-комментарии (tags содержит 'review') удаляются из списка,
     новые треды добавляются (тот же экстрактор, что в пайплайне, — свежая
     индексация и backfill сходятся);
  4. бандл пересобирается целиком через OKFGenerator.save_bundle (atomic move),
     replace_concepts перезаписывает okf_concepts ПОЛНЫМ списком (обязательно:
     частичная запись стёрла бы остальные концепты), новые концепты эмбеддятся
     и апсертятся в Qdrant (point_id детерминирован uuid5 от filepath),
     осиротевшие точки (старые концепты-комментарии) удаляются.

Идемпотентен: повторный запуск даёт тот же набор (слаги стабильны, uuid5
стабилен, replace_concepts перезаписывает). Chunk-точки Qdrant НЕ трогаются
(dual-index chunk-слой не участвует в проблеме).

Запуск (из backend/, при доступной БД и Qdrant):
    python scripts/backfill_comment_concepts.py            # все документы
    python scripts/backfill_comment_concepts.py --doc-id <id>
    python scripts/backfill_comment_concepts.py --dry-run  # только отчёт
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docparser import blocks_to_markdown, parse_document
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Document
from app.db.session import session_scope
from app.services.comment_concepts import extract_comment_concepts
from app.services.concept_store import replace_concepts
from app.services.okf_generator import OKFGenerator, _slugify
from app.services.pipeline import _atomic_move
from app.services.registry import get_registry
from app.services.vector_store import chunk_point_id, concept_point_id
from app.services.bundle import load_bundle
from app.models.schemas import Concept

logger = logging.getLogger("backfill_comment_concepts")

# Сколько символов вопроса использовать как якорь для поиска chunk_index.
_ANCHOR_CHARS = 80


def _iter_docs(doc_id: str | None) -> list[tuple[str, str, int | None]]:
    """(doc_id, filename, development_id) для done-документов вне корзины."""
    from app.db.session import session_scope

    with session_scope() as s:
        q = select(Document.id, Document.filename, Document.development_id).where(
            Document.deleted_at.is_(None),
            Document.status == "done",
        )
        if doc_id:
            q = q.where(Document.id == doc_id)
        rows = s.execute(q).all()
    return [(r.id, r.filename, r.development_id) for r in rows]


def _find_chunk_index(question_text: str, chunk_texts: list[str]) -> int | None:
    """Якорь — первая строка raw-текста вопроса (инвариантна к формату рендера:
    старые чанки содержат её дословно после жирного префикса)."""
    first_line = question_text.split("\n", 1)[0].strip()
    anchor = first_line[:_ANCHOR_CHARS]
    if not anchor:
        return None
    for idx, text in enumerate(chunk_texts):
        if anchor in text:
            return idx
    return None


def _question_of(concept: Concept) -> str:
    """Текст вопроса из content концепта-треда (первая строка после метки;
    двоеточие внутри жирного — формат comment_concepts._build_concept)."""
    for line in concept.content.split("\n"):
        line = line.strip()
        if line.startswith("**Комментарий рецензента"):
            _, _, rest = line.partition(":** ")
            return rest
    return ""


def process_doc(
    doc_id: str,
    filename: str,
    settings,
    generator: OKFGenerator,
    embedder,
    vector_store,
    development_id: int | None = None,
    dry_run: bool = False,
) -> dict:
    result = {"threads": 0, "removed": 0, "skipped": [], "error": None}
    bundle_dir = settings.okf_dir / doc_id
    src = settings.uploads_dir / f"{doc_id}{Path(filename).suffix.lower()}"
    if Path(filename).suffix.lower() != ".docx":
        result["skipped"].append("not_docx")
        return result
    if not src.is_file():
        result["skipped"].append("no_source_file")
        return result
    if not (bundle_dir / "chunks").is_dir():
        result["skipped"].append("no_chunks")  # не done-бандл (paused/staging)
        return result

    # 1. re-parse: блоки-комментарии (треды) из исходника
    try:
        blocks = parse_document(src)
    except Exception as exc:
        result["error"] = f"parse: {exc}"
        return result
    comment_blocks = [b for b in blocks if b.type == "comment"]
    if not comment_blocks:
        result["skipped"].append("no_comments")
        return result

    # треды → концепты тем же экстрактором, что и в пайплайне
    thread_md = blocks_to_markdown(comment_blocks)
    new_concepts, _ = extract_comment_concepts(thread_md)
    if not new_concepts:
        result["skipped"].append("no_thread_concepts")
        return result
    result["threads"] = len(new_concepts)

    # 2. chunk_index по якорю вопроса
    chunk_texts = [
        p.read_text(encoding="utf-8")
        for p in sorted(
            (bundle_dir / "chunks").glob("chunk_*.md"),
            key=lambda p: int(p.stem.split("_")[-1]),
        )
    ]

    # 3. полный список: старые минус концепты-комментарии, плюс треды
    existing = load_bundle(bundle_dir)
    kept = [d for d in existing if "review" not in (d.metadata.get("tags") or [])]
    result["removed"] = len(existing) - len(kept)

    if dry_run:
        return result

    attachments: list = []
    global_tags: list[str] = []
    if existing:
        attachments = existing[0].metadata.get("attachments") or []
        global_tags = existing[0].metadata.get("global_tags") or []

    concepts: list[Concept] = []
    slugs: list[str] = []
    chunk_of_slug: dict[str, int] = {}
    for d in kept:
        concepts.append(
            Concept(
                id="",
                title=d.metadata.get("title", ""),
                type=d.metadata.get("type", "concept"),
                tags=list(d.metadata.get("tags") or []),
                content=d.content,
                relations=list(d.metadata.get("relations") or []),
            )
        )
        slug = Path(d.filepath).stem
        slugs.append(slug)
        ci = d.metadata.get("chunk_index")
        if ci is not None:
            chunk_of_slug[slug] = ci

    new_slugs: set[str] = set()
    used = set(slugs)
    for c in new_concepts:
        base = _slugify(c.title)
        slug = base
        n = 1
        while slug in used:  # коллизии получают суффикс, как в staging
            slug = f"{base}-{n}"
            n += 1
        used.add(slug)
        slugs.append(slug)
        new_slugs.add(slug)
        concepts.append(c)
        ci = _find_chunk_index(_question_of(c), chunk_texts)
        if ci is not None:
            chunk_of_slug[slug] = ci

    # 4. пересборка бандла (atomic): tmp с chunks/attachments -> move
    tmp_dir = settings.okf_dir / f".tmp-{doc_id}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True)
    for part in ("chunks", "attachments"):
        if (bundle_dir / part).is_dir():
            shutil.copytree(bundle_dir / part, tmp_dir / part)
    okf_docs = generator.save_bundle(
        doc_id,
        filename,
        concepts,
        attachments=attachments,
        global_tags=global_tags,
        bundle_root=tmp_dir,
        slugs=slugs,
        chunk_of_slug=chunk_of_slug,
    )
    _atomic_move(tmp_dir, bundle_dir)
    for d in okf_docs:
        d.filepath = str(bundle_dir / Path(d.filepath).name)

    # 5. БД (полный список!) + Qdrant (только новые + очистка орфанов)
    with session_scope() as s:
        replace_concepts(s, doc_id, okf_docs)

    dev_tags: list[str] = []
    if development_id:
        try:
            from app.services.development_registry import get_development_registry

            dev_tags = get_development_registry().dev_tags(development_id)
        except Exception:
            dev_tags = []

    new_docs = [d for d in okf_docs if Path(d.filepath).stem in new_slugs]
    if new_docs:
        cap = settings.okf_max_concept_chars
        vectors = embedder.embed_texts(
            [f"{d.metadata.get('title', '')}\n{d.content[:cap]}" for d in new_docs]
        )
        vector_store.ensure_collection()
        vector_store.index_concepts(doc_id, new_docs, vectors, dev_tags=dev_tags)

    keep = {concept_point_id(doc_id, Path(d.filepath).stem) for d in okf_docs}
    for i in range(len(chunk_texts)):  # chunk-точки не трогаем — в keep
        keep.add(chunk_point_id(doc_id, i))
    vector_store.delete_orphaned_points(doc_id, keep)

    get_registry().update(doc_id, okf_concept_count=len(okf_docs))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Бэкфилл концептов из комментариев рецензентов.")
    parser.add_argument("--doc-id", type=str, default=None, help="Обработать один документ")
    parser.add_argument("--dry-run", action="store_true", help="Только отчёт, без записи")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()
    generator = OKFGenerator()
    from app.services.embedder import Embedder
    from app.services.vector_store import VectorStore

    embedder = Embedder()
    vector_store = VectorStore()

    docs = _iter_docs(args.doc_id)
    if args.doc_id and not docs:
        print(f"Документ {args.doc_id} не найден (или не done/в корзине)")
        sys.exit(1)

    counters = {"docs": 0, "threads": 0, "removed": 0, "skipped": 0, "errors": 0}
    for doc_id, filename, development_id in docs:
        r = process_doc(doc_id, filename, settings, generator, embedder, vector_store,
                        development_id=development_id, dry_run=args.dry_run)
        if r["error"]:
            counters["errors"] += 1
            logger.error("%s: %s", doc_id, r["error"])
            continue
        if r["skipped"]:
            counters["skipped"] += 1
            logger.info("%s: пропущено (%s)", doc_id, ", ".join(r["skipped"]))
            continue
        counters["docs"] += 1
        counters["threads"] += r["threads"]
        counters["removed"] += r["removed"]
        logger.info(
            "%s: +%d тредов-комментариев, удалено старых концептов-комментариев: %d%s",
            doc_id, r["threads"], r["removed"], " (dry-run)" if args.dry_run else "",
        )

    print(
        f"Итог: документов={counters['docs']}, тредов={counters['threads']}, "
        f"удалено старых={counters['removed']}, пропущено={counters['skipped']}, "
        f"ошибок={counters['errors']}"
    )


if __name__ == "__main__":
    main()
