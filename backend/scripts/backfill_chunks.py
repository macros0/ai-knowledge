"""Генерация чанков для старых документов без перезагрузки.

Чанки строятся из исходного файла (data/uploads/{doc_id}{ext}) через
Pipeline.ensure_chunks и сохраняются в канонической БД, с учётом дерева
источников. LLM и эмбеддинги не задействованы.

Существующие SQL-чанки и документы с опубликованной либо подготавливаемой
версией пропускаются. Для замены опубликованного текста нужна регенерация:
старый --force отклоняется до каких-либо изменений.

Запуск (при остановленном сервисе, из каталога backend):
    python scripts/backfill_chunks.py
    python scripts/backfill_chunks.py --doc-id 0198b6c14efc43d5
    python scripts/backfill_chunks.py --data-dir /path/to/data
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Построить чанки для документов из исходников.")
    parser.add_argument("--data-dir", type=Path, default=None, help="Каталог данных (по умолчанию корневой ./data)")
    parser.add_argument("--doc-id", type=str, default=None, help="Обработать только конкретный документ")
    parser.add_argument("--force", action="store_true", help="Устарел: используйте регенерацию документа")
    args = parser.parse_args()
    if args.force:
        parser.error("--force не поддерживается: для замены чанков используйте регенерацию документа")

    if args.data_dir is not None:
        os.environ["DATA_DIR"] = str(args.data_dir)

    from app.services.pipeline import Pipeline
    from app.db.models import Document, DocumentChunk, DocumentGenerationState
    from app.db.session import session_scope
    from app.services.generation_store import lock_generation_read

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
        with session_scope() as session:
            lock_generation_read(session, [doc_id])
            current = session.get(Document, doc_id)
            state = session.get(DocumentGenerationState, doc_id)
            count = session.query(DocumentChunk).filter_by(doc_id=doc_id).count()
            if current is None or current.deleted_at is not None:
                print(f"[{doc_id}] пропущен: документ отсутствует или в корзине")
                skipped += 1
                continue
            if count or (state and (state.active_generation_id or state.candidate_generation_id)):
                print(f"[{doc_id}] пропущен: чанки уже в БД ({count}) или есть версия документа")
                skipped += 1
                continue
            ext = Path(current.filename).suffix.lower()
        source = pipeline.settings.uploads_dir / f"{doc_id}{ext}"
        if not source.is_file():
            print(f"[{doc_id}] пропущен: исходный файл не найден ({source.name})")
            skipped += 1
            continue

        try:
            pipeline.ensure_chunks(doc_id)
            with session_scope() as session:
                count = session.query(DocumentChunk).filter_by(doc_id=doc_id).count()
        except Exception as exc:
            print(f"[{doc_id}] ОШИБКА: {exc}")
            failed += 1
            continue
        if not count:
            print(f"[{doc_id}] пропущен: нет канонических чанков (пустой текст или состояние изменилось)")
            skipped += 1
            continue
        print(f"[{doc_id}] чанков в БД после обработки: {count}")
        processed += 1

    print(f"Готово: обработано {processed}, пропущено {skipped}, ошибок {failed}")


if __name__ == "__main__":
    main()
