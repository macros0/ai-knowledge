"""Генерация чанков для старых документов без перезагрузки.

Чанки строятся из исходного файла (data/uploads/{doc_id}{ext}) через
parse_document -> blocks_to_markdown -> chunk_text и кэшируются в
data/okf_bundles/{doc_id}/chunks/ — та же логика, что и ленивый backfill
в Pipeline.ensure_chunks, но пакетно и без участия API. LLM и эмбеддинги
не задействованы.

Документы с уже существующими чанками пропускаются (или пересобираются
с --force).

Запуск (при остановленном сервисе, из каталога backend):
    python scripts/backfill_chunks.py
    python scripts/backfill_chunks.py --doc-id 0198b6c14efc43d5
    python scripts/backfill_chunks.py --force
    python scripts/backfill_chunks.py --data-dir /path/to/data
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Построить чанки для документов из исходников.")
    parser.add_argument("--data-dir", type=Path, default=None, help="Каталог данных (по умолчанию корневой ./data)")
    parser.add_argument("--doc-id", type=str, default=None, help="Обработать только конкретный документ")
    parser.add_argument("--force", action="store_true", help="Перестроить чанки, даже если уже есть")
    args = parser.parse_args()

    if args.data_dir is not None:
        os.environ["DATA_DIR"] = str(args.data_dir)

    from app.services.pipeline import Pipeline

    pipeline = Pipeline()
    if args.doc_id:
        doc = pipeline.registry.get(args.doc_id)
        if not doc:
            print(f"Документ не найден: {args.doc_id}")
            raise SystemExit(1)
        docs = [doc]
    else:
        docs = [d for d in pipeline.registry.list() if d.get("status") == "done"]

    processed = skipped = failed = 0
    for doc in docs:
        doc_id = doc["id"]
        ext = Path(doc["filename"]).suffix.lower()
        source = pipeline.settings.uploads_dir / f"{doc_id}{ext}"
        if not source.is_file():
            print(f"[{doc_id}] пропущен: исходный файл не найден ({source.name})")
            skipped += 1
            continue

        chunks_dir = pipeline.settings.okf_dir / doc_id / "chunks"
        if chunks_dir.is_dir() and not args.force:
            count = len(list(chunks_dir.glob("chunk_*.md")))
            print(f"[{doc_id}] пропущен: чанки уже есть ({count})")
            skipped += 1
            continue
        if args.force and chunks_dir.exists():
            shutil.rmtree(chunks_dir, ignore_errors=True)

        try:
            meta = pipeline.ensure_chunks(doc_id)
        except Exception as exc:
            print(f"[{doc_id}] ОШИБКА: {exc}")
            failed += 1
            continue
        print(f"[{doc_id}] сгенерировано чанков: {len(meta)}")
        processed += 1

    print(f"Готово: обработано {processed}, пропущено {skipped}, ошибок {failed}")


if __name__ == "__main__":
    main()