# Copyright (C) 2026 Alexey
# SPDX-License-Identifier: MIT

"""Одноразовая чистка абсолютных локальных путей из маркер-блоков вложений (2026-09-07).

Инцидент: blocks_to_markdown рендерил маркер-блок вложения со значением
meta['saved_path'] «как есть», а парсеры кладут туда АБСОЛЮТНЫЙ путь машины
обработки (C:\\Users\\<user>\\...\\uploads\\<doc_id>\\attachments\\<файл>). Путь
утекал в текст чанков, в концепты (LLM копировал маркер) и в индекс Qdrant.

Скрипт идемпотентен:
  1. переписывает сегмент «(файл: <абсолютный путь>)» в document_chunks.content и
     okf_concepts.content на переносимое «(файл: attachments/<имя>)» — меняются
     ТОЛЬКО маркер-строки с абсолютным путём (относительные не трогаются);
  2. то же для второго варианта утечки — markdown-ссылки вида
     «[Вложение: …](file:<абсолютный путь>)» из доканноновой эры бандлов
     (цель ссылки становится «attachments/<имя>»);
  3. пересчитывает content_hash правленых чанков;
  4. удаляет затронутые chunk-точки Qdrant и пере-эмбеддит их из БД штатным
     VectorStore.backfill_chunks — единая каноническая формула dense/sparse,
     без третьей копии логики в скрипте (инвариант «пайплайн = reindex = rebuild»).

Concept-точки Qdrant НЕ трогаются: slim-payload не хранит content концептов, он
гидрируется из okf_concepts при чтении. chunk-точки, наоборот, несут content в
payload (и вектор посчитан по старому тексту) — поэтому их пересобираем.

Пример:
    python scripts/fix_attachment_paths.py            # правка БД + resync Qdrant
    python scripts/fix_attachment_paths.py --dry-run  # только показать изменения

Требует доступные Qdrant и embedding-сервер (или EMBEDDING_PROVIDER=fake).
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.models import DocumentChunk, OkfConcept
from app.db.session import session_scope
from qdrant_client.http import models as qm

from app.services.embedder import Embedder
from app.services.vector_store import VectorStore, chunk_point_id

# Абсолютный путь: Windows-диск (C:\) или корень (/ или \\ в начале).
_ABS_PATH = re.compile(r"^[A-Za-z]:[\\/]|^[\\/]")
# Маркер-строка вложения: «(файл: <путь>)» (в чанке обрамлена курсивом: *(файл: ...)*).
_MARKER = re.compile(r"\(файл:\s*([^()\r\n]*?)\s*\)")
# Устаревшая ссылка на вложение: «[подпись](file:<абсолютный путь>)» (доканноновая эра).
_FILE_LINK = re.compile(r"\]\(file:([^()\r\n]*?)\)")


def _relativize_abs_path(token: str) -> str | None:
    """attachments/<имя файла> для абсолютного пути, иначе None (не трогаем)."""
    if not _ABS_PATH.match(token):
        return None
    name = token.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return f"attachments/{name}"


def _relativize_marker(text: str) -> tuple[str, bool]:
    """Заменяет абсолютный путь в маркер-строках на attachments/<имя файла>.

    Относительные/прочие значения между скобками не трогает. Возвращает
    (новый текст, было ли изменение).
    """

    def _repl(m: re.Match) -> str:
        rel = _relativize_abs_path(m.group(1).strip())
        return f"(файл: {rel})" if rel else m.group(0)

    source = text or ""
    new = _MARKER.sub(_repl, source)
    return new, new != source


def _relativize_file_links(text: str) -> tuple[str, bool]:
    """Заменяет цель ссылок [..](file:<абсолютный путь>) на attachments/<имя файла>."""

    def _repl(m: re.Match) -> str:
        rel = _relativize_abs_path(m.group(1).strip())
        return f"]({rel})" if rel else m.group(0)

    source = text or ""
    new = _FILE_LINK.sub(_repl, source)
    return new, new != source


def fix_rows(dry_run: bool) -> dict:
    """Правит document_chunks.content и okf_concepts.content в БД.

    Возвращает {chunks: [(doc_id, chunk_index), ...], concepts: [(doc_id, id), ...]}.
    """
    updated_chunks: list[tuple[str, int]] = []
    updated_concepts: list[tuple[str, int]] = []
    with session_scope() as s:
        for c in s.query(DocumentChunk).filter(DocumentChunk.content.like("%файл: %")).all():
            new, changed = _relativize_marker(c.content)
            if not changed:
                continue
            updated_chunks.append((c.doc_id, c.chunk_index))
            if not dry_run:
                c.content = new
                c.content_hash = hashlib.sha256(new.encode("utf-8")).hexdigest()
        for c in s.query(DocumentChunk).filter(DocumentChunk.content.like("%](file:%")).all():
            new, changed = _relativize_file_links(c.content)
            if not changed:
                continue
            updated_chunks.append((c.doc_id, c.chunk_index))
            if not dry_run:
                c.content = new
                c.content_hash = hashlib.sha256(new.encode("utf-8")).hexdigest()
        for c in s.query(OkfConcept).filter(OkfConcept.content.like("%файл: %")).all():
            new, changed = _relativize_marker(c.content)
            if not changed:
                continue
            updated_concepts.append((c.doc_id, c.id))
            if not dry_run:
                c.content = new
        for c in s.query(OkfConcept).filter(OkfConcept.content.like("%](file:%")).all():
            new, changed = _relativize_file_links(c.content)
            if not changed:
                continue
            updated_concepts.append((c.doc_id, c.id))
            if not dry_run:
                c.content = new
    return {"chunks": updated_chunks, "concepts": updated_concepts}


def resync_chunks(affected: list[tuple[str, int]]) -> int:
    """Удаляет затронутые chunk-точки и пере-эмбеддит их из БД через backfill_chunks."""
    vs = VectorStore()
    vs.ensure_collection()
    ids = [chunk_point_id(doc_id, ci) for doc_id, ci in affected]
    # str point_id — допустимый ExtendedPointId в рантайме; стабы qdrant-client
    # слишком узки (тот же приём, что в vector_store.delete_orphaned_points).
    vs.client.delete(
        collection_name=vs.collection,
        points_selector=qm.PointIdsList(points=ids),  # type: ignore[arg-type]
    )
    print(f"Удалено chunk-точек из Qdrant: {len(ids)}")
    return vs.backfill_chunks(Embedder())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Чистка абсолютных локальных путей из маркер-блоков вложений."
    )
    parser.add_argument("--dry-run", action="store_true", help="Показать изменения без записи")
    args = parser.parse_args()

    stats = fix_rows(dry_run=args.dry_run)
    # Ряд мог попасть в список из двух проходов (маркер + file-ссылка) — дедуп.
    chunks = list(dict.fromkeys(stats["chunks"]))
    concepts = list(dict.fromkeys(stats["concepts"]))
    if not chunks and not concepts:
        print("Абсолютных путей в маркер-блоках не найдено — ничего не менять.")
        return

    print(f"Маркер-строк с абсолютным путём: чанков {len(chunks)}, концептов {len(concepts)}")
    for doc_id, ci in chunks:
        print(f"  chunk   [{doc_id}] #{ci}")
    for doc_id, cid in concepts:
        print(f"  concept [{doc_id}] #{cid}")

    if args.dry_run:
        print("dry-run: БД и Qdrant не изменены.")
        return

    # Правка концептов закоммичена выше (session_scope). Qdrant не хранит их
    # content — исправленное значение подхватится при гидрации, точки не трогаем.
    if chunks:
        indexed = resync_chunks(chunks)
        print(f"Resync чанков: пере-эмбедждено точек {indexed} (ожидалось {len(chunks)})")
        if indexed != len(chunks):
            print("ВНИМАНИЕ: число пере-эмбеджденных точек не совпало с ожидаемым —")
            print("проверьте логи backfill (часть точек может не дойти до Qdrant).")


if __name__ == "__main__":
    main()
