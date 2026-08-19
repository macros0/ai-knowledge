"""Пересборка коллекции Qdrant из OKF-бандлов на диске (data/okf_bundles/{doc_id}/).

Векторный индекс — производные данные: первоисточник это OKF-файлы с
YAML-фронтматтером. Если коллекция потеряна (например, после апгрейда Qdrant,
когда storage-формат несовместим) — этот скрипт полностью пересобирает индекс
из okf_bundles без пересоздания документов.

Запуск (при остановленном сервисе, из каталога backend):
    python scripts/reindex.py
    python scripts/reindex.py --data-dir /path/to/data

Требует доступный Qdrant и embedding-сервер (или EMBEDDING_PROVIDER=fake).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.bundle import load_bundle
from app.services.embedder import Embedder
from app.services.vector_store import VectorStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Пересобрать коллекцию Qdrant из OKF-бандлов.")
    parser.add_argument("--data-dir", type=Path, default=Path("./data"), help="Каталог данных (по умолчанию ./data)")
    args = parser.parse_args()

    bundles_dir = args.data_dir / "okf_bundles"
    if not bundles_dir.is_dir():
        print(f"Каталог не найден: {bundles_dir}")
        raise SystemExit(1)

    bundle_ids = sorted(d.name for d in bundles_dir.iterdir() if d.is_dir())
    if not bundle_ids:
        print("OKF-бандлы не найдены. Индекс будет пересоздан пустым.")
        bundle_ids = []

    vs = VectorStore()
    if vs.client.collection_exists(vs.collection):
        vs.client.delete_collection(vs.collection)
        print(f"Удалена старая коллекция: {vs.collection}")
    vs.ensure_collection()
    print(f"Создана коллекция: {vs.collection}")

    embedder = Embedder()
    total_concepts = 0
    for doc_id in bundle_ids:
        okf_docs = load_bundle(bundles_dir / doc_id)
        if not okf_docs:
            print(f"[{doc_id}] пропущен: OKF-концепты не найдены")
            continue
        # Dense-эмбеддинг из title + content: title содержит коды разделов,
        # которые иначе не попадают в вектор (см. pipeline.py).
        vectors = embedder.embed_texts(
            [f"{d.metadata.get('title', '')}\n{d.content}" for d in okf_docs]
        )
        vs.index_concepts(doc_id, okf_docs, vectors)
        total_concepts += len(okf_docs)
        print(f"[{doc_id}] индексировано концептов: {len(okf_docs)}")

    points = vs.client.count(collection_name=vs.collection, exact=True).count
    print(f"Готово: документов {len(bundle_ids)}, концептов {total_concepts}, точек в коллекции {points}")


if __name__ == "__main__":
    main()